# HANDOFF — divcounter-tinytapeout

## What it is

A Tiny Tapeout ASIC project: `tt_um_janane2802_divcounter`, a glitch-free
programmable clock divider (N = 2..255) that doubles as a gated frequency
counter with SPI serial readout. Single clock domain throughout — no
generated/derived clocks, no ripple dividers. The headline feature is
glitch-free ratio/mode switching: a write to the divide ratio or mode
never affects the period already in progress, only the next one, via a
shadow-register-plus-commit-at-period-boundary mechanism. Target: SKY130,
1x1 tile, `ttsky-verilog-template`, shuttle **TTSKY26d** (confirmed open,
closes 2026-11-30, as of 2026-10-25 — re-verify at tinytapeout.com if
resuming after that date).

**This project has NOT been submitted to the shuttle.** That is
deliberately the one step left — see "What is NOT done" below.

## Where things live

- Repo: https://github.com/janane2802/divcounter-tinytapeout (public)
- Local: `D:\Projects\divcounter-tinytapeout` (WSL:
  `/mnt/d/Projects/divcounter-tinytapeout`)
- `src/` — RTL (see Design below)
- `test/` — cocotb tests, the standalone runt-pulse checker, GL test
  fixtures (gitignored: `gate_level_netlist.v`, `pdk_models/`)
- `docs/info.md` — the full datasheet: how it works, register map, SPI
  frame format + worked example, hardening results, precheck results,
  known limitations. **Read this for design detail** — this handoff
  doesn't repeat it all.
- `tt/` — local clone of `TinyTapeout/tt-support-tools`, gitignored, not
  committed. Used for `tt_tool.py --harden`, `--create-tt-submission`,
  `--check-docs`, and `tt/precheck/precheck.py`.
- `runs/wokwi/` — the accepted hardening run output (gitignored). Final
  GDS/netlist/LEF at `runs/wokwi/final/{gds,pnl,nl,...}/`.
- `tt_submission/` — the TT submission package (gitignored), built by
  `tt_tool.py --create-tt-submission`.

## Design

Five source files, all under `src/`:

- `project.v` — top level: pin wiring, the shadow-register write/commit
  logic (N and mode changes), instantiates the three modules below.
- `divider_core.v` — the 8-bit down-counter + registered duty-cycle
  output flop. This is where the glitch-free guarantee actually lives:
  a ratio change (`commit`) is only ever consumed at a wrap or a forced
  `restart`, never mid-period.
- `meas_fsm.v` — the IDLE→COUNT→LATCH→DONE measurement FSM, gate timer,
  16-bit saturating/sticky-overflow measurement counter, result latch.
- `spi_slave.v` — SPI mode-0 shift register, snapshotted on `cs_n` fall.
- `sync2.v` — the 2-FF synchronizer + edge detector reused for every
  external pin (`wr`, `sig_in`, `sck`, `cs_n`).

Pinout (full detail, register map, SPI frame format, and a worked
frequency-calculation example are in `docs/info.md`):

| Pin | Signal |
|---|---|
| `ui_in[7:0]` | data bus |
| `uio_in[0]` | `wr` |
| `uio_in[1]` | `reg_sel` (0 = N register, 1 = control register) |
| `uio_in[2]` | `sig_in` |
| `uio_in[3]` | `sck` |
| `uio_in[4]` | `cs_n` |
| `uio_in[7:5]` | spare, unused |
| `uo_out[0]` | `clk_out` |
| `uo_out[1]` | `miso` |
| `uo_out[2]` | `busy` |
| `uo_out[3]` | `done` |
| `uo_out[4]` | `overflow` |
| `uo_out[5]` | `ratio_pending` |
| `uo_out[6]` | `ratio_applied` |
| `uo_out[7]` | `gate` |

`uio_oe = 8'h00` always — every `uio` pin is an input.

## Verification state

**Verified by a tool run** (not just discussed):

1. **cocotb directed + serial tests** (`test/test.py`, 11 tests): N=0/1
   clamp, back-to-back writes (last write wins), write deferred to the
   next window during mode-1 COUNT, mid-run mode switch, clear mid-window,
   overflow saturation + sticky flag, single-shot vs continuous, SPI
   result-update-during-read, cs_n abort mid-frame, back-to-back reads,
   sck at clk/8 and clk/4. **All pass**, both at RTL
   (`python test/run_tests.py`) and gate level
   (`python test/run_tests_gl.py`).
2. **Cycle-accurate golden-model sweep** (`test/test_golden.py`): a
   from-scratch Python replica of the whole write/commit/divider/
   measurement path, driven in per-cycle lockstep with the DUT, compared
   exactly (not ±1). Exhaustive over N=2..40 at `gate_sel=0`, spot-checked
   at `gate_sel=1..4`; `gate_sel=5..7` not simulated end-to-end (documented
   in the file and in `docs/info.md`) — those three window-length lookup
   constants were checked by hand instead. At gate level, this sweep
   further reduces itself to 8 representative cases (`GATE_LEVEL_SIM` env
   var) — the exhaustive run already passed at RTL, gate level only needs
   to catch a synthesis-introduced bug. **All pass.**
