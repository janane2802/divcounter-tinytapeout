# SPDX-FileCopyrightText: © 2026 Srija Nane
# SPDX-License-Identifier: Apache-2.0

"""Directed cocotb tests for tt_um_janane2802_divcounter.

Covers: N=0/1 clamp, back-to-back writes (last write wins), a write
deferred to the next window while mid-COUNT in mode 1, a mid-run mode
switch, clear mid-window, overflow saturation + sticky flag, single-shot
vs continuous, and SPI serial edge cases (update-during-read, cs_n abort,
back-to-back reads, sck at clk/8 and clk/4).

See test_golden.py for the cycle-accurate golden-model sweep and
test_runt_pulse.v (run separately via iverilog, not cocotb) for the
exhaustive glitch-free ratio-switching property check.
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles

WR, REG_SEL, SIG_IN, SCK, CS_N = 0, 1, 2, 3, 4
UO_CLK_OUT, UO_MISO, UO_BUSY, UO_DONE, UO_OVERFLOW, UO_RATIO_PENDING, UO_RATIO_APPLIED, UO_GATE = range(8)


class Bus:
    """Tracks the uio_in bits for the independent control lines (wr,
    reg_sel, sig_in, sck, cs_n) so each helper can toggle one line without
    disturbing the others."""

    def __init__(self, dut):
        self.dut = dut
        self.bits = 1 << CS_N  # idle: chip select deasserted
        self.dut.uio_in.value = self.bits

    def set(self, bit, value):
        if value:
            self.bits |= 1 << bit
        else:
            self.bits &= ~(1 << bit)
        self.dut.uio_in.value = self.bits


def uo_bit(dut, bit):
    return (int(dut.uo_out.value) >> bit) & 1


def ctrl_byte(mode=0, gate_sel=0, continuous=0, arm=0, clear=0):
    return (
        (mode & 1)
        | ((gate_sel & 0x7) << 1)
        | ((continuous & 1) << 4)
        | ((arm & 1) << 5)
        | ((clear & 1) << 6)
    )


async def start_clock(dut):
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())


async def reset(dut):
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 1 << CS_N
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)


async def write_reg(dut, bus, reg_sel, value):
    """Pulse wr for a few cycles with data_bus/reg_sel held stable around it
    -- comfortably longer than the 2-FF-sync + edge-detect minimum, matching
    the documented 'hold stable >=3 clk cycles around wr' host requirement."""
    dut.ui_in.value = value
    bus.set(REG_SEL, reg_sel)
    await ClockCycles(dut.clk, 1)
    bus.set(WR, 1)
    await ClockCycles(dut.clk, 4)
    bus.set(WR, 0)
    await ClockCycles(dut.clk, 4)


async def write_n(dut, bus, n):
    await write_reg(dut, bus, reg_sel=0, value=n & 0xFF)


class Ctrl:
    """Tracks the persistent control-register fields (mode, gate_sel,
    continuous) so arm/clear writes don't accidentally reset them -- every
    write to reg_sel=1 is a full byte, so the test must always restate the
    fields it isn't changing."""

    def __init__(self, dut, bus):
        self.dut = dut
        self.bus = bus
        self.mode = 0
        self.gate_sel = 0
        self.continuous = 0

    async def _write(self, arm=0, clear=0):
        await write_reg(
            self.dut, self.bus, reg_sel=1,
            value=ctrl_byte(mode=self.mode, gate_sel=self.gate_sel,
                             continuous=self.continuous, arm=arm, clear=clear),
        )

    async def set_mode(self, mode):
        self.mode = mode
        await self._write()

    async def set_gate_sel(self, gate_sel):
        self.gate_sel = gate_sel
        await self._write()

    async def set_continuous(self, continuous):
        self.continuous = continuous
        await self._write()

    async def arm_now(self):
        await self._write(arm=1)

    async def clear_now(self):
        await self._write(clear=1)


