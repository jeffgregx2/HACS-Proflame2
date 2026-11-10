"""Payload-only diagnostics are opt-in and do not alter fireplace intent."""

import logging

import pytest

from custom_components.proflame2.rf.esphome.contract import ESPHomeRXEvent
from custom_components.proflame2.rf.esphome_api import ESPHomeAPIBackend
from custom_components.proflame2.rf.fake import FakeRFBackend


def _add_entry(hass):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain="proflame2",
        title="Living Room",
        data={"name": "Living Room", "backend_type": "fake", "remote_id": 0x123456, "c1": 5, "d1": 7, "c2": 1, "d2": 8},
    )
    entry.add_to_hass(hass)
    return entry


def test_pcm_logging_default_off_and_full_payload_toggle(caplog):
    backend = FakeRFBackend()
    other = FakeRFBackend()
    payload = bytes(range(255))
    with caplog.at_level(logging.INFO, logger="custom_components.proflame2.pcm"):
        backend.log_pcm("RX", payload)
        assert not caplog.records
        backend.set_pcm_logging_enabled(True)
        backend.log_pcm("TX", payload, bit_length=2039, context="stage=request")
        other.log_pcm("RX", payload)
        backend.set_pcm_logging_enabled(False)
        backend.log_pcm("RX", payload)
    assert len(caplog.records) == 1
    assert f"pcm_hex={payload.hex()}" in caplog.text
    assert "bits=2039" in caplog.text
    assert "stage=request" in caplog.text


@pytest.mark.parametrize("kind", ["pulse_capture", "fifo_capture", "rx_packet", "unknown"])
def test_pcm_receive_logs_before_rejection_and_without_verbose_debug(caplog, kind):
    backend = ESPHomeAPIBackend()
    backend.set_pcm_logging_enabled(True)
    assert not backend.debug_logging_enabled
    event = ESPHomeRXEvent(
        event_id="test-event",
        raw_payload=b"\xde\xad\x80",
        capture_metadata={"event_kind": kind, "pcm_bit_length": "17"},
    )
    with caplog.at_level(logging.INFO, logger="custom_components.proflame2.pcm"):
        backend._rx_event_is_decodable(event, strict_packet_policy=True)
    assert len([record for record in caplog.records if record.name.endswith(".pcm")]) == 1
    assert "bits=17" in caplog.text
    assert "pcm_hex=dead80" in caplog.text


@pytest.mark.ha
async def test_pcm_switch_is_diagnostic_default_off_and_never_sends(hass, enable_custom_integrations):
    from homeassistant.helpers import entity_registry as er

    from custom_components.proflame2.runtime import async_get_runtime_entries

    entry = _add_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    runtime = async_get_runtime_entries(hass)[entry.entry_id]
    entity_id = next(
        entity.entity_id
        for entity in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if entity.unique_id.endswith("pcm_logging_control")
    )
    assert er.async_get(hass).async_get(entity_id).entity_category.value == "diagnostic"
    assert hass.states.get(entity_id).state == "off"
    for action, expected in [("turn_on", True), ("turn_off", False)]:
        await hass.services.async_call("switch", action, {"entity_id": entity_id}, blocking=True)
        assert runtime.backend.pcm_logging_enabled is expected
        assert runtime.desired_state is None
        assert not runtime.sending_in_progress
        assert runtime.backend.sent_packets == []


@pytest.mark.ha
async def test_pcm_switch_restores_explicit_opt_in(hass, enable_custom_integrations):
    from homeassistant.core import State
    from pytest_homeassistant_custom_component.common import mock_restore_cache

    entry = _add_entry(hass)
    mock_restore_cache(hass, [State("switch.living_room_pcm_payload_logging", "on")])
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("switch.living_room_pcm_payload_logging").state == "on"
