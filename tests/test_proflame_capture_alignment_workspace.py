from __future__ import annotations

import json
from pathlib import Path

from tools.proflame_capture_analysis.alignment_workspace import build_alignment_workspace


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_sample(
    session_dir: Path,
    *,
    sample_id: str,
    collection_valid: bool = True,
) -> None:
    sample_dir = session_dir / sample_id
    _write_json(sample_dir / "sample_manifest.json", {"identity": {"sample_id": sample_id}})
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
    semantic_artifact = {
        "artifact_class": "semantic_fifo_candidate",
        "semantic_comparable": True,
        "decode_success": True,
        "packet_normalized": True,
        "remote_id": "3b3f02",
        "cmd1": "01",
        "cmd2": "23",
        "err1": "76",
        "err2": "19",
        "payload_byte_count": 28,
    }
    _write_json(sample_dir / "lilygo" / "capture_export.json", {"semantic_fifo_present": True})
    _write_json(sample_dir / "lilygo" / "semantic_fifo_artifact.json", semantic_artifact)
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
    _write_json(
        sample_dir / "yardstick" / "diagnostic.json",
        {
            "payload_length_bytes": 4,
            "raw_payload_hex": "aa55",
            "bit_stream": "1010",
            "symbol_stream": "S0S1",
            "decode_success": False,
            "decode_failure_reason": "invalid_manchester_symbols",
            "best_failure_reason": "invalid_manchester_symbols",
            "reason_counts": {"invalid_manchester_symbols": 1},
            "occurrence_offsets": [],
            "selected_bit_offset": None,
            "selected_symbol_offset": None,
            "candidate_count": 0,
            "active_frequency_hz": 315000000,
        },
    )
    _write_text(sample_dir / "yardstick" / "bit_stream.txt", "1010\n")
    _write_text(sample_dir / "yardstick" / "symbol_stream.txt", "S0S1\n")


def _make_comparison_report(session_dir: Path) -> Path:
    path = session_dir / "comparison.json"
    _write_json(
        path,
        {
            "samples": [
                {
                    "sample_id": "s_yes",
                    "requested_action": "flame_up",
                    "collection_valid": True,
                    "comparison_ready": "YES",
                },
                {
                    "sample_id": "s_partial",
                    "requested_action": "flame_down",
                    "collection_valid": True,
                    "comparison_ready": "PARTIAL",
                },
            ]
        },
    )
    return path


def test_workspace_generated_only_for_yes_samples_by_default(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    _make_sample(session_dir, sample_id="s_yes")
    _make_sample(session_dir, sample_id="s_partial")
    comparison = _make_comparison_report(session_dir)
    manifest = build_alignment_workspace(session_dir, comparison_report_path=comparison)
    assert manifest["selected_sample_ids"] == ["s_yes"]
    assert (session_dir / "alignment_workspace" / "s_yes" / "sample_alignment_manifest.json").exists()
    assert not (session_dir / "alignment_workspace" / "s_yes" / "lilygo_intervals_normalized.csv").exists()
    assert not (session_dir / "alignment_workspace" / "s_partial").exists()


def test_include_partial_and_sample_filter(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    _make_sample(session_dir, sample_id="s_yes")
    _make_sample(session_dir, sample_id="s_partial")
    comparison = _make_comparison_report(session_dir)
    manifest = build_alignment_workspace(
        session_dir,
        comparison_report_path=comparison,
        include_partial=True,
        sample_ids=["s_partial"],
    )
    assert manifest["selected_sample_ids"] == ["s_partial"]


def test_lilygo_semantic_fifo_artifact_copied(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    _make_sample(session_dir, sample_id="s_yes")
    comparison = _make_comparison_report(session_dir)
    build_alignment_workspace(session_dir, comparison_report_path=comparison)
    copied = json.loads(
        (session_dir / "alignment_workspace" / "s_yes" / "lilygo_semantic_fifo_artifact.json").read_text(
            encoding="utf-8"
        )
    )
    assert copied["semantic_comparable"] is True
    assert copied["remote_id"] == "3b3f02"


def test_yardstick_summary_and_rtl433_export(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    _make_sample(session_dir, sample_id="s_yes")
    comparison = _make_comparison_report(session_dir)
    build_alignment_workspace(session_dir, comparison_report_path=comparison)
    yardstick = json.loads(
        (session_dir / "alignment_workspace" / "s_yes" / "yardstick_transport_summary.json").read_text(encoding="utf-8")
    )
    rtl433 = json.loads(
        (session_dir / "alignment_workspace" / "s_yes" / "rtl433_semantic.json").read_text(encoding="utf-8")
    )
    assert yardstick["payload_length_bytes"] == 4
    assert rtl433["requested_action"] == "flame_up"


def test_workspace_manifest_written(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    _make_sample(session_dir, sample_id="s_yes")
    comparison = _make_comparison_report(session_dir)
    build_alignment_workspace(session_dir, comparison_report_path=comparison)
    manifest = json.loads((session_dir / "alignment_workspace" / "workspace_manifest.json").read_text(encoding="utf-8"))
    assert manifest["selected_sample_ids"] == ["s_yes"]
    assert manifest["lilygo_rx_artifact"] == "semantic_fifo_artifact"


def test_missing_optional_yardstick_files_tolerated(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    _make_sample(session_dir, sample_id="s_yes")
    (session_dir / "s_yes" / "yardstick" / "bit_stream.txt").unlink()
    (session_dir / "s_yes" / "yardstick" / "symbol_stream.txt").unlink()
    comparison = _make_comparison_report(session_dir)
    build_alignment_workspace(session_dir, comparison_report_path=comparison)
    manifest = json.loads(
        (session_dir / "alignment_workspace" / "s_yes" / "sample_alignment_manifest.json").read_text(encoding="utf-8")
    )
    assert "missing_yardstick_bit_stream" in manifest["warnings"]
