"""Build packet-comparison workspaces from validated capture sessions.

This decision-gating tool assembles canonical semantic labels and raw source
artifacts without treating debug or failed YardStick/LilyGO captures as
semantic evidence.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .comparison import build_session_comparison_report
from .session_report import build_session_report, load_session_report_input

ALIGNMENT_WORKSPACE_SCHEMA_VERSION = 1


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _text_length(value: Any) -> int:
    if value is None:
        return 0
    return len(str(value))


def _load_comparison_report(
    session_dir: Path,
    *,
    comparison_report_path: str | Path | None,
    include_invalid: bool,
    expected_id: str | None,
) -> dict[str, Any]:
    if comparison_report_path:
        return _load_json(Path(comparison_report_path))
    loaded = load_session_report_input(session_dir)
    session_report = build_session_report(loaded, include_invalid=include_invalid)
    return build_session_comparison_report(session_report, expected_id=expected_id)


def _selected_samples(
    comparison_report: dict[str, Any],
    *,
    include_partial: bool,
    include_invalid: bool,
    sample_ids: set[str] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    selected: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for sample in comparison_report["samples"]:
        sample_id = sample["sample_id"]
        if sample_ids and sample_id not in sample_ids:
            skipped.append({"sample_id": sample_id, "reason": "sample_id_filtered"})
            continue
        if sample["comparison_ready"] == "YES":
            selected.append(sample)
            continue
        if include_partial and sample["comparison_ready"] == "PARTIAL":
            selected.append(sample)
            continue
        if include_invalid and not sample["collection_valid"]:
            selected.append(sample)
            continue
        skipped.append({"sample_id": sample_id, "reason": f"comparison_ready_{sample['comparison_ready'].lower()}"})
    return selected, skipped


def _build_yardstick_summary(sample_dir: Path) -> tuple[dict[str, Any], list[str]]:
    yardstick_dir = sample_dir / "yardstick"
    diagnostic = _load_json(yardstick_dir / "diagnostic.json")
    warnings: list[str] = []
    bit_path = yardstick_dir / "bit_stream.txt"
    symbol_path = yardstick_dir / "symbol_stream.txt"
    if not bit_path.exists():
        warnings.append("missing_yardstick_bit_stream")
    if not symbol_path.exists():
        warnings.append("missing_yardstick_symbol_stream")
    summary = {
        "raw_payload_hex_present": bool(diagnostic.get("raw_payload_hex")),
        "raw_payload_hex_length": _text_length(diagnostic.get("raw_payload_hex")),
        "payload_length_bytes": diagnostic.get("payload_length_bytes"),
        "bit_stream_length": _text_length(diagnostic.get("bit_stream")),
        "symbol_stream_length": _text_length(diagnostic.get("symbol_stream")),
        "decode_success": diagnostic.get("decode_success"),
        "decode_failure_reason": diagnostic.get("decode_failure_reason"),
        "best_failure_reason": diagnostic.get("best_failure_reason"),
        "reason_counts": diagnostic.get("reason_counts", {}),
        "repeat_count": diagnostic.get("repeat_count"),
        "occurrence_offsets": diagnostic.get("occurrence_offsets", []),
        "selected_bit_offset": diagnostic.get("selected_bit_offset"),
        "selected_symbol_offset": diagnostic.get("selected_symbol_offset"),
        "candidate_count": diagnostic.get("candidate_count"),
        "artifact_layer": diagnostic.get("artifact_layer"),
        "symbol_stream_layer": diagnostic.get("symbol_stream_layer"),
        "bit_stream_layer": diagnostic.get("bit_stream_layer"),
        "packet_normalized": diagnostic.get("packet_normalized"),
        "candidate_search_performed": diagnostic.get("candidate_search_performed"),
        "candidate_windows_retained": diagnostic.get("candidate_windows_retained"),
        "selected_window_available": diagnostic.get("selected_window_available"),
        "diagnostic_limitations": diagnostic.get("diagnostic_limitations", []),
        "candidate_windows": diagnostic.get("candidate_windows", []),
        "failed_candidate_windows": diagnostic.get("failed_candidate_windows", []),
        "best_candidate_window": diagnostic.get("best_candidate_window"),
        "selected_candidate_window": diagnostic.get("selected_candidate_window"),
        "diagnostic_candidate_windows": diagnostic.get("diagnostic_candidate_windows", []),
        "diagnostic_candidate_offsets": diagnostic.get("diagnostic_candidate_offsets", []),
        "diagnostic_candidate_reason": diagnostic.get("diagnostic_candidate_reason"),
        "diagnostic_candidate_confidence": diagnostic.get("diagnostic_candidate_confidence"),
        "semantic_artifact": diagnostic.get("semantic_artifact"),
        "semantic_comparable": diagnostic.get("semantic_comparable", False),
        "artifact_class": diagnostic.get("artifact_class"),
        "learning_attempt_count": diagnostic.get("learning_attempt_count"),
        "failed_attempt_count_before_success": diagnostic.get("failed_attempt_count_before_success"),
        "failed_attempts": diagnostic.get("failed_attempts", []),
        "active_frequency_hz": diagnostic.get("active_frequency_hz"),
        "warnings": warnings,
    }
    return summary, warnings


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _copy_text_if_present(source: Path, destination: Path) -> bool:
    if not source.exists():
        return False
    destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return True


def _build_side_by_side_summary(
    *,
    sample: dict[str, Any],
    lilygo_fifo_summary: dict[str, Any],
    yardstick_summary: dict[str, Any],
    rtl433_semantic: dict[str, Any],
) -> str:
    lines = [
        f"# Alignment Workspace Summary: {sample['sample_id']}",
        "",
        f"- Requested action: `{sample['requested_action']}`",
        f"- Collection valid: `{sample['collection_valid']}`",
        f"- Comparison ready: `{sample['comparison_ready']}`",
        "",
        "## LilyGO",
        "",
        f"- FIFO semantic artifact present: {lilygo_fifo_summary['present']}",
        f"- Decode success: {lilygo_fifo_summary.get('decode_success')}",
        f"- Remote ID: {lilygo_fifo_summary.get('remote_id')}",
        f"- Cmd1/Cmd2: {lilygo_fifo_summary.get('cmd1')}/{lilygo_fifo_summary.get('cmd2')}",
        "",
        "## YardStick",
        "",
        f"- Payload bytes: {yardstick_summary['payload_length_bytes']}",
        f"- Bit stream length: {yardstick_summary['bit_stream_length']}",
        f"- Symbol stream length: {yardstick_summary['symbol_stream_length']}",
        f"- Decode success: {yardstick_summary['decode_success']}",
        f"- Best failure reason: {yardstick_summary['best_failure_reason']}",
        "",
        "## rtl_433",
        "",
        f"- ID: {rtl433_semantic.get('id')}",
        f"- Cmd1/Cmd2: {rtl433_semantic.get('cmd1')}/{rtl433_semantic.get('cmd2')}",
        f"- Power/Flame/Fan: {rtl433_semantic.get('power')}/{rtl433_semantic.get('flame')}/{rtl433_semantic.get('fan')}",
        "",
        "## Notes",
        "",
        "- LilyGO RX alignment uses semantic FIFO artifacts only.",
        "- LilyGO edge interval artifacts are not part of the active RX path and are not generated.",
        "",
    ]
    return "\n".join(lines)


def build_alignment_workspace(
    session_dir: str | Path,
    *,
    comparison_report_path: str | Path | None = None,
    output_dir: str | Path | None = None,
    include_partial: bool = False,
    include_invalid: bool = False,
    sample_ids: list[str] | None = None,
    expected_id: str | None = "3b3f02",
) -> dict[str, Any]:
    session_path = Path(session_dir)
    comparison_report = _load_comparison_report(
        session_path,
        comparison_report_path=comparison_report_path,
        include_invalid=include_invalid,
        expected_id=expected_id,
    )
    selected, skipped = _selected_samples(
        comparison_report,
        include_partial=include_partial,
        include_invalid=include_invalid,
        sample_ids=set(sample_ids or []),
    )
    workspace_dir = Path(output_dir) if output_dir else session_path / "alignment_workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)

    generated_samples: list[dict[str, Any]] = []
    for sample in selected:
        sample_id = sample["sample_id"]
        sample_dir = session_path / sample_id
        workspace_sample_dir = workspace_dir / sample_id
        workspace_sample_dir.mkdir(parents=True, exist_ok=True)

        lilygo_semantic_fifo_written = _copy_text_if_present(
            sample_dir / "lilygo" / "semantic_fifo_artifact.json",
            workspace_sample_dir / "lilygo_semantic_fifo_artifact.json",
        )
        lilygo_fifo_summary: dict[str, Any] = {"present": lilygo_semantic_fifo_written}
        if lilygo_semantic_fifo_written:
            artifact = _load_json(workspace_sample_dir / "lilygo_semantic_fifo_artifact.json")
            decoded = artifact.get("decoded_fields") if isinstance(artifact.get("decoded_fields"), dict) else {}
            lilygo_fifo_summary.update(
                {
                    "decode_success": artifact.get("decode_success"),
                    "semantic_comparable": artifact.get("semantic_comparable"),
                    "remote_id": decoded.get("remote_id"),
                    "cmd1": decoded.get("cmd1"),
                    "cmd2": decoded.get("cmd2"),
                    "err1": decoded.get("err1"),
                    "err2": decoded.get("err2"),
                    "payload_byte_count": artifact.get("payload_byte_count"),
                }
            )

        yardstick_summary, yardstick_warnings = _build_yardstick_summary(sample_dir)
        _write_json(workspace_sample_dir / "yardstick_transport_summary.json", yardstick_summary)
        bit_written = _copy_text_if_present(
            sample_dir / "yardstick" / "bit_stream.txt", workspace_sample_dir / "yardstick_bit_stream.txt"
        )
        symbol_written = _copy_text_if_present(
            sample_dir / "yardstick" / "symbol_stream.txt", workspace_sample_dir / "yardstick_symbol_stream.txt"
        )
        semantic_written = _copy_text_if_present(
            sample_dir / "yardstick" / "semantic_symbol_stream.txt",
            workspace_sample_dir / "yardstick_semantic_symbol_stream.txt",
        )
        semantic_bit_written = _copy_text_if_present(
            sample_dir / "yardstick" / "semantic_bit_stream.txt",
            workspace_sample_dir / "yardstick_semantic_bit_stream.txt",
        )

        rtl433_semantic = _load_json(sample_dir / "rtl433" / "decoded.json")
        rtl433_semantic["requested_action"] = sample["requested_action"]
        _write_json(workspace_sample_dir / "rtl433_semantic.json", rtl433_semantic)

        manifest = {
            "schema_version": ALIGNMENT_WORKSPACE_SCHEMA_VERSION,
            "session_path": str(session_path),
            "sample_id": sample_id,
            "requested_action": sample["requested_action"],
            "comparison_ready": sample["comparison_ready"],
            "collection_valid": sample["collection_valid"],
            "source_sample_dir": str(sample_dir),
            "files": {
                "lilygo_semantic_fifo_artifact": (
                    "lilygo_semantic_fifo_artifact.json" if lilygo_semantic_fifo_written else None
                ),
                "yardstick_transport_summary": "yardstick_transport_summary.json",
                "yardstick_bit_stream": "yardstick_bit_stream.txt" if bit_written else None,
                "yardstick_symbol_stream": "yardstick_symbol_stream.txt" if symbol_written else None,
                "yardstick_semantic_symbol_stream": (
                    "yardstick_semantic_symbol_stream.txt" if semantic_written else None
                ),
                "yardstick_semantic_bit_stream": "yardstick_semantic_bit_stream.txt" if semantic_bit_written else None,
                "rtl433_semantic": "rtl433_semantic.json",
                "side_by_side_summary": "side_by_side_summary.md",
            },
            "warnings": yardstick_warnings,
        }
        _write_json(workspace_sample_dir / "sample_alignment_manifest.json", manifest)
        side_by_side = _build_side_by_side_summary(
            sample=sample,
            lilygo_fifo_summary=lilygo_fifo_summary,
            yardstick_summary=yardstick_summary,
            rtl433_semantic=rtl433_semantic,
        )
        (workspace_sample_dir / "side_by_side_summary.md").write_text(side_by_side, encoding="utf-8")
        generated_samples.append(
            {
                "sample_id": sample_id,
                "workspace_dir": str(workspace_sample_dir),
                "warnings": yardstick_warnings,
            }
        )

    manifest = {
        "schema_version": ALIGNMENT_WORKSPACE_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "session_path": str(session_path),
        "selected_sample_ids": [sample["sample_id"] for sample in selected],
        "skipped_samples": skipped,
        "generated_samples": generated_samples,
        "lilygo_rx_artifact": "semantic_fifo_artifact",
        "tool_version": 1,
    }
    _write_json(workspace_dir / "workspace_manifest.json", manifest)
    return manifest
