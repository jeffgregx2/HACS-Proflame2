from __future__ import annotations

import json
import time
from pathlib import Path

from tools.proflame_capture.collectors import StubLilyGoCollector, StubRtl433Collector
from tools.proflame_capture.models import CaptureSessionConfig
from tools.proflame_capture.runner import CaptureSessionRunner
from tools.proflame_capture.yardstick_collector import (
    InjectedYardStickDiagnosticSource,
    LiveYardStickDiagnosticSource,
    YardStickDiagnosticCollector,
    YardStickDiagnosticResult,
)


def _session(tmp_path: Path):
    return type("Session", (), {"session_id": "s", "session_dir": tmp_path, "config": None, "started_at_utc": "now"})()


def _sample(tmp_path: Path):
    return type("Sample", (), {"sample_dir": tmp_path, "identity": None, "state_before": None})()


def test_injected_raw_payload_completes_valid(tmp_path: Path) -> None:
    source = InjectedYardStickDiagnosticSource(
        [YardStickDiagnosticResult(raw_payload_hex="aabbccdd", payload_length_bytes=4)]
    )
    collector = YardStickDiagnosticCollector(mode="injected", source=source)
    collector.start_session(_session(tmp_path))
    collector.start_sample(_sample(tmp_path))

    collector.poll()
    result = collector.finalize_sample(_sample(tmp_path))

    assert result.valid is True
    assert result.metadata["raw_payload_hex"] == "aabbccdd"


def test_injected_symbol_stream_completes_valid(tmp_path: Path) -> None:
    source = InjectedYardStickDiagnosticSource([YardStickDiagnosticResult(symbol_stream="SLLSSLS", candidate_count=1)])
    collector = YardStickDiagnosticCollector(mode="injected", source=source)
    collector.start_session(_session(tmp_path))
    collector.start_sample(_sample(tmp_path))

    collector.poll()
    result = collector.finalize_sample(_sample(tmp_path))

    assert result.valid is True
    assert result.metadata["symbol_stream"] == "SLLSSLS"


def test_decode_failure_with_useful_payload_is_still_valid(tmp_path: Path) -> None:
    source = InjectedYardStickDiagnosticSource(
        [
            YardStickDiagnosticResult(
                raw_payload_hex="01021676ef",
                decode_success=False,
                decode_failure_reason="bad_crc",
                best_failure_reason="bad_crc",
            )
        ]
    )
    collector = YardStickDiagnosticCollector(mode="injected", source=source)
    collector.start_session(_session(tmp_path))
    collector.start_sample(_sample(tmp_path))

    collector.poll()
    result = collector.finalize_sample(_sample(tmp_path))

    assert result.valid is True
    assert result.metadata["decode_success"] is False


def test_no_diagnostic_is_invalid(tmp_path: Path) -> None:
    collector = YardStickDiagnosticCollector(mode="injected", source=InjectedYardStickDiagnosticSource())
    collector.start_session(_session(tmp_path))
    collector.start_sample(_sample(tmp_path))
    result = collector.finalize_sample(_sample(tmp_path))

    assert result.valid is False
    assert result.reject_reason == "timeout"


def test_malformed_or_no_useful_artifact_is_invalid(tmp_path: Path) -> None:
    malformed = YardStickDiagnosticResult(raw_payload_hex="zz-not-hex")
    collector = YardStickDiagnosticCollector(
        mode="injected",
        source=InjectedYardStickDiagnosticSource([malformed]),
    )
    collector.start_session(_session(tmp_path))
    collector.start_sample(_sample(tmp_path))
    collector.poll()
    result = collector.finalize_sample(_sample(tmp_path))

    assert result.valid is False
    assert result.reject_reason == "malformed_diagnostic"


def test_artifacts_are_written(tmp_path: Path) -> None:
    diagnostic = YardStickDiagnosticResult(
        raw_payload_hex="aabb",
        bit_stream="10101010",
        symbol_stream="SLSL",
        decoded_fields={"id": "3b3f02", "cmd1": "01"},
    )
    collector = YardStickDiagnosticCollector(
        mode="injected",
        source=InjectedYardStickDiagnosticSource([diagnostic]),
    )
    collector.start_session(_session(tmp_path))
    sample = _sample(tmp_path)
    collector.start_sample(sample)
    collector.poll()
    collector.finalize_sample(sample)

    assert (tmp_path / "yardstick" / "diagnostic.json").is_file()
    assert (tmp_path / "yardstick" / "raw_payload.hex").is_file()
    assert (tmp_path / "yardstick" / "bit_stream.txt").is_file()
    assert (tmp_path / "yardstick" / "symbol_stream.txt").is_file()
    assert (tmp_path / "yardstick" / "decoded.json").is_file()


