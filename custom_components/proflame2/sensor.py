"""Read-only sensor surface for Proflame2 runtime status and diagnostics."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any, Callable

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN, MANUFACTURER
from .profile import remote_id_as_hex
from .runtime import (
    Proflame2RuntimeEntry,
    async_get_runtime_entries,
    async_runtime_signal,
)


@dataclass(frozen=True, kw_only=True)
class Proflame2SensorDefinition(SensorEntityDescription):
    """Static definition for one Proflame2 sensor entity."""

    value_fn: Callable[[Proflame2RuntimeEntry], Any]
    enabled_default: bool = True


USER_SENSORS: tuple[Proflame2SensorDefinition, ...] = (
    Proflame2SensorDefinition(
        key="status",
        name="Status",
        value_fn=lambda runtime: _status_value(runtime),
    ),
    Proflame2SensorDefinition(
        key="last_state",
        name="Last State",
        value_fn=lambda runtime: _last_state_summary(runtime),
    ),
    Proflame2SensorDefinition(
        key="last_issue",
        name="Last Issue",
        value_fn=lambda runtime: _last_issue_summary(runtime),
    ),
)

DIAGNOSTIC_SENSORS: tuple[Proflame2SensorDefinition, ...] = (
    Proflame2SensorDefinition(
        key="remote_id",
        name="Remote ID",
        value_fn=lambda runtime: remote_id_as_hex(runtime.remote_profile.serial_id),
        entity_category=EntityCategory.DIAGNOSTIC,
        enabled_default=False,
    ),
    Proflame2SensorDefinition(
        key="c1",
        name="C1",
        value_fn=lambda runtime: runtime.remote_profile.ecc.c1,
        entity_category=EntityCategory.DIAGNOSTIC,
        enabled_default=False,
    ),
    Proflame2SensorDefinition(
        key="d1",
        name="D1",
        value_fn=lambda runtime: runtime.remote_profile.ecc.d1,
        entity_category=EntityCategory.DIAGNOSTIC,
        enabled_default=False,
    ),
    Proflame2SensorDefinition(
        key="c2",
        name="C2",
        value_fn=lambda runtime: runtime.remote_profile.ecc.c2,
        entity_category=EntityCategory.DIAGNOSTIC,
        enabled_default=False,
    ),
    Proflame2SensorDefinition(
        key="d2",
        name="D2",
        value_fn=lambda runtime: runtime.remote_profile.ecc.d2,
        entity_category=EntityCategory.DIAGNOSTIC,
        enabled_default=False,
    ),
    Proflame2SensorDefinition(
        key="last_cmd1",
        name="Last Cmd1",
        value_fn=lambda runtime: _hex_byte(runtime.last_packet.frame.cmd1) if runtime.last_packet else None,
        entity_category=EntityCategory.DIAGNOSTIC,
        enabled_default=False,
    ),
    Proflame2SensorDefinition(
        key="last_err1",
        name="Last Err1",
        value_fn=lambda runtime: _hex_byte(runtime.last_packet.frame.err1) if runtime.last_packet else None,
        entity_category=EntityCategory.DIAGNOSTIC,
        enabled_default=False,
    ),
    Proflame2SensorDefinition(
        key="last_cmd2",
        name="Last Cmd2",
        value_fn=lambda runtime: _hex_byte(runtime.last_packet.frame.cmd2) if runtime.last_packet else None,
        entity_category=EntityCategory.DIAGNOSTIC,
        enabled_default=False,
    ),
    Proflame2SensorDefinition(
        key="last_err2",
        name="Last Err2",
        value_fn=lambda runtime: _hex_byte(runtime.last_packet.frame.err2) if runtime.last_packet else None,
        entity_category=EntityCategory.DIAGNOSTIC,
        enabled_default=False,
    ),
    Proflame2SensorDefinition(
        key="last_requested_state_json",
        name="Last Requested State JSON",
        value_fn=lambda runtime: _json_or_none(runtime.last_packet.state) if runtime.last_packet else None,
        entity_category=EntityCategory.DIAGNOSTIC,
        enabled_default=False,
    ),
    Proflame2SensorDefinition(
        key="last_packet_state_json",
        name="Last Packet State JSON",
        value_fn=lambda runtime: _json_or_none(runtime.last_packet.state) if runtime.last_packet else None,
        entity_category=EntityCategory.DIAGNOSTIC,
        enabled_default=False,
    ),
    Proflame2SensorDefinition(
        key="last_transmission_plan",
        name="Last Transmission Plan",
        value_fn=lambda runtime: _transmission_plan_summary(runtime),
        entity_category=EntityCategory.DIAGNOSTIC,
        enabled_default=False,
    ),
    Proflame2SensorDefinition(
        key="last_backend",
        name="Last Backend",
        value_fn=lambda runtime: _backend_name(runtime),
        entity_category=EntityCategory.DIAGNOSTIC,
        enabled_default=False,
    ),
    Proflame2SensorDefinition(
        key="last_warnings",
        name="Last Warnings",
        value_fn=lambda runtime: _warnings_summary(runtime),
        entity_category=EntityCategory.DIAGNOSTIC,
        enabled_default=False,
    ),
    Proflame2SensorDefinition(
        key="last_echo",
        name="Last Echo",
        value_fn=lambda runtime: _echo_summary(runtime),
        entity_category=EntityCategory.DIAGNOSTIC,
        enabled_default=False,
    ),
    Proflame2SensorDefinition(
        key="last_raw_packet",
        name="Last Raw Packet",
        value_fn=lambda runtime: _raw_summary(runtime),
        entity_category=EntityCategory.DIAGNOSTIC,
        enabled_default=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Proflame2 read-only sensor entities for one fireplace."""

    runtime_entry = async_get_runtime_entries(hass)[entry.entry_id]
    async_add_entities(
        Proflame2RuntimeSensor(runtime_entry, definition)
        for definition in (*USER_SENSORS, *DIAGNOSTIC_SENSORS)
    )