3. **Standalone exhaustive runt-pulse checker** (`test/test_runt_pulse.v`,
   run directly with iverilog, not cocotb): for 13 documented (N_old,
   N_new) pairs covering {even,odd} × {min=2, max=255, max-1, and three
   mid-range values}, sweeps **every** phase offset in the N_old period
   and asserts the in-progress period always finishes at N_old (correct
   length, correct duty, no runt pulse) and the next period is exactly
   N_new. 1236 trials, 0 errors. **This is the actual proof of the
   headline glitch-free claim** — the other two layers test the protocol
   around it, this one tests the waveform itself.

**Caveats, explicit per the project's own rules:**

- Metastability on `sig_in`/`sck`/`cs_n`/`wr` cannot be exercised in
  simulation at all — the 2-FF synchronizers are a mitigation, and
  simulation can only confirm their logical (not metastability)
  behavior.
- Gate-level simulation is functional, unit-delay only (`-DUNIT_DELAY=#1`,
  no SDF/SPEF back-annotation) — it confirms logic against the real
  netlist, not real silicon timing margins (that's LibreLane's signoff
  STA's job, below).
- The runt-pulse checker's 13-pair scope and the golden-model sweep's
  `gate_sel=5..7` scope are both documented, deliberate reductions from
  literal exhaustion — see each file's header comment and `docs/info.md`
  for the reasoning.

**A real RTL bug was found and fixed along the way**: `divider_core.v`'s
duty-cycle `low_half` helper did `(n + 8'd1)` in 8-bit arithmetic, which
overflows at n=255 (wraps to 0), corrupting the duty split at the top of
the N range. Caught by the runt-pulse checker, fixed by widening the
intermediate sum to 9 bits before shifting. Commit `464dc78`.

## Hardening results

LibreLane 3.0.5, sky130A, 1x1 tile, 50MHz (`CLOCK_PERIOD`=20ns):

- **Area/utilization**: 16493.3 um² core / 17954.7 um² die (fits the
  1x1 tile exactly), 65.2% utilization, 2767 instances.
- **DRC, LVS, antenna**: all clean (0 errors/violations everywhere).
- **Setup/hold timing**: 0 violations at all 9 signoff PVT corners.
- **Max slew**: 120 violations, all at the `*_ss_100C_1v60` corner
  cluster (slow/hot/low-voltage) — every other corner is clean.

**Known limitation, accepted after informed attempts** (not abandoned
prematurely — see `docs/info.md`'s Known Limitations for the full
writeup): two more aggressive `DESIGN_REPAIR_MAX_SLEW_PCT` settings (70,
then 60) were tried to reduce the 120 slew violations further. Both made
OpenROAD's resizer repair pass run away in memory and get OOM-killed on
this machine's 3.7GiB WSL VM — the 70 attempt also wedged the whole WSL
VM, requiring `wsl --shutdown` to recover. Neither attempt got far enough
to show whether the setting itself would have helped; it's a resource
ceiling on this specific machine, not a proof that a higher setting
wouldn't work elsewhere. Reverted to 50 (density unchanged from the
template default, 60), which is what's committed and is clean apart from
the documented slew violations.

## Precheck