def test_provenance_and_candidate_windows_are_serialized(tmp_path: Path) -> None:
    diagnostic = YardStickDiagnosticResult(
        artifact_layer="rfrecv_fixed_length_payload",
        symbol_stream_layer="full_payload_tolerant_symbols_bit_offset_0",
        bit_stream_layer="full_payload_raw_bits",
        packet_normalized=False,
        candidate_search_performed=True,
        candidate_windows_retained=True,
        selected_window_available=False,
        diagnostic_limitations=["whole stream is not packet normalized"],
        failed_candidate_windows=[
            {
                "candidate_index": 0,
                "symbol_offset": 12,
                "bit_offset": 0,
                "symbol_length": 91,
                "bit_length": 182,
                "symbol_stream": "S" * 91,
                "bit_stream": "10" * 91,
                "decode_attempted": True,
                "decode_success": False,
                "failure_reason": "invalid_manchester_symbols",
            }
        ],
        diagnostic_candidate_windows=[
            {
                "candidate_index": 0,
                "symbol_offset": 13,
                "bit_offset": 0,
                "symbol_length": 91,
                "bit_length": 182,
                "symbol_stream": "Z" * 91,
                "bit_stream": "01" * 91,
                "decode_attempted": False,
                "decode_success": False,
                "failure_reason": "diagnostic_only_not_decoded",
            }
        ],
        diagnostic_candidate_offsets=[13],
        diagnostic_candidate_reason="guard_pattern_heuristic_windows",
        diagnostic_candidate_confidence="low",
    )
    collector = YardStickDiagnosticCollector(
        mode="injected",
        source=InjectedYardStickDiagnosticSource([diagnostic]),
    )
    collector.start_session(_session(tmp_path))
    sample = _sample(tmp_path)
    collector.start_sample(sample)
    collector.poll()
    result = collector.finalize_sample(sample)

    payload = json.loads((tmp_path / "yardstick" / "diagnostic.json").read_text(encoding="utf-8"))
    candidates = json.loads((tmp_path / "yardstick" / "candidate_windows.json").read_text(encoding="utf-8"))

    assert result.valid is True
    assert payload["packet_normalized"] is False
    assert payload["candidate_windows_retained"] is True
    assert candidates["failed_candidate_windows"][0]["failure_reason"] == "invalid_manchester_symbols"
    assert candidates["diagnostic_candidate_offsets"] == [13]


def test_runner_can_use_injected_yardstick_collector(tmp_path: Path) -> None:
    diagnostic = YardStickDiagnosticResult(
        raw_payload_hex="aabbcc",
        bit_stream="1010",
        symbol_stream="SLSL",
    )
    collector = YardStickDiagnosticCollector(
        mode="injected",
        source=InjectedYardStickDiagnosticSource([diagnostic]),
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
        collectors=[StubLilyGoCollector(), collector, StubRtl433Collector()],
    )
    summary = runner.run()
    session_dir = Path(summary["session_dir"])
    session_manifest = json.loads((session_dir / "session_manifest.json").read_text(encoding="utf-8"))
    sample_id = session_manifest["sample_ids"][0]
    sample_manifest = json.loads((session_dir / sample_id / "sample_manifest.json").read_text(encoding="utf-8"))

    assert summary["valid_samples_collected"] == 1
    assert sample_manifest["collector_results"]["yardstick"]["valid"] is True


class _FakeBackendResult:
    def __init__(self) -> None:
        self.capture_complete = True
        self.sample = None
        self.raw_payload_hex = "aabbccdd"
        self.payload_length_bytes = 4
        self.bit_stream = "10101010"
        self.symbol_stream = "S1010"
        self.decoded_fields = {"id": "3b3f02", "cmd1": "01", "cmd2": "41", "err1": "76", "err2": "fd"}
        self.decode_success = True
        self.decode_failure_reason = None
        self.best_failure_reason = None
        self.reason_counts = {}
        self.selected_bit_offset = 2
        self.selected_symbol_offset = 1
        self.repeat_count = 1
        self.occurrence_offsets = ((2, 1),)
        self.candidate_count = 1
        self.active_frequency_hz = 315000000
        self.rx_settings = {"worker_mode": True}
        self.host_start_ns = 1
        self.host_complete_ns = 2
        self.semantic_artifact = {
            "artifact_type": "yardstick_learning_semantic_candidate",
            "artifact_class": "semantic",
            "semantic_comparable": True,
            "decode_success": True,
            "acceptance_policy": "learning_equivalent_success",
            "id": "3b3f02",
            "cmd1": "01",
            "cmd2": "41",
            "err1": "76",
            "err2": "fd",
            "candidate_symbol_stream": "S0101",
            "candidate_bit_stream": "101010",
            "candidate_symbol_offset": 1,
            "candidate_bit_offset": 2,
            "candidate_symbol_length": 5,
            "candidate_bit_length": 6,
        }
        self.semantic_comparable = True
        self.artifact_class = "semantic"
        self.learning_attempt_count = 1
        self.failed_attempt_count_before_success = 0
        self.failed_attempts = ()


