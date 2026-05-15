from __future__ import annotations

from pathlib import Path

from tools.proflame_capture_analysis.comparison import (
    build_session_comparison_report,
    render_session_comparison_markdown,
    write_session_comparison_report,
)
from tools.proflame_capture_analysis.session_report import (
    build_session_report,
    load_session_report_input,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(__import__("json").dumps(payload, indent=2), encoding="utf-8")


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
        },
    )
    _write_json(
        sample_dir / "analysis" / "quick_validation.json",
        {
            "collection_valid": collection_valid,
            "semantic_summary": {
                "yardstick_decode_success": False,
                "yardstick_decoded_id": None,
                "lilygo_fifo_decode_success": True,
                "lilygo_fifo_matches_rtl433": True,
            },
        },
    )
    _write_json(
        sample_dir / "lilygo" / "capture_export.json",
        {
            "artifact_class": "lilygo_fifo_capture_export",
            "semantic_fifo_present": True,
        },
    )
    _write_json(
        sample_dir / "lilygo" / "semantic_fifo_artifact.json",
        {
            "artifact_class": "semantic_fifo_candidate",
            "semantic_comparable": True,
            "decode_success": True,
            "packet_normalized": True,
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
    _write_text(sample_dir / "yardstick" / "raw_payload.hex", "aa55\n")
    _write_text(sample_dir / "yardstick" / "bit_stream.txt", "1010\n")
    _write_text(sample_dir / "yardstick" / "symbol_stream.txt", "S0S1\n")
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
    return session_dir


def _build_report(session_dir: Path, *, include_invalid: bool = True, expected_id: str = "3b3f02") -> dict:
    loaded = load_session_report_input(session_dir)
    session_report = build_session_report(loaded, include_invalid=include_invalid)
    return build_session_comparison_report(session_report, expected_id=expected_id)


def test_ready_yes_with_yardstick_decode_failure_nonblocking(tmp_path: Path) -> None:
    session_dir = _make_session(tmp_path)
    _make_sample(session_dir)
    report = _build_report(session_dir)
    sample = report["samples"][0]
    assert sample["comparison_ready"] == "YES"
    assert sample["cross_source_basic_consistency"]["yardstick_decode_failure_is_nonblocking"] is True
    assert sample["recommended_for_stage5c"] is True


def test_partial_when_yardstick_missing_but_lilygo_and_rtl433_present(tmp_path: Path) -> None:
    session_dir = _make_session(tmp_path)
    sample_dir = _make_sample(session_dir)
    (sample_dir / "yardstick" / "diagnostic.json").unlink()
    (sample_dir / "yardstick" / "raw_payload.hex").unlink()
    (sample_dir / "yardstick" / "bit_stream.txt").unlink()
    (sample_dir / "yardstick" / "symbol_stream.txt").unlink()
    report = _build_report(session_dir)
    sample = report["samples"][0]
    assert sample["comparison_ready"] == "PARTIAL"
    assert "missing_yardstick_transport" in sample["readiness_reasons"]


def test_no_when_lilygo_semantic_fifo_missing(tmp_path: Path) -> None:
    session_dir = _make_session(tmp_path)
    sample_dir = _make_sample(session_dir)
    (sample_dir / "lilygo" / "semantic_fifo_artifact.json").unlink()
    report = _build_report(session_dir)
    sample = report["samples"][0]
    assert sample["comparison_ready"] == "NO"
    assert "missing_lilygo_semantic_fifo" in sample["readiness_reasons"]


def test_ready_yes_with_lilygo_semantic_fifo(tmp_path: Path) -> None:
    session_dir = _make_session(tmp_path)
    _make_sample(session_dir)
    report = _build_report(session_dir)
    sample = report["samples"][0]
    assert sample["comparison_ready"] == "YES"
    assert sample["source_presence"]["lilygo_semantic_fifo_present"] is True
    assert sample["cross_source_basic_consistency"]["sample_has_all_fifo_inputs"] is True
    assert "missing_lilygo_semantic_fifo" not in sample["readiness_reasons"]


def test_lilygo_fifo_rtl433_mismatch_is_counted(tmp_path: Path) -> None:
    session_dir = _make_session(tmp_path)
    sample_dir = _make_sample(session_dir)
    _write_json(
        sample_dir / "analysis" / "quick_validation.json",
        {
            "collection_valid": True,
            "semantic_summary": {
                "yardstick_decode_success": False,
                "yardstick_decoded_id": None,
                "lilygo_fifo_decode_success": True,
                "lilygo_fifo_matches_rtl433": False,
            },
        },
    )
    report = _build_report(session_dir)
    assert report["lilygo_quality_warning_counts"]["semantic_fifo_rtl433_mismatch"] == 1


def test_expected_id_match_true_false(tmp_path: Path) -> None:
    session_dir = _make_session(tmp_path)
    _make_sample(session_dir)
    matching = _build_report(session_dir, expected_id="3b3f02")
    mismatching = _build_report(session_dir, expected_id="deadbe")
    assert matching["samples"][0]["rtl433_semantic"]["expected_remote_id_match"] is True
    assert mismatching["samples"][0]["rtl433_semantic"]["expected_remote_id_match"] is False


def test_yardstick_failure_reason_counted_but_nonblocking(tmp_path: Path) -> None:
    session_dir = _make_session(tmp_path)
    _make_sample(session_dir)
    report = _build_report(session_dir)
    assert report["yardstick_failure_reason_counts"]["invalid_manchester_symbols"] == 1
    assert report["comparison_ready_yes_count"] == 1


def test_recommended_samples_list(tmp_path: Path) -> None:
    session_dir = _make_session(tmp_path)
    _make_sample(session_dir, sample_id="s1", sample_index=1)
    _make_sample(session_dir, sample_id="s2", sample_index=2, collection_valid=False)
    report = _build_report(session_dir)
    assert report["samples_recommended_for_stage5c"] == ["s1"]
    assert any(item["sample_id"] == "s2" for item in report["samples_excluded"])


def test_json_and_markdown_output_written(tmp_path: Path) -> None:
    session_dir = _make_session(tmp_path)
    _make_sample(session_dir)
    report = _build_report(session_dir)
    written = write_session_comparison_report(report, output_dir=tmp_path / "out", json_only=False, markdown_only=False)
    assert written["json"].exists()
    assert written["markdown"].exists()
    markdown = render_session_comparison_markdown(report)
    assert "YardStick decode failure is nonblocking" in markdown
