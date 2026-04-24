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
    CONF_REMOTE_ID,
    DOMAIN,
)


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


def _sensor_entity_id(title: str, name: str) -> str:
    return f"sensor.{slugify(title)}_{slugify(name)}"


async def test_default_visible_entities_are_created(hass) -> None:
    """Users should see only the simple status/state/issue sensors by default."""

    entry = _add_entry(hass, title="Living Room Fireplace", remote_id=0x3B3F02)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get(_sensor_entity_id(entry.title, "Status")).state == "ready"
    assert (
        hass.states.get(_sensor_entity_id(entry.title, "Last State")).state
        == "No fireplace state known yet."
    )
    assert (
        hass.states.get(_sensor_entity_id(entry.title, "Last Issue")).state
        == "No recent errors."
    )


async def test_diagnostic_entities_are_disabled_by_default(hass) -> None:
    """Protocol internals should exist but stay disabled until explicitly enabled."""

    entry = _add_entry(hass, title="Living Room Fireplace", remote_id=0x3B3F02)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    remote_id_entry = entity_registry.async_get(
        _sensor_entity_id(entry.title, "Remote ID")
    )
    cmd1_entry = entity_registry.async_get(_sensor_entity_id(entry.title, "Last Cmd1"))

    assert remote_id_entry is not None
    assert cmd1_entry is not None
    assert remote_id_entry.disabled_by == er.RegistryEntryDisabler.INTEGRATION
    assert cmd1_entry.disabled_by == er.RegistryEntryDisabler.INTEGRATION
    assert hass.states.get(remote_id_entry.entity_id) is None
    assert hass.states.get(cmd1_entry.entity_id) is None


async def test_status_updates_after_successful_set_state(hass) -> None:
    """User-facing status sensors should reflect successful service calls."""

    entry = _add_entry(hass, title="Living Room Fireplace", remote_id=0x3B3F02)
    assert await hass.config_entries.async_setup(entry.entry_id)

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

    assert hass.states.get(_sensor_entity_id(entry.title, "Status")).state == "last_command_succeeded"
    assert (
        hass.states.get(_sensor_entity_id(entry.title, "Last State")).state
        == "On, flame 1, fan 0, light 0."
    )
    assert (
        hass.states.get(_sensor_entity_id(entry.title, "Last Issue")).state
        == "No recent errors."
    )


async def test_last_error_summary_updates_after_failed_service_call(hass) -> None:
    """User-facing issue sensor should expose a natural-language backend failure."""

    entry = _add_entry(
        hass,
        title="Living Room Fireplace",
        remote_id=0x3B3F02,
        backend_type=BACKEND_YARDSTICK,
    )
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

    assert hass.states.get(_sensor_entity_id(entry.title, "Status")).state == "backend_unavailable"
    assert (
        hass.states.get(_sensor_entity_id(entry.title, "Last Issue")).state
        == "RF backend is unavailable."
    )


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
            _sensor_entity_id(entry.title, name),
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

    assert hass.states.get(_sensor_entity_id(entry.title, "Remote ID")).state == "3b3f02"
    assert hass.states.get(_sensor_entity_id(entry.title, "Last Cmd1")).state == "0x01"
    assert hass.states.get(_sensor_entity_id(entry.title, "Last Err1")).state == "0x76"
    assert (
        '"flame": 1'
        in hass.states.get(_sensor_entity_id(entry.title, "Last Requested State JSON")).state
    )
    assert hass.states.get(_sensor_entity_id(entry.title, "Last Backend")).state == "fake"
    assert "repeat_count=5" in hass.states.get(
        _sensor_entity_id(entry.title, "Last Transmission Plan")
    ).state


async def test_multiple_fireplaces_have_distinct_unique_ids(hass) -> None:
    """Each fireplace config entry should get its own unique entity instances."""

    first = _add_entry(hass, title="Living Room Fireplace", remote_id=0x3B3F02)
    assert await hass.config_entries.async_setup(first.entry_id)
    second = _add_entry(hass, title="Bedroom Fireplace", remote_id=0x3B3F03)
    assert await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    first_status = entity_registry.async_get(_sensor_entity_id(first.title, "Status"))
    second_status = entity_registry.async_get(_sensor_entity_id(second.title, "Status"))

    assert first_status is not None
    assert second_status is not None
    assert first_status.unique_id != second_status.unique_id
