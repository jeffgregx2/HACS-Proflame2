"""Home Assistant service-layer tests for Proflame2."""

from __future__ import annotations

import pytest

homeassistant = pytest.importorskip("homeassistant")
pytest.importorskip("pytest_homeassistant_custom_component")

pytestmark = pytest.mark.ha

from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.proflame2.const import (
    BACKEND_FAKE,
    BACKEND_YARDSTICK,
    CONF_AUX,
    CONF_BACKEND_TYPE,
    CONF_C1,
    CONF_C2,
    CONF_CONFIG_ENTRY_ID,
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
    DATA_RUNTIME_ENTRIES,
    DOMAIN,
)
from custom_components.proflame2.diagnostics import async_get_config_entry_diagnostics
from custom_components.proflame2.rf.base import SendResult
from custom_components.proflame2.rf.yardstick import YardStickBackend
from custom_components.proflame2.runtime import async_get_runtime_entries


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
    profiles: dict | None = None,
) -> MockConfigEntry:
    merged_options = {
        CONF_FAN: True,
        CONF_LIGHT: True,
        CONF_FRONT: False,
        CONF_AUX: False,
        CONF_CPI: False,
        CONF_PROFILES: profiles or {},
    }
    if options:
        merged_options.update(options)

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
        options=merged_options,
    )
    entry.add_to_hass(hass)
    return entry


async def test_set_state_with_one_fireplace_sends_exactly_one_frame(hass) -> None:
    """A single configured fireplace should accept an untargeted service call."""

    entry = _add_entry(hass, title="Living Room", remote_id=0x3B3F02)
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

    runtime_entry = async_get_runtime_entries(hass)[entry.entry_id]
    backend = runtime_entry.backend
    assert backend is not None
    assert len(backend.sent_packets) == 1
    packet = backend.sent_packets[0]
    frame = packet.frame
    assert packet.state == runtime_entry.last_packet.state
    assert frame.serial_id == 0x3B3F02
    assert frame.cmd1 == 0x01
    assert frame.err1 == 0x76
    assert frame.cmd2 == 0x01
    assert frame.err2 == 0x39
    assert runtime_entry.last_send_result is not None
    assert runtime_entry.last_packet is not None
    assert runtime_entry.last_packet.transmission_plan is not None


async def test_set_state_uses_yardstick_backend_when_available(hass, monkeypatch) -> None:
    """Service-layer TX should dispatch through Yard Stick instead of blocking early."""

    async def fake_send(self, packet):
        return SendResult(
            packet=packet,
            backend_name="yardstick",
            warnings=packet.warnings,
        )

    monkeypatch.setattr(YardStickBackend, "send", fake_send)

    entry = _add_entry(
        hass,
        title="Living Room",
        remote_id=0x3B3F02,
        backend_type=BACKEND_YARDSTICK,
    )
    assert await hass.config_entries.async_setup(entry.entry_id)

    await hass.services.async_call(
        DOMAIN,
        "set_state",
        {
            CONF_POWER: True,
            CONF_FLAME: 1,
            CONF_CONFIG_ENTRY_ID: entry.entry_id,
        },
        blocking=True,
    )

    runtime_entry = async_get_runtime_entries(hass)[entry.entry_id]
    assert runtime_entry.last_send_result is not None
    assert runtime_entry.last_send_result.backend_name == "yardstick"
    assert runtime_entry.last_error is None


async def test_diagnostics_include_last_send_result(hass) -> None:
    """Diagnostics should expose the last requested state and encoded frame."""

    entry = _add_entry(hass, title="Living Room", remote_id=0x3B3F02)
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

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    runtime = diagnostics["runtime"]
    assert runtime is not None
    assert runtime["last_packet"]["remote_id"] == 0x3B3F02
    assert runtime["last_requested_state"] == {
        "power": True,
        "flame": 1,
        "fan": 0,
        "light": 0,
        "front": False,
        "aux": False,
        "cpi": False,
        "thermostat": False,
    }
    assert runtime["last_encoded_frame"] == {
        "serial_id": 0x3B3F02,
        "cmd1": 0x01,
        "err1": 0x76,
        "cmd2": 0x01,
        "err2": 0x39,
    }
    assert runtime["last_send_result"]["backend_name"] == "fake"
    assert runtime["last_send_result"]["warnings"] == ()


