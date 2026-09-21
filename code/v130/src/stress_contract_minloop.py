from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'v110/src'), str(ROOT/'src')]
import direct_stress as c
import hardware as h

ASSET = ROOT/'v091/results/sic-asset-01/sic_gate_ctrl_v3'
GUARD_RTL = ROOT/'v120/rtl/context_guard.v'
HEADROOM_BITS = [2,5,6]
EXACT_BITS = [2,3,4,5,6,7]
AREA_CAP = 0.20
DEADLINE = 1000.0
PHASES = (1.0,4.0,9.0)
RESISTORS = (10000,1000,330)
FILTERS = (0,100)
METHODS = ('raw','fixed','blanking')
MODE = {'raw':0,'fixed':1,'blanking':2}


def frontend(folder: Path, r_ohm: int, filter_pf: int, slew: float,
             coupling_pf: float, trip_ns: float | None = None):
    cm = 'Vcm cm 0 800' if slew == 0 else (
        f'Vcm cm 0 PWL(0 800 1000n 800 {1000+800/slew}n 0 6u 0)')
    trip = 'Vtrip trip 0 0' if trip_ns is None else (
        f'Vtrip trip 0 PWL(0 0 {trip_ns}n 0 {trip_ns+0.05}n 1 6u 1)')
    return c.spice(folder, f'''V0.13 direct common-mode feedback model
Vcc vcc 0 3.3
{cm}
Rpull vcc flt {r_ohm}
Cpin flt 0 {20+filter_pf}p
Ccouple cm flt {coupling_pf}p
Dlow 0 flt dclamp
Dhigh flt vcc dclamp
.model dclamp D(Is=1n N=1 Rs=1)
{trip}
Sfault flt 0 trip 0 swfault
.model swfault SW(Ron=20 Roff=1e12 Vt=.5 Vh=0)
.ic v(flt)=3.3 v(cm)=800
.control
set wr_singlescale
set wr_vecnames
tran .25n 6u 0 .25n
wrdata wave.txt v(flt) v(cm)
quit
.endc
.end
''')


def detection(folder: Path, current_uA: int, short_ns: float, gate_on_ns: float):
    begin = max(short_ns, gate_on_ns + 200.0)
    rows = c.spice(folder, f'''V0.13 DESAT charge envelope
Cblank ds 0 100p IC=2
Rleak ds 0 1e12
Bcharge 0 ds I=ternary_fcn(time >= {begin}n, {current_uA}u, 0)
.ic v(ds)=2
.control
set wr_singlescale
set wr_vecnames
tran .25n 6u 0 .25n uic
wrdata wave.txt v(ds)
quit
.endc
.end
''')
    threshold = c.crossing(rows, 9.15)
    expected = begin + 7.15*100e-12/(current_uA*1e-6)*1e9
    assert abs(threshold-expected) < .6
    return dict(short_ns=short_ns, charge_start_ns=begin,
                threshold_ns=threshold, trip_ns=threshold+140.0,
                current_uA=current_uA)


def calibrate(out: Path, r_ohm: int, filter_pf: int):
    longest = 0
    latest = -1.0
    for slew, cc in itertools.product((10,150), (.05,.2,1.0)):
        wave = frontend(out/f'calibration/r{r_ohm}_f{filter_pf}_s{slew}_c{cc}',
                        r_ohm, filter_pf, slew, cc)
        for phase in (2.5,7.5):
            run = 0
            for i, word in enumerate(c.stimulus(phase, wave)):
                if word & 512:
                    run += 1
                    longest = max(longest, run)
                    latest = max(latest, phase+10*i-980)
                else:
                    run = 0
    samples = max(2, longest+2)
    guard = max(2, math.ceil(max(0.0, latest)/10.0)+2)
    return dict(r_ohm=r_ohm, filter_pf=filter_pf, samples=samples,
                guard_cycles=guard, measured_longest_fault_samples=longest,
                measured_last_fault_after_marker_ns=latest,
                asserted_pullup_mA=3.3/r_ohm*1000.0)


