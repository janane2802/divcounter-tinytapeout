/*
 * Copyright (c) 2026 Srija Nane
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// Glitch-free programmable divider (N = 2..255) plus a gated frequency
// counter with SPI serial readout. See docs/info.md for the register map,
// SPI frame format, and the glitch-free commit protocol.
//
// Pinout:
//   ui_in[7:0]   = data bus
//   uio_in[0]    = wr        (write strobe, 2-FF synchronized)
//   uio_in[1]    = reg_sel   (0 = ratio/N register, 1 = control register)
//   uio_in[2]    = sig_in    (external signal to measure, mode 1)
//   uio_in[3]    = sck       (SPI clock, mode 0)
//   uio_in[4]    = cs_n      (SPI chip select, active low)
//   uio_in[7:5]  = spare, unused
//   uo_out[0]    = clk_out
//   uo_out[1]    = miso
//   uo_out[2]    = busy
//   uo_out[3]    = done
//   uo_out[4]    = overflow
//   uo_out[5]    = ratio_pending
//   uo_out[6]    = ratio_applied
//   uo_out[7]    = gate (measurement window active)
//
// Control register (reg_sel = 1): [0] mode, [3:1] gate_sel, [4] continuous,
// [5] arm (self-clearing), [6] clear (self-clearing).
module tt_um_janane2802_divcounter (
    input  wire [7:0] ui_in,    // Dedicated inputs
    output wire [7:0] uo_out,   // Dedicated outputs
    input  wire [7:0] uio_in,   // IOs: Input path
    output wire [7:0] uio_out,  // IOs: Output path
    output wire [7:0] uio_oe,   // IOs: Enable path (active high: 0=input, 1=output)
    input  wire       ena,      // always 1 when the design is powered, so you can ignore it
    input  wire       clk,      // clock
    input  wire       rst_n     // reset_n - low to reset
);

  wire [7:0] data_bus = ui_in;
  wire       reg_sel  = uio_in[1];

  // ---- 2-FF synchronizers + edge detection on every external pin ----
  wire wr_pulse, sig_in_rise, sck_fall, cs_n_fall, cs_n_level;

  sync2 wr_sync (
      .clk(clk), .rst_n(rst_n), .d(uio_in[0]),
      .sync_level(), .rise(wr_pulse), .fall()
  );

  sync2 sig_sync (
      .clk(clk), .rst_n(rst_n), .d(uio_in[2]),
      .sync_level(), .rise(sig_in_rise), .fall()
  );

  sync2 sck_sync (
      .clk(clk), .rst_n(rst_n), .d(uio_in[3]),
      .sync_level(), .rise(), .fall(sck_fall)
  );

  sync2 cs_sync (
      .clk(clk), .rst_n(rst_n), .d(uio_in[4]),
      .sync_level(cs_n_level), .rise(), .fall(cs_n_fall)
  );

  // ---- write decode: shadow registers + glitch-free commit ----
  wire [7:0] n_clamped   = (data_bus < 8'd2) ? 8'd2 : data_bus;
  wire       arm_pulse   = wr_pulse && reg_sel && data_bus[5];
  wire       clear_pulse = wr_pulse && reg_sel && data_bus[6];

  reg [7:0] shadow_n;
  reg       mode_shadow;
  reg       pending;
  reg [2:0] ctrl_gate_sel;
  reg       ctrl_continuous;
  reg       active_mode;
  reg       ratio_applied;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      shadow_n    <= 8'd2;
      mode_shadow <= 1'b0;
    end else if (wr_pulse) begin
      if (!reg_sel) shadow_n <= n_clamped;
      else mode_shadow <= data_bus[0];
    end
  end

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      ctrl_gate_sel   <= 3'd0;
      ctrl_continuous <= 1'b0;
    end else if (wr_pulse && reg_sel) begin
      ctrl_gate_sel   <= data_bus[3:1];
      ctrl_continuous <= data_bus[4];
    end
  end

  // A ratio/mode write is only allowed to take effect at a period boundary
  // (divider wrap) or, in mode 1, at the start of the next measurement
  // window (window_start) -- and never while mode 1 is mid-window (COUNT),
  // so a window's N_used is always fixed for its whole duration.
  wire fsm_blocking = active_mode && counting;
  wire commit       = pending && !fsm_blocking && (div_tick || div_restart);

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) pending <= 1'b0;
    else if (wr_pulse) pending <= 1'b1;
    else if (commit) pending <= 1'b0;
  end

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      active_mode   <= 1'b0;
      ratio_applied <= 1'b0;
    end else if (commit) begin
      active_mode   <= mode_shadow;
      ratio_applied <= ~ratio_applied;
    end
  end

  // ---- divider core ----
  wire       div_enable  = active_mode ? sig_in_rise : 1'b1;
  wire       div_restart = active_mode && window_start;
  wire [7:0] n_active;
  wire       div_tick;
  wire       clk_out;

  divider_core div_inst (
      .clk     (clk),
      .rst_n   (rst_n),
      .enable  (div_enable),
      .restart (div_restart),
      .new_n   (shadow_n),
      .commit  (commit),
      .n_active(n_active),
      .tick    (div_tick),
      .out_q   (clk_out)
  );

  // ---- measurement FSM (gate timer + counter + result latch) ----
  wire        window_start;
  wire        counting;
  wire        busy;
  wire        done;
  wire        gate;
  wire        overflow;
  wire [7:0]  result_n;
  wire [15:0] result_count;

  meas_fsm fsm_inst (
      .clk         (clk),
      .rst_n       (rst_n),
      .arm_pulse   (arm_pulse),
      .clear_pulse (clear_pulse),
      .gate_sel    (ctrl_gate_sel),
      .continuous  (ctrl_continuous),
      .div_tick    (div_tick),
      .n_active    (n_active),
      .window_start(window_start),
      .counting    (counting),
      .busy        (busy),
      .done        (done),
      .gate        (gate),
      .overflow    (overflow),
      .result_n    (result_n),
      .result_count(result_count)
  );

  // ---- SPI readout ----
  wire miso;

  spi_slave spi_inst (
      .clk       (clk),
      .rst_n     (rst_n),
      .cs_n_fall (cs_n_fall),
      .sck_fall  (sck_fall),
      .cs_n_level(cs_n_level),
      .n_used    (result_n),
      .count     (result_count),
      .miso      (miso)
  );

  // ---- output pins ----
  assign uo_out[0] = clk_out;
  assign uo_out[1] = miso;
  assign uo_out[2] = busy;
  assign uo_out[3] = done;
  assign uo_out[4] = overflow;
  assign uo_out[5] = pending;
  assign uo_out[6] = ratio_applied;
  assign uo_out[7] = gate;

  assign uio_out = 8'b0;
  assign uio_oe  = 8'b0;

  // List all unused inputs to prevent warnings
  wire _unused = &{ena, uio_in[7:5], 1'b0};

endmodule
