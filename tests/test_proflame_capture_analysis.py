from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.proflame_capture_analysis.session_report import (
    build_session_report,
    load_session_report_input,
    render_session_report_markdown,
    write_session_report,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_sample(
    session_dir: Path,
    *,
    sample_id: str = "20260512T113455Z-s001-a001",
    sample_index: int = 1,
    requested_action: str = "flame_up",
    collection_valid: bool = True,
    include_optional: bool = True,
) -> Path:
    sample_dir = session_dir / sample_id
    sample_dir.mkdir(parents=True, exist_ok=True)

    _write_json(
        sample_dir / "sample_manifest.json",
        {
            "identity": {
                "sample_id": sample_id,
                "sample_index": sample_index,
                "attempt_index": sample_index,
                "requested_action": requested_action,
                "collection_valid": collection_valid,
            },
            "collector_results": {
                "lilygo": {
                    "selected": True,
                    "complete": True,
                    "valid": collection_valid,
                    "artifact_dir": "lilygo",
                    "mode": "syslog",
                },
                "rtl433": {
                    "selected": True,
                    "complete": True,
                    "valid": collection_valid,
                    "artifact_dir": "rtl433",
                    "mode": "subprocess",
                },
                "yardstick": {
                    "selected": True,
                    "complete": True,
                    "valid": collection_valid,
                    "artifact_dir": "yardstick",
                    "mode": "live",
                },
            },
        },
    )
    _write_json(
        sample_dir / "analysis" / "quick_validation.json",
        {
            "collection_valid": collection_valid,
            "pairing_summary": {
                "lilygo_marker_present": True,
                "lilygo_export_complete": True,
                "rtl433_decode_present": True,
                "yardstick_diagnostic_present": True,
                "pairing_confidence": "high_all_sources_complete",
            },
            "semantic_summary": {
                "rtl433_id": "3b3f02",
                "rtl433_power": 1,
                "rtl433_flame": 3,
                "rtl433_fan": 2,
                "yardstick_decode_success": False,
            },
            "source_summary": {
                "lilygo": {
                    "selected": True,
                    "complete": True,
                    "valid": collection_valid,
                    "artifact_dir": "lilygo",
                    "reject_reason": None,
                },
                "rtl433": {
                    "selected": True,
                    "complete": True,
                    "valid": collection_valid,
                    "artifact_dir": "rtl433",
                    "reject_reason": None,
                },
                "yardstick": {
                    "selected": True,
                    "complete": True,
                    "valid": collection_valid,
                    "artifact_dir": "yardstick",
                    "reject_reason": None,
                },
            },
        },
    )
    _write_json(
        sample_dir / "lilygo" / "capture_export.json",
        {
            "artifact_class": "lilygo_fifo_capture_export",
            "semantic_fifo_present": True,
            "fifo_probe": {"latest_probe": {"byte_count": 12}},
        },
    )
    _write_json(
        sample_dir / "lilygo" / "semantic_fifo_artifact.json",
        {
            "artifact_class": "semantic_fifo_candidate",
            "semantic_comparable": True,
            "decode_success": True,
            "remote_id": "3b3f02",
            "cmd1": "01",
            "cmd2": "23",
            "err1": "76",
            "err2": "19",
        },
    )
    _write_text(sample_dir / "lilygo" / "raw_syslog.log", "raw syslog\n")

    _write_json(
        sample_dir / "rtl433" / "decoded.json",
        {
            "id": "3b3f02",
            "cmd1": "01",
            "cmd2": "23",
            "err1": "76",
            "err2": "19",
            "power": 1,
            "flame": 3,
            "fan": 2,
            "integrity": "CHECKSUM",
            "model": "Proflame2-Remote",
        },
    )
    if include_optional:
        _write_json(sample_dir / "rtl433" / "parser_debug.json", {"selected_block_index": 0})
        _write_text(sample_dir / "rtl433" / "raw_stdout.log", "stdout\n")

    _write_json(
        sample_dir / "yardstick" / "diagnostic.json",
        {
            "payload_length_bytes": 255,
            "raw_payload_hex": "aa55",
            "bit_stream": "1010",
            "symbol_stream": "S0S1",
            "decode_success": False,
            "decode_failure_reason": "invalid_manchester_symbols",
            "best_failure_reason": "invalid_manchester_symbols",
            "repeat_count": None,
            "selected_bit_offset": None,
            "selected_symbol_offset": None,
            "candidate_count": 0,
        },
    )
    if include_optional:
        _write_text(sample_dir / "yardstick" / "raw_payload.hex", "aa55\n")
        _write_text(sample_dir / "yardstick" / "bit_stream.txt", "1010\n")
        _write_text(sample_dir / "yardstick" / "symbol_stream.txt", "S0S1\n")
        _write_json(sample_dir / "yardstick" / "collector_debug.json", {"worker_alive": True})
    return sample_dir


def _make_session(tmp_path: Path) -> Path:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    _write_json(
        session_dir / "session_manifest.json",
        {
            "session_id": "20260512T113455Z",
            "selected_collectors": ["lilygo", "rtl433", "yardstick"],
            "collector_modes": {
                "lilygo": "syslog",
                "rtl433": "subprocess",
                "yardstick": "live",
            },
        },
    )
    _write_json(
        session_dir / "run_summary.json",
        {
            "valid_samples_collected": 1,
            "invalid_attempts": 0,
        },
    )
    _make_sample(session_dir)
    return session_dir


def test_loading_valid_three_source_sample(tmp_path: Path) -> None:
    session_dir = _make_session(tmp_path)
    loaded = load_session_report_input(session_dir)
    assert loaded.session_manifest["session_id"] == "20260512T113455Z"
    assert len(loaded.samples) == 1
    sample = loaded.samples[0]
    assert sample.lilygo_capture_export["semantic_fifo_present"] is True
    assert sample.lilygo_semantic_fifo_artifact["decode_success"] is True
    assert sample.rtl433_decoded["id"] == "3b3f02"
    assert sample.yardstick_diagnostic["payload_length_bytes"] == 255


def test_missing_optional_files_do_not_crash(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    _write_json(session_dir / "session_manifest.json", {"session_id": "s", "selected_collectors": []})
    _make_sample(session_dir, include_optional=False)
    loaded = load_session_report_input(session_dir)
    report = build_session_report(loaded, include_invalid=True)
    assert report["samples"][0]["artifact_availability"]["rtl433"]["parser_debug"] is False


def test_missing_required_manifest_raises_clear_error(tmp_path: Path) -> None:
    session_dir = tmp_path / "missing_session"
    session_dir.mkdir()
    with pytest.raises(FileNotFoundError, match="session_manifest.json"):
        load_session_report_input(session_dir)


def test_report_json_and_markdown_written(tmp_path: Path) -> None:
    session_dir = _make_session(tmp_path)
    loaded = load_session_report_input(session_dir)
    report = build_session_report(loaded, include_invalid=True)
    written = write_session_report(report, output_dir=tmp_path / "out", json_only=False, markdown_only=False)
    assert written["json"].exists()
    assert written["markdown"].exists()


def test_invalid_samples_included_or_excluded(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    _write_json(session_dir / "session_manifest.json", {"session_id": "s", "selected_collectors": []})
    _make_sample(session_dir, sample_id="s-valid", sample_index=1, collection_valid=True)
    _make_sample(session_dir, sample_id="s-invalid", sample_index=2, collection_valid=False)
    loaded = load_session_report_input(session_dir)
    included = build_session_report(loaded, include_invalid=True)
    excluded = build_session_report(loaded, include_invalid=False)
    assert included["total_sample_count"] == 2
    assert excluded["total_sample_count"] == 1


def test_lilygo_fifo_semantic_summary_from_fixture_json(tmp_path: Path) -> None:
    session_dir = _make_session(tmp_path)
    loaded = load_session_report_input(session_dir)
    report = build_session_report(loaded, include_invalid=True)
    lilygo = report["samples"][0]["lilygo"]
    assert lilygo["semantic_fifo_present"] is True
    assert lilygo["semantic_fifo_decode_success"] is True
    assert lilygo["semantic_fifo_remote_id"] == "3b3f02"
    assert lilygo["semantic_fifo_cmd1"] == "01"
    assert lilygo["semantic_fifo_cmd2"] == "23"


def test_yardstick_diagnostic_summary_from_fixture_json(tmp_path: Path) -> None:
    session_dir = _make_session(tmp_path)
    loaded = load_session_report_input(session_dir)
    report = build_session_report(loaded, include_invalid=True)
    yardstick = report["samples"][0]["yardstick"]
    assert yardstick["raw_payload_hex_present"] is True
    assert yardstick["payload_length_bytes"] == 255
    assert yardstick["bit_stream_length"] == 4
    assert yardstick["decode_failure_reason"] == "invalid_manchester_symbols"


def test_rtl433_decoded_summary_from_fixture_json(tmp_path: Path) -> None:
    session_dir = _make_session(tmp_path)
    loaded = load_session_report_input(session_dir)
    report = build_session_report(loaded, include_invalid=True)
    rtl433 = report["samples"][0]["rtl433"]
    assert rtl433["cmd1"] == "01"
    assert rtl433["cmd2"] == "23"
    assert rtl433["power"] == 1
    assert rtl433["model"] == "Proflame2-Remote"


def test_markdown_render_contains_sample_table(tmp_path: Path) -> None:
    session_dir = _make_session(tmp_path)
    loaded = load_session_report_input(session_dir)
    report = build_session_report(loaded, include_invalid=True)
    markdown = render_session_report_markdown(report)
    assert "| Sample | Action | Valid | LilyGO | rtl_433 | YardStick | Warnings |" in markdown
    assert "20260512T113455Z-s001-a001" in markdown
