"""Diagnostics helpers for Proflame 2."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .runtime import async_get_runtime_entries, serialize_runtime_entry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict:
    """Return minimal diagnostics for future troubleshooting."""

    return {
        "entry_id": entry.entry_id,
        "domain": entry.domain,
        "title": entry.title,
        "data": dict(entry.data),
        "options": dict(entry.options),
        "runtime": (
            serialize_runtime_entry(async_get_runtime_entries(hass)[entry.entry_id])
            if entry.entry_id in async_get_runtime_entries(hass)
            else None
        ),
    }
