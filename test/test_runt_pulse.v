`default_nettype none
`timescale 1ns / 1ps

// Standalone, self-checking exhaustive glitch-free ratio-switching check
// for divider_core.v. Run directly with iverilog (no cocotb) -- see
// test/README.md for the command.
//
// For a documented set of (N_old, N_new) pairs, sweeps EVERY phase offset
// within the N_old period at which a ratio write can land, and asserts
// that the in-progress period always completes at N_old (full length,
// full duty pattern -- no runt pulses), the next period is exactly N_new,
// and n_active reflects the commit immediately after.
//
// Scope: exhaustive over phase (0..N_old-1) for the 13 (N_old, N_new)
// pairs below, covering {even, odd} x {even, odd} at {min=2, max=255,
// max-1, and three representative mid-range} N values, plus the
// degenerate N_old==N_new case. NOT literally all (N_old, N_new) pairs in
// 2..255 x 2..255 (~64k pairs x up to 255 phases each) -- that scale adds
// no further coverage of the underlying logic (an 8-bit down-counter plus
// one latched threshold) over this set, and would take far longer to run
// for no benefit; see docs/info.md.
module test_runt_pulse;

  reg clk = 1'b0;
  reg rst_n;
  reg enable_tb;
  reg restart_tb;
  reg commit_tb;
  reg [7:0] new_n_tb;

  wire [7:0] n_active;
  wire tick;
  wire out_q;

  integer errors;
  integer trial_count;

  divider_core dut (
      .clk     (clk),
      .rst_n   (rst_n),
      .enable  (enable_tb),
      .restart (restart_tb),
      .new_n   (new_n_tb),
      .commit  (commit_tb),
      .n_active(n_active),
      .tick    (tick),
      .out_q   (out_q)
  );

  always #5 clk = ~clk;

  function integer high_cycles;
    input integer n;
    begin
      high_cycles = (n + 1) >> 1;
    end
  endfunction

  // Runs exactly one period of length `n`, checking out_q against the
  // expected duty pattern for N every single cycle (catches a runt pulse
  // or any corrupted waveform) and checking tick fires on exactly the
  // last cycle and no other (catches a period that's the wrong length).
  // If write_phase >= 0, asserts commit/new_n starting at that cycle
  // (held through the rest of the period -- divider_core only consumes it
  // at the wrap, so holding it is harmless and mirrors the real
  // pending-register behaviour upstream of this module).
  task run_period;
    input integer n;
    input integer write_phase;
    input integer write_new_n;
    integer c;
    integer expected;
    begin
      for (c = 0; c < n; c = c + 1) begin
        expected = (c < high_cycles(n)) ? 1 : 0;
        if (out_q !== expected) begin
          errors = errors + 1;
          $display("ERROR(waveform): n=%0d write_phase=%0d cycle=%0d expected_out=%0d got=%0d",
                    n, write_phase, c, expected, out_q);
        end
        if (tick !== (c == n - 1)) begin
          errors = errors + 1;
          $display("ERROR(period-length): n=%0d write_phase=%0d cycle=%0d tick=%0d expected_tick=%0d",
                    n, write_phase, c, tick, (c == n - 1));
        end
        if (c == write_phase) begin
          commit_tb = 1'b1;
          new_n_tb  = write_new_n;
        end
        @(posedge clk);
        #1;
      end
    end
  endtask

  task run_trial;
    input integer n_old;
    input integer n_new;
    input integer phase;
    begin
      rst_n      = 1'b0;
      enable_tb  = 1'b1;
      restart_tb = 1'b0;
      commit_tb  = 1'b0;
      new_n_tb   = 8'd2;
      @(posedge clk); #1;
      @(posedge clk); #1;

      // Force-establish N_old deterministically regardless of reset state.
      rst_n      = 1'b1;
      restart_tb = 1'b1;
      commit_tb  = 1'b1;
      new_n_tb   = n_old;
      @(posedge clk); #1;
      restart_tb = 1'b0;
      commit_tb  = 1'b0;

      run_period(n_old, -1, 8'd0);     // period A: clean baseline, no write
      run_period(n_old, phase, n_new); // period B: transition -- write lands at `phase`
      commit_tb = 1'b0;

      if (n_active !== n_new) begin
        errors = errors + 1;
        $display("ERROR(commit): n_old=%0d n_new=%0d phase=%0d n_active=%0d expected=%0d",
                  n_old, n_new, phase, n_active, n_new);
      end

      run_period(n_new, -1, 8'd0); // period C: new ratio takes over
      run_period(n_new, -1, 8'd0); // period D: confirm it's stable, not a one-off

      trial_count = trial_count + 1;
    end
  endtask

  integer pair_idx;
  integer phase_idx;
  integer n_olds[0:12];
  integer n_news[0:12];
  integer num_pairs;

  initial begin
    errors      = 0;
    trial_count = 0;
    num_pairs   = 13;

    n_olds[0]  = 2;   n_news[0]  = 2;    // degenerate: commit to the same N
    n_olds[1]  = 2;   n_news[1]  = 3;    // min even -> min+1 odd
    n_olds[2]  = 3;   n_news[2]  = 2;    // min+1 odd -> min even
    n_olds[3]  = 2;   n_news[3]  = 255;  // min -> max
    n_olds[4]  = 255; n_news[4]  = 2;    // max -> min
    n_olds[5]  = 255; n_news[5]  = 254;  // max odd -> max-1 even
    n_olds[6]  = 254; n_news[6]  = 255;  // max-1 even -> max odd
    n_olds[7]  = 3;   n_news[7]  = 4;    // small odd -> small even
    n_olds[8]  = 4;   n_news[8]  = 3;    // small even -> small odd
    n_olds[9]  = 100; n_news[9]  = 101;  // mid even -> mid odd
    n_olds[10] = 101; n_news[10] = 100;  // mid odd -> mid even
    n_olds[11] = 127; n_news[11] = 128;  // mid odd -> mid even
    n_olds[12] = 128; n_news[12] = 127;  // mid even -> mid odd

    for (pair_idx = 0; pair_idx < num_pairs; pair_idx = pair_idx + 1) begin
      for (phase_idx = 0; phase_idx < n_olds[pair_idx]; phase_idx = phase_idx + 1) begin
        run_trial(n_olds[pair_idx], n_news[pair_idx], phase_idx);
      end
    end

    if (errors == 0) begin
      $display("RUNT-PULSE CHECK: PASSED (%0d trials, %0d pairs, all phases, 0 errors)",
                trial_count, num_pairs);
    end else begin
      $display("RUNT-PULSE CHECK: FAILED (%0d errors across %0d trials)", errors, trial_count);
      $fatal(1);
    end
    $finish;
  end

endmodule
