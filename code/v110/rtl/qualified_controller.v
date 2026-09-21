// Exploratory conventional temporal qualification; not a novel TMR algorithm.
module fault_qualifier #(parameter SAMPLES = 4)(
    input wire clk, rst, fault_in,
    output reg fault_out
);
localparam WIDTH = $clog2(SAMPLES);
reg [WIDTH-1:0] count;
always @(posedge clk) begin
    if (rst || !fault_in) begin
        count <= 0;
        fault_out <= 0;
    end else if (count == SAMPLES-1) begin
        fault_out <= 1;
    end else begin
        count <= count + 1'b1;
    end
end
endmodule

module qualified_controller #(parameter SAMPLES = 0)(
    input wire clk, rst, pwm_hi, pwm_lo, desat_hi, desat_lo, oc_fault,
    input wire uvlo_ok, ot_warn, cfg_we,
    input wire [2:0] cfg_addr,
    input wire [7:0] cfg_wdata,
    output wire gate_hi, gate_lo, miller_clamp_hi, miller_clamp_lo,
    output wire fault_latched, softoff_active,
    output wire [1:0] drive_strength,
    output wire [7:0] status
);
wire fault_qualified;
generate
    if (SAMPLES == 0) begin : bypass
        assign fault_qualified = oc_fault;
    end else begin : qualify
        fault_qualifier #(.SAMPLES(SAMPLES)) q(
            .clk(clk), .rst(rst), .fault_in(oc_fault), .fault_out(fault_qualified));
    end
endgenerate
top core(.clk(clk), .rst(rst), .pwm_hi(pwm_hi), .pwm_lo(pwm_lo),
    .desat_hi(desat_hi), .desat_lo(desat_lo), .oc_fault(fault_qualified),
    .uvlo_ok(uvlo_ok), .ot_warn(ot_warn), .cfg_we(cfg_we),
    .cfg_addr(cfg_addr), .cfg_wdata(cfg_wdata),
    .gate_hi(gate_hi), .gate_lo(gate_lo), .miller_clamp_hi(miller_clamp_hi),
    .miller_clamp_lo(miller_clamp_lo), .fault_latched(fault_latched),
    .softoff_active(softoff_active), .drive_strength(drive_strength), .status(status));
endmodule
