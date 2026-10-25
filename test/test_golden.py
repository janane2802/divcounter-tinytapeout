# SPDX-FileCopyrightText: © 2026 Srija Nane
# SPDX-License-Identifier: Apache-2.0

"""Cycle-accurate golden-model sweep for tt_um_janane2802_divcounter.

GoldenModel below is a line-by-line Python replica of the whole chip's
write/commit/divider/measurement path (two independent 2-FF-sync+edge-detect
blocks for wr and sig_in, the shadow-register commit logic, divider_core,
and meas_fsm), using the same old-state-in/new-state-out update order as
real flip-flops (every step() computes all next values from the current
state and this cycle's raw pin inputs, then commits them together). The
test drives the real DUT and the model with IDENTICAL per-cycle raw pin
values and compares the SPI-readable result exactly -- not +/-1 -- so the
2-FF synchronizer latency on both wr and sig_in has to match exactly too.

Runtime scope: gate_sel=0 (a 1024-clk-cycle window) is swept over N=2..40
(covering even/odd/boundary N) x 5 random sig_in phases each -- effectively
exhaustive over the interesting N range. gate_sel=1..4 (4096..262144-cycle
windows) are spot-checked at 3 N values x 2 phases each. gate_sel=5..7
(windows of 2^20, 2^22, 2^24 = up to ~16.8M clk cycles) are NOT simulated
end-to-end here -- at cocotb/icarus speeds that's tens of minutes per case
for no further logic coverage (the gate timer and window-length lookup are
the same hardware path already exercised at gate_sel=0..4; the only thing
that changes for g=5..7 is which constant comes out of that same lookup).
Those three lookup constants were verified by hand against the formula
2^(10+2g)-1 instead: g=5 -> 1048575, g=6 -> 4194303, g=7 -> 16777215.
"""

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles

WR, REG_SEL, SIG_IN, SCK, CS_N = 0, 1, 2, 3, 4
UO_MISO, UO_DONE = 1, 3


def ctrl_byte(mode=0, gate_sel=0, continuous=0, arm=0, clear=0):
    return (
        (mode & 1)
        | ((gate_sel & 0x7) << 1)
        | ((continuous & 1) << 4)
        | ((arm & 1) << 5)
        | ((clear & 1) << 6)
    )


class Sync2:
    """Python replica of sync2.v: 2-FF synchronizer + rise/fall detect."""

    def __init__(self):
        self.ff0 = 0
        self.ff1 = 0
        self.prev = 0

    @property
    def rise(self):
        return bool(self.ff1) and not self.prev

    def step(self, d):
        new_ff1 = self.ff0
        new_ff0 = 1 if d else 0
        new_prev = self.ff1
        self.ff0, self.ff1, self.prev = new_ff0, new_ff1, new_prev


