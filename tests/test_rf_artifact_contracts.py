from __future__ import annotations

from custom_components.proflame2.rf.artifacts import (
    is_lilygo_fifo_semantic_artifact,
    is_yardstick_semantic_artifact,
)


def test_yardstick_semantic_gate_accepts_only_canonical_semantic_artifact() -> None:
    assert is_yardstick_semantic_artifact(
        {
            "artifact_class": "semantic",
            "semantic_comparable": True,
            "decode_success": True,
            "provenance": "yardstick_rfrecv_learning",
        }
    )
    assert not is_yardstick_semantic_artifact(
        {
            "artifact_class": "candidate_window",
            "semantic_comparable": True,
            "decode_success": True,
        }
    )
    assert not is_yardstick_semantic_artifact(
        {
            "artifact_class": "semantic",
            "semantic_comparable": True,
            "decode_success": False,
        }
    )


def test_lilygo_fifo_semantic_gate_accepts_fifo_candidate_and_production_semantic() -> None:
    assert is_lilygo_fifo_semantic_artifact(
        {
            "artifact_class": "semantic_fifo_candidate",
            "semantic_comparable": True,
            "decode_success": True,
            "provenance": "lilygo_cc1101_fifo_candidate_scanner",
        }
    )
    assert is_lilygo_fifo_semantic_artifact(
        {
            "artifact_class": "semantic",
            "semantic_comparable": True,
            "decode_success": True,
            "provenance": "lilygo_cc1101_fifo_firmware_decoder",
        }
    )
    assert not is_lilygo_fifo_semantic_artifact(
        {
            "artifact_class": "experimental_fifo_probe",
            "semantic_comparable": False,
            "decode_success": True,
        }
    )
