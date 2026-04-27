"""Switch control entities for Proflame2 fireplaces."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN, MANUFACTURER, CONF_AUX, CONF_CPI, CONF_FRONT, CONF_POWER
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
    """Set up switch controls for one fireplace."""

    runtime_entry = async_get_runtime_entries(hass)[entry.entry_id]
    entities: list[SwitchEntity] = [Proflame2SwitchEntity(runtime_entry, CONF_POWER, "Power", "mdi:power")]
    if runtime_entry.features.front:
        entities.append(
            Proflame2SwitchEntity(runtime_entry, CONF_FRONT, "Front Burner", "mdi:fire")
        )
    if runtime_entry.features.aux:
        entities.append(Proflame2SwitchEntity(runtime_entry, CONF_AUX, "Aux", "mdi:toggle-switch"))
    if runtime_entry.features.cpi:
        entities.append(Proflame2SwitchEntity(runtime_entry, CONF_CPI, "CPI", "mdi:fire-circle"))
    async_add_entities(entities)


class Proflame2SwitchEntity(SwitchEntity):
    """One user-facing staged switch control."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(
        self,
        runtime_entry: Proflame2RuntimeEntry,
        key: str,
        name: str,
        icon: str,
    ) -> None:
        self._runtime_entry = runtime_entry
        self._key = key
        self._attr_name = name
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
    def is_on(self) -> bool:
        desired_state = self._runtime_entry.desired_state
        if desired_state is not None:
            return self._extract_value(desired_state)
        current_state = runtime_current_state(self._runtime_entry)
        if current_state is None:
            return False
        return self._extract_value(current_state)

    async def async_turn_on(self, **kwargs) -> None:
        await async_stage_control_change(self.hass, self._runtime_entry, {self._key: True})

    async def async_turn_off(self, **kwargs) -> None:
        await async_stage_control_change(self.hass, self._runtime_entry, {self._key: False})

    def _extract_value(self, state) -> bool:
        if self._key == CONF_POWER:
            return state.power
        if self._key == CONF_FRONT:
            return state.front
        if self._key == CONF_AUX:
            return state.aux
        return state.cpi
