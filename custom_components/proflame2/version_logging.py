"""Report build identity once per debug session and firmware availability change."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_LOGGING_CHANGED, EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .const import BACKEND_ESPHOME, CONF_ESPHOME_ENTRY_ID
from .version import integration_version

if TYPE_CHECKING:
    from .runtime import Proflame2RuntimeEntry

_LOGGER = logging.getLogger(__name__)


@callback
def async_register_version_logging(hass: HomeAssistant, entry: ConfigEntry, runtime: Proflame2RuntimeEntry) -> None:
    """Own read-only version observation for this entry until it unloads."""
    last_signature: tuple[str, str | None] | None = None
    linked_entry_id = entry.options.get(CONF_ESPHOME_ENTRY_ID) or entry.data.get(CONF_ESPHOME_ENTRY_ID)

    @callback
    def report(_event: Event | None = None) -> None:
        nonlocal last_signature
        if not _LOGGER.isEnabledFor(logging.DEBUG):
            last_signature = None
            return

        version = integration_version()
        firmware = None
        if runtime.backend_type == BACKEND_ESPHOME:
            transport = getattr(runtime.backend, "transport", None)
            read_version = getattr(transport, "proflame2_firmware_version", None)
            if callable(read_version):
                firmware = read_version()
        signature = (version, firmware)
        if signature == last_signature:
            return
        last_signature = signature
        if runtime.backend_type == BACKEND_ESPHOME:
            _LOGGER.debug(
                "Proflame2 version information: config_entry_id=%s integration=%s controller=%s proflame2_firmware=%s",
                entry.entry_id,
                version,
                runtime.backend_type,
                firmware or "unavailable",
            )
        else:
            _LOGGER.debug(
                "Proflame2 version information: config_entry_id=%s integration=%s controller=%s",
                entry.entry_id,
                version,
                runtime.backend_type,
            )

    @callback
    def state_changed(event: Event) -> None:
        if not _LOGGER.isEnabledFor(logging.DEBUG):
            return
        entity_id = event.data.get("entity_id")
        registered = er.async_get(hass).async_get(entity_id) if entity_id else None
        if registered is not None and registered.config_entry_id == linked_entry_id:
            report()

    entry.async_on_unload(hass.bus.async_listen(EVENT_LOGGING_CHANGED, report))
    if runtime.backend_type == BACKEND_ESPHOME and linked_entry_id:
        entry.async_on_unload(hass.bus.async_listen(EVENT_STATE_CHANGED, state_changed))
    report()
