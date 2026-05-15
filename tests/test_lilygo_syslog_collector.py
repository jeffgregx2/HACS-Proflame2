from __future__ import annotations

import json
from pathlib import Path

from custom_components.proflame2.protocol.packet import ProflameFrame
from custom_components.proflame2.rf.capture import frame_to_air_bytes
from tools.proflame_capture.collectors import StubRtl433Collector, StubYardStickCollector
from tools.proflame_capture.lilygo_syslog import InjectedSyslogReceiver, LilyGoSyslogCollector
from tools.proflame_capture.models import (
    CaptureCommand,
    CaptureSessionConfig,
    FireplaceState,
    SampleContext,
    SampleIdentity,
    utc_now,
)
from tools.proflame_capture.runner import CaptureSessionRunner

FIFO_PROBE_LINES = [
    "RX fifo probe begin schema=1 probe_id=3 artifact_class=experimental_fifo_probe "
    "source=cc1101_rx_fifo frequency_hz=314973000 data_rate_bps=2400 requested_duration_ms=1000",
    "RX fifo probe meta settings schema=1 probe_id=3 mdmcfg4=0xF5 mdmcfg3=0x83 "
    "mdmcfg2=0x30 pktctrl1=0x00 pktctrl0=0x00 sync1=0x00 sync0=0x00",
    "RX fifo probe meta status schema=1 probe_id=3 ok=YES byte_count=6 buffer_full=NO "
    "rx_fifo_overflow=NO rxbytes_max=4 rxbytes_final=0 poll_count=12",
    "RX fifo probe chunk schema=1 probe_id=3 chunk=0 offset=0 count=3 hex=A55A00",
    "RX fifo probe chunk schema=1 probe_id=3 chunk=1 offset=3 count=3 hex=123456",
    "RX fifo probe end schema=1 probe_id=3 ok=YES failure_reason=none byte_count=6 "
    "buffer_full=NO rx_fifo_overflow=NO",
]

FIFO_SEMANTIC_PAYLOAD_HEX = (
    b"\x00\xff"
    + frame_to_air_bytes(ProflameFrame(serial_id=0x3B3F02, cmd1=0x01, err1=0x76, cmd2=0x31, err2=0x6A))
    + b"\x55"
).hex()
FIFO_SEMANTIC_LINES = [
    "RX fifo probe begin schema=2 probe_id=4 artifact_class=experimental_fifo_probe "
    "source=cc1101_rx_fifo capture_mode=rolling_fifo_trailing_window profile=rfcat_fixed_none_rfcat_wide "
    "frequency_hz=314973000 data_rate_bps=2400 requested_duration_ms=5000",
    f"RX fifo probe chunk schema=2 probe_id=4 chunk=0 offset=0 count={len(bytes.fromhex(FIFO_SEMANTIC_PAYLOAD_HEX))} "
    f"hex={FIFO_SEMANTIC_PAYLOAD_HEX}",
    "RX fifo probe meta status schema=2 probe_id=4 ok=YES "
    f"byte_count={len(bytes.fromhex(FIFO_SEMANTIC_PAYLOAD_HEX))} buffer_full=NO rx_fifo_overflow=NO "
    "rolling_history_overflow=YES dropped_required_window_byte=NO trailing_window_complete=YES insufficient_trailing_window=NO",
    "RX fifo probe end schema=2 probe_id=4 ok=YES failure_reason=none "
    f"byte_count={len(bytes.fromhex(FIFO_SEMANTIC_PAYLOAD_HEX))} buffer_full=NO rx_fifo_overflow=NO",
]


def _collector_with_lines(lines: list[str]) -> LilyGoSyslogCollector:
    receiver = InjectedSyslogReceiver()
    for line in lines:
        receiver.feed_line(line, source_host="192.168.100.46", source_port=5514)
    collector = LilyGoSyslogCollector(receiver=receiver)
    collector.start_session(CaptureSessionConfig(output_root=Path(".")))
    return collector


def _sample_context(tmp_path: Path) -> SampleContext:
    sample_dir = tmp_path / "sample"
    sample_dir.mkdir(parents=True, exist_ok=True)
    return SampleContext(
        identity=SampleIdentity(
            session_id="session",
            sample_id="sample-001",
            sample_index=1,
            attempt_index=1,
            requested_action=CaptureCommand.FAN_UP,
            operator_prompt="press fan up",
            coordinator_started_at_utc=utc_now().isoformat(),
        ),
        sample_dir=sample_dir,
        state_before=FireplaceState(),
    )


