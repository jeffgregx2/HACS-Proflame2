"""Home Assistant entity-surface tests for Proflame2."""

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
    BACKEND_YARDSTICK,
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
    CONF_POWER,
    CONF_PROFILE_ID,
    CONF_PROFILES,
    CONF_REMOTE_ID,
    DOMAIN,
)
from custom_components.proflame2.rf.yardstick import YardStickBackend


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading integrations from the local custom_components directory."""

    yield


def _add_entry(
    hass,
    *,
    title: str,
    remote_id: int,
    backend_type: str = BACKEND_FAKE,
    options: dict | None = None,
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=title,
        data={
            "name": title,
            CONF_BACKEND_TYPE: backend_type,
            CONF_REMOTE_ID: remote_id,
            CONF_C1: 5,
            CONF_D1: 7,
            CONF_C2: 1,
            CONF_D2: 8,
        },
        options=options
        or {
            CONF_FAN: True,
            CONF_LIGHT: True,
            CONF_FRONT: False,
            CONF_AUX: False,
            CONF_CPI: False,
        },
    )
    entry.add_to_hass(hass)
    return entry


def _primary_entity_id(title: str) -> str:
    return f"sensor.{slugify(title)}"


def _secondary_entity_id(title: str, name: str) -> str:
    return f"sensor.{slugify(title)}_{slugify(name)}"


async def test_primary_entity_and_last_issue_sensor_are_created(hass) -> None:
    """Users should see one primary fireplace entity plus the last-issue sensor."""

    entry = _add_entry(hass, title="Living Room Fireplace", remote_id=0x3B3F02)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    primary = hass.states.get(_primary_entity_id(entry.title))
    last_issue = hass.states.get(_secondary_entity_id(entry.title, "Last Issue"))

    assert primary is not None
    assert primary.state == "On · Flame 1"
    assert primary.attributes["icon"] == "mdi:fireplace"
    assert primary.attributes["operational_status"] == "ready"
    assert primary.attributes["power"] == "On"
    assert primary.attributes["flame"] == "Level 1"
    assert primary.attributes["fan"] == "Level 0"
    assert primary.attributes["light"] == "Level 0"
    assert primary.attributes["last_issue"] == "None"
    assert primary.attributes["last_update_source"] == "Simulated Packet"
    assert hass.states.get(_secondary_entity_id(entry.title, "Summary")) is None

    assert last_issue is not None
    assert last_issue.state == "No recent errors."


async def test_primary_entity_icon_switches_with_power_state(hass) -> None:
    """The primary fireplace icon should reflect on/off semantic state."""

    entry = _add_entry(hass, title="Living Room Fireplace", remote_id=0x3B3F02)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    primary = hass.states.get(_primary_entity_id(entry.title))
    assert primary is not None
    assert primary.state == "On · Flame 1"
    assert primary.attributes["icon"] == "mdi:fireplace"

    await hass.services.async_call(
        DOMAIN,
        "set_state",
        {
            CONF_POWER: False,
        },
        blocking=True,
    )
    await hass.async_block_till_done()

    primary = hass.states.get(_primary_entity_id(entry.title))
    assert primary is not None
    assert primary.state == "Off"
    assert primary.attributes["icon"] == "mdi:fireplace-off"
    assert primary.attributes["operational_status"] == "ready"


async def test_enabled_optional_attributes_are_present_even_before_known_state(hass) -> None:
    """Enabled optional attributes should remain visible before a state is known."""

    entry = _add_entry(
        hass,
        title="Living Room Fireplace",
        remote_id=0x3B3F02,
        options={
            CONF_FAN: True,
            CONF_LIGHT: True,
            CONF_FRONT: True,
            CONF_AUX: True,
            CONF_CPI: True,
        },
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    primary = hass.states.get(_primary_entity_id(entry.title))
    assert primary is not None
    assert primary.attributes["fan"] == "Level 0"
    assert primary.attributes["light"] == "Level 0"
    assert primary.attributes["front_burner"] == "Off"
    assert primary.attributes["aux"] == "Off"
    assert primary.attributes["cpi"] == "Off"
    assert primary.state == "On · Flame 1"
    assert primary.attributes["operational_status"] == "ready"


async def test_primary_entity_renders_human_readable_attributes(hass) -> None:
    """The primary fireplace entity should expose semantic human-readable attributes."""

    entry = _add_entry(
        hass,
        title="Living Room Fireplace",
        remote_id=0x3B3F02,
        options={
            CONF_FAN: True,
            CONF_LIGHT: True,
            CONF_FRONT: True,
            CONF_AUX: True,
            CONF_CPI: True,
        },
    )
    assert await hass.config_entries.async_setup(entry.entry_id)

    await hass.services.async_call(
        DOMAIN,
        "set_state",
        {
            CONF_POWER: True,
            CONF_FLAME: 2,
            CONF_FAN: 1,
            CONF_LIGHT: 3,
            CONF_FRONT: False,
            CONF_AUX: False,
            CONF_CPI: True,
        },
        blocking=True,
    )
    await hass.async_block_till_done()

    primary = hass.states.get(_primary_entity_id(entry.title))
    assert primary is not None
    assert primary.state == "On · Flame 2 · Fan 1 · Light 3 · CPI On"
    assert primary.attributes["operational_status"] == "ready"
    assert primary.attributes["power"] == "On"
    assert primary.attributes["flame"] == "Level 2"
    assert primary.attributes["fan"] == "Level 1"
    assert primary.attributes["light"] == "Level 3"
    assert primary.attributes["front_burner"] == "Off"
    assert primary.attributes["aux"] == "Off"
    assert primary.attributes["cpi"] == "On"
    assert primary.attributes["last_update_source"] == "Direct Control"
    assert primary.attributes["last_issue"] == "None"


async def test_disabled_optional_attributes_are_hidden(hass) -> None:
    """Disabled optional fireplace features should not appear on the primary entity."""

    entry = _add_entry(
        hass,
        title="Living Room Fireplace",
        remote_id=0x3B3F02,
        options={
            CONF_FAN: False,
            CONF_LIGHT: False,
            CONF_FRONT: False,
            CONF_AUX: False,
            CONF_CPI: False,
        },
    )
    assert await hass.config_entries.async_setup(entry.entry_id)

    await hass.services.async_call(
        DOMAIN,
        "set_state",
        {
            CONF_POWER: True,
            CONF_FLAME: 1,
            CONF_FAN: 3,
            CONF_LIGHT: 2,
            CONF_FRONT: True,
            CONF_AUX: True,
            CONF_CPI: True,
        },
        blocking=True,
    )
    await hass.async_block_till_done()

    primary = hass.states.get(_primary_entity_id(entry.title))
    assert primary is not None
    assert primary.state == "On · Flame 1"
    assert primary.attributes["operational_status"] == "ready"
    assert primary.attributes["power"] == "On"
    assert primary.attributes["flame"] == "Level 1"
    assert "fan" not in primary.attributes
    assert "light" not in primary.attributes
    assert "front_burner" not in primary.attributes
    assert "aux" not in primary.attributes
    assert "cpi" not in primary.attributes
    assert primary.attributes["last_issue"] == "Fan was ignored because it is disabled for this fireplace."


async def test_diagnostic_entities_are_disabled_by_default(hass) -> None:
    """Protocol internals should exist but stay disabled until explicitly enabled."""

    entry = _add_entry(hass, title="Living Room Fireplace", remote_id=0x3B3F02)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    remote_id_entry = entity_registry.async_get(
        _secondary_entity_id(entry.title, "Remote ID")
    )
    cmd1_entry = entity_registry.async_get(_secondary_entity_id(entry.title, "Last Cmd1"))

    assert remote_id_entry is not None
    assert cmd1_entry is not None
    assert remote_id_entry.disabled_by == er.RegistryEntryDisabler.INTEGRATION
    assert cmd1_entry.disabled_by == er.RegistryEntryDisabler.INTEGRATION
    assert hass.states.get(remote_id_entry.entity_id) is None
    assert hass.states.get(cmd1_entry.entity_id) is None


async def test_last_issue_updates_after_failed_service_call(hass, monkeypatch) -> None:
    """The primary entity and last-issue sensor should reflect backend failures."""

    async def fake_send(self, packet):
        raise RuntimeError("RF backend is unavailable.")

    entry = _add_entry(
        hass,
        title="Living Room Fireplace",
        remote_id=0x3B3F02,
        backend_type=BACKEND_YARDSTICK,
    )
    monkeypatch.setattr(YardStickBackend, "send", fake_send)
    assert await hass.config_entries.async_setup(entry.entry_id)

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            "set_state",
            {
                CONF_POWER: True,
                CONF_FLAME: 1,
            },
            blocking=True,
        )
    await hass.async_block_till_done()

    primary = hass.states.get(_primary_entity_id(entry.title))
    issue = hass.states.get(_secondary_entity_id(entry.title, "Last Issue"))

    assert primary is not None
    assert primary.state == "Off"
    assert primary.attributes["operational_status"] == "failed"
    assert (
        primary.attributes["last_issue"]
        == "Transmit failed because RF backend is unavailable; controls reverted to last known state."
    )
    assert issue is not None
    assert (
        issue.state
        == "Transmit failed because RF backend is unavailable; controls reverted to last known state."
    )


async def test_primary_entity_shows_active_profile_only_after_apply_profile(hass) -> None:
    """The primary entity should show a profile only when it was explicitly applied."""

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Living Room Fireplace",
        data={
            "name": "Living Room Fireplace",
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
            CONF_FRONT: True,
            CONF_AUX: True,
            CONF_CPI: True,
            CONF_PROFILES: {
                "evening_relax": {
                    CONF_PROFILE_ID: "evening_relax",
                    "name": "Evening Relax",
                    CONF_POWER: True,
                    CONF_FLAME: 2,
                    CONF_FAN: 1,
                    CONF_LIGHT: 0,
                    CONF_FRONT: False,
                    CONF_AUX: False,
                    CONF_CPI: False,
                }
            },
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)

    await hass.services.async_call(
        DOMAIN,
        "apply_profile",
        {CONF_PROFILE_ID: "evening_relax"},
        blocking=True,
    )
    await hass.async_block_till_done()

    primary = hass.states.get(_primary_entity_id(entry.title))
    assert primary is not None
    assert primary.state == "On · Flame 2 · Fan 1 · Evening Relax"
    assert primary.attributes["active_profile"] == "Evening Relax"
    assert primary.attributes["operational_status"] == "ready"

    await hass.services.async_call(
        DOMAIN,
        "set_state",
        {
            CONF_POWER: True,
            CONF_FLAME: 3,
            CONF_FAN: 1,
            CONF_LIGHT: 0,
        },
        blocking=True,
    )
    await hass.async_block_till_done()

    primary = hass.states.get(_primary_entity_id(entry.title))
    assert primary is not None
    assert primary.state == "On · Flame 3 · Fan 1"
    assert "active_profile" not in primary.attributes


async def test_diagnostic_entities_reflect_runtime_packet_data(hass) -> None:
    """Enabled diagnostic entities should mirror the current runtime packet/frame values."""

    entry = _add_entry(hass, title="Living Room Fireplace", remote_id=0x3B3F02)
    assert await hass.config_entries.async_setup(entry.entry_id)

    entity_registry = er.async_get(hass)
    for name in (
        "Remote ID",
        "Last Cmd1",
        "Last Err1",
        "Last Requested State JSON",
        "Last Backend",
        "Last Transmission Plan",
    ):
        entity_registry.async_update_entity(
            _secondary_entity_id(entry.title, name),
            disabled_by=None,
        )

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    await hass.services.async_call(
        DOMAIN,
        "set_state",
        {
            CONF_POWER: True,
            CONF_FLAME: 1,
            CONF_FAN: 0,
            CONF_LIGHT: 0,
        },
        blocking=True,
    )
    await hass.async_block_till_done()

    assert hass.states.get(_secondary_entity_id(entry.title, "Remote ID")).state == "3b3f02"
    assert hass.states.get(_secondary_entity_id(entry.title, "Last Cmd1")).state == "0x01"
    assert hass.states.get(_secondary_entity_id(entry.title, "Last Err1")).state == "0x76"
    assert (
        '"flame": 1'
        in hass.states.get(_secondary_entity_id(entry.title, "Last Requested State JSON")).state
    )
    assert hass.states.get(_secondary_entity_id(entry.title, "Last Backend")).state == "fake"
    assert "repeat_count=5" in hass.states.get(
        _secondary_entity_id(entry.title, "Last Transmission Plan")
    ).state


async def test_multiple_fireplaces_have_clean_entity_names_and_unique_ids(hass) -> None:
    """Each fireplace should get one clean primary entity and distinct unique ids."""

    first = _add_entry(hass, title="Living Room Fireplace", remote_id=0x3B3F02)
    assert await hass.config_entries.async_setup(first.entry_id)
    second = _add_entry(hass, title="Bedroom Fireplace", remote_id=0x3B3F03)
    assert await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    first_primary = entity_registry.async_get(_primary_entity_id(first.title))
    second_primary = entity_registry.async_get(_primary_entity_id(second.title))

    assert first_primary is not None
    assert second_primary is not None
    assert first_primary.unique_id != second_primary.unique_id
    assert first_primary.original_name == first.title
    assert second_primary.original_name == second.title
    assert hass.states.get(_secondary_entity_id(first.title, "Summary")) is None
    assert hass.states.get(_secondary_entity_id(second.title, "Summary")) is None
