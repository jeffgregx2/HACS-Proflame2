from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from custom_components.proflame2.protocol.packet import ProflameFrame
from custom_components.proflame2.rf.capture import frame_to_air_bytes
from tools.proflame_capture.collectors import StubLilyGoCollector, StubYardStickCollector
from tools.proflame_capture.lilygo_syslog import InjectedSyslogReceiver, LilyGoSyslogCollector
from tools.proflame_capture.models import (
    CaptureCommand,
    CaptureSessionConfig,
    CollectorArtifact,
    CollectorResult,
    FireplaceState,
    SampleContext,
    SampleIdentity,
)
from tools.proflame_capture.quick_validation import build_quick_validation
from tools.proflame_capture.rtl433_collector import InjectedRtl433Source, Rtl433Collector
from tools.proflame_capture.runner import CaptureSessionRunner
from tools.proflame_capture.yardstick_collector import (
    InjectedYardStickDiagnosticSource,
    YardStickDiagnosticCollector,
    YardStickDiagnosticResult,
)

RTL433_BLOCK = [
    "time      : 2026-05-07 04:52:22",
    "model     : Proflame2-Remote                       Id        : 3b3f02",
    "Cmd1      : 01           Cmd2      : 16            Err1      : 76            Err2      : ef",
    "Pilot     : 0            Light     : 0             Thermostat: 0             Power     : 1",
    "Front     : 0            Fan       : 1             Aux       : 0             Flame     : 6",
    "Integrity : CHECKSUM",
    "",
]
FIFO_SEMANTIC_PAYLOAD_HEX = (
    frame_to_air_bytes(ProflameFrame(serial_id=0x3B3F02, cmd1=0x01, err1=0x76, cmd2=0x16, err2=0xEF))
).hex()
FIFO_SEMANTIC_LINES = [
    "RX fifo probe begin schema=2 probe_id=4 artifact_class=experimental_fifo_probe "
    "source=cc1101_rx_fifo capture_mode=rolling_fifo_trailing_window profile=rfcat_fixed_none_rfcat_wide",
    f"RX fifo probe chunk schema=2 probe_id=4 chunk=0 offset=0 count={len(bytes.fromhex(FIFO_SEMANTIC_PAYLOAD_HEX))} "
    f"hex={FIFO_SEMANTIC_PAYLOAD_HEX}",
    "RX fifo probe end schema=2 probe_id=4 ok=YES failure_reason=none "
    f"byte_count={len(bytes.fromhex(FIFO_SEMANTIC_PAYLOAD_HEX))} buffer_full=NO rx_fifo_overflow=NO",
]


def _sample_context(
    tmp_path: Path,
    *,
    action: CaptureCommand = CaptureCommand.FLAME_UP,
    state_before: FireplaceState | None = None,
) -> SampleContext:
    sample_dir = tmp_path / "sample"
    sample_dir.mkdir(parents=True, exist_ok=True)
    return SampleContext(
        identity=SampleIdentity(
            session_id="session",
            sample_id="sample-001",
            sample_index=1,
            attempt_index=1,
            requested_action=action,
            operator_prompt="prompt",
            coordinator_started_at_utc="2026-05-11T00:00:00+00:00",
        ),
        sample_dir=sample_dir,
        state_before=state_before or FireplaceState(),
    )


def _touch_artifact(sample_dir: Path, relative_path: str) -> CollectorArtifact:
    path = sample_dir / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("artifact\n", encoding="utf-8")
    kind = {
        "lilygo/raw_syslog.log": "syslog_log",
        "lilygo/capture_export.json": "capture_export",
        "lilygo/semantic_fifo_artifact.json": "semantic_fifo_artifact",
        "rtl433/raw_stdout.log": "rtl433_stdout",
        "rtl433/decoded.json": "rtl433_decoded",
        "rtl433/parser_debug.json": "rtl433_parser_debug",
        "yardstick/diagnostic.json": "yardstick_diagnostic",
        "yardstick/raw_payload.hex": "yardstick_raw_payload",
        "yardstick/bit_stream.txt": "yardstick_bit_stream",
        "yardstick/symbol_stream.txt": "yardstick_symbol_stream",
        "yardstick/decoded.json": "yardstick_decoded",
    }[relative_path]
    return CollectorArtifact(path=relative_path, kind=kind)


