"""Generate deterministic evidence from the Issue #15 extended-frame fixture."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Sequence
from itertools import combinations
from pathlib import Path
from typing import Any

from custom_components.proflame2.protocol.ecc import build_err_byte, derive_ecc_profile, derive_stable_cd

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURE = REPOSITORY_ROOT / "tests" / "fixtures" / "issue_15_tmfsla_extended_capture.json"
STATE_WORD_START = 3
STATE_WORD_END = 7

CRC8_FAMILIES = (
    ("CRC-8/SMBUS", 0x07, 0x00, False, False, 0x00),
    ("CRC-8/SAE-J1850", 0x1D, 0xFF, False, False, 0xFF),
    ("CRC-8/AUTOSAR", 0x2F, 0xFF, False, False, 0xFF),
    ("CRC-8/MAXIM-DOW", 0x31, 0x00, True, True, 0x00),
    ("CRC-8/ROHC", 0x07, 0xFF, True, True, 0x00),
    ("CRC-8/WCDMA", 0x9B, 0x00, False, False, 0x00),
    ("CRC-8/DARC", 0x39, 0x00, True, True, 0x00),
    ("CRC-8/CDMA2000", 0x9B, 0xFF, False, False, 0x00),
)


def _load_fixture(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _accepted_captures(fixture: dict[str, Any]) -> list[dict[str, Any]]:
    return [capture for capture in fixture["captures"] if capture["expected_decode"] != "rejected"]


def _gf2_rank(rows: Sequence[int]) -> int:
    """Return the rank of integer bit vectors over GF(2)."""

    pivots: dict[int, int] = {}
    for row in rows:
        value = row
        while value:
            pivot = value.bit_length() - 1
            if pivot not in pivots:
                pivots[pivot] = value
                break
            value ^= pivots[pivot]
    return len(pivots)


def _reflect(value: int, width: int) -> int:
    """Return ``value`` with exactly ``width`` bits reversed."""

    reflected = 0
    for bit_index in range(width):
        reflected = (reflected << 1) | ((value >> bit_index) & 1)
    return reflected


def _crc8(
    data: Sequence[int],
    *,
    polynomial: int,
    initial: int,
    reflect_input: bool,
    reflect_output: bool,
    xor_output: int,
) -> int:
    """Calculate one explicitly parameterized CRC-8 family member."""

    remainder = initial
    for byte in data:
        value = _reflect(byte, 8) if reflect_input else byte
        remainder ^= value
        for _ in range(8):
            remainder = ((remainder << 1) ^ polynomial) & 0xFF if remainder & 0x80 else (remainder << 1) & 0xFF
    if reflect_output:
        remainder = _reflect(remainder, 8)
    return remainder ^ xor_output


def _integrity_comparison(
    captures: Sequence[dict[str, Any]],
    *,
    input_word_indexes: Sequence[int],
    output_word_index: int,
    calculation: Callable[[Sequence[int]], int],
) -> dict[str, list[int]]:
    """Return capture IDs which do and do not match one integrity calculation."""

    matching: list[int] = []
    nonmatching: list[int] = []
    for capture in captures:
        calculated = calculation([capture["words"][index] for index in input_word_indexes])
        if calculated == capture["words"][output_word_index]:
            matching.append(capture["capture_id"])
        else:
            nonmatching.append(capture["capture_id"])
    return {"matching_capture_ids": matching, "nonmatching_capture_ids": nonmatching}


def _minimal_input_sets(captures: Sequence[dict[str, Any]], output_word_index: int) -> list[list[str]]:
    """Find the smallest observed input-word sets with no output conflicts."""

    input_indexes = range(STATE_WORD_START, 8)
    for size in range(1, 6):
        valid_sets: list[list[str]] = []
        for indexes in combinations(input_indexes, size):
            values: dict[tuple[int, ...], int] = {}
            for capture in captures:
                key = tuple(capture["words"][index] for index in indexes)
                value = capture["words"][output_word_index]
                if key in values and values[key] != value:
                    break
                values[key] = value
            else:
                valid_sets.append([f"W{index + 1}" for index in indexes])
        if valid_sets:
            return valid_sets
    return []


def _direct_cd_assignments(captures: Sequence[dict[str, Any]]) -> list[dict[str, str]]:
    """Test each state word as a direct legacy C/D command for each tail byte."""

    results: list[dict[str, str]] = []
    for output_index in (8, 9):
        for input_index in range(STATE_WORD_START, 8):
            try:
                cd_value = derive_stable_cd(
                    (capture["words"][input_index], capture["words"][output_index]) for capture in captures
                )
            except ValueError as error:
                results.append(
                    {
                        "input_word": f"W{input_index + 1}",
                        "output_word": f"W{output_index + 1}",
                        "status": "rejected",
                        "reason": str(error),
                    }
                )
            else:
                results.append(
                    {
                        "input_word": f"W{input_index + 1}",
                        "output_word": f"W{output_index + 1}",
                        "status": "match",
                        "cd": f"0x{cd_value:02X}",
                    }
                )
    return results


def _packed_nibble_cd_summary(captures: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Test legacy C/D over every two-nibble byte built from W4-W8."""

    nibble_sources = [
        (word_index, nibble_shift, f"W{word_index + 1}.{'high' if nibble_shift else 'low'}")
        for word_index in range(STATE_WORD_START, 8)
        for nibble_shift in (4, 0)
    ]
    matches: list[dict[str, str]] = []
    attempted = 0
    for output_index in (8, 9):
        for high_word, high_shift, high_name in nibble_sources:
            for low_word, low_shift, low_name in nibble_sources:
                attempted += 1
                samples = (
                    (
                        ((capture["words"][high_word] >> high_shift) & 0x0F) << 4
                        | ((capture["words"][low_word] >> low_shift) & 0x0F),
                        capture["words"][output_index],
                    )
                    for capture in captures
                )
                try:
                    cd_value = derive_stable_cd(samples)
                except ValueError:
                    continue
                matches.append(
                    {
                        "high_nibble": high_name,
                        "low_nibble": low_name,
                        "output_word": f"W{output_index + 1}",
                        "cd": f"0x{cd_value:02X}",
                    }
                )
    return {
        "attempted_assignments": attempted,
        "matching_assignments": matches,
        "interpretation": "No match rules out direct legacy C/D over any byte made from two W4-W8 nibbles.",
    }


