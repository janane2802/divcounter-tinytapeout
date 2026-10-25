/*
 * Copyright (c) 2026 Srija Nane
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// Divide-by-N core: an 8-bit down-counter driving a REGISTERED duty-cycle
// output. The output flop's next value is always computed from registered
// state (the previous cnt value and a per-period-latched threshold), never
// from a live combinational compare against a possibly-changing N -- so a
// ratio change can only take effect through the `commit` strobe below,
// which this module only consumes at a wrap or a restart, i.e. at a period
// boundary. The current period in progress always finishes at the old N.
//
// Odd N gives (N+1)/2 cycles high, (N-1)/2 cycles low -- intentional, see
// docs/info.md.
module divider_core (
    input  wire       clk,
    input  wire       rst_n,
    input  wire       enable,    // advance the counter this cycle
    input  wire       restart,   // force an immediate fresh-period reload (mode 1 window start)
    input  wire [7:0] new_n,     // candidate N, already clamped to 2..255
    input  wire       commit,    // adopt new_n as active -- only consumed while wrapping/restarting
    output wire [7:0] n_active,
    output wire       tick,      // combinational: this cycle completes a period
    output wire       out_q      // registered duty-cycle output
);

  reg [7:0] cnt;
  reg [7:0] low_cycles;
  reg [7:0] n_active_r;
  reg       out_q_r;

  assign n_active = n_active_r;
  assign out_q    = out_q_r;
  assign tick     = enable && (cnt == 8'd0);

  function [7:0] low_half;
    input [7:0] n;
    reg [8:0] n_plus_1;  // 9 bits: n+1 overflows 8 bits when n=255
    begin
      n_plus_1 = {1'b0, n} + 9'd1;
      low_half = n - n_plus_1[8:1];
    end
  endfunction

  wire [7:0] n_next = commit ? new_n : n_active_r;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      n_active_r <= 8'd2;
      cnt        <= 8'd1;
      low_cycles <= 8'd1;
      out_q_r    <= 1'b1;
    end else if (restart) begin
      n_active_r <= n_next;
      cnt        <= n_next - 8'd1;
      low_cycles <= low_half(n_next);
      out_q_r    <= 1'b1;
    end else if (enable) begin
      if (cnt == 8'd0) begin
        n_active_r <= n_next;
        cnt        <= n_next - 8'd1;
        low_cycles <= low_half(n_next);
        out_q_r    <= 1'b1;
      end else begin
        cnt <= cnt - 8'd1;
        if ((cnt - 8'd1) < low_cycles) out_q_r <= 1'b0;
      end
    end
  end

endmodule
