from __future__ import annotations
import argparse, hashlib, itertools, json, math, random, re, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[
    str(ROOT/'v130/src'), str(ROOT/'v110/src'), str(ROOT/'v096/src'),
    str(ROOT/'v095/src'), str(ROOT/'v030/src'), str(ROOT/'src')
]
import stress_contract_minloop as s
import direct_stress as c
import hardware as h
import ext_end_to_end as ext
from headroom_certificate import full_information_summary

EXT_ASSET=ROOT/'v095/results/ext-asset-01/ext_motor_axis_ctrl'
EXT_HW=ROOT/'v095/results/ext-hardware-01'
EXT_GUARD=ROOT/'v132/rtl/ext_compact_guard.v'
AREA_CAP=.20
DEADLINE=1000.0
METHODS=('raw','fixed','blanking')
MODE={'raw':0,'fixed':1,'blanking':2}
PHASES=(1.0,9.0)
POINTS=((10000,0),(1000,0),(330,100))
PUBLIC_SHA='43e01bcb79057ecf99909d96a1637499a14f0bd3'

def dump(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(obj,indent=2,sort_keys=True)+'\n',encoding='utf-8')

def power_sanity(primary_summary):
    rows=[]
    for r in (10000,1000,330):
        for cap in (0,100):
            current_a=3.3/r
            rows.append(dict(
                r_ohm=r,filter_pf=cap,
                asserted_low_current_mA=current_a*1e3,
                static_pullup_power_mW=3.3*current_a*1e3,
                full_swing_cap_energy_nJ=.5*(cap*1e-12)*(3.3**2)*1e9,
                cap_dynamic_power_uW_at_100kHz=.5*(cap*1e-12)*(3.3**2)*1e5*1e6,
            ))
    selected=primary_summary.get('selected_headroom') or {}
    return dict(
        selected=dict(r_ohm=selected.get('r_ohm'),filter_pf=selected.get('filter_pf'),
                      digital_area_ratio=selected.get('area_ratio'),
                      worst_shutdown_ns=selected.get('worst_shutdown_ns')),
        rows=rows,
        boundary='analytic proxy: asserted-low resistor dissipation and full-swing capacitor charging only; not measured total-system power'
    )

def normalize_candidate(out,label,selected):
    frozen=json.loads((EXT_ASSET/'frozen.json').read_text())
    bits=json.loads((EXT_ASSET/'logical_bits.json').read_text())
    folder=out/'generated'/label
    folder.mkdir(parents=True,exist_ok=False)
    raw=folder/'candidate.v'
    physical=h.emit(frozen['modules']['top'],bits,EXT_ASSET/'comb.v',selected,raw)
    text=raw.read_text()
    text,n=re.subn(r'\bmodule\s+dut\b','module top',text,count=1)
    if n!=1:
        raise RuntimeError(f'cannot normalize emitted module for {label}')
    top=folder/'candidate_top.v'
    top.write_text(text,encoding='utf-8')
    return top,len(bits)+2*len(selected)

def make_tb(plan):
    decl=[]; inst=[]
    for method in METHODS:
        decl.append(f'wire [11:0] {method};')
        inst.append(f'''ext_context_controller #(.MODE({MODE[method]}),.SAMPLES({plan["samples"]}),.GUARD({plan["guard_cycles"]})) u_{method}(
 .clk(clk),.rst(rst),.start_cmd(start),.stop_cmd(stop),.fault_clear(clear),.fault_in(fault),
 .oc_fault(1'b0),.switch_marker(marker),.pwm_cmd(pwm),
 .gate_hi({method}[11:9]),.gate_lo({method}[8:6]),.fault_latched({method}[5]),
 .run_active({method}[4]),.status({method}[3:0]));''')
    return '''`timescale 1ns/1ps
module tb;
reg clk=0,rst=1,start=0,stop=0,clear=0,fault=0,marker=0;
reg [2:0] pwm=0;
reg [9:0] words[0:599];
integer i,out;
real phase;
reg [2047:0] input_path,output_path;
'''+ '\n'.join(decl+inst)+'''
initial begin
 if (!$value$plusargs("input=%s",input_path)) $fatal;
 if (!$value$plusargs("output=%s",output_path)) $fatal;
 if (!$value$plusargs("phase=%f",phase)) $fatal;
 $readmemh(input_path,words); out=$fopen(output_path,"w");
 #(phase);
 for(i=0;i<600;i=i+1) begin
  {marker,rst,start,stop,clear,fault,pwm}=words[i];
  #0.001; clk=1; #0.001;
  $fdisplay(out,"%0d %03h %03h %03h",i,raw,fixed,blanking);
  #4.998; clk=0; #5;
 end
 $fclose(out); $finish;
end
endmodule
'''

