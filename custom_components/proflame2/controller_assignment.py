"""Identify exclusive fireplace ownership of a physical RF controller."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from .const import BACKEND_FAKE, CONF_BACKEND_TYPE, CONF_ESPHOME_ENTRY_ID, DOMAIN
from .rf.registry import get_backend_definition, normalize_controller_id

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


def controller_assignment_key(data: Mapping[str, Any], options: Mapping[str, Any]) -> tuple[str, str] | None:
    """Match runtime controller selection, including legacy option overrides.

    YardStick currently opens the single default USB device; there is no
    per-profile USB selector. Simulated backends do not own physical hardware.
    """

    try:
        backend = normalize_controller_id(data.get(CONF_BACKEND_TYPE, ""))
        definition = get_backend_definition(backend)
    except (ValueError, KeyError):
        return None
    if backend == BACKEND_FAKE:
        return None
    if definition.requires_esphome_entry:
        linked = options.get(CONF_ESPHOME_ENTRY_ID) or data.get(CONF_ESPHOME_ENTRY_ID)
        return (backend, linked) if isinstance(linked, str) and linked else None
    return backend, "default"


def controller_assignments(
    hass: HomeAssistant, data: Mapping[str, Any], options: Mapping[str, Any]
) -> list[ConfigEntry]:
    """Return matching saved entries in registration order, even if disabled."""

    key = controller_assignment_key(data, options)
    if key is None:
        return []
    return [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if controller_assignment_key(entry.data, entry.options) == key
    ]
