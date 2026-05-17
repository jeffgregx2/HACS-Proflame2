"""Home Assistant profile-button tests for Proflame2."""

from __future__ import annotations

import pytest

homeassistant = pytest.importorskip("homeassistant")
pytest.importorskip("pytest_homeassistant_custom_component")

pytestmark = pytest.mark.ha

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.proflame2.const import (
    BACKEND_FAKE,
    CONF_AUX,
    CONF_BACKEND_TYPE,
    CONF_C1,
    CONF_C2,
    CONF_CPI,
    CONF_D1,
    CONF_D2,
    CONF_FAN,
    CONF_FLAME,
    CONF_FRONT,
    CONF_LIGHT,
    CONF_NAME,
    CONF_POWER,
    CONF_PROFILE_ID,
    CONF_PROFILES,
    CONF_REMOTE_ID,
    DATA_CONFIRMATION_WINDOW_SECONDS,
    DOMAIN,
)
from custom_components.proflame2.rf.fake import FakeRFBackend
from custom_components.proflame2.runtime import async_get_runtime_entries


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


def _add_entry(
    hass,
    *,
    title: str = "Living Room",
    profiles: dict | None = None,
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=title,
        data={
            CONF_NAME: title,
            CONF_BACKEND_TYPE: BACKEND_FAKE,
            CONF_REMOTE_ID: 0x3B3F02,
            CONF_C1: 5,
            CONF_D1: 7,
            CONF_C2: 1,
            CONF_D2: 8,
        },
        options={
            CONF_FAN: True,
            CONF_LIGHT: True,
            CONF_FRONT: False,
            CONF_AUX: False,
            CONF_CPI: False,
            CONF_PROFILES: profiles or {},
        },
    )
    entry.add_to_hass(hass)
    return entry


def _profile_entity_id(title: str, profile_name: str) -> str:
    return f"button.{slugify(title)}_{slugify(profile_name)}"


