from pathlib import Path

SCENARIOS = [
    'normal',
    'cmti_extra_edge',
    'cmti_missing_edge',
    'cmti_simultaneous_edge',
    'cmti_delayed_edge',
    'cmti_stuck_high',
    'desat',
    'cmti_desat_glitch',
    'overcurrent',
    'uvlo',
    'overtemperature',
    'dvdt_commutation_burst',
    'dvdt_opposite_glitch',
    'hot_deadtime_margin',
    'hot_thermal_derate',
    'mixed_dvdt_desat_glitch',
    'desat_blanking_glitch',
    'desat_after_blank',
    'desat_blank_race',
    'miller_low_commutation',
    'miller_high_commutation',
    'miller_low_opposite_glitch',
    'miller_high_opposite_glitch',
    'hot_desat_after_blank',
]
SCENARIO_COUNT=len(SCENARIOS)
DV_DT_SCENARIOS={1,2,3,4,5,7,11,12,15,19,20,21,22}
THERMAL_SCENARIOS={10,13,14,23}
PROTECTION_SCENARIOS={6,8,9,10,14,16,17,18,23}
DESAT_BLANK_SCENARIOS={16,17,18,23}
MILLER_LOW_SCENARIOS={19,21}
MILLER_HIGH_SCENARIOS={20,22}
MILLER_SCENARIOS=MILLER_LOW_SCENARIOS|MILLER_HIGH_SCENARIOS
SHORT_CIRCUIT_SCENARIOS={6,17,18,23}
BLANK_RELEASE_CYCLE=32


def make_tb(cfg,physical,path,module='dut'):
    cases=[]
    for r in physical:
        i=r['physical_id'];q=r['path']
        cases.append("%d: begin before_value=%s; %s=~%s; after_value=%s; end"%(i,q,q,q,q))
    cases2=[]
    for r in physical:
        i=r['physical_id'];q=r['path']
        cases2.append("%d: begin before2=%s; %s=~%s; after2=%s; end"%(i,q,q,q,q))
    ports='''reg pwm_hi=0,pwm_lo=0,desat_hi=0,desat_lo=0,oc_fault=0;
reg uvlo_ok=1,ot_warn=0,cfg_we=0; reg[2:0]cfg_addr=0;reg[7:0]cfg_wdata=0;
wire gate_hi,gate_lo,miller_clamp_hi,miller_clamp_lo,fault_latched,softoff_active;
wire[1:0]drive_strength;wire[7:0]status;
DUT dut(.clk(clk),.rst(rst),.pwm_hi(pwm_hi),.pwm_lo(pwm_lo),.desat_hi(desat_hi),
.desat_lo(desat_lo),.oc_fault(oc_fault),.uvlo_ok(uvlo_ok),.ot_warn(ot_warn),
.cfg_we(cfg_we),.cfg_addr(cfg_addr),.cfg_wdata(cfg_wdata),.gate_hi(gate_hi),.gate_lo(gate_lo),
.miller_clamp_hi(miller_clamp_hi),.miller_clamp_lo(miller_clamp_lo),
.fault_latched(fault_latched),.softoff_active(softoff_active),.drive_strength(drive_strength),.status(status));'''.replace('DUT',module)
    text=r'''`timescale 1ns/1ps
module tb;
reg clk=0,rst=1;
PORTS
integer seed,target,target2,fault_cycle,window,mode,c,errors,unexpected,alarms,applied,applied2;
integer before_value,after_value,before2,after2,received,hi_off,lo_off,last_hi,last_lo;
integer fault_wait,fault_started,scenario,m,min_dead,expects_trip,sc_age,sc_active,shutdown_seen;
integer shootthrough,deadtime_violation,missed_shutdown,false_trip,thermal_derate_violation,unsafe_gate_pulse;
integer desat_blanking_violation,late_shutdown,miller_clamp_violation,parasitic_turnon_proxy;
initial begin
forever begin
 clk=0;rst=1;pwm_hi=0;pwm_lo=0;desat_hi=0;desat_lo=0;oc_fault=0;uvlo_ok=1;ot_warn=0;cfg_we=0;
 seed=1;target=-1;target2=-1;fault_cycle=12;window=192;mode=0;
 if($value$plusargs("seed=%d",seed))begin end
 if($value$plusargs("target=%d",target))begin end
 if($value$plusargs("target2=%d",target2))begin end
 if($value$plusargs("cycle=%d",fault_cycle))begin end
 if($value$plusargs("window=%d",window))begin end
 if($value$plusargs("mode=%d",mode))begin end
 errors=0;unexpected=0;alarms=0;applied=0;applied2=0;before_value=-1;after_value=-1;before2=-1;after2=-1;received=0;
 hi_off=99;lo_off=99;last_hi=0;last_lo=0;fault_wait=0;fault_started=0;scenario=seed%24;
 sc_age=0;sc_active=0;shutdown_seen=0;
 shootthrough=0;deadtime_violation=0;missed_shutdown=0;false_trip=0;thermal_derate_violation=0;unsafe_gate_pulse=0;
 desat_blanking_violation=0;late_shutdown=0;miller_clamp_violation=0;parasitic_turnon_proxy=0;
 min_dead=((scenario==13)||(scenario==14)||(scenario==23))?4:2;
 expects_trip=((scenario==6)||(scenario==8)||(scenario==9)||(scenario==10)||(scenario==14)||(scenario==17)||(scenario==18)||(scenario==23));
 repeat(3)begin #5;clk=1;#5;clk=0;end rst=0;
'''.replace('PORTS',ports)
    Path(path).write_text(text,encoding='utf-8')
    return append_tb(path,cases,cases2)


