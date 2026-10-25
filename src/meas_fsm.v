/*
 * Copyright (c) 2026 Srija Nane
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// Gated measurement FSM: IDLE -> COUNT -> LATCH -> DONE.
// gate_sel/continuous are latched into *_active at window_start so a
// mid-window change of either can't alter the window already running.
// The gate timer runs unconditionally every clk cycle for the duration of
// the window (it is restarted, not free-running across windows, to give an
// exact window length regardless of when it was armed).
module meas_fsm (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        arm_pulse,
    input  wire        clear_pulse,
    input  wire [2:0]  gate_sel,
    input  wire        continuous,
    input  wire        div_tick,
    input  wire [7:0]  n_active,
    output wire        window_start,  // 1-cycle pulse: entering COUNT this cycle
    output wire        counting,      // state == COUNT
    output wire        busy,
    output wire        done,
    output wire        gate,
    output wire        overflow,
    output wire [7:0]  result_n,
    output wire [15:0] result_count
);

  localparam S_IDLE  = 2'b00;
  localparam S_COUNT = 2'b01;
  localparam S_LATCH = 2'b10;
  localparam S_DONE  = 2'b11;

  reg [1:0]  state;
  reg [1:0]  next_state;
  reg [2:0]  gate_sel_active;
  reg        continuous_active;
  reg [23:0] gate_count;
  reg [23:0] window_len_m1;
  reg [15:0] meas_count;
  reg        overflow_r;
  reg [15:0] result_count_r;
  reg [7:0]  result_n_r;

  assign window_start = (next_state == S_COUNT) && (state != S_COUNT);
  assign counting     = (state == S_COUNT);
  assign busy         = (state == S_COUNT) || (state == S_LATCH);
  assign done         = (state == S_DONE);
  assign gate         = (state == S_COUNT);
  assign overflow     = overflow_r;
  assign result_n     = result_n_r;
  assign result_count = result_count_r;

  wire window_done = counting && (gate_count == window_len_m1);

  // ---- next-state logic ----
  always @* begin
    next_state = state;
    case (state)
      S_IDLE:  if (arm_pulse) next_state = S_COUNT;
      S_COUNT: if (window_done) next_state = S_LATCH;
      S_LATCH: next_state = S_DONE;
      S_DONE: begin
        if (continuous_active) next_state = S_COUNT;
        else if (arm_pulse) next_state = S_COUNT;
      end
      default: next_state = S_IDLE;
    endcase
    if (clear_pulse) next_state = S_IDLE;
  end

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) state <= S_IDLE;
    else        state <= next_state;
  end

  // ---- per-window config latch ----
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      gate_sel_active   <= 3'd0;
      continuous_active <= 1'b0;
    end else if (window_start) begin
      gate_sel_active   <= gate_sel;
      continuous_active <= continuous;
    end
  end

  // ---- window length lookup: 2^(10+2*gate_sel) - 1 ----
  always @* begin
    case (gate_sel_active)
      3'd0: window_len_m1 = 24'd1023;
      3'd1: window_len_m1 = 24'd4095;
      3'd2: window_len_m1 = 24'd16383;
      3'd3: window_len_m1 = 24'd65535;
      3'd4: window_len_m1 = 24'd262143;
      3'd5: window_len_m1 = 24'd1048575;
      3'd6: window_len_m1 = 24'd4194303;
      3'd7: window_len_m1 = 24'd16777215;
      default: window_len_m1 = 24'd1023;
    endcase
  end

  // ---- gate timer ----
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) gate_count <= 24'd0;
    else if (window_start) gate_count <= 24'd0;
    else if (counting) gate_count <= gate_count + 24'd1;
  end

  // ---- measurement counter: counts div_tick during COUNT, saturates, sticky overflow ----
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      meas_count <= 16'd0;
      overflow_r <= 1'b0;
    end else if (clear_pulse) begin
      meas_count <= 16'd0;
      overflow_r <= 1'b0;
    end else if (window_start) begin
      meas_count <= 16'd0;
    end else if (counting && div_tick) begin
      if (meas_count == 16'hFFFF) overflow_r <= 1'b1;
      else meas_count <= meas_count + 16'd1;
    end
  end

  // ---- latch result at LATCH ----
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      result_count_r <= 16'd0;
      result_n_r     <= 8'd2;
    end else if (state == S_LATCH) begin
      result_count_r <= meas_count;
      result_n_r     <= n_active;
    end
  end

endmodule