async def sig_in_pulse(dut, bus, low_cycles=2, high_cycles=4):
    """One rising edge on sig_in, held long enough either side for the 2-FF
    synchronizer to register it reliably in simulation. high_cycles must be
    >=3: the synchronizer's rise pulse lands 2 cycles after the edge, so
    anything shorter risks the pulse only landing after this call already
    returned (still delivered, just one call later than requested)."""
    bus.set(SIG_IN, 0)
    await ClockCycles(dut.clk, low_cycles)
    bus.set(SIG_IN, 1)
    await ClockCycles(dut.clk, high_cycles)


async def feed_edges(dut, bus, n, low_cycles=2, high_cycles=4):
    for _ in range(n):
        await sig_in_pulse(dut, bus, low_cycles, high_cycles)


async def measure_period_mode0(dut, cycles_to_watch=60):
    """Count clk cycles between consecutive clk_out rising edges (mode 0
    only -- clk_out free-runs there). Returns the list of observed periods."""
    periods = []
    prev = uo_bit(dut, UO_CLK_OUT)
    count = 0
    for _ in range(cycles_to_watch):
        await ClockCycles(dut.clk, 1)
        count += 1
        cur = uo_bit(dut, UO_CLK_OUT)
        if cur == 1 and prev == 0:
            periods.append(count)
            count = 0
        prev = cur
    return periods


async def spi_read_frame(dut, bus, half_period=4):
    """Read one 24-bit {N_used, count} frame, MSB first, SPI mode 0."""
    bus.set(CS_N, 0)
    await ClockCycles(dut.clk, 4)
    bits = []
    for _ in range(24):
        bus.set(SCK, 1)
        await ClockCycles(dut.clk, half_period)
        bits.append((int(dut.uo_out.value) >> UO_MISO) & 1)
        bus.set(SCK, 0)
        await ClockCycles(dut.clk, half_period)
    bus.set(CS_N, 1)
    await ClockCycles(dut.clk, 4)

    value = 0
    for b in bits:
        value = (value << 1) | b
    n_used = (value >> 16) & 0xFF
    count = value & 0xFFFF
    return n_used, count


# ---------------------------------------------------------------------------
# Directed tests
# ---------------------------------------------------------------------------


@cocotb.test()
async def test_n_clamp(dut):
    """1. N=0 and N=1 both clamp to N=2."""
    await start_clock(dut)
    bus = Bus(dut)
    await reset(dut)

    await write_n(dut, bus, 0)
    await ClockCycles(dut.clk, 20)
    periods = await measure_period_mode0(dut, 60)
    assert periods and all(p == 2 for p in periods[-3:]), f"N=0 should clamp to 2, got {periods}"

    await write_n(dut, bus, 1)
    await ClockCycles(dut.clk, 20)
    periods = await measure_period_mode0(dut, 60)
    assert periods and all(p == 2 for p in periods[-3:]), f"N=1 should clamp to 2, got {periods}"

    dut._log.info("test_n_clamp PASSED")


@cocotb.test()
async def test_back_to_back_writes(dut):
    """2. Two writes issued back-to-back, well inside the current (long)
    period, so neither commits before the next supersedes it -- the first
    (N=5) must never be observed; only the last (N=10) ever becomes active."""
    await start_clock(dut)
    bus = Bus(dut)
    await reset(dut)

    await write_n(dut, bus, 200)
    await ClockCycles(dut.clk, 210)
    periods = await measure_period_mode0(dut, 650)  # >=3 full periods, so the last 2 are never the leading partial one
    assert all(p == 200 for p in periods[-2:]), f"baseline N=200 not established: {periods}"

    await write_n(dut, bus, 5)
    await write_n(dut, bus, 10)

    await ClockCycles(dut.clk, 250)  # let the in-progress N=200 period finish and the next commit land
    periods = await measure_period_mode0(dut, 60)
    assert 5 not in periods, f"the superseded N=5 write must never be applied, got {periods}"
    assert all(p == 10 for p in periods[-3:]), f"expected N=10 (last write wins), got {periods}"

    dut._log.info("test_back_to_back_writes PASSED")