@pytest.mark.parametrize(
    ("field", "value", "expected_cmd1", "expected_cmd2"),
    [
        (CONF_FAN, 3, 0x01, 0x01),
        (CONF_LIGHT, 4, 0x01, 0x01),
        (CONF_FRONT, True, 0x01, 0x01),
        (CONF_AUX, True, 0x01, 0x01),
        (CONF_CPI, True, 0x01, 0x01),
    ],
)
async def test_disabled_optional_feature_is_ignored_with_warning(
    hass, field: str, value: int | bool, expected_cmd1: int, expected_cmd2: int
) -> None:
    """Disabled optional features should be ignored and recorded as warnings."""

    entry = _add_entry(
        hass,
        title="Living Room",
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
            field: value,
        },
        blocking=True,
    )

    runtime_entry = async_get_runtime_entries(hass)[entry.entry_id]
    backend = runtime_entry.backend
    assert backend is not None
    assert len(backend.sent_packets) == 1
    packet = backend.sent_packets[0]
    frame = packet.frame
    assert frame.cmd1 == expected_cmd1
    assert frame.cmd2 == expected_cmd2
    assert frame.err1 == 0x76
    assert frame.err2 == 0x39
    assert runtime_entry.last_send_result is not None
    assert runtime_entry.last_send_result.warnings == (
        f"Ignored {field} because it is disabled for this fireplace.",
    )

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["runtime"]["last_send_result"]["warnings"] == (
        f"Ignored {field} because it is disabled for this fireplace.",
    )


async def test_disabled_optional_feature_omitted_has_no_warning(hass) -> None:
    """Disabled optional features should stay quiet when omitted."""

    entry = _add_entry(
        hass,
        title="Living Room",
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
        },
        blocking=True,
    )

    runtime_entry = async_get_runtime_entries(hass)[entry.entry_id]
    assert runtime_entry.last_send_result is not None
    assert runtime_entry.last_send_result.warnings == ()


async def test_invalid_ranges_are_rejected(hass) -> None:
    """Invalid manual ranges must fail clearly."""

    entry = _add_entry(hass, title="Living Room", remote_id=0x3B3F02)
    assert await hass.config_entries.async_setup(entry.entry_id)

    with pytest.raises(HomeAssistantError, match="flame must be between 1 and 6"):
        await hass.services.async_call(
            DOMAIN,
            "set_state",
            {
                CONF_POWER: True,
                CONF_FLAME: 7,
            },
            blocking=True,
        )

    with pytest.raises(HomeAssistantError, match="light must be between 0 and 6"):
        await hass.services.async_call(
            DOMAIN,
            "set_state",
            {
                CONF_POWER: True,
                CONF_FLAME: 1,
                CONF_LIGHT: 7,
            },
            blocking=True,
        )


