"""Tests for the unified ProflamePacket runtime model."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.protocol

from custom_components.proflame2.protocol.ecc import derive_ecc_profile
from custom_components.proflame2.protocol.encoder import encode_packet
from custom_components.proflame2.protocol.models import FireplaceState
from custom_components.proflame2.protocol.packet import ProflameFrame, ProflamePacket
from custom_components.proflame2.rf.waveform import build_transmission_plan


def test_packet_from_frame_decodes_state_and_preserves_metadata() -> None:
    """Operational packets should decode state without needing ECC profile data."""

    received_at = datetime(2026, 4, 24, 12, 30, tzinfo=timezone.utc)
    frame = ProflameFrame(
        serial_id=0x3B3F02,
        cmd1=0x31,
        err1=0x25,
        cmd2=0x26,
        err2=0xBC,
    )

    packet = ProflamePacket.from_frame(
        frame,
        source="yardstick",
        raw=b"\xaa\xbb",
        received_at=received_at,
        rssi=-42.5,
    )

    assert packet.remote_id == 0x3B3F02
    assert packet.frame == frame
    assert packet.state == FireplaceState(power=True, flame=6, fan=2, light=3)
    assert packet.raw == b"\xaa\xbb"
    assert packet.source == "yardstick"
    assert packet.received_at == received_at
    assert packet.rssi == -42.5


def test_packet_can_hold_transmission_plan_without_import_cycles(remote_profile) -> None:
    """Waveform plans should attach cleanly to the unified packet model."""

    packet = encode_packet(
        FireplaceState(power=True, flame=1, fan=0, light=0),
        remote_profile,
        source="test",
    )
    plan = build_transmission_plan(packet.frame)
    packet.transmission_plan = plan

    assert packet.transmission_plan == plan
    assert packet.transmission_plan.frame == packet.frame


def test_packet_from_frame_accepts_observed_power_off_flame_bits() -> None:
    """Receive-side packet decode should preserve observed off-frame flame bits."""

    frame = ProflameFrame(
        serial_id=0x3B3F02,
        cmd1=0x00,
        err1=0x00,
        cmd2=0x06,
        err2=0x00,
    )

    packet = ProflamePacket.from_frame(frame, source="yardstick")

    assert packet.state == FireplaceState(power=False, flame=6, fan=0, light=0)
    assert packet.warnings == ()


def test_packet_from_frame_accepts_observed_power_off_flame_one() -> None:
    """Observed off-frame packets may carry low nonzero flame bits."""

    frame = ProflameFrame(
        serial_id=0x3B3F02,
        cmd1=0x00,
        err1=0x57,
        cmd2=0x01,
        err2=0x39,
    )

    packet = ProflamePacket.from_frame(frame, source="yardstick")

    assert packet.state == FireplaceState(power=False, flame=1, fan=0, light=0)


def test_ecc_derivation_can_use_packet_frame_values(rtl433_samples, remote_profile) -> None:
    """Learning code should be able to consume packet.frame values directly."""

    cmd1_packets = [
        ProflamePacket.from_frame(
            ProflameFrame(
                serial_id=0x3B3F02,
                cmd1=sample["cmd"],
                err1=sample["err"],
                cmd2=0x01 if (sample["cmd"] & 0x01) else 0x00,
                err2=0x39 if (sample["cmd"] & 0x01) else 0x18,
            ),
            source="capture",
        )
        for sample in rtl433_samples["cmd1_samples"]
    ]
    cmd2_packets = [
        ProflamePacket.from_frame(
            ProflameFrame(
                serial_id=0x3B3F02,
                cmd1=0x01 if (sample["cmd"] & 0x07) else 0x03,
                err1=0x76 if (sample["cmd"] & 0x07) else 0x34,
                cmd2=sample["cmd"],
                err2=sample["err"],
            ),
            source="capture",
        )
        for sample in rtl433_samples["cmd2_samples"]
    ]

    profile = derive_ecc_profile(
        [(packet.frame.cmd1, packet.frame.err1) for packet in cmd1_packets],
        [(packet.frame.cmd2, packet.frame.err2) for packet in cmd2_packets],
    )

    assert profile == remote_profile.ecc