def test_lilygo_semantic_fifo_artifact_can_validate_without_edge_export(tmp_path: Path) -> None:
    sample_context = _sample_context(tmp_path)
    semantic_artifact = {
        "artifact_class": "semantic_fifo_candidate",
        "semantic_comparable": True,
        "decode_success": True,
        "decoded_fields": {
            "remote_id": "3b3f02",
            "cmd1": "01",
            "cmd2": "16",
            "err1": "76",
            "err2": "ef",
        },
    }
    lilygo_result = CollectorResult(
        source_name="lilygo",
        complete=True,
        mode="syslog",
        valid=True,
        artifact_paths=(
            _touch_artifact(sample_context.sample_dir, "lilygo/raw_syslog.log"),
            _touch_artifact(sample_context.sample_dir, "lilygo/capture_export.json"),
            _touch_artifact(sample_context.sample_dir, "lilygo/semantic_fifo_artifact.json"),
        ),
        artifact_dir="lilygo",
        metadata={
            "semantic_fifo_artifact": semantic_artifact,
        },
    )
    rtl433_result = CollectorResult(
        source_name="rtl433",
        complete=True,
        mode="subprocess",
        valid=True,
        artifact_paths=(
            _touch_artifact(sample_context.sample_dir, "rtl433/raw_stdout.log"),
            _touch_artifact(sample_context.sample_dir, "rtl433/decoded.json"),
            _touch_artifact(sample_context.sample_dir, "rtl433/parser_debug.json"),
        ),
        artifact_dir="rtl433",
        metadata={
            "model": "Proflame2-Remote",
            "id": "3b3f02",
            "cmd1": "01",
            "cmd2": "16",
            "err1": "76",
            "err2": "ef",
            "integrity": "CHECKSUM",
        },
    )
    yardstick_result = CollectorResult(
        source_name="yardstick",
        complete=True,
        mode="injected",
        valid=True,
        artifact_paths=(
            _touch_artifact(sample_context.sample_dir, "yardstick/diagnostic.json"),
            _touch_artifact(sample_context.sample_dir, "yardstick/raw_payload.hex"),
        ),
        artifact_dir="yardstick",
        metadata={"diagnostic_present": True, "raw_payload_hex": "aa"},
    )

    result = build_quick_validation(
        sample_context=sample_context,
        collector_results={
            "lilygo": lilygo_result,
            "rtl433": rtl433_result,
            "yardstick": yardstick_result,
        },
    )

    assert result.collection_valid is True
    assert result.collection_reject_reasons == []
    assert result.payload["pairing_summary"]["lilygo_semantic_fifo_present"] is True
    assert result.payload["semantic_summary"]["lilygo_fifo_decode_success"] is True
    assert result.payload["semantic_summary"]["lilygo_fifo_matches_rtl433"] is True


