"""Typed RF artifact contracts and semantic eligibility gates.

RF receivers produce several useful artifact layers: raw capture buffers,
debug/candidate windows, and packet-owned semantic artifacts. Only semantic
artifacts that pass these gates may be used as evidence in replicate or
cross-source comparisons.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict


class ProflameDecodedFields(TypedDict, total=False):
    """Normalized decoded Proflame2 frame fields used for semantic matching."""

    remote_id: str
    cmd1: str
    cmd2: str
    err1: str
    err2: str


class SemanticArtifact(TypedDict, total=False):
    """Common fields required by packet-owned semantic RF artifacts."""

    artifact_class: Literal["semantic"]
    artifact_type: str
    semantic_comparable: bool
    decode_success: bool
    provenance: str


class LilyGoFifoSemanticArtifact(TypedDict, total=False):
    """LilyGO FIFO semantic artifact produced by firmware or HA candidate scan."""

    artifact_class: Literal["semantic", "semantic_fifo_candidate"]
    artifact_type: str
    semantic_comparable: bool
    decode_success: bool
    packet_normalized: bool
    source: str
    provenance: str
    decoded_fields: ProflameDecodedFields
    learning_equivalent_acceptance_path: bool
    learning_accepted: bool
    acceptance_policy: str
    event_id: str
    remote_id: str
    cmd1: str
    cmd2: str
    err1: str
    err2: str
    bit_offset: int
    symbol_offset: int
    absolute_bit_offset: int
    repeat_count: int
    confidence: int
    raw_payload_hex: str
    candidate_raw_slice_hex: str
    symbol_stream: str
    capture_metadata: dict[str, Any]


class FifoDebugFailure(TypedDict, total=False):
    """Debug-only FIFO rejection metadata that must not be used semantically."""

    event_id: str
    reason: str
    event_kind: str
    qualifier: str
    reject_reason: str
    payload_length_bytes: int
    max_payload_length_bytes: int
    raw_payload_hex: str
    expected_remote_id: str
    observed_remote_id: str
    error: str
    capture_metadata: dict[str, Any]


class ESPHomeAcceptedRXPacketMetadata(TypedDict, total=False):
    """Firmware-decoded active-listening metadata accepted by HA."""

    event_kind: Literal["rx_packet"]
    accepted: Literal["true"]
    qualifier: Literal["strict"]
    remote_id: str
    cmd1: str
    cmd2: str
    err1: str
    err2: str


def is_yardstick_semantic_artifact(artifact: object) -> bool:
    """Return true only for canonical YardStick semantic comparison evidence."""

    return (
        isinstance(artifact, dict)
        and artifact.get("artifact_class") == "semantic"
        and artifact.get("semantic_comparable") is True
        and artifact.get("decode_success") is True
    )


def is_lilygo_fifo_semantic_artifact(artifact: object) -> bool:
    """Return true for accepted LilyGO FIFO packet-owned artifacts.

    Older capture tooling used `semantic_fifo_candidate` for the class, while
    the production HA path now emits `semantic`. Both are packet-owned only
    after the FIFO scanner/firmware decoder has accepted the packet.
    """

    return (
        isinstance(artifact, dict)
        and artifact.get("artifact_class") in {"semantic", "semantic_fifo_candidate"}
        and artifact.get("semantic_comparable") is True
        and artifact.get("decode_success") is True
    )