@cocotb.test()
async def test_write_during_count_deferred(dut):
    """3. In mode 1, a write issued mid-COUNT is deferred: N_used for the
    window in progress stays the N that was active when it started, and the
    deferred write only lands once the FSM leaves COUNT."""
    await start_clock(dut)
    bus = Bus(dut)
    ctrl = Ctrl(dut, bus)
    await reset(dut)

    await ctrl.set_mode(1)
    await write_n(dut, bus, 4)
    await feed_edges(dut, bus, 6)  # idle edges so the initial mode/N commit lands

    await ctrl.set_gate_sel(0)
    await ctrl.arm_now()

    await feed_edges(dut, bus, 2)
    await write_n(dut, bus, 7)
    assert uo_bit(dut, UO_RATIO_PENDING) == 1, "the write should be pending, not yet applied"

    await ClockCycles(dut.clk, 1040)  # finish the 1024-cycle window

    n_used, _count = await spi_read_frame(dut, bus)
    assert n_used == 4, f"N_used must reflect the N active for the whole window, got {n_used}"
    assert uo_bit(dut, UO_RATIO_PENDING) == 1, "commit should still be deferred -- no edges arrived since DONE"

    await feed_edges(dut, bus, 5)  # force at least one more wrap -> commit point
    assert uo_bit(dut, UO_RATIO_PENDING) == 0, "pending write should have committed by now"

    await ctrl.arm_now()
    await ClockCycles(dut.clk, 1040)
    n_used2, _count2 = await spi_read_frame(dut, bus)
    assert n_used2 == 7, f"expected the committed N=7 to be used for the new window, got {n_used2}"

    dut._log.info("test_write_during_count_deferred PASSED")


@cocotb.test()
async def test_mid_run_mode_switch(dut):
    """4. Mid-run switch from mode 0 to mode 1: the pending mode change
    commits like any other ratio write, clk_out freezes once mode 1 takes
    over, and feeding sig_in edges resumes activity."""
    await start_clock(dut)
    bus = Bus(dut)
    ctrl = Ctrl(dut, bus)
    await reset(dut)

    await write_n(dut, bus, 6)
    await ClockCycles(dut.clk, 30)
    periods = await measure_period_mode0(dut, 60)
    assert all(p == 6 for p in periods[-3:]), f"baseline N=6 not established: {periods}"

    before_applied = uo_bit(dut, UO_RATIO_APPLIED)
    await ctrl.set_mode(1)
    await ClockCycles(dut.clk, 12)
    assert uo_bit(dut, UO_RATIO_PENDING) == 0, "mode switch should have committed by now"
    assert uo_bit(dut, UO_RATIO_APPLIED) != before_applied, "ratio_applied should toggle on commit"

    frozen = uo_bit(dut, UO_CLK_OUT)
    await ClockCycles(dut.clk, 20)
    assert uo_bit(dut, UO_CLK_OUT) == frozen, "clk_out must not move on its own once in mode 1"

    await feed_edges(dut, bus, 3)  # half of N=6
    assert uo_bit(dut, UO_CLK_OUT) == 0, "halfway into the N=6 period, clk_out should have gone low"
    await feed_edges(dut, bus, 3)
    assert uo_bit(dut, UO_CLK_OUT) == 1, "a full period of edges should bring clk_out back high"

    dut._log.info("test_mid_run_mode_switch PASSED")


@cocotb.test()
async def test_clear_mid_window(dut):
    """5. clear aborts an in-progress window immediately, resetting
    overflow/meas_count and returning the FSM to IDLE so it can be re-armed."""
    await start_clock(dut)
    bus = Bus(dut)
    ctrl = Ctrl(dut, bus)
    await reset(dut)

    await ctrl.arm_now()  # mode 0, gate_sel=0 -> 1024-cycle window
    await ClockCycles(dut.clk, 50)
    assert uo_bit(dut, UO_BUSY) == 1 and uo_bit(dut, UO_GATE) == 1, "should be mid-window"

    await ctrl.clear_now()
    await ClockCycles(dut.clk, 2)
    assert uo_bit(dut, UO_BUSY) == 0
    assert uo_bit(dut, UO_DONE) == 0
    assert uo_bit(dut, UO_GATE) == 0
    assert uo_bit(dut, UO_OVERFLOW) == 0

    await ctrl.arm_now()
    await ClockCycles(dut.clk, 5)
    assert uo_bit(dut, UO_BUSY) == 1, "should be able to re-arm cleanly after a mid-window clear"

    dut._log.info("test_clear_mid_window PASSED")