def _checksum_and_crc8_hypotheses(captures: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Test simple byte checksums and documented CRC-8 variants on observed groups."""

    groups = (([4, 6], 8), ([3, 5], 9))
    hypotheses: list[dict[str, Any]] = []
    for input_indexes, output_index in groups:
        input_words = [f"W{index + 1}" for index in input_indexes]
        output_word = f"W{output_index + 1}"
        for name, calculation in (
            ("xor8", lambda values: values[0] ^ values[1]),
            ("sum8", lambda values: sum(values) & 0xFF),
        ):
            hypotheses.append(
                {
                    "family": name,
                    "input_words": input_words,
                    "output_word": output_word,
                    **_integrity_comparison(
                        captures,
                        input_word_indexes=input_indexes,
                        output_word_index=output_index,
                        calculation=calculation,
                    ),
                }
            )
        for name, polynomial, initial, reflect_input, reflect_output, xor_output in CRC8_FAMILIES:
            hypotheses.append(
                {
                    "family": name,
                    "parameters": {
                        "polynomial": f"0x{polynomial:02X}",
                        "initial": f"0x{initial:02X}",
                        "reflect_input": reflect_input,
                        "reflect_output": reflect_output,
                        "xor_output": f"0x{xor_output:02X}",
                    },
                    "input_words": input_words,
                    "output_word": output_word,
                    **_integrity_comparison(
                        captures,
                        input_word_indexes=input_indexes,
                        output_word_index=output_index,
                        calculation=lambda values, polynomial=polynomial, initial=initial, reflect_input=reflect_input, reflect_output=reflect_output, xor_output=xor_output: _crc8(
                            values,
                            polynomial=polynomial,
                            initial=initial,
                            reflect_input=reflect_input,
                            reflect_output=reflect_output,
                            xor_output=xor_output,
                        ),
                    ),
                }
            )
    return hypotheses


def _state_feature_vector(words: Sequence[int]) -> int:
    """Pack W4-W7 into a 32-bit vector for linear-model diagnostics."""

    vector = 0
    for offset, word_index in enumerate(range(STATE_WORD_START, STATE_WORD_END)):
        vector |= words[word_index] << (offset * 8)
    return vector


def _affine_fit_summary(captures: Sequence[dict[str, Any]], output_word_index: int) -> dict[str, Any]:
    """Report whether observed output bits admit an underdetermined affine fit."""

    features = [_state_feature_vector(capture["words"]) for capture in captures]
    base = features[0]
    differences = [feature ^ base for feature in features]
    design_rows = [1 | (feature << 1) for feature in features]

    bit_consistency: list[bool] = []
    for output_bit in range(8):
        rows = [
            design | (((capture["words"][output_word_index] >> output_bit) & 1) << 33)
            for capture, design in zip(captures, design_rows, strict=True)
        ]
        coefficient_rank = _gf2_rank([row & ((1 << 33) - 1) for row in rows])
        augmented_rank = _gf2_rank(rows)
        bit_consistency.append(coefficient_rank == augmented_rank)

    return {
        "input_words": ["W4", "W5", "W6", "W7"],
        "input_bit_rank": _gf2_rank(differences),
        "affine_design_rank": _gf2_rank(design_rows),
        "observed_rows": len(captures),
        "output_bits_consistent": bit_consistency,
        "interpretation": (
            "An affine fit is possible for the observed rows, but the input rank is below 32; "
            "this is diagnostic evidence, not a complete encoder algorithm."
        ),
    }


def _legacy_cd_summary(captures: Sequence[dict[str, Any]]) -> dict[str, str]:
    """Test the current parser's W4/W5/W6/W7 legacy C/D assignment."""

    cmd1_samples = [(capture["words"][3], capture["words"][5]) for capture in captures]
    cmd2_samples = [(capture["words"][4], capture["words"][6]) for capture in captures]
    try:
        derive_ecc_profile(cmd1_samples, cmd2_samples)
    except ValueError as error:
        return {"status": "rejected", "reason": str(error)}
    return {"status": "unexpected_match", "reason": "Legacy C/D derivation unexpectedly succeeded."}


def _extended_ecc_lane(values: Sequence[int], final_xor: int) -> int:
    """Apply the candidate C/D-free legacy transform across one word lane."""

    ecc = 0
    for value in values:
        ecc = build_err_byte(ecc ^ value, 0, 0)
    return ecc ^ final_xor


def _extended_lane_hypotheses(captures: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate Alex's proposed alternating-word integrity lanes on the fixture."""

    lanes = (
        ("W9", [4, 6], 8, 0x23, "T(T(W5)) ^ T(W7) ^ 0x23"),
        ("W10", [3, 5, 7], 9, 0x65, "T(T(T(W4))) ^ T(T(W6)) ^ T(W8) ^ 0x65"),
    )
    results: list[dict[str, Any]] = []
    for name, input_indexes, output_index, final_xor, expansion in lanes:
        prefix_values = [
            _extended_ecc_lane([capture["words"][index] for index in input_indexes], 0) for capture in captures
        ]
        derived_final_xors = sorted(
            {capture["words"][output_index] ^ prefix for capture, prefix in zip(captures, prefix_values, strict=True)}
        )
        comparison = _integrity_comparison(
            captures,
            input_word_indexes=input_indexes,
            output_word_index=output_index,
            calculation=lambda values, final_xor=final_xor: _extended_ecc_lane(values, final_xor),
        )
        results.append(
            {
                "lane": name,
                "input_words": [f"W{index + 1}" for index in input_indexes],
                "output_word": f"W{output_index + 1}",
                "transform": "T(value) = build_err_byte(value, 0, 0)",
                "expanded_formula": expansion,
                "proposed_final_xor": f"0x{final_xor:02X}",
                "derived_final_xors": [f"0x{value:02X}" for value in derived_final_xors],
                **comparison,
            }
        )
    return results


def _consecutive_deltas(captures: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Record word changes between adjacent accepted capture IDs."""

    deltas: list[dict[str, Any]] = []
    for previous, current in zip(captures, captures[1:], strict=False):
        if current["capture_id"] != previous["capture_id"] + 1:
            continue
        changed_words = {
            f"W{index + 1}": previous["words"][index] ^ current["words"][index]
            for index in range(3, 10)
            if previous["words"][index] != current["words"][index]
        }
        deltas.append(
            {
                "from_capture_id": previous["capture_id"],
                "to_capture_id": current["capture_id"],
                "from_label": previous["label"],
                "to_label": current["label"],
                "to_state": current["resulting_state"],
                "changed_word_xor": changed_words,
            }
        )
    return deltas


def analyze_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    """Return the reproducible baseline analysis for one Issue #15 fixture."""

    accepted = _accepted_captures(fixture)
    rejected = [capture for capture in fixture["captures"] if capture["expected_decode"] == "rejected"]
    return {
        "schema_version": 1,
        "source": fixture["source"],
        "remote": fixture["remote"],
        "capture_counts": {
            "total": len(fixture["captures"]),
            "accepted": len(accepted),
            "rejected": len(rejected),
            "rejected_capture_ids": [capture["capture_id"] for capture in rejected],
        },
        "observed_word_values": {
            f"W{word_index + 1}": sorted({capture["words"][word_index] for capture in accepted})
            for word_index in range(10)
        },
        "consecutive_deltas": _consecutive_deltas(accepted),
        "hypotheses": {
            "legacy_cd_assignment": _legacy_cd_summary(accepted),
            "candidate_extended_ecc_lanes": _extended_lane_hypotheses(accepted),
            "observed_minimal_input_sets": {
                "W9": _minimal_input_sets(accepted, 8),
                "W10": _minimal_input_sets(accepted, 9),
            },
            "direct_legacy_cd_assignments": _direct_cd_assignments(accepted),
            "packed_nibble_legacy_cd": _packed_nibble_cd_summary(accepted),
            "checksum_and_crc8": _checksum_and_crc8_hypotheses(accepted),
            "w9_affine_from_w4_to_w7": _affine_fit_summary(accepted, 8),
            "w10_affine_from_w4_to_w7": _affine_fit_summary(accepted, 9),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--output", type=Path, help="Write JSON analysis to this path instead of stdout.")
    args = parser.parse_args()

    output = json.dumps(analyze_fixture(_load_fixture(args.fixture)), indent=2) + "\n"
    if args.output is None:
        print(output, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
