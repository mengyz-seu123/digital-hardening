from pathlib import Path
SCENARIOS=['normal','rapid_u_toggle','simultaneous_edges','fault_u_high','overcurrent_v','fault_commutation','stop_restart','clear_while_fault','clean_clear_restart','all_high_commands','burst_toggle','short_fault_pulse']
SCENARIO_COUNT=len(SCENARIOS)
FAULT_SCENARIOS={3,4,5,7,8,11}
SEVERE=['shootthrough','deadtime_violation','missed_shutdown','unsafe_restart','unsafe_gate_pulse']


def make_tb(cfg,physical,path,module='dut'):
    cases=[]
    for r in physical:
        i=r['physical_id'];q=r['path'];cases.append("%d: begin before_value=%s; %s=~%s; after_value=%s; end"%(i,q,q,q,q))
    ports='''reg start_cmd=0,stop_cmd=0,fault_clear=0,fault_in=0,oc_fault=0;reg[2:0]pwm_cmd=0;
wire[2:0]gate_hi,gate_lo;wire fault_latched,run_active;wire[3:0]status;
DUT dut(.clk(clk),.rst(rst),.start_cmd(start_cmd),.stop_cmd(stop_cmd),.fault_clear(fault_clear),.fault_in(fault_in),.oc_fault(oc_fault),.pwm_cmd(pwm_cmd),.gate_hi(gate_hi),.gate_lo(gate_lo),.fault_latched(fault_latched),.run_active(run_active),.status(status));'''.replace('DUT',module)
    text=r'''`timescale 1ns/1ps
module tb;
reg clk=0,rst=1;
PORTS
integer seed,target,fault_cycle,window,mode,c,scenario,m,errors,applied,before_value,after_value;
integer shootthrough,deadtime_violation,missed_shutdown,false_trip,unsafe_restart,unsafe_gate_pulse;
integer fault_started,fault_wait,received,restarted;
integer u_hi_off,u_lo_off,v_hi_off,v_lo_off,w_hi_off,w_lo_off;
reg[2:0]last_hi,last_lo;
initial begin
forever begin
 clk=0;rst=1;start_cmd=0;stop_cmd=0;fault_clear=0;fault_in=0;oc_fault=0;pwm_cmd=0;
 seed=1;target=-1;fault_cycle=12;window=144;mode=0;
 if($value$plusargs("seed=%d",seed))begin end
 if($value$plusargs("target=%d",target))begin end
 if($value$plusargs("cycle=%d",fault_cycle))begin end
 if($value$plusargs("window=%d",window))begin end
 if($value$plusargs("mode=%d",mode))begin end
 scenario=seed%12;errors=0;applied=0;before_value=-1;after_value=-1;
 shootthrough=0;deadtime_violation=0;missed_shutdown=0;false_trip=0;unsafe_restart=0;unsafe_gate_pulse=0;
 fault_started=0;fault_wait=0;received=0;restarted=0;u_hi_off=99;u_lo_off=99;v_hi_off=99;v_lo_off=99;w_hi_off=99;w_lo_off=99;last_hi=0;last_lo=0;
 repeat(3)begin #5;clk=1;#5;clk=0;end rst=0;
 for(c=0;c<window;c=c+1)begin
   m=c%96;start_cmd=0;stop_cmd=0;fault_clear=0;fault_in=0;oc_fault=0;
   pwm_cmd[0]=(m<32);pwm_cmd[1]=(m>=16&&m<48);pwm_cmd[2]=(m>=32&&m<64);
   if(c==2)start_cmd=1;
   if(scenario==1)pwm_cmd[0]=((c/8)%2)==0;
   if(scenario==2 && (m==31||m==63))pwm_cmd=~pwm_cmd;
   if(scenario==3 && c>=50 && c<56)fault_in=1;
   if(scenario==4 && c==70)oc_fault=1;
   if(scenario==5 && c>=31 && c<35)fault_in=1;
   if(scenario==6)begin if(c==60)stop_cmd=1;if(c==72)start_cmd=1;end
   if(scenario==7)begin if(c>=45 && c<66)fault_in=1;if(c==50)fault_clear=1;if(c==55)start_cmd=1;end
   if(scenario==8)begin if(c>=45 && c<50)fault_in=1;if(c==55)fault_clear=1;if(c==62)start_cmd=1;end
   if(scenario==9 && c>=40 && c<52)pwm_cmd=3'b111;
   if(scenario==10 && c>=36 && c<68)pwm_cmd={(c%5)<2,(c%7)<3,(c%3)==0};
   if(scenario==11 && c==85)fault_in=1;
   if(mode==2)begin start_cmd=0;stop_cmd=0;fault_clear=0;fault_in=0;oc_fault=0;pwm_cmd=0;end
   #1;
   if(c==fault_cycle && target>=0)begin
      case(target)
CASES
      default:before_value=-1;
      endcase
      if((before_value==0||before_value==1)&&after_value==(1-before_value))applied=1;
   end
   #3;
   if((gate_hi[0]&&gate_lo[0])||(gate_hi[1]&&gate_lo[1])||(gate_hi[2]&&gate_lo[2]))begin shootthrough=1;unsafe_gate_pulse=1;errors=errors+1;end
   if(gate_hi[0]&&!last_hi[0]&&u_lo_off<2)begin deadtime_violation=1;errors=errors+1;end
   if(gate_lo[0]&&!last_lo[0]&&u_hi_off<2)begin deadtime_violation=1;errors=errors+1;end
   if(gate_hi[1]&&!last_hi[1]&&v_lo_off<2)begin deadtime_violation=1;errors=errors+1;end
   if(gate_lo[1]&&!last_lo[1]&&v_hi_off<2)begin deadtime_violation=1;errors=errors+1;end
   if(gate_hi[2]&&!last_hi[2]&&w_lo_off<2)begin deadtime_violation=1;errors=errors+1;end
   if(gate_lo[2]&&!last_lo[2]&&w_hi_off<2)begin deadtime_violation=1;errors=errors+1;end
   if(fault_latched&&(run_active||(|gate_hi)||(|gate_lo)))begin unsafe_restart=1;unsafe_gate_pulse=1;errors=errors+1;end
   if((scenario==3||scenario==4||scenario==5||scenario==7||scenario==8||scenario==11)&&!fault_started&&(fault_in||oc_fault))begin fault_started=1;fault_wait=3;end
   if(fault_wait>0)begin fault_wait=fault_wait-1;if(fault_wait==0&&((|gate_hi)||(|gate_lo)||!fault_latched))begin missed_shutdown=1;errors=errors+1;end end
   if(!(scenario==3||scenario==4||scenario==5||scenario==7||scenario==8||scenario==11)&&fault_latched)begin false_trip=1;errors=errors+1;end
   if(scenario==8&&c>65&&run_active)restarted=1;
   if(run_active&&((|gate_hi)||(|gate_lo)))received=1;
   if(gate_hi[0])u_hi_off=0;else if(u_hi_off<99)u_hi_off=u_hi_off+1;
   if(gate_lo[0])u_lo_off=0;else if(u_lo_off<99)u_lo_off=u_lo_off+1;
   if(gate_hi[1])v_hi_off=0;else if(v_hi_off<99)v_hi_off=v_hi_off+1;
   if(gate_lo[1])v_lo_off=0;else if(v_lo_off<99)v_lo_off=v_lo_off+1;
   if(gate_hi[2])w_hi_off=0;else if(w_hi_off<99)w_hi_off=w_hi_off+1;
   if(gate_lo[2])w_lo_off=0;else if(w_lo_off<99)w_lo_off=w_lo_off+1;
   last_hi=gate_hi;last_lo=gate_lo;
   #1;clk=1;#4;clk=0;
 end
 if(received==0)errors=errors+1;
 if((scenario==3||scenario==4||scenario==5||scenario==7||scenario==11)&&!fault_latched)begin missed_shutdown=1;errors=errors+1;end
 if(scenario==8&&!restarted)begin unsafe_restart=1;errors=errors+1;end
 $display("RESULT seed=%0d target=%0d cycle=%0d window=%0d mode=%0d scenario=%0d applied=%0d before=%0d after=%0d sent=1 received=%0d errors=%0d unexpected=0 timeout=0 alarms=0 shootthrough=%0d deadtime_violation=%0d missed_shutdown=%0d false_trip=%0d unsafe_restart=%0d unsafe_gate_pulse=%0d",
 seed,target,fault_cycle,window,mode,scenario,applied,before_value,after_value,received,errors,shootthrough,deadtime_violation,missed_shutdown,false_trip,unsafe_restart,unsafe_gate_pulse);
 if($test$plusargs("stream"))begin $display("DONE_EVENT");$fflush();end else $finish;
end
end
endmodule
'''.replace('PORTS',ports).replace('CASES','\n'.join(cases))
    Path(path).write_text(text,encoding='utf-8')
    return path
