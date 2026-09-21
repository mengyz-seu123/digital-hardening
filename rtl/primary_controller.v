module top(
    input wire clk, input wire rst,
    input wire pwm_hi, input wire pwm_lo,
    input wire desat_hi, input wire desat_lo, input wire oc_fault,
    input wire uvlo_ok, input wire ot_warn,
    input wire cfg_we, input wire [2:0] cfg_addr, input wire [7:0] cfg_wdata,
    output reg gate_hi, output reg gate_lo,
    output reg miller_clamp_hi, output reg miller_clamp_lo,
    output reg fault_latched, output reg softoff_active,
    output reg [1:0] drive_strength, output reg [7:0] status
);
localparam [6:0] DESAT_BLANK_CYCLES = 7'd25;
reg [1:0] pwm_hi_hist, pwm_lo_hist;
reg req_hi, req_lo;
reg [2:0] hi_off_age, lo_off_age;
reg [1:0] desat_hi_cnt, desat_lo_cnt;
reg [6:0] desat_blank_cnt;
reg [1:0] thermal_cnt, thermal_state;
reg [2:0] softoff_count;
reg [2:0] cfg_deadtime;
reg [1:0] cfg_slew;
reg [1:0] cfg_filter;
reg [2:0] fault_code;
reg [3:0] watchdog;
reg [3:0] edge_count;
reg [3:0] diag_count;
reg prev_hi, prev_lo;
wire [1:0] desat_filter = (cfg_filter < 2) ? 2 : cfg_filter;

always @(posedge clk) begin
    if (rst) begin
        pwm_hi_hist <= 0; pwm_lo_hist <= 0;
        req_hi <= 0; req_lo <= 0;
        hi_off_age <= 7; lo_off_age <= 7;
        desat_hi_cnt <= 0; desat_lo_cnt <= 0; desat_blank_cnt <= 0;
        thermal_cnt <= 0; thermal_state <= 0;
        softoff_count <= 0; softoff_active <= 0;
        cfg_deadtime <= 3; cfg_slew <= 2; cfg_filter <= 2;
        fault_code <= 0; fault_latched <= 0;
        watchdog <= 0; edge_count <= 0; diag_count <= 0;
        gate_hi <= 0; gate_lo <= 0;
        miller_clamp_hi <= 1; miller_clamp_lo <= 1;
        prev_hi <= 0; prev_lo <= 0;
        drive_strength <= 2; status <= 0;
    end else begin
        // Two-sample command filter rejects a one-cycle CMTI-like command glitch.
        pwm_hi_hist <= {pwm_hi_hist[0], pwm_hi};
        pwm_lo_hist <= {pwm_lo_hist[0], pwm_lo};
        if (pwm_hi_hist == 2'b11) req_hi <= 1;
        else if (pwm_hi_hist == 2'b00) req_hi <= 0;
        if (pwm_lo_hist == 2'b11) req_lo <= 1;
        else if (pwm_lo_hist == 2'b00) req_lo <= 0;

        if (desat_blank_cnt != 0) desat_blank_cnt <= desat_blank_cnt - 1;

        if (desat_hi) begin
            if (desat_hi_cnt != 3) desat_hi_cnt <= desat_hi_cnt + 1;
        end else desat_hi_cnt <= 0;
        if (desat_lo) begin
            if (desat_lo_cnt != 3) desat_lo_cnt <= desat_lo_cnt + 1;
        end else desat_lo_cnt <= 0;
        if (ot_warn) begin
            if (thermal_cnt != 3) thermal_cnt <= thermal_cnt + 1;
        end else thermal_cnt <= 0;
        if (thermal_cnt >= 2) thermal_state <= 2;
        else if (!ot_warn) thermal_state <= 0;

        if (!uvlo_ok) begin fault_latched <= 1; fault_code <= 1; end
        else if (oc_fault) begin fault_latched <= 1; fault_code <= 2; end
        // DESAT is qualified only after the turn-on blanking window and after
        // consecutive samples. The blanking counter is deliberate state so its
        // corruption can be audited as a SiC short-circuit protection hazard.
        else if ((desat_blank_cnt == 0) && desat_hi && desat_hi_cnt >= (desat_filter - 1)) begin
            fault_latched <= 1; fault_code <= 3;
        end else if ((desat_blank_cnt == 0) && desat_lo && desat_lo_cnt >= (desat_filter - 1)) begin
            fault_latched <= 1; fault_code <= 4;
        end else if (thermal_cnt >= 3) begin fault_latched <= 1; fault_code <= 5; end

        if (!fault_latched && !gate_hi && !gate_lo && cfg_we) begin
            if (cfg_addr == 0) cfg_deadtime <= (cfg_wdata[2:0] < 2) ? 2 : cfg_wdata[2:0];
            if (cfg_addr == 1) cfg_slew <= cfg_wdata[1:0];
            if (cfg_addr == 2) cfg_filter <= (cfg_wdata[1:0] < 2) ? 2 : cfg_wdata[1:0];
        end
        if (gate_hi) hi_off_age <= 0;
        else if (hi_off_age != 7) hi_off_age <= hi_off_age + 1;
        if (gate_lo) lo_off_age <= 0;
        else if (lo_off_age != 7) lo_off_age <= lo_off_age + 1;

        if (fault_latched || !uvlo_ok) begin
            gate_hi <= 0; gate_lo <= 0;
            miller_clamp_hi <= 1; miller_clamp_lo <= 1;
            if (softoff_count == 0) softoff_count <= 3;
        end else if (req_hi && !req_lo) begin
            gate_lo <= 0; miller_clamp_lo <= 1;
            if (lo_off_age >= cfg_deadtime) begin
                if (!gate_hi) desat_blank_cnt <= DESAT_BLANK_CYCLES;
                gate_hi <= 1; miller_clamp_hi <= 0;
            end else begin
                gate_hi <= 0; miller_clamp_hi <= 1;
            end
        end else if (req_lo && !req_hi) begin
            gate_hi <= 0; miller_clamp_hi <= 1;
            if (hi_off_age >= cfg_deadtime) begin
                if (!gate_lo) desat_blank_cnt <= DESAT_BLANK_CYCLES;
                gate_lo <= 1; miller_clamp_lo <= 0;
            end else begin
                gate_lo <= 0; miller_clamp_lo <= 1;
            end
        end else begin
            gate_hi <= 0; gate_lo <= 0;
            miller_clamp_hi <= 1; miller_clamp_lo <= 1;
        end

        if (softoff_count != 0) begin
            softoff_count <= softoff_count - 1;
            softoff_active <= 1;
        end else softoff_active <= 0;
        if (thermal_state[1]) drive_strength <= 1;
        else drive_strength <= cfg_slew;
        if ((gate_hi != prev_hi) || (gate_lo != prev_lo)) edge_count <= edge_count + 1;
        prev_hi <= gate_hi; prev_lo <= gate_lo;
        watchdog <= watchdog + 1;
        if (fault_latched || ot_warn || !uvlo_ok) diag_count <= diag_count + 1;
        status <= {fault_latched, thermal_state[1], fault_code,
                   (desat_blank_cnt != 0), miller_clamp_hi, miller_clamp_lo};
    end
end
endmodule
