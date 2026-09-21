from pathlib import Path
import json, re, subprocess, time, zlib
import hardware as h


def catalog():
    p=h.PROJECT; rtl=p/'rtl'; rtl.mkdir(exist_ok=True)
    original=(h.ROOT/'rtl/top.v').read_text(); designs=[]
    for name,kind in [('axis_simple8',1),('axis_skid8',2)]:
        f=rtl/(name+'.v');f.write_text(original.replace('.REG_TYPE(1)','.REG_TYPE(%d)'%kind))
        designs.append(dict(design_id=name,family_id='verilog-axis',bench='axis',sources=[str(p/'upstream/verilog-axis__axis_register.v'),str(f)],output_gates={'m_data':'m_valid'},window=128,cycle_min=8,cycle_max=40,transactions=32))
    tx=rtl/'uart_tx8.v';tx.write_text('''module top(input wire clk,rst,input wire [7:0] s_data,input wire s_valid,
output wire s_ready,txd,busy);
uart_tx #(.DATA_WIDTH(8)) core(.clk(clk),.rst(rst),.s_axis_tdata(s_data),.s_axis_tvalid(s_valid),
.s_axis_tready(s_ready),.txd(txd),.busy(busy),.prescale(16'd1));
endmodule
''')
    designs.append(dict(design_id='uart_tx8',family_id='verilog-uart',bench='uart_tx',sources=[str(p/'upstream/verilog-uart__uart_tx.v'),str(tx)],output_gates={},window=512,cycle_min=10,cycle_max=350,transactions=4))
    rx=rtl/'uart_rx8.v';rx.write_text('''module top(input wire clk,rst,rxd,m_ready,output wire [7:0] m_data,
output wire m_valid,busy,overrun_error,frame_error);
uart_rx #(.DATA_WIDTH(8)) core(.clk(clk),.rst(rst),.rxd(rxd),.m_axis_tready(m_ready),
.m_axis_tdata(m_data),.m_axis_tvalid(m_valid),.busy(busy),.overrun_error(overrun_error),.frame_error(frame_error),.prescale(16'd1));
endmodule
''')
    designs.append(dict(design_id='uart_rx8',family_id='verilog-uart',bench='uart_rx',sources=[str(p/'upstream/verilog-uart__uart_rx.v'),str(rx)],output_gates={'m_data':'m_valid'},window=512,cycle_min=10,cycle_max=380,transactions=4))
    fcs=rtl/'eth_fcs8.v';fcs.write_text('''module top(input wire clk,rst,input wire [7:0] s_data,input wire s_valid,s_last,
output wire s_ready,output wire [31:0] fcs,output wire fcs_valid);
axis_eth_fcs #(.DATA_WIDTH(8),.KEEP_ENABLE(0),.KEEP_WIDTH(1)) core(.clk(clk),.rst(rst),
.s_axis_tdata(s_data),.s_axis_tkeep(1'b1),.s_axis_tvalid(s_valid),.s_axis_tready(s_ready),
.s_axis_tlast(s_last),.s_axis_tuser(1'b0),.output_fcs(fcs),.output_fcs_valid(fcs_valid));
endmodule
''')
    designs.append(dict(design_id='eth_fcs8',family_id='verilog-ethernet',bench='crc',sources=[str(p/'upstream/verilog-ethernet__lfsr.v'),str(p/'upstream/verilog-ethernet__axis_eth_fcs.v'),str(fcs)],output_gates={'fcs':'fcs_valid'},window=160,cycle_min=20,cycle_max=70,transactions=4))
    return designs


