"""Synthetic endpoint publication through real HA registries/services/states."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.helpers import entity_registry as er


def install_public_endpoint(hass, entry, native):
    """Attach fake firmware to public HA interfaces, not production internals."""
    entry._proflame2_public_fixture = True
    node = entry.data.get("device_name") or f"test_{entry.entry_id.lower()}"
    hass.config_entries.async_update_entry(entry, data={**entry.data, "device_name": node})
    if native is None:
        entry.mock_state(hass, ConfigEntryState.NOT_LOADED)
        for entity in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id):
            hass.states.async_set(entity.entity_id, STATE_UNAVAILABLE)
        for name in list(hass.services.async_services().get("esphome", {})):
            if name.startswith(node + "_"):
                hass.services.async_remove("esphome", name)
        return
    entry.mock_state(hass, ConfigEntryState.LOADED)
    registry = er.async_get(hass)
    for object_id, value in native.object_states.items():
        entity = registry.async_get_or_create(
            "sensor",
            "esphome",
            f"{entry.entry_id}-text_sensor-{object_id}",
            config_entry=entry,
            suggested_object_id=f"{node}_{object_id}",
        )

        def publish(state, *, entity_id=entity.entity_id):
            hass.states.async_set(entity_id, str(state) if state is not None else "unknown")

        value._publish = publish
        publish(value.state)

    def availability_changed(available):
        state = native.object_states["proflame2_endpoint_status"]
        state._publish(state.state if available else STATE_UNAVAILABLE)

    native._public_availability_changed = availability_changed
    availability_changed(native.available)
    for name in list(hass.services.async_services().get("esphome", {})):
        if name.startswith(node + "_"):
            hass.services.async_remove("esphome", name)
    for service in native.services.values():

        async def handler(call, *, service=service):
            await native.client.execute_service(service, call.data)

        hass.services.async_register("esphome", f"{node}_{service.name}", handler)
