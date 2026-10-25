"""Compile and execute the real register-trace implementation with a mock SPI bus."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "esphome/components/proflame2_tembed"


@pytest.mark.parametrize("test_name", ["radio_readiness", "delayed_tx_retry"])
def test_radio_readiness_and_retry_policy(tmp_path: Path, test_name: str) -> None:
    compiler = shutil.which("g++")
    assert compiler
    executable = tmp_path / test_name
    sources = [str(ROOT / f"tests/cpp/test_{test_name}.cpp")]
    if test_name == "radio_readiness":
        sources.append(str(COMPONENT / "radio_cc1101.cpp"))
    result = subprocess.run(
        [
            compiler,
            "-std=c++17",
            "-Wall",
            "-Wextra",
            "-Werror",
            "--coverage",
            f"-I{ROOT / 'tests/cpp/radio_trace_stubs'}",
            f"-I{COMPONENT}",
            *sources,
            "-o",
            str(executable),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    result = subprocess.run([str(executable)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    if test_name == "delayed_tx_retry":
        gcov = shutil.which("gcov")
        assert gcov, "gcov is required for retry-policy coverage"
        result = subprocess.run(
            [
                gcov,
                "-b",
                "-o",
                str(tmp_path / "delayed_tx_retry-test_delayed_tx_retry.gcno"),
                str(ROOT / "tests/cpp/test_delayed_tx_retry.cpp"),
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        report = re.search(
            rf"File '{re.escape(str(COMPONENT / 'delayed_tx_retry.h'))}'\nLines executed:([0-9.]+)%",
            result.stdout,
        )
        assert report and float(report[1]) == 100.0, result.stdout


def test_retry_runtime_wiring_keeps_rx_and_ha_authority() -> None:
    component = (COMPONENT / "proflame2_tembed.cpp").read_text()
    enqueue = component.split("bool Proflame2TEmbedComponent::enqueue_tx_(", 1)[1].split(
        "bool Proflame2TEmbedComponent::enqueue_test_pattern_", 1
    )[0]
    assert enqueue.index("command.retry_generation =") < enqueue.index("xQueueSend(")
    assert enqueue.index("xQueueSend(") < enqueue.index("retry.accepted(command)")
    assert (
        "retry.accepted(command)"
        not in component.split("process_delayed_retransmission_() {", 1)[1].split(
            "void Proflame2TEmbedComponent::log_rf_path_for_tx_", 1
        )[0]
    )
    loop = component.split("void Proflame2TEmbedComponent::loop()", 1)[1].split("void Proflame2TEmbedComponent::", 1)[0]
    assert loop.index("poll_rmt_pulse_capture_()") < loop.index("process_delayed_retransmission_()")
    completion = component.split("RadioRuntimeEvent result;", 1)[1]
    retry = completion.split("if (result.is_retransmission)", 1)[1].split("retry.complete(", 1)[0]
    assert "return;" in retry
    assert "apply_pending_display_intent_" not in retry
    assert "last_tx_result_ =" not in retry
    assert "tx_success_count_++" not in retry
    for reason in (
        "accepted_native_packet",
        "rmt_activity_pending_ha_validation",
        "ha_state_changed",
        "listener_disabled",
        "listener_profile_changed",
        "ha_disconnected",
        "test_pattern_queued",
    ):
        assert f'cancel_delayed_retransmission_("{reason}")' in component
    worker = component.split("static void radio_runtime_task_entry_", 1)[1].split(
        "void Proflame2TEmbedComponent::set_firmware_version", 1
    )[0]
    assert worker.index("event.finished_ms = millis()") < worker.index("event.marcstate_after_tx =")
    package = (ROOT / "esphome/packages/proflame2_tembed_base.yaml").read_text()
    switch = package.split("id: proflame2_delayed_retransmission", 1)[1].split("  - platform:", 1)[0]
    assert "restore_mode: ALWAYS_OFF" in switch
    assert 'proflame2_radio_ready_timeout_us: "10000"' in package


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


def test_native_tx_settling_legacy_and_extended_real_scheduler(tmp_path: Path) -> None:
    from custom_components.proflame2.protocol.packet import ProflameFrame
    from custom_components.proflame2.rf.waveform import frame_to_air_bytes

    executable = tmp_path / "native_tx_settling"
    compiler = shutil.which("g++")
    assert compiler
    result = subprocess.run(
        [
            compiler,
            "-std=c++17",
            "-Wall",
            "-Wextra",
            "-DPROFLAME2_RADIO_TRACE=1",
            f"-I{ROOT / 'tests/cpp/radio_trace_stubs'}",
            f"-I{COMPONENT}",
            str(COMPONENT / "radio_cc1101.cpp"),
            str(COMPONENT / "radio_cc1101_tx.cpp"),
            str(COMPONENT / "tx_payload_layout.cpp"),
            str(ROOT / "tests/cpp/test_native_tx_settling.cpp"),
            "-o",
            str(executable),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    fixture = json.loads((ROOT / "tests/fixtures/issue_26_mendota_extended_capture.json").read_text())
    words = fixture["captures"][0]["words"]
    extended = ProflameFrame(
        serial_id=(words[0] << 16) | (words[1] << 8) | words[2],
        cmd1=words[3],
        cmd2=words[4],
        err1=words[5],
        err2=words[6],
        extension_words=tuple(words[7:]),
    )
    legacy = ProflameFrame(serial_id=0x3B3F02, cmd1=0x61, cmd2=1, err1=0xD0, err2=0x39)
    for frame, bits in ((legacy, 182), (extended, 260)):
        result = subprocess.run(
            [str(executable), frame_to_air_bytes(frame).hex(), str(bits)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr


def test_runtime_controls_defaults_and_queued_delay_are_explicit() -> None:
    header = (COMPONENT / "radio_cc1101.h").read_text()
    assert "std::atomic<bool> radio_trace_enabled_{false}" in header
    assert "std::atomic<uint32_t> pre_tx_settle_delay_us_{20000}" in header
    component = (COMPONENT / "proflame2_tembed.cpp").read_text()
    assert "command.pre_tx_settle_delay_us = this->get_pre_tx_settle_delay_us();" in component
    assert "event.pre_tx_settle_delay_us = command.pre_tx_settle_delay_us;" in component
    assert "radio_error, command.pre_tx_settle_delay_us)" in component
    tx = (COMPONENT / "radio_cc1101_tx.cpp").read_text()
    call = tx.index("target_first_rising_edge_us = radio_trace_pre_tx_deadline(")
    wait = tx.index("wait_until_(target_first_rising_edge_us);", call)
    edge = tx.index("this->async_tx_pin_()->digital_write(true);", wait)
    assert call < wait < edge
    assert "if (transmission_index == 0 && pre_tx_settle_delay_us > 0)" in tx
    package = (ROOT / "esphome/packages/proflame2_tembed_base.yaml").read_text()
    delay = package.split("id: proflame2_pre_tx_settle_delay", 1)[1].split("  - platform:", 1)[0]
    assert "max_value: 100" in delay and "step: 0.1" in delay and "unit_of_measurement: ms" in delay
    toggle = package.split("id: proflame2_radio_trace_logging", 1)[1].split("  - platform:", 1)[0]
    assert "restore_mode: ALWAYS_OFF" in toggle
    assert "set_radio_trace_enabled(false)" in toggle
    assert "set_radio_trace_enabled(true)" in toggle
