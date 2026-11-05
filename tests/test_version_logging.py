"""Debug captures identify the configured build without invoking the controller."""

import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from homeassistant.const import EVENT_LOGGING_CHANGED
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.proflame2.const import BACKEND_ESPHOME, BACKEND_YARDSTICK, CONF_ESPHOME_ENTRY_ID, DOMAIN
from custom_components.proflame2.rf.esphome.transport import HomeAssistantESPHomeTransport
from custom_components.proflame2.version_logging import async_register_version_logging

LOGGER = "custom_components.proflame2"


def _records(caplog):
    return [r.getMessage() for r in caplog.records if r.name == LOGGER + ".version_logging"]


def _case(hass, *, firmware="v0.7.0-beta4", tuple_keys=True):
    linked = MockConfigEntry(domain="esphome", title="Controller")
    linked.add_to_hass(hass)
    key = (0, 7) if tuple_keys else 7
    value = SimpleNamespace(state=firmware)
    linked.runtime_data = SimpleNamespace(
        available=True,
        info={str: {key: SimpleNamespace(object_id="proflame2_firmware_version", key=7)}},
        state={str: {key: value}},
        services={},
        device_info=SimpleNamespace(esphome_version="2026.9.0"),
    )
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_ESPHOME_ENTRY_ID: linked.entry_id})
    entry.add_to_hass(hass)
    transport = HomeAssistantESPHomeTransport(
        hass, linked_entry_id=linked.entry_id, controller_id="test", debug_logging_enabled=False
    )
    runtime = SimpleNamespace(backend_type=BACKEND_ESPHOME, backend=SimpleNamespace(transport=transport))
    entity = er.async_get(hass).async_get_or_create(
        "text_sensor",
        "esphome",
        "version-" + linked.entry_id,
        config_entry=linked,
        suggested_object_id="renamed_controller_version",
    )
    return entry, linked, runtime, transport, value, entity.entity_id


@pytest.mark.parametrize("tuple_keys", [False, True])
@pytest.mark.parametrize(
    "value,expected",
    [("v0.7.0-beta4", "v0.7.0-beta4"), (None, None), ("", None), ("unknown", None), ("unavailable", None)],
)
async def test_firmware_reader_uses_live_proflame_telemetry_without_actions(hass, tuple_keys, value, expected):
    entry, linked, runtime, transport, state, entity_id = _case(hass, firmware=value, tuple_keys=tuple_keys)
    assert transport.proflame2_firmware_version() == expected
    assert not transport.connected
    assert not transport.configurations
    assert not transport.tx_requests
    assert not linked.runtime_data.services


async def test_firmware_reader_rejects_disconnected_cache_and_resolves_fresh_runtime(hass):
    entry, linked, runtime, transport, value, entity_id = _case(hass)
    old = linked.runtime_data
    transport._runtime_data = old
    old.available = False
    assert transport.proflame2_firmware_version() is None
    linked.runtime_data = SimpleNamespace(
        available=True, info=old.info, state={str: {(0, 7): SimpleNamespace(state="v0.7.0-beta5")}}
    )
    assert transport.proflame2_firmware_version() == "v0.7.0-beta5"
    linked.runtime_data = None
    assert transport.proflame2_firmware_version() is None


async def test_ha_logger_service_starts_new_debug_session_without_duplicate_headers(hass, caplog, monkeypatch):
    assert await async_setup_component(hass, "logger", {})
    monkeypatch.setenv("PROFLAME2_VERSION", "9.8.7-beta1")
    entry, linked, runtime, transport, value, entity_id = _case(hass)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        # Capture DEBUG records without enabling the integration's own logger.
        caplog.set_level(logging.DEBUG)
        async_register_version_logging(hass, entry, runtime)
        assert not _records(caplog)
        for level in ("info", "debug", "debug"):
            await hass.services.async_call("logger", "set_level", {LOGGER: level}, blocking=True)
            await hass.async_block_till_done()
        assert len(_records(caplog)) == 1
        assert "integration=9.8.7-beta1" in _records(caplog)[0]
        assert "proflame2_firmware=v0.7.0-beta4" in _records(caplog)[0]
        assert "2026.9.0" not in _records(caplog)[0]
        await hass.services.async_call("logger", "set_level", {"unrelated.component": "debug"}, blocking=True)
        await hass.async_block_till_done()
        assert len(_records(caplog)) == 1
        for level in ("warning", "debug"):
            await hass.services.async_call("logger", "set_level", {LOGGER: level}, blocking=True)
            await hass.async_block_till_done()
        assert len(_records(caplog)) == 2
        await entry._async_process_on_unload(hass)


async def test_late_firmware_and_version_changes_are_reported_once_for_linked_entry(hass, caplog):
    entry, linked, runtime, transport, value, entity_id = _case(hass, firmware=None)
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        async_register_version_logging(hass, entry, runtime)
        assert "proflame2_firmware=unavailable" in _records(caplog)[0]
        for version in ("v0.7.0-beta4", "v0.7.0-beta4", "v0.7.0-beta5"):
            value.state = version
            hass.states.async_set(entity_id, version)
            await hass.async_block_till_done()
        assert len(_records(caplog)) == 3
        assert "proflame2_firmware=v0.7.0-beta5" in _records(caplog)[-1]
        other_entry, other_linked, other_runtime, other_transport, other_value, other_entity = _case(hass)
        value.state = "v0.7.0-dev"
        hass.states.async_set(other_entity, "changed")
        await hass.async_block_till_done()
        assert len(_records(caplog)) == 3
        linked.runtime_data.available = False
        hass.states.async_set(entity_id, "unavailable")
        await hass.async_block_till_done()
        assert "proflame2_firmware=unavailable" in _records(caplog)[-1]
        assert len(_records(caplog)) == 4
        assert not transport.connected
        assert not transport.configurations
        assert not transport.tx_requests
        await entry._async_process_on_unload(hass)


async def test_yardstick_omits_firmware_and_unload_reload_owns_observers(hass, caplog):
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    backend = Mock()
    runtime = SimpleNamespace(backend_type=BACKEND_YARDSTICK, backend=backend)
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        async_register_version_logging(hass, entry, runtime)
        assert len(_records(caplog)) == 1
        assert "controller=yardstick" in _records(caplog)[0]
        assert "firmware" not in _records(caplog)[0]
        await entry._async_process_on_unload(hass)
        hass.bus.async_fire(EVENT_LOGGING_CHANGED)
        await hass.async_block_till_done()
        assert len(_records(caplog)) == 1
        async_register_version_logging(hass, entry, runtime)
        hass.bus.async_fire(EVENT_LOGGING_CHANGED)
        await hass.async_block_till_done()
        assert len(_records(caplog)) == 2
        backend.assert_not_called()
        assert not backend.mock_calls
        await entry._async_process_on_unload(hass)


async def test_entry_setup_automatically_registers_version_header(hass, caplog, enable_custom_integrations):
    from custom_components.proflame2.version import integration_version

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"name": "Test", "backend_type": "fake", "remote_id": 0x123456, "c1": 5, "d1": 7, "c2": 1, "d2": 8},
    )
    entry.add_to_hass(hass)
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert any("integration=" + integration_version() in line for line in _records(caplog))
        assert await hass.config_entries.async_unload(entry.entry_id)
        caplog.clear()
        hass.bus.async_fire(EVENT_LOGGING_CHANGED)
        await hass.async_block_till_done()
        assert not _records(caplog)
