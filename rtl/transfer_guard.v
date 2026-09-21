// Transfer wrapper using the shared interface protection semantics.
module ext_context_guard #(parameter MODE=0, SAMPLES=2, GUARD=32)(
 input wire clk,rst,fault_in,switch_marker,
 output wire fault_out
);
localparam NW=(SAMPLES<2)?1:$clog2(SAMPLES+1);
localparam GW=(GUARD<2)?1:$clog2(GUARD+1);
generate
 if (MODE==0) begin: g_raw
   assign fault_out = fault_in;
 end else if (MODE==1) begin: g_fixed
   reg [NW-1:0] count;
   always @(posedge clk) begin
     if (rst) count <= 0;
     else if (!fault_in) count <= 0;
     else if (count < SAMPLES) count <= count + 1'b1;
   end
   assign fault_out = fault_in && count >= SAMPLES-1;
 end else begin: g_blanking
   reg [GW-1:0] left;
   always @(posedge clk) begin
     if (rst) left <= 0;
     else if (switch_marker) left <= GUARD;
     else if (left != 0) left <= left - 1'b1;
   end
   assign fault_out = fault_in && !switch_marker && left == 0;
 end
endgenerate
endmodule

module ext_context_controller #(parameter MODE=0,SAMPLES=2,GUARD=32)(
 input wire clk,rst,start_cmd,stop_cmd,fault_clear,fault_in,oc_fault,switch_marker,
 input wire [2:0] pwm_cmd,
 output wire [2:0] gate_hi,
 output wire [2:0] gate_lo,
 output wire fault_latched,run_active,
 output wire [3:0] status
);
wire f;
ext_context_guard #(.MODE(MODE),.SAMPLES(SAMPLES),.GUARD(GUARD)) g(
 .clk(clk),.rst(rst),.fault_in(fault_in),.switch_marker(switch_marker),.fault_out(f));
top core(.clk(clk),.rst(rst),.start_cmd(start_cmd),.stop_cmd(stop_cmd),
 .fault_clear(fault_clear),.fault_in(f),.oc_fault(oc_fault),.pwm_cmd(pwm_cmd),
 .gate_hi(gate_hi),.gate_lo(gate_lo),.fault_latched(fault_latched),
 .run_active(run_active),.status(status));
endmodule