def make_tb(plan):
    decl = []
    inst = []
    for label in METHODS:
        decl.append(f'wire [15:0] {label};')
        inst.append(f'''context_controller #(.MODE({MODE[label]}),.SAMPLES({plan["samples"]}),.GUARD({plan["guard_cycles"]})) u_{label}(
 .clk(clk),.rst(rst),.pwm_hi(hi),.pwm_lo(lo),.desat_hi(1'b0),.desat_lo(1'b0),.oc_fault(fault),
 .uvlo_ok(1'b1),.ot_warn(1'b0),.cfg_we(we),.cfg_addr(3'd0),.cfg_wdata(cfg),.switch_marker(marker),
 .gate_hi({label}[15]),.gate_lo({label}[14]),.miller_clamp_hi({label}[13]),.miller_clamp_lo({label}[12]),
 .fault_latched({label}[11]),.softoff_active({label}[10]),.drive_strength({label}[9:8]),.status({label}[7:0]));''')
    return '''`timescale 1ns/1ps
module tb;
reg clk=0,rst=1,hi=0,lo=0,fault=0,we=0,marker=0;
reg [7:0] cfg=3;
reg [13:0] words[0:599];
integer i,out;
real phase;
reg [2047:0] input_path,output_path;
''' + '\n'.join(decl+inst) + '''
initial begin
 if (!$value$plusargs("input=%s",input_path)) $fatal;
 if (!$value$plusargs("output=%s",output_path)) $fatal;
 if (!$value$plusargs("phase=%f",phase)) $fatal;
 $readmemh(input_path,words);out=$fopen(output_path,"w");
 #(phase);
 for(i=0;i<600;i=i+1) begin
  {marker,rst,hi,lo,fault,we,cfg}=words[i];
  #0.001;clk=1;#0.001;
  $fdisplay(out,"%0d %04h %04h %04h",i,raw,fixed,blanking);
  #4.998;clk=0;#5;
 end
 $fclose(out);$finish;
end
endmodule
'''


def add_marker(words, phase: float, jitter_ns: float = 0.0, enabled: bool = True):
    result = list(words)
    if enabled:
        i = math.ceil((980+jitter_ns-phase)/10)
        if 0 <= i < len(result):
            result[i] |= 8192
    return result


def stress_words(phase, wave=None, jitter_ns=0, marker=True, **kw):
    return add_marker(c.stimulus(phase, wave, **kw), phase, jitter_ns, marker)


def compile_exec(folder: Path, rtl: Path, plan):
    folder.mkdir(parents=True, exist_ok=False)
    (folder/'tb.v').write_text(make_tb(plan), encoding='utf-8')
    c.command(folder, 'compile',
              ['iverilog','-g2012','-s','tb','-o','sim.vvp',
               rtl, GUARD_RTL, folder/'tb.v'])
    return folder/'sim.vvp'


def replay(folder: Path, executable: Path, phase: float, words):
    folder.mkdir(parents=True, exist_ok=False)
    (folder/'input.hex').write_text(''.join(f'{x:04x}\n' for x in words), encoding='ascii')
    c.command(folder, 'vvp',
              ['vvp', executable, '+input=input.hex', '+output=output.txt',
               f'+phase={phase}'])
    rows = []
    for line in (folder/'output.txt').read_text().splitlines():
        parts = line.split()
        if len(parts) != 4 or any('x' in x.lower() or 'z' in x.lower() for x in parts):
            raise ValueError(('invalid digital output', line))
        rows.append([int(parts[0])] + [int(x,16) for x in parts[1:]])
    if len(rows) != c.CYCLES:
        raise ValueError('incomplete RTL execution')
    return rows