@pytest.mark.parametrize(
    "semantic_artifact",
    [
        {
            "artifact_class": "raw_fifo_window",
            "semantic_comparable": False,
            "decode_success": False,
        },
        {
            "artifact_class": "debug_failure",
            "semantic_comparable": False,
            "decode_success": False,
        },
        {
            "artifact_class": "semantic_fifo_candidate",
            "semantic_comparable": False,
            "decode_success": True,
        },
        {
            "artifact_class": "semantic_fifo_candidate",
            "semantic_comparable": True,
            "decode_success": False,
        },
    ],
)
def test_lilygo_debug_or_raw_fifo_artifacts_are_not_semantic(
    tmp_path: Path, semantic_artifact: dict[str, object]
) -> None:
    sample_context = _sample_context(tmp_path)
    lilygo_result = CollectorResult(
        source_name="lilygo",
        complete=True,
        mode="syslog",
        valid=True,
        artifact_paths=(
            _touch_artifact(sample_context.sample_dir, "lilygo/raw_syslog.log"),
            _touch_artifact(sample_context.sample_dir, "lilygo/capture_export.json"),
            _touch_artifact(sample_context.sample_dir, "lilygo/semantic_fifo_artifact.json"),
        ),
        artifact_dir="lilygo",
        metadata={
            "semantic_fifo_artifact": semantic_artifact,
        },
    )
    rtl433_result = CollectorResult(
        source_name="rtl433",
        complete=True,
        mode="subprocess",
        valid=True,
        artifact_paths=(
            _touch_artifact(sample_context.sample_dir, "rtl433/raw_stdout.log"),
            _touch_artifact(sample_context.sample_dir, "rtl433/decoded.json"),
            _touch_artifact(sample_context.sample_dir, "rtl433/parser_debug.json"),
        ),
        artifact_dir="rtl433",
        metadata={
            "model": "Proflame2-Remote",
            "id": "3b3f02",
            "cmd1": "01",
            "cmd2": "16",
            "err1": "76",
            "err2": "ef",
            "integrity": "CHECKSUM",
        },
    )
    yardstick_result = CollectorResult(
        source_name="yardstick",
        complete=True,
        mode="injected",
        valid=True,
        artifact_paths=(
            _touch_artifact(sample_context.sample_dir, "yardstick/diagnostic.json"),
            _touch_artifact(sample_context.sample_dir, "yardstick/raw_payload.hex"),
        ),
        artifact_dir="yardstick",
        metadata={"diagnostic_present": True, "raw_payload_hex": "aa"},
    )

    result = build_quick_validation(
        sample_context=sample_context,
        collector_results={
            "lilygo": lilygo_result,
            "rtl433": rtl433_result,
            "yardstick": yardstick_result,
        },
    )

    assert result.collection_valid is False
    assert "lilygo:missing_semantic_fifo_artifact" in result.collection_reject_reasons
    assert result.payload["pairing_summary"]["lilygo_semantic_fifo_present"] is False
    assert result.payload["semantic_summary"]["lilygo_fifo_decode_success"] is False


def test_lilygo_semantic_fifo_mismatch_rejects_sample(tmp_path: Path) -> None:
    sample_context = _sample_context(tmp_path)
    semantic_artifact = {
        "artifact_class": "semantic_fifo_candidate",
        "semantic_comparable": True,
        "decode_success": True,
        "decoded_fields": {
            "remote_id": "3b3f02",
            "cmd1": "01",
            "cmd2": "11",
            "err1": "76",
            "err2": "08",
        },
    }
    lilygo_result = CollectorResult(
        source_name="lilygo",
        complete=True,
        mode="syslog",
        valid=True,
        artifact_paths=(
            _touch_artifact(sample_context.sample_dir, "lilygo/raw_syslog.log"),
            _touch_artifact(sample_context.sample_dir, "lilygo/capture_export.json"),
            _touch_artifact(sample_context.sample_dir, "lilygo/semantic_fifo_artifact.json"),
        ),
        artifact_dir="lilygo",
        metadata={
            "semantic_fifo_artifact": semantic_artifact,
        },
    )
    rtl433_result = CollectorResult(
        source_name="rtl433",
        complete=True,
        mode="subprocess",
        valid=True,
        artifact_paths=(
            _touch_artifact(sample_context.sample_dir, "rtl433/raw_stdout.log"),
            _touch_artifact(sample_context.sample_dir, "rtl433/decoded.json"),
            _touch_artifact(sample_context.sample_dir, "rtl433/parser_debug.json"),
        ),
        artifact_dir="rtl433",
        metadata={
            "model": "Proflame2-Remote",
            "id": "3b3f02",
            "cmd1": "01",
            "cmd2": "01",
            "err1": "76",
            "err2": "39",
            "integrity": "CHECKSUM",
        },
    )
    yardstick_result = CollectorResult(
        source_name="yardstick",
        complete=True,
        mode="injected",
        valid=True,
        artifact_paths=(
            _touch_artifact(sample_context.sample_dir, "yardstick/diagnostic.json"),
            _touch_artifact(sample_context.sample_dir, "yardstick/raw_payload.hex"),
        ),
        artifact_dir="yardstick",
        metadata={"diagnostic_present": True, "raw_payload_hex": "aa"},
    )

    result = build_quick_validation(
        sample_context=sample_context,
        collector_results={
            "lilygo": lilygo_result,
            "rtl433": rtl433_result,
            "yardstick": yardstick_result,
        },
    )

    assert result.collection_valid is False
    assert result.collection_reject_reasons == ["lilygo:semantic_fifo_rtl433_mismatch"]
    assert result.payload["semantic_summary"]["lilygo_fifo_matches_rtl433"] is False
    assert "LilyGO FIFO semantic candidate does not match rtl_433 canonical decode." in result.payload["notes"]


