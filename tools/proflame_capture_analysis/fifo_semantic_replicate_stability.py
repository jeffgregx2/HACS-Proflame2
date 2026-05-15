"""Evaluate packet-owned FIFO semantic replicate stability.

This decision-gating module compares only accepted LilyGO FIFO semantic
artifacts and canonical YardStick semantic artifacts within exact rtl_433
groups; raw FIFO bytes and debug captures are unsuitable for semantic scoring.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path
from statistics import mean
from typing import Any

from custom_components.proflame2.rf.artifacts import (
    is_lilygo_fifo_semantic_artifact,
    is_yardstick_semantic_artifact,
)

FIFO_SEMANTIC_REPLICATE_SCHEMA_VERSION = 1
SEMANTIC_KEY_FIELDS = ("power", "flame", "fan", "cmd1", "cmd2", "err1", "err2")
DECODE_FIELD_PAIRS = (
    ("remote_id", "id"),
    ("cmd1", "cmd1"),
    ("cmd2", "cmd2"),
    ("err1", "err1"),
    ("err2", "err2"),
)


@dataclass(frozen=True)
class FifoSemanticSample:
    sample_id: str
    sample_dir: Path
    source_sample_dir: Path | None
    requested_action: str | None
    rtl433: dict[str, Any]
    semantic_key: tuple[Any, ...]
    lilygo_artifact: dict[str, Any] | None
    yardstick_artifact: dict[str, Any] | None
    warnings: tuple[str, ...]


def _load_json(path: Path, *, required: bool = False) -> dict[str, Any] | None:
    if not path.exists():
        if required:
            raise FileNotFoundError(f"Required JSON file not found: {path}")
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _load_text(path: Path) -> str | None:
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8").strip()


def _sample_dirs(root: Path) -> list[Path]:
    if (root / "workspace_manifest.json").exists():
        return sorted(
            path for path in root.iterdir() if path.is_dir() and (path / "sample_alignment_manifest.json").exists()
        )
    if (root / "session_manifest.json").exists():
        return sorted(path for path in root.iterdir() if path.is_dir() and (path / "sample_manifest.json").exists())
    return sorted(path for path in root.iterdir() if path.is_dir())


def _semantic_key(semantic: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(semantic.get(field) for field in SEMANTIC_KEY_FIELDS)


def _semantic_key_label(key: tuple[Any, ...]) -> str:
    return ",".join(f"{field}={value}" for field, value in zip(SEMANTIC_KEY_FIELDS, key, strict=False))


def _source_sample_dir(sample_dir: Path, manifest: dict[str, Any]) -> Path | None:
    value = manifest.get("source_sample_dir")
    if isinstance(value, str) and value:
        path = Path(value)
        if path.exists():
            return path
    return None


def _load_rtl433(sample_dir: Path) -> dict[str, Any]:
    return _load_json(sample_dir / "rtl433_semantic.json") or _load_json(sample_dir / "rtl433" / "decoded.json") or {}


def _load_lilygo_fifo(sample_dir: Path, source_sample_dir: Path | None) -> dict[str, Any] | None:
    return (
        _load_json(sample_dir / "lilygo_semantic_fifo_artifact.json")
        or _load_json(sample_dir / "lilygo" / "semantic_fifo_artifact.json")
        or (_load_json(source_sample_dir / "lilygo" / "semantic_fifo_artifact.json") if source_sample_dir else None)
    )


def _load_yardstick_semantic(sample_dir: Path, source_sample_dir: Path | None) -> dict[str, Any] | None:
    artifact = (
        _load_json(sample_dir / "yardstick_semantic_artifact.json")
        or _load_json(sample_dir / "yardstick" / "semantic_artifact.json")
        or (_load_json(source_sample_dir / "yardstick" / "semantic_artifact.json") if source_sample_dir else None)
    )
    if artifact:
        return artifact
    symbol_stream = _load_text(sample_dir / "yardstick_semantic_symbol_stream.txt")
    bit_stream = _load_text(sample_dir / "yardstick_semantic_bit_stream.txt")
    if symbol_stream or bit_stream:
        return {
            "artifact_class": "semantic",
            "semantic_comparable": True,
            "decode_success": True,
            "candidate_symbol_stream": symbol_stream,
            "candidate_bit_stream": bit_stream,
        }
    return None


def load_fifo_semantic_samples(root_dir: str | Path) -> list[FifoSemanticSample]:
    root = Path(root_dir)
    samples: list[FifoSemanticSample] = []
    for sample_dir in _sample_dirs(root):
        manifest = (
            _load_json(sample_dir / "sample_alignment_manifest.json")
            or _load_json(sample_dir / "sample_manifest.json")
            or {}
        )
        identity = manifest.get("identity", {}) if "identity" in manifest else manifest
        source_dir = _source_sample_dir(sample_dir, manifest)
        rtl433 = _load_rtl433(sample_dir)
        warnings: list[str] = []
        if not rtl433:
            warnings.append("missing_rtl433_semantic")
        lilygo_artifact = _load_lilygo_fifo(sample_dir, source_dir)
        yardstick_artifact = _load_yardstick_semantic(sample_dir, source_dir)
        if lilygo_artifact is None:
            warnings.append("missing_lilygo_semantic_fifo_artifact")
        if yardstick_artifact is None:
            warnings.append("missing_yardstick_semantic_artifact")
        samples.append(
            FifoSemanticSample(
                sample_id=str(identity.get("sample_id") or sample_dir.name),
                sample_dir=sample_dir,
                source_sample_dir=source_dir,
                requested_action=identity.get("requested_action") or manifest.get("requested_action"),
                rtl433=rtl433,
                semantic_key=_semantic_key(rtl433),
                lilygo_artifact=lilygo_artifact,
                yardstick_artifact=yardstick_artifact,
                warnings=tuple(warnings),
            )
        )
    return samples


def _norm_hex(value: Any, *, width: int = 2) -> str | None:
    if value is None:
        return None
    if isinstance(value, int):
        return f"{value:0{width}x}"
    text = str(value).strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    return text.zfill(width) if text and all(ch in "0123456789abcdef" for ch in text) else text


def _decoded_fields_match_lilygo(artifact: dict[str, Any] | None, rtl433: dict[str, Any]) -> bool:
    if not isinstance(artifact, dict):
        return False
    fields = artifact.get("decoded_fields") if isinstance(artifact.get("decoded_fields"), dict) else artifact
    for artifact_field, rtl_field in DECODE_FIELD_PAIRS:
        artifact_value = (
            fields.get(artifact_field)
            if artifact_field == "remote_id"
            else fields.get(artifact_field, artifact.get(artifact_field))
        )
        rtl_value = rtl433.get(rtl_field)
        if artifact_field == "remote_id":
            if _norm_hex(artifact_value, width=6) != _norm_hex(rtl_value, width=6):
                return False
        elif _norm_hex(artifact_value) != _norm_hex(rtl_value):
            return False
    return True


def _decoded_fields_match_yardstick(artifact: dict[str, Any] | None, rtl433: dict[str, Any]) -> bool:
    if not isinstance(artifact, dict):
        return False
    for artifact_field, rtl_field in DECODE_FIELD_PAIRS:
        artifact_value = artifact.get("id") if artifact_field == "remote_id" else artifact.get(artifact_field)
        if artifact_value is None and artifact_field == "remote_id":
            artifact_value = artifact.get("remote_id")
        rtl_value = rtl433.get(rtl_field)
        if artifact_field == "remote_id":
            if _norm_hex(artifact_value, width=6) != _norm_hex(rtl_value, width=6):
                return False
        elif artifact_value is not None and _norm_hex(artifact_value) != _norm_hex(rtl_value):
            return False
    return True


def _valid_lilygo_artifact(artifact: dict[str, Any] | None) -> bool:
    return is_lilygo_fifo_semantic_artifact(artifact) and artifact.get("packet_normalized") is True


def _valid_yardstick_artifact(artifact: dict[str, Any] | None) -> bool:
    return is_yardstick_semantic_artifact(artifact)


def _hex_to_bits(value: str | None) -> str:
    if not value:
        return ""
    cleaned = "".join(ch for ch in value.strip().lower() if ch in "0123456789abcdef")
    return "".join(f"{int(ch, 16):04b}" for ch in cleaned)


def _similarity(left: str | None, right: str | None) -> float | None:
    if left is None or right is None:
        return None
    if left == "" and right == "":
        return 1.0
    denom = max(len(left), len(right))
    if denom == 0:
        return None
    shared = min(len(left), len(right))
    matches = sum(1 for index in range(shared) if left[index] == right[index])
    return matches / denom


def _pairwise_stats(values: list[str | None]) -> dict[str, Any]:
    scores = [score for left, right in combinations(values, 2) if (score := _similarity(left, right)) is not None]
    return {
        "pair_count": len(scores),
        "min_similarity": round(min(scores), 6) if scores else None,
        "mean_similarity": round(mean(scores), 6) if scores else None,
    }


def _range(values: list[Any]) -> dict[str, Any]:
    non_null = [value for value in values if value is not None]
    return {
        "values": values,
        "unique_values": sorted(set(non_null)),
        "min": min(non_null) if non_null else None,
        "max": max(non_null) if non_null else None,
    }


def _group_quality(samples: list[FifoSemanticSample], *, threshold: float) -> dict[str, Any]:
    lilygo_symbols: list[str | None] = []
    lilygo_bits: list[str | None] = []
    yardstick_symbols: list[str | None] = []
    yardstick_bits: list[str | None] = []
    lilygo_repeat_counts: list[Any] = []
    lilygo_confidences: list[Any] = []
    lilygo_offsets: list[Any] = []
    yardstick_repeat_counts: list[Any] = []
    yardstick_confidences: list[Any] = []
    yardstick_offsets: list[Any] = []
    sample_rows: list[dict[str, Any]] = []
    mismatch_count = 0
    lilygo_mismatch_count = 0
    yardstick_mismatch_count = 0
    artifact_warning_count = 0

    for sample in samples:
        lilygo = sample.lilygo_artifact or {}
        yardstick = sample.yardstick_artifact or {}
        lilygo_candidate = lilygo.get("candidate") if isinstance(lilygo.get("candidate"), dict) else {}
        lilygo_valid = _valid_lilygo_artifact(lilygo)
        yardstick_valid = _valid_yardstick_artifact(yardstick)
        lilygo_matches = _decoded_fields_match_lilygo(lilygo, sample.rtl433)
        yardstick_matches = _decoded_fields_match_yardstick(yardstick, sample.rtl433)
        if not lilygo_matches:
            lilygo_mismatch_count += 1
        if not yardstick_matches:
            yardstick_mismatch_count += 1
        if not lilygo_matches or not yardstick_matches:
            mismatch_count += 1
        if not lilygo_valid or not yardstick_valid:
            artifact_warning_count += 1

        lilygo_symbols.append(lilygo_candidate.get("symbols"))
        lilygo_bits.append(_hex_to_bits(lilygo_candidate.get("raw_slice_hex")))
        yardstick_symbols.append(yardstick.get("candidate_symbol_stream"))
        yardstick_bits.append(yardstick.get("candidate_bit_stream"))
        lilygo_repeat_counts.append(lilygo_candidate.get("repeat_count"))
        lilygo_confidences.append(lilygo_candidate.get("confidence"))
        lilygo_offsets.append(lilygo_candidate.get("absolute_bit_offset"))
        yardstick_repeat_counts.append(yardstick.get("repeat_count"))
        yardstick_confidences.append(yardstick.get("candidate_confidence"))
        yardstick_offsets.append(yardstick.get("candidate_absolute_bit_offset"))

        sample_rows.append(
            {
                "sample_id": sample.sample_id,
                "requested_action": sample.requested_action,
                "lilygo_artifact_valid": lilygo_valid,
                "yardstick_artifact_valid": yardstick_valid,
                "lilygo_matches_rtl433": lilygo_matches,
                "yardstick_matches_rtl433": yardstick_matches,
                "lilygo_repeat_count": lilygo_candidate.get("repeat_count"),
                "lilygo_confidence": lilygo_candidate.get("confidence"),
                "lilygo_absolute_bit_offset": lilygo_candidate.get("absolute_bit_offset"),
                "yardstick_repeat_count": yardstick.get("repeat_count"),
                "yardstick_confidence": yardstick.get("candidate_confidence"),
                "yardstick_absolute_bit_offset": yardstick.get("candidate_absolute_bit_offset"),
                "warnings": list(sample.warnings),
            }
        )

    lilygo_symbol_stats = _pairwise_stats(lilygo_symbols)
    lilygo_bit_stats = _pairwise_stats(lilygo_bits)
    yardstick_symbol_stats = _pairwise_stats(yardstick_symbols)
    yardstick_bit_stats = _pairwise_stats(yardstick_bits)
    lilygo_min = lilygo_symbol_stats["min_similarity"]
    yardstick_min = (
        min(
            score
            for score in (yardstick_symbol_stats["min_similarity"], yardstick_bit_stats["min_similarity"])
            if score is not None
        )
        if any(
            score is not None
            for score in (yardstick_symbol_stats["min_similarity"], yardstick_bit_stats["min_similarity"])
        )
        else None
    )

    return {
        "sample_count": len(samples),
        "samples": sample_rows,
        "lilygo_fifo": {
            "symbol_similarity": lilygo_symbol_stats,
            "bit_similarity_from_raw_slice_hex": lilygo_bit_stats,
            "repeat_count_stability": _range(lilygo_repeat_counts),
            "confidence_stability": _range(lilygo_confidences),
            "absolute_bit_offset_stability": _range(lilygo_offsets),
        },
        "yardstick_semantic": {
            "symbol_similarity": yardstick_symbol_stats,
            "bit_similarity": yardstick_bit_stats,
            "repeat_count_stability": _range(yardstick_repeat_counts),
            "confidence_stability": _range(yardstick_confidences),
            "absolute_bit_offset_stability": _range(yardstick_offsets),
        },
        "decoded_field_mismatch_count": mismatch_count,
        "lilygo_decoded_field_mismatch_count": lilygo_mismatch_count,
        "yardstick_decoded_field_mismatch_count": yardstick_mismatch_count,
        "artifact_warning_count": artifact_warning_count,
        "group_gate_passed": (
            len(samples) >= 2
            and mismatch_count == 0
            and artifact_warning_count == 0
            and lilygo_min is not None
            and lilygo_min >= threshold
            and yardstick_min is not None
            and yardstick_min >= threshold
        ),
    }


def _group_samples_by_exact_semantics(
    samples: list[FifoSemanticSample],
) -> dict[tuple[Any, ...], list[FifoSemanticSample]]:
    groups: dict[tuple[Any, ...], list[FifoSemanticSample]] = {}
    for sample in samples:
        if sample.semantic_key and all(value is not None for value in sample.semantic_key):
            groups.setdefault(sample.semantic_key, []).append(sample)
    return groups


def _split_repeated_groups(
    groups: dict[tuple[Any, ...], list[FifoSemanticSample]],
    *,
    min_group_size: int,
    similarity_threshold: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    repeated_groups: list[dict[str, Any]] = []
    insufficient_groups: list[dict[str, Any]] = []
    for key, group_samples in sorted(groups.items(), key=lambda item: _semantic_key_label(item[0])):
        group_report = {
            "semantic_key": dict(zip(SEMANTIC_KEY_FIELDS, key, strict=False)),
            "semantic_key_label": _semantic_key_label(key),
            **_group_quality(group_samples, threshold=similarity_threshold),
        }
        if len(group_samples) >= min_group_size:
            repeated_groups.append(group_report)
        else:
            insufficient_groups.append(group_report)
    return repeated_groups, insufficient_groups


def _yardstick_group_min(group: dict[str, Any]) -> float | None:
    scores = [
        score
        for score in (
            group["yardstick_semantic"]["symbol_similarity"]["min_similarity"],
            group["yardstick_semantic"]["bit_similarity"]["min_similarity"],
        )
        if score is not None
    ]
    return min(scores) if scores else None


def _fifo_semantic_replicate_pass_gate(
    *,
    repeated_groups: list[dict[str, Any]],
    min_group_size: int,
    similarity_threshold: float,
) -> dict[str, Any]:
    all_group_gates_passed = bool(repeated_groups) and all(group["group_gate_passed"] for group in repeated_groups)
    pass_gate = (
        len(repeated_groups) >= 2
        and all(group["sample_count"] >= min_group_size for group in repeated_groups)
        and all_group_gates_passed
    )
    mismatch_count = sum(group["decoded_field_mismatch_count"] for group in repeated_groups)
    artifact_warning_count = sum(group["artifact_warning_count"] for group in repeated_groups)

    if pass_gate:
        recommendation = "Stage 5AM: deprecate/remove LilyGO edge ownership path."
        failure_mode = None
    elif mismatch_count:
        recommendation = "Keep FIFO semantic validation guard active; investigate stale candidate selection."
        failure_mode = "stale_or_mismatched_fifo_candidate"
    elif artifact_warning_count:
        recommendation = "Fix missing/non-semantic artifacts before alignment."
        failure_mode = "artifact_quality"
    elif len(repeated_groups) < 2:
        recommendation = "Collect more exact semantic FIFO replicate groups."
        failure_mode = "insufficient_samples"
    elif not all_group_gates_passed:
        recommendation = "Investigate FIFO or YardStick semantic instability before alignment."
        failure_mode = "replicate_instability"
    else:
        recommendation = "Collect more exact semantic FIFO replicate groups."
        failure_mode = "insufficient_samples"

    return {
        "passed": pass_gate,
        "requires_at_least_2_exact_groups": len(repeated_groups) >= 2,
        "requires_min_group_size": all(group["sample_count"] >= min_group_size for group in repeated_groups),
        "requires_lilygo_similarity_gte_threshold": all(
            (group["lilygo_fifo"]["symbol_similarity"]["min_similarity"] or 0.0) >= similarity_threshold
            for group in repeated_groups
        ),
        "requires_yardstick_similarity_gte_threshold": (
            all(
                (yardstick_min is not None and yardstick_min >= similarity_threshold)
                for group in repeated_groups
                for yardstick_min in (_yardstick_group_min(group),)
            )
            if repeated_groups
            else False
        ),
        "requires_decoded_fields_match": mismatch_count == 0,
        "decoded_field_mismatch_count": mismatch_count,
        "artifact_warning_count": artifact_warning_count,
        "failure_mode": failure_mode,
        "recommendation": recommendation,
    }


def build_fifo_semantic_replicate_stability_report(
    root_dir: str | Path,
    *,
    expected_id: str = "3b3f02",
    min_group_size: int = 2,
    similarity_threshold: float = 0.95,
) -> dict[str, Any]:
    root = Path(root_dir)
    samples = load_fifo_semantic_samples(root)
    groups = _group_samples_by_exact_semantics(samples)
    repeated_groups, insufficient_groups = _split_repeated_groups(
        groups,
        min_group_size=min_group_size,
        similarity_threshold=similarity_threshold,
    )
    groups_with_preferred_size = sum(1 for group in repeated_groups if group["sample_count"] >= 3)
    pass_gate = _fifo_semantic_replicate_pass_gate(
        repeated_groups=repeated_groups,
        min_group_size=min_group_size,
        similarity_threshold=similarity_threshold,
    )

    return {
        "schema_version": FIFO_SEMANTIC_REPLICATE_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "input_path": str(root),
        "expected_id": expected_id,
        "min_group_size": min_group_size,
        "similarity_threshold": similarity_threshold,
        "samples_analyzed": len(samples),
        "exact_semantic_group_count": len(groups),
        "repeated_group_count": len(repeated_groups),
        "groups_with_preferred_3_plus_repeats": groups_with_preferred_size,
        "repeated_groups": repeated_groups,
        "insufficient_groups": insufficient_groups,
        "pass_gate": pass_gate,
    }


def render_fifo_semantic_replicate_stability_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# FIFO Semantic Replicate Stability",
        "",
        f"- Input: `{report['input_path']}`",
        f"- Samples analyzed: {report['samples_analyzed']}",
        f"- Repeated exact groups: {report['repeated_group_count']}",
        f"- Pass gate: {report['pass_gate']['passed']}",
        f"- Recommendation: {report['pass_gate']['recommendation']}",
        "",
        "## Repeated Groups",
        "",
        "| Semantic group | Count | LilyGO min/mean | YardStick symbol min/mean | YardStick bit min/mean | Gate |",
        "| --- | ---: | --- | --- | --- | --- |",
    ]
    for group in report["repeated_groups"]:
        lilygo = group["lilygo_fifo"]["symbol_similarity"]
        ysym = group["yardstick_semantic"]["symbol_similarity"]
        ybit = group["yardstick_semantic"]["bit_similarity"]
        lines.append(
            f"| `{group['semantic_key_label']}` | {group['sample_count']} | "
            f"{lilygo['min_similarity']}/{lilygo['mean_similarity']} | "
            f"{ysym['min_similarity']}/{ysym['mean_similarity']} | "
            f"{ybit['min_similarity']}/{ybit['mean_similarity']} | "
            f"{group['group_gate_passed']} |"
        )
    lines.extend(["", "## Notes", ""])
    if report["pass_gate"]["failure_mode"]:
        lines.append(f"- Failure mode: `{report['pass_gate']['failure_mode']}`")
    lines.append(
        "- LilyGO bit similarity is computed from `candidate.raw_slice_hex`; LilyGO symbol similarity is the primary FIFO semantic gate."
    )
    lines.append("- Whole YardStick streams and LilyGO edge windows are not used.")
    return "\n".join(lines) + "\n"


def write_fifo_semantic_replicate_stability_report(
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
        json_path = output_path / "fifo_semantic_replicate_stability_report.json"
        json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written["json"] = json_path
    if not json_only:
        markdown_path = output_path / "fifo_semantic_replicate_stability_report.md"
        markdown_path.write_text(render_fifo_semantic_replicate_stability_markdown(report), encoding="utf-8")
        written["markdown"] = markdown_path
    return written
