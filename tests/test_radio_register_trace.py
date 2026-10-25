"""Compile and execute the real register-trace implementation with a mock SPI bus."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "esphome/components/proflame2_tembed"


@pytest.mark.parametrize("enabled", [0, 1])
def test_radio_trace_readback_and_disabled_noop(tmp_path: Path, enabled: int) -> None:
    compiler = shutil.which("g++")
    assert compiler, "g++ is required for firmware host tests"
    executable = tmp_path / "radio_trace"
    subprocess.run(
        [
            compiler,
            "-std=c++17",
            "-Wall",
            "-Wextra",
            "-Werror",
            f"-DPROFLAME2_RADIO_TRACE={enabled}",
            f"-I{ROOT / 'tests/cpp/radio_trace_stubs'}",
            f"-I{COMPONENT}",
            str(COMPONENT / "radio_cc1101.cpp"),
            str(ROOT / "tests/cpp/test_radio_trace.cpp"),
            "-o",
            str(executable),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run([str(executable)], check=True, capture_output=True, text=True)


def test_trace_is_outside_waveform_and_enabled_by_diagnostic_package() -> None:
    tx = (COMPONENT / "radio_cc1101_tx.cpp").read_text()
    assert "trace_radio_registers" not in tx
    component = (COMPONENT / "proflame2_tembed.cpp").read_text()
    for stage in (
        "radio_initialized",
        "rmt_rx_stopping",
        "rmt_rx_stopped_tx_config",
        "tx_prepared",
        "tx_complete",
        "rmt_rx_running",
    ):
        assert f'trace_radio_registers("{stage}"' in component
    package = (ROOT / "esphome/packages/proflame2_tembed_base.yaml").read_text()
    assert "-DPROFLAME2_RADIO_TRACE=1" in package