def test_all_selected_sources_complete_yields_collection_valid(tmp_path: Path) -> None:
    sample_context = _sample_context(tmp_path)
    semantic_artifact = {
        "artifact_class": "semantic_fifo_candidate",
        "semantic_comparable": True,
        "decode_success": True,
        "decoded_fields": {
            "remote_id": "3b3f02",
            "cmd1": "01",
            "cmd2": "16",
            "err1": "76",
            "err2": "ef",
        },
    }
    lilygo_result = CollectorResult(
        source_name="lilygo",
        complete=True,
        mode="syslog",
        valid=True,
        artifact_paths=(
            _touch_artifact(sample_context.sample_dir, "lilygo/raw_syslog.log"),
            _touch_artifact(sample_context.sample_dir, "lilygo/capture_export.json"),
            _touch_artifact(sample_context.sample_dir, "lilygo/semantic_fifo_artifact.json"),
        ),
        artifact_dir="lilygo",
        metadata={"semantic_fifo_artifact": semantic_artifact},
    )
    rtl433_result = CollectorResult(
        source_name="rtl433",
        complete=True,
        mode="subprocess",
        valid=True,
        artifact_paths=(
            _touch_artifact(sample_context.sample_dir, "rtl433/raw_stdout.log"),
            _touch_artifact(sample_context.sample_dir, "rtl433/decoded.json"),
            _touch_artifact(sample_context.sample_dir, "rtl433/parser_debug.json"),
        ),
        artifact_dir="rtl433",
        metadata={
            "id": "3b3f02",
            "model": "Proflame2-Remote",
            "integrity": "CHECKSUM",
            "cmd1": "01",
            "cmd2": "16",
            "err1": "76",
            "err2": "ef",
            "power": 1,
            "flame": 4,
            "fan": 1,
        },
    )
    yardstick_result = CollectorResult(
        source_name="yardstick",
        complete=True,
        mode="injected",
        valid=True,
        artifact_paths=(
            _touch_artifact(sample_context.sample_dir, "yardstick/diagnostic.json"),
            _touch_artifact(sample_context.sample_dir, "yardstick/raw_payload.hex"),
        ),
        artifact_dir="yardstick",
        metadata={
            "diagnostic_present": True,
            "raw_payload_hex": "aabb",
            "decode_success": False,
        },
    )

    result = build_quick_validation(
        sample_context=sample_context,
        collector_results={
            "lilygo": lilygo_result,
            "rtl433": rtl433_result,
            "yardstick": yardstick_result,
        },
    )

    assert result.collection_valid is True
    assert result.payload["pairing_summary"]["pairing_confidence"] == "medium_missing_optional_timing"


def test_missing_lilygo_export_is_invalid(tmp_path: Path) -> None:
    sample_context = _sample_context(tmp_path)
    result = build_quick_validation(
        sample_context=sample_context,
        collector_results={
            "lilygo": CollectorResult(
                source_name="lilygo",
                complete=True,
                mode="syslog",
                valid=False,
                reject_reason="missing_semantic_fifo_artifact",
                artifact_paths=(
                    _touch_artifact(sample_context.sample_dir, "lilygo/raw_syslog.log"),
                    _touch_artifact(sample_context.sample_dir, "lilygo/capture_export.json"),
                ),
                artifact_dir="lilygo",
                metadata={
                    "semantic_fifo_artifact": None,
                },
            ),
        },
    )
    assert result.collection_valid is False
    assert "lilygo:missing_semantic_fifo_artifact" in result.collection_reject_reasons


