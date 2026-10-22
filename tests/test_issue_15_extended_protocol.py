"""Regression baseline for the Issue #15 TMFSLA extended-frame evidence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom_components.proflame2.protocol.ecc import (
    build_extended_integrity_byte,
    derive_ecc_profile,
    extended_frame_integrity_matches,
)
from custom_components.proflame2.rf.pulse import find_proflame_pcm_candidates

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "issue_15_tmfsla_extended_capture.json"


def _fixture() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _pcm_bits(capture: dict[str, object]) -> str:
    packed_bits = "".join(f"{byte:08b}" for byte in bytes.fromhex(str(capture["pcm_hex"])))
    return packed_bits[: int(capture["pcm_bit_length"])]


def _candidate_words(candidate: object) -> tuple[int, ...]:
    return candidate.frame.wire_words


def test_issue_15_fixture_preserves_complete_controlled_capture_set() -> None:
    """The source evidence contains 91 accepted rows and three anomalies."""

    fixture = _fixture()
    captures = fixture["captures"]

    assert fixture["schema_version"] == 1
    assert fixture["remote"] == {"serial_id": "08E905", "model": "SIT TMFSLA / T99058402300"}
    assert len(captures) == 94
    assert sum(capture["expected_decode"] != "rejected" for capture in captures) == 91
    assert [capture["capture_id"] for capture in captures if capture["expected_decode"] == "rejected"] == [21, 37, 50]


def test_issue_15_all_259_bit_rows_decode_to_reported_ten_words() -> None:
    """Every normal RMT row must stay structurally reproducible from raw PCM."""

    captures = _fixture()["captures"]
    for capture in captures:
        if capture["expected_decode"] == "rejected":
            continue

        candidates = find_proflame_pcm_candidates(_pcm_bits(capture))

        assert len(candidates) == 1, capture["capture_id"]
        candidate = candidates[0]
        assert candidate.frame_format == "extended_10_word_truncated_end_guard"
        assert candidate.bit_offset == 0
        assert candidate.repeat_gap_bits == 0
        assert _candidate_words(candidate) == tuple(capture["words"]), capture["capture_id"]
        assert candidate.extended_integrity_valid is True


def test_issue_15_260_bit_anomalies_remain_rejected() -> None:
    """Do not weaken bounded PCM validation to accept adjacent malformed rows."""

    captures = _fixture()["captures"]
    for capture in captures:
        if capture["expected_decode"] != "rejected":
            continue

        assert find_proflame_pcm_candidates(_pcm_bits(capture)) == [], capture["capture_id"]


def test_issue_15_candidate_extended_ecc_matches_all_raw_backed_rows() -> None:
    """Alex's proposed alternating-word transform must validate decoded raw PCM."""

    for capture in _fixture()["captures"]:
        if capture["expected_decode"] == "rejected":
            continue
        (candidate,) = find_proflame_pcm_candidates(_pcm_bits(capture))
        words = _candidate_words(candidate)

        assert build_extended_integrity_byte((words[4], words[6]), 0x23) == words[8], capture["capture_id"]
        assert build_extended_integrity_byte((words[3], words[5], words[7]), 0x65) == words[9], capture["capture_id"]
        assert extended_frame_integrity_matches(words), capture["capture_id"]


def test_issue_15_extended_integrity_rejects_single_word_corruption() -> None:
    """A structural ten-word row is not accepted without valid integrity lanes."""

    words = (0x08, 0xE9, 0x05, 0x81, 0x06, 0x15, 0xF5, 0x00, 0x13, 0x77)
    assert extended_frame_integrity_matches(words)
    assert not extended_frame_integrity_matches((*words[:8], words[8] ^ 0x01, words[9]))
    assert not extended_frame_integrity_matches((*words[:9], words[9] ^ 0x01))


def test_issue_15_legacy_cd_assignment_cannot_explain_extended_rows() -> None:
    """The legacy W4/W5/W6/W7 assignment must remain an explicit failure."""

    captures = [capture for capture in _fixture()["captures"] if capture["expected_decode"] != "rejected"]
    cmd1_samples = [(capture["words"][3], capture["words"][5]) for capture in captures]
    cmd2_samples = [(capture["words"][4], capture["words"][6]) for capture in captures]

    with pytest.raises(ValueError, match="No stable C/D value"):
        derive_ecc_profile(cmd1_samples, cmd2_samples)