def metrics(rows, reference, method, phase, short_ns=None):
    col = METHODS.index(method)+1
    first_fault = next((phase+r[0]*c.PERIOD for r in rows if r[col] & 0x800), None)
    first_gate_off = None
    if short_ns is not None:
        first_gate_off = next((phase+r[0]*c.PERIOD for r in rows
                               if phase+r[0]*c.PERIOD >= short_ns and not r[col]&0xc000), None)
    false_trip = first_fault is not None and (short_ns is None or first_fault < short_ns)
    delay = None if first_gate_off is None else first_gate_off-short_ns
    missed = short_ns is not None and first_gate_off is None
    late = short_ns is not None and (missed or delay > DEADLINE)
    lost = sum(bool(ref[1]&0xc000) and not bool(r[col]&0xc000)
               for r,ref in zip(rows,reference)
               if short_ns is None or phase+r[0]*c.PERIOD < short_ns)
    unintended = sum(bool(r[col]&0xc000) and not bool(ref[1]&0xc000)
                     for r,ref in zip(rows,reference))
    return dict(false_trip=bool(false_trip),missed_shutdown=bool(missed),
                late_shutdown=bool(late),shutdown_delay_ns=delay,
                lost_drive_cycles=lost,unexpected_drive_cycles=unintended,
                bridge_overlap_cycles=sum((r[col]&0xc000)==0xc000 for r in rows),
                normal_output_mismatch_cycles=sum(r[col]!=ref[1] for r,ref in zip(rows,reference)))


