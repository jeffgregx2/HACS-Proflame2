"""Home Assistant service-layer tests for Proflame2."""

from __future__ import annotations

import logging

import pytest

homeassistant = pytest.importorskip("homeassistant")
pytest.importorskip("pytest_homeassistant_custom_component")

pytestmark = pytest.mark.ha

from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.proflame2.const import (
    BACKEND_FAKE,
    BACKEND_YARDSTICK,
    CONF_ACTION_LABEL,
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
    CONF_THERMOSTAT,
    DOMAIN,
    SERVICE_DISPLAY_STATE_UPDATE,
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


async def test_set_state_emits_presend_diagnostic(hass, caplog) -> None:
    """Explicit service sends should log the encoded frame and active profile."""

    entry = _add_entry(hass, title="Living Room", remote_id=0x3B3F02)
    assert await hass.config_entries.async_setup(entry.entry_id)

    caplog.set_level(logging.WARNING, logger="custom_components.proflame2.services")

    await hass.services.async_call(
        DOMAIN,
        "set_state",
        {
            CONF_POWER: True,
            CONF_FLAME: 6,
            CONF_FAN: 0,
            CONF_LIGHT: 0,
        },
        blocking=True,
    )

    assert "PROFLAME_TX_PRESEND" in caplog.text
    assert f"config_entry_id={entry.entry_id}" in caplog.text
    assert "source=homeassistant_service" in caplog.text
    assert "controller_id=fake" in caplog.text
    assert "serial_id=3b3f02" in caplog.text
    assert "c1=5 d1=7 c2=1 d2=8" in caplog.text
    assert "cmd1=0x01 err1=0x76 cmd2=0x06 err2=0xDE" in caplog.text
    assert "payload_bit_length=182" in caplog.text
    assert "repeat_count=5" in caplog.text


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


async def test_display_state_update_service_calls_backend_without_tx(hass) -> None:
    entry = _add_entry(
        hass,
        title="Living Room",
        remote_id=0x3B3F02,
        backend_type=BACKEND_FAKE,
    )
    assert await hass.config_entries.async_setup(entry.entry_id)

    runtime_entry = async_get_runtime_entries(hass)[entry.entry_id]
    received = {}

    class _BackendStub:
        async def update_display_state(self, display_state):
            received["display_state"] = display_state

        async def close(self, *, reason: str | None = None):
            return None

    runtime_entry.backend = _BackendStub()

    await hass.services.async_call(
        DOMAIN,
        SERVICE_DISPLAY_STATE_UPDATE,
        {
            CONF_CONFIG_ENTRY_ID: entry.entry_id,
            CONF_POWER: True,
            CONF_FLAME: 4,
            CONF_FAN: 2,
            CONF_LIGHT: 1,
            CONF_THERMOSTAT: True,
            CONF_AUX: False,
            CONF_ACTION_LABEL: "Startup sync",
        },
        blocking=True,
    )

    display_state = received["display_state"]
    assert display_state.power is True
    assert display_state.flame == 4
    assert display_state.fan == 2
    assert display_state.light == 1
    assert display_state.thermostat is True
    assert display_state.aux is False
    assert display_state.action_label == "Startup sync"


async def test_runtime_display_state_sync_pushes_current_known_state(hass) -> None:
    from custom_components.proflame2.services import async_sync_runtime_display_state

    entry = _add_entry(
        hass,
        title="Living Room",
        remote_id=0x3B3F02,
        backend_type=BACKEND_FAKE,
    )
    assert await hass.config_entries.async_setup(entry.entry_id)

    runtime_entry = async_get_runtime_entries(hass)[entry.entry_id]
    received = {}

    class _BackendStub:
        async def update_display_state(self, display_state):
            received["display_state"] = display_state

        async def close(self, *, reason: str | None = None):
            return None

    runtime_entry.backend = _BackendStub()

    await async_sync_runtime_display_state(hass, runtime_entry, action_label="Startup sync")

    display_state = received["display_state"]
    assert display_state.power is True
    assert display_state.flame == 1
    assert display_state.fan == 0
    assert display_state.light == 0
    assert display_state.front is False
    assert display_state.aux is False
    assert display_state.action_label == "Startup sync"


async def test_setup_entry_defers_display_sync_when_linked_esphome_not_ready(hass, monkeypatch) -> None:
    entry = _add_entry(
        hass,
        title="Living Room",
        remote_id=0x3B3F02,
        backend_type=BACKEND_FAKE,
    )

    from custom_components.proflame2 import services as services_module

    async def _raise_not_ready(*args, **kwargs):
        raise RuntimeError(
            "ESPHome backend is unavailable: Linked ESPHome entry is not loaded or has no runtime_data: abc123"
        )

    created = []

    def _capture_task(coro):
        created.append(coro)
        return None

    monkeypatch.setattr(services_module, "async_sync_runtime_display_state", _raise_not_ready)
    monkeypatch.setattr(hass, "async_create_task", _capture_task)

    try:
        assert await hass.config_entries.async_setup(entry.entry_id)
        assert created
    finally:
        for coro in created:
            coro.close()


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
    assert runtime_entry.last_send_result.warnings == (f"Ignored {field} because it is disabled for this fireplace.",)

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


async def test_same_serial_different_backends_device_target_routes_to_requested_runtime(hass, monkeypatch) -> None:
    """Distinct devices for the same serial should still route to the correct backend."""

    async def fake_send(self, packet):
        return SendResult(
            packet=packet,
            backend_name="yardstick",
            warnings=packet.warnings,
        )

    monkeypatch.setattr(YardStickBackend, "send", fake_send)

    first = _add_entry(
        hass,
        title="LilyGO Fireplace",
        remote_id=0x3B3F02,
        backend_type=BACKEND_FAKE,
    )
    assert await hass.config_entries.async_setup(first.entry_id)
    second = _add_entry(
        hass,
        title="YardStick Fireplace",
        remote_id=0x3B3F02,
        backend_type=BACKEND_YARDSTICK,
    )
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
    assert first_backend is not None
    assert len(first_backend.sent_packets) == 0
    assert second_runtime.last_send_result is not None
    assert second_runtime.last_send_result.backend_name == "yardstick"


async def test_ambiguous_device_target_fails_loudly(hass) -> None:
    """Service resolution should refuse ambiguous device->runtime mappings."""

    first = _add_entry(hass, title="First", remote_id=0x3B3F02)
    assert await hass.config_entries.async_setup(first.entry_id)
    second = _add_entry(hass, title="Second", remote_id=0x3B3F03)
    assert await hass.config_entries.async_setup(second.entry_id)

    runtime_entries = async_get_runtime_entries(hass)
    runtime_entries[second.entry_id].device_id = runtime_entries[first.entry_id].device_id

    with pytest.raises(HomeAssistantError, match="Ambiguous Proflame2 device target"):
        await hass.services.async_call(
            DOMAIN,
            "set_state",
            {
                CONF_POWER: True,
                CONF_FLAME: 1,
            },
            blocking=True,
            target={"device_id": runtime_entries[first.entry_id].device_id},
        )


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