Ran `tt/precheck/precheck.py` against
`tt_submission/tt_um_janane2802_divcounter.gds`. **14/15 checks pass.**
The one failure, Magic DRC, is a local-environment artifact: this
machine's `apt` Magic is 8.3.105 (2021), too old for the PDK techfile.
Confirmed redundant with hardening's own Magic DRC step
(`runs/wokwi/62-magic-drc` / `63-checker-magicdrc`, using a current Magic
build inside LibreLane's Docker container), which reports
`magic__drc_error__count: 0`. Precheck doesn't check timing at all, so
the max-slew limitation above isn't something precheck would catch
either way.

Two tool-invocation gotchas if re-running precheck by hand (not design
issues, just how the tool expects to be invoked):
`PDK_ROOT` must point at the specific PDK version directory (the one that
directly contains `sky130A/`, e.g.
`~/.ciel/ciel/sky130/versions/<hash>`) — not the top-level ciel root
LibreLane itself is given. And `precheck.py` must be run from inside
`tt/precheck/` (it resolves several of its own data files with relative
paths), with the `--gds` path passed as absolute (it finds `info.yaml`
by walking up from the GDS's directory, which only works with an
absolute path).

## Docs

`docs/info.md` is complete and passes `tt_tool.py --check-docs`: How it
works (including the glitch-free mechanism and synchronization in
detail), register map, write protocol, SPI frame format + a worked
`f_in` calculation example, how to test, external hardware (none), the
hardening results and precheck results above, and known limitations
(sig_in/sck rate limits, prescaler resolution cost, metastability,
gate-level-sim scope, and both test-suite scope reductions).

`info.yaml`: title, description, pinout, `clock_hz: 50000000`,
`tiles: "1x1"`, source_files all list correctly.
**`author:` is left as `"TODO"`** — confirm the spelling yourself before
submission, per your own instruction not to guess it.

## CI

GitHub Actions on every push (`.github/workflows/{test,gds,docs,fpga}.yaml`,
using `TinyTapeout/tt-gds-action@ttsky26d`):

| Commit | test | docs | gds job | precheck job | gl_test job | viewer job |
|---|---|---|---|---|---|---|
| `29fa955` template | ✅ | ❌ (blank docs, expected) | — | — | — | ❌ (Pages) |
| `464dc78` RTL+tests | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ (Pages) |
| `c906e3d` harden | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ (Pages) |
| `e76c3b6` GL verify | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ (Pages) |
| `790a593` docs | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ (Pages) |

`85fe96f` (the current HEAD, precheck docs) was still running when I
checked (https://github.com/janane2802/divcounter-tinytapeout/actions/runs/40206196011)
-- no reason to expect it to differ from the pattern above, but not
confirmed; worth a glance.

Every `viewer` job failure is the same cause: "Failed to create
deployment (status: 404)" — **GitHub Pages is not enabled for this repo.**
**You need to do this by hand**: repo Settings → Pages → Source =
"GitHub Actions". This is cosmetic (the results-page viewer), not a
submission gate — `gds`/`precheck`/`gl_test` all pass independently of it.

CI was checked by fetching the Actions run pages directly (run URLs
above are in the git log / github.com/janane2802/divcounter-tinytapeout/actions);
not from a stale cached view — each job's individual pass/fail was read
off the actual run page.

## What is NOT done

- **Submission is NOT done.** Nothing has been submitted to the TTSKY26d
  shuttle. Steps left: confirm `author:` in `info.yaml`, enable GitHub
  Pages (above), do a final look at the submission GDS/docs on
  app.tinytapeout.com, and actually submit — all yours to do.
- **SymbiYosys formal proof** (the optional stretch goal) was not
  attempted. The runt-pulse checker already gives strong, exhaustive-ish
  confidence in the no-runt-pulse property; a formal proof would be
  additive, not a gap in what's already verified.
- **Metastability** is fundamentally not verifiable in simulation (see
  Verification state above) — not a gap in this project's work, just a
  hard limit of what simulation can show.
- Precheck's Magic DRC and the two slew-tuning attempts are documented
  limitations, not loose ends — see above for why they're accepted
  rather than left as TODOs.

## Environment gotchas (this session, beyond the ones you already gave me)

- **Docker Desktop is gone from this machine.** WSL's Ubuntu-24.04 now
  runs native `docker-ce` + systemd directly. It works, but the `docker`
  CLI's default context was stale (pointed at `desktop-linux`, which no
  longer exists) — fixed with `docker context use default`. If `docker`
  commands panic or say "protocol not available" on this machine again,
  check `docker context ls` first.
- **This machine's WSL VM has only 3.7GiB RAM** (host has ~7.7GiB total).
  Aggressive LibreLane repair settings (`DESIGN_REPAIR_MAX_SLEW_PCT` 60+)
  can make OpenROAD's resizer run away in memory, OOM-kill, and in one
  case wedge the whole WSL VM (even `wsl.exe -- echo hello` timed out).
  Fix: `wsl --shutdown` from PowerShell, then relaunch. Prefer dialing
  settings back over pushing them further here.
- **No `gh` CLI, and I'm not to install it or log into GitHub in Chrome
  myself** (a winget install attempt hung on a UAC prompt I can't
  answer). Repo creation and anything needing fresh GitHub auth goes
  through you; pushes work fine from native Windows `git` (Git Credential
  Manager already has a cached token).
- `tt_tool.py` resolves `src/config.json`/`src/user_config.json` with
  *relative* paths from wherever it's invoked, regardless of
  `--project-dir` — `create_user_config()` in particular doesn't `chdir`
  first the way `harden()` does. Invoke it as
  `cd <project-dir> && python3 tt/tt_tool.py <flag>` (no `--project-dir`,
  rely on cwd), not from inside `tt/`.
- C: drive has only ~12GB free on this machine (vs. the 20GB the original
  gotchas asked to keep clear) — didn't end up mattering since Docker's
  data lives in the WSL VM's own disk (on D:, which has ~176GB free), not
  on C:, but worth knowing if something else starts writing to C:.

## Suggested next steps

1. Confirm and fill in `author:` in `info.yaml`.
2. Enable GitHub Pages (Settings → Pages → Source = GitHub Actions) so
   the `viewer` job and results page work.
3. Re-check tinytapeout.com for TTSKY26d's current status/deadline
   (confirmed open, closing 2026-11-30, as of 2026-10-25) before
   submitting — don't assume it's still true if much time has passed.
4. Review `docs/info.md` once yourself end-to-end — it's long, and it's
   the actual datasheet reviewers/users will read.
5. If you want to push the max-slew limitation further, it'll need a
   machine with more RAM than this one's WSL VM for the repair pass to
   have room to actually try harder settings — not something to retry
   here without that.
6. Submit via app.tinytapeout.com when ready. This project deliberately
   stops short of that step.
