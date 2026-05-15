"""Per-fireplace saved-profile activation buttons for Proflame2."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_NAME, CONF_PROFILE_ID, DOMAIN, MANUFACTURER
from .profile import remote_id_as_hex
from .runtime import Proflame2RuntimeEntry, async_get_runtime_entries
from .services import async_execute_apply_profile


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one button per saved profile for this fireplace."""

    runtime_entry = async_get_runtime_entries(hass)[entry.entry_id]
    profiles = tuple((runtime_entry.saved_profiles or {}).values())
    async_add_entities(
        [
            Proflame2ProfileButtonEntity(
                runtime_entry=runtime_entry,
                profile_id=str(profile[CONF_PROFILE_ID]),
                profile_name=str(profile[CONF_NAME]),
            )
            for profile in profiles
        ]
    )


class Proflame2ProfileButtonEntity(ButtonEntity):
    """One saved-profile activation button scoped to a single fireplace."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_icon = "mdi:gesture-tap-button"

    def __init__(
        self,
        *,
        runtime_entry: Proflame2RuntimeEntry,
        profile_id: str,
        profile_name: str,
    ) -> None:
        self._runtime_entry = runtime_entry
        self._profile_id = profile_id
        self._attr_name = profile_name
        self._attr_unique_id = (
            f"{runtime_entry.config_entry_id}_{profile_id}_profile_button"
        )

    @property
    def available(self) -> bool:
        return True

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, remote_id_as_hex(self._runtime_entry.remote_profile.serial_id))},
            manufacturer=MANUFACTURER,
            name=self._runtime_entry.title,
            model=f"Backend: {self._runtime_entry.backend_type}",
        )

    async def async_press(self) -> None:
        await async_execute_apply_profile(
            self.hass,
            self._runtime_entry,
            self._profile_id,
            source="saved_profile",
        )
