// Existing edge-blanking primitive, configured by the deadline compiler.
module context_guard #(parameter MODE=0, SAMPLES=2, GUARD=32)(
 input wire clk,rst,fault_in,switch_marker,
 output wire fault_out
);
localparam NW=(SAMPLES<2)?1:$clog2(SAMPLES+1);
localparam GW=(GUARD<2)?1:$clog2(GUARD+1);
reg [NW-1:0] count;
reg [GW-1:0] left;
always @(posedge clk) begin
 if (rst) begin count<=0;left<=0; end
 else begin
  if (switch_marker) left<=GUARD;
  else if(left!=0) left<=left-1'b1;
  if(!fault_in) count<=0;
  else if(count<SAMPLES) count<=count+1'b1;
 end
end
// Raw and edge-blanking preserve immediate steady-state fault semantics.
assign fault_out = MODE==0 ? fault_in :
                   MODE==1 ? (fault_in && count>=SAMPLES-1) :
                   (fault_in && !switch_marker && left==0);
endmodule

module context_controller #(parameter MODE=0,SAMPLES=2,GUARD=32)(
 input wire clk,rst,pwm_hi,pwm_lo,desat_hi,desat_lo,oc_fault,uvlo_ok,ot_warn,cfg_we,switch_marker,
 input wire [2:0] cfg_addr, input wire [7:0] cfg_wdata,
 output wire gate_hi,gate_lo,miller_clamp_hi,miller_clamp_lo,fault_latched,softoff_active,
 output wire [1:0] drive_strength, output wire [7:0] status
);
wire f;
context_guard #(.MODE(MODE),.SAMPLES(SAMPLES),.GUARD(GUARD)) g(clk,rst,oc_fault,switch_marker,f);
top core(.clk(clk),.rst(rst),.pwm_hi(pwm_hi),.pwm_lo(pwm_lo),.desat_hi(desat_hi),.desat_lo(desat_lo),
 .oc_fault(f),.uvlo_ok(uvlo_ok),.ot_warn(ot_warn),.cfg_we(cfg_we),.cfg_addr(cfg_addr),.cfg_wdata(cfg_wdata),
 .gate_hi(gate_hi),.gate_lo(gate_lo),.miller_clamp_hi(miller_clamp_hi),.miller_clamp_lo(miller_clamp_lo),
 .fault_latched(fault_latched),.softoff_active(softoff_active),.drive_strength(drive_strength),.status(status));
endmodule
