from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import platform
import re
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
PERIOD = 10.0
CYCLES = 600
PHASES = (2.5, 7.5)
METHODS = ('baseline', 'q4', 'q8', 'shared_tmr')
SLEWS = (0, 10, 50, 150)
COUPLING = (0.05, 0.2, 1.0)
FILTERS = (0, 100, 300)
CAPS = (10, 33, 100)
DEADLINE = 1000.0


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def command(folder, name, argv, timeout=180):
    start = time.monotonic()
    p = subprocess.run(list(map(str, argv)), cwd=folder, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, timeout=timeout)
    output = p.stdout.decode('utf-8', errors='replace')
    (folder / (name + '.log')).write_text(output, encoding='utf-8')
    with (folder / 'commands.jsonl').open('a', encoding='utf-8') as f:
        f.write(json.dumps(dict(argv=list(map(str, argv)), returncode=p.returncode,
                               elapsed_s=time.monotonic()-start, log=name+'.log'))+'\n')
    if p.returncode:
        raise RuntimeError(f'{name}: exit {p.returncode}: {output[-1800:]}')
    return output


def read_wave(path):
    rows = []
    for line in path.read_text().splitlines()[1:]:
        values = list(map(float, line.split()))
        if not values or not all(map(math.isfinite, values)):
            raise ValueError(f'invalid ngspice waveform: {path}')
        rows.append(values)
    if len(rows) < 100 or rows[-1][0] < 5.9e-6:
        raise ValueError(f'incomplete ngspice waveform: {path}')
    if any(a[0] > b[0] for a, b in zip(rows, rows[1:])):
        raise ValueError('nonmonotonic waveform time')
    return rows


def sample(rows, t_ns, column=1):
    t = t_ns * 1e-9
    k = bisect.bisect_right(rows, t, key=lambda r: r[0])
    if k == 0:
        return rows[0][column]
    if k == len(rows):
        return rows[-1][column]
    a, b = rows[k-1], rows[k]
    return a[column] + (b[column]-a[column]) * (t-a[0])/(b[0]-a[0])


def crossing(rows, threshold):
    for a, b in zip(rows, rows[1:]):
        if a[1] < threshold <= b[1]:
            return 1e9*(a[0] + (b[0]-a[0])*(threshold-a[1])/(b[1]-a[1]))
    raise ValueError('DESAT threshold was never reached')


def spice(folder, netlist):
    folder.mkdir(parents=True, exist_ok=False)
    (folder/'circuit.cir').write_text(netlist, encoding='utf-8')
    log = command(folder, 'ngspice', ['ngspice', '-b', 'circuit.cir'])
    if re.search(r'(?im)^(?:Error:|.*simulation\(s\) aborted|doAnalyses:)', log):
        raise RuntimeError(f'ngspice analysis failed: {folder}')
    return read_wave(folder/'wave.txt')


def sense(folder, cap_pf, short_ns, gate_on_ns):
    begin = max(short_ns, gate_on_ns + 200.0)
    rows = spice(folder, f'''DESAT charge envelope: explicit feedback architecture
Cblank ds 0 {cap_pf}p IC=2
Rleak ds 0 1e12
Bcharge 0 ds I=ternary_fcn(time >= {begin}n, 500u, 0)
.ic v(ds)=2
.control
set wr_singlescale
set wr_vecnames
tran 0.25n 6u 0 0.25n uic
wrdata wave.txt v(ds)
quit
.endc
.end
''')
    actual = crossing(rows, 9.15)
    expected = begin + (9.15-2.0)*cap_pf*1e-12/500e-6*1e9
    if abs(actual-expected) > 0.6:
        raise AssertionError(('charge analytic check', actual, expected))
    result = dict(cap_pf=cap_pf, short_ns=short_ns, gate_on_ns=gate_on_ns,
                  charge_begin_ns=begin, threshold_ns=actual, filtered_fault_ns=actual+140.0,
                  analytic_threshold_ns=expected, analytic_error_ns=actual-expected)
    dump(folder/'summary.json', result)
    return result


