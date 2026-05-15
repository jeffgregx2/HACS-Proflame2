"""Home Assistant integration bootstrap for Proflame 2."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.const import Platform
    from homeassistant.core import HomeAssistant

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

try:
    from homeassistant.helpers.device_registry import DeviceIdentifierCollisionError
except ImportError:  # pragma: no cover - older HA versions may not expose this name.
    DeviceIdentifierCollisionError = ValueError  # type: ignore[misc,assignment]

from .const import BACKEND_ESPHOME, CONF_REMOTE_ID, DOMAIN
from .identity import (
    fireplace_device_identifier,
    legacy_fireplace_device_identifier,
    primary_entity_unique_id,
    runtime_entity_unique_id,
)
from .profile import remote_id_as_hex
from .version import INTEGRATION_VERSION

__version__ = INTEGRATION_VERSION

PLATFORMS: list[Platform] = ["sensor", "switch", "number", "button"]
_LOGGER = logging.getLogger(__name__)
_DISPLAY_SYNC_RETRY_DELAYS_SECONDS: tuple[float, ...] = (5.0, 15.0, 30.0)


async def _async_migrate_registry_identity(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Migrate legacy serial-based device/entity identities when unambiguous."""

    remote_id = entry.data.get(CONF_REMOTE_ID)
    if not isinstance(remote_id, int):
        return

    old_remote_hex = remote_id_as_hex(remote_id)
    same_serial_entries = [
        other
        for other in hass.config_entries.async_entries(DOMAIN)
        if other.entry_id != entry.entry_id and other.data.get(CONF_REMOTE_ID) == remote_id
    ]
    entity_registry = er.async_get(hass)

    if same_serial_entries:
        for registry_entry in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
            if registry_entry.unique_id == old_remote_hex or registry_entry.unique_id.startswith(f"{old_remote_hex}_"):
                _LOGGER.warning(
                    "Proflame2 identity migration skipped for config_entry_id=%s because remote serial %s is shared by multiple entries; old unique_id=%s remains untouched",
                    entry.entry_id,
                    old_remote_hex,
                    registry_entry.unique_id,
                )
        return

    for registry_entry in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        old_unique_id = registry_entry.unique_id
        if old_unique_id == old_remote_hex:
            new_unique_id = primary_entity_unique_id(entry.entry_id)
        elif old_unique_id.startswith(f"{old_remote_hex}_"):
            suffix = old_unique_id[len(old_remote_hex) + 1 :]
            new_unique_id = runtime_entity_unique_id(entry.entry_id, suffix)
        else:
            continue
        if new_unique_id == old_unique_id:
            continue
        existing_entity_id = entity_registry.async_get_entity_id(
            registry_entry.domain, registry_entry.platform, new_unique_id
        )
        if existing_entity_id is not None and existing_entity_id != registry_entry.entity_id:
            _LOGGER.warning(
                "Proflame2 identity migration skipped entity unique_id change for config_entry_id=%s old_unique_id=%s new_unique_id=%s because the target unique_id already exists as %s",
                entry.entry_id,
                old_unique_id,
                new_unique_id,
                existing_entity_id,
            )
            continue
        entity_registry.async_update_entity(
            registry_entry.entity_id,
            new_unique_id=new_unique_id,
        )

    device_registry = dr.async_get(hass)
    new_identifier = fireplace_device_identifier(entry.entry_id)
    existing_target_device = device_registry.async_get_device(identifiers={new_identifier})
    for old_identifier in ((DOMAIN, old_remote_hex), legacy_fireplace_device_identifier(entry.entry_id)):
        old_device = device_registry.async_get_device(identifiers={old_identifier})
        if old_device is None:
            continue
        if existing_target_device is not None:
            if old_device.id == existing_target_device.id:
                break
            _LOGGER.warning(
                "Proflame2 identity migration skipped device identifier change for config_entry_id=%s because target identifier %s already belongs to device %s",
                entry.entry_id,
                new_identifier,
                existing_target_device.id,
            )
            continue
        if set(old_device.config_entries) != {entry.entry_id}:
            _LOGGER.warning(
                "Proflame2 identity migration skipped device identifier change for config_entry_id=%s because legacy device %s is attached to multiple config entries: %s",
                entry.entry_id,
                old_device.id,
                sorted(old_device.config_entries),
            )
            continue
        try:
            device_registry.async_update_device(
                old_device.id,
                new_identifiers={new_identifier},
            )
        except DeviceIdentifierCollisionError:
            _LOGGER.warning(
                "Proflame2 identity migration skipped device identifier change for config_entry_id=%s because target identifier %s collided with an existing device",
                entry.entry_id,
                new_identifier,
            )
            continue
        break


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Proflame2 integration domain."""

    from .runtime import async_register_shutdown_listener

    hass.data.setdefault(DOMAIN, {})
    async_register_shutdown_listener(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Proflame 2 from a config entry."""

    from .runtime import (
        async_refresh_runtime_device_link,
        async_retry_runtime_device_link,
        async_setup_runtime_entry,
    )
    from .services import (
        async_register_services,
        async_start_active_listener,
        async_start_display_sync_listener,
        async_sync_runtime_display_state,
    )

    await _async_migrate_registry_identity(hass, entry)
    runtime_entry = await async_setup_runtime_entry(hass, entry)
    device_linked = async_refresh_runtime_device_link(hass, runtime_entry, entry)
    if (
        runtime_entry.backend_type == BACKEND_ESPHOME
        and runtime_entry.controller_device_id is None
        and not device_linked
    ):

        async def _async_retry_device_link() -> None:
            linked = await async_retry_runtime_device_link(
                hass,
                runtime_entry,
                entry,
                delays=_DISPLAY_SYNC_RETRY_DELAYS_SECONDS,
            )
            if not linked:
                _LOGGER.debug(
                    "Proflame2 controller link still unresolved after retries config_entry_id=%s",
                    entry.entry_id,
                )

        hass.async_create_task(_async_retry_device_link())
    await async_register_services(hass)
    try:
        await async_sync_runtime_display_state(hass, runtime_entry, action_label="Startup sync")
    except RuntimeError as exc:
        message = str(exc)
        if "Linked ESPHome entry is not loaded or has no runtime_data" in message:
            _LOGGER.warning(
                "Proflame2 startup display sync deferred for config_entry_id=%s because linked ESPHome entry is not ready yet: %s",
                entry.entry_id,
                message,
            )

            async def _async_retry_display_sync() -> None:
                for delay in _DISPLAY_SYNC_RETRY_DELAYS_SECONDS:
                    await asyncio.sleep(delay)
                    if runtime_entry.shutting_down:
                        return
                    try:
                        async_refresh_runtime_device_link(hass, runtime_entry, entry)
                        await async_sync_runtime_display_state(hass, runtime_entry, action_label="Startup sync")
                    except RuntimeError as retry_exc:
                        retry_message = str(retry_exc)
                        if "Linked ESPHome entry is not loaded or has no runtime_data" in retry_message:
                            _LOGGER.debug(
                                "Proflame2 startup display sync still waiting for linked ESPHome entry config_entry_id=%s delay=%ss reason=%s",
                                entry.entry_id,
                                delay,
                                retry_message,
                            )
                            continue
                        _LOGGER.warning(
                            "Proflame2 startup display sync skipped for config_entry_id=%s after retry failure: %s",
                            entry.entry_id,
                            retry_message,
                        )
                        return
                    except Exception:
                        _LOGGER.exception(
                            "Proflame2 startup display sync failed for config_entry_id=%s during retry",
                            entry.entry_id,
                        )
                        return
                    else:
                        _LOGGER.debug(
                            "Proflame2 startup display sync completed for config_entry_id=%s after retry delay=%ss",
                            entry.entry_id,
                            delay,
                        )
                        return

            hass.async_create_task(_async_retry_display_sync())
        else:
            _LOGGER.warning(
                "Proflame2 startup display sync skipped for config_entry_id=%s: %s",
                entry.entry_id,
                message,
            )
    except Exception:
        _LOGGER.exception(
            "Proflame2 startup display sync failed unexpectedly for config_entry_id=%s",
            entry.entry_id,
        )
    await async_start_display_sync_listener(hass, runtime_entry)
    await async_start_active_listener(hass, runtime_entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload Proflame 2 config entry."""

    from .runtime import async_get_runtime_entries, async_unload_runtime_entry
    from .services import async_unregister_services

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await async_unload_runtime_entry(hass, entry)
        if not async_get_runtime_entries(hass):
            await async_unregister_services(hass)
    return unload_ok