class GoldenModel:
    """Cycle-accurate replica of the top-level write/commit path,
    divider_core, and meas_fsm -- including the mode_shadow/active_mode
    commit, since the chip genuinely starts in mode 0 at reset and only
    switches to mode 1 once the first write's commit lands (which this
    sweep's setup relies on: it writes mode=1 then feeds idle/priming
    cycles and waits for that commit, exactly like real host code would)."""

    IDLE, COUNT, LATCH, DONE = range(4)

    def __init__(self):
        self.wr_sync = Sync2()
        self.sig_sync = Sync2()

        self.shadow_n = 2
        self.mode_shadow = 0
        self.pending = False

        self.ctrl_gate_sel = 0

        self.active_mode = 0

        self.n_active = 2
        self.cnt = 1
        self.low_cycles = 1

        self.state = self.IDLE
        self.gate_sel_active = 0
        self.gate_count = 0
        self.meas_count = 0
        self.overflow = False
        self.result_n = 2
        self.result_count = 0

    @staticmethod
    def _low_half(n):
        return n - ((n + 1) >> 1)

    @staticmethod
    def _window_len_m1(gate_sel):
        return (1 << (10 + 2 * gate_sel)) - 1

    def step(self, wr_raw, reg_sel_raw, data_raw, sig_in_raw):
        wr_pulse = self.wr_sync.rise
        reg_sel = bool(reg_sel_raw)
        data = data_raw

        n_clamped = 2 if data < 2 else data
        arm_pulse = wr_pulse and reg_sel and bool(data & 0x20)
        clear_pulse = wr_pulse and reg_sel and bool(data & 0x40)

        counting = self.state == self.COUNT
        div_enable = self.sig_sync.rise if self.active_mode else 1
        window_done = counting and (self.gate_count == self._window_len_m1(self.gate_sel_active))

        if self.state == self.IDLE:
            next_state = self.COUNT if arm_pulse else self.IDLE
        elif self.state == self.COUNT:
            next_state = self.LATCH if window_done else self.COUNT
        elif self.state == self.LATCH:
            next_state = self.DONE
        else:
            next_state = self.COUNT if arm_pulse else self.DONE
        if clear_pulse:
            next_state = self.IDLE

        window_start = (next_state == self.COUNT) and (self.state != self.COUNT)
        div_restart = self.active_mode and window_start

        tick = div_enable and (self.cnt == 0)
        fsm_blocking = self.active_mode and counting
        commit = self.pending and (not fsm_blocking) and (tick or div_restart)

        n_next = self.shadow_n if commit else self.n_active

        if div_restart:
            new_n_active, new_cnt, new_low = n_next, n_next - 1, self._low_half(n_next)
        elif div_enable:
            if self.cnt == 0:
                new_n_active, new_cnt, new_low = n_next, n_next - 1, self._low_half(n_next)
            else:
                new_n_active = self.n_active
                new_cnt = self.cnt - 1
                new_low = self.low_cycles
        else:
            new_n_active, new_cnt, new_low = self.n_active, self.cnt, self.low_cycles

        new_gate_sel_active = self.ctrl_gate_sel if window_start else self.gate_sel_active

        new_gate_count = 0 if window_start else (self.gate_count + 1 if counting else self.gate_count)

        new_meas_count = self.meas_count
        new_overflow = self.overflow
        if clear_pulse:
            new_meas_count, new_overflow = 0, False
        elif window_start:
            new_meas_count = 0
        elif counting and tick:
            if self.meas_count == 0xFFFF:
                new_overflow = True
            else:
                new_meas_count = self.meas_count + 1

        new_result_n, new_result_count = self.result_n, self.result_count
        if self.state == self.LATCH:
            new_result_n, new_result_count = self.n_active, self.meas_count

        new_shadow_n = self.shadow_n
        new_mode_shadow = self.mode_shadow
        if wr_pulse:
            if not reg_sel:
                new_shadow_n = n_clamped
            else:
                new_mode_shadow = data & 1

        new_ctrl_gate_sel = self.ctrl_gate_sel
        if wr_pulse and reg_sel:
            new_ctrl_gate_sel = (data >> 1) & 0x7

        new_pending = self.pending
        if wr_pulse:
            new_pending = True
        elif commit:
            new_pending = False

        new_active_mode = self.active_mode
        if commit:
            new_active_mode = self.mode_shadow

        # commit (mirrors the clock edge)
        self.wr_sync.step(wr_raw)
        self.sig_sync.step(sig_in_raw)
        self.shadow_n = new_shadow_n
        self.mode_shadow = new_mode_shadow
        self.ctrl_gate_sel = new_ctrl_gate_sel
        self.pending = new_pending
        self.active_mode = new_active_mode
        self.n_active, self.cnt, self.low_cycles = new_n_active, new_cnt, new_low
        self.state = next_state
        self.gate_sel_active = new_gate_sel_active
        self.gate_count = new_gate_count
        self.meas_count, self.overflow = new_meas_count, new_overflow
        self.result_n, self.result_count = new_result_n, new_result_count


class Bus:
    def __init__(self):
        self.wr = 0
        self.reg_sel = 0
        self.sig_in = 0
        self.sck = 0
        self.cs_n = 1

    def value(self):
        return (
            (self.wr & 1)
            | ((self.reg_sel & 1) << 1)
            | ((self.sig_in & 1) << 2)
            | ((self.sck & 1) << 3)
            | ((self.cs_n & 1) << 4)
        )


