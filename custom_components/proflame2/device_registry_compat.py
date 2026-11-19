"""Version-selected device registry APIs for the supported HA releases."""

from __future__ import annotations

from abc import ABC, abstractmethod

from homeassistant import const as ha_const
from homeassistant.helpers import device_registry as dr


class DeviceRegistryCompatibility(ABC):
    """Keep lookup and ownership differences out of integration workflows."""

    def __init__(self, registry: dr.DeviceRegistry) -> None:
        self._registry = registry

    @abstractmethod
    def get_device(self, identifier: tuple[str, str], config_entry_id: str) -> dr.DeviceEntry | None:
        """Find an existing device; callers check ownership before mutation."""

    @abstractmethod
    def config_entry_ids(self, device: dr.DeviceEntry) -> frozenset[str]:
        """Return the device's owning entries in the applicable registry model."""


class LegacyDeviceRegistryCompatibility(DeviceRegistryCompatibility):
    """Use the existing registry APIs before HA 2026.8."""

    def get_device(self, identifier: tuple[str, str], config_entry_id: str) -> dr.DeviceEntry | None:
        # Legacy identifiers are globally scoped. Keep the old lookup and the
        # caller's existing single-owner guard; never take over shared devices.
        return self._registry.async_get_device(identifiers={identifier})

    def config_entry_ids(self, device: dr.DeviceEntry) -> frozenset[str]:
        return frozenset(device.config_entries)


class ModernDeviceRegistryCompatibility(DeviceRegistryCompatibility):
    """Use scoped lookups and single-entry ownership from HA 2026.8 onward."""

    def get_device(self, identifier: tuple[str, str], config_entry_id: str) -> dr.DeviceEntry | None:
        return self._registry.async_get_device_by_identifier(identifier, config_entry_id)

    def config_entry_ids(self, device: dr.DeviceEntry) -> frozenset[str]:
        return frozenset({device.config_entry_id})


def device_registry_compatibility(registry: dr.DeviceRegistry) -> DeviceRegistryCompatibility:
    """Select by HA version, without silently falling back on modern HA."""
    if (ha_const.MAJOR_VERSION, ha_const.MINOR_VERSION) >= (2026, 8):
        return ModernDeviceRegistryCompatibility(registry)
    return LegacyDeviceRegistryCompatibility(registry)
