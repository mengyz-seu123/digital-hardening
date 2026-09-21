// Clean-room transfer controller derived from public Microchip motor-control
// architecture documentation: complementary 3-phase PWM, break-before-make
// dead time, overcurrent/fault shutdown, and start/stop/fault-clear sequencing.
// This is NOT Microchip RTL.
module top(
    input  wire       clk,
    input  wire       rst,
    input  wire       start_cmd,
    input  wire       stop_cmd,
    input  wire       fault_clear,
    input  wire       fault_in,
    input  wire       oc_fault,
    input  wire [2:0] pwm_cmd,
    output reg  [2:0] gate_hi,
    output reg  [2:0] gate_lo,
    output reg        fault_latched,
    output reg        run_active,
    output wire [3:0] status
);
localparam [1:0] ST_IDLE=2'd0, ST_RUN=2'd1, ST_FAULT=2'd2;
localparam [1:0] DEAD_CYC=2'd2;
reg [1:0] state;
reg [1:0] dead_u, dead_v, dead_w;
assign status={fault_latched,state,run_active};

always @(posedge clk) begin
    if (rst) begin
        state <= ST_IDLE;
        fault_latched <= 1'b0;
        run_active <= 1'b0;
        gate_hi <= 3'b000;
        gate_lo <= 3'b000;
        dead_u <= 2'd0;
        dead_v <= 2'd0;
        dead_w <= 2'd0;
    end else if (fault_in || oc_fault) begin
        state <= ST_FAULT;
        fault_latched <= 1'b1;
        run_active <= 1'b0;
        gate_hi <= 3'b000;
        gate_lo <= 3'b000;
        dead_u <= 2'd0;
        dead_v <= 2'd0;
        dead_w <= 2'd0;
    end else begin
        case (state)
        ST_IDLE: begin
            run_active <= 1'b0;
            gate_hi <= 3'b000;
            gate_lo <= 3'b000;
            dead_u <= 2'd0;
            dead_v <= 2'd0;
            dead_w <= 2'd0;
            if (start_cmd && !fault_latched) begin
                state <= ST_RUN;
                run_active <= 1'b1;
            end
        end
        ST_RUN: begin
            run_active <= 1'b1;
            if (stop_cmd) begin
                state <= ST_IDLE;
                run_active <= 1'b0;
                gate_hi <= 3'b000;
                gate_lo <= 3'b000;
                dead_u <= 2'd0;
                dead_v <= 2'd0;
                dead_w <= 2'd0;
            end else begin
                // Phase U
                if (dead_u != 0) begin
                    dead_u <= dead_u - 1'b1;
                    gate_hi[0] <= 1'b0; gate_lo[0] <= 1'b0;
                end else if (pwm_cmd[0]) begin
                    if (gate_lo[0]) begin
                        gate_hi[0] <= 1'b0; gate_lo[0] <= 1'b0; dead_u <= DEAD_CYC;
                    end else begin
                        gate_hi[0] <= 1'b1; gate_lo[0] <= 1'b0;
                    end
                end else begin
                    if (gate_hi[0]) begin
                        gate_hi[0] <= 1'b0; gate_lo[0] <= 1'b0; dead_u <= DEAD_CYC;
                    end else begin
                        gate_hi[0] <= 1'b0; gate_lo[0] <= 1'b1;
                    end
                end
                // Phase V
                if (dead_v != 0) begin
                    dead_v <= dead_v - 1'b1;
                    gate_hi[1] <= 1'b0; gate_lo[1] <= 1'b0;
                end else if (pwm_cmd[1]) begin
                    if (gate_lo[1]) begin
                        gate_hi[1] <= 1'b0; gate_lo[1] <= 1'b0; dead_v <= DEAD_CYC;
                    end else begin
                        gate_hi[1] <= 1'b1; gate_lo[1] <= 1'b0;
                    end
                end else begin
                    if (gate_hi[1]) begin
                        gate_hi[1] <= 1'b0; gate_lo[1] <= 1'b0; dead_v <= DEAD_CYC;
                    end else begin
                        gate_hi[1] <= 1'b0; gate_lo[1] <= 1'b1;
                    end
                end
                // Phase W
                if (dead_w != 0) begin
                    dead_w <= dead_w - 1'b1;
                    gate_hi[2] <= 1'b0; gate_lo[2] <= 1'b0;
                end else if (pwm_cmd[2]) begin
                    if (gate_lo[2]) begin
                        gate_hi[2] <= 1'b0; gate_lo[2] <= 1'b0; dead_w <= DEAD_CYC;
                    end else begin
                        gate_hi[2] <= 1'b1; gate_lo[2] <= 1'b0;
                    end
                end else begin
                    if (gate_hi[2]) begin
                        gate_hi[2] <= 1'b0; gate_lo[2] <= 1'b0; dead_w <= DEAD_CYC;
                    end else begin
                        gate_hi[2] <= 1'b0; gate_lo[2] <= 1'b1;
                    end
                end
            end
        end
        default: begin // ST_FAULT and illegal state fail safe
            run_active <= 1'b0;
            gate_hi <= 3'b000;
            gate_lo <= 3'b000;
            dead_u <= 2'd0;
            dead_v <= 2'd0;
            dead_w <= 2'd0;
            if (fault_clear) begin
                fault_latched <= 1'b0;
                state <= ST_IDLE;
            end
        end
        endcase
    end
end
endmodule
