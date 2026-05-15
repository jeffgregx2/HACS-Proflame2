"""Number control entities for Proflame2 fireplaces."""

from __future__ import annotations

from homeassistant.exceptions import HomeAssistantError
from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN, MANUFACTURER, CONF_FAN, CONF_FLAME, CONF_LIGHT
from .profile import remote_id_as_hex
from .runtime import (
    Proflame2RuntimeEntry,
    async_get_runtime_entries,
    async_runtime_signal,
    runtime_current_state,
)
from .services import async_stage_control_change


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up number controls for one fireplace."""

    runtime_entry = async_get_runtime_entries(hass)[entry.entry_id]
    entities: list[NumberEntity] = [
        Proflame2NumberEntity(runtime_entry, CONF_FLAME, "Flame", 1, 6, "mdi:fire"),
    ]
    if runtime_entry.features.fan:
        entities.append(
            Proflame2NumberEntity(runtime_entry, CONF_FAN, "Fan", 0, 6, "mdi:fan")
        )
    if runtime_entry.features.light:
        entities.append(
            Proflame2NumberEntity(runtime_entry, CONF_LIGHT, "Light", 0, 6, "mdi:lightbulb")
        )
    async_add_entities(entities)


class Proflame2NumberEntity(NumberEntity):
    """One user-facing staged numeric control."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_mode = "slider"

    def __init__(
        self,
        runtime_entry: Proflame2RuntimeEntry,
        key: str,
        name: str,
        minimum: int,
        maximum: int,
        icon: str,
    ) -> None:
        self._runtime_entry = runtime_entry
        self._key = key
        self._attr_name = name
        self._attr_native_min_value = minimum
        self._attr_native_max_value = maximum
        self._attr_native_step = 1
        self._attr_icon = icon
        self._attr_unique_id = (
            f"{remote_id_as_hex(runtime_entry.remote_profile.serial_id)}_{key}_control"
        )

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, remote_id_as_hex(self._runtime_entry.remote_profile.serial_id))},
            manufacturer=MANUFACTURER,
            name=self._runtime_entry.title,
            model=f"Backend: {self._runtime_entry.backend_type}",
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                async_runtime_signal(self._runtime_entry.config_entry_id),
                self._handle_runtime_updated,
            )
        )

    @callback
    def _handle_runtime_updated(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> int | None:
        desired_state = self._runtime_entry.desired_state
        state = desired_state or runtime_current_state(self._runtime_entry)
        if state is None:
            return 1 if self._key == CONF_FLAME else 0
        value = self._extract_value(state)
        if self._key == CONF_FLAME and value == 0:
            return 1
        return int(value)

    async def async_set_native_value(self, value: float) -> None:
        if not float(value).is_integer():
            raise HomeAssistantError(
                f"{self._attr_name} must be set to a whole-number level."
            )
        await async_stage_control_change(
            self.hass,
            self._runtime_entry,
            {self._key: int(value)},
        )

    def _extract_value(self, state) -> int:
        if self._key == CONF_FLAME:
            return state.flame
        if self._key == CONF_FAN:
            return state.fan
        return state.light
