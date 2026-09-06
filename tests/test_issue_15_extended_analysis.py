"""Tests for the deterministic Issue #15 evidence analyzer."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[1]
ANALYZER_PATH = REPOSITORY_ROOT / "tools" / "proflame_extended" / "analyze_issue_15.py"
ARTIFACT_PATH = REPOSITORY_ROOT / "artifacts" / "issue_15_extended" / "analysis-baseline.json"
SPEC = importlib.util.spec_from_file_location("issue_15_extended_analysis", ANALYZER_PATH)
assert SPEC is not None and SPEC.loader is not None
ANALYZER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ANALYZER)


def test_issue_15_analysis_preserves_baseline_limits() -> None:
    """The analyzer must not turn an underdetermined fit into an encoder claim."""

    analysis = ANALYZER.analyze_fixture(ANALYZER._load_fixture(ANALYZER.DEFAULT_FIXTURE))

    assert analysis["capture_counts"] == {
        "total": 94,
        "accepted": 91,
        "rejected": 3,
        "rejected_capture_ids": [21, 37, 50],
    }
    assert analysis["hypotheses"]["legacy_cd_assignment"]["status"] == "rejected"
    assert analysis["hypotheses"]["candidate_extended_ecc_lanes"] == [
        {
            "lane": "W9",
            "input_words": ["W5", "W7"],
            "output_word": "W9",
            "transform": "T(value) = build_err_byte(value, 0, 0)",
            "expanded_formula": "T(T(W5)) ^ T(W7) ^ 0x23",
            "proposed_final_xor": "0x23",
            "derived_final_xors": ["0x23"],
            "matching_capture_ids": [capture for capture in range(15, 109) if capture not in {21, 37, 50}],
            "nonmatching_capture_ids": [],
        },
        {
            "lane": "W10",
            "input_words": ["W4", "W6", "W8"],
            "output_word": "W10",
            "transform": "T(value) = build_err_byte(value, 0, 0)",
            "expanded_formula": "T(T(T(W4))) ^ T(T(W6)) ^ T(W8) ^ 0x65",
            "proposed_final_xor": "0x65",
            "derived_final_xors": ["0x65"],
            "matching_capture_ids": [capture for capture in range(15, 109) if capture not in {21, 37, 50}],
            "nonmatching_capture_ids": [],
        },
    ]
    assert analysis["hypotheses"]["observed_minimal_input_sets"] == {
        "W9": [["W5", "W7"]],
        "W10": [["W4", "W6"]],
    }
    assert all(result["status"] == "rejected" for result in analysis["hypotheses"]["direct_legacy_cd_assignments"])
    assert analysis["hypotheses"]["packed_nibble_legacy_cd"]["attempted_assignments"] == 200
    assert analysis["hypotheses"]["packed_nibble_legacy_cd"]["matching_assignments"] == []
    assert not any(
        result["matching_capture_ids"]
        for result in analysis["hypotheses"]["checksum_and_crc8"]
        if result["family"].startswith("CRC-")
    )
    assert analysis["hypotheses"]["w9_affine_from_w4_to_w7"]["input_bit_rank"] < 32
    assert analysis["hypotheses"]["w10_affine_from_w4_to_w7"]["input_bit_rank"] < 32
    assert all(analysis["hypotheses"]["w9_affine_from_w4_to_w7"]["output_bits_consistent"])
    assert all(analysis["hypotheses"]["w10_affine_from_w4_to_w7"]["output_bits_consistent"])


def test_issue_15_committed_analysis_artifact_is_current() -> None:
    """The reviewable analysis artifact must be regenerated from the fixture."""

    expected = ANALYZER.analyze_fixture(ANALYZER._load_fixture(ANALYZER.DEFAULT_FIXTURE))
    artifact = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))

    assert artifact == expected
