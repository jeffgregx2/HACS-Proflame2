"""Compile and execute the real register-trace implementation with a mock SPI bus."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "esphome/components/proflame2_tembed"


def test_wifi_diagnostics_and_api_summary(tmp_path: Path) -> None:
    component = (COMPONENT / "proflame2_tembed.cpp").read_text()
    method = component.split("void Proflame2TEmbedComponent::set_wifi_connected(", 1)[1].split(
        "void Proflame2TEmbedComponent::set_api_connected", 1
    )[0]
    package = (ROOT / "esphome/packages/proflame2_tembed_base.yaml").read_text()
    script = yaml.safe_load("script:" + package.split("\nscript:", 1)[1].split("\nspi:", 1)[0])["script"][0]
    assert script["id"] == "proflame2_log_connection_summary"
    assert script["mode"] == "restart"
    assert script["then"][0] == {"delay": "1s"}
    updates = [step["component.update"] for step in script["then"] if "component.update" in step]
    assert set(updates) == {
        "proflame2_uptime",
        "proflame2_wifi_rssi",
        "proflame2_wifi_disconnect_count",
        "proflame2_wifi_reconnect_count",
        "proflame2_wifi_last_outage",
    }
    summary = script["then"][-1]["lambda"]
    assert summary.count("ESP_LOGI(") == 1
    api_connect = package.split("  on_client_connected:", 1)[1].split("  on_client_disconnected:", 1)[0]
    assert "script.execute: proflame2_log_connection_summary" in api_connect
    sensors = yaml.safe_load("sensor:" + package.split("\nsensor:", 1)[1].split("\nnumber:", 1)[0])["sensor"]
    assert len(sensors) == 5
    assert all(sensor["entity_category"] == "diagnostic" for sensor in sensors)
    assert all(sensor["update_interval"] == "10s" for sensor in sensors)
    wifi_connect = package.split("\nwifi:\n", 1)[1].split("  on_disconnect:", 1)[0]
    assert "script.execute: proflame2_log_connection_summary" in wifi_connect
    text = yaml.safe_load("text_sensor:" + package.split("\ntext_sensor:", 1)[1].split("\nscript:", 1)[0])[
        "text_sensor"
    ]
    assert text[0]["bssid"]["entity_category"] == "diagnostic"
    assert text[0]["ssid"]["entity_category"] == "diagnostic"
    assert text[1]["reset_reason"]["entity_category"] == "diagnostic"
    harness = (ROOT / "tests/cpp/test_wifi_diagnostics.cpp").read_text()
    wifi = yaml.safe_load("wifi:" + package.split("\nwifi:", 1)[1].split("\ndebug:", 1)[0])["wifi"]
    disconnect = wifi["on_disconnect"]["then"][0]["lambda"]
    assert disconnect.count("ESP_LOGI(") == 1
    source = tmp_path / "wifi_diagnostics.cpp"
    source.write_text(
        harness.replace(
            "// INSERT_PRODUCTION_WIFI_METHOD", "void Proflame2TEmbedComponent::set_wifi_connected(" + method
        )
        .replace("// INSERT_PRODUCTION_SUMMARY", summary)
        .replace("// INSERT_PRODUCTION_DISCONNECT", disconnect)
    )
    compiler = shutil.which("g++")
    assert compiler
    executable = tmp_path / "wifi_diagnostics"
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
            str(source),
            "-o",
            str(executable),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    result = subprocess.run([str(executable)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    gcov = shutil.which("gcov")
    assert gcov
    result = subprocess.run(
        [gcov, "-b", str(tmp_path / "wifi_diagnostics.gcno")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    report = re.search(
        rf"File '{re.escape(str(COMPONENT / 'wifi_diagnostics.h'))}'\nLines executed:([0-9.]+)%",
        result.stdout,
    )
    assert report and float(report[1]) == 100.0, result.stdout


@pytest.mark.parametrize("local_completion", [False, True], ids=["runtime-storage", "old-stack-negative-control"])
def test_actual_retry_tx_queue_and_completion_arbitration(tmp_path: Path, local_completion: bool) -> None:
    """Execute production TX admission/completion using deterministic RTOS queues."""
    component = (COMPONENT / "proflame2_tembed.cpp").read_text()

    def section(start: str, end: str) -> str:
        begin = component.index(start)
        return component[begin : component.index(end, begin)]

    types = section("template <size_t N> static void copy_string_", "static void radio_runtime_task_entry_")
    methods = "\n".join(
        [
            section(
                "void Proflame2TEmbedComponent::refresh_status_text_()",
                "void Proflame2TEmbedComponent::publish_telemetry_()",
            ),
            section(
                "std::string Proflame2TEmbedComponent::get_display_mode_badge_text() const",
                "std::string Proflame2TEmbedComponent::get_display_field_text_",
            ),
            section("bool Proflame2TEmbedComponent::tx(", "bool Proflame2TEmbedComponent::cc1101_test_pattern"),
            section(
                "bool Proflame2TEmbedComponent::enqueue_tx_(", "bool Proflame2TEmbedComponent::enqueue_test_pattern_"
            ),
            section(
                "void Proflame2TEmbedComponent::cancel_delayed_retransmission_(",
                "void Proflame2TEmbedComponent::log_rf_path_for_tx_",
            ),
            section(
                "void Proflame2TEmbedComponent::consume_radio_runtime_events_()",
                "bool Proflame2TEmbedComponent::initialize_radio_",
            ),
        ]
    )
    harness = (ROOT / "tests/cpp/test_retry_tx_arbitration.cpp").read_text()
    if local_completion:
        methods = methods.replace(
            "auto& result = this->radio_runtime_->completion_buffer;", "RadioRuntimeEvent result;"
        )
    source = tmp_path / "retry_tx_arbitration.cpp"
    source.write_text(
        harness.replace("// INSERT_PRODUCTION_RUNTIME_TYPES", types).replace("// INSERT_PRODUCTION_METHODS", methods)
    )
    compiler = shutil.which("g++")
    assert compiler
    executable = tmp_path / "retry_tx_arbitration"
    result = subprocess.run(
        [
            compiler,
            "-std=c++17",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-DPROFLAME2_RADIO_TRACE=1",
            "-DPROFLAME2_TX_CLEAN_MODE=0",
            "-Os",
            "-fstack-usage",
            f"-I{ROOT / 'tests/cpp/radio_trace_stubs'}",
            f"-I{COMPONENT}",
            str(source),
            str(COMPONENT / "tx_controller.cpp"),
            str(COMPONENT / "tx_payload_layout.cpp"),
            "-o",
            str(executable),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    stack_reports = list(tmp_path.glob("*.su"))
    assert stack_reports
    completion_frames = [
        line
        for report in stack_reports
        for line in report.read_text().splitlines()
        if "Proflame2TEmbedComponent::consume_radio_runtime_events_()" in line
    ]
    assert len(completion_frames) == 1
    # Host measurements guard against reintroducing the 4 KiB local event.
    # They do not measure Xtensa driver/ISR or complete task-stack depth.
    frame_bytes = int(completion_frames[0].split("\t")[1])
    if local_completion:
        assert frame_bytes > 4096, completion_frames
    else:
        assert frame_bytes <= 1024, completion_frames
    for (payload, bits), (new_payload, _) in zip(
        _native_tx_payloads(), _native_tx_payloads(alternate=True), strict=True
    ):
        result = subprocess.run(
            [str(executable), payload.hex(), str(bits), new_payload.hex()], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr


def test_retry_survives_actual_listener_resume_method(tmp_path: Path) -> None:
    """Execute the production listener entrypoint with mocked radio side effects."""
    component = (COMPONENT / "proflame2_tembed.cpp").read_text()
    start = component.index("void Proflame2TEmbedComponent::configure_active_listener(")
    end = component.index("void Proflame2TEmbedComponent::set_fifo_capture_enabled(", start)
    harness = (ROOT / "tests/cpp/test_retry_listener_lifecycle.cpp").read_text()
    source = tmp_path / "retry_listener_lifecycle.cpp"
    source.write_text(harness.replace("// INSERT_PRODUCTION_LISTENER_DEFINITION", component[start:end]))
    compiler = shutil.which("g++")
    assert compiler
    executable = tmp_path / "retry_listener_lifecycle"
    result = subprocess.run(
        [
            compiler,
            "-std=c++17",
            "-Wall",
            "-Wextra",
            "-Werror",
            f"-I{ROOT / 'tests/cpp/radio_trace_stubs'}",
            f"-I{COMPONENT}",
            str(source),
            "-o",
            str(executable),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    result = subprocess.run([str(executable)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


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
    completion = component.split("void Proflame2TEmbedComponent::consume_radio_runtime_events_()", 1)[1]
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
    assert "restore_mode: ALWAYS_ON" in switch
    assert "set_delayed_retransmission_enabled(true)" in switch
    assert "set_delayed_retransmission_enabled(false)" in switch
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
    completion = component.split("void Proflame2TEmbedComponent::consume_radio_runtime_events_()", 1)[1]
    restore = completion.index("this->restore_rx_after_tx_if_needed_();")
    before_output = completion.index('log_radio_registers(result.trace_before_tx, "tx_prepared", result.request_id)')
    after_output = completion.index('log_radio_registers(result.trace_after_tx, "tx_complete", result.request_id)')
    failure = completion.index("if (!result.ok)")
    assert restore < before_output < after_output < failure


@pytest.mark.parametrize("trace_enabled", [0, 1])
def test_native_tx_settling_legacy_and_extended_real_scheduler(tmp_path: Path, trace_enabled: int) -> None:
    executable = tmp_path / "native_tx_settling"
    compiler = shutil.which("g++")
    assert compiler
    result = subprocess.run(
        [
            compiler,
            "-std=c++17",
            "-Wall",
            "-Wextra",
            f"-DPROFLAME2_RADIO_TRACE={trace_enabled}",
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
    for payload, bits in _native_tx_payloads():
        result = subprocess.run(
            [str(executable), payload.hex(), str(bits)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr


def _native_tx_payloads(*, alternate: bool = False) -> list[tuple[bytes, int]]:
    from custom_components.proflame2.protocol.packet import ProflameFrame
    from custom_components.proflame2.rf.waveform import frame_to_air_bytes

    fixture = json.loads((ROOT / "tests/fixtures/issue_26_mendota_extended_capture.json").read_text())
    words = fixture["captures"][2 if alternate else 0]["words"]
    extended = ProflameFrame(
        serial_id=(words[0] << 16) | (words[1] << 8) | words[2],
        cmd1=words[3],
        cmd2=words[4],
        err1=words[5],
        err2=words[6],
        extension_words=tuple(words[7:]),
    )
    legacy = ProflameFrame(
        serial_id=0x3B3F02, cmd1=0x01 if alternate else 0x61, cmd2=1, err1=0x76 if alternate else 0xD0, err2=0x39
    )
    return [(frame_to_air_bytes(legacy), 182), (frame_to_air_bytes(extended), 260)]


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
    assert "name: Pre-TX Settling Delay" not in package
    assert 'proflame2_pre_tx_settle_delay_us: "20000"' in package
    assert "pre_tx_settle_delay_us: ${proflame2_pre_tx_settle_delay_us}" in package
    schema = (COMPONENT / "__init__.py").read_text()
    assert "cv.Optional(CONF_PRE_TX_SETTLE_DELAY_US, default=20000)" in schema
    assert "var.set_pre_tx_settle_delay_us(config[CONF_PRE_TX_SETTLE_DELAY_US])" in schema
    toggle = package.split("id: proflame2_radio_trace_logging", 1)[1].split("  - platform:", 1)[0]
    assert "restore_mode: ALWAYS_OFF" in toggle
    assert "set_radio_trace_enabled(false)" in toggle
    assert "set_radio_trace_enabled(true)" in toggle
    assert "entity_category: diagnostic" in toggle


def test_completion_storage_is_separate_from_worker_and_not_on_stack() -> None:
    component = (COMPONENT / "proflame2_tembed.cpp").read_text()
    assert "RadioRuntimeEvent event_buffer{};" in component
    assert "RadioRuntimeEvent completion_buffer{};" in component
    completion = component.split("void Proflame2TEmbedComponent::consume_radio_runtime_events_()", 1)[1]
    assert "auto& result = this->radio_runtime_->completion_buffer;" in completion
    assert "RadioRuntimeEvent result;" not in completion