class Proflame2RuntimeSensor(SensorEntity):
    """Sensor that exposes one runtime-derived Proflame2 summary value."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(
        self,
        runtime_entry: Proflame2RuntimeEntry,
        definition: Proflame2SensorDefinition,
    ) -> None:
        self._runtime_entry = runtime_entry
        self.entity_description = definition
        self._attr_name = definition.name
        self._attr_unique_id = (
            f"{remote_id_as_hex(runtime_entry.remote_profile.serial_id)}_{definition.key}"
        )
        self._attr_entity_category = definition.entity_category
        self._attr_entity_registry_enabled_default = definition.enabled_default

    @property
    def native_value(self) -> Any:
        """Return the current runtime-derived value."""

        return self.entity_description.value_fn(self._runtime_entry)

    @property
    def available(self) -> bool:
        """Entities remain available as long as the runtime entry exists."""

        return True

    @property
    def device_info(self) -> DeviceInfo:
        """Bind the entity to the fireplace device created during setup."""

        return DeviceInfo(
            identifiers={(DOMAIN, remote_id_as_hex(self._runtime_entry.remote_profile.serial_id))},
            manufacturer=MANUFACTURER,
            name=self._runtime_entry.title,
            model=f"Backend: {self._runtime_entry.backend_type}",
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to runtime update notifications."""

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                async_runtime_signal(self._runtime_entry.config_entry_id),
                self._handle_runtime_updated,
            )
        )

    @callback
    def _handle_runtime_updated(self) -> None:
        """Write updated state after runtime mutations."""

        self.async_write_ha_state()


def _status_value(runtime: Proflame2RuntimeEntry) -> str:
    if runtime.learning_in_progress:
        return "learning"
    if not _backend_available(runtime):
        return "backend_unavailable"
    if runtime.last_error:
        return "last_command_failed"
    if runtime.last_send_result:
        return "last_command_succeeded"
    return "ready"