async def test_profile_buttons_are_created_per_fireplace_profile(hass) -> None:
    """Each per-fireplace saved profile should create one activation button."""

    entry = _add_entry(
        hass,
        profiles={
            "evening_relax": {
                CONF_PROFILE_ID: "evening_relax",
                CONF_NAME: "Evening Relax",
                CONF_POWER: True,
                CONF_FLAME: 2,
                CONF_FAN: 1,
                CONF_LIGHT: 1,
                CONF_FRONT: False,
                CONF_AUX: False,
                CONF_CPI: False,
            },
            "warmup": {
                CONF_PROFILE_ID: "warmup",
                CONF_NAME: "Warmup",
                CONF_POWER: True,
                CONF_FLAME: 6,
                CONF_FAN: 0,
                CONF_LIGHT: 0,
                CONF_FRONT: False,
                CONF_AUX: False,
                CONF_CPI: False,
            },
        },
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get(_profile_entity_id(entry.title, "Evening Relax")) is not None
    assert hass.states.get(_profile_entity_id(entry.title, "Warmup")) is not None


async def test_no_profile_buttons_when_no_profiles_exist(hass) -> None:
    """Fireplaces without saved profiles should not create profile buttons."""

    entry = _add_entry(hass, profiles={})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    assert not [
        item
        for item in entity_registry.entities.values()
        if item.config_entry_id == entry.entry_id and item.domain == "button"
    ]


async def test_profile_button_unique_id_is_stable_across_rename(hass) -> None:
    """Renaming a saved profile should keep the same unique_id and update the name."""

    entry = _add_entry(
        hass,
        profiles={
            "movie_night": {
                CONF_PROFILE_ID: "movie_night",
                CONF_NAME: "Movie Night",
                CONF_POWER: True,
                CONF_FLAME: 1,
                CONF_FAN: 0,
                CONF_LIGHT: 0,
                CONF_FRONT: False,
                CONF_AUX: False,
                CONF_CPI: False,
            }
        },
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    entity_id = _profile_entity_id(entry.title, "Movie Night")
    registry_entry = entity_registry.async_get(entity_id)
    assert registry_entry is not None
    assert registry_entry.unique_id == f"{entry.entry_id}_movie_night_profile_button"

    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            CONF_PROFILES: {
                "movie_night": {
                    CONF_PROFILE_ID: "movie_night",
                    CONF_NAME: "Evening Relax",
                    CONF_POWER: True,
                    CONF_FLAME: 2,
                    CONF_FAN: 1,
                    CONF_LIGHT: 1,
                    CONF_FRONT: False,
                    CONF_AUX: False,
                    CONF_CPI: False,
                }
            },
        },
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    registry_entry = entity_registry.async_get(entity_id)
    assert registry_entry is not None
    assert registry_entry.unique_id == f"{entry.entry_id}_movie_night_profile_button"
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.attributes["friendly_name"] == "Living Room Evening Relax"


async def test_profile_button_add_and_delete_follow_entry_reload(hass) -> None:
    """Adding or deleting a profile should add/remove the matching button on reload."""

    entry = _add_entry(hass, profiles={})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_id = _profile_entity_id(entry.title, "Minimum Flame")
    assert hass.states.get(entity_id) is None

    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            CONF_PROFILES: {
                "minimum_flame": {
                    CONF_PROFILE_ID: "minimum_flame",
                    CONF_NAME: "Minimum Flame",
                    CONF_POWER: True,
                    CONF_FLAME: 1,
                    CONF_FAN: 0,
                    CONF_LIGHT: 0,
                    CONF_FRONT: False,
                    CONF_AUX: False,
                    CONF_CPI: False,
                }
            },
        },
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id) is not None

    hass.config_entries.async_update_entry(
        entry,
        options={**entry.options, CONF_PROFILES: {}},
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    deleted_state = hass.states.get(entity_id)
    assert deleted_state is None or deleted_state.state == "unavailable"


async def test_profile_button_press_calls_shared_internal_executor_without_debounce(hass, monkeypatch) -> None:
    """Button press should call the shared apply-profile executor directly."""

    entry = _add_entry(
        hass,
        profiles={
            "evening_relax": {
                CONF_PROFILE_ID: "evening_relax",
                CONF_NAME: "Evening Relax",
                CONF_POWER: True,
                CONF_FLAME: 2,
                CONF_FAN: 1,
                CONF_LIGHT: 1,
                CONF_FRONT: False,
                CONF_AUX: False,
                CONF_CPI: False,
            }
        },
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    runtime_entry = async_get_runtime_entries(hass)[entry.entry_id]

    calls: list[tuple[str, str, str]] = []

    async def fake_apply_profile(hass_arg, runtime_entry_arg, profile_id_arg, *, source):
        calls.append((runtime_entry_arg.config_entry_id, profile_id_arg, source))

    monkeypatch.setattr(
        "custom_components.proflame2.button.async_execute_apply_profile",
        fake_apply_profile,
    )

    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": _profile_entity_id(entry.title, "Evening Relax")},
        blocking=True,
    )

    assert calls == [(entry.entry_id, "evening_relax", "saved_profile")]
    assert runtime_entry.debounce_task is None
    assert runtime_entry.desired_state is None


async def test_profile_button_press_applies_profile_successfully(hass) -> None:
    """Successful button press should send immediately and mark the active profile."""

    hass.data.setdefault(DOMAIN, {})[DATA_CONFIRMATION_WINDOW_SECONDS] = 0.0

    entry = _add_entry(
        hass,
        profiles={
            "evening_relax": {
                CONF_PROFILE_ID: "evening_relax",
                CONF_NAME: "Evening Relax",
                CONF_POWER: True,
                CONF_FLAME: 2,
                CONF_FAN: 1,
                CONF_LIGHT: 1,
                CONF_FRONT: False,
                CONF_AUX: False,
                CONF_CPI: False,
            }
        },
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    runtime_entry = async_get_runtime_entries(hass)[entry.entry_id]
    backend = runtime_entry.backend
    assert isinstance(backend, FakeRFBackend)

    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": _profile_entity_id(entry.title, "Evening Relax")},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert len(backend.sent_packets) == 1
    assert backend.sent_packets[0].source == "saved_profile"
    assert backend.sent_packets[0].state.flame == 2
    assert runtime_entry.last_applied_profile_id == "evening_relax"
    assert runtime_entry.last_applied_profile_name == "Evening Relax"
    assert runtime_entry.desired_state is None


async def test_profile_button_press_failure_does_not_set_active_profile(hass, monkeypatch) -> None:
    """Failed button presses should leave the active profile unset and surface the error."""

    hass.data.setdefault(DOMAIN, {})[DATA_CONFIRMATION_WINDOW_SECONDS] = 0.0

    entry = _add_entry(
        hass,
        profiles={
            "evening_relax": {
                CONF_PROFILE_ID: "evening_relax",
                CONF_NAME: "Evening Relax",
                CONF_POWER: True,
                CONF_FLAME: 2,
                CONF_FAN: 1,
                CONF_LIGHT: 1,
                CONF_FRONT: False,
                CONF_AUX: False,
                CONF_CPI: False,
            }
        },
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    runtime_entry = async_get_runtime_entries(hass)[entry.entry_id]

    async def fake_send(self, packet):
        raise RuntimeError("boom")

    monkeypatch.setattr(FakeRFBackend, "send", fake_send)

    with pytest.raises(HomeAssistantError, match="boom"):
        await hass.services.async_call(
            "button",
            "press",
            {"entity_id": _profile_entity_id(entry.title, "Evening Relax")},
            blocking=True,
        )
    await hass.async_block_till_done()

    assert runtime_entry.last_applied_profile_id is None
    assert runtime_entry.last_applied_profile_name is None
    assert runtime_entry.last_error == "Transmit failed because boom; controls reverted to last known state."
