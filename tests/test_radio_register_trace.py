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
        "rmt_rx_running",
    ):
        assert f'trace_radio_registers("{stage}"' in component
    package = (ROOT / "esphome/packages/proflame2_tembed_base.yaml").read_text()
    assert "-DPROFLAME2_RADIO_TRACE=1" in package


def test_worker_snapshots_are_deferred_through_completion_on_success_and_failure() -> None:
    component = (COMPONENT / "proflame2_tembed.cpp").read_text()
    worker = component.split("static void radio_runtime_task_entry_", 1)[1].split(
        "void Proflame2TEmbedComponent::setup()", 1
    )[0]
    assert "trace_radio_registers(" not in worker
    assert "log_radio_registers(" not in worker
    before = worker.index("event.trace_before_tx = runtime->owner->capture_radio_registers()")
    transmit = worker.index("event.ok = runtime->owner->transmit_async_ook(")
    after = worker.index("event.trace_after_tx = runtime->owner->capture_radio_registers()")
    error_branch = worker.index("if (!event.ok)")
    queue = worker.index("xQueueSend(runtime->event_queue")
    assert before < transmit < after < error_branch < queue
    completion = component.split("RadioRuntimeEvent result;", 1)[1]
    restore = completion.index("this->restore_rx_after_tx_if_needed_();")
    before_output = completion.index('log_radio_registers(result.trace_before_tx, "tx_prepared", result.request_id)')
    after_output = completion.index('log_radio_registers(result.trace_after_tx, "tx_complete", result.request_id)')
    failure = completion.index("if (!result.ok)")
    assert restore < before_output < after_output < failure