@cocotb.test()
async def test_overflow_saturation_sticky(dut):
    """6. meas_count saturates at 0xFFFF on overflow, and the sticky flag
    persists across a subsequent non-overflowing measurement until cleared."""
    await start_clock(dut)
    bus = Bus(dut)
    ctrl = Ctrl(dut, bus)
    await reset(dut)

    await write_n(dut, bus, 2)  # fastest possible tick rate in mode 0
    await ClockCycles(dut.clk, 10)

    await ctrl.set_gate_sel(4)  # window = 2^18 = 262144 clk cycles -> up to 131072 ticks
    await ctrl.arm_now()
    await ClockCycles(dut.clk, 262144 + 20)

    assert uo_bit(dut, UO_DONE) == 1
    assert uo_bit(dut, UO_OVERFLOW) == 1, "131072 available ticks should have overflowed the 16-bit counter"

    _n_used, count = await spi_read_frame(dut, bus)
    assert count == 0xFFFF, f"count should saturate at 0xFFFF, got {count}"

    await ctrl.set_gate_sel(0)  # short window -> far fewer ticks, no overflow this time
    await ctrl.arm_now()
    await ClockCycles(dut.clk, 1024 + 20)
    assert uo_bit(dut, UO_DONE) == 1
    assert uo_bit(dut, UO_OVERFLOW) == 1, "overflow is sticky -- must remain set until an explicit clear"

    await ctrl.clear_now()
    await ClockCycles(dut.clk, 2)
    assert uo_bit(dut, UO_OVERFLOW) == 0, "clear should reset the sticky overflow flag"

    dut._log.info("test_overflow_saturation_sticky PASSED")


@cocotb.test()
async def test_single_shot_vs_continuous(dut):
    """7. Single-shot stays in DONE until re-armed; continuous auto-restarts
    the next window without a new arm."""
    await start_clock(dut)
    bus = Bus(dut)
    ctrl = Ctrl(dut, bus)
    await reset(dut)

    await ctrl.arm_now()
    await ClockCycles(dut.clk, 1024 + 10)
    assert uo_bit(dut, UO_DONE) == 1
    await ClockCycles(dut.clk, 50)
    assert uo_bit(dut, UO_DONE) == 1, "single-shot must stay DONE until explicitly re-armed"
    assert uo_bit(dut, UO_BUSY) == 0

    await ctrl.set_continuous(1)
    await ctrl.arm_now()
    await ClockCycles(dut.clk, 1024 + 2)
    await ClockCycles(dut.clk, 5)
    assert uo_bit(dut, UO_BUSY) == 1, "continuous mode should auto-restart the next window without re-arming"

    dut._log.info("test_single_shot_vs_continuous PASSED")


# ---------------------------------------------------------------------------
# Serial (SPI) tests
# ---------------------------------------------------------------------------