def feedback(folder, slew, coupling_pf, filter_pf, trip_ns=None, step=0.25):
    cm = 'Vcm cm 0 800' if slew == 0 else (
        f'Vcm cm 0 PWL(0 800 1000n 800 {1000+800/slew}n 0 6u 0)')
    switch = 'Vtrip trip 0 0' if trip_ns is None else (
        f'Vtrip trip 0 PWL(0 0 {trip_ns}n 0 {trip_ns+0.05}n 1 6u 1)')
    return spice(folder, f'''Capacitive common-mode coupling into active-low FLT
Vcc vcc 0 3.3
{cm}
Rpull vcc flt 10k
Cpin flt 0 {20+filter_pf}p
Ccouple cm flt {coupling_pf}p
Dlow 0 flt dclamp
Dhigh flt vcc dclamp
.model dclamp D(Is=1n N=1 Rs=1)
{switch}
Sfault flt 0 trip 0 swfault
.model swfault SW(Ron=20 Roff=1e12 Vt=0.5 Vh=0)
.ic v(flt)=3.3 v(cm)=800
.control
set wr_singlescale
set wr_vecnames
tran {step}n 6u 0 {step}n
wrdata wave.txt v(flt) v(cm)
quit
.endc
.end
''')


def make_tb():
    declarations, instances = [], []
    for label, n in [('baseline', 0), ('q4', 4), ('q8', 8), ('tm0', 0), ('tm1', 0), ('tm2', 0)]:
        declarations.append(f'wire [15:0] {label};')
        instances.append(f'''qualified_controller #(.SAMPLES({n})) u_{label}(
 .clk(clk), .rst(rst), .pwm_hi(hi), .pwm_lo(lo), .desat_hi(1'b0), .desat_lo(1'b0),
 .oc_fault(fault), .uvlo_ok(1'b1), .ot_warn(1'b0), .cfg_we(we), .cfg_addr(3'd0), .cfg_wdata(cfg),
 .gate_hi({label}[15]), .gate_lo({label}[14]), .miller_clamp_hi({label}[13]), .miller_clamp_lo({label}[12]),
 .fault_latched({label}[11]), .softoff_active({label}[10]), .drive_strength({label}[9:8]), .status({label}[7:0]));''')
    return '''`timescale 1ns/1ps
module tb;
reg clk=0, rst=1, hi=0, lo=0, fault=0, we=0;
reg [7:0] cfg=3;
reg [12:0] words [0:599];
integer i, out;
real phase;
reg [2047:0] input_path, output_path;
''' + '\n'.join(declarations+instances) + '''
wire [15:0] shared_tmr = (tm0 & tm1) | (tm0 & tm2) | (tm1 & tm2);
initial begin
 if (!$value$plusargs("input=%s", input_path)) $fatal(1,"missing input");
 if (!$value$plusargs("output=%s", output_path)) $fatal(1,"missing output");
 if (!$value$plusargs("phase=%f", phase)) $fatal(1,"missing phase");
 $readmemh(input_path, words);
 out=$fopen(output_path,"w");
 if (!out) $fatal(1,"cannot open output");
 #(phase);
 for(i=0;i<600;i=i+1) begin
   {rst,hi,lo,fault,we,cfg}=words[i];
   #0.001; clk=1;
   #0.001;
   $fdisplay(out,"%0d %04h %04h %04h %04h",i,baseline,q4,q8,shared_tmr);
   #4.998; clk=0;
   #5;
 end
 $fclose(out); $finish;
end
endmodule
'''


def stimulus(phase, wave=None, side=0, width=None, deadtime=3):
    words = []
    for i in range(CYCLES):
        t = phase+i*PERIOD
        rst = int(i < 10)
        we = int(i == 12)
        hi, lo = 0, 0
        if width is None:
            hi = int(20 <= i < 550)
        elif i >= 20:
            j = (i-20) % (2*(width+deadtime+6))
            a = j < width
            b = width+deadtime+6 <= j < 2*width+deadtime+6
            hi, lo = (int(a), int(b)) if side == 0 else (int(b), int(a))
        fault = int(wave is not None and sample(wave, t) < 1.65)
        words.append((rst<<12)|(hi<<11)|(lo<<10)|(fault<<9)|(we<<8)|deadtime)
    return words