def test_missing_rtl433_decode_is_invalid_when_selected(tmp_path: Path) -> None:
    sample_context = _sample_context(tmp_path)
    result = build_quick_validation(
        sample_context=sample_context,
        collector_results={
            "rtl433": CollectorResult(
                source_name="rtl433",
                complete=False,
                mode="subprocess",
                valid=False,
                reject_reason="no_decode",
                artifact_paths=(
                    _touch_artifact(sample_context.sample_dir, "rtl433/raw_stdout.log"),
                    _touch_artifact(sample_context.sample_dir, "rtl433/decoded.json"),
                    _touch_artifact(sample_context.sample_dir, "rtl433/parser_debug.json"),
                ),
                artifact_dir="rtl433",
                metadata={"model": None, "integrity": None, "id": None},
            ),
        },
    )
    assert result.collection_valid is False
    assert "rtl433:no_decode" in result.collection_reject_reasons


def test_yardstick_decode_failure_with_raw_payload_is_still_valid(tmp_path: Path) -> None:
    sample_context = _sample_context(tmp_path)
    result = build_quick_validation(
        sample_context=sample_context,
        collector_results={
            "yardstick": CollectorResult(
                source_name="yardstick",
                complete=True,
                mode="injected",
                valid=True,
                artifact_paths=(
                    _touch_artifact(sample_context.sample_dir, "yardstick/diagnostic.json"),
                    _touch_artifact(sample_context.sample_dir, "yardstick/raw_payload.hex"),
                ),
                artifact_dir="yardstick",
                metadata={
                    "diagnostic_present": True,
                    "raw_payload_hex": "aabb",
                    "decode_success": False,
                    "decode_failure_reason": "bad_checksum",
                },
            ),
        },
    )
    assert result.collection_valid is True


def test_missing_required_artifact_is_invalid(tmp_path: Path) -> None:
    sample_context = _sample_context(tmp_path)
    result = build_quick_validation(
        sample_context=sample_context,
        collector_results={
            "rtl433": CollectorResult(
                source_name="rtl433",
                complete=True,
                mode="subprocess",
                valid=True,
                artifact_paths=(
                    _touch_artifact(sample_context.sample_dir, "rtl433/raw_stdout.log"),
                    CollectorArtifact(path="rtl433/decoded.json", kind="rtl433_decoded"),
                    _touch_artifact(sample_context.sample_dir, "rtl433/parser_debug.json"),
                ),
                artifact_dir="rtl433",
                metadata={
                    "id": "3b3f02",
                    "model": "Proflame2-Remote",
                    "integrity": "CHECKSUM",
                },
            ),
        },
    )
    assert result.collection_valid is False
    assert "rtl433:missing_decoded_artifact" in result.collection_reject_reasons


def test_requested_action_plausibility_is_null_when_prior_state_unknown(tmp_path: Path) -> None:
    sample_context = _sample_context(tmp_path, action=CaptureCommand.FLAME_UP, state_before=FireplaceState())
    result = build_quick_validation(
        sample_context=sample_context,
        collector_results={
            "rtl433": CollectorResult(
                source_name="rtl433",
                complete=True,
                mode="subprocess",
                valid=True,
                artifact_paths=(
                    _touch_artifact(sample_context.sample_dir, "rtl433/raw_stdout.log"),
                    _touch_artifact(sample_context.sample_dir, "rtl433/decoded.json"),
                    _touch_artifact(sample_context.sample_dir, "rtl433/parser_debug.json"),
                ),
                artifact_dir="rtl433",
                metadata={"id": "3b3f02", "model": "Proflame2-Remote", "integrity": "CHECKSUM", "flame": 4},
            ),
        },
    )
    assert result.payload["semantic_summary"]["requested_action_plausible"] is None


def test_requested_action_plausibility_works_with_known_state(tmp_path: Path) -> None:
    sample_context = _sample_context(
        tmp_path,
        action=CaptureCommand.FLAME_UP,
        state_before=FireplaceState(power=1, flame=3, fan=1),
    )
    result = build_quick_validation(
        sample_context=sample_context,
        collector_results={
            "rtl433": CollectorResult(
                source_name="rtl433",
                complete=True,
                mode="subprocess",
                valid=True,
                artifact_paths=(
                    _touch_artifact(sample_context.sample_dir, "rtl433/raw_stdout.log"),
                    _touch_artifact(sample_context.sample_dir, "rtl433/decoded.json"),
                    _touch_artifact(sample_context.sample_dir, "rtl433/parser_debug.json"),
                ),
                artifact_dir="rtl433",
                metadata={
                    "id": "3b3f02",
                    "model": "Proflame2-Remote",
                    "integrity": "CHECKSUM",
                    "flame": 4,
                    "power": 1,
                    "fan": 1,
                },
            ),
        },
    )
    assert result.payload["semantic_summary"]["requested_action_plausible"] is True
    assert result.payload["semantic_summary"]["state_update_from_rtl433"]["flame"] == 4