def _last_state_summary(runtime: Proflame2RuntimeEntry) -> str:
    packet = runtime.last_packet
    if packet is None:
        return "No fireplace state known yet."

    state = packet.state
    if not state.power:
        summary = "Fireplace off."
        if runtime.last_applied_profile_name:
            return f"Profile {runtime.last_applied_profile_name}: {summary}"
        return summary

    parts = [f"On, flame {state.flame}", f"fan {state.fan}", f"light {state.light}"]
    if state.front:
        parts.append("front burner on")
    if state.aux:
        parts.append("aux on")
    if state.cpi:
        parts.append("CPI on")
    summary = ", ".join(parts) + "."
    if runtime.last_applied_profile_name:
        return f"Profile {runtime.last_applied_profile_name}: {summary}"
    return summary


def _last_issue_summary(runtime: Proflame2RuntimeEntry) -> str:
    if runtime.last_error:
        return _humanize_message(runtime.last_error)
    if runtime.last_send_result and runtime.last_send_result.errors:
        return _humanize_message(runtime.last_send_result.errors[0])
    if runtime.last_send_result and runtime.last_send_result.warnings:
        return _humanize_message(runtime.last_send_result.warnings[0])
    return "No recent errors."


def _backend_available(runtime: Proflame2RuntimeEntry) -> bool:
    backend = runtime.backend
    if backend is None:
        return False
    return bool(getattr(backend, "connected", True))


def _backend_name(runtime: Proflame2RuntimeEntry) -> str:
    if runtime.last_send_result is not None:
        return runtime.last_send_result.backend_name
    if runtime.backend is not None:
        return getattr(runtime.backend, "name", runtime.backend_type)
    return runtime.backend_type


def _warnings_summary(runtime: Proflame2RuntimeEntry) -> str | None:
    if runtime.last_send_result is None or not runtime.last_send_result.warnings:
        return None
    return " | ".join(runtime.last_send_result.warnings)


def _echo_summary(runtime: Proflame2RuntimeEntry) -> str:
    result = runtime.last_send_result
    if result is None:
        return "unknown"
    if result.echo_seen:
        if result.echo_delay_ms is not None:
            return f"observed ({result.echo_delay_ms} ms)"
        return "observed"
    return "not_observed"


def _raw_summary(runtime: Proflame2RuntimeEntry) -> str | None:
    if runtime.last_packet is None or runtime.last_packet.raw is None:
        return None
    if isinstance(runtime.last_packet.raw, bytes):
        return runtime.last_packet.raw.hex()
    return str(runtime.last_packet.raw)


def _transmission_plan_summary(runtime: Proflame2RuntimeEntry) -> str | None:
    packet = runtime.last_packet
    if packet is None or packet.transmission_plan is None:
        return None
    plan = packet.transmission_plan
    return (
        f"repeat_count={plan.repeat_count}, "
        f"backend_repeat_argument={plan.backend_repeat_argument}, "
        f"sync={plan.sync_strategy}, "
        f"payload={plan.air_payload.hex()}"
    )


def _humanize_message(message: str) -> str:
    if "YARD Stick One transmit is not implemented yet" in message:
        return "RF backend is unavailable."
    if "multiple remote IDs" in message:
        return "Learning failed because packets from more than one remote were observed."
    if message.startswith("Ignored ") and " disabled for this fireplace." in message:
        feature = message.split(" ", 1)[1].split(" ", 1)[0]
        return (
            f"{feature.capitalize()} was ignored because it is disabled for this fireplace."
        )
    normalized = message.strip()
    if not normalized:
        return "No recent errors."
    if normalized[-1] not in ".!?":
        normalized += "."
    return normalized[0].upper() + normalized[1:]


def _hex_byte(value: int) -> str:
    return f"0x{value:02X}"


def _json_or_none(value: Any) -> str | None:
    return json.dumps(asdict(value), sort_keys=True) if value is not None else None