@cocotb.test()
async def test_result_update_during_read(dut):
    """Serial 1: a result update mid-read must not corrupt the frame already
    snapshotted at cs_n-fall; a fresh read afterward sees the new result."""
    await start_clock(dut)
    bus = Bus(dut)
    ctrl = Ctrl(dut, bus)
    await reset(dut)

    await ctrl.set_gate_sel(0)

    await write_n(dut, bus, 5)
    await ClockCycles(dut.clk, 10)
    await ctrl.arm_now()
    await ClockCycles(dut.clk, 1024 + 10)
    assert uo_bit(dut, UO_DONE) == 1

    bus.set(CS_N, 0)
    await ClockCycles(dut.clk, 4)
    bits = []
    for i in range(24):
        bus.set(SCK, 1)
        await ClockCycles(dut.clk, 4)
        bits.append((int(dut.uo_out.value) >> UO_MISO) & 1)
        bus.set(SCK, 0)
        await ClockCycles(dut.clk, 4)
        if i == 11:
            # mid-frame: let a new (third) measurement complete entirely
            await write_n(dut, bus, 9)
            await ClockCycles(dut.clk, 10)
            await ctrl.arm_now()
            await ClockCycles(dut.clk, 1024 + 10)
            assert uo_bit(dut, UO_DONE) == 1
    bus.set(CS_N, 1)
    await ClockCycles(dut.clk, 4)

    value = 0
    for b in bits:
        value = (value << 1) | b
    n_mid = (value >> 16) & 0xFF

    assert n_mid == 5, f"frame snapshotted at cs_n-fall must stay N=5 despite a new result latching mid-read, got N={n_mid}"

    n3, _c3 = await spi_read_frame(dut, bus)
    assert n3 == 9, f"a new read after cs_n is released should see the latest result, got N={n3}"

    dut._log.info("test_result_update_during_read PASSED")


@cocotb.test()
async def test_cs_n_abort_mid_frame(dut):
    """Serial 2: releasing cs_n mid-frame abandons the partial transfer; the
    next cs_n-fall starts a fresh, correct frame."""
    await start_clock(dut)
    bus = Bus(dut)
    ctrl = Ctrl(dut, bus)
    await reset(dut)

    await write_n(dut, bus, 11)
    await ClockCycles(dut.clk, 10)
    await ctrl.arm_now()
    await ClockCycles(dut.clk, 1024 + 10)

    bus.set(CS_N, 0)
    await ClockCycles(dut.clk, 4)
    for _ in range(5):
        bus.set(SCK, 1)
        await ClockCycles(dut.clk, 4)
        bus.set(SCK, 0)
        await ClockCycles(dut.clk, 4)
    bus.set(CS_N, 1)
    await ClockCycles(dut.clk, 4)

    n_used, _count = await spi_read_frame(dut, bus)
    assert n_used == 11, f"aborted partial read should not corrupt the next full read, got N={n_used}"

    dut._log.info("test_cs_n_abort_mid_frame PASSED")


@cocotb.test()
async def test_back_to_back_reads(dut):
    """Serial 3: immediately re-asserting cs_n after a read starts a fresh,
    correct frame."""
    await start_clock(dut)
    bus = Bus(dut)
    ctrl = Ctrl(dut, bus)
    await reset(dut)

    await write_n(dut, bus, 13)
    await ClockCycles(dut.clk, 10)
    await ctrl.arm_now()
    await ClockCycles(dut.clk, 1024 + 10)

    n1, c1 = await spi_read_frame(dut, bus)
    n2, c2 = await spi_read_frame(dut, bus)
    assert n1 == 13 and n2 == 13, f"back-to-back reads should both return the same correct frame, got {n1},{n2}"
    assert c1 == c2

    dut._log.info("test_back_to_back_reads PASSED")


@cocotb.test()
async def test_sck_rates(dut):
    """Serial 4: SPI readout is correct at sck = clk/8 (documented max) and
    clk/4 (faster than the documented margin -- still correct here since
    RTL sim has no metastability, but outside the documented safe margin on
    real silicon; see docs/info.md)."""
    await start_clock(dut)
    bus = Bus(dut)
    ctrl = Ctrl(dut, bus)
    await reset(dut)

    await write_n(dut, bus, 17)
    await ClockCycles(dut.clk, 10)
    await ctrl.arm_now()
    await ClockCycles(dut.clk, 1024 + 10)

    n_8, c_8 = await spi_read_frame(dut, bus, half_period=4)  # sck = clk/8
    assert n_8 == 17

    n_4, c_4 = await spi_read_frame(dut, bus, half_period=2)  # sck = clk/4
    assert n_4 == 17
    assert c_4 == c_8

    dut._log.info("test_sck_rates PASSED")