def evaluate_interface(out: Path, rtl: Path, plan):
    tag=f"r{plan['r_ohm']}_f{plan['filter_pf']}"
    exe=compile_exec(out/f'executables/{tag}', rtl, plan)
    refs={}
    gate_on={}
    normal_pass=True
    normal_traces=0
    for phase in PHASES:
        ref=replay(out/f'normal/{tag}/p{phase}_steady',exe,phase,stress_words(phase))
        refs[phase]=ref
        gate_on[phase]=next(phase+10*r[0] for r in ref if r[1]&0x8000)
        for side,width in itertools.product((0,1),(8,40)):
            rr=replay(out/f'normal/{tag}/p{phase}_s{side}_w{width}',exe,phase,
                      stress_words(phase,side=side,width=width))
            normal_traces += 1
            normal_pass &= all(r[1]==r[2]==r[3] and (r[1]&0xc000)!=0xc000 for r in rr)

    events=[]
    for slew,cc in itertools.product((50,100),(.1,.5,.8)):
        wave_folder=out/f'holdout/{tag}/s{slew}_c{cc}/analog'
        wave=frontend(wave_folder,plan['r_ohm'],plan['filter_pf'],slew,cc)
        for phase,jitter in itertools.product(PHASES,(-10,0,10)):
            rr=replay(out/f'holdout/{tag}/s{slew}_c{cc}/p{phase}_j{jitter}',
                      exe,phase,stress_words(phase,wave,jitter))
            for method in METHODS:
                m=metrics(rr,refs[phase],method,phase)
                m.update(study='noise_holdout',r_ohm=plan['r_ohm'],
                         filter_pf=plan['filter_pf'],slew=slew,coupling_pf=cc,
                         phase_ns=phase,marker_jitter_ns=jitter,method=method)
                events.append(m)

    for phase,kind in itertools.product(PHASES,('turnon','stable','simultaneous')):
        short=gate_on[phase] if kind=='turnon' else 1500 if kind=='stable' else 1000
        folder=out/f'short/{tag}/p{phase}_{kind}'
        det=detection(folder/'sense',2000,short,gate_on[phase])
        wave=frontend(folder/'analog',plan['r_ohm'],plan['filter_pf'],
                      100 if kind=='simultaneous' else 0,.8,det['trip_ns'])
        rr=replay(folder/'rtl',exe,phase,stress_words(phase,wave))
        for method in METHODS:
            m=metrics(rr,refs[phase],method,phase,short)
            m.update(study='short_holdout',r_ohm=plan['r_ohm'],
                     filter_pf=plan['filter_pf'],kind=kind,phase_ns=phase,
                     current_uA=2000,trip_ns=det['trip_ns'],method=method)
            events.append(m)

    diag_wave=frontend(out/f'diagnostic/{tag}/analog',
                       plan['r_ohm'],plan['filter_pf'],100,.8)
    for phase in PHASES:
        rr=replay(out/f'diagnostic/{tag}/p{phase}',exe,phase,
                  stress_words(phase,diag_wave,marker=False))
        for method in METHODS:
            m=metrics(rr,refs[phase],method,phase)
            m.update(study='marker_absent',r_ohm=plan['r_ohm'],
                     filter_pf=plan['filter_pf'],phase_ns=phase,method=method)
            events.append(m)

    summaries=[]
    for method in METHODS:
        noise=[x for x in events if x['study']=='noise_holdout' and x['method']==method]
        short=[x for x in events if x['study']=='short_holdout' and x['method']==method]
        diag=[x for x in events if x['study']=='marker_absent' and x['method']==method]
        delays=[x['shutdown_delay_ns'] for x in short
                if x['shutdown_delay_ns'] is not None and not x['false_trip']]
        summaries.append(dict(
            r_ohm=plan['r_ohm'],filter_pf=plan['filter_pf'],
            samples=plan['samples'],guard_cycles=plan['guard_cycles'],
            method=method,mode=MODE[method],normal_pass=bool(normal_pass),
            normal_traces=normal_traces,
            noise_events=len(noise),noise_false_trips=sum(x['false_trip'] for x in noise),
            noise_unexpected=sum(x['unexpected_drive_cycles']>0 for x in noise),
            noise_overlap=sum(x['bridge_overlap_cycles']>0 for x in noise),
            short_events=len(short),short_late=sum(x['late_shutdown'] for x in short),
            short_missed=sum(x['missed_shutdown'] for x in short),
            short_false_trips=sum(x['false_trip'] for x in short),
            short_unexpected=sum(x['unexpected_drive_cycles']>0 for x in short),
            short_overlap=sum(x['bridge_overlap_cycles']>0 for x in short),
            worst_shutdown_ns=max(delays) if delays else None,
            marker_absent_events=len(diag),
            marker_absent_false_trips=sum(x['false_trip'] for x in diag),
            marker_required=(method=='blanking'),
            stress_contract_pass=bool(
                normal_pass and noise and short
                and sum(x['false_trip'] for x in noise)==0
                and sum(x['unexpected_drive_cycles']>0 for x in noise)==0
                and sum(x['bridge_overlap_cycles']>0 for x in noise)==0
                and sum(x['late_shutdown'] for x in short)==0
                and sum(x['missed_shutdown'] for x in short)==0
                and sum(x['unexpected_drive_cycles']>0 for x in short)==0
                and sum(x['bridge_overlap_cycles']>0 for x in short)==0
                and delays and max(delays)<=DEADLINE
            ),
            marker_robust_pass=bool(sum(x['false_trip'] for x in diag)==0),
            asserted_pullup_mA=plan['asserted_pullup_mA'],
        ))
    return summaries,events


def emit_candidate(out: Path, label: str, selected):
    frozen=json.loads((ASSET/'frozen.json').read_text())
    bits=json.loads((ASSET/'logical_bits.json').read_text())
    folder=out/'generated'/label
    folder.mkdir(parents=True,exist_ok=False)
    physical=h.emit(frozen['modules']['top'],bits,ASSET/'comb.v',selected,folder/'candidate.v')
    h.dump(folder/'selected.json',dict(label=label,selected=selected,physical_bits=len(physical)))
    return folder/'candidate.v'


