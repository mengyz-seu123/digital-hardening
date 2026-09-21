from pathlib import Path
import itertools, math
import primary_interface as s
import stress as c

ROOT=Path(__file__).resolve().parents[1]
EXT_GUARD=ROOT/"rtl/transfer_guard.v"
DEADLINE=1000.0
METHODS=("raw","fixed","blanking")
MODE={"raw":0,"fixed":1,"blanking":2}
PHASES=(1.0,9.0)

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
reg [8:0] words[0:599];
integer i,out;
real phase;
reg [2047:0] input_path,output_path;
'''+ '\n'.join(decl+inst)+'''
initial begin
 if (!$value$plusargs("input=%s",input_path)) $fatal;
 if (!$value$plusargs("output=%s",output_path)) $fatal;
 if (!$value$plusargs("phase=%f",phase)) $fatal;
 $readmemh(input_path,words);
 // Clock synchronous reset before logging.
 rst=1; start=0; stop=0; clear=0; fault=0; marker=0; pwm=0;
 repeat(4) begin #5; clk=1; #0.001; #4.999; clk=0; end
 out=$fopen(output_path,"w");
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
        words.append((mark<<8)|(rst<<7)|(start<<6)|(stop<<5)|(clear<<4)|(fault<<3)|pwm)
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
