from __future__ import annotations

import json
import time
from pathlib import Path

from custom_components.proflame2.protocol.packet import ProflameFrame
from custom_components.proflame2.rf.capture import frame_to_air_bytes
from tools.proflame_capture.composition import CollectorPlanEntry, build_collector_plan, summarize_plan
from tools.proflame_capture.lilygo_syslog import InjectedSyslogReceiver, LilyGoSyslogCollector
from tools.proflame_capture.models import CaptureSessionConfig
from tools.proflame_capture.rtl433_collector import InjectedRtl433Source, Rtl433Collector
from tools.proflame_capture.run_capture_session import _build_parser
from tools.proflame_capture.runner import CaptureSessionRunner
from tools.proflame_capture.yardstick_collector import (
    InjectedYardStickDiagnosticSource,
    YardStickDiagnosticCollector,
    YardStickDiagnosticResult,
)

FIFO_SEMANTIC_PAYLOAD_HEX = (
    frame_to_air_bytes(ProflameFrame(serial_id=0x3B3F02, cmd1=0x01, err1=0x76, cmd2=0x16, err2=0xEF))
).hex()
LILYGO_FIFO_LINES = [
    "RX fifo probe begin schema=2 probe_id=4 artifact_class=experimental_fifo_probe "
    "source=cc1101_rx_fifo capture_mode=rolling_fifo_trailing_window profile=rfcat_fixed_none_rfcat_wide",
    f"RX fifo probe chunk schema=2 probe_id=4 chunk=0 offset=0 count={len(bytes.fromhex(FIFO_SEMANTIC_PAYLOAD_HEX))} "
    f"hex={FIFO_SEMANTIC_PAYLOAD_HEX}",
    "RX fifo probe end schema=2 probe_id=4 ok=YES failure_reason=none "
    f"byte_count={len(bytes.fromhex(FIFO_SEMANTIC_PAYLOAD_HEX))} buffer_full=NO rx_fifo_overflow=NO",
]
RTL433_BLOCK = [
    "time      : 2026-05-07 04:52:22",
    "model     : Proflame2-Remote                       Id        : 3b3f02",
    "Cmd1      : 01           Cmd2      : 16            Err1      : 76            Err2      : ef",
    "Pilot     : 0            Light     : 0             Thermostat: 0             Power     : 1",
    "Front     : 0            Fan       : 1             Aux       : 0             Flame     : 6",
    "Integrity : CHECKSUM",
    "",
]

JEFFA_CODEX_HOST = "jeffa-codex"
LILYGO_CC1101_HOST = "lilygo-cc1101"


def _parse_args(*argv: str):
    return _build_parser().parse_args(list(argv))


def _run_with_plan(tmp_path: Path, plan: list[CollectorPlanEntry], *, valid_target: int = 1, max_attempts: int = 1):
    config = CaptureSessionConfig(
        output_root=tmp_path,
        valid_samples_target=valid_target,
        max_attempts=max_attempts,
        non_interactive=True,
        sample_timeout_seconds=0.05,
        poll_interval_seconds=0.0,
    )
    runner = CaptureSessionRunner(
        config=config,
        collectors=[entry.collector for entry in plan],
        session_metadata=summarize_plan(plan),
    )
    summary = runner.run()
    session_dir = Path(summary["session_dir"])
    session_manifest = json.loads((session_dir / "session_manifest.json").read_text(encoding="utf-8"))
    sample_id = session_manifest["sample_ids"][0]
    sample_manifest = json.loads((session_dir / sample_id / "sample_manifest.json").read_text(encoding="utf-8"))
    return summary, session_manifest, sample_manifest


def _lilygo_override() -> CollectorPlanEntry:
    receiver = InjectedSyslogReceiver()
    for line in LILYGO_FIFO_LINES:
        receiver.feed_line(line, source_host=LILYGO_CC1101_HOST)
    return CollectorPlanEntry(
        source_name="lilygo",
        mode="syslog",
        collector=LilyGoSyslogCollector(receiver=receiver, source_host_filter=LILYGO_CC1101_HOST),
        config={"bind_host": JEFFA_CODEX_HOST, "bind_port": 5514, "source_host_filter": LILYGO_CC1101_HOST},
    )


def _rtl433_override() -> CollectorPlanEntry:
    source = InjectedRtl433Source()
    base = time.monotonic()
    for index, line in enumerate(RTL433_BLOCK):
        source.feed_line(
            line, host_monotonic=base + 0.01 + index * 0.001, host_received_at_utc="2026-05-11T12:00:00+00:00"
        )
    collector = Rtl433Collector(source=source)
    collector.source_mode = "subprocess"
    return CollectorPlanEntry(
        source_name="rtl433",
        mode="subprocess",
        collector=collector,
        config={
            "executable_path": "/usr/local/bin/rtl_433",
            "frequency": "315M",
            "gain": "40",
            "protocol": "207",
            "extra_args": [],
        },
    )