def map_integrated(folder: Path, rtl: Path, mode: int, samples: int, guard: int,
                   liberty: Path, area0: float):
    folder.mkdir(parents=True,exist_ok=False)
    script=f'''read_verilog {rtl} {GUARD_RTL}
chparam -set MODE {mode} -set SAMPLES {samples} -set GUARD {guard} context_controller
synth -top context_controller -flatten
dfflibmap -liberty {liberty}
abc -liberty {liberty}
read_liberty -lib {liberty}
clean
check -assert
stat -liberty {liberty}
write_verilog -noattr -noexpr mapped.v
'''
    (folder/'map.ys').write_text(script,encoding='utf-8')
    log=c.command(folder,'yosys',['yosys','-Q','-T','-s','map.ys'])
    areas=re.findall(r'Chip area for (?:top )?module.*?:\s*([0-9.]+)',log)
    if not areas:
        areas=re.findall(r'Chip area.*?([0-9]+\.[0-9]+)',log)
    if not areas:
        raise ValueError('missing mapped area')
    area=float(areas[-1])
    (folder/'timing.tcl').write_text(f'''read_liberty {liberty}
read_verilog {folder/'mapped.v'}
link_design context_controller
create_clock -name clk -period 10 [get_ports clk]
set_input_delay 1 -clock clk [get_ports {{rst pwm_hi pwm_lo desat_hi desat_lo oc_fault uvlo_ok ot_warn cfg_we cfg_addr* cfg_wdata* switch_marker}}]
set_output_delay 1 -clock clk [all_outputs]
set_load .01 [all_outputs]
report_checks -path_delay max
report_checks -path_delay min
exit
''',encoding='utf-8')
    timing=c.command(folder,'sta',['sta','-exit','timing.tcl'])
    assert not re.search(r'(^|\n)Error:',timing)
    slacks=list(map(float,re.findall(r'(-?\d+\.\d+)\s+slack',timing)))
    if len(slacks)<2:
        raise ValueError('missing setup/hold slack')
    return dict(area=area,area_ratio=(area-area0)/area0,
                setup_slack=slacks[0],hold_slack=slacks[-1],
                timing_pass=min(slacks[0],slacks[-1])>=0)