def _drain_collector(collector: LilyGoSyslogCollector, line_count: int) -> None:
    for _ in range(line_count):
        collector.poll()


def test_fifo_probe_artifacts_preserve_raw_bytes_and_diagnostics(tmp_path: Path) -> None:
    collector = _collector_with_lines(FIFO_PROBE_LINES)
    sample_context = _sample_context(tmp_path)
    _drain_collector(collector, len(FIFO_PROBE_LINES))

    result = collector.finalize_sample(sample_context)

    assert result.complete is True
    assert result.valid is False
    assert result.reject_reason == "fifo_probe_no_candidate"
    fifo_path = sample_context.sample_dir / "lilygo" / "fifo_probe.json"
    payload_path = sample_context.sample_dir / "lilygo" / "fifo_probe_payload.hex"
    bit_path = sample_context.sample_dir / "lilygo" / "fifo_probe_bit_stream.txt"
    assert fifo_path.is_file()
    assert payload_path.read_text(encoding="utf-8").strip() == "A55A00123456"
    assert bit_path.read_text(encoding="utf-8").strip().startswith("1010010101011010")
    fifo = json.loads(fifo_path.read_text(encoding="utf-8"))
    assert fifo["artifact_class"] == "experimental_fifo_probe"
    assert fifo["latest_probe"]["metadata"]["frequency_hz"] == 314973000
    assert fifo["latest_probe"]["warnings"] == []
    assert fifo["latest_probe"]["semantic_candidate_count"] == 0


def test_fifo_probe_with_decoded_candidate_is_valid_semantic_artifact(tmp_path: Path) -> None:
    collector = _collector_with_lines(FIFO_SEMANTIC_LINES)
    sample_context = _sample_context(tmp_path)
    _drain_collector(collector, len(FIFO_SEMANTIC_LINES))

    result = collector.finalize_sample(sample_context)

    assert result.complete is True
    assert result.valid is True
    assert result.reject_reason is None
    semantic_path = sample_context.sample_dir / "lilygo" / "semantic_fifo_artifact.json"
    artifact = json.loads(semantic_path.read_text(encoding="utf-8"))
    assert artifact["artifact_class"] == "semantic_fifo_candidate"
    assert artifact["semantic_comparable"] is True
    assert artifact["decode_success"] is True
    assert artifact["decoded_fields"] == {
        "remote_id": "3b3f02",
        "cmd1": "01",
        "cmd2": "31",
        "err1": "76",
        "err2": "6a",
    }
    assert result.metadata["semantic_fifo_present"] is True


def test_syslog_collector_ignores_non_fifo_lines(tmp_path: Path) -> None:
    collector = _collector_with_lines(["RX capture status enabled=YES rolling_edges_available=12"])
    sample_context = _sample_context(tmp_path)
    _drain_collector(collector, 1)

    result = collector.finalize_sample(sample_context)

    assert result.complete is False
    assert result.valid is False
    assert result.reject_reason == "missing_semantic_fifo_artifact"


def test_source_port_metadata_is_preserved(tmp_path: Path) -> None:
    receiver = InjectedSyslogReceiver()
    receiver.feed_line(FIFO_SEMANTIC_LINES[0], source_host="192.168.100.46", source_port=5514)
    for line in FIFO_SEMANTIC_LINES[1:]:
        receiver.feed_line(line, source_host="192.168.100.46", source_port=5514)
    collector = LilyGoSyslogCollector(receiver=receiver)
    collector.start_session(CaptureSessionConfig(output_root=tmp_path))
    sample_context = _sample_context(tmp_path)
    _drain_collector(collector, len(FIFO_SEMANTIC_LINES))

    result = collector.finalize_sample(sample_context)

    assert result.metadata["source_host"] == "192.168.100.46"
    assert result.metadata["source_port"] == 5514


def test_runner_can_use_lilygo_collector_with_injected_fifo_lines(tmp_path: Path) -> None:
    collector = _collector_with_lines(FIFO_SEMANTIC_LINES)
    runner = CaptureSessionRunner(
        config=CaptureSessionConfig(
            output_root=tmp_path,
            sample_timeout_seconds=0.25,
            valid_samples_target=1,
            max_attempts=1,
            poll_interval_seconds=0.0,
        ),
        collectors=[collector, StubRtl433Collector(), StubYardStickCollector()],
    )

    summary = runner.run()

    assert summary["valid_samples_collected"] == 1
    session_dir = Path(summary["session_dir"])
    sample_dir = next(path for path in session_dir.iterdir() if (path / "sample_manifest.json").exists())
    assert (sample_dir / "lilygo" / "semantic_fifo_artifact.json").is_file()
