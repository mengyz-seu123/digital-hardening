// Compact specialization of the primary interface protection policies.
// Scientific semantics are unchanged; only inactive policy state is removed.
module context_guard #(parameter MODE=0, SAMPLES=2, GUARD=32)(
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

module context_controller #(parameter MODE=0,SAMPLES=2,GUARD=32)(
 input wire clk,rst,pwm_hi,pwm_lo,desat_hi,desat_lo,oc_fault,uvlo_ok,ot_warn,cfg_we,switch_marker,
 input wire [2:0] cfg_addr, input wire [7:0] cfg_wdata,
 output wire gate_hi,gate_lo,miller_clamp_hi,miller_clamp_lo,fault_latched,softoff_active,
 output wire [1:0] drive_strength, output wire [7:0] status
);
wire f;
context_guard #(.MODE(MODE),.SAMPLES(SAMPLES),.GUARD(GUARD)) g(
 .clk(clk),.rst(rst),.fault_in(oc_fault),.switch_marker(switch_marker),.fault_out(f));

top core(.clk(clk),.rst(rst),.pwm_hi(pwm_hi),.pwm_lo(pwm_lo),.desat_hi(desat_hi),.desat_lo(desat_lo),
 .oc_fault(f),.uvlo_ok(uvlo_ok),.ot_warn(ot_warn),.cfg_we(cfg_we),.cfg_addr(cfg_addr),.cfg_wdata(cfg_wdata),
 .gate_hi(gate_hi),.gate_lo(gate_lo),.miller_clamp_hi(miller_clamp_hi),.miller_clamp_lo(miller_clamp_lo),
 .fault_latched(fault_latched),.softoff_active(softoff_active),.drive_strength(drive_strength),.status(status));
endmodule