def replay(folder, executable, phase, words):
    folder.mkdir(parents=True, exist_ok=False)
    (folder/'input.hex').write_text(''.join(f'{x:04x}\n' for x in words), encoding='ascii')
    command(folder, 'vvp', ['vvp', executable, '+input=input.hex', '+output=output.txt', f'+phase={phase}'])
    rows = []
    for line in (folder/'output.txt').read_text().splitlines():
        parts = line.split()
        if len(parts) != 5 or any('x' in x.lower() or 'z' in x.lower() for x in parts):
            raise ValueError(('invalid digital output', line))
        rows.append([int(parts[0])] + [int(x, 16) for x in parts[1:]])
    if len(rows) != CYCLES or [r[0] for r in rows] != list(range(CYCLES)):
        raise ValueError('incomplete RTL execution')
    return rows


def metrics(rows, reference, method, phase, short_ns=None):
    col = METHODS.index(method)+1
    first_fault = next((phase+r[0]*PERIOD for r in rows if r[col] & 0x800), None)
    first_gate_off = None
    if short_ns is not None:
        first_gate_off = next((phase+r[0]*PERIOD for r in rows
                               if phase+r[0]*PERIOD >= short_ns and not r[col]&0xc000), None)
    false_trip = first_fault is not None and (short_ns is None or first_fault < short_ns)
    delay = None if first_gate_off is None else first_gate_off-short_ns
    # An early spurious trip is a functional failure, never credited as fast SC protection.
    missed = short_ns is not None and first_gate_off is None
    late = short_ns is not None and (missed or delay > DEADLINE)
    lost = sum(bool(ref[1]&0xc000) and not bool(r[col]&0xc000)
               for r, ref in zip(rows, reference)
               if short_ns is None or phase+r[0]*PERIOD < short_ns)
    unintended = sum(bool(r[col]&0xc000) and not bool(ref[1]&0xc000)
                     for r, ref in zip(rows, reference))
    return dict(false_trip=bool(false_trip), missed_shutdown=bool(missed), late_shutdown=bool(late),
                first_fault_ns=first_fault, gate_off_ns=first_gate_off, shutdown_delay_ns=delay,
                lost_drive_cycles=lost, unexpected_drive_cycles=unintended,
                bridge_overlap_cycles=sum((r[col]&0xc000)==0xc000 for r in rows),
                normal_output_mismatch_cycles=sum(r[col]!=ref[1] for r, ref in zip(rows, reference)),
                shared_tmr_exact_match=all(r[4]==r[1] for r in rows))


def hardware(out, rtl, liberty):
    rows = []
    for n in (0, 4, 8):
        folder = out/f'hardware/q{n}'
        folder.mkdir(parents=True)
        script = f'''read_verilog {rtl} {ROOT/'rtl/qualified_controller.v'}
chparam -set SAMPLES {n} qualified_controller
synth -top qualified_controller -flatten
dfflibmap -liberty {liberty}
abc -liberty {liberty}
read_liberty -lib {liberty}
clean
check -assert
stat -liberty {liberty}
write_verilog -noattr -noexpr mapped.v
write_json mapped.json
'''
        (folder/'map.ys').write_text(script)
        log = command(folder, 'yosys', ['yosys', '-Q', '-T', '-s', 'map.ys'])
        areas = re.findall(r'Chip area for (?:top )?module.*?:\s*([0-9.]+)', log)
        if not areas:
            areas = re.findall(r'Chip area.*?([0-9]+\.[0-9]+)', log)
        if not areas:
            raise ValueError('missing mapped area')
        sta = f'''read_liberty {liberty}
read_verilog {folder/'mapped.v'}
link_design qualified_controller
create_clock -name clk -period 10 [get_ports clk]
set_input_delay 1 -clock clk [get_ports {{rst pwm_hi pwm_lo desat_hi desat_lo oc_fault uvlo_ok ot_warn cfg_we cfg_addr* cfg_wdata*}}]
set_output_delay 1 -clock clk [all_outputs]
set_load 0.01 [all_outputs]
report_checks -path_delay max
report_checks -path_delay min
exit
'''
        (folder/'timing.tcl').write_text(sta)
        timing = command(folder, 'sta', ['sta', '-exit', 'timing.tcl'])
        if re.search(r'(^|\n)Error:', timing):
            raise ValueError('OpenSTA reported an error')
        slack = re.findall(r'(-?\d+\.\d+)\s+slack', timing)
        if not slack:
            raise ValueError('missing STA slack')
        rows.append(dict(samples=n, area=float(areas[-1]), setup_slack_ns=float(slack[0]), hold_slack_ns=float(slack[-1])))
    for r in rows:
        r['overhead'] = r['area']/rows[0]['area']-1
        r['within_20pct_area'] = r['overhead'] <= .2+1e-9
        r['timing_pass'] = min(r['setup_slack_ns'], r['hold_slack_ns']) >= 0
    dump(out/'hardware.json', rows)
    return rows


