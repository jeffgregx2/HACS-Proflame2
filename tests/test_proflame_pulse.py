"""Tests for the bounded LilyGO GDO0 pulse/PCM fallback decoder."""

from __future__ import annotations

import pytest

from custom_components.proflame2.protocol.packet import ProflameFrame
from custom_components.proflame2.rf.esphome.contract import ESPHomeRXEvent
from custom_components.proflame2.rf.esphome_api import ESPHomeAPIBackend
from custom_components.proflame2.rf.pulse import (
    PCM_UNIT_US,
    find_proflame_pcm_candidates,
    pulse_durations_to_bits,
)
from custom_components.proflame2.rf.waveform import SYMBOL_TO_BITS, frame_to_air_bytes


ISSUE_15_FRAME = ProflameFrame(
    serial_id=0x08E905,
    cmd1=0x81,
    err1=0x15,
    cmd2=0x06,
    err2=0xEB,
)
ISSUE_15_EXTENSION = (0x00, 0xEC, 0x77)


def _word_bits(value: int, *, trailing_bit: int) -> str:
    word = f"{value:08b}{trailing_bit}"
    symbols = f"S1{word}{word.count('1') % 2}1"
    return "".join(SYMBOL_TO_BITS[symbol] for symbol in symbols)


def _runs_from_bits(bits: str) -> list[int]:
    runs: list[int] = []
    cursor = 0
    while cursor < len(bits):
        value = bits[cursor]
        end = cursor + 1
        while end < len(bits) and bits[end] == value:
            end += 1
        runs.append((end - cursor) * PCM_UNIT_US * (1 if value == "1" else -1))
        cursor = end
    return runs


def test_issue_15_extended_row_decodes_standard_fields_and_extension() -> None:
    """The observed Bruce frame is seven standard words plus three extensions."""

    first_seven_bits = "".join(f"{byte:08b}" for byte in frame_to_air_bytes(ISSUE_15_FRAME))[:182]
    extension_bits = "".join(_word_bits(value, trailing_bit=0) for value in ISSUE_15_EXTENSION)
    row = first_seven_bits + extension_bits + ("0" * 13)

    candidates = find_proflame_pcm_candidates(row)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.frame == ISSUE_15_FRAME
    assert candidate.frame_format == "extended_10_word"
    assert candidate.extension_words == ISSUE_15_EXTENSION
    assert candidate.repeat_gap_bits == 13


def test_issue_15_pulse_runs_decode_to_same_extended_row() -> None:
    """A GDO0 edge capture retains the PCM row needed by the decoder."""

    first_seven_bits = "".join(f"{byte:08b}" for byte in frame_to_air_bytes(ISSUE_15_FRAME))[:182]
    extension_bits = "".join(_word_bits(value, trailing_bit=0) for value in ISSUE_15_EXTENSION)
    row = first_seven_bits + extension_bits + ("0" * 13)

    assert pulse_durations_to_bits(_runs_from_bits(row)) == row
    assert find_proflame_pcm_candidates(pulse_durations_to_bits(_runs_from_bits(row)))[0].frame == ISSUE_15_FRAME


def test_extended_row_requires_full_extension_and_repeat_gap() -> None:
    """A clipped tenth word or missing repeat gap must remain diagnostic only."""

    first_seven_bits = "".join(f"{byte:08b}" for byte in frame_to_air_bytes(ISSUE_15_FRAME))[:182]
    extension_bits = "".join(_word_bits(value, trailing_bit=0) for value in ISSUE_15_EXTENSION)

    assert find_proflame_pcm_candidates(first_seven_bits + extension_bits[:-2] + ("0" * 13)) == []
    assert find_proflame_pcm_candidates(first_seven_bits + extension_bits) == []


@pytest.mark.parametrize(
    ("pcm_hex", "frame"),
    [
        (
            "e5a9a9b96aa96e55596b95559ae55695b9a9a5aea6a958",
            ProflameFrame(serial_id=0x3B3F02, cmd1=0x01, cmd2=0x06, err1=0x76, err2=0xDE),
        ),
        (
            "e5a9a9b96aa96e55596b955556e55695b999a9aea6a958",
            ProflameFrame(serial_id=0x3B3F02, cmd1=0x00, cmd2=0x06, err1=0x57, err2=0xDE),
        ),
    ],
)
def test_rmt_terminal_end_guard_truncation_decodes_captured_power_frames(pcm_hex: str, frame: ProflameFrame) -> None:
    """RMT captures can retain only the first bit of the final `1` symbol."""

    bits = "".join(f"{byte:08b}" for byte in bytes.fromhex(pcm_hex))[:181]

    candidates = find_proflame_pcm_candidates(bits)

    assert len(candidates) == 1
    assert candidates[0].frame == frame
    assert candidates[0].frame_format == "standard_7_word_truncated_end_guard"


def test_invalid_long_high_run_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported OOK run length"):
        pulse_durations_to_bits([PCM_UNIT_US * 4])


def test_esphome_pulse_event_uses_pcm_bit_length_and_preserves_extension_metadata() -> None:
    first_seven_bits = "".join(f"{byte:08b}" for byte in frame_to_air_bytes(ISSUE_15_FRAME))[:182]
    extension_bits = "".join(_word_bits(value, trailing_bit=0) for value in ISSUE_15_EXTENSION)
    row = first_seven_bits + extension_bits + ("0" * 13)
    padded_row = row + ("0" * ((8 - (len(row) % 8)) % 8))
    event = ESPHomeRXEvent(
        event_id="pulse-1",
        raw_payload=bytes(int(padded_row[index : index + 8], 2) for index in range(0, len(padded_row), 8)),
        capture_metadata={"event_kind": "pulse_capture", "pcm_bit_length": str(len(row))},
    )

    candidates = ESPHomeAPIBackend()._scan_fifo_event(event)

    assert len(candidates) == 1
    assert candidates[0].frame == ISSUE_15_FRAME
    assert "frame_format=extended_10_word" in candidates[0].validation_notes
    assert "extension_hex=00ec77" in candidates[0].validation_notes
