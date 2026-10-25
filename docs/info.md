<!---

This file is used to generate your project datasheet. Please fill in the information below and delete any unused
sections.

You can also include images in this folder and reference them in the markdown. Each image must be less than
512 kb in size, and the combined size of all images must be less than 1 MB.
-->

## How it works

A glitch-free programmable clock divider (N = 2..255) that doubles as a
gated frequency counter with SPI serial readout. Single clock domain
throughout -- there are no generated/derived clocks and no ripple dividers;
every output is driven from a registered flip-flop, never a live
combinational compare.

### Divider core (`divider_core.v`)

An 8-bit down-counter (`cnt`, counting N-1 down to 0) plus a *registered*
duty-cycle output flop. Each period, the output goes high for
`ceil(N/2) = (N+1)/2` cycles and low for `floor(N/2) = (N-1)/2` cycles. For
even N that's an exact 50/50 duty; for odd N it's intentionally
`(N+1)/2 : (N-1)/2` (e.g. N=5 gives 3 high, 2 low) -- a perfectly even
split isn't possible for an odd divisor with an integer-cycle output, so
the core always rounds the high phase up.

Two independent uses of the same counter, selected by `mode`:

- **Mode 0 (divider):** `enable` is always 1 (every clk cycle advances the
  counter) and the registered output drives `clk_out` directly -- this is
  a plain programmable clock divider.
- **Mode 1 (prescaler):** `enable` is a single-cycle pulse on the
  synchronized rising edge of `sig_in` (see Synchronization below), so the
  counter advances once per external edge instead of once per clk cycle.
  Every time it wraps (every N edges), it emits a one-cycle `tick` that
  feeds the frequency counter below. `clk_out` is still driven from the
  same registered duty-cycle flop in this mode, but since it only updates
  on sig_in edges rather than every clk cycle, it isn't a meaningful
  "clock" here and should be ignored -- only `tick`/the measured count
  matters in mode 1.

### Glitch-free ratio/mode switching

A write to the N register (`reg_sel=0`) or the control register's `mode`
bit (`reg_sel=1`) never touches the active divider state directly. It only
updates a shadow register and sets a pending flag (shown on `uo_out[5]`,
`ratio_pending`). The shadow commits -- i.e. the active N/mode actually
changes -- **only** at the next period boundary (the counter's wrap/reload
point), so the period already in progress always finishes at the *old*
ratio, full length, full duty pattern. Worst case latency from write to
commit is one full old-ratio period. `uo_out[6]` (`ratio_applied`) toggles
every time a commit actually happens, so the host can poll for it instead
of guessing the timing.

In mode 1, there's one more rule: a commit is also blocked for the entire
duration of a measurement window (the FSM's COUNT state -- see below), so
every reading's `N_used` is guaranteed correct for the *whole* window it
was measured over, never a mix of old and new N. A pending write just
waits until the window ends; the very start of the *next* window (if one
is armed) is also a valid commit point, which is when the divider's phase
is reset for that new window anyway (see below), so a write made while
idle between windows takes effect right as the next window begins.

Mode changes go through the exact same shadow/commit mechanism as N
changes (both are gated by the same `ratio_pending`/`ratio_applied` pair),
so switching from divider mode to prescaler mode mid-run is also
glitch-free on whatever the divider was doing at the time.

This is proven exhaustively (not just spot-checked) by
`test/test_runt_pulse.v` -- see that file's header and the Verification
section below.

### Synchronization

Every external control pin (`wr`, `sig_in`, `sck`, `cs_n`) goes through an
identical 2-flip-flop synchronizer with an extra register for edge
detection (`sync2.v`), since none of them have a known phase relationship
to `clk`. The data bus and `reg_sel` are sampled directly (combinationally)
at the moment the synchronized `wr` pulse fires -- only `wr` itself is
synchronized, not the bus -- which is why the host must hold the data bus
and `reg_sel` stable for at least 3 clk cycles centered on toggling `wr`.
If two writes land before either commits, the second one simply overwrites
the shadow register first (shadow registers always hold the latest
write) -- last write wins.

In mode 1, the prescaler's phase is also force-reset (a fresh N-edge count
starts) the instant a measurement window begins (IDLE/DONE -> COUNT), so
every window starts from a clean, known phase rather than wherever the
free-running prescaler happened to be.

### Gate timer and measurement FSM (`meas_fsm.v`)

A simple 4-state FSM: `IDLE -> COUNT -> LATCH -> DONE`.