async def tick(dut, model, bus, data=0):
    """Advance both the real DUT and the golden model by exactly one clk
    cycle, from identical raw pin values -- the shared primitive every
    helper below is built from, so the two can never drift out of lockstep."""
    dut.ui_in.value = data
    dut.uio_in.value = bus.value()
    await ClockCycles(dut.clk, 1)
    model.step(bus.wr, bus.reg_sel, data, bus.sig_in)


async def golden_reset(dut):
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 1 << CS_N
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)


async def golden_write(dut, model, bus, reg_sel, value):
    bus.reg_sel = reg_sel
    await tick(dut, model, bus, data=value)
    bus.wr = 1
    for _ in range(4):
        await tick(dut, model, bus, data=value)
    bus.wr = 0
    for _ in range(4):
        await tick(dut, model, bus, data=value)


async def golden_feed_edge(dut, model, bus, low=2, high=2):
    bus.sig_in = 0
    for _ in range(low):
        await tick(dut, model, bus)
    bus.sig_in = 1
    for _ in range(high):
        await tick(dut, model, bus)


async def golden_idle(dut, model, bus, n):
    bus.sig_in = 0
    for _ in range(n):
        await tick(dut, model, bus)


async def golden_spi_read(dut, model, bus):
    bus.cs_n = 0
    for _ in range(4):
        await tick(dut, model, bus)
    bits = []
    for _ in range(24):
        bus.sck = 1
        for _ in range(4):
            await tick(dut, model, bus)
        bits.append((int(dut.uo_out.value) >> UO_MISO) & 1)
        bus.sck = 0
        for _ in range(4):
            await tick(dut, model, bus)
    bus.cs_n = 1
    for _ in range(4):
        await tick(dut, model, bus)
    value = 0
    for b in bits:
        value = (value << 1) | b
    return (value >> 16) & 0xFF, value & 0xFFFF


async def run_case(dut, rng, gate_sel, n_active, num_edges):
    await golden_reset(dut)

    model = GoldenModel()
    bus = Bus()

    await golden_write(dut, model, bus, reg_sel=0, value=n_active)
    await golden_write(dut, model, bus, reg_sel=1, value=ctrl_byte(mode=1, gate_sel=gate_sel))
    await golden_feed_edge(dut, model, bus)  # land the initial mode/N commit
    await golden_idle(dut, model, bus, 4)

    await golden_write(dut, model, bus, reg_sel=1, value=ctrl_byte(mode=1, gate_sel=gate_sel, arm=1))

    for _ in range(num_edges):
        low = rng.randint(1, 5)
        high = rng.randint(1, 5)
        await golden_feed_edge(dut, model, bus, low, high)

    window_len = 1 << (10 + 2 * gate_sel)
    await golden_idle(dut, model, bus, window_len + 20)

    assert model.state == GoldenModel.DONE, "golden model did not reach DONE -- test setup bug"
    assert ((int(dut.uo_out.value) >> UO_DONE) & 1) == 1, "DUT did not reach done in time"

    n_dut, count_dut = await golden_spi_read(dut, model, bus)
    assert n_dut == model.result_n, (
        f"N_used mismatch (gate_sel={gate_sel}, N={n_active}, edges={num_edges}): "
        f"dut={n_dut} model={model.result_n}"
    )
    assert count_dut == model.result_count, (
        f"count mismatch (gate_sel={gate_sel}, N={n_active}, edges={num_edges}): "
        f"dut={count_dut} model={model.result_count}"
    )


@cocotb.test()
async def test_golden_model_sweep(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="us").start())
    rng = random.Random(12345)

    # gate_sel=0: effectively exhaustive over N, randomized phase per case.
    for n_active in range(2, 41):
        for _ in range(5):
            num_edges = rng.randint(2, 30)
            await run_case(dut, rng, gate_sel=0, n_active=n_active, num_edges=num_edges)

    # gate_sel=1..4: spot-checked (see module docstring for the runtime rationale).
    for gate_sel in range(1, 5):
        for n_active in (2, 7, 100):
            for _ in range(2):
                num_edges = rng.randint(2, 10)
                await run_case(dut, rng, gate_sel=gate_sel, n_active=n_active, num_edges=num_edges)

    dut._log.info("test_golden_model_sweep PASSED")
