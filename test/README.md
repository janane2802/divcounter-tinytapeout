# Testbench for tt_um_janane2802_divcounter

This project has three separate verification layers:

1. **cocotb directed tests + golden-model sweep** (`test.py`, `test_golden.py`)
   -- run via `make` (Linux/CI) or `run_tests.py` (Windows-friendly, no GNU
   make needed).
2. **Standalone exhaustive runt-pulse checker** (`test_runt_pulse.v`) -- a
   plain self-checking Verilog bench, run directly with `iverilog`/`vvp`,
   no cocotb. This is the headline glitch-free-switching proof: see its
   header comment for exact scope.
3. **Gate-level** versions of (1), against the hardened netlist.

## cocotb tests

### Linux / CI (GNU make)

RTL simulation:

```sh
make -B
```

Gate-level simulation: first harden the project and copy
`../runs/<tag>/final/pnl/tt_um_janane2802_divcounter.pnl.v` to
`gate_level_netlist.v`, and the PDK's `sky130_fd_sc_hd.v` / `primitives.v`
verilog models into `pdk_models/`, then:

```sh
make -B GATES=yes
```

### Windows (no GNU make)

```sh
python run_tests.py       # RTL
python run_tests_gl.py    # gate-level (same file prerequisites as above)
```

Both run `test.py` and `test_golden.py` and write `results.xml` /
`results_gl.xml`.

If you wish to save the waveform in VCD format instead of FST format, edit
`tb.v` to use `$dumpfile("tb.vcd");` and then run `make -B FST=` (or set
`FST=` when invoking make). This generates `tb.vcd` instead of `tb.fst`.

## Runt-pulse checker (standalone, no cocotb)

```sh
iverilog -g2005 -o sim_build/runt_pulse.vvp test_runt_pulse.v ../src/divider_core.v
vvp sim_build/runt_pulse.vvp
```

Prints `RUNT-PULSE CHECK: PASSED (...)` or lists every failing cycle and
exits non-zero. To run it against the gate-level netlist instead of
`divider_core.v` directly, note that `divider_core` is synthesized away
into the flat gate-level netlist -- this checker only applies pre-synthesis;
gate-level glitch-free behavior is instead covered by the cocotb directed
tests in gate-level mode (`run_tests_gl.py`).

## How to view the waveform file

Using GTKWave:

```sh
gtkwave tb.fst tb.gtkw
```

Using Surfer:

```sh
surfer tb.fst
```
