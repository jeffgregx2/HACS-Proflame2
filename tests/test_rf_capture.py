"""Tests for Proflame2 RF air-packet decoding."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.protocol

from proflame2_protocol.packet import ProflameFrame
from proflame2_rf.capture import (
    AIR_PACKET_BYTES,
    decode_single_sample,
    extract_samples_from_air_bytes,
    frame_to_capture_sample,
)
from proflame2_rf.waveform import frame_to_air_bytes


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


def test_air_packet_can_be_found_inside_noise(remote_profile) -> None:
    """Valid packets should still be found when surrounded by extra bytes."""

    frame = ProflameFrame(
        serial_id=remote_profile.serial_id,
        cmd1=0x01,
        err1=0x76,
        cmd2=0x01,
        err2=0x39,
    )
    raw_payload = b"\x00\xFF" + frame_to_air_bytes(frame) + b"\xAA\x55"

    samples = extract_samples_from_air_bytes(raw_payload)
    assert len(samples) == 1
    assert samples[0].as_frame() == frame


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
