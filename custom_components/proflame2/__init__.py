"""Home Assistant integration bootstrap for Proflame 2."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.const import Platform
    from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .version import INTEGRATION_VERSION

__version__ = INTEGRATION_VERSION

PLATFORMS: list["Platform"] = ["sensor", "switch", "number"]


async def async_setup(hass: "HomeAssistant", config: dict) -> bool:
    """Set up the Proflame2 integration domain."""

    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: "HomeAssistant", entry: "ConfigEntry") -> bool:
    """Set up Proflame 2 from a config entry."""

    from .runtime import async_setup_runtime_entry
    from .services import async_register_services, async_start_active_listener

    runtime_entry = await async_setup_runtime_entry(hass, entry)
    await async_register_services(hass)
    await async_start_active_listener(hass, runtime_entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: "HomeAssistant", entry: "ConfigEntry") -> bool:
    """Unload Proflame 2 config entry."""

    from .runtime import async_get_runtime_entries, async_unload_runtime_entry
    from .services import async_unregister_services

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await async_unload_runtime_entry(hass, entry)
        if not async_get_runtime_entries(hass):
            await async_unregister_services(hass)
    return unload_ok
