from __future__ import annotations

import json
from pathlib import Path

from tools.proflame_capture_analysis.yardstick_diagnostic_audit import (
    build_yardstick_diagnostic_audit_report,
    write_yardstick_diagnostic_audit_report,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _make_session(tmp_path: Path, diagnostics: list[dict]) -> Path:
    session = tmp_path / "session"
    _write_json(session / "session_manifest.json", {"session_id": "s"})
    for index, diagnostic in enumerate(diagnostics, start=1):
        sample_dir = session / f"s001-a{index:03d}"
        _write_json(sample_dir / "sample_manifest.json", {"identity": {"sample_id": sample_dir.name}})
        _write_json(sample_dir / "yardstick" / "diagnostic.json", diagnostic)
    return session


def test_audit_identifies_legacy_whole_stream_only_as_unsuitable(tmp_path: Path) -> None:
    session = _make_session(
        tmp_path,
        [
            {
                "diagnostic_present": True,
                "symbol_stream": "S" * 1020,
                "bit_stream": "10" * 1020,
                "decode_success": False,
                "best_failure_reason": "invalid_manchester_symbols",
                "candidate_count": 0,
                "selected_symbol_offset": None,
                "occurrence_offsets": [],
            }
        ],
    )

    report = build_yardstick_diagnostic_audit_report(session)

    assert report["summary"]["whole_stream_only_count"] == 1
    assert report["summary"]["suitable_for_replicate_comparison_count"] == 0
    assert (
        report["summary"]["recommended_future_comparison_artifact"]
        == "none_yet_generate_canonical_yardstick_semantic_artifacts"
    )
    assert "whole_stream_not_packet_normalized" in report["samples"][0]["warnings"]


def test_audit_marks_candidate_windows_debug_only(tmp_path: Path) -> None:
    session = _make_session(
        tmp_path,
        [
            {
                "artifact_layer": "rfrecv_fixed_length_payload",
                "packet_normalized": False,
                "decode_success": False,
                "candidate_count": 0,
                "selected_symbol_offset": None,
                "candidate_windows": [],
                "failed_candidate_windows": [],
                "diagnostic_candidate_windows": [{"symbol_offset": 4, "symbol_stream": "S" * 91}],
                "best_failure_reason": "bad_start_end_guard",
            }
        ],
    )

    report = build_yardstick_diagnostic_audit_report(session)

    assert report["summary"]["candidate_windows_available_count"] == 1
    assert report["samples"][0]["recommended_artifact"] == "none_yet_canonical_semantic_artifact_required"
    assert report["samples"][0]["debug_artifact_available"] == "debug_only_diagnostic_candidate_windows"
    assert report["samples"][0]["suitable_for_replicate_comparison"] is False
    assert "yardstick_diagnostic_windows_debug_only" in report["samples"][0]["warnings"]


def test_audit_marks_failed_backend_windows_debug_only(tmp_path: Path) -> None:
    session = _make_session(
        tmp_path,
        [
            {
                "artifact_layer": "rfrecv_fixed_length_payload",
                "packet_normalized": False,
                "decode_success": False,
                "candidate_count": 0,
                "selected_symbol_offset": None,
                "failed_candidate_windows": [{"symbol_offset": 6, "symbol_stream": "S" * 100}],
                "diagnostic_candidate_windows": [{"symbol_offset": 44, "symbol_stream": "Z" * 100}],
                "best_failure_reason": "invalid_manchester_symbols",
            }
        ],
    )

    report = build_yardstick_diagnostic_audit_report(session)

    assert report["samples"][0]["recommended_artifact"] == "none_yet_canonical_semantic_artifact_required"
    assert report["samples"][0]["debug_artifact_available"] == "debug_only_failed_backend_candidate_windows"
    assert report["samples"][0]["suitable_for_replicate_comparison"] is False


def test_audit_accepts_only_canonical_semantic_artifacts(tmp_path: Path) -> None:
    session = _make_session(
        tmp_path,
        [
            {
                "artifact_layer": "rfrecv_fixed_length_payload",
                "semantic_artifact": {
                    "artifact_class": "semantic",
                    "semantic_comparable": True,
                    "decode_success": True,
                    "candidate_symbol_stream": "SSSS",
                },
                "candidate_windows": [{"symbol_offset": 4, "symbol_stream": "S" * 91}],
                "decode_success": False,
                "candidate_count": 1,
                "selected_symbol_offset": 4,
                "occurrence_offsets": [4],
            }
        ],
    )

    report = build_yardstick_diagnostic_audit_report(session)

    assert report["summary"]["suitable_for_replicate_comparison_count"] == 1
    assert report["summary"]["recommended_future_comparison_artifact"] == "canonical_yardstick_semantic_artifact"
    assert report["samples"][0]["recommended_artifact"] == "semantic_artifact"
    assert report["samples"][0]["debug_artifact_available"] == "debug_only_candidate_windows"
    assert report["samples"][0]["canonical_semantic_artifact_present"] is True
    assert report["samples"][0]["suitable_for_replicate_comparison"] is True


def test_audit_writes_json_and_markdown(tmp_path: Path) -> None:
    session = _make_session(tmp_path, [{"packet_normalized": False, "candidate_count": 0}])
    report = build_yardstick_diagnostic_audit_report(session)
    written = write_yardstick_diagnostic_audit_report(report, output_dir=tmp_path / "out")

    assert written["json"].exists()
    assert written["markdown"].exists()