def test_all_stubs_still_works() -> None:
    args = _parse_args("--stub-sources")
    plan = build_collector_plan(args)

    assert [entry.mode for entry in plan] == ["stub", "stub", "stub"]
    assert [entry.source_name for entry in plan] == ["lilygo", "rtl433", "yardstick"]


def test_stub_sources_plus_lilygo_syslog_overrides_only_lilygo(tmp_path: Path) -> None:
    args = _parse_args("--stub-sources", "--lilygo-syslog")
    plan = build_collector_plan(args, overrides={"lilygo": _lilygo_override()})
    summary, session_manifest, sample_manifest = _run_with_plan(tmp_path, plan)

    assert [entry.mode for entry in plan] == ["syslog", "stub", "stub"]
    assert summary["valid_samples_collected"] == 1
    assert session_manifest["collector_modes"]["lilygo"] == "syslog"
    assert sample_manifest["collector_results"]["lilygo"]["mode"] == "syslog"
    assert sample_manifest["collector_results"]["rtl433"]["mode"] == "stub"


def test_stub_sources_plus_rtl433_overrides_only_rtl433(tmp_path: Path) -> None:
    args = _parse_args("--stub-sources", "--rtl433")
    plan = build_collector_plan(args, overrides={"rtl433": _rtl433_override()})
    summary, session_manifest, sample_manifest = _run_with_plan(tmp_path, plan)

    assert [entry.mode for entry in plan] == ["stub", "subprocess", "stub"]
    assert summary["valid_samples_collected"] == 1
    assert session_manifest["collector_modes"]["rtl433"] == "subprocess"
    assert sample_manifest["collector_results"]["rtl433"]["mode"] == "subprocess"
    assert sample_manifest["collector_results"]["rtl433"]["artifact_dir"] == "rtl433"


def test_yardstick_stub_mode_composes_as_expected() -> None:
    args = _parse_args("--yardstick", "--yardstick-mode", "stub")
    plan = build_collector_plan(args)

    assert len(plan) == 1
    assert plan[0].source_name == "yardstick"
    assert plan[0].mode == "stub"


def test_duplicate_source_selection_is_prevented() -> None:
    args = _parse_args("--stub-sources", "--lilygo-syslog")
    plan = build_collector_plan(args, overrides={"lilygo": _lilygo_override()})

    names = [entry.source_name for entry in plan]
    assert names.count("lilygo") == 1


def test_session_and_sample_manifest_record_modes_and_status(tmp_path: Path) -> None:
    args = _parse_args("--stub-sources", "--rtl433", "--yardstick", "--yardstick-mode", "injected")
    yardstick = CollectorPlanEntry(
        source_name="yardstick",
        mode="injected",
        collector=YardStickDiagnosticCollector(
            mode="injected",
            source=InjectedYardStickDiagnosticSource([YardStickDiagnosticResult(raw_payload_hex="aabb")]),
        ),
        config={"mode": "injected"},
    )
    plan = build_collector_plan(args, overrides={"rtl433": _rtl433_override(), "yardstick": yardstick})
    _summary, session_manifest, sample_manifest = _run_with_plan(tmp_path, plan)

    assert session_manifest["selected_collectors"] == ["lilygo", "rtl433", "yardstick"]
    assert session_manifest["collector_modes"]["yardstick"] == "injected"
    assert sample_manifest["collector_results"]["yardstick"]["selected"] is True
    assert sample_manifest["collector_results"]["yardstick"]["artifact_dir"] == "yardstick"


def test_invalid_source_does_not_count_and_max_attempts_honored(tmp_path: Path) -> None:
    args = _parse_args("--stub-sources", "--yardstick", "--yardstick-mode", "injected")
    bad_yardstick = CollectorPlanEntry(
        source_name="yardstick",
        mode="injected",
        collector=YardStickDiagnosticCollector(
            mode="injected",
            source=InjectedYardStickDiagnosticSource([YardStickDiagnosticResult(), YardStickDiagnosticResult()]),
        ),
        config={"mode": "injected"},
    )
    plan = build_collector_plan(args, overrides={"yardstick": bad_yardstick})
    summary, session_manifest, _sample_manifest = _run_with_plan(tmp_path, plan, valid_target=1, max_attempts=2)

    assert summary["valid_samples_collected"] == 0
    assert summary["max_attempts_exhausted"] is True
    assert len(session_manifest["sample_ids"]) == 2
