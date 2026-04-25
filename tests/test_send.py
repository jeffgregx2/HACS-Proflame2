"""Tests for transmit-side backend boundaries."""

from __future__ import annotations

import asyncio
import pytest

pytestmark = pytest.mark.protocol

from custom_components.proflame2.protocol.encoder import encode_packet, encode_state
from custom_components.proflame2.protocol.models import FireplaceState
from custom_components.proflame2.rf.fake import FakeRFBackend
from custom_components.proflame2.rf.waveform import (
    AIR_PACKET_BYTES,
    SMARTFIRE_DEFAULT_RFCAT_REPEAT,
    SMARTFIRE_DEFAULT_TOTAL_TRANSMISSIONS,
    build_transmission_plan,
    frame_to_symbol_string,
)


def test_fake_backend_records_send_result(remote_profile) -> None:
    """The fake backend should preserve the unified packet and derived views."""

    async def _run() -> None:
        backend = FakeRFBackend()
        await backend.connect()
        state = FireplaceState(power=True, flame=1, fan=0, light=0)
        packet = encode_packet(state, remote_profile, source="test")
        result = await backend.send(packet)

        assert result.requested_state == state
        assert result.packet == packet
        assert result.encoded_frame == packet.frame
        assert result.backend_name == "fake"
        assert backend.sent_packets == [packet]
        assert backend.sent_frames == [packet.frame]
        assert backend.sent_results == [result]

    asyncio.run(_run())


def test_transmission_plan_separates_logical_frame_from_rf_payload(remote_profile) -> None:
    """Logical frame bytes and RF serialization should remain distinct layers."""

    state = FireplaceState(power=True, flame=1, fan=0, light=0)
    frame = encode_state(state, remote_profile)
    plan = build_transmission_plan(frame)

    assert plan.frame == frame
    assert frame.as_bytes() == bytes.fromhex("3b3f0201760139")
    assert len(plan.air_payload) == AIR_PACKET_BYTES
    assert plan.air_payload != frame.as_bytes()
    assert plan.repeat_count == SMARTFIRE_DEFAULT_TOTAL_TRANSMISSIONS
    assert plan.backend_repeat_argument == SMARTFIRE_DEFAULT_RFCAT_REPEAT
    assert plan.preamble_bytes == b""
    assert plan.repeat_spacing_ms is None
    assert plan.sync_strategy == "embedded_symbol_sync"
    assert any("RfCat.RFxmit" in note for note in plan.notes)


def test_waveform_matches_exact_smartfire_low_state_bytes(remote_profile) -> None:
    """Known low-state frame should serialize to the exact SmartFire byte stream."""

    state = FireplaceState(power=True, flame=1, fan=0, light=0)
    frame = encode_state(state, remote_profile)
    plan = build_transmission_plan(frame)

    assert plan.symbol_string == frame_to_symbol_string(frame)
    assert plan.air_payload.hex() == "e5a9a9b96aa96e55596b95559ae55566b9a9a5ae5a96580000"


def test_waveform_matches_exact_smartfire_high_state_bytes(remote_profile) -> None:
    """Known higher-state frame should serialize to the exact SmartFire byte stream."""

    state = FireplaceState(power=True, flame=6, fan=2, light=3)
    frame = encode_state(state, remote_profile)
    plan = build_transmission_plan(frame)

    assert plan.air_payload.hex() == "e5a9a9b96aa96e55596b96959ae59696b96599ae9aa5680000"
