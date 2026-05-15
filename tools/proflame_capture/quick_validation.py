"""Lightweight cross-source quick validation for capture samples."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from custom_components.proflame2.rf.artifacts import is_lilygo_fifo_semantic_artifact

from .models import CollectorResult, FireplaceState, SampleContext

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class QuickValidationResult:
    """One normalized quick-validation outcome."""

    payload: dict[str, Any]

    @property
    def collection_valid(self) -> bool:
        return bool(self.payload["collection_valid"])

    @property
    def collection_reject_reasons(self) -> list[str]:
        return list(self.payload["collection_reject_reasons"])

    @property
    def proposed_state_after(self) -> FireplaceState | None:
        update = self.payload.get("semantic_summary", {}).get("state_update_from_rtl433")
        if not isinstance(update, dict):
            return None
        return FireplaceState(
            power=update.get("power"),
            flame=update.get("flame"),
            fan=update.get("fan"),
        )


def build_quick_validation(
    *,
    sample_context: SampleContext,
    collector_results: dict[str, CollectorResult],
) -> QuickValidationResult:
    """Build one lightweight validation summary for a sample."""

    source_summary: dict[str, dict[str, Any]] = {}
    selected_sources = list(collector_results.keys())
    reject_reasons: list[str] = []

    for source_name, result in collector_results.items():
        key_artifacts = _key_artifacts_present(sample_context.sample_dir, result)
        source_entry = {
            "selected": result.selected,
            "complete": result.complete,
            "valid": result.valid,
            "reject_reason": result.reject_reason,
            "artifact_dir": result.artifact_dir,
            "key_artifacts_present": key_artifacts,
        }
        source_summary[source_name] = source_entry
        reasons = _source_reject_reasons(sample_context.sample_dir, result, key_artifacts)
        reject_reasons.extend(f"{source_name}:{reason}" for reason in reasons)

    rtl433_metadata = collector_results.get("rtl433").metadata if "rtl433" in collector_results else {}
    yardstick_metadata = collector_results.get("yardstick").metadata if "yardstick" in collector_results else {}
    lilygo_metadata = collector_results.get("lilygo").metadata if "lilygo" in collector_results else {}

    semantic_summary = _build_semantic_summary(
        sample_context=sample_context,
        lilygo_metadata=lilygo_metadata,
        rtl433_metadata=rtl433_metadata,
        yardstick_metadata=yardstick_metadata,
    )
    if semantic_summary.get("lilygo_fifo_matches_rtl433") is False:
        reject_reasons.append("lilygo:semantic_fifo_rtl433_mismatch")

    collection_valid = not reject_reasons
    pairing_summary = _build_pairing_summary(
        collection_valid=collection_valid,
        selected_sources=selected_sources,
        source_summary=source_summary,
        lilygo_metadata=lilygo_metadata,
        rtl433_metadata=rtl433_metadata,
        yardstick_metadata=yardstick_metadata,
    )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "sample_id": sample_context.identity.sample_id,
        "requested_action": sample_context.identity.requested_action.value,
        "collection_valid": collection_valid,
        "collection_reject_reasons": sorted(reject_reasons),
        "selected_sources": selected_sources,
        "source_summary": source_summary,
        "pairing_summary": pairing_summary,
        "semantic_summary": semantic_summary,
        "analysis_valid": None,
        "notes": _build_notes(pairing_summary=pairing_summary, semantic_summary=semantic_summary),
    }
    return QuickValidationResult(payload=payload)


def _source_reject_reasons(
    sample_dir: Path,
    result: CollectorResult,
    key_artifacts_present: dict[str, bool],
) -> list[str]:
    reasons: list[str] = []
    if not result.selected:
        return reasons
    if not result.complete:
        reasons.append("incomplete")
    if not result.valid:
        reasons.append(result.reject_reason or "invalid")
    metadata = result.metadata
    if result.mode == "stub" or metadata.get("stub") is True:
        if not result.artifact_paths or not any(key_artifacts_present.values()):
            reasons.append("missing_artifacts")
        return sorted(set(reasons))

    if result.source_name == "lilygo":
        semantic_fifo = metadata.get("semantic_fifo_artifact")
        has_semantic_fifo = is_lilygo_fifo_semantic_artifact(semantic_fifo)
        if not has_semantic_fifo:
            reasons.append("missing_semantic_fifo_artifact")
        if not key_artifacts_present.get("capture_export", False):
            reasons.append("missing_capture_export_artifact")
        if not key_artifacts_present.get("semantic_fifo_artifact", False):
            reasons.append("missing_semantic_fifo_artifact_file")
    elif result.source_name == "rtl433":
        if metadata.get("model") != "Proflame2-Remote":
            reasons.append("wrong_model")
        if metadata.get("integrity") in (None, ""):
            reasons.append("missing_integrity")
        if metadata.get("id") in (None, ""):
            reasons.append("missing_decode")
        if not key_artifacts_present.get("decoded", False):
            reasons.append("missing_decoded_artifact")
    elif result.source_name == "yardstick":
        useful_artifact = any(
            [
                bool(metadata.get("raw_payload_hex")),
                bool(metadata.get("bit_stream")),
                bool(metadata.get("symbol_stream")),
                bool(metadata.get("decoded")),
                bool(metadata.get("decoded_fields")),
            ]
        )
        if not metadata.get("diagnostic_present", False):
            reasons.append("no_diagnostic")
        if not useful_artifact:
            reasons.append("no_useful_artifact")
        if not key_artifacts_present.get("diagnostic", False):
            reasons.append("missing_diagnostic_artifact")
    else:
        if not result.artifact_paths:
            reasons.append("missing_artifacts")
        elif not any(key_artifacts_present.values()):
            reasons.append("missing_artifacts")

    return sorted(set(reasons))


def _key_artifacts_present(sample_dir: Path, result: CollectorResult) -> dict[str, bool]:
    if result.mode == "stub" or result.metadata.get("stub") is True:
        return {artifact.kind: _artifact_exists(sample_dir, artifact.path) for artifact in result.artifact_paths}
    relative_paths = {artifact.kind: artifact.path for artifact in result.artifact_paths}
    if result.source_name == "lilygo":
        return {
            "raw_syslog": _artifact_exists(sample_dir, relative_paths.get("syslog_log")),
            "capture_export": _artifact_exists(sample_dir, relative_paths.get("capture_export")),
            "semantic_fifo_artifact": _artifact_exists(sample_dir, relative_paths.get("semantic_fifo_artifact")),
        }
    if result.source_name == "rtl433":
        return {
            "raw_stdout": _artifact_exists(sample_dir, relative_paths.get("rtl433_stdout")),
            "decoded": _artifact_exists(sample_dir, relative_paths.get("rtl433_decoded")),
            "parser_debug": _artifact_exists(sample_dir, relative_paths.get("rtl433_parser_debug")),
        }
    if result.source_name == "yardstick":
        return {
            "diagnostic": _artifact_exists(sample_dir, relative_paths.get("yardstick_diagnostic")),
            "raw_payload_hex": _artifact_exists(sample_dir, relative_paths.get("yardstick_raw_payload")),
            "bit_stream": _artifact_exists(sample_dir, relative_paths.get("yardstick_bit_stream")),
            "symbol_stream": _artifact_exists(sample_dir, relative_paths.get("yardstick_symbol_stream")),
            "decoded": _artifact_exists(sample_dir, relative_paths.get("yardstick_decoded")),
        }
    return {artifact.kind: _artifact_exists(sample_dir, artifact.path) for artifact in result.artifact_paths}


def _artifact_exists(sample_dir: Path, relative_path: str | None) -> bool:
    if not relative_path:
        return False
    return (sample_dir / relative_path).is_file()


def _build_pairing_summary(
    *,
    collection_valid: bool,
    selected_sources: list[str],
    source_summary: dict[str, dict[str, Any]],
    lilygo_metadata: dict[str, Any],
    rtl433_metadata: dict[str, Any],
    yardstick_metadata: dict[str, Any],
) -> dict[str, Any]:
    lilygo_semantic_fifo_present = is_lilygo_fifo_semantic_artifact(lilygo_metadata.get("semantic_fifo_artifact"))
    rtl433_decode_present = rtl433_metadata.get("id") not in (None, "")
    yardstick_diagnostic_present = yardstick_metadata.get("diagnostic_present") is True

    selected_complete = all(
        source_summary[name]["selected"] and source_summary[name]["complete"] and source_summary[name]["valid"]
        for name in selected_sources
    )
    if not collection_valid or not selected_complete:
        pairing_confidence = "invalid_missing_required_source"
    else:
        timing_known = any(
            [
                rtl433_metadata.get("host_received_utc"),
                yardstick_metadata.get("host_complete_ns"),
            ]
        )
        if timing_known:
            pairing_confidence = "high_all_sources_complete"
        else:
            pairing_confidence = "medium_missing_optional_timing"
    if selected_complete and not any(
        [lilygo_semantic_fifo_present, rtl433_decode_present, yardstick_diagnostic_present]
    ):
        pairing_confidence = "low_incomplete_metadata"

    timing_summary = {
        "rtl433_host_received_utc": rtl433_metadata.get("host_received_utc"),
        "yardstick_host_complete_ns": yardstick_metadata.get("host_complete_ns"),
    }
    return {
        "lilygo_semantic_fifo_present": lilygo_semantic_fifo_present,
        "rtl433_decode_present": rtl433_decode_present,
        "yardstick_diagnostic_present": yardstick_diagnostic_present,
        "timing_summary": timing_summary,
        "pairing_confidence": pairing_confidence,
    }


def _build_semantic_summary(
    *,
    sample_context: SampleContext,
    lilygo_metadata: dict[str, Any],
    rtl433_metadata: dict[str, Any],
    yardstick_metadata: dict[str, Any],
) -> dict[str, Any]:
    requested_action_plausible = _requested_action_plausibility(
        sample_context=sample_context,
        rtl433_metadata=rtl433_metadata,
    )
    state_update = None
    if rtl433_metadata.get("id") not in (None, ""):
        state_update = {
            "power": rtl433_metadata.get("power"),
            "flame": rtl433_metadata.get("flame"),
            "fan": rtl433_metadata.get("fan"),
            "id": rtl433_metadata.get("id"),
            "cmd1": rtl433_metadata.get("cmd1"),
            "cmd2": rtl433_metadata.get("cmd2"),
            "err1": rtl433_metadata.get("err1"),
            "err2": rtl433_metadata.get("err2"),
        }
    lilygo_fifo = lilygo_metadata.get("semantic_fifo_artifact")
    lilygo_fields = lilygo_fifo.get("decoded_fields") if isinstance(lilygo_fifo, dict) else None
    lilygo_fifo_matches_rtl433 = None
    if isinstance(lilygo_fields, dict) and state_update is not None:
        lilygo_fifo_matches_rtl433 = all(
            str(lilygo_fields.get(field)).lower() == str(state_update.get(field)).lower()
            for field in ("cmd1", "cmd2", "err1", "err2")
            if field in state_update
        )
        if lilygo_fields.get("remote_id") is not None and state_update.get("id") is not None:
            lilygo_fifo_matches_rtl433 = (
                lilygo_fifo_matches_rtl433
                and str(lilygo_fields.get("remote_id")).lower() == str(state_update.get("id")).lower()
            )
    return {
        "rtl433_id": rtl433_metadata.get("id"),
        "rtl433_power": rtl433_metadata.get("power"),
        "rtl433_flame": rtl433_metadata.get("flame"),
        "rtl433_fan": rtl433_metadata.get("fan"),
        "yardstick_decoded_id": yardstick_metadata.get("decoded_id"),
        "yardstick_decode_success": yardstick_metadata.get("decode_success"),
        "lilygo_fifo_decode_success": is_lilygo_fifo_semantic_artifact(lilygo_fifo),
        "lilygo_fifo_decoded_fields": lilygo_fields,
        "lilygo_fifo_matches_rtl433": lilygo_fifo_matches_rtl433,
        "requested_action_plausible": requested_action_plausible,
        "state_update_from_rtl433": state_update,
    }


def _requested_action_plausibility(
    *,
    sample_context: SampleContext,
    rtl433_metadata: dict[str, Any],
) -> bool | None:
    action = sample_context.identity.requested_action.value
    state_before = sample_context.state_before
    if rtl433_metadata.get("id") in (None, ""):
        return None

    if action == "power_toggle":
        return True if state_before.power is not None and rtl433_metadata.get("power") is not None else None
    if action == "flame_up":
        if state_before.flame is None or rtl433_metadata.get("flame") is None:
            return None
        return rtl433_metadata["flame"] >= state_before.flame
    if action == "flame_down":
        if state_before.flame is None or rtl433_metadata.get("flame") is None:
            return None
        return rtl433_metadata["flame"] <= state_before.flame
    if action == "fan_up":
        if state_before.fan is None or rtl433_metadata.get("fan") is None:
            return None
        return rtl433_metadata["fan"] >= state_before.fan
    if action == "fan_down":
        if state_before.fan is None or rtl433_metadata.get("fan") is None:
            return None
        return rtl433_metadata["fan"] <= state_before.fan
    return None


def _build_notes(
    *,
    pairing_summary: dict[str, Any],
    semantic_summary: dict[str, Any],
) -> list[str]:
    notes: list[str] = []
    if pairing_summary["pairing_confidence"] == "invalid_missing_required_source":
        notes.append("One or more selected sources did not complete with usable artifacts.")
    if semantic_summary.get("lilygo_fifo_matches_rtl433") is False:
        notes.append("LilyGO FIFO semantic candidate does not match rtl_433 canonical decode.")
    if semantic_summary["requested_action_plausible"] is None:
        notes.append("Requested action plausibility is unknown with current state/decode data.")
    return notes