async def test_invalid_disabled_optional_ranges_are_still_rejected(hass) -> None:
    """Provided numeric optionals must still validate even when disabled."""

    entry = _add_entry(
        hass,
        title="Living Room",
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

    with pytest.raises(HomeAssistantError, match="fan must be between 0 and 6"):
        await hass.services.async_call(
            DOMAIN,
            "set_state",
            {
                CONF_POWER: True,
                CONF_FLAME: 1,
                CONF_FAN: 7,
            },
            blocking=True,
        )

    with pytest.raises(HomeAssistantError, match="light must be between 0 and 6"):
        await hass.services.async_call(
            DOMAIN,
            "set_state",
            {
                CONF_POWER: True,
                CONF_FLAME: 1,
                CONF_LIGHT: 7,
            },
            blocking=True,
        )


async def test_multiple_fireplaces_without_target_is_rejected(hass) -> None:
    """Ambiguous service calls should fail when multiple fireplaces exist."""

    first = _add_entry(hass, title="Living Room", remote_id=0x3B3F02)
    assert await hass.config_entries.async_setup(first.entry_id)
    second = _add_entry(hass, title="Bedroom", remote_id=0x3B3F03)
    assert await hass.config_entries.async_setup(second.entry_id)

    with pytest.raises(HomeAssistantError, match="Multiple Proflame2 fireplaces are configured"):
        await hass.services.async_call(
            DOMAIN,
            "set_state",
            {
                CONF_POWER: True,
                CONF_FLAME: 1,
            },
            blocking=True,
        )


async def test_selected_fireplace_target_works(hass) -> None:
    """Device targeting should select the intended fireplace."""

    first = _add_entry(hass, title="Living Room", remote_id=0x3B3F02)
    assert await hass.config_entries.async_setup(first.entry_id)
    second = _add_entry(hass, title="Bedroom", remote_id=0x3B3F03)
    assert await hass.config_entries.async_setup(second.entry_id)

    second_runtime = async_get_runtime_entries(hass)[second.entry_id]
    await hass.services.async_call(
        DOMAIN,
        "set_state",
        {
            CONF_POWER: True,
            CONF_FLAME: 1,
        },
        blocking=True,
        target={"device_id": second_runtime.device_id},
    )

    first_backend = async_get_runtime_entries(hass)[first.entry_id].backend
    second_backend = async_get_runtime_entries(hass)[second.entry_id].backend
    assert first_backend is not None
    assert second_backend is not None
    assert len(first_backend.sent_packets) == 0
    assert len(second_backend.sent_packets) == 1
    assert second_backend.sent_packets[0].frame.serial_id == 0x3B3F03


async def test_config_entry_id_target_works(hass) -> None:
    """config_entry_id should also target one configured fireplace."""

    first = _add_entry(hass, title="Living Room", remote_id=0x3B3F02)
    assert await hass.config_entries.async_setup(first.entry_id)
    second = _add_entry(hass, title="Bedroom", remote_id=0x3B3F03)
    assert await hass.config_entries.async_setup(second.entry_id)

    await hass.services.async_call(
        DOMAIN,
        "set_state",
        {
            CONF_CONFIG_ENTRY_ID: first.entry_id,
            CONF_POWER: True,
            CONF_FLAME: 1,
        },
        blocking=True,
    )

    first_backend = async_get_runtime_entries(hass)[first.entry_id].backend
    second_backend = async_get_runtime_entries(hass)[second.entry_id].backend
    assert first_backend is not None
    assert second_backend is not None
    assert len(first_backend.sent_packets) == 1
    assert len(second_backend.sent_packets) == 0


async def test_yardstick_backend_runtime_error_surfaces_cleanly(hass, monkeypatch) -> None:
    """Configured yardstick entries should surface backend TX failures cleanly."""

    async def fake_send(self, packet):
        raise RuntimeError("USB transmit failed")

    entry = _add_entry(
        hass,
        title="Living Room",
        remote_id=0x3B3F02,
        backend_type=BACKEND_YARDSTICK,
    )
    monkeypatch.setattr(YardStickBackend, "send", fake_send)
    assert await hass.config_entries.async_setup(entry.entry_id)

    with pytest.raises(HomeAssistantError, match="USB transmit failed"):
        await hass.services.async_call(
            DOMAIN,
            "set_state",
            {
                CONF_POWER: True,
                CONF_FLAME: 1,
            },
            blocking=True,
        )


async def test_apply_profile_sends_expected_frame_via_same_backend_path(hass) -> None:
    """Saved profiles should route through the same fake backend execution path."""

    entry = _add_entry(
        hass,
        title="Living Room",
        remote_id=0x3B3F02,
        profiles={
            "minimum_flame": {
                CONF_PROFILE_ID: "minimum_flame",
                CONF_NAME: "Minimum Flame",
                CONF_POWER: True,
                CONF_FLAME: 1,
                CONF_FAN: 0,
                CONF_LIGHT: 2,
                CONF_FRONT: False,
                CONF_AUX: False,
                CONF_CPI: False,
            }
        },
    )
    assert await hass.config_entries.async_setup(entry.entry_id)

    await hass.services.async_call(
        DOMAIN,
        "apply_profile",
        {"profile_id": "minimum_flame"},
        blocking=True,
    )

    runtime_entry = async_get_runtime_entries(hass)[entry.entry_id]
    backend = runtime_entry.backend
    assert backend is not None
    assert len(backend.sent_packets) == 1
    packet = backend.sent_packets[0]
    assert packet.source == "saved_profile"
    assert packet.frame.cmd1 == 0x21
    assert packet.frame.err1 == 0x14
    assert packet.frame.cmd2 == 0x01
    assert packet.frame.err2 == 0x39
    assert runtime_entry.last_applied_profile_id == "minimum_flame"
    assert runtime_entry.last_applied_profile_name == "Minimum Flame"


async def test_apply_profile_deleted_profile_is_not_callable(hass) -> None:
    """Applying a missing saved profile should fail clearly."""

    entry = _add_entry(hass, title="Living Room", remote_id=0x3B3F02, profiles={})
    assert await hass.config_entries.async_setup(entry.entry_id)

    with pytest.raises(HomeAssistantError, match="Unknown saved profile 'minimum_flame'"):
        await hass.services.async_call(
            DOMAIN,
            "apply_profile",
            {"profile_id": "minimum_flame"},
            blocking=True,
        )


async def test_multiple_fireplaces_keep_separate_profile_sets(hass) -> None:
    """Profile application should stay scoped to the targeted fireplace entry."""

    first = _add_entry(
        hass,
        title="Living Room",
        remote_id=0x3B3F02,
        profiles={
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
    )
    assert await hass.config_entries.async_setup(first.entry_id)
    second = _add_entry(
        hass,
        title="Bedroom",
        remote_id=0x3B3F03,
        profiles={
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
            }
        },
    )
    assert await hass.config_entries.async_setup(second.entry_id)

    with pytest.raises(HomeAssistantError, match="Unknown saved profile 'minimum_flame'"):
        await hass.services.async_call(
            DOMAIN,
            "apply_profile",
            {"profile_id": "minimum_flame"},
            blocking=True,
            target={"device_id": async_get_runtime_entries(hass)[second.entry_id].device_id},
        )

    await hass.services.async_call(
        DOMAIN,
        "apply_profile",
        {"profile_id": "minimum_flame"},
        blocking=True,
        target={"device_id": async_get_runtime_entries(hass)[first.entry_id].device_id},
    )

    first_backend = async_get_runtime_entries(hass)[first.entry_id].backend
    second_backend = async_get_runtime_entries(hass)[second.entry_id].backend
    assert first_backend is not None
    assert second_backend is not None
    assert len(first_backend.sent_packets) == 1
    assert len(second_backend.sent_packets) == 0
