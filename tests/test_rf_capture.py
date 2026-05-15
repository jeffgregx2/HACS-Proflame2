"""Tests for Proflame2 RF air-packet decoding."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.protocol

from custom_components.proflame2.protocol.packet import ProflameFrame
from custom_components.proflame2.rf.capture import (
    AIR_PACKET_BYTES,
    REASON_BAD_PARITY,
    REASON_BAD_START_END_GUARD,
    REASON_PAYLOAD_TOO_SHORT,
    decode_single_sample,
    diagnose_air_payload,
    extract_samples_from_air_bytes,
    find_proflame_candidates,
    frame_to_capture_sample,
    raw_payload_to_bit_stream,
)
from custom_components.proflame2.rf.waveform import frame_to_air_bytes, frame_to_symbol_string, symbols_to_air_bytes


def _bits_to_bytes(bit_stream: str) -> bytes:
    """Pack an arbitrary bit stream into bytes with zero padding."""

    padding = (-len(bit_stream)) % 8
    padded = bit_stream + ("0" * padding)
    return bytes(int(padded[index : index + 8], 2) for index in range(0, len(padded), 8))


def test_air_packet_round_trip(remote_profile) -> None:
    """A protocol frame should round-trip through the over-the-air encoding."""

    frame = ProflameFrame(
        serial_id=remote_profile.serial_id,
        cmd1=0x31,
        err1=0x25,
        cmd2=0x26,
        err2=0xBC,
    )

    raw_payload = frame_to_air_bytes(frame)
    assert len(raw_payload) == AIR_PACKET_BYTES

    sample = decode_single_sample(raw_payload)
    assert sample.remote_id == remote_profile.serial_id
    assert sample.cmd1_tuple == (0x31, 0x25)
    assert sample.cmd2_tuple == (0x26, 0xBC)
    assert sample.as_frame() == frame


def test_exact_trailing_guard_decodes_without_warning(remote_profile) -> None:
    """An exact SmartFire trailer should decode at full confidence."""

    frame = ProflameFrame(
        serial_id=remote_profile.serial_id,
        cmd1=0x31,
        err1=0x25,
        cmd2=0x26,
        err2=0xBC,
    )

    candidates = find_proflame_candidates(frame_to_air_bytes(frame))

    assert candidates
    assert candidates[0].trailing_guard_valid is True
    assert candidates[0].trailing_guard_warning is None


def test_air_packet_can_be_found_inside_noise(remote_profile) -> None:
    """Valid packets should still be found when surrounded by extra bytes."""

    frame = ProflameFrame(
        serial_id=remote_profile.serial_id,
        cmd1=0x01,
        err1=0x76,
        cmd2=0x01,
        err2=0x39,
    )
    raw_payload = b"\x00\xff" + frame_to_air_bytes(frame) + b"\xaa\x55"

    samples = extract_samples_from_air_bytes(raw_payload)
    assert len(samples) == 1
    assert samples[0].as_frame() == frame


def test_long_payload_with_leading_noise_and_full_frame_decodes(remote_profile) -> None:
    """Longer buffers should still find a full embedded frame after noise."""

    frame = ProflameFrame(
        serial_id=remote_profile.serial_id,
        cmd1=0x01,
        err1=0x76,
        cmd2=0x06,
        err2=0xDE,
    )
    noise = bytes.fromhex("f8000000000000014dcb")
    raw_payload = noise + frame_to_air_bytes(frame) + (b"\x00" * 48)

    candidates = find_proflame_candidates(raw_payload)

    assert candidates
    assert candidates[0].frame == frame


def test_air_packet_can_be_found_with_trailing_noise_only(remote_profile) -> None:
    """A valid packet should still decode with extra trailing bytes."""

    frame = ProflameFrame(
        serial_id=remote_profile.serial_id,
        cmd1=0x11,
        err1=0x47,
        cmd2=0x16,
        err2=0xEF,
    )

    raw_payload = frame_to_air_bytes(frame) + b"\xf0\x0d\xaa"
    sample = decode_single_sample(raw_payload)

    assert sample.as_frame() == frame


def test_air_packet_can_be_found_when_not_byte_aligned(remote_profile) -> None:
    """The RX scanner should find a valid frame even when shifted by one bit."""

    frame = ProflameFrame(
        serial_id=remote_profile.serial_id,
        cmd1=0x31,
        err1=0x25,
        cmd2=0x26,
        err2=0xBC,
    )

    shifted = _bits_to_bytes("1" + raw_payload_to_bit_stream(frame_to_air_bytes(frame)) + "0")
    candidates = find_proflame_candidates(shifted)

    assert candidates
    assert candidates[0].frame == frame
    assert candidates[0].bit_offset == 1
    assert "non_byte_aligned_start" in candidates[0].validation_notes


def test_repeated_frames_are_detected_and_preferred(remote_profile) -> None:
    """Repeated copies of the same frame should increase candidate confidence."""

    frame = ProflameFrame(
        serial_id=remote_profile.serial_id,
        cmd1=0x21,
        err1=0x14,
        cmd2=0x16,
        err2=0xEF,
    )

    raw_payload = frame_to_air_bytes(frame) + frame_to_air_bytes(frame)
    candidates = find_proflame_candidates(raw_payload)

    assert candidates
    assert candidates[0].frame == frame
    assert candidates[0].repeat_count >= 2


def test_partial_trailing_guard_decodes_with_warning(remote_profile) -> None:
    """A clipped trailer should still decode when the frame body is valid."""

    frame = ProflameFrame(
        serial_id=remote_profile.serial_id,
        cmd1=0x01,
        err1=0x76,
        cmd2=0x06,
        err2=0xDE,
    )
    symbols = list(frame_to_symbol_string(frame))
    symbols[-9:] = list("ZZZZZZS10")
    payload = symbols_to_air_bytes("".join(symbols))

    candidates = find_proflame_candidates(payload)

    assert candidates
    assert candidates[0].frame == frame
    assert candidates[0].trailing_guard_valid is False
    assert candidates[0].trailing_guard_observed == "ZZZZZZS10"
    assert candidates[0].trailing_guard_warning is not None
    assert "accepted_despite_trailing_guard_mismatch" in candidates[0].validation_notes
    assert candidates[0].packet.warnings


def test_repeated_imperfect_trailing_guard_candidates_are_preferred(remote_profile) -> None:
    """Repeated matching frames should compensate for imperfect trailer quality."""

    frame = ProflameFrame(
        serial_id=remote_profile.serial_id,
        cmd1=0x01,
        err1=0x76,
        cmd2=0x06,
        err2=0xDE,
    )
    symbols = list(frame_to_symbol_string(frame))
    symbols[-9:] = list("ZZZZZZS10")
    imperfect = symbols_to_air_bytes("".join(symbols))

    candidates = find_proflame_candidates(imperfect + imperfect + bytes.fromhex("fffffff47b"))

    assert candidates
    assert candidates[0].frame == frame
    assert candidates[0].repeat_count >= 2
    assert candidates[0].trailing_guard_warning is not None


def test_long_payload_with_multiple_repeated_frames_detects_repeats(remote_profile) -> None:
    """A long payload containing multiple full frames should record repeat agreement."""

    frame = ProflameFrame(
        serial_id=remote_profile.serial_id,
        cmd1=0x31,
        err1=0x25,
        cmd2=0x26,
        err2=0xBC,
    )
    raw_payload = (b"\x00" * 16) + frame_to_air_bytes(frame) + frame_to_air_bytes(frame) + (b"\xff" * 16)

    candidates = find_proflame_candidates(raw_payload)

    assert candidates
    assert candidates[0].frame == frame
    assert candidates[0].repeat_count >= 2


def test_frame_to_capture_sample_reuses_protocol_values(remote_profile) -> None:
    """Capture samples should normalize to the same protocol tuple structure."""

    frame = ProflameFrame(
        serial_id=remote_profile.serial_id,
        cmd1=0x21,
        err1=0x14,
        cmd2=0x16,
        err2=0xEF,
    )

    sample = frame_to_capture_sample(frame)
    assert sample.cmd1_tuple == (0x21, 0x14)
    assert sample.cmd2_tuple == (0x16, 0xEF)
    assert sample.as_frame() == frame
    assert sample.as_packet().frame == frame


def test_decode_diagnostics_reports_payload_too_short() -> None:
    """Very short payloads should produce a specific diagnostic reason."""

    diagnostics = diagnose_air_payload(b"\x01\x02")

    assert diagnostics.samples_found == 0
    assert diagnostics.best_failure is not None
    assert diagnostics.best_failure.reason == REASON_PAYLOAD_TOO_SHORT


def test_random_noise_does_not_produce_false_positive_candidates() -> None:
    """Pure random-ish noise should not decode into a valid frame."""

    noise = bytes.fromhex("fffffff47bfffffffffffefffffd6bffffffffffffbffffff7")
    diagnostics = diagnose_air_payload(noise)

    assert diagnostics.samples_found == 0
    assert diagnostics.candidates == ()


def test_partial_frame_fails_cleanly(remote_profile) -> None:
    """A truncated frame should not decode as a valid candidate."""

    frame = ProflameFrame(
        serial_id=remote_profile.serial_id,
        cmd1=0x01,
        err1=0x76,
        cmd2=0x06,
        err2=0xDE,
    )
    payload = frame_to_air_bytes(frame)[:18]

    diagnostics = diagnose_air_payload(payload)

    assert diagnostics.samples_found == 0
    assert diagnostics.best_failure is not None


def test_badly_corrupted_trailing_guard_still_rejects(remote_profile) -> None:
    """A trailer with no meaningful zero guard should still reject."""

    frame = ProflameFrame(
        serial_id=remote_profile.serial_id,
        cmd1=0x01,
        err1=0x76,
        cmd2=0x06,
        err2=0xDE,
    )
    symbols = list(frame_to_symbol_string(frame))
    symbols[-9:] = list("S101S101S")
    payload = symbols_to_air_bytes("".join(symbols))

    diagnostics = diagnose_air_payload(payload)

    assert diagnostics.samples_found == 0
    assert diagnostics.best_failure is not None
    assert diagnostics.best_failure.reason == "bad_trailing_zero_guard"


def test_decode_diagnostics_reports_bad_parity(remote_profile) -> None:
    """Parity failures should surface a specific decode reason."""

    frame = ProflameFrame(
        serial_id=remote_profile.serial_id,
        cmd1=0x31,
        err1=0x25,
        cmd2=0x26,
        err2=0xBC,
    )
    symbols = list(frame_to_symbol_string(frame))
    symbols[11] = "1" if symbols[11] == "0" else "0"
    payload = symbols_to_air_bytes("".join(symbols))

    diagnostics = diagnose_air_payload(payload)

    assert diagnostics.samples_found == 0
    assert diagnostics.best_failure is not None
    assert diagnostics.best_failure.reason == REASON_BAD_PARITY


def test_decode_diagnostics_reports_bad_start_guard(remote_profile) -> None:
    """Corrupted sync/start guards should surface the corresponding reason."""

    frame = ProflameFrame(
        serial_id=remote_profile.serial_id,
        cmd1=0x31,
        err1=0x25,
        cmd2=0x26,
        err2=0xBC,
    )
    symbols = list(frame_to_symbol_string(frame))
    symbols[0] = "0"
    payload = symbols_to_air_bytes("".join(symbols))

    diagnostics = diagnose_air_payload(payload)

    assert diagnostics.samples_found == 0
    assert diagnostics.best_failure is not None
    assert diagnostics.best_failure.reason == REASON_BAD_START_END_GUARD