def summarize(events, normals, hardware_rows):
    groups = []
    for study, method, filt in itertools.product(('dvdt','short','combined'), METHODS, FILTERS):
        rr = [e for e in events if e['study']==study and e['method']==method and e['filter_pf']==filt]
        delays = [e['shutdown_delay_ns'] for e in rr if e['shutdown_delay_ns'] is not None and not e['false_trip']]
        groups.append(dict(study=study, method=method, filter_pf=filt, cases=len(rr),
                           false_trips=sum(e['false_trip'] for e in rr),
                           late_shutdowns=sum(e['late_shutdown'] for e in rr),
                           missed_shutdowns=sum(e['missed_shutdown'] for e in rr),
                           lost_drive_cases=sum(e['lost_drive_cycles']>0 for e in rr),
                           unexpected_drive_cases=sum(e['unexpected_drive_cycles']>0 for e in rr),
                           overlap_cases=sum(e['bridge_overlap_cycles']>0 for e in rr),
                           worst_shutdown_ns=max(delays) if delays else None))
    return dict(status='completed', evidence_level='ngspice transient + RTL replay, pre-layout area/timing only',
                no_internal_upset_injection=True, grid_not_field_probability=True,
                normal_cases=len(normals), normal_all_outputs_equal=all(n['equal'] for n in normals),
                shared_input_tmr_always_equals_baseline=all(e['shared_tmr_exact_match'] for e in events),
                hardware=hardware_rows, groups=groups, events=len(events),
                novel_method_superiority_established=False,
                limitations=['unmeasured effective coupling', 'ideal receiver, no metastability/PVT',
                             'feedback shutdown architecture, not complete UCC21750',
                             'imposed power-stage waveforms, not transistor damage simulation',
                             'standard RC/qualification are controls, not novel methods',
                             'no power characterization or general-RTL noninferiority claim'])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--rtl', type=Path, required=True)
    ap.add_argument('--liberty', type=Path, required=True)
    args = ap.parse_args()
    out, rtl, liberty = args.out.resolve(), args.rtl.resolve(), args.liberty.resolve()
    out.mkdir(parents=True, exist_ok=False)
    protocol = dict(slews=SLEWS, coupling_pf=COUPLING, filters_pf=FILTERS, desat_caps_pf=CAPS,
                    phases_ns=PHASES, methods=METHODS, deadline_ns=DEADLINE, cycles=CYCLES,
                    sources_sha256=sha(ROOT/'sources.json'), rtl_sha256=sha(rtl),
                    wrapper_sha256=sha(ROOT/'rtl/qualified_controller.v'), script_sha256=sha(__file__),
                    liberty_sha256=sha(liberty), host=platform.node(), python=platform.python_version(),
                    status='written_before_execution', selection='none: all predeclared candidates reported')
    dump(out/'protocol.json', protocol)
    for tool, argv in [('ngspice',['ngspice','--version']), ('iverilog',['iverilog','-V']), ('yosys',['yosys','-V'])]:
        command(out, tool+'-version', argv)
    (out/'tb.v').write_text(make_tb())
    command(out,'compile',['iverilog','-g2012','-s','tb','-o','sim.vvp',rtl,ROOT/'rtl/qualified_controller.v',out/'tb.v'])
    executable = out/'sim.vvp'
    normals, references, gate_ons = [], {}, {}
    for phase in PHASES:
        reference = replay(out/f'normal/steady_{phase}', executable, phase, stimulus(phase))
        references[phase] = reference
        gate_ons[phase] = next(phase+r[0]*PERIOD for r in reference if r[1]&0x8000)
        for side, width, deadtime in itertools.product((0,1),(8,40),(2,5)):
            rows = replay(out/f'normal/p{phase}_s{side}_w{width}_d{deadtime}', executable, phase,
                          stimulus(phase,side=side,width=width,deadtime=deadtime))
            equal = all(r[1]==r[2]==r[3]==r[4] for r in rows)
            safe = all((r[1]&0xc000)!=0xc000 for r in rows)
            active = any(r[1]&0x8000 for r in rows) and any(r[1]&0x4000 for r in rows)
            normals.append(dict(phase=phase,side=side,width=width,deadtime=deadtime,
                                equal=equal, no_overlap=safe, both_gates_exercised=active))
    dump(out/'normal.json', normals)
    if not all(r['equal'] and r['no_overlap'] and r['both_gates_exercised'] for r in normals):
        raise AssertionError('normal function contract failed')
    print('NORMAL_PASS',len(normals),flush=True)
    hardware_rows = hardware(out,rtl,liberty)
    events, senses = [], {}
    for phase, cap, kind in itertools.product(PHASES,CAPS,('at_turnon','established')):
        short_ns = gate_ons[phase] if kind=='at_turnon' else 1500.0
        senses[phase,cap,kind] = sense(out/f'sense/p{phase}_c{cap}_{kind}',cap,short_ns,gate_ons[phase])
    cases = []
    for slew, coupling, filt in itertools.product(SLEWS,COUPLING,FILTERS):
        cases.append(dict(study='dvdt',slew=slew,coupling_pf=coupling,filter_pf=filt,cap_pf=None,kind=None))
    for cap, kind, filt in itertools.product(CAPS,('at_turnon','established'),FILTERS):
        cases.append(dict(study='short',slew=0,coupling_pf=.2,filter_pf=filt,cap_pf=cap,kind=kind))
    for coupling, filt in itertools.product(COUPLING,FILTERS):
        cases.append(dict(study='combined',slew=150,coupling_pf=coupling,filter_pf=filt,cap_pf=33,kind='established'))
    for caseid, case in enumerate(cases):
        for phase in PHASES:
            det = senses[phase,case['cap_pf'],case['kind']] if case['cap_pf'] else None
            folder=out/f'cases/c{caseid:03d}_p{phase}'
            wave=feedback(folder/'analog',case['slew'],case['coupling_pf'],case['filter_pf'],
                          None if det is None else det['filtered_fault_ns'])
            words=stimulus(phase,wave)
            rows=replay(folder/'rtl',executable,phase,words)
            for method in METHODS:
                result=dict(case,case_id=caseid,phase_ns=phase,method=method,
                            min_flt_V=min(r[1] for r in wave),receiver_low_samples=sum(bool(w&512) for w in words))
                result.update(metrics(rows,references[phase],method,phase,None if det is None else det['short_ns']))
                if det:
                    result.update(det)
                events.append(result)
                with (out/'events.jsonl').open('a',encoding='utf-8') as f:
                    f.write(json.dumps(result,sort_keys=True)+'\n')
            dump(folder/'summary.json',events[-4:])
        print('CASE',caseid+1,'/',len(cases),case,flush=True)
    # Same strongest-coupling point with half timestep; compare sampled inputs and all output words.
    refine=[]
    original_id=next(i for i,c in enumerate(cases) if c['study']=='dvdt' and c['slew']==150
                     and c['coupling_pf']==1.0 and c['filter_pf']==0)
    for phase in PHASES:
        w=feedback(out/f'refinement/p{phase}/analog',150,1.0,0,step=.125)
        inputs=stimulus(phase,w)
        old_path=out/f'cases/c{original_id:03d}_p{phase}/rtl'
        old_inputs=[int(x,16) for x in (old_path/'input.hex').read_text().splitlines()]
        replay(out/f'refinement/p{phase}/rtl',executable,phase,inputs)
        identical=inputs==old_inputs and (out/f'refinement/p{phase}/rtl/output.txt').read_bytes()==(old_path/'output.txt').read_bytes()
        refine.append(dict(phase=phase,identical=identical))
    dump(out/'refinement.json',refine)
    if not all(r['identical'] for r in refine):
        raise AssertionError('timestep refinement changed classifications')
    fields=sorted(set().union(*(r.keys() for r in events)))
    with (out/'events.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(events)
    summary=summarize(events,normals,hardware_rows)
    summary['timestep_refinement_pass']=True
    dump(out/'summary.json',summary)
    manifest={str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file()}
    dump(out/'manifest.json',manifest)
    print('COMPLETE',json.dumps(summary),flush=True)


if __name__=='__main__':
    main()
