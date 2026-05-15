"""Load and summarize multi-source capture session artifacts.

This decision-gating module separates canonical semantic artifacts from raw and
debug capture context so session reports do not accidentally promote diagnostic
data into semantic evidence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class LoadedSample:
    sample_dir: Path
    sample_manifest: dict[str, Any]
    quick_validation: dict[str, Any] | None
    lilygo_capture_export: dict[str, Any] | None
    lilygo_semantic_fifo_artifact: dict[str, Any] | None
    lilygo_raw_syslog_present: bool
    rtl433_decoded: dict[str, Any] | None
    rtl433_parser_debug: dict[str, Any] | None
    rtl433_raw_stdout_present: bool
    yardstick_diagnostic: dict[str, Any] | None
    yardstick_raw_payload_hex_present: bool
    yardstick_bit_stream_present: bool
    yardstick_symbol_stream_present: bool
    yardstick_decoded: dict[str, Any] | None
    yardstick_collector_debug: dict[str, Any] | None


@dataclass(frozen=True)
class LoadedSession:
    session_dir: Path
    session_manifest: dict[str, Any]
    run_summary: dict[str, Any] | None
    samples: list[LoadedSample]


def _read_json(path: Path, *, required: bool) -> dict[str, Any] | None:
    if not path.exists():
        if required:
            raise FileNotFoundError(f"Required JSON file not found: {path}")
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _read_text(path: Path) -> str | None:
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def _text_length(value: Any) -> int:
    if value is None:
        return 0
    return len(str(value))


def _load_sample(sample_dir: Path) -> LoadedSample:
    sample_manifest = _read_json(sample_dir / "sample_manifest.json", required=True)
    quick_validation = _read_json(sample_dir / "analysis" / "quick_validation.json", required=False)
    if quick_validation is None:
        quick_validation = _read_json(sample_dir / "quick_validation.json", required=False)

    lilygo_dir = sample_dir / "lilygo"
    rtl433_dir = sample_dir / "rtl433"
    yardstick_dir = sample_dir / "yardstick"

    return LoadedSample(
        sample_dir=sample_dir,
        sample_manifest=sample_manifest,
        quick_validation=quick_validation,
        lilygo_capture_export=_read_json(lilygo_dir / "capture_export.json", required=False),
        lilygo_semantic_fifo_artifact=_read_json(lilygo_dir / "semantic_fifo_artifact.json", required=False),
        lilygo_raw_syslog_present=(lilygo_dir / "raw_syslog.log").exists(),
        rtl433_decoded=_read_json(rtl433_dir / "decoded.json", required=False),
        rtl433_parser_debug=_read_json(rtl433_dir / "parser_debug.json", required=False),
        rtl433_raw_stdout_present=(rtl433_dir / "raw_stdout.log").exists(),
        yardstick_diagnostic=_read_json(yardstick_dir / "diagnostic.json", required=False),
        yardstick_raw_payload_hex_present=(yardstick_dir / "raw_payload.hex").exists(),
        yardstick_bit_stream_present=(yardstick_dir / "bit_stream.txt").exists(),
        yardstick_symbol_stream_present=(yardstick_dir / "symbol_stream.txt").exists(),
        yardstick_decoded=_read_json(yardstick_dir / "decoded.json", required=False),
        yardstick_collector_debug=_read_json(yardstick_dir / "collector_debug.json", required=False),
    )


def load_session_report_input(session_dir: str | Path) -> LoadedSession:
    session_path = Path(session_dir)
    if not session_path.exists():
        raise FileNotFoundError(f"Session directory not found: {session_path}")
    if not session_path.is_dir():
        raise ValueError(f"Session path is not a directory: {session_path}")

    session_manifest = _read_json(session_path / "session_manifest.json", required=True)
    run_summary = _read_json(session_path / "run_summary.json", required=False)

    sample_dirs = sorted(
        path for path in session_path.iterdir() if path.is_dir() and (path / "sample_manifest.json").exists()
    )
    samples = [_load_sample(sample_dir) for sample_dir in sample_dirs]
    return LoadedSession(
        session_dir=session_path,
        session_manifest=session_manifest,
        run_summary=run_summary,
        samples=samples,
    )


def _artifact_presence(sample: LoadedSample) -> dict[str, dict[str, bool]]:
    return {
        "lilygo": {
            "capture_export": sample.lilygo_capture_export is not None,
            "semantic_fifo_artifact": sample.lilygo_semantic_fifo_artifact is not None,
            "raw_syslog": sample.lilygo_raw_syslog_present,
        },
        "rtl433": {
            "decoded": sample.rtl433_decoded is not None,
            "parser_debug": sample.rtl433_parser_debug is not None,
            "raw_stdout": sample.rtl433_raw_stdout_present,
        },
        "yardstick": {
            "diagnostic": sample.yardstick_diagnostic is not None,
            "raw_payload_hex": sample.yardstick_raw_payload_hex_present,
            "bit_stream": sample.yardstick_bit_stream_present,
            "symbol_stream": sample.yardstick_symbol_stream_present,
            "decoded": sample.yardstick_decoded is not None,
        },
    }


def _source_status_summary(sample: LoadedSample) -> dict[str, Any]:
    quick_validation = sample.quick_validation or {}
    source_summary = quick_validation.get("source_summary")
    if source_summary:
        return source_summary

    collector_results = sample.sample_manifest.get("collector_results", {})
    summary: dict[str, Any] = {}
    for source_name, result in collector_results.items():
        summary[source_name] = {
            "selected": result.get("selected", False),
            "complete": result.get("complete", False),
            "valid": result.get("valid", False),
            "reject_reason": result.get("reject_reason"),
            "artifact_dir": result.get("artifact_dir"),
            "mode": result.get("mode"),
            "key_artifacts_present": {
                artifact.get("kind", "unknown"): True for artifact in result.get("artifact_paths", [])
            },
        }
    return summary


def _warning_list(sample: LoadedSample, normalized: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    if not normalized["collection_valid"]:
        warnings.append("invalid_sample")

    if not normalized["artifact_availability"]["lilygo"]["capture_export"]:
        warnings.append("missing_lilygo_capture_export")
    if not normalized["artifact_availability"]["lilygo"]["semantic_fifo_artifact"]:
        warnings.append("lilygo_missing_semantic_fifo")

    rtl433 = normalized["rtl433"]
    if normalized["artifact_availability"]["rtl433"]["decoded"] and rtl433["model"] != "Proflame2-Remote":
        warnings.append("rtl433_wrong_model")
    if not normalized["artifact_availability"]["rtl433"]["decoded"]:
        warnings.append("rtl433_missing_decode")

    yardstick = normalized["yardstick"]
    if not (
        yardstick["raw_payload_hex_present"]
        or yardstick["bit_stream_length"] > 0
        or yardstick["symbol_stream_length"] > 0
        or yardstick["decoded_fields_present"]
    ):
        warnings.append("yardstick_no_useful_artifact")
    if yardstick["decode_success"] is False:
        warnings.append("yardstick_decode_failure")
    return warnings


def _normalize_sample(sample: LoadedSample) -> dict[str, Any]:
    identity = sample.sample_manifest.get("identity", {})
    quick_validation = sample.quick_validation or {}
    source_summary = _source_status_summary(sample)
    artifact_availability = _artifact_presence(sample)

    lilygo = sample.lilygo_capture_export or {}
    lilygo_semantic_fifo = sample.lilygo_semantic_fifo_artifact or lilygo.get("semantic_fifo_artifact") or {}
    rtl433 = sample.rtl433_decoded or {}
    yardstick = sample.yardstick_diagnostic or {}

    normalized = {
        "sample_id": identity.get("sample_id"),
        "sample_index": identity.get("sample_index"),
        "attempt_index": identity.get("attempt_index"),
        "requested_action": identity.get("requested_action"),
        "collection_valid": bool(quick_validation.get("collection_valid", identity.get("collection_valid", False))),
        "source_status_summary": source_summary,
        "artifact_availability": artifact_availability,
        "pairing_summary": quick_validation.get("pairing_summary", {}),
        "semantic_summary": quick_validation.get("semantic_summary", {}),
        "lilygo": {
            "semantic_fifo_present": bool(lilygo_semantic_fifo),
            "semantic_fifo_decode_success": lilygo_semantic_fifo.get("decode_success"),
            "semantic_fifo_remote_id": lilygo_semantic_fifo.get("remote_id"),
            "semantic_fifo_cmd1": lilygo_semantic_fifo.get("cmd1"),
            "semantic_fifo_cmd2": lilygo_semantic_fifo.get("cmd2"),
            "semantic_fifo_err1": lilygo_semantic_fifo.get("err1"),
            "semantic_fifo_err2": lilygo_semantic_fifo.get("err2"),
        },
        "rtl433": {
            "id": rtl433.get("id"),
            "cmd1": rtl433.get("cmd1"),
            "cmd2": rtl433.get("cmd2"),
            "err1": rtl433.get("err1"),
            "err2": rtl433.get("err2"),
            "power": rtl433.get("power"),
            "flame": rtl433.get("flame"),
            "fan": rtl433.get("fan"),
            "integrity": rtl433.get("integrity"),
            "model": rtl433.get("model"),
        },
        "yardstick": {
            "raw_payload_hex_present": bool(yardstick.get("raw_payload_hex")),
            "raw_payload_hex_length": _text_length(yardstick.get("raw_payload_hex")),
            "payload_length_bytes": yardstick.get("payload_length_bytes"),
            "bit_stream_length": _text_length(yardstick.get("bit_stream")),
            "symbol_stream_length": _text_length(yardstick.get("symbol_stream")),
            "decode_success": yardstick.get("decode_success"),
            "decode_failure_reason": yardstick.get("decode_failure_reason"),
            "best_failure_reason": yardstick.get("best_failure_reason"),
            "repeat_count": yardstick.get("repeat_count"),
            "selected_bit_offset": yardstick.get("selected_bit_offset"),
            "selected_symbol_offset": yardstick.get("selected_symbol_offset"),
            "candidate_count": yardstick.get("candidate_count"),
            "decoded_fields_present": bool(yardstick.get("decoded_fields") or sample.yardstick_decoded),
        },
    }
    normalized["warnings"] = _warning_list(sample, normalized)
    return normalized


def _collector_modes(session: LoadedSession) -> dict[str, Any]:
    return session.session_manifest.get("collector_modes", {})


def build_session_report(loaded_session: LoadedSession, *, include_invalid: bool) -> dict[str, Any]:
    normalized_samples = [_normalize_sample(sample) for sample in loaded_session.samples]
    included_samples = [sample for sample in normalized_samples if include_invalid or sample["collection_valid"]]

    warning_counts: dict[str, int] = {}
    for sample in included_samples:
        for warning in sample["warnings"]:
            warning_counts[warning] = warning_counts.get(warning, 0) + 1

    report = {
        "schema_version": SCHEMA_VERSION,
        "session_id": loaded_session.session_manifest.get("session_id"),
        "session_path": str(loaded_session.session_dir),
        "selected_collectors": loaded_session.session_manifest.get("selected_collectors", []),
        "collector_modes": _collector_modes(loaded_session),
        "valid_sample_count": sum(1 for sample in included_samples if sample["collection_valid"]),
        "invalid_sample_count": sum(1 for sample in included_samples if not sample["collection_valid"]),
        "total_sample_count": len(included_samples),
        "all_sample_count": len(normalized_samples),
        "include_invalid": include_invalid,
        "run_summary": loaded_session.run_summary,
        "samples": included_samples,
        "warning_counts": warning_counts,
        "analysis_valid": None,
    }
    return report


def render_session_report_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Multi-Source Session Report")
    lines.append("")
    lines.append(f"- Session ID: `{report['session_id']}`")
    lines.append(f"- Session Path: `{report['session_path']}`")
    lines.append(f"- Selected Collectors: {', '.join(report['selected_collectors']) or 'none'}")
    lines.append(f"- Valid Samples: {report['valid_sample_count']}")
    lines.append(f"- Invalid Samples: {report['invalid_sample_count']}")
    lines.append(f"- Included Samples: {report['total_sample_count']} of {report['all_sample_count']}")
    lines.append("")
    lines.append("## Samples")
    lines.append("")
    lines.append("| Sample | Action | Valid | LilyGO | rtl_433 | YardStick | Warnings |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for sample in report["samples"]:
        lines.append(
            "| "
            f"{sample['sample_id']} | {sample['requested_action']} | "
            f"{'yes' if sample['collection_valid'] else 'no'} | "
            f"{'yes' if sample['artifact_availability']['lilygo']['capture_export'] else 'no'} | "
            f"{'yes' if sample['artifact_availability']['rtl433']['decoded'] else 'no'} | "
            f"{'yes' if sample['artifact_availability']['yardstick']['diagnostic'] else 'no'} | "
            f"{', '.join(sample['warnings']) or '-'} |"
        )
    lines.append("")
    lines.append("## Source Artifact Matrix")
    lines.append("")
    for sample in report["samples"]:
        lines.append(f"### {sample['sample_id']}")
        lines.append("")
        lines.append(
            f"- LilyGO: semantic_fifo_present={sample['lilygo']['semantic_fifo_present']}, "
            f"decode_success={sample['lilygo']['semantic_fifo_decode_success']}, "
            f"remote_id={sample['lilygo']['semantic_fifo_remote_id']}, "
            f"cmd1/cmd2={sample['lilygo']['semantic_fifo_cmd1']}/{sample['lilygo']['semantic_fifo_cmd2']}"
        )
        lines.append(
            f"- rtl_433: id={sample['rtl433']['id']}, power={sample['rtl433']['power']}, "
            f"flame={sample['rtl433']['flame']}, fan={sample['rtl433']['fan']}, "
            f"integrity={sample['rtl433']['integrity']}"
        )
        lines.append(
            f"- YardStick: payload_bytes={sample['yardstick']['payload_length_bytes']}, "
            f"bit_stream_length={sample['yardstick']['bit_stream_length']}, "
            f"symbol_stream_length={sample['yardstick']['symbol_stream_length']}, "
            f"decode_success={sample['yardstick']['decode_success']}, "
            f"best_failure_reason={sample['yardstick']['best_failure_reason']}"
        )
        lines.append("")
    lines.append("## Warning Summary")
    lines.append("")
    if report["warning_counts"]:
        for warning, count in sorted(report["warning_counts"].items()):
            lines.append(f"- `{warning}`: {count}")
    else:
        lines.append("- None")
    lines.append("")
    return "\n".join(lines)


def write_session_report(
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
        json_path = output_path / "multi_source_session_report.json"
        json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written["json"] = json_path

    if not json_only:
        markdown_path = output_path / "multi_source_session_report.md"
        markdown_path.write_text(render_session_report_markdown(report), encoding="utf-8")
        written["markdown"] = markdown_path

    return written
