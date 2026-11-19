"""The registry compatibility boundary preserves ownership across HA models."""

from __future__ import annotations

import ast
import inspect
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from homeassistant import const as ha_const
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.proflame2 import _async_migrate_registry_identity
from custom_components.proflame2.const import CONF_REMOTE_ID, DOMAIN
from custom_components.proflame2.device_registry_compat import (
    LegacyDeviceRegistryCompatibility,
    ModernDeviceRegistryCompatibility,
    device_registry_compatibility,
)


@pytest.mark.parametrize(
    "version,expected",
    [
        ((2025, 8), LegacyDeviceRegistryCompatibility),
        ((2026, 7), LegacyDeviceRegistryCompatibility),
        ((2026, 8), ModernDeviceRegistryCompatibility),
        ((2026, 11), ModernDeviceRegistryCompatibility),
        ((2027, 1), ModernDeviceRegistryCompatibility),
    ],
)
def test_registry_implementation_selected_by_ha_version(monkeypatch, version, expected):
    monkeypatch.setattr(ha_const, "MAJOR_VERSION", version[0])
    monkeypatch.setattr(ha_const, "MINOR_VERSION", version[1])
    assert isinstance(device_registry_compatibility(SimpleNamespace()), expected)


def test_legacy_lookup_keeps_original_global_lookup():
    device = SimpleNamespace(config_entries={"owner"})
    registry = Mock(spec_set=["async_get_device"])
    registry.async_get_device.return_value = device
    compat = LegacyDeviceRegistryCompatibility(registry)
    identifier = (DOMAIN, "legacy")
    assert compat.get_device(identifier, "owner") is device
    registry.async_get_device.assert_called_once_with(identifiers={identifier})


@pytest.mark.parametrize("owners", [set(), {"owner"}, {"owner", "other"}])
def test_legacy_ownership_keeps_all_shared_owners(owners):
    compat = LegacyDeviceRegistryCompatibility(SimpleNamespace())
    assert compat.config_entry_ids(SimpleNamespace(config_entries=owners)) == owners


def test_modern_lookup_and_ownership_do_not_touch_legacy_apis():
    device = SimpleNamespace(config_entry_id="owner")
    registry = Mock(spec_set=["async_get_device_by_identifier"])
    registry.async_get_device_by_identifier.return_value = device
    compat = ModernDeviceRegistryCompatibility(registry)
    identifier = (DOMAIN, "legacy")
    assert compat.get_device(identifier, "owner") is device
    registry.async_get_device_by_identifier.assert_called_once_with(identifier, "owner")
    assert compat.config_entry_ids(device) == {"owner"}
    tree = ast.parse(inspect.getsource(ModernDeviceRegistryCompatibility))
    assert not any(
        isinstance(node, ast.Attribute) and node.attr in {"async_get_device", "config_entries"}
        for node in ast.walk(tree)
    )


def test_modern_missing_api_does_not_fall_back_to_legacy():
    registry = Mock(spec_set=["async_get_device"])
    with pytest.raises(AttributeError):
        ModernDeviceRegistryCompatibility(registry).get_device((DOMAIN, "legacy"), "owner")
    registry.async_get_device.assert_not_called()


def _entry(hass, remote_id):
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_REMOTE_ID: remote_id})
    entry.add_to_hass(hass)
    return entry


async def test_identity_migration_never_modifies_foreign_legacy_device(hass):
    entry = _entry(hass, 0x3B3F02)
    other = _entry(hass, 0x3B3F03)
    registry = dr.async_get(hass)
    foreign = registry.async_get_or_create(config_entry_id=other.entry_id, identifiers={(DOMAIN, "3b3f02")})
    await _async_migrate_registry_identity(hass, entry)
    assert registry.async_get(foreign.id).identifiers == {(DOMAIN, "3b3f02")}
    assert dr.async_entries_for_config_entry(registry, entry.entry_id) == []


async def test_modern_migration_is_not_blocked_by_another_entries_identifier(hass):
    registry = dr.async_get(hass)
    compat = device_registry_compatibility(registry)
    if not isinstance(compat, ModernDeviceRegistryCompatibility):
        pytest.skip("Legacy HA requires globally unique device identifiers")
    entry = _entry(hass, 0x3B3F02)
    other = _entry(hass, 0x3B3F03)
    target_identifier = (DOMAIN, f"fireplace:{entry.entry_id}")
    foreign = registry.async_get_or_create(config_entry_id=other.entry_id, identifiers={target_identifier})
    original = registry.async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, "3b3f02")})
    await _async_migrate_registry_identity(hass, entry)
    assert compat.get_device(target_identifier, entry.entry_id).id == original.id
    assert compat.get_device(target_identifier, other.entry_id).id == foreign.id
    assert registry.async_get(foreign.id).identifiers == {target_identifier}


async def test_legacy_shared_device_is_not_migrated(hass):
    registry = dr.async_get(hass)
    compat = device_registry_compatibility(registry)
    if not isinstance(compat, LegacyDeviceRegistryCompatibility):
        pytest.skip("Modern HA stores separate devices for each owning entry")
    entry = _entry(hass, 0x3B3F02)
    other = _entry(hass, 0x3B3F03)
    device = registry.async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, "3b3f02")})
    registry.async_update_device(device.id, add_config_entry_id=other.entry_id)
    await _async_migrate_registry_identity(hass, entry)
    current = registry.async_get(device.id)
    assert current.identifiers == {(DOMAIN, "3b3f02")}
    assert compat.config_entry_ids(current) == {entry.entry_id, other.entry_id}