def integrated_normal(out: Path, rtl: Path, plan):
    exe=compile_exec(out/'integrated_exec',rtl,plan)
    ok=True
    traces=0
    col=METHODS.index(plan['method'])+1
    for phase in PHASES:
        ref=replay(out/f'integrated_normal/p{phase}_steady',exe,phase,stress_words(phase))
        for side,width in itertools.product((0,1),(8,40)):
            rr=replay(out/f'integrated_normal/p{phase}_s{side}_w{width}',exe,phase,
                      stress_words(phase,side=side,width=width))
            ok &= all(r[col]==ref[1] and (r[col]&0xc000)!=0xc000 for r in rr)
            traces += 1
    return bool(ok),traces


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--liberty',type=Path,required=True)
    args=ap.parse_args()
    out=args.out.resolve()
    liberty=args.liberty.resolve()
    out.mkdir(parents=True,exist_ok=False)

    area0=json.loads((ASSET/'baseline_hardware.json').read_text())['area']
    source=ASSET/'frozen.v'
    c.dump(out/'protocol.json',dict(
        status='predeclared',area_cap=AREA_CAP,deadline_ns=DEADLINE,
        resistors_ohm=RESISTORS,filter_pf=FILTERS,
        calibration_slew=(10,150),calibration_coupling_pf=(.05,.2,1.0),
        holdout_slew=(50,100),holdout_coupling_pf=(.1,.5,.8),
        holdout_phases=PHASES,marker_jitter_ns=(-10,0,10),
        digital_methods=METHODS,desat_current_uA=2000,desat_cap_pf=100,
        headroom_bits=HEADROOM_BITS,exact_bits=EXACT_BITS,
        scientific_negative_does_not_fail_ci=True))

    plans=[]
    all_events=[]
    interface=[]
    for r,flt in itertools.product(RESISTORS,FILTERS):
        p=calibrate(out,r,flt)
        plans.append(p)
        rows,events=evaluate_interface(out,source,p)
        interface.extend(rows)
        all_events.extend(events)
        print('V130_INTERFACE',r,flt,json.dumps(rows,sort_keys=True),flush=True)
    c.dump(out/'calibration.json',plans)
    c.dump(out/'interface_summary.json',interface)

    fields=sorted(set().union(*(x.keys() for x in all_events)))
    with (out/'events.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(all_events)

    headroom_rtl=emit_candidate(out,'headroom',HEADROOM_BITS)
    exact_rtl=emit_candidate(out,'exact',EXACT_BITS)

    candidates=[]
    feasible_interface=[x for x in interface if x['stress_contract_pass']]
    for idx,row in enumerate(feasible_interface):
        for start,rtl in [('headroom',headroom_rtl),('exact',exact_rtl)]:
            hw=map_integrated(out/f'hardware/{start}/c{idx:02d}',rtl,row['mode'],
                              row['samples'],row['guard_cycles'],liberty,area0)
            z=dict(row,start=start,**hw)
            z['overall_pass']=bool(hw['timing_pass'] and hw['area_ratio']<=AREA_CAP)
            candidates.append(z)
            print('V130_HW',start,idx,json.dumps(z,sort_keys=True),flush=True)
    c.dump(out/'complete_candidates.json',candidates)

    head=[x for x in candidates if x['start']=='headroom' and x['overall_pass']]
    exact=[x for x in candidates if x['start']=='exact' and x['overall_pass']]
    key=lambda x:(x['area_ratio'],x['asserted_pullup_mA'],x['filter_pf'],
                  x['worst_shutdown_ns'] if x['worst_shutdown_ns'] is not None else 1e9)
    selected=min(head,key=key) if head else None

    integrated_ok=None
    integrated_traces=0
    if selected is not None:
        integrated_ok,integrated_traces=integrated_normal(
            out/'selected_integrated',headroom_rtl,selected)

    default_raw=next(x for x in interface if x['r_ohm']==10000 and x['filter_pf']==0 and x['method']=='raw')
    strongest_raw=[x for x in interface if x['method']=='raw' and x['stress_contract_pass']]
    strongest_fixed=[x for x in interface if x['method']=='fixed' and x['stress_contract_pass']]
    strongest_blanking=[x for x in interface if x['method']=='blanking' and x['stress_contract_pass']]

    summary=dict(
        status='completed',
        interface_candidates=len(interface),
        stress_feasible_interface=len(feasible_interface),
        complete_candidates=len(candidates),
        selected_headroom=selected,
        exact_complete_feasible=len(exact),
        headroom_complete_feasible=len(head),
        integrated_normal_pass=integrated_ok,
        integrated_normal_traces=integrated_traces,
        h1_system_value=bool(selected is not None and len(exact)==0),
        h2_direct_stress=bool(default_raw['noise_false_trips']>0 and feasible_interface),
        h3_normal_function=bool(selected is not None and integrated_ok and selected['timing_pass']),
        default_tmr_only_noise_false_trips=default_raw['noise_false_trips'],
        raw_analog_only_feasible=len(strongest_raw),
        fixed_feasible=len(strongest_fixed),
        blanking_feasible=len(strongest_blanking),
        marker_absent_selected_pass=(selected['marker_robust_pass'] if selected else None),
        marker_requirement=(selected['marker_required'] if selected else None),
        negative_control_exact_can_fit=bool(exact),
        claim_boundary='finite mixed-signal grid, mapped digital area, pre-layout timing; not field FIT or hardware measurement'
    )
    c.dump(out/'summary.json',summary)
    c.dump(out/'manifest.json',{str(p.relative_to(out)):c.sha(p)
                                for p in out.rglob('*') if p.is_file()})
    print('V130_COMPLETE',json.dumps(summary,sort_keys=True),flush=True)


if __name__=='__main__':
    main()
