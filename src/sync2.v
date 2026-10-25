/*
 * Copyright (c) 2026 Srija Nane
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// 2-FF synchronizer for an asynchronous input pin, plus edge detection on
// the synchronized level. Used for every external control pin (wr, sig_in,
// sck, cs_n): their phase relationship to clk is unknown, and metastability
// on them can't be exercised in RTL/gate-level simulation -- this is a
// mitigation, not something the testbench can verify directly.
module sync2 (
    input  wire clk,
    input  wire rst_n,
    input  wire d,
    output wire sync_level,
    output wire rise,
    output wire fall
);

  reg [1:0] ff;
  reg       prev;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      ff   <= 2'b00;
      prev <= 1'b0;
    end else begin
      ff   <= {ff[0], d};
      prev <= ff[1];
    end
  end

  assign sync_level = ff[1];
  assign rise       = ff[1] & ~prev;
  assign fall        = ~ff[1] & prev;

endmodule
