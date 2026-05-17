from __future__ import annotations

import json
import time
from pathlib import Path

from tools.proflame_capture.collectors import StubLilyGoCollector, StubYardStickCollector
from tools.proflame_capture.models import CaptureSessionConfig
from tools.proflame_capture.rtl433_collector import (
    InjectedRtl433Source,
    Rtl433Collector,
    Rtl433OwnershipError,
)
from tools.proflame_capture.runner import CaptureSessionRunner

BLOCK_LINES = [
    "time      : 2026-05-07 04:52:22",
    "model     : Proflame2-Remote                       Id        : 3b3f02",
    "Cmd1      : 01           Cmd2      : 16            Err1      : 76            Err2      : ef",
    "Pilot     : 0            Light     : 0             Thermostat: 0             Power     : 1",
    "Front     : 0            Fan       : 1             Aux       : 0             Flame     : 6",
    "Integrity : CHECKSUM",
]
INCOMPLETE_BLOCK_LINES = BLOCK_LINES[:-1]


def _sample_time(base: float, offset: float) -> float:
    return base + offset


def _build_collector(lines: list[tuple[str, float, str]]) -> tuple[Rtl433Collector, InjectedRtl433Source]:
    source = InjectedRtl433Source()
    for line, monotonic, stream in lines:
        source.feed_line(
            line, stream=stream, host_monotonic=monotonic, host_received_at_utc="2026-05-10T12:00:00+00:00"
        )
    collector = Rtl433Collector(source=source)
    return collector, source


def _session(tmp_path: Path):
    return type("Session", (), {"session_id": "s", "session_dir": tmp_path, "config": None, "started_at_utc": "now"})()


def _sample(tmp_path: Path):
    return type("Sample", (), {"sample_dir": tmp_path, "identity": None, "state_before": None})()


def test_parser_handles_proflame2_block(tmp_path: Path) -> None:
    base = time.monotonic()
    lines = [(line, _sample_time(base, index * 0.001), "stdout") for index, line in enumerate(BLOCK_LINES + [""])]
    collector, _source = _build_collector(lines)
    collector.start_session(_session(tmp_path))
    collector.start_sample(_sample(tmp_path))

    for _ in range(len(lines)):
        collector.poll()
    result = collector.finalize_sample(_sample(tmp_path))

    assert result.valid is True
    assert result.metadata["model"] == "Proflame2-Remote"
    assert result.metadata["id"] == "3b3f02"
    assert result.metadata["flame"] == 6


def test_parser_handles_label_comments(tmp_path: Path) -> None:
    base = time.monotonic()
    lines = [("# rtl_433 - Fan Up", base, "stdout")]
    lines.extend(
        (line, _sample_time(base, 0.001 + index * 0.001), "stdout") for index, line in enumerate(BLOCK_LINES + [""])
    )
    collector, _source = _build_collector(lines)
    collector.start_session(_session(tmp_path))
    collector.start_sample(_sample(tmp_path))

    for _ in range(len(lines)):
        collector.poll()
    result = collector.finalize_sample(_sample(tmp_path))

    assert result.metadata["label"] == "Fan Up"


def test_parser_rejects_incomplete_block(tmp_path: Path) -> None:
    base = time.monotonic()
    lines = [
        (line, _sample_time(base, index * 0.001), "stdout") for index, line in enumerate(INCOMPLETE_BLOCK_LINES + [""])
    ]
    collector, source = _build_collector(lines)
    collector.start_session(_session(tmp_path))
    collector.start_sample(_sample(tmp_path))
    for _ in range(len(lines)):
        collector.poll()
    source.set_running(False, exit_code=0)
    result = collector.finalize_sample(_sample(tmp_path))

    assert result.valid is False
    assert result.reject_reason == "incomplete_block"


def test_parser_selects_first_block_after_sample_start(tmp_path: Path) -> None:
    base = time.monotonic()
    early = [
        (line, _sample_time(base, -1.0 + index * 0.001), "stdout") for index, line in enumerate(BLOCK_LINES + [""])
    ]
    late = [
        (line, _sample_time(base, 0.010 + index * 0.001), "stdout") for index, line in enumerate(BLOCK_LINES + [""])
    ]
    collector, _source = _build_collector(early + late)
    collector.start_session(_session(tmp_path))
    collector.start_sample(_sample(tmp_path))
    collector._state.sample_started_monotonic = base

    for _ in range(len(early + late)):
        collector.poll()
    result = collector.finalize_sample(_sample(tmp_path))

    assert result.valid is True
    assert result.metadata["selected_block_index"] == 1