def test_quick_validation_written_and_counts_only_when_valid(tmp_path: Path) -> None:
    receiver = InjectedSyslogReceiver()
    lilygo = LilyGoSyslogCollector(receiver=receiver, source_host_filter="192.168.1.77")

    rtl433_source = InjectedRtl433Source()
    rtl433 = Rtl433Collector(source=rtl433_source)
    rtl433.source_mode = "subprocess"

    yardstick = YardStickDiagnosticCollector(
        mode="injected",
        source=InjectedYardStickDiagnosticSource([YardStickDiagnosticResult(raw_payload_hex="aabb")]),
    )

    config = CaptureSessionConfig(
        output_root=tmp_path,
        valid_samples_target=1,
        max_attempts=1,
        non_interactive=True,
        sample_timeout_seconds=0.05,
        poll_interval_seconds=0.0,
    )
    status_lines: list[str] = []
    runner = CaptureSessionRunner(
        config=config,
        collectors=[lilygo, rtl433, yardstick],
        status_handler=status_lines.append,
    )
    summary = runner.run()
    session_dir = Path(summary["session_dir"])
    session_manifest = json.loads((session_dir / "session_manifest.json").read_text(encoding="utf-8"))
    sample_dir = session_dir / session_manifest["sample_ids"][0]
    quick_validation = json.loads((sample_dir / "analysis" / "quick_validation.json").read_text(encoding="utf-8"))

    assert summary["valid_samples_collected"] == 0
    assert quick_validation["collection_valid"] is False
    assert (sample_dir / "quick_validation.json").is_file()
    assert any("collection=INVALID" in line for line in status_lines)
    assert summary["reject_reason_counts"]


def test_runner_counts_valid_sample_when_quick_validation_passes(tmp_path: Path) -> None:
    receiver = InjectedSyslogReceiver()
    for line in FIFO_SEMANTIC_LINES:
        receiver.feed_line(line, source_host="192.168.1.77")
    lilygo = LilyGoSyslogCollector(receiver=receiver, source_host_filter="192.168.1.77")

    rtl433_source = InjectedRtl433Source()
    base = time.monotonic() + 0.25
    for index, line in enumerate(RTL433_BLOCK):
        rtl433_source.feed_line(line, host_monotonic=base + index * 0.001)
    rtl433 = Rtl433Collector(source=rtl433_source)
    rtl433.source_mode = "subprocess"

    yardstick = YardStickDiagnosticCollector(
        mode="injected",
        source=InjectedYardStickDiagnosticSource([YardStickDiagnosticResult(raw_payload_hex="aabb")]),
    )

    config = CaptureSessionConfig(
        output_root=tmp_path,
        valid_samples_target=1,
        max_attempts=1,
        non_interactive=True,
        sample_timeout_seconds=0.05,
        poll_interval_seconds=0.0,
    )
    runner = CaptureSessionRunner(
        config=config,
        collectors=[lilygo, rtl433, yardstick],
    )
    summary = runner.run()

    assert summary["valid_samples_collected"] == 1


def test_runner_reject_counts_are_reported(tmp_path: Path) -> None:
    config = CaptureSessionConfig(
        output_root=tmp_path,
        valid_samples_target=1,
        max_attempts=1,
        non_interactive=True,
        sample_timeout_seconds=0.01,
        poll_interval_seconds=0.0,
    )
    status_lines: list[str] = []
    runner = CaptureSessionRunner(
        config=config,
        collectors=[
            StubLilyGoCollector(),
            StubYardStickCollector(should_validate=False),
        ],
        status_handler=status_lines.append,
    )
    summary = runner.run()

    assert summary["valid_samples_collected"] == 0
    assert summary["reject_reason_counts"]
    assert any(line.startswith("session_complete") for line in status_lines)
