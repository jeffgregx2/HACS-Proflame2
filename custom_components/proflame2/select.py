"""Controller-scoped YardStick RF band selection."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import BACKEND_YARDSTICK
from .rf.band import YARDSTICK_BANDS
from .runtime import Proflame2RuntimeEntry, async_get_runtime_entries, async_runtime_signal
from .yardstick_controller import async_set_yardstick_band


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Add a band selector on YardStick only, never on the fireplace or LilyGO."""
    runtime = async_get_runtime_entries(hass)[entry.entry_id]
    if runtime.backend_type == BACKEND_YARDSTICK:
        async_add_entities([YardStickBandSelect(entry, runtime)])


class YardStickBandSelect(SelectEntity):
    """The sole band control, owned by the YardStick controller device."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "rf_band"
    _attr_icon = "mdi:radio-tower"
    _attr_options = list(YARDSTICK_BANDS)

    def __init__(self, entry: ConfigEntry, runtime: Proflame2RuntimeEntry) -> None:
        self._entry = entry
        self._runtime = runtime
        self._attr_unique_id = f"{entry.entry_id}_yardstick_rf_band"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={self._runtime.controller_device_identifier})

    @property
    def current_option(self) -> str | None:
        return self._runtime.rf_band

    @property
    def extra_state_attributes(self) -> dict:
        # current_option is the saved choice, not a claim of hardware readiness.
        return {
            "configuration_in_progress": self._runtime.radio_configuration_in_progress,
            "configuration_error": self._runtime.radio_configuration_error,
            "fireplace": self._runtime.title,
        }

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, async_runtime_signal(self._entry.entry_id), self._handle_update)
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        await async_set_yardstick_band(self.hass, self._entry, self._runtime, option)
