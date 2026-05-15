"""Build cross-source readiness summaries for capture sessions.

This decision-gating module reports whether source artifacts are available and
usable; transport/debug artifacts are never promoted to semantic truth here.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

COMPARISON_SCHEMA_VERSION = 1


def _transport_artifact_present(yardstick: dict[str, Any]) -> bool:
    return bool(
        yardstick.get("raw_payload_hex_present")
        or (yardstick.get("bit_stream_length") or 0) > 0
        or (yardstick.get("symbol_stream_length") or 0) > 0
        or yardstick.get("decoded_fields_present")
    )


def _readiness_for_sample(sample: dict[str, Any], expected_id: str | None) -> dict[str, Any]:
    rtl433 = sample["rtl433"]
    yardstick = sample["yardstick"]
    artifact_availability = sample["artifact_availability"]

    lilygo_semantic_fifo_present = bool(artifact_availability["lilygo"].get("semantic_fifo_artifact"))
    rtl433_decode_present = bool(artifact_availability["rtl433"]["decoded"])
    yardstick_transport_present = _transport_artifact_present(yardstick)

    expected_id_match = None
    if expected_id is not None and rtl433.get("id") is not None:
        expected_id_match = str(rtl433.get("id")).lower() == expected_id.lower()

    rtl433_id_vs_yardstick_decoded_id = None
    semantic_summary = sample.get("semantic_summary", {})
    yardstick_decoded_id = semantic_summary.get("yardstick_decoded_id")
    if rtl433.get("id") and yardstick_decoded_id:
        rtl433_id_vs_yardstick_decoded_id = str(rtl433["id"]).lower() == str(yardstick_decoded_id).lower()

    sample_has_all_fifo_inputs = lilygo_semantic_fifo_present and rtl433_decode_present and yardstick_transport_present
    yardstick_decode_failure_is_nonblocking = yardstick_transport_present and yardstick.get("decode_success") is False

    readiness_reasons: list[str] = []
    if not sample["collection_valid"]:
        readiness_reasons.append("collection_invalid")
    if not lilygo_semantic_fifo_present:
        readiness_reasons.append("missing_lilygo_semantic_fifo")
    if not rtl433_decode_present:
        readiness_reasons.append("missing_rtl433_decode")
    if not yardstick_transport_present:
        readiness_reasons.append("missing_yardstick_transport")
    if expected_id_match is False:
        readiness_reasons.append("rtl433_expected_id_mismatch")
    if yardstick_decode_failure_is_nonblocking:
        readiness_reasons.append("yardstick_decode_failure_nonblocking")

    if sample["collection_valid"] and sample_has_all_fifo_inputs:
        comparison_ready = "YES"
    elif (
        sample["collection_valid"]
        and lilygo_semantic_fifo_present
        and (
            (rtl433_decode_present and not yardstick_transport_present)
            or (yardstick_transport_present and not rtl433_decode_present)
        )
    ):
        comparison_ready = "PARTIAL"
    else:
        comparison_ready = "NO"

    return {
        "sample_id": sample["sample_id"],
        "requested_action": sample["requested_action"],
        "collection_valid": sample["collection_valid"],
        "comparison_ready": comparison_ready,
        "readiness_reasons": readiness_reasons,
        "source_presence": {
            "lilygo_semantic_fifo_present": lilygo_semantic_fifo_present,
            "rtl433_decode_present": rtl433_decode_present,
            "yardstick_transport_present": yardstick_transport_present,
        },
        "lilygo_quality": {
            "semantic_fifo_present": lilygo_semantic_fifo_present,
            "semantic_fifo_decode_success": sample.get("semantic_summary", {}).get("lilygo_fifo_decode_success"),
            "semantic_fifo_matches_rtl433": sample.get("semantic_summary", {}).get("lilygo_fifo_matches_rtl433"),
        },
        "rtl433_semantic": {
            "id": rtl433.get("id"),
            "cmd1": rtl433.get("cmd1"),
            "cmd2": rtl433.get("cmd2"),
            "err1": rtl433.get("err1"),
            "err2": rtl433.get("err2"),
            "power": rtl433.get("power"),
            "flame": rtl433.get("flame"),
            "fan": rtl433.get("fan"),
            "integrity": rtl433.get("integrity"),
            "expected_remote_id_match": expected_id_match,
        },
        "yardstick_transport": {
            "raw_payload_hex_present": yardstick.get("raw_payload_hex_present"),
            "raw_payload_hex_length": yardstick.get("raw_payload_hex_length"),
            "payload_length_bytes": yardstick.get("payload_length_bytes"),
            "bit_stream_length": yardstick.get("bit_stream_length"),
            "symbol_stream_length": yardstick.get("symbol_stream_length"),
            "decode_success": yardstick.get("decode_success"),
            "decode_failure_reason": yardstick.get("decode_failure_reason"),
            "best_failure_reason": yardstick.get("best_failure_reason"),
            "repeat_count": yardstick.get("repeat_count"),
            "candidate_count": yardstick.get("candidate_count"),
            "selected_bit_offset": yardstick.get("selected_bit_offset"),
            "selected_symbol_offset": yardstick.get("selected_symbol_offset"),
            "transport_artifact_present": yardstick_transport_present,
        },
        "cross_source_basic_consistency": {
            "rtl433_id_vs_yardstick_decoded_id": rtl433_id_vs_yardstick_decoded_id,
            "rtl433_semantic_present_vs_yardstick_transport_present": rtl433_decode_present
            and yardstick_transport_present,
            "sample_has_all_fifo_inputs": sample_has_all_fifo_inputs,
            "yardstick_decode_failure_is_nonblocking": yardstick_decode_failure_is_nonblocking,
        },
        "recommended_for_stage5c": comparison_ready == "YES",
    }


def build_session_comparison_report(
    session_report: dict[str, Any],
    *,
    expected_id: str | None,
) -> dict[str, Any]:
    samples = [_readiness_for_sample(sample, expected_id) for sample in session_report["samples"]]
    readiness_counts = Counter(sample["comparison_ready"] for sample in samples)
    lilygo_warning_counts: Counter[str] = Counter()
    yardstick_failure_reason_counts: Counter[str] = Counter()
    rtl433_id_counts: Counter[str] = Counter()
    command_state_distribution: Counter[str] = Counter()

    recommended_samples: list[str] = []
    excluded_samples: list[dict[str, Any]] = []

    for sample in samples:
        lilygo_quality = sample["lilygo_quality"]
        if not lilygo_quality["semantic_fifo_present"]:
            lilygo_warning_counts["missing_semantic_fifo"] += 1
        if lilygo_quality.get("semantic_fifo_matches_rtl433") is False:
            lilygo_warning_counts["semantic_fifo_rtl433_mismatch"] += 1

        yardstick_transport = sample["yardstick_transport"]
        if yardstick_transport["best_failure_reason"]:
            yardstick_failure_reason_counts[str(yardstick_transport["best_failure_reason"])] += 1

        rtl433_semantic = sample["rtl433_semantic"]
        if rtl433_semantic["id"]:
            rtl433_id_counts[str(rtl433_semantic["id"])] += 1
        command_state_distribution[
            f"{sample['requested_action']}|power={rtl433_semantic['power']}|flame={rtl433_semantic['flame']}|fan={rtl433_semantic['fan']}"
        ] += 1

        if sample["recommended_for_stage5c"]:
            recommended_samples.append(sample["sample_id"])
        else:
            excluded_samples.append(
                {
                    "sample_id": sample["sample_id"],
                    "comparison_ready": sample["comparison_ready"],
                    "reasons": sample["readiness_reasons"],
                }
            )

    return {
        "schema_version": COMPARISON_SCHEMA_VERSION,
        "session_id": session_report["session_id"],
        "session_path": session_report["session_path"],
        "expected_id": expected_id,
        "selected_collectors": session_report["selected_collectors"],
        "collector_modes": session_report["collector_modes"],
        "total_samples": session_report["total_sample_count"],
        "valid_samples": session_report["valid_sample_count"],
        "comparison_ready_yes_count": readiness_counts.get("YES", 0),
        "comparison_ready_partial_count": readiness_counts.get("PARTIAL", 0),
        "comparison_ready_no_count": readiness_counts.get("NO", 0),
        "samples": samples,
        "lilygo_quality_warning_counts": dict(sorted(lilygo_warning_counts.items())),
        "yardstick_failure_reason_counts": dict(sorted(yardstick_failure_reason_counts.items())),
        "rtl433_id_counts": dict(sorted(rtl433_id_counts.items())),
        "command_state_distribution": dict(sorted(command_state_distribution.items())),
        "samples_recommended_for_stage5c": recommended_samples,
        "samples_excluded": excluded_samples,
        "notes": [
            "YardStick decode failure is nonblocking when transport artifacts are present.",
        ],
    }


def render_session_comparison_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Multi-Source Comparison Report")
    lines.append("")
    lines.append(f"- Session ID: `{report['session_id']}`")
    lines.append(f"- Session Path: `{report['session_path']}`")
    lines.append(
        f"- Expected rtl_433 ID: `{report['expected_id']}`" if report["expected_id"] else "- Expected rtl_433 ID: none"
    )
    lines.append(f"- Ready YES: {report['comparison_ready_yes_count']}")
    lines.append(f"- Ready PARTIAL: {report['comparison_ready_partial_count']}")
    lines.append(f"- Ready NO: {report['comparison_ready_no_count']}")
    lines.append("")
    lines.append("## Per-Sample Readiness")
    lines.append("")
    lines.append("| Sample | Action | Ready | LilyGO | rtl_433 | YardStick | Reasons |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for sample in report["samples"]:
        source_presence = sample["source_presence"]
        lilygo_presence = "fifo" if source_presence.get("lilygo_semantic_fifo_present") else "no"
        lines.append(
            "| "
            f"{sample['sample_id']} | {sample['requested_action']} | {sample['comparison_ready']} | "
            f"{lilygo_presence} | "
            f"{'yes' if source_presence['rtl433_decode_present'] else 'no'} | "
            f"{'yes' if source_presence['yardstick_transport_present'] else 'no'} | "
            f"{', '.join(sample['readiness_reasons']) or '-'} |"
        )
    lines.append("")
    lines.append("## Recommended For Stage 5C")
    lines.append("")
    if report["samples_recommended_for_stage5c"]:
        for sample_id in report["samples_recommended_for_stage5c"]:
            lines.append(f"- `{sample_id}`")
    else:
        lines.append("- None")
    lines.append("")
    lines.append("## Excluded Samples")
    lines.append("")
    if report["samples_excluded"]:
        for item in report["samples_excluded"]:
            lines.append(f"- `{item['sample_id']}`: {', '.join(item['reasons']) or item['comparison_ready']}")
    else:
        lines.append("- None")
    lines.append("")
    lines.append("## Warning Summary")
    lines.append("")
    lines.append("- YardStick decode failure is nonblocking when transport artifacts are present.")
    if report["lilygo_quality_warning_counts"]:
        for key, value in report["lilygo_quality_warning_counts"].items():
            lines.append(f"- LilyGO `{key}`: {value}")
    if report["yardstick_failure_reason_counts"]:
        for key, value in report["yardstick_failure_reason_counts"].items():
            lines.append(f"- YardStick `{key}`: {value}")
    if report["rtl433_id_counts"]:
        for key, value in report["rtl433_id_counts"].items():
            lines.append(f"- rtl_433 id `{key}`: {value}")
    lines.append("")
    return "\n".join(lines)


def write_session_comparison_report(
    report: dict[str, Any],
    *,
    output_dir: str | Path,
    json_only: bool,
    markdown_only: bool,
) -> dict[str, Path]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    if not markdown_only:
        json_path = output_path / "multi_source_comparison_report.json"
        json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written["json"] = json_path
    if not json_only:
        markdown_path = output_path / "multi_source_comparison_report.md"
        markdown_path.write_text(render_session_comparison_markdown(report), encoding="utf-8")
        written["markdown"] = markdown_path
    return written