class _FakeBackend:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self._result = result or _FakeBackendResult()
        self._error = error
        self.closed = False

    async def connect(self) -> None:
        return None

    async def receive_diagnostics(self, timeout: float | None = None):
        if self._error is not None:
            raise self._error
        return self._result

    async def receive_learning_diagnostics(self, timeout: float | None = None):
        if self._error is not None:
            raise self._error
        return self._result

    async def close(self, *, reason: str | None = None) -> None:
        self.closed = True

    def serialize_worker_diagnostics(self) -> dict[str, object]:
        return {"worker_alive": False}


def test_live_mode_converts_backend_result(tmp_path: Path) -> None:
    source = LiveYardStickDiagnosticSource(backend_factory=lambda: _FakeBackend())
    collector = YardStickDiagnosticCollector(mode="live", source=source)
    try:
        collector.start_session(_session(tmp_path))
        collector.start_sample(_sample(tmp_path))
        for _ in range(100):
            collector.poll()
            if collector.is_complete():
                break
            time.sleep(0.01)
        result = collector.finalize_sample(_sample(tmp_path))
    finally:
        collector.close()

    assert result.valid is True
    assert result.metadata["raw_payload_hex"] == "aabbccdd"
    assert result.metadata["decode_success"] is True
    assert result.metadata["semantic_artifact"]["artifact_class"] == "semantic"
    assert result.metadata["worker_diagnostics"]["worker_alive"] is False


def test_live_mode_requires_semantic_artifact(tmp_path: Path) -> None:
    backend_result = _FakeBackendResult()
    backend_result.semantic_artifact = None
    backend_result.semantic_comparable = False
    backend_result.artifact_class = "debug_failure"
    backend_result.decode_success = False
    backend_result.decode_failure_reason = "invalid_manchester_symbols"
    source = LiveYardStickDiagnosticSource(backend_factory=lambda: _FakeBackend(result=backend_result))
    collector = YardStickDiagnosticCollector(mode="live", source=source)
    try:
        collector.start_session(_session(tmp_path))
        collector.start_sample(_sample(tmp_path))
        for _ in range(100):
            collector.poll()
            if collector.is_complete():
                break
            time.sleep(0.01)
        result = collector.finalize_sample(_sample(tmp_path))
    finally:
        collector.close()

    assert result.valid is False
    assert result.reject_reason == "no_semantic_artifact"


def test_live_mode_failure_maps_reject_reason(tmp_path: Path) -> None:
    source = LiveYardStickDiagnosticSource(
        backend_factory=lambda: _FakeBackend(
            error=RuntimeError("The YARD Stick One could not be opened because access was denied.")
        )
    )
    collector = YardStickDiagnosticCollector(mode="live", source=source)
    try:
        collector.start_session(_session(tmp_path))
        collector.start_sample(_sample(tmp_path))
        for _ in range(100):
            collector.poll()
            if collector.is_complete():
                break
            time.sleep(0.01)
        result = collector.finalize_sample(_sample(tmp_path))
    finally:
        collector.close()

    assert result.valid is False
    assert result.reject_reason == "backend_open_failed"


def test_runner_can_use_live_yardstick_collector_with_fake_backend(tmp_path: Path) -> None:
    source = LiveYardStickDiagnosticSource(backend_factory=lambda: _FakeBackend())
    collector = YardStickDiagnosticCollector(mode="live", source=source)
    config = CaptureSessionConfig(
        output_root=tmp_path,
        valid_samples_target=1,
        max_attempts=1,
        non_interactive=True,
        sample_timeout_seconds=0.1,
        poll_interval_seconds=0.0,
    )
    runner = CaptureSessionRunner(
        config=config,
        collectors=[StubLilyGoCollector(), collector, StubRtl433Collector()],
    )
    try:
        summary = runner.run()
        assert summary["valid_samples_collected"] == 1
    finally:
        collector.close()