def test_collector_completes_when_injected_block_arrives(tmp_path: Path) -> None:
    base = time.monotonic()
    lines = [(line, _sample_time(base, index * 0.001), "stdout") for index, line in enumerate(BLOCK_LINES + [""])]
    collector, _source = _build_collector(lines)
    collector.start_session(_session(tmp_path))
    collector.start_sample(_sample(tmp_path))
    for _ in range(len(lines)):
        collector.poll()

    assert collector.is_complete() is True


def test_collector_invalid_on_timeout_no_decode(tmp_path: Path) -> None:
    collector, _source = _build_collector([])
    collector.start_session(_session(tmp_path))
    collector.start_sample(_sample(tmp_path))
    result = collector.finalize_sample(_sample(tmp_path))

    assert result.valid is False
    assert result.reject_reason == "timeout"


def test_artifacts_are_written(tmp_path: Path) -> None:
    base = time.monotonic()
    lines = [(line, _sample_time(base, index * 0.001), "stdout") for index, line in enumerate(BLOCK_LINES + [""])]
    collector, _source = _build_collector(lines)
    collector.start_session(_session(tmp_path))
    sample = _sample(tmp_path)
    collector.start_sample(sample)
    for _ in range(len(lines)):
        collector.poll()
    collector.finalize_sample(sample)

    assert (tmp_path / "rtl433" / "raw_stdout.log").is_file()
    assert (tmp_path / "rtl433" / "decoded.json").is_file()
    payload = json.loads((tmp_path / "rtl433" / "decoded.json").read_text(encoding="utf-8"))
    assert payload["model"] == "Proflame2-Remote"


def test_runner_can_use_injected_rtl433_collector(tmp_path: Path) -> None:
    source = InjectedRtl433Source()
    base = time.monotonic()
    for index, line in enumerate(BLOCK_LINES + [""]):
        source.feed_line(
            line,
            host_monotonic=_sample_time(base, 0.01 + index * 0.001),
            host_received_at_utc="2026-05-10T12:00:00+00:00",
        )
    collector = Rtl433Collector(source=source)
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
        collectors=[StubLilyGoCollector(), StubYardStickCollector(), collector],
    )
    summary = runner.run()
    session_dir = Path(summary["session_dir"])
    session_manifest = json.loads((session_dir / "session_manifest.json").read_text(encoding="utf-8"))
    sample_id = session_manifest["sample_ids"][0]
    sample_manifest = json.loads((session_dir / sample_id / "sample_manifest.json").read_text(encoding="utf-8"))

    assert summary["valid_samples_collected"] == 1
    assert sample_manifest["collector_results"]["rtl433"]["valid"] is True


def test_subprocess_rtl433_lock_prevents_concurrent_sessions(tmp_path: Path) -> None:
    lock_path = tmp_path / "rtl433.lock"
    first = Rtl433Collector(source=None, lock_path=lock_path)
    second = Rtl433Collector(source=None, lock_path=lock_path)

    try:
        first._source = InjectedRtl433Source()
        first.source_mode = "subprocess"
        second._source = InjectedRtl433Source()
        second.source_mode = "subprocess"
        first.start_session(_session(tmp_path))
        try:
            second.start_session(_session(tmp_path))
        except Rtl433OwnershipError as exc:
            assert "already in use" in str(exc)
        else:
            raise AssertionError("Expected Rtl433OwnershipError for concurrent session startup.")
    finally:
        first.close()
        second.close()


def test_subprocess_rtl433_lock_is_released_on_close(tmp_path: Path) -> None:
    lock_path = tmp_path / "rtl433.lock"
    first = Rtl433Collector(source=None, lock_path=lock_path)
    second = Rtl433Collector(source=None, lock_path=lock_path)

    first._source = InjectedRtl433Source()
    first.source_mode = "subprocess"
    second._source = InjectedRtl433Source()
    second.source_mode = "subprocess"

    try:
        first.start_session(_session(tmp_path))
        first.close()
        second.start_session(_session(tmp_path))
    finally:
        first.close()
        second.close()


def test_subprocess_owner_cleans_up_stale_rtl433_processes(tmp_path: Path, monkeypatch) -> None:
    lock_path = tmp_path / "rtl433.lock"
    collector = Rtl433Collector(source=None, lock_path=lock_path)
    collector._source = InjectedRtl433Source()
    collector.source_mode = "subprocess"

    seen_signals: list[tuple[int, object]] = []
    stale_rounds = iter(([111, 222], [222], []))

    monkeypatch.setattr(collector, "_find_stale_rtl433_pids", lambda: list(next(stale_rounds, [])))
    monkeypatch.setattr(collector, "_terminate_pid", lambda pid, sig: seen_signals.append((pid, sig)))

    try:
        collector.start_session(_session(tmp_path))
    finally:
        collector.close()

    assert seen_signals[0][0] == 111
    assert seen_signals[1][0] == 222
    assert len(seen_signals) == 2
