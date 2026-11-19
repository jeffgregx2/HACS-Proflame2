"""Entity registration must preserve the runtime's controller device-ID link."""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from test_esphome_runtime import (
    _add_entry,
    _add_linked_esphome_device,
    _add_linked_esphome_entry,
    _fake_esphome_runtime_data,
)

import custom_components.proflame2 as integration
from custom_components.proflame2 import runtime as runtime_module
from custom_components.proflame2.const import (
    BACKEND_YARDSTICK,
    CONF_ESPHOME_ENTRY_ID,
    CONF_FLAME,
    CONF_NAME,
    CONF_POWER,
    CONF_PROFILES,
)
from custom_components.proflame2.device_registry_compat import (
    ModernDeviceRegistryCompatibility,
    device_registry_compatibility,
)
from custom_components.proflame2.rf.yardstick import YardStickBackend
from custom_components.proflame2.runtime import async_get_runtime_entries


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    yield


def _entity_identities(hass, entry):
    return {
        entity.entity_id: (entity.unique_id, entity.device_id)
        for entity in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    }


@pytest.mark.parametrize("has_identifier", [True, False])
@pytest.mark.parametrize("late_parent", [False, True])
async def test_all_entity_platforms_preserve_parent_through_registration_and_reload(
    hass, monkeypatch, has_identifier, late_parent
):
    registry = dr.async_get(hass)
    linked = _add_linked_esphome_entry(hass, _fake_esphome_runtime_data())
    device_arguments = (
        {"identifier": ("esphome", "synthetic-controller")}
        if has_identifier
        else {"connection": ("mac", "02:00:00:00:00:01")}
    )
    controller_id = None if late_parent else _add_linked_esphome_device(hass, linked, **device_arguments)
    monkeypatch.setattr(integration, "_DISPLAY_SYNC_RETRY_DELAYS_SECONDS", (0.01, 0.02, 0.05))
    parent_visible = asyncio.Event()
    if late_parent:
        original_retry = runtime_module.async_retry_runtime_device_link

        async def retry_after_parent(hass_arg, runtime_arg, entry_arg, *, delays):
            # Gate the real retry on fixture publication, avoiding both the
            # production backoffs and a race with platform setup duration.
            await parent_visible.wait()
            return await original_retry(hass_arg, runtime_arg, entry_arg, delays=(0,))

        monkeypatch.setattr(runtime_module, "async_retry_runtime_device_link", retry_after_parent)
    entry = _add_entry(
        hass,
        options={
            CONF_ESPHOME_ENTRY_ID: linked.entry_id,
            CONF_PROFILES: {"low": {CONF_NAME: "Low", CONF_POWER: True, CONF_FLAME: 2}},
        },
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    if not late_parent:
        await hass.async_block_till_done()
    runtime = async_get_runtime_entries(hass)[entry.entry_id]
    fireplace_id = runtime.device_id
    assert registry.async_get(fireplace_id).via_device_id == controller_id
    identities = _entity_identities(hass, entry)
    assert {entity_id.split(".")[0] for entity_id in identities} == {"sensor", "switch", "number", "button"}
    assert {device_id for _, device_id in identities.values()} == {fireplace_id}

    if late_parent:
        linked_event = asyncio.Event()

        @callback
        def parent_updated(event):
            if event.data.get("device_id") == fireplace_id:
                device = registry.async_get(fireplace_id)
                if device is not None and device.via_device_id == controller_id:
                    linked_event.set()

        unsubscribe = hass.bus.async_listen(dr.EVENT_DEVICE_REGISTRY_UPDATED, parent_updated)
        try:
            controller_id = _add_linked_esphome_device(hass, linked, **device_arguments)
            parent_visible.set()
            async with asyncio.timeout(1):
                await linked_event.wait()
        finally:
            unsubscribe()
        assert runtime.controller_device_id == controller_id
        assert _entity_identities(hass, entry) == identities

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    runtime = async_get_runtime_entries(hass)[entry.entry_id]
    assert runtime.device_id == fireplace_id
    assert runtime.controller_device_id == controller_id
    assert registry.async_get(fireplace_id).via_device_id == controller_id
    assert len(dr.async_entries_for_config_entry(registry, entry.entry_id)) == 1
    assert _entity_identities(hass, entry) == identities


async def test_same_controller_identifiers_in_different_entries_keep_correct_parents(hass):
    registry = dr.async_get(hass)
    if not isinstance(device_registry_compatibility(registry), ModernDeviceRegistryCompatibility):
        pytest.skip("Legacy HA merges devices with the same globally unique identifier")
    controllers = []
    fireplaces = []
    for index in range(2):
        linked = _add_linked_esphome_entry(hass, _fake_esphome_runtime_data())
        controllers.append(_add_linked_esphome_device(hass, linked, identifier=("esphome", "same-identifier")))
        entry = _add_entry(
            hass,
            title=f"Fireplace {index}",
            remote_id=0x3B3F02 + index,
            options={CONF_ESPHOME_ENTRY_ID: linked.entry_id},
        )
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        fireplaces.append(async_get_runtime_entries(hass)[entry.entry_id].device_id)
    assert controllers[0] != controllers[1]
    assert fireplaces[0] != fireplaces[1]
    assert [registry.async_get(device_id).via_device_id for device_id in fireplaces] == controllers


async def test_removed_controller_detaches_and_reload_links_replacement(hass):
    from esphome_public_helpers import install_public_endpoint

    registry = dr.async_get(hass)
    native = _fake_esphome_runtime_data()
    linked = _add_linked_esphome_entry(hass, native)
    old_controller = _add_linked_esphome_device(hass, linked, identifier=("esphome", "old-controller"))
    entry = _add_entry(hass, options={CONF_ESPHOME_ENTRY_ID: linked.entry_id})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    fireplace_id = async_get_runtime_entries(hass)[entry.entry_id].device_id
    identities = _entity_identities(hass, entry)
    registry.async_remove_device(old_controller)
    await hass.async_block_till_done()
    assert registry.async_get(fireplace_id).via_device_id is None
    install_public_endpoint(hass, linked, native)
    new_controller = _add_linked_esphome_device(hass, linked, identifier=("esphome", "replacement-controller"))
    assert new_controller != old_controller
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert async_get_runtime_entries(hass)[entry.entry_id].device_id == fireplace_id
    assert registry.async_get(fireplace_id).via_device_id == new_controller
    assert _entity_identities(hass, entry) == identities


async def test_yardstick_entities_and_band_control_remain_on_one_parentless_device(hass, monkeypatch):
    async def no_hardware_connect(self):
        return None

    monkeypatch.setattr(YardStickBackend, "connect", no_hardware_connect)
    entry = _add_entry(hass, backend_type=BACKEND_YARDSTICK)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    runtime = async_get_runtime_entries(hass)[entry.entry_id]
    registry = dr.async_get(hass)
    identities = _entity_identities(hass, entry)
    assert "select" in {entity_id.split(".")[0] for entity_id in identities}
    assert {device_id for _, device_id in identities.values()} == {runtime.device_id}
    assert registry.async_get(runtime.device_id).via_device_id is None
    assert len(dr.async_entries_for_config_entry(registry, entry.entry_id)) == 1
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert _entity_identities(hass, entry) == identities
    assert registry.async_get(runtime.device_id).via_device_id is None


def test_production_does_not_supply_deprecated_identifier_parent_hints():
    root = Path(__file__).resolve().parents[1] / "custom_components/proflame2"
    uses = [
        (str(path.relative_to(root)), node.lineno)
        for path in root.rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.keyword) and node.arg == "via_device"
    ]
    assert uses == []
