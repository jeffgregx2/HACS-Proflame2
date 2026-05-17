"""Audit YardStick diagnostics without promoting debug windows to semantics.

Stage 5U established that only canonical YardStick semantic artifacts are
packet-owned evidence. Candidate, failed, heuristic, and whole-stream
diagnostics remain useful for debugging receiver behavior, but they must not be
recommended for semantic replicate comparison.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from custom_components.proflame2.rf.artifacts import is_yardstick_semantic_artifact


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _sample_dirs(input_dir: Path) -> tuple[str, list[Path]]:
    if (input_dir / "session_manifest.json").exists():
        return "session", sorted(
            path for path in input_dir.iterdir() if path.is_dir() and (path / "sample_manifest.json").exists()
        )
    if (input_dir / "workspace_manifest.json").exists():
        return "alignment_workspace", sorted(
            path for path in input_dir.iterdir() if path.is_dir() and (path / "sample_alignment_manifest.json").exists()
        )
    return "unknown", sorted(
        path
        for path in input_dir.iterdir()
        if path.is_dir()
        and ((path / "yardstick" / "diagnostic.json").exists() or (path / "yardstick_transport_summary.json").exists())
    )


def _load_diagnostic(sample_dir: Path, input_kind: str) -> dict[str, Any] | None:
    if input_kind == "session":
        return _load_json(sample_dir / "yardstick" / "diagnostic.json")
    if input_kind == "alignment_workspace":
        return _load_json(sample_dir / "yardstick_transport_summary.json")
    return _load_json(sample_dir / "yardstick" / "diagnostic.json") or _load_json(
        sample_dir / "yardstick_transport_summary.json"
    )


def _is_packet_normalized(diagnostic: dict[str, Any]) -> bool | None:
    if "packet_normalized" in diagnostic:
        return diagnostic.get("packet_normalized")
    if diagnostic.get("selected_symbol_offset") is not None or diagnostic.get("selected_candidate_window"):
        return True
    if diagnostic.get("symbol_stream") or diagnostic.get("symbol_stream_length"):
        return False
    return None


def _candidate_window_count(diagnostic: dict[str, Any]) -> int:
    return (
        len(diagnostic.get("candidate_windows") or [])
        + len(diagnostic.get("failed_candidate_windows") or [])
        + len(diagnostic.get("diagnostic_candidate_windows") or [])
    )


def _canonical_semantic_artifact(diagnostic: dict[str, Any]) -> dict[str, Any] | None:
    """Return the canonical semantic artifact if the Stage 5U gate passes."""
    nested_artifact = diagnostic.get("semantic_artifact")
    candidates = [nested_artifact, diagnostic] if isinstance(nested_artifact, dict) else [diagnostic]
    for artifact in candidates:
        if is_yardstick_semantic_artifact(artifact):
            return artifact
    return None


def _semantic_gate_warnings(diagnostic: dict[str, Any]) -> list[str]:
    artifact = diagnostic.get("semantic_artifact")
    semantic_source = artifact if isinstance(artifact, dict) else diagnostic
    warnings: list[str] = []
    if semantic_source.get("artifact_class") != "semantic":
        warnings.append("missing_canonical_semantic_artifact")
    if semantic_source.get("semantic_comparable") is not True:
        warnings.append("semantic_comparable_not_true")
    if semantic_source.get("decode_success") is not True:
        warnings.append("decode_success_not_true")
    return warnings


def _debug_artifact_recommendation(diagnostic: dict[str, Any]) -> str:
    if diagnostic.get("selected_candidate_window"):
        return "debug_only_selected_candidate_window"
    if diagnostic.get("candidate_windows"):
        return "debug_only_candidate_windows"
    if diagnostic.get("failed_candidate_windows"):
        return "debug_only_failed_backend_candidate_windows"
    if diagnostic.get("diagnostic_candidate_windows"):
        return "debug_only_diagnostic_candidate_windows"
    if _is_packet_normalized(diagnostic) is False:
        return "debug_only_whole_stream_not_packet_normalized"
    return "none_yet"


def _recommended_artifact(diagnostic: dict[str, Any]) -> str:
    if _canonical_semantic_artifact(diagnostic) is not None:
        return "semantic_artifact"
    return "none_yet_canonical_semantic_artifact_required"


def build_yardstick_diagnostic_audit_report(input_dir: str | Path) -> dict[str, Any]:
    input_path = Path(input_dir)
    input_kind, sample_dirs = _sample_dirs(input_path)
    samples: list[dict[str, Any]] = []
    provenance_counts: Counter[str] = Counter()
    failure_counts: Counter[str] = Counter()
    candidate_count_distribution: Counter[str] = Counter()
    recommendation_counts: Counter[str] = Counter()
    packet_normalized_counts: Counter[str] = Counter()

    for sample_dir in sample_dirs:
        diagnostic = _load_diagnostic(sample_dir, input_kind)
        if diagnostic is None:
            samples.append(
                {
                    "sample_id": sample_dir.name,
                    "diagnostic_present": False,
                    "warnings": ["missing_yardstick_diagnostic"],
                    "suitable_for_replicate_comparison": False,
                    "recommended_artifact": "none_yet",
                }
            )
            continue
        packet_normalized = _is_packet_normalized(diagnostic)
        candidate_window_count = _candidate_window_count(diagnostic)
        recommended = _recommended_artifact(diagnostic)
        debug_recommendation = _debug_artifact_recommendation(diagnostic)
        failure_reason = diagnostic.get("best_failure_reason") or diagnostic.get("decode_failure_reason")
        provenance = diagnostic.get("artifact_layer") or "legacy_unspecified"
        candidate_count = diagnostic.get("candidate_count")
        selected_offset_available = diagnostic.get("selected_symbol_offset") is not None
        occurrence_offsets = diagnostic.get("occurrence_offsets") or []
        canonical_semantic_artifact = _canonical_semantic_artifact(diagnostic)
        suitable = canonical_semantic_artifact is not None
        warnings: list[str] = []
        if packet_normalized is False:
            warnings.append("whole_stream_not_packet_normalized")
        if candidate_count in (None, 0) and not candidate_window_count:
            warnings.append("no_candidate_windows_available")
        if not selected_offset_available:
            warnings.append("selected_symbol_offset_unavailable")
        if not occurrence_offsets:
            warnings.append("occurrence_offsets_empty")
        if not suitable:
            warnings.append("unsuitable_for_replicate_comparison")
            warnings.extend(_semantic_gate_warnings(diagnostic))
        if debug_recommendation.startswith("debug_only"):
            warnings.append("yardstick_diagnostic_windows_debug_only")

        provenance_counts[str(provenance)] += 1
        failure_counts[str(failure_reason or "none")] += 1
        candidate_count_distribution[str(candidate_count)] += 1
        recommendation_counts[recommended] += 1
        packet_normalized_counts[str(packet_normalized)] += 1
        samples.append(
            {
                "sample_id": sample_dir.name,
                "diagnostic_present": True,
                "artifact_layer": provenance,
                "symbol_stream_layer": diagnostic.get("symbol_stream_layer"),
                "bit_stream_layer": diagnostic.get("bit_stream_layer"),
                "packet_normalized": packet_normalized,
                "candidate_search_performed": diagnostic.get("candidate_search_performed"),
                "candidate_count": candidate_count,
                "candidate_window_count": candidate_window_count,
                "selected_symbol_offset": diagnostic.get("selected_symbol_offset"),
                "selected_window_available": bool(diagnostic.get("selected_candidate_window")),
                "occurrence_offsets_count": len(occurrence_offsets),
                "decode_success": diagnostic.get("decode_success"),
                "best_failure_reason": failure_reason,
                "recommended_artifact": recommended,
                "debug_artifact_available": debug_recommendation,
                "canonical_semantic_artifact_present": suitable,
                "canonical_semantic_artifact_class": (
                    canonical_semantic_artifact.get("artifact_class")
                    if canonical_semantic_artifact is not None
                    else None
                ),
                "suitable_for_replicate_comparison": suitable,
                "diagnostic_limitations": diagnostic.get("diagnostic_limitations", []),
                "warnings": warnings,
            }
        )

    suitable_count = sum(1 for sample in samples if sample.get("suitable_for_replicate_comparison"))
    whole_stream_only_count = sum(
        1
        for sample in samples
        if sample.get("packet_normalized") is False and sample.get("candidate_window_count") == 0
    )
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "input_path": str(input_path),
        "input_kind": input_kind,
        "samples_analyzed": len(samples),
        "samples": samples,
        "summary": {
            "artifact_provenance_counts": dict(provenance_counts),
            "packet_normalized_counts": dict(packet_normalized_counts),
            "candidate_count_distribution": dict(candidate_count_distribution),
            "failure_reason_distribution": dict(failure_counts),
            "recommended_artifact_counts": dict(recommendation_counts),
            "candidate_windows_available_count": sum(
                1 for sample in samples if sample.get("candidate_window_count", 0) > 0
            ),
            "suitable_for_replicate_comparison_count": suitable_count,
            "whole_stream_only_count": whole_stream_only_count,
            "whole_symbol_stream_suitable_for_replicate_comparison": False,
            "selected_offsets_unavailable_because_decode_failed": any(
                sample.get("decode_success") is False and sample.get("selected_symbol_offset") is None
                for sample in samples
            ),
            "recommended_future_comparison_artifact": (
                "canonical_yardstick_semantic_artifact"
                if suitable_count
                else "none_yet_generate_canonical_yardstick_semantic_artifacts"
            ),
        },
    }


def render_yardstick_diagnostic_audit_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# YardStick Diagnostic Audit Report",
        "",
        f"- Input: `{report['input_path']}`",
        f"- Input kind: `{report['input_kind']}`",
        f"- Samples analyzed: {report['samples_analyzed']}",
        f"- Candidate windows available: {summary['candidate_windows_available_count']}",
        f"- Suitable for replicate comparison: {summary['suitable_for_replicate_comparison_count']}",
        "- Whole symbol stream suitable for replicate comparison: "
        f"{summary['whole_symbol_stream_suitable_for_replicate_comparison']}",
        f"- Recommended future artifact: `{summary['recommended_future_comparison_artifact']}`",
        "",
        "## Distributions",
        "",
        f"- Artifact provenance: `{summary['artifact_provenance_counts']}`",
        f"- Packet normalized: `{summary['packet_normalized_counts']}`",
        f"- Candidate counts: `{summary['candidate_count_distribution']}`",
        f"- Failure reasons: `{summary['failure_reason_distribution']}`",
        f"- Recommended artifacts: `{summary['recommended_artifact_counts']}`",
        "",
        "## Samples",
        "",
        "| Sample | Packet Normalized | Candidate Windows | Selected Offset | Failure | Recommended | Debug Artifact | Warnings |",
        "| --- | --- | ---: | --- | --- | --- | --- | --- |",
    ]
    for sample in report["samples"]:
        lines.append(
            f"| `{sample['sample_id']}` | {sample.get('packet_normalized')} | "
            f"{sample.get('candidate_window_count', 0)} | {sample.get('selected_symbol_offset')} | "
            f"{sample.get('best_failure_reason')} | `{sample.get('recommended_artifact')}` | "
            f"`{sample.get('debug_artifact_available')}` | "
            f"{', '.join(sample.get('warnings', [])) or '-'} |"
        )
    lines.extend(
        [
            "",
            "## Conclusion",
            "",
            "- Whole YardStick symbol streams are receive-buffer diagnostics, not proven packet-normalized artifacts.",
            "- Selected, candidate, failed, heuristic, and whole-stream diagnostic windows are debug-only.",
            "- Semantic replicate comparison requires `artifact_class=semantic`, "
            "`semantic_comparable=true`, and `decode_success=true`.",
            "",
        ]
    )
    return "\n".join(lines)


def write_yardstick_diagnostic_audit_report(
    report: dict[str, Any],
    *,
    output_dir: str | Path,
    json_only: bool = False,
    markdown_only: bool = False,
) -> dict[str, Path]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    if not markdown_only:
        json_path = output_path / "yardstick_diagnostic_audit_report.json"
        json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written["json"] = json_path
    if not json_only:
        markdown_path = output_path / "yardstick_diagnostic_audit_report.md"
        markdown_path.write_text(render_yardstick_diagnostic_audit_markdown(report), encoding="utf-8")
        written["markdown"] = markdown_path
    return written
