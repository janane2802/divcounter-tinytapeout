/*
 * Copyright (c) 2026 Srija Nane
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// SPI mode-0 slave shift register. The frame {n_used[7:0], count[15:0]} is
// snapshotted into the shift register the instant cs_n falls, so a result
// update or a window completing mid-read can never corrupt an in-flight
// frame -- the master always reads one consistent, atomic snapshot. Shifts
// out MSB-first on each sck falling edge, so MISO is settled well before
// the master's next rising-edge sample (SPI mode 0).
module spi_slave (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        cs_n_fall,
    input  wire        sck_fall,
    input  wire        cs_n_level,
    input  wire [7:0]  n_used,
    input  wire [15:0] count,
    output wire        miso
);

  reg [23:0] shift_reg;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) shift_reg <= 24'd0;
    else if (cs_n_fall) shift_reg <= {n_used, count};
    else if (sck_fall) shift_reg <= {shift_reg[22:0], 1'b0};
  end

  assign miso = cs_n_level ? 1'b0 : shift_reg[23];

endmodule