- **IDLE:** waiting for `arm`.
- **COUNT:** a gate timer runs every clk cycle, unconditionally, for
  exactly `2^(10+2*gate_sel)` cycles (`gate_sel` = control register bits
  `[3:1]`, so window lengths are 1024, 4096, 16384, 65536, 262144,
  1048576, 4194304, or 16777216 clk cycles). While counting, a separate
  16-bit counter counts the divider core's `tick` pulses -- this is the
  actual measurement result. `gate_sel` and `continuous` are latched for
  the whole window the instant it starts, so changing either mid-window
  has no effect on the window already running.
- **LATCH:** one cycle; the measurement and the N that was active for the
  whole window are copied into the result registers that SPI reads from.
- **DONE:** `done=1`. In single-shot mode (`continuous=0`), it stays here
  until a new `arm`. In continuous mode, it loops straight back to COUNT
  for another window with no new `arm` needed.

The 16-bit measurement counter saturates at `0xFFFF` on overflow rather
than wrapping, and the overflow flag is sticky -- once set, it stays set
across subsequent measurements until an explicit `clear`, not just until
the next non-overflowing one.

`clear` (control register bit `[6]`) forces the FSM back to IDLE and zeros
the measurement counter and overflow flag immediately, regardless of what
state it was in -- including mid-window.

### Serial readout (`spi_slave.v`)

A 24-bit frame, `{N_used[7:0], count[15:0]}`, MSB first, SPI mode 0 (CPOL=0,
CPHA=0). The frame is snapshotted into the shift register the instant
`cs_n` falls, so a measurement finishing (and overwriting the result
registers) in the middle of a read can never corrupt the frame already
being shifted out -- the master always gets one complete, consistent
snapshot. Bits shift out on each `sck` falling edge, so MISO is settled
well before the master's next rising-edge sample. Releasing `cs_n` mid-frame
simply abandons that partial transfer; the next `cs_n` fall starts a fresh
one from bit 0.

## Register map

### N register (`reg_sel = 0`)

| Bits | Field | Notes |
|---|---|---|
| `[7:0]` | N | Written value is clamped to 2 if 0 or 1 is written. Range 2..255. |

### Control register (`reg_sel = 1`)

| Bit | Field | Notes |
|---|---|---|
| `[0]` | `mode` | 0 = divider, 1 = prescaler. Glitch-free shadow/commit, same as N. |
| `[3:1]` | `gate_sel` | Window = 2^(10+2*gate_sel) clk cycles, 0..7. Takes effect immediately but is only *consumed* at the start of the next window. |
| `[4]` | `continuous` | 0 = single-shot, 1 = auto-restart after DONE. Same latch timing as `gate_sel`. |
| `[5]` | `arm` | Self-clearing: starts a measurement (IDLE or DONE -> COUNT) on this write only. |
| `[6]` | `clear` | Self-clearing: immediately aborts to IDLE, zeros count/overflow, this write only. |
| `[7]` | -- | unused |

Writing the control register always writes the *whole* byte -- there's no
partial update, so a write meant only to `arm` must restate the current
`mode`/`gate_sel`/`continuous` too, or it will (harmlessly, since the
value doesn't actually change) also request a ratio commit with whatever
was left in those bits.

### Write protocol

1. Drive the data bus (`ui_in`) and `reg_sel` (`uio_in[1]`) to the desired
   values.
2. Raise `wr` (`uio_in[0]`) and hold everything stable for at least 3 clk
   cycles (2-FF synchronizer + edge detector + margin).
3. Drop `wr`, hold stable at least 1-2 more clk cycles before changing
   anything, to let the write cleanly resolve.

## SPI frame format and worked example

24 bits, MSB first, SPI mode 0: `{N_used[7:0], count[15:0]}`.

To recover the measured input frequency from a frame: the gate window is
`window = 2^(10+2*gate_sel)` clk cycles, so it lasted
`window / f_clk` seconds, during which `count` prescaler ticks were seen,
each tick representing `N_used` input edges. So:

```
f_in = count * N_used * f_clk / window
```

**Worked example:** `f_clk = 50 MHz`, `gate_sel = 2` (window = 2^14 =
16384 cycles = 327.68 us), `N_used = 10`, and the frame reads `count =
1525`. Then:

```
f_in = 1525 * 10 * 50_000_000 / 16384 = 46,539,306 Hz  (~46.5 MHz)
```

(Sanity check from the other direction: in 327.68 us, a 46.5 MHz input
produces about 15,250 edges; divided by N=10, that's ~1525 prescaler
ticks -- matches `count`.)

## How to test

1. Hold `rst_n` low for a few clock cycles, release it. `uio_oe` is always
   `8'h00` -- every `uio` pin is an input; drive `cs_n` high (deasserted)
   whenever you're not actively reading.
2. **Divider mode:** write N via the N register (`reg_sel=0`), leave
   `mode=0` (the reset default). `clk_out` (`uo_out[0]`) starts dividing
   by N once the write commits (within one old period, or immediately if
   this is the first write after reset).
3. **Frequency counter:** write `mode=1` and the desired `N`/`gate_sel`,
   drive the signal to measure onto `sig_in` (`uio_in[2]`), then write the
   control register again with `arm=1` (keeping `mode`/`gate_sel`/
   `continuous` the same). Poll `busy`/`gate` (`uo_out[2]`/`[7]`) or wait
   for `done` (`uo_out[3]`), then read the 24-bit frame over SPI
   (`sck`=`uio_in[3]`, `cs_n`=`uio_in[4]`, `miso`=`uo_out[1]`) and compute
   `f_in` as above. Check `overflow` (`uo_out[4]`) -- if set, `count` is
   saturated at `0xFFFF` and the true count was higher; `clear` (control
   bit `[6]`) resets it.
4. For continuous measurement, write `continuous=1` once; after the first
   `arm`, each window auto-restarts with no further writes needed -- just
   keep reading fresh frames over SPI.

The full behavior above is exercised by the cocotb test suite
(`test/test.py`, `test/test_golden.py`) -- run with `python
test/run_tests.py` for RTL simulation, or `python test/run_tests_gl.py`
for gate-level simulation against the hardened netlist (see
`test/README.md`). The exhaustive glitch-free-switching proof is
`test/test_runt_pulse.v`, run directly with iverilog (not cocotb) -- see
its header comment and `test/README.md` for the exact command and scope.

## External hardware

None. All inputs and outputs are driven/read directly from the TT demo
board's digital I/O (or, in the lab, a function generator into `sig_in`
and a logic analyzer/SPI master on `sck`/`cs_n`/`miso`/`clk_out`).