PORTS={
'axis':'''reg [7:0] s_data=0; reg s_valid=0,m_ready=0;wire s_ready,m_valid;wire[7:0]m_data;
DUT dut(.clk(clk),.rst(rst),.s_data(s_data),.s_valid(s_valid),.m_ready(m_ready),.s_ready(s_ready),.m_valid(m_valid),.m_data(m_data));''',
'uart_tx':'''reg [7:0]s_data=0;reg s_valid=0;wire s_ready,txd,busy;
DUT dut(.clk(clk),.rst(rst),.s_data(s_data),.s_valid(s_valid),.s_ready(s_ready),.txd(txd),.busy(busy));
integer serial_active,serial_wait,serial_bit;reg[7:0]serial_data;''',
'uart_rx':'''reg rxd=1,m_ready=1;wire[7:0]m_data;wire m_valid,busy,overrun_error,frame_error;
DUT dut(.clk(clk),.rst(rst),.rxd(rxd),.m_ready(m_ready),.m_data(m_data),.m_valid(m_valid),.busy(busy),.overrun_error(overrun_error),.frame_error(frame_error));
integer frame_index,frame_cycle;reg[7:0]serial_data;''',
'crc':'''reg[7:0]s_data=0;reg s_valid=0,s_last=0;wire s_ready,fcs_valid;wire[31:0]fcs;
DUT dut(.clk(clk),.rst(rst),.s_data(s_data),.s_valid(s_valid),.s_last(s_last),.s_ready(s_ready),.fcs(fcs),.fcs_valid(fcs_valid));
reg[31:0]expected_fcs[0:3];reg[31:0]fcs_arg;'''
}
DRIVE={
'axis':'''if(sent<32 && (pending || rng[2:0]!=0 || c<4))pending=1;
s_valid=pending;s_data=transaction(sent,seed);m_ready=(rng[6:4]!=0);
if(c<4)m_ready=0; if(c>=100)m_ready=1;
if(mode==1 && c>=2)begin s_valid=0;pending=0;m_ready=0;end
if(mode==2)begin s_valid=0;pending=0;m_ready=1;s_data=0;end''',
'uart_tx':'''if(sent<4 && (pending || rng[2:0]!=0))pending=1;
s_valid=pending;s_data=transaction(sent,seed);
if(mode==2)begin s_valid=0;pending=0;end''',
'uart_rx':'''rxd=1;frame_index=(c-8)/96;frame_cycle=(c-8)%96;
if(c>=8 && frame_index<4)begin
 serial_data=transaction(frame_index,seed);
 if(frame_cycle<8)rxd=0;
 else if(frame_cycle<72)rxd=serial_data[(frame_cycle/8)-1];
end
m_ready=(rng[6:4]!=0);if(c>=450)m_ready=1;
if(mode==2)rxd=1;''',
'crc':'''if(sent<64 && (sent<16 || pending || rng[2:0]!=0))pending=1;
s_valid=pending;s_data=transaction(sent,seed);s_last=(sent%16==15);
if(mode==2)begin s_valid=0;pending=0;end'''
}
CHECK={
'axis':'''if(mode==0 && m_valid && m_ready)begin
 if(received>=sent)unexpected=unexpected+1;
 if(m_data !== transaction(received,seed))errors=errors+1;
 received=received+1;
end
if(mode==2 && m_valid && m_ready)unexpected=unexpected+1;
if(s_valid && s_ready)begin sent=sent+1;pending=0;end''',
'uart_tx':'''if(serial_active==0)begin
 if(txd===1'b0)begin serial_active=1;serial_wait=11;serial_bit=0;serial_data=0;end
end else begin
 if(serial_wait>0)serial_wait=serial_wait-1;
 else if(serial_bit<8)begin
   serial_data[serial_bit]=txd;serial_bit=serial_bit+1;serial_wait=7;
 end else begin
   if(txd!==1'b1)errors=errors+1;
   if(received>=sent)unexpected=unexpected+1;
   if(serial_data !== transaction(received,seed))errors=errors+1;
   received=received+1;serial_active=0;
 end
end
if(s_valid && s_ready)begin sent=sent+1;pending=0;end''',
'uart_rx':'''if(m_valid && m_ready)begin
 if(received>=4 || c<8+76+96*received)unexpected=unexpected+1;
 if(m_data !== transaction(received,seed))errors=errors+1;
 received=received+1;
end
if(overrun_error || frame_error)alarms=alarms+1;
sent=4;''',
'crc':'''if(fcs_valid)begin
 if(received>=4 || received>=sent/16)unexpected=unexpected+1;
 if(received<4 && fcs!==expected_fcs[received])errors=errors+1;
 received=received+1;
end
if(s_valid && s_ready)begin sent=sent+1;pending=0;end'''
}


