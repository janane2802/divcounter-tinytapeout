"""
Gate-level (post-synthesis netlist) cocotb runner. Mirrors the GATES=yes
block in test/Makefile: functional, unit-delay gate-level simulation against
the hardened netlist + sky130_fd_sc_hd cell models, not timing-annotated
(no SDF/SPEF back-annotation -- that isn't part of this project's standard
GL sim flow).

Requires test/gate_level_netlist.v (the powered netlist, copied from
runs/<tag>/final/pnl/<design>.pnl.v) and test/pdk_models/{primitives,
sky130_fd_sc_hd}.v (copied from the PDK) to be present locally.
"""

import os
import sys
from pathlib import Path

from cocotb_tools.runner import get_runner

TEST_DIR = Path(__file__).resolve().parent
SRC_DIR = TEST_DIR.parent / "src"
PDK_MODELS_DIR = TEST_DIR / "pdk_models"

GATE_LEVEL_NETLIST = TEST_DIR / "gate_level_netlist.v"


def main():
    if not GATE_LEVEL_NETLIST.exists():
        sys.exit(f"Missing {GATE_LEVEL_NETLIST} -- copy it from the hardening run's final/pnl/ output first.")
    if not (PDK_MODELS_DIR / "sky130_fd_sc_hd.v").exists():
        sys.exit(f"Missing {PDK_MODELS_DIR}/sky130_fd_sc_hd.v -- copy it from the PDK's libs.ref verilog dir first.")

    # Gate-level sim (real standard-cell primitives, not behavioral RTL) is
    # far more expensive per cycle than RTL sim. test_golden.py's exhaustive
    # sweep already ran -- and passed -- at RTL; here it only needs to catch
    # synthesis-introduced bugs, not re-prove the algorithm, so it reads this
    # to cut its scope way down. See test_golden.py and docs/info.md.
    os.environ["GATE_LEVEL_SIM"] = "1"

    runner = get_runner("icarus")

    verilog_sources = [
        PDK_MODELS_DIR / "primitives.v",
        PDK_MODELS_DIR / "sky130_fd_sc_hd.v",
        GATE_LEVEL_NETLIST,
        TEST_DIR / "tb.v",
    ]

    runner.build(
        verilog_sources=verilog_sources,
        includes=[SRC_DIR],
        hdl_toplevel="tb",
        always=True,
        build_dir=TEST_DIR / "sim_build" / "gl",
        build_args=["-DGL_TEST", "-DFUNCTIONAL", "-DUSE_POWER_PINS", "-DSIM", "-DUNIT_DELAY=#1"],
    )

    runner.test(
        test_module="test,test_golden",
        hdl_toplevel="tb",
        test_dir=TEST_DIR,
        build_dir=TEST_DIR / "sim_build" / "gl",
        results_xml="results_gl.xml",
    )


if __name__ == "__main__":
    sys.path.insert(0, str(TEST_DIR))
    main()