## Known limitations

- **`sig_in` must stay below clk/2.** The prescaler's 2-FF synchronizer +
  edge detector needs the input held stable for a few clk cycles each
  phase to register reliably; above roughly clk/2 edges can be
  missed or merged. There's comfortable margin below that in practice.
- **`sck` must stay at or below clk/8.** Same reasoning applies to the SPI
  clock's synchronizer. The cocotb suite tests both clk/8 (the documented
  limit) and clk/4 (faster, outside the documented margin) -- clk/4 reads
  back correctly in RTL simulation, which has zero metastability by
  construction, but that result doesn't carry over to real silicon; don't
  rely on faster-than-clk/8 SPI on the actual chip.
- **The prescaler costs resolution.** In mode 1, the counter only
  advances once per N input edges, so the measured count is quantized in
  steps of N edges (±N edges of uncertainty per window) rather than
  resolving individual edges. Larger N trades frequency-measurement
  resolution for a coarser, lower-overhead count.
- **Metastability on `sig_in`, `sck`, `cs_n`, and `wr` cannot be exercised
  in RTL or gate-level simulation.** The 2-FF synchronizers on all four
  pins are the mitigation; simulation can only confirm the synchronizer's
  *logical* behavior (correct edge detection, correct latency), not its
  metastability-resolution behavior, which is a physical property of the
  real flip-flops that doesn't exist in a digital simulator.
- **Gate-level simulation here is functional, unit-delay only** (`-DSIM
  -DUNIT_DELAY=#1`, no SDF/SPEF back-annotation). It confirms logical
  correctness against the real standard-cell netlist, not real timing
  margins under process/voltage/temperature -- that's LibreLane's own
  signoff STA's job (see the hardening results for current signoff
  status and any known timing limitations).
- **The exhaustive runt-pulse checker covers a documented set of
  (N_old, N_new) pairs, not literally all 254x254 combinations** -- 13
  pairs chosen to cover {even, odd} x {even, odd} at the N=2 minimum, the
  N=255 maximum, N=254 (max-1), and several representative mid-range
  values, each swept over *every* phase offset in the old period (1236
  trials total). This exercises every structurally distinct case the
  8-bit-down-counter-plus-one-latched-threshold design can produce; a
  full 64k-pair sweep would not add further coverage of the underlying
  logic, only runtime. See `test/test_runt_pulse.v`'s header for the exact
  list.
- **The cycle-accurate golden-model sweep** (`test/test_golden.py`) is
  exhaustive over N=2..40 at `gate_sel=0` (1024-cycle windows) and
  spot-checked (3 N values x 2 random phases) at `gate_sel=1..4`.
  `gate_sel=5..7` (windows up to 2^24 = ~16.8M clk cycles) are not
  simulated end-to-end there -- at iverilog/cocotb speeds that's minutes
  per case for no further logic coverage, since the gate-timer and
  window-length-lookup hardware path is already exhaustively exercised at
  the smaller `gate_sel` values. Those three lookup constants (2^20-1,
  2^22-1, 2^24-1) were instead verified by hand against the formula.
