"""Switch control entities for Proflame2 fireplaces."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import CONF_AUX, CONF_CPI, CONF_FRONT, CONF_POWER, MANUFACTURER
from .identity import fireplace_device_identifiers, runtime_entity_unique_id
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
    entities.append(Proflame2PCMLoggingSwitch(runtime_entry))
    if runtime_entry.features.front:
        entities.append(Proflame2SwitchEntity(runtime_entry, CONF_FRONT, "Front Burner", "mdi:fire"))
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
        self._attr_unique_id = runtime_entity_unique_id(runtime_entry.config_entry_id, f"{key}_control")

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers=fireplace_device_identifiers(self._runtime_entry.fireplace_id),
            manufacturer=MANUFACTURER,
            name=self._runtime_entry.title,
            model=f"Backend: {self._runtime_entry.backend_type}",
            via_device=self._runtime_entry.controller_device_identifier,
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


class Proflame2PCMLoggingSwitch(Proflame2SwitchEntity, RestoreEntity):
    """Payload-only diagnostic logging; never stage a fireplace control change."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, runtime_entry: Proflame2RuntimeEntry) -> None:
        super().__init__(runtime_entry, "pcm_logging", "PCM Payload Logging", "mdi:code-braces")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        state = await self.async_get_last_state()
        self._set_enabled(state is not None and state.state == "on")

    @property
    def is_on(self) -> bool:
        backend = self._runtime_entry.backend
        return backend is not None and backend.pcm_logging_enabled

    def _set_enabled(self, enabled: bool) -> None:
        backend = self._runtime_entry.backend
        if backend is not None:
            backend.set_pcm_logging_enabled(enabled)
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs) -> None:
        self._set_enabled(True)

    async def async_turn_off(self, **kwargs) -> None:
        self._set_enabled(False)
