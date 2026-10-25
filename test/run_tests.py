"""
Standalone cocotb runner (Windows-friendly alternative to `make`, which
depends on GNU make / unix tools like `tr` that don't work well natively
on this machine). Mirrors the config in test/Makefile.
"""

import sys
from pathlib import Path

from cocotb_tools.runner import get_runner

TEST_DIR = Path(__file__).resolve().parent
SRC_DIR = TEST_DIR.parent / "src"

PROJECT_SOURCES = ["project.v", "divider_core.v", "meas_fsm.v", "spi_slave.v", "sync2.v"]


def main():
    runner = get_runner("icarus")

    verilog_sources = [SRC_DIR / f for f in PROJECT_SOURCES] + [TEST_DIR / "tb.v"]

    runner.build(
        verilog_sources=verilog_sources,
        includes=[SRC_DIR],
        hdl_toplevel="tb",
        always=True,
        build_dir=TEST_DIR / "sim_build" / "rtl",
    )

    runner.test(
        test_module="test,test_golden",
        hdl_toplevel="tb",
        test_dir=TEST_DIR,
        build_dir=TEST_DIR / "sim_build" / "rtl",
        results_xml="results.xml",
    )


if __name__ == "__main__":
    sys.path.insert(0, str(TEST_DIR))
    main()
