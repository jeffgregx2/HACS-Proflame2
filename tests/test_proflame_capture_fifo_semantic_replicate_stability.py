from __future__ import annotations

import json
from pathlib import Path

from tools.proflame_capture_analysis.fifo_semantic_replicate_stability import (
    build_fifo_semantic_replicate_stability_report,
    write_fifo_semantic_replicate_stability_report,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "alignment_workspace"
    workspace.mkdir()
    _write_json(workspace / "workspace_manifest.json", {"selected_sample_ids": []})
    return workspace


def _make_sample(
    workspace: Path,
    *,
    sample_id: str,
    fan: int,
    cmd2: str,
    err2: str,
    lilygo_symbols: str,
    yardstick_symbols: str | None = None,
    lilygo_cmd2: str | None = None,
    yardstick_cmd2: str | None = None,
) -> None:
    sample_dir = workspace / sample_id
    sample_dir.mkdir()
    _write_json(
        sample_dir / "sample_alignment_manifest.json",
        {
            "sample_id": sample_id,
            "requested_action": "fan_up" if fan else "fan_down",
            "collection_valid": True,
            "comparison_ready": "YES",
        },
    )
    _write_json(
        sample_dir / "rtl433_semantic.json",
        {
            "id": "3b3f02",
            "power": 1,
            "flame": 1,
            "fan": fan,
            "cmd1": "01",
            "cmd2": cmd2,
            "err1": "76",
            "err2": err2,
        },
    )
    _write_json(
        sample_dir / "lilygo_semantic_fifo_artifact.json",
        {
            "artifact_class": "semantic_fifo_candidate",
            "semantic_comparable": True,
            "decode_success": True,
            "packet_normalized": True,
            "remote_id": "3b3f02",
            "cmd1": "01",
            "cmd2": lilygo_cmd2 or cmd2,
            "err1": "76",
            "err2": err2,
            "decoded_fields": {
                "remote_id": "3b3f02",
                "cmd1": "01",
                "cmd2": lilygo_cmd2 or cmd2,
                "err1": "76",
                "err2": err2,
            },
            "candidate": {
                "symbols": lilygo_symbols,
                "raw_slice_hex": "aa55aa55",
                "repeat_count": 4,
                "confidence": 180,
                "absolute_bit_offset": 100,
            },
        },
    )
    _write_json(
        sample_dir / "yardstick_semantic_artifact.json",
        {
            "artifact_class": "semantic",
            "semantic_comparable": True,
            "decode_success": True,
            "id": "3b3f02",
            "cmd1": "01",
            "cmd2": yardstick_cmd2 or cmd2,
            "err1": "76",
            "err2": err2,
            "candidate_symbol_stream": yardstick_symbols or lilygo_symbols,
            "candidate_bit_stream": "101010101010",
            "repeat_count": 4,
            "candidate_confidence": 180,
            "candidate_absolute_bit_offset": 200,
        },
    )
    _write_text(sample_dir / "yardstick_semantic_symbol_stream.txt", yardstick_symbols or lilygo_symbols)
    _write_text(sample_dir / "yardstick_semantic_bit_stream.txt", "101010101010")


def test_fifo_semantic_replicate_pass_gate(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    base_a = "S100111011101S100111111001S100000010011"
    base_b = "S100111011101S100111111001S100000010001"
    _make_sample(workspace, sample_id="s1", fan=1, cmd2="11", err2="08", lilygo_symbols=base_a)
    _make_sample(workspace, sample_id="s2", fan=1, cmd2="11", err2="08", lilygo_symbols=base_a)
    _make_sample(workspace, sample_id="s3", fan=0, cmd2="01", err2="39", lilygo_symbols=base_b)
    _make_sample(workspace, sample_id="s4", fan=0, cmd2="01", err2="39", lilygo_symbols=base_b)

    report = build_fifo_semantic_replicate_stability_report(workspace)

    assert report["samples_analyzed"] == 4
    assert report["repeated_group_count"] == 2
    assert report["pass_gate"]["passed"] is True
    assert report["pass_gate"]["recommendation"] == "Stage 5AM: deprecate/remove LilyGO edge ownership path."


def test_fifo_semantic_mismatch_fails_gate(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    symbols = "S100111011101S100111111001S100000010011"
    _make_sample(workspace, sample_id="s1", fan=1, cmd2="11", err2="08", lilygo_symbols=symbols)
    _make_sample(workspace, sample_id="s2", fan=1, cmd2="11", err2="08", lilygo_symbols=symbols, lilygo_cmd2="01")
    _make_sample(workspace, sample_id="s3", fan=0, cmd2="01", err2="39", lilygo_symbols=symbols)
    _make_sample(workspace, sample_id="s4", fan=0, cmd2="01", err2="39", lilygo_symbols=symbols)

    report = build_fifo_semantic_replicate_stability_report(workspace)

    assert report["pass_gate"]["passed"] is False
    assert report["pass_gate"]["failure_mode"] == "stale_or_mismatched_fifo_candidate"
    assert report["pass_gate"]["decoded_field_mismatch_count"] == 1


def test_fifo_semantic_yardstick_mismatch_fails_gate(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    symbols = "S100111011101S100111111001S100000010011"
    _make_sample(workspace, sample_id="s1", fan=1, cmd2="11", err2="08", lilygo_symbols=symbols)
    _make_sample(workspace, sample_id="s2", fan=1, cmd2="11", err2="08", lilygo_symbols=symbols, yardstick_cmd2="01")
    _make_sample(workspace, sample_id="s3", fan=0, cmd2="01", err2="39", lilygo_symbols=symbols)
    _make_sample(workspace, sample_id="s4", fan=0, cmd2="01", err2="39", lilygo_symbols=symbols)

    report = build_fifo_semantic_replicate_stability_report(workspace)

    assert report["pass_gate"]["passed"] is False
    assert report["pass_gate"]["failure_mode"] == "stale_or_mismatched_fifo_candidate"
    assert report["pass_gate"]["decoded_field_mismatch_count"] == 1
    assert report["repeated_groups"][1]["yardstick_decoded_field_mismatch_count"] == 1


def test_fifo_semantic_insufficient_groups_fails_gate(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    symbols = "S100111011101S100111111001S100000010011"
    _make_sample(workspace, sample_id="s1", fan=1, cmd2="11", err2="08", lilygo_symbols=symbols)
    _make_sample(workspace, sample_id="s2", fan=1, cmd2="11", err2="08", lilygo_symbols=symbols)

    report = build_fifo_semantic_replicate_stability_report(workspace)

    assert report["pass_gate"]["passed"] is False
    assert report["pass_gate"]["failure_mode"] == "insufficient_samples"


def test_fifo_semantic_report_written(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    symbols = "S100111011101S100111111001S100000010011"
    _make_sample(workspace, sample_id="s1", fan=1, cmd2="11", err2="08", lilygo_symbols=symbols)
    _make_sample(workspace, sample_id="s2", fan=1, cmd2="11", err2="08", lilygo_symbols=symbols)
    report = build_fifo_semantic_replicate_stability_report(workspace)

    written = write_fifo_semantic_replicate_stability_report(report, output_dir=tmp_path / "out")

    assert written["json"].is_file()
    assert written["markdown"].is_file()
    assert "FIFO Semantic Replicate Stability" in written["markdown"].read_text(encoding="utf-8")
