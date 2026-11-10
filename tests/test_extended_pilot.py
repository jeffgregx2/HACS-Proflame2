"""Synthetic Pilot regressions through codec and HA control paths."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("homeassistant")
pytest.importorskip("pytest_homeassistant_custom_component")

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.proflame2.const import (
    BACKEND_FAKE,
    CONF_AUX,
    CONF_BACKEND_TYPE,
    CONF_C1,
    CONF_C2,
    CONF_CPI,
    CONF_D1,
    CONF_D2,
    CONF_EXTENDED_W4_BASE,
    CONF_EXTENDED_W6,
    CONF_EXTENDED_W7,
    CONF_EXTENDED_W8,
    CONF_FAN,
    CONF_FRONT,
    CONF_LIGHT,
    CONF_PROTOCOL_VARIANT,
    CONF_REMOTE_ID,
    DATA_CONFIRMATION_WINDOW_SECONDS,
    DATA_CONTROL_DEBOUNCE_SECONDS,
    DOMAIN,
)
from custom_components.proflame2.protocol.decoder import decode_state
from custom_components.proflame2.protocol.ecc import build_extended_integrity_words, extended_frame_integrity_matches
from custom_components.proflame2.protocol.encoder import encode_state
from custom_components.proflame2.protocol.models import ECCProfile, ExtendedFrameTemplate, FireplaceState, RemoteProfile
from custom_components.proflame2.protocol.packet import ProflameFrame, ProflamePacket
from custom_components.proflame2.runtime import (
    async_get_runtime_entries,
    async_initialize_safe_default_runtime_state,
    runtime_current_state,
)
from custom_components.proflame2.services import async_apply_observed_packet

pytestmark = pytest.mark.ha


@pytest.fixture(autouse=True)
def enable_integration(enable_custom_integrations):
    yield


def profile(flags=0x88):
    return RemoteProfile(
        0x123456,
        ECCProfile(0, 0, 0, 0),
        protocol_variant="extended_10_word",
        extended_template=ExtendedFrameTemplate(flags, 20, 200, 0),
    )


def observed(cpi):
    # Build independent state words rather than using the encoder under test.
    w4 = 0x21 | (0x80 if cpi else 0)
    w9, w10 = build_extended_integrity_words(0x123456, w4, 3, 20, 200, 0)
    return ProflamePacket.from_frame(ProflameFrame(0x123456, w4, 20, 3, 200, (0, w9, w10)))


@pytest.mark.parametrize("flags", [0x00, 0x08, 0x80, 0x88, 0x8C])
def test_pilot_can_set_and_clear_independently_of_learned_flags(flags):
    remote = profile(flags)
    off = encode_state(FireplaceState(True, flame=3, light=2, cpi=False), remote)
    on = encode_state(FireplaceState(True, flame=3, light=2, cpi=True), remote)
    assert off.cmd1 & 0x80 == 0
    assert on.cmd1 == off.cmd1 | 0x80
    assert off.cmd1 & 0x0C == flags & 0x0C  # Units and the unassigned bit are retained.
    assert off.extension_words[:2] == on.extension_words[:2]  # W8/W9 unchanged.
    assert off.extension_words[2] != on.extension_words[2]
    for frame, cpi in [(off, False), (on, True)]:
        assert extended_frame_integrity_matches(frame.wire_words)
        assert decode_state(frame, remote).cpi is cpi
        with pytest.raises(ValueError, match="integrity"):
            decode_state(replace(frame, cmd1=frame.cmd1 ^ 0x80), remote)


@pytest.mark.parametrize("cpi", [False, True])
def test_observed_pilot_comes_from_wire_not_learned_template(cpi):
    packet = observed(cpi)
    assert packet.state.cpi is cpi
    assert decode_state(packet.frame, profile(0x80 if not cpi else 0)).cpi is cpi


@pytest.mark.parametrize("cpi", [False, True])
async def test_safe_initial_state_retains_learned_pilot(hass, monkeypatch, cpi):
    runtime = SimpleNamespace(remote_profile=profile(0x80 if cpi else 0), last_packet=None, config_entry_id="synthetic")
    monkeypatch.setattr("custom_components.proflame2.runtime.async_persist_runtime_entry_state", AsyncMock())
    await async_initialize_safe_default_runtime_state(hass, runtime)
    assert runtime.last_packet.state == FireplaceState(False, flame=0, cpi=cpi)
    assert bool(runtime.last_packet.frame.cmd1 & 0x80) is cpi


async def setup_fireplace(hass, *, cpi_enabled, initial_cpi, extended=True):
    hass.data.setdefault(DOMAIN, {})[DATA_CONTROL_DEBOUNCE_SECONDS] = 0.001
    hass.data[DOMAIN][DATA_CONFIRMATION_WINDOW_SECONDS] = 0
    data = {
        "name": "Pilot Test",
        CONF_BACKEND_TYPE: BACKEND_FAKE,
        CONF_REMOTE_ID: 0x123456,
        CONF_C1: 0,
        CONF_D1: 0,
        CONF_C2: 0,
        CONF_D2: 0,
    }
    if extended:
        data.update(
            {
                CONF_PROTOCOL_VARIANT: "extended_10_word",
                CONF_EXTENDED_W4_BASE: 0x80 if initial_cpi else 0,
                CONF_EXTENDED_W6: 20,
                CONF_EXTENDED_W7: 200,
                CONF_EXTENDED_W8: 0,
            }
        )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Pilot Test",
        data=data,
        options={CONF_CPI: cpi_enabled, CONF_LIGHT: True, CONF_FAN: True, CONF_FRONT: True, CONF_AUX: True},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, async_get_runtime_entries(hass)[entry.entry_id]


async def change_switch(hass, value):
    await hass.services.async_call(
        "switch", "turn_on" if value else "turn_off", {"entity_id": "switch.pilot_test_cpi"}, blocking=True
    )
    await asyncio.sleep(0.05)
    await hass.async_block_till_done()


@pytest.mark.parametrize("initial_cpi", [False, True])
async def test_cpi_switch_observation_and_reload_follow_latest_mode(hass, initial_cpi):
    entry, runtime = await setup_fireplace(hass, cpi_enabled=True, initial_cpi=initial_cpi)
    assert runtime_current_state(runtime).cpi is initial_cpi
    for value in [not initial_cpi, initial_cpi, False]:
        await change_switch(hass, value)
        assert runtime_current_state(runtime).cpi is value
        assert bool(runtime.backend.sent_packets[-1].frame.cmd1 & 0x80) is value
        assert hass.states.get("switch.pilot_test_cpi").state == ("on" if value else "off")
    await async_apply_observed_packet(hass, runtime, observed(True))
    await hass.async_block_till_done()
    assert hass.states.get("switch.pilot_test_cpi").state == "on"
    await change_switch(hass, False)
    assert runtime.backend.sent_packets[-1].frame.cmd1 & 0x80 == 0
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    restored = async_get_runtime_entries(hass)[entry.entry_id]
    assert runtime_current_state(restored).cpi is False
    assert hass.states.get("switch.pilot_test_cpi").state == "off"
    assert restored.backend.sent_packets == []


@pytest.mark.parametrize("cpi_enabled", [False, True])
@pytest.mark.parametrize("initial_cpi", [False, True])
async def test_unrelated_entity_and_service_changes_preserve_pilot(hass, cpi_enabled, initial_cpi):
    _, runtime = await setup_fireplace(hass, cpi_enabled=cpi_enabled, initial_cpi=initial_cpi)
    for value in [initial_cpi, not initial_cpi]:
        await async_apply_observed_packet(hass, runtime, observed(value))
        await hass.services.async_call(
            "number", "set_value", {"entity_id": "number.pilot_test_light", "value": 4}, blocking=True
        )
        await asyncio.sleep(0.05)
        await hass.async_block_till_done()
        assert bool(runtime.backend.sent_packets[-1].frame.cmd1 & 0x80) is value
        await hass.services.async_call(DOMAIN, "set_state", {"power": True, "flame": 2}, blocking=True)
        assert runtime_current_state(runtime).cpi is value
        assert bool(runtime.backend.sent_packets[-1].frame.cmd1 & 0x80) is value


async def test_explicit_service_cpi_overrides_the_learned_mode(hass):
    _, runtime = await setup_fireplace(hass, cpi_enabled=True, initial_cpi=True)
    for value in [False, True, False]:
        await hass.services.async_call(DOMAIN, "set_state", {"power": True, "flame": 2, "cpi": value}, blocking=True)
        assert runtime_current_state(runtime).cpi is value
        assert bool(runtime.backend.sent_packets[-1].frame.cmd1 & 0x80) is value


async def set_off_observation(hass, runtime, *, cpi=False):
    state = FireplaceState(False, flame=3, fan=4, light=2, front=True, aux=True, cpi=cpi)
    frame = encode_state(state, runtime.remote_profile, allow_power_off_flame=True)
    await async_apply_observed_packet(hass, runtime, ProflamePacket.from_frame(frame))
    await hass.async_block_till_done()
    assert runtime.backend.sent_packets == []
    return state


@pytest.mark.parametrize("extended", [False, True])
@pytest.mark.parametrize("initial_cpi", [False, True])
async def test_off_cpi_switch_transmits_without_changing_power_or_settings(hass, extended, initial_cpi):
    _, runtime = await setup_fireplace(hass, cpi_enabled=True, initial_cpi=initial_cpi, extended=extended)
    state = await set_off_observation(hass, runtime, cpi=initial_cpi)
    for count, value in enumerate([not initial_cpi, initial_cpi], start=1):
        await change_switch(hass, value)
        assert len(runtime.backend.sent_packets) == count
        packet = runtime.backend.sent_packets[-1]
        assert packet.state == replace(state, cpi=value)
        assert decode_state(packet.frame, runtime.remote_profile) == packet.state
        assert packet.frame.cmd1 & 1 == 0
        assert bool(packet.frame.cmd1 & 0x80) is value
        assert runtime_current_state(runtime) == packet.state
        assert hass.states.get("switch.pilot_test_power").state == "off"
    # Redundant CPI and ordinary OFF-state accessory edits still need no TX.
    await change_switch(hass, initial_cpi)
    await hass.services.async_call(
        "number", "set_value", {"entity_id": "number.pilot_test_light", "value": 5}, blocking=True
    )
    await asyncio.sleep(0.05)
    await hass.async_block_till_done()
    assert len(runtime.backend.sent_packets) == 2
    assert runtime_current_state(runtime) == replace(state, light=5)


@pytest.mark.parametrize("extended", [False, True])
async def test_off_accessory_edit_cannot_discard_pending_cpi_transmission(hass, extended):
    _, runtime = await setup_fireplace(hass, cpi_enabled=True, initial_cpi=False, extended=extended)
    state = await set_off_observation(hass, runtime)
    hass.data[DOMAIN][DATA_CONTROL_DEBOUNCE_SECONDS] = 0.05
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.pilot_test_cpi"}, blocking=True)
    await hass.services.async_call(
        "number", "set_value", {"entity_id": "number.pilot_test_light", "value": 5}, blocking=True
    )
    await asyncio.sleep(0.12)
    await hass.async_block_till_done()
    assert len(runtime.backend.sent_packets) == 1
    packet = runtime.backend.sent_packets[0]
    assert packet.state == replace(state, cpi=True, light=5)
    assert decode_state(packet.frame, runtime.remote_profile) == packet.state
    # Cancelling a pending CPI change before debounce must not send stale mode.
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.pilot_test_cpi"}, blocking=True)
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.pilot_test_cpi"}, blocking=True)
    await asyncio.sleep(0.12)
    await hass.async_block_till_done()
    assert len(runtime.backend.sent_packets) == 1
    assert runtime_current_state(runtime) == packet.state
