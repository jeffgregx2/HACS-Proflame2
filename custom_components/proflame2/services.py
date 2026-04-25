"""Service registration and execution for Proflame2."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.const import ATTR_AREA_ID, ATTR_DEVICE_ID, ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError

from .protocol.encoder import encode_packet
from .rf.waveform import build_transmission_plan

from .control import StateValidationError, build_requested_state
from .const import (
    BACKEND_YARDSTICK,
    CONF_AUX,
    CONF_CONFIG_ENTRY_ID,
    CONF_CPI,
    CONF_FAN,
    CONF_FLAME,
    CONF_FRONT,
    CONF_LIGHT,
    CONF_NAME,
    CONF_PROFILE_ID,
    CONF_POWER,
    DATA_SERVICES_REGISTERED,
    DOMAIN,
    SERVICE_APPLY_PROFILE,
    SERVICE_SET_STATE,
)
from .runtime import Proflame2RuntimeEntry, async_get_runtime_entries
from .runtime import async_notify_runtime_entry_updated

SERVICE_SET_STATE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_POWER): bool,
        vol.Optional(CONF_CONFIG_ENTRY_ID): str,
        vol.Optional(ATTR_DEVICE_ID): vol.Any(str, [str]),
        vol.Optional(ATTR_ENTITY_ID): vol.Any(str, [str]),
        vol.Optional(ATTR_AREA_ID): vol.Any(str, [str]),
        vol.Optional(CONF_FLAME): vol.Coerce(int),
        vol.Optional(CONF_FAN): vol.Coerce(int),
        vol.Optional(CONF_LIGHT): vol.Coerce(int),
        vol.Optional(CONF_FRONT): bool,
        vol.Optional(CONF_AUX): bool,
        vol.Optional(CONF_CPI): bool,
    }
)

SERVICE_APPLY_PROFILE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PROFILE_ID): str,
        vol.Optional(CONF_CONFIG_ENTRY_ID): str,
        vol.Optional(ATTR_DEVICE_ID): vol.Any(str, [str]),
        vol.Optional(ATTR_ENTITY_ID): vol.Any(str, [str]),
        vol.Optional(ATTR_AREA_ID): vol.Any(str, [str]),
    }
)


async def async_register_services(hass: HomeAssistant) -> None:
    """Register domain services once."""

    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(DATA_SERVICES_REGISTERED):
        return

    async def handle_set_state(call: ServiceCall) -> None:
        runtime_entry = _resolve_runtime_entry(hass, call)
        await async_execute_set_state(
            hass,
            runtime_entry,
            call.data,
            source="homeassistant_service",
        )

    async def handle_apply_profile(call: ServiceCall) -> None:
        runtime_entry = _resolve_runtime_entry(hass, call)
        profile_id = str(call.data[CONF_PROFILE_ID]).strip().lower()
        profile = (runtime_entry.saved_profiles or {}).get(profile_id)
        if profile is None:
            runtime_entry.last_error = (
                f"Unknown saved profile '{profile_id}' for this fireplace."
            )
            async_notify_runtime_entry_updated(hass, runtime_entry.config_entry_id)
            raise HomeAssistantError(runtime_entry.last_error)

        await async_execute_set_state(
            hass,
            runtime_entry,
            profile,
            source="saved_profile",
            applied_profile_id=profile[CONF_PROFILE_ID],
            applied_profile_name=profile[CONF_NAME],
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_STATE,
        handle_set_state,
        schema=SERVICE_SET_STATE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_APPLY_PROFILE,
        handle_apply_profile,
        schema=SERVICE_APPLY_PROFILE_SCHEMA,
    )
    domain_data[DATA_SERVICES_REGISTERED] = True


async def async_unregister_services(hass: HomeAssistant) -> None:
    """Unregister domain services when no entries remain."""

    domain_data = hass.data.setdefault(DOMAIN, {})
    if not domain_data.get(DATA_SERVICES_REGISTERED):
        return
    hass.services.async_remove(DOMAIN, SERVICE_SET_STATE)
    hass.services.async_remove(DOMAIN, SERVICE_APPLY_PROFILE)
    domain_data[DATA_SERVICES_REGISTERED] = False


def _resolve_runtime_entry(hass: HomeAssistant, call: ServiceCall) -> Proflame2RuntimeEntry:
    """Resolve the target fireplace runtime for a service call."""

    runtime_entries = async_get_runtime_entries(hass)
    if not runtime_entries:
        raise HomeAssistantError("No Proflame2 fireplaces are configured.")

    device_ids = _coerce_target_ids(call.data.get(ATTR_DEVICE_ID))
    entity_ids = _coerce_target_ids(call.data.get(ATTR_ENTITY_ID))
    area_ids = _coerce_target_ids(call.data.get(ATTR_AREA_ID))
    if entity_ids or area_ids:
        raise HomeAssistantError(
            "Proflame2 services currently support device targets or config_entry_id only."
        )

    data_config_entry_id = call.data.get(CONF_CONFIG_ENTRY_ID)

    candidates: list[Proflame2RuntimeEntry] = []
    if data_config_entry_id is not None:
        runtime_entry = runtime_entries.get(data_config_entry_id)
        if runtime_entry is None:
            raise HomeAssistantError(
                f"Unknown Proflame2 config_entry_id: {data_config_entry_id}"
            )
        candidates.append(runtime_entry)

    if device_ids:
        if len(device_ids) != 1:
            raise HomeAssistantError("Target exactly one Proflame2 device per service call.")
        targeted = [entry for entry in runtime_entries.values() if entry.device_id == device_ids[0]]
        if not targeted:
            raise HomeAssistantError("No Proflame2 fireplace matches the targeted device.")
        if candidates and targeted[0].config_entry_id != candidates[0].config_entry_id:
            raise HomeAssistantError("config_entry_id and device target refer to different fireplaces.")
        candidates = targeted

    if not candidates:
        if len(runtime_entries) == 1:
            return next(iter(runtime_entries.values()))
        raise HomeAssistantError(
            "Multiple Proflame2 fireplaces are configured; specify config_entry_id or target a device."
        )

    return candidates[0]


def _coerce_target_ids(raw_value: Any) -> list[str]:
    """Normalize a Home Assistant service target field into a list of ids."""

    if raw_value is None:
        return []
    if isinstance(raw_value, str):
        return [raw_value]
    if isinstance(raw_value, list):
        return [str(item) for item in raw_value]
    return [str(raw_value)]


async def async_execute_set_state(
    hass: HomeAssistant,
    runtime_entry: Proflame2RuntimeEntry,
    data: dict[str, Any],
    *,
    source: str,
    applied_profile_id: str | None = None,
    applied_profile_name: str | None = None,
) -> None:
    """Execute the shared atomic state-application path for all services."""

    try:
        requested_state, ignored_warnings = build_requested_state(runtime_entry.features, data)
    except StateValidationError as exc:
        runtime_entry.last_error = str(exc)
        async_notify_runtime_entry_updated(hass, runtime_entry.config_entry_id)
        raise HomeAssistantError(str(exc)) from exc

    packet = encode_packet(
        requested_state,
        runtime_entry.remote_profile,
        source=source,
        warnings=ignored_warnings,
    )
    packet.transmission_plan = build_transmission_plan(packet.frame)

    runtime_entry.last_packet = packet
    runtime_entry.last_send_result = None
    runtime_entry.last_error = None
    runtime_entry.sending_in_progress = True
    async_notify_runtime_entry_updated(hass, runtime_entry.config_entry_id)

    if runtime_entry.backend_type == BACKEND_YARDSTICK or runtime_entry.backend is None:
        runtime_entry.sending_in_progress = False
        runtime_entry.last_error = "YARD Stick One transmit is not implemented yet."
        async_notify_runtime_entry_updated(hass, runtime_entry.config_entry_id)
        raise HomeAssistantError(runtime_entry.last_error)

    try:
        send_result = await runtime_entry.backend.send(packet)
    except NotImplementedError as exc:
        runtime_entry.sending_in_progress = False
        runtime_entry.last_error = str(exc)
        async_notify_runtime_entry_updated(hass, runtime_entry.config_entry_id)
        raise HomeAssistantError(str(exc)) from exc
    except RuntimeError as exc:
        runtime_entry.sending_in_progress = False
        runtime_entry.last_error = str(exc)
        async_notify_runtime_entry_updated(hass, runtime_entry.config_entry_id)
        raise HomeAssistantError(str(exc)) from exc

    runtime_entry.sending_in_progress = False
    runtime_entry.last_send_result = send_result
    runtime_entry.last_applied_profile_id = applied_profile_id
    runtime_entry.last_applied_profile_name = applied_profile_name
    async_notify_runtime_entry_updated(hass, runtime_entry.config_entry_id)