def append_tb(path,cases,cases2):
    more=r''' for(c=0;c<window;c=c+1)begin
   m=c%96;pwm_hi=0;pwm_lo=0;desat_hi=0;desat_lo=0;oc_fault=0;uvlo_ok=1;ot_warn=0;cfg_we=0;
   if(m>=3 && m<=38)pwm_hi=1;
   if(m>=52 && m<=87)pwm_lo=1;

   // Dynamic-CMTI/dVdt interface proxies.
   if(scenario==1 && m==57)pwm_hi=1;
   if(scenario==2 && m==16)pwm_hi=0;
   if(scenario==3 && m==57)begin pwm_hi=1;pwm_lo=1;end
   if(scenario==4)begin if(m==3)pwm_hi=0;if(m==39)pwm_hi=1;end
   if(scenario==5 && (m==55||m==56))pwm_hi=1;
   if(scenario==11 && (m==40||m==88))begin pwm_hi=1;pwm_lo=1;end
   if(scenario==12)begin if(m==40)pwm_lo=1;if(m==88)pwm_hi=1;end

   // Protection and thermal workloads.
   if(scenario==6 && c>=34 && c<42)desat_hi=1;
   if(scenario==7 && c==18)desat_hi=1;
   if(scenario==8 && c==80)oc_fault=1;
   if(scenario==9 && c>=72 && c<78)uvlo_ok=0;
   if((scenario==10||scenario==14) && c>=100 && c<114)ot_warn=1;
   if(scenario==15 && c==36)begin desat_hi=1;pwm_hi=1;pwm_lo=1;end

   // DESAT blanking / short-circuit protection-race workloads.
   if(scenario==16 && c==18)desat_hi=1;
   if(scenario==17 && c>=34 && c<48)desat_hi=1;
   if(scenario==18 && c>=28 && c<48)desat_hi=1;
   if(scenario==23 && c>=34 && c<52)desat_hi=1;

   // Miller-clamp workloads. No analog gate waveform is claimed: if the
   // complementary clamp state is lost while the opposite gate commutates,
   // the scoreboard reports a parasitic-turn-on proxy.
   if(scenario==21 && m==7)pwm_lo=1;
   if(scenario==22 && m==56)pwm_hi=1;

   if(c==1)begin
      if((scenario==13)||(scenario==14)||(scenario==23))begin cfg_we=1;cfg_addr=0;cfg_wdata=8'd4;end
      else begin cfg_we=1;cfg_addr=1;cfg_wdata={6'b0,seed[1:0]};end
   end
   if(c==2 && ((scenario==7)||(scenario==15)||(scenario==16)||(scenario==17)||(scenario==18)||(scenario==23)))begin
      cfg_we=1;cfg_addr=2;cfg_wdata=8'd2;
   end
   if(mode==2)begin pwm_hi=0;pwm_lo=0;desat_hi=0;desat_lo=0;oc_fault=0;uvlo_ok=1;ot_warn=0;end

   #1;
   if(c==fault_cycle && target>=0)begin
     case(target)
CASES
     default:before_value=-1;
     endcase
     if((before_value==0||before_value==1)&&after_value==(1-before_value))applied=1;
   end
   if(c==fault_cycle && target2>=0)begin
     case(target2)
CASES2
     default:before2=-1;
     endcase
     if((before2==0||before2==1)&&after2==(1-before2))applied2=1;
   end
   #3;

   if(gate_hi && gate_lo)begin shootthrough=1;errors=errors+1;end
   if(gate_hi && !last_hi && lo_off<min_dead)begin deadtime_violation=1;errors=errors+1;end
   if(gate_lo && !last_lo && hi_off<min_dead)begin deadtime_violation=1;errors=errors+1;end

   if((scenario==16||scenario==18) && fault_latched && c<32)begin
      desat_blanking_violation=1;errors=errors+1;
   end

   if((scenario==19||scenario==21) && gate_hi && !miller_clamp_lo)begin
      miller_clamp_violation=1;parasitic_turnon_proxy=1;unsafe_gate_pulse=1;errors=errors+1;
   end
   if((scenario==20||scenario==22) && gate_lo && !miller_clamp_hi)begin
      miller_clamp_violation=1;parasitic_turnon_proxy=1;unsafe_gate_pulse=1;errors=errors+1;
   end

   if(expects_trip && !fault_started &&
      (((scenario==6||scenario==17||scenario==18||scenario==23)&&desat_hi)||
       (scenario==8&&oc_fault)||(scenario==9&&!uvlo_ok)||((scenario==10||scenario==14)&&ot_warn)))begin
      fault_started=1;
      if((scenario==6)||(scenario==17)||(scenario==18)||(scenario==23))begin sc_active=1;sc_age=0;end
      else fault_wait=((scenario==10)||(scenario==14))?6:4;
   end

   if(fault_wait>0)begin
      fault_wait=fault_wait-1;
      if(fault_wait==0 && ((gate_hi||gate_lo)||!fault_latched))begin missed_shutdown=1;errors=errors+1;end
   end

   if(sc_active && !shutdown_seen)begin
      if(fault_latched && !gate_hi && !gate_lo)shutdown_seen=1;
      else begin
         sc_age=sc_age+1;
         if(sc_age==50)begin late_shutdown=1;errors=errors+1;end
         if(sc_age==100)begin missed_shutdown=1;errors=errors+1;end
      end
   end

   if(!expects_trip && fault_latched)begin false_trip=1;errors=errors+1;end
   if((scenario==10||scenario==14) && c>=106 && c<114 && drive_strength>1)begin
      thermal_derate_violation=1;errors=errors+1;
   end
   if((scenario==1||scenario==2||scenario==3||scenario==4||scenario==5||scenario==11||scenario==12||scenario==15)
       && gate_hi && gate_lo)begin unsafe_gate_pulse=1;end

   if(gate_hi||gate_lo)received=1;
   if(gate_hi)hi_off=0;else if(hi_off<99)hi_off=hi_off+1;
   if(gate_lo)lo_off=0;else if(lo_off<99)lo_off=lo_off+1;
   last_hi=gate_hi;last_lo=gate_lo;
   #1;clk=1;#4;clk=0;
 end
 if(received==0)errors=errors+1;
 if(expects_trip && !fault_latched)begin missed_shutdown=1;errors=errors+1;end
 if(sc_active && !shutdown_seen)begin missed_shutdown=1;errors=errors+1;end
 $display("RESULT seed=%0d target=%0d target2=%0d cycle=%0d window=%0d mode=%0d scenario=%0d applied=%0d applied2=%0d before=%0d after=%0d before2=%0d after2=%0d sent=1 received=%0d errors=%0d unexpected=%0d timeout=0 alarms=%0d shootthrough=%0d deadtime_violation=%0d missed_shutdown=%0d late_shutdown=%0d false_trip=%0d desat_blanking_violation=%0d miller_clamp_violation=%0d parasitic_turnon_proxy=%0d thermal_derate_violation=%0d unsafe_gate_pulse=%0d",
 seed,target,target2,fault_cycle,window,mode,scenario,applied,applied2,before_value,after_value,before2,after2,received,errors,unexpected,alarms,
 shootthrough,deadtime_violation,missed_shutdown,late_shutdown,false_trip,desat_blanking_violation,miller_clamp_violation,parasitic_turnon_proxy,thermal_derate_violation,unsafe_gate_pulse);
 if($test$plusargs("stream"))begin $display("DONE_EVENT");$fflush();end else $finish;
end
end
endmodule
'''.replace('CASES2','\n'.join(cases2)).replace('CASES','\n'.join(cases))
    with Path(path).open('a') as f:f.write(more)
    return Path(path)