def make_tb(cfg,physical,path,module='dut'):
    bench=cfg['bench'];n=max(1,len(physical));cases=[];reads=[]
    for r in physical:
        i=r['physical_id'];q=r['path']
        cases.append('%d: begin before_value=%s;%s=~%s;after_value=%s;end'%(i,q,q,q,q))
        reads.append('%d: sampled=%s;'%(i,q))
    canonical=bool(physical) and all('.q_' in r['path'] for r in physical)
    features=[]
    for r in physical:
        i=r['physical_id'];bit=r['bit_id']
        cond=('dut.en_%d'%bit) if r['ff_type'] in ('$dffe','$sdffe','$sdffce') else "1'b1"
        if canonical:features.append('if(%s)writes[%d]=writes[%d]+1;'%(cond,i,i))
    observed="{%s}"%','.join(r['path'] for r in reversed(physical)) if physical else "1'b0"
    init=''
    if bench=='uart_tx':init='serial_active=0;serial_wait=0;serial_bit=0;serial_data=0;'
    if bench=='crc':
        init='\n'.join('if($value$plusargs("fcs%d=%%h",fcs_arg))expected_fcs[%d]=fcs_arg;'%(i,i) for i in range(4))
    if bench=='crc':init+='\n'+''.join('if(stream_mode)expected_fcs[%d]=stream_fcs%d;'%(i,i) for i in range(4))
    reset_inputs={'axis':'s_data=0;s_valid=0;m_ready=0;', 'uart_tx':'s_data=0;s_valid=0;', 'uart_rx':'rxd=1;m_ready=1;', 'crc':'s_data=0;s_valid=0;s_last=0;'}[bench]
    template=r'''`timescale 1ns/1ps
module tb;
reg clk=0,rst=1;
PORTS
integer seed,target,fault_cycle,window,mode,c,sent,received,errors,unexpected,alarms,timeout_error;
integer applied,before_value,after_value,profile,j,hold_checks,hold_errors;
integer stream_mode,stream_fd,stream_scan; reg[31:0]stream_fcs0,stream_fcs1,stream_fcs2,stream_fcs3;
integer toggles[0:NMINUS],writes[0:NMINUS],unknowns[0:NMINUS];
reg [NMINUS:0]previous_state;wire[NMINUS:0]observed=OBSERVED;
reg[31:0]rng;reg pending,sampled;
function[7:0]transaction;
input integer idx,key;
begin transaction=(idx*73+key*19+(idx^(key>>8)))&255;end
endfunction
task sample_state;begin sampled=1'bx;case(target)
READS
default:sampled=1'bx;endcase end endtask
initial begin
stream_mode=$test$plusargs("stream");
if(stream_mode)stream_fd=$fopen("/dev/stdin","r");
forever begin
clk=0;rst=1;RESETINPUTS
seed=1;target=-1;fault_cycle=8;window=WINDOW;mode=0;profile=0;
if($value$plusargs("seed=%d",seed))begin end
if($value$plusargs("target=%d",target))begin end
if($value$plusargs("cycle=%d",fault_cycle))begin end
if($value$plusargs("window=%d",window))begin end
if($value$plusargs("mode=%d",mode))begin end
if($value$plusargs("profile=%d",profile))begin end
if(stream_mode)begin
stream_scan=$fscanf(stream_fd,"%d %d %d %d %d %d %h %h %h %h",seed,target,fault_cycle,window,mode,profile,stream_fcs0,stream_fcs1,stream_fcs2,stream_fcs3);
if(stream_scan!=10)$finish;
end
sent=0;received=0;errors=0;unexpected=0;alarms=0;applied=0;pending=0;
before_value=-1;after_value=-1;hold_checks=0;hold_errors=0;rng=seed;
for(j=0;j<N;j=j+1)begin toggles[j]=0;writes[j]=0;unknowns[j]=0;end
INIT
repeat(3)begin #5;clk=1;#5;clk=0;end
rst=0;previous_state=observed;
for(c=0;c<window;c=c+1)begin
 rng=rng^(rng<<13);rng=rng^(rng>>17);rng=rng^(rng<<5);
 DRIVE
 #1;
 if(c==fault_cycle && target>=0)begin
 case(target)
 CASES
 default:before_value=-1;
 endcase
 if((before_value==0||before_value==1)&&after_value==(1-before_value))applied=1;
 end
 #3;
 CHECK
 if(profile)begin WRITES end
 #1;clk=1;
 #1;
 if(profile)begin
 for(j=0;j<N;j=j+1)begin
  if(observed[j]!==1'b0 && observed[j]!==1'b1)unknowns[j]=unknowns[j]+1;
  else if(observed[j]!==previous_state[j])toggles[j]=toggles[j]+1;
 end
 previous_state=observed;
 end
 if(mode==1 && c>=fault_cycle && c<fault_cycle+4)begin
 sample_state;hold_checks=hold_checks+1;
 if(sampled!==after_value[0])hold_errors=hold_errors+1;
 end
 #4;clk=0;
end
timeout_error=(mode==0 && received!=TRANSACTIONS);
$display("RESULT seed=%0d target=%0d cycle=%0d window=%0d mode=%0d applied=%0d before=%0d after=%0d sent=%0d received=%0d errors=%0d unexpected=%0d timeout=%0d alarms=%0d hold_checks=%0d hold_errors=%0d",
seed,target,fault_cycle,window,mode,applied,before_value,after_value,sent,received,errors,unexpected,timeout_error,alarms,hold_checks,hold_errors);
if(profile)for(j=0;j<N;j=j+1)$display("FEATURE bit=%0d toggles=%0d writes=%0d unknowns=%0d",j,toggles[j],writes[j],unknowns[j]);
if(stream_mode)begin $display("DONE_EVENT");$fflush();end else $finish;
end
end
endmodule
'''
    replacements = {
        'RESETINPUTS':reset_inputs, 'PORTS': PORTS[bench].replace('DUT', module), 'NMINUS': str(n-1),
        'OBSERVED': observed, 'READS': '\n'.join(reads), 'WINDOW': str(cfg['window']),
        'INIT': init, 'DRIVE': DRIVE[bench], 'CASES': '\n'.join(cases),
        'CHECK': CHECK[bench], 'WRITES': '\n'.join(features),
        'TRANSACTIONS': str(cfg['transactions']),
    }
    for key, value in replacements.items():
        template = re.sub(r'\b' + key + r'\b', lambda _: value, template)
    template = re.sub(r'\bN\b', str(n), template)
    Path(path).write_text(template, encoding='utf-8')
    return Path(path)