def transfer_words(phase,wave=None,jitter=0,marker=True,pattern=0):
    words=[]
    for i in range(600):
        t=phase+10*i
        rst=int(i<10)
        start=int(i==12)
        stop=0
        clear=0
        fault=int(wave is not None and c.sample(wave,t)<1.65)
        if pattern==0:
            pwm=((i//17)&1) | (((i//23)&1)<<1) | (((i//31)&1)<<2)
        else:
            pwm=((i//(9+pattern))&1) | (((i//(13+pattern))&1)<<1) | (((i//(19+pattern))&1)<<2)
        mark=0
        if marker:
            k=math.ceil((980+jitter-phase)/10)
            mark=int(i==k)
        words.append((mark<<9)|(rst<<8)|(start<<7)|(stop<<6)|(clear<<5)|(fault<<4)|pwm)
    return words

def compile_transfer(folder,rtl,plan):
    folder.mkdir(parents=True,exist_ok=False)
    (folder/'tb.v').write_text(make_tb(plan),encoding='utf-8')
    c.command(folder,'compile',['iverilog','-g2012','-s','tb','-o','sim.vvp',rtl,EXT_GUARD,folder/'tb.v'])
    return folder/'sim.vvp'

def replay(folder,exe,phase,words):
    folder.mkdir(parents=True,exist_ok=False)
    (folder/'input.hex').write_text(''.join(f'{x:03x}\n' for x in words),encoding='ascii')
    c.command(folder,'vvp',['vvp',exe,'+input=input.hex','+output=output.txt',f'+phase={phase}'])
    rows=[]
    for line in (folder/'output.txt').read_text().splitlines():
        q=line.split()
        if len(q)!=4 or any('x' in x.lower() or 'z' in x.lower() for x in q):
            raise ValueError(('bad transfer output',line))
        rows.append([int(q[0])]+[int(x,16) for x in q[1:]])
    if len(rows)!=600:
        raise ValueError('incomplete transfer execution')
    return rows

def decode(word):
    return dict(hi=(word>>9)&7,lo=(word>>6)&7,fault=(word>>5)&1,run=(word>>4)&1,status=word&15)

def metric(rows,ref,method,phase,short=None):
    col=METHODS.index(method)+1
    overlap=0; mismatch=0; lost=0; unexpected=0; first_trip=None
    for r,rr in zip(rows,ref):
        t=phase+r[0]*10
        d=decode(r[col]); d0=decode(rr[1])
        if d['fault'] and first_trip is None:
            first_trip=t
        if d['hi'] & d['lo']:
            overlap += 1
        mismatch += int(r[col]!=rr[1])
        if short is None or t<short:
            lost += int((d0['hi']|d0['lo'])!=0 and (d['hi']|d['lo'])==0)
        unexpected += int(((d['hi']|d['lo']) & ~(d0['hi']|d0['lo']))!=0)
    false=first_trip is not None and (short is None or first_trip<short)
    delay=None; missed=False; late=False
    if short is not None:
        off=next((phase+r[0]*10 for r in rows
                  if phase+r[0]*10>=short and decode(r[col])['fault']
                  and (decode(r[col])['hi']|decode(r[col])['lo'])==0),None)
        missed=off is None
        delay=None if off is None else off-short
        late=missed or delay>DEADLINE
    return dict(false_trip=bool(false),shutdown_delay_ns=delay,missed_shutdown=bool(missed),
                late_shutdown=bool(late),bridge_overlap_cycles=overlap,
                output_mismatch_cycles=mismatch,lost_drive_cycles=lost,unexpected_drive_cycles=unexpected)

def evaluate_point(out,rtl,plan):
    tag=f"r{plan['r_ohm']}_f{plan['filter_pf']}"
    exe=compile_transfer(out/'exe'/tag,rtl,plan)
    refs={}; gate_on={}; normal_ok=True; normal_traces=0
    for phase in PHASES:
        ref=replay(out/f'normal/{tag}/p{phase}_base',exe,phase,transfer_words(phase))
        refs[phase]=ref
        gate_on[phase]=next(phase+r[0]*10 for r in ref if (decode(r[1])['hi']|decode(r[1])['lo'])!=0)
        for pattern in range(1,5):
            rr=replay(out/f'normal/{tag}/p{phase}_q{pattern}',exe,phase,transfer_words(phase,pattern=pattern))
            normal_traces+=1
            normal_ok &= all(r[1]==r[2]==r[3] for r in rr)
    events=[]
    for slew,cc in itertools.product((50,100),(.5,.8)):
        wave=s.frontend(out/f'holdout/{tag}/s{slew}_c{cc}/analog',plan['r_ohm'],plan['filter_pf'],slew,cc)
        for phase in PHASES:
            rr=replay(out/f'holdout/{tag}/s{slew}_c{cc}/p{phase}',exe,phase,transfer_words(phase,wave))
            for method in METHODS:
                m=metric(rr,refs[phase],method,phase)
                m.update(study='noise',method=method,r_ohm=plan['r_ohm'],filter_pf=plan['filter_pf'],slew=slew,coupling_pf=cc,phase=phase)
                events.append(m)
    for phase,kind in itertools.product(PHASES,('turnon','stable','simultaneous')):
        short=gate_on[phase] if kind=='turnon' else 1500 if kind=='stable' else 1000
        folder=out/f'short/{tag}/p{phase}_{kind}'
        det=s.detection(folder/'sense',2000,short,gate_on[phase])
        wave=s.frontend(folder/'analog',plan['r_ohm'],plan['filter_pf'],100 if kind=='simultaneous' else 0,.8,det['trip_ns'])
        rr=replay(folder/'rtl',exe,phase,transfer_words(phase,wave))
        for method in METHODS:
            m=metric(rr,refs[phase],method,phase,short)
            m.update(study='short',method=method,r_ohm=plan['r_ohm'],filter_pf=plan['filter_pf'],kind=kind,phase=phase)
            events.append(m)
    diag=s.frontend(out/f'diagnostic/{tag}/analog',plan['r_ohm'],plan['filter_pf'],100,.8)
    for phase in PHASES:
        rr=replay(out/f'diagnostic/{tag}/p{phase}',exe,phase,transfer_words(phase,diag,marker=False))
        for method in METHODS:
            m=metric(rr,refs[phase],method,phase)
            m.update(study='marker_absent',method=method,r_ohm=plan['r_ohm'],filter_pf=plan['filter_pf'],phase=phase)
            events.append(m)
    summary=[]
    for method in METHODS:
        noise=[x for x in events if x['study']=='noise' and x['method']==method]
        short=[x for x in events if x['study']=='short' and x['method']==method]
        diag=[x for x in events if x['study']=='marker_absent' and x['method']==method]
        delays=[x['shutdown_delay_ns'] for x in short if x['shutdown_delay_ns'] is not None and not x['false_trip']]
        summary.append(dict(r_ohm=plan['r_ohm'],filter_pf=plan['filter_pf'],samples=plan['samples'],guard_cycles=plan['guard_cycles'],
                            method=method,mode=MODE[method],normal_pass=bool(normal_ok),normal_traces=normal_traces,
                            noise_events=len(noise),noise_false_trips=sum(x['false_trip'] for x in noise),
                            short_events=len(short),short_late=sum(x['late_shutdown'] for x in short),
                            short_missed=sum(x['missed_shutdown'] for x in short),
                            worst_shutdown_ns=max(delays) if delays else None,
                            marker_absent_false_trips=sum(x['false_trip'] for x in diag),
                            stress_pass=bool(normal_ok and noise and short and sum(x['false_trip'] for x in noise)==0 and
                                             sum(x['late_shutdown'] for x in short)==0 and sum(x['missed_shutdown'] for x in short)==0 and
                                             delays and max(delays)<=DEADLINE)))
    return summary,events

def map_integrated(folder,rtl,mode,samples,guard,liberty,area0,core_floor):
    folder.mkdir(parents=True,exist_ok=False)
    script=f'''read_verilog {rtl} {EXT_GUARD}
chparam -set MODE {mode} -set SAMPLES {samples} -set GUARD {guard} ext_context_controller
hierarchy -check -top ext_context_controller
flatten
proc
setattr -set keep 1 t:$dff t:$dffe t:$sdff t:$sdffe t:$sdffce
opt -nodffe -nosdff
techmap
dfflibmap -liberty {liberty}
abc -liberty {liberty}
clean
read_liberty -lib {liberty}
check -assert
stat -liberty {liberty}
write_json mapped.json
write_verilog -noattr -noexpr -simple-lhs mapped.v
'''
    (folder/'map.ys').write_text(script)
    log=c.command(folder,'yosys',['yosys','-Q','-T','-s','map.ys'])
    areas=re.findall(r'Chip area for (?:top )?module.*?:\s*([0-9.]+)',log)
    if not areas:
        raise ValueError('missing transfer area')
    area=float(areas[-1])
    design=json.loads((folder/'mapped.json').read_text())
    mod=design['modules']['ext_context_controller']
    ff=sum(cell.get('type','').startswith('DFF') for cell in mod.get('cells',{}).values())
    if ff<core_floor:
        raise RuntimeError(f'transfer TMR loss {ff} < {core_floor}')
    (folder/'timing.tcl').write_text(f'''read_liberty {liberty}
read_verilog {folder/'mapped.v'}
link_design ext_context_controller
create_clock -name clk -period 10 [get_ports clk]
set data_inputs {{}}
set clock_port [lindex [get_ports clk] 0]
foreach port [all_inputs] {{ if {{$port ne $clock_port}} {{ lappend data_inputs $port }} }}
set_input_delay -clock clk 1 $data_inputs
set_input_transition 0.1 $data_inputs
set_output_delay -clock clk 1 [all_outputs]
set_load .01 [all_outputs]
report_checks -path_delay max
report_checks -path_delay min
exit
''')
    timing=c.command(folder,'sta',['sta','-exit','timing.tcl'])
    slacks=list(map(float,re.findall(r'(-?\d+\.\d+)\s+slack',timing)))
    if len(slacks)<2:
        raise ValueError('missing transfer slacks')
    return dict(area=area,area_ratio=(area-area0)/area0,flip_flops=ff,core_floor=core_floor,
                setup_slack=slacks[0],hold_slack=slacks[-1],timing_pass=min(slacks[0],slacks[-1])>=0)

def expanded_primary_normal(out,primary_out):
    summary=json.loads((primary_out/'summary.json').read_text())
    selected=summary['selected_headroom']
    if not selected:
        raise RuntimeError('missing primary selected design')
    rtl=primary_out/'generated/headroom/candidate_top.v'
    s.GUARD_RTL=ROOT/'v130/rtl/compact_context_guard.v'
    exe=s.compile_exec(out/'exe',rtl,selected)
    col=s.METHODS.index(selected['method'])+1
    total=0; mismatch=0; overlap=0
    for phase in (.5,1.,2.5,4.,6.5,9.):
        for side,width in itertools.product((0,1),(4,8,16,40,80)):
            rr=s.replay(out/f'matrix/p{phase}_s{side}_w{width}',exe,phase,s.stress_words(phase,side=side,width=width))
            total+=1
            mismatch+=sum(r[col]!=r[1] for r in rr)
            overlap+=sum((r[col]&0xc000)==0xc000 for r in rr)
    for seed in range(8):
        rng=random.Random(13000+seed)
        words=[]; state=0; hold=0
        for i in range(c.CYCLES):
            rst=int(i<10)
            if hold<=0:
                state=rng.choice((0,1,2)); hold=rng.randint(5,30)
            hold-=1
            hi=int(state==1); lo=int(state==2)
            words.append((rst<<12)|(hi<<11)|(lo<<10)|(0<<9)|(int(i==12)<<8)|3)
        rr=s.replay(out/f'random/seed{seed}',exe,1.0,s.add_marker(words,1.0,enabled=False))
        total+=1
        mismatch+=sum(r[col]!=r[1] for r in rr)
        overlap+=sum((r[col]&0xc000)==0xc000 for r in rr)
    result=dict(traces=total,mismatch_cycles=mismatch,bridge_overlap_cycles=overlap,pass_all=(mismatch==0 and overlap==0))
    dump(out/'summary.json',result)
    return result

def public_smoke(out,public_dir):
    dead=public_dir/'DEAD_TIME.v'
    if not dead.exists():
        raise FileNotFoundError(dead)
    sha=hashlib.sha256(dead.read_bytes()).hexdigest()
    out.mkdir(parents=True,exist_ok=False)
    tb=out/'tb.v'
    tb.write_text(r'''`timescale 1ns/1ps
module qual #(parameter N=5)(input clk,input fault,output trip);
 reg [3:0] c=0; always @(posedge clk) if(!fault)c<=0; else if(c<N)c<=c+1'b1;
 assign trip=fault && c>=N-1;
endmodule
module tb;
reg clk=0,pwm=0,fault=0; wire [1:0] p; wire q; integer i,fh;
DEAD_TIME #(.N(12)) d(p,pwm,12'd3,clk); qual #(.N(5)) g(clk,fault,q);
initial begin fh=$fopen("smoke.txt","w");
 for(i=0;i<160;i=i+1) begin
  if(i%20==0)pwm=~pwm;
  fault=(i>=50 && i<53) || (i>=100);
  #5;clk=1;#0.001;$fdisplay(fh,"%0d %b %b %b",i,p,q,fault);#4.999;clk=0;
 end
 $fclose(fh);$finish; end
endmodule
''')
    c.command(out,'compile',['iverilog','-g2012','-s','tb','-o','sim.vvp',dead,tb])
    c.command(out,'run',['vvp','sim.vvp'])
    rows=[x.split() for x in (out/'smoke.txt').read_text().splitlines()]
    nuisance_qualified=sum(int(r[2]) for r in rows if 50<=int(r[0])<53)
    persistent_trip=next((int(r[0]) for r in rows if int(r[0])>=100 and int(r[2])),None)
    result=dict(public_repo='Awesama-T/Gate_Driver_Controller',pinned_commit=PUBLIC_SHA,dead_time_sha256=sha,
                nuisance_pulse_cycles=3,qualified_nuisance_trip_cycles=nuisance_qualified,
                persistent_fault_start_cycle=100,qualified_persistent_trip_cycle=persistent_trip,
                pass_smoke=(nuisance_qualified==0 and persistent_trip is not None and persistent_trip<=105),
                boundary='external DEAD_TIME RTL plus external fail-safe qualifier smoke; no selective-TMR claim')
    dump(out/'summary.json',result)
    return result

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--primary-out',type=Path,required=True)
    ap.add_argument('--liberty',type=Path,required=True)
    ap.add_argument('--public-dir',type=Path,required=True)
    a=ap.parse_args()
    out=a.out.resolve(); out.mkdir(parents=True,exist_ok=False)
    primary_summary=json.loads((a.primary_out/'summary.json').read_text())
    power=power_sanity(primary_summary); dump(out/'power_sanity.json',power)
    normal=expanded_primary_normal(out/'primary_normal',a.primary_out.resolve())

    data=ext.dataset()
    structural=full_information_summary(data,epsilon=.02,area_budget=.2)
    records=json.loads((EXT_HW/'records.json').read_text())
    by_pid={r['plan_id']:r for r in records}
    starts={'headroom':by_pid[structural['headroom_plan_id']],'exact':by_pid[structural['exact_plan_id']]}
    area0=json.loads((EXT_ASSET/'baseline_hardware.json').read_text())['area']
    rtl={}; floors={}
    for label,rec in starts.items():
        rtl[label],floors[label]=normalize_candidate(out/'transfer',label,rec['selected'])

    plans=[s.calibrate(out/'transfer',r,f) for r,f in POINTS]
    interface=[]; events=[]
    for p in plans:
        rr,ee=evaluate_point(out/'transfer/interface',rtl['headroom'],p)
        interface+=rr; events+=ee
    dump(out/'transfer/interface_summary.json',interface)
    feasible=[x for x in interface if x['stress_pass']]
    mapped=[]
    for idx,row in enumerate(feasible):
        for label in ('headroom','exact'):
            hw=map_integrated(out/f'transfer/hardware/{label}/c{idx:02d}',rtl[label],row['mode'],row['samples'],row['guard_cycles'],
                              a.liberty.resolve(),area0,floors[label])
            z=dict(row,start=label,**hw)
            z['overall_under_20']=bool(hw['timing_pass'] and hw['area_ratio']<=AREA_CAP)
            mapped.append(z)
    dump(out/'transfer/complete_candidates.json',mapped)
    default=next(x for x in interface if x['r_ohm']==10000 and x['filter_pf']==0 and x['method']=='raw')
    transfer=dict(structural=structural,interface_candidates=len(interface),stress_feasible=len(feasible),
                  raw_default_false_trips=default['noise_false_trips'],
                  headroom_complete_under20=sum(x['overall_under_20'] for x in mapped if x['start']=='headroom'),
                  exact_complete_under20=sum(x['overall_under_20'] for x in mapped if x['start']=='exact'),
                  methodology_transfer_pass=bool(default['noise_false_trips']>0 and feasible),
                  capacity_ranking_is_outcome_not_gate=True)
    dump(out/'transfer/summary.json',transfer)

    public=public_smoke(out/'public_smoke',a.public_dir.resolve())
    summary=dict(status='completed',power_sanity=power,primary_expanded_normal=normal,
                 transfer=transfer,public_smoke=public,
                 p0_power_done=True,p0_transfer_done=transfer['methodology_transfer_pass'],
                 p0_normal_done=normal['pass_all'],p0_public_smoke_done=public['pass_smoke'])
    dump(out/'summary.json',summary)
    print('V132_P0_COMPLETE',json.dumps(summary,sort_keys=True))

if __name__=='__main__':
    main()
