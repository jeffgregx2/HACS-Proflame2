"""Home Assistant control-entity tests for debounced Proflame2 UI controls."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

homeassistant = pytest.importorskip("homeassistant")
pytest.importorskip("pytest_homeassistant_custom_component")

pytestmark = pytest.mark.ha

from homeassistant.util import slugify
from homeassistant.exceptions import HomeAssistantError
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
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
    CONF_DEBUG_LOGGING,
    CONF_FAN,
    CONF_FLAME,
    CONF_FRONT,
    CONF_INITIAL_FRAME,
    CONF_INITIAL_PACKET_SOURCE,
    CONF_LIGHT,
    CONF_POWER,
    CONF_REMOTE_ID,
    DATA_ACTIVE_LISTENING,
    DATA_CONFIRMATION_RECEIVE_TIMEOUT_SECONDS,
    DATA_CONFIRMATION_WINDOW_SECONDS,
    DATA_CONTROL_DEBOUNCE_SECONDS,
    DOMAIN,
    STATE_CONFIDENCE_OBSERVED,
    STATE_CONFIDENCE_REQUESTED,
    STATE_CONFIDENCE_RESTORED,
)
from custom_components.proflame2.packet_debug import PacketDebugLogPaths
from custom_components.proflame2.protocol.models import FireplaceState
from custom_components.proflame2.protocol.packet import ProflameFrame, ProflamePacket
from custom_components.proflame2.rf.base import SendResult
from custom_components.proflame2.rf.fake import FakeRFBackend
from custom_components.proflame2.runtime import (
    _runtime_store,
    async_get_runtime_entries,
    async_set_runtime_current_state,
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


def _add_entry(
    hass,
    *,
    title: str = "Living Room Fireplace",
    remote_id: int = 0x3B3F02,
    options: dict | None = None,
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=title,
        data={
            "name": title,
            CONF_BACKEND_TYPE: BACKEND_FAKE,
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


def _sensor_entity_id(title: str) -> str:
    return f"sensor.{slugify(title)}"


def _switch_entity_id(title: str, name: str) -> str:
    return f"switch.{slugify(title)}_{slugify(name)}"


def _number_entity_id(title: str, name: str) -> str:
    return f"number.{slugify(title)}_{slugify(name)}"


async def test_control_entities_are_created_only_for_enabled_features(hass) -> None:
    """Power/flame should always exist, with optional controls gated by features."""

    entry = _add_entry(
        hass,
        options={
            CONF_FAN: True,
            CONF_LIGHT: False,
            CONF_FRONT: True,
            CONF_AUX: False,
            CONF_CPI: True,
        },
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get(_switch_entity_id(entry.title, "Power")) is not None
    assert hass.states.get(_number_entity_id(entry.title, "Flame")) is not None
    assert hass.states.get(_number_entity_id(entry.title, "Fan")) is not None
    assert hass.states.get(_switch_entity_id(entry.title, "Front Burner")) is not None
    assert hass.states.get(_switch_entity_id(entry.title, "CPI")) is not None
    assert hass.states.get(_number_entity_id(entry.title, "Light")) is None
    assert hass.states.get(_switch_entity_id(entry.title, "Aux")) is None


async def test_number_controls_use_integer_configuration_and_values(hass) -> None:
    """Discrete Proflame levels should stay integer-oriented at the entity boundary."""

    entry = _add_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    flame = next(entity for entity in hass.data["number"].entities if entity.entity_id == _number_entity_id(entry.title, "Flame"))
    fan = next(entity for entity in hass.data["number"].entities if entity.entity_id == _number_entity_id(entry.title, "Fan"))
    light = next(entity for entity in hass.data["number"].entities if entity.entity_id == _number_entity_id(entry.title, "Light"))

    assert flame.native_step == 1
    assert fan.native_step == 1
    assert light.native_step == 1
    assert flame.native_min_value == 1
    assert flame.native_max_value == 6
    assert fan.native_min_value == 0
    assert fan.native_max_value == 6
    assert light.native_min_value == 0
    assert light.native_max_value == 6
    assert flame.native_value == 1
    assert fan.native_value == 0
    assert light.native_value == 0


async def test_debounced_controls_send_one_latest_full_state_packet(hass) -> None:
    """Multiple edits inside the debounce window should produce one latest-state TX."""

    hass.data.setdefault(DOMAIN, {})[DATA_CONTROL_DEBOUNCE_SECONDS] = 0.01
    hass.data.setdefault(DOMAIN, {})[DATA_CONFIRMATION_WINDOW_SECONDS] = 0.0

    entry = _add_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    runtime_entry = async_get_runtime_entries(hass)[entry.entry_id]
    backend = runtime_entry.backend
    assert isinstance(backend, FakeRFBackend)

    await hass.services.async_call(
        "number",
        "set_value",
        {
            "entity_id": _number_entity_id(entry.title, "Flame"),
            "value": 2,
        },
        blocking=True,
    )
    await hass.services.async_call(
        "number",
        "set_value",
        {
            "entity_id": _number_entity_id(entry.title, "Fan"),
            "value": 3,
        },
        blocking=True,
    )

    primary = hass.states.get(_sensor_entity_id(entry.title))
    assert primary is not None
    assert primary.attributes["operational_status"] == "pending"
    assert primary.attributes["pending_state"] == "On · Flame 2 · Fan 3"
    assert len(backend.sent_packets) == 0

    await asyncio.sleep(0.03)
    await hass.async_block_till_done()

    assert len(backend.sent_packets) == 1
    assert backend.sent_packets[0].state.flame == 2
    assert backend.sent_packets[0].state.fan == 3
    primary = hass.states.get(_sensor_entity_id(entry.title))
    assert primary is not None
    assert primary.state == "On · Flame 2 · Fan 3"
    assert primary.attributes["operational_status"] == "ready"
    assert primary.attributes["pending_state"] is None
    assert primary.attributes["state_confidence"] == STATE_CONFIDENCE_REQUESTED


async def test_controls_show_desired_state_during_debounce_and_active_send_without_flicker(
    hass, monkeypatch
) -> None:
    """Controls should keep showing desired values until TX finishes successfully."""

    hass.data.setdefault(DOMAIN, {})[DATA_CONTROL_DEBOUNCE_SECONDS] = 0.01
    hass.data.setdefault(DOMAIN, {})[DATA_CONFIRMATION_WINDOW_SECONDS] = 0.0

    send_started = asyncio.Event()
    allow_send_finish = asyncio.Event()

    async def slow_send(self, packet):
        send_started.set()
        await allow_send_finish.wait()
        self.sent_packets.append(packet)
        return SendResult(packet=packet, backend_name=self.name)

    monkeypatch.setattr(FakeRFBackend, "send", slow_send)

    entry = _add_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": _number_entity_id(entry.title, "Flame"), "value": 3},
        blocking=True,
    )
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": _number_entity_id(entry.title, "Fan"), "value": 6},
        blocking=True,
    )

    flame = hass.states.get(_number_entity_id(entry.title, "Flame"))
    fan = hass.states.get(_number_entity_id(entry.title, "Fan"))
    primary = hass.states.get(_sensor_entity_id(entry.title))
    assert flame is not None and flame.state == "3"
    assert fan is not None and fan.state == "6"
    assert primary is not None
    assert primary.state == "On · Flame 1"
    assert primary.attributes["pending_state"] == "On · Flame 3 · Fan 6"
    assert primary.attributes["operational_status"] == "pending"

    await asyncio.wait_for(send_started.wait(), timeout=1)
    await asyncio.sleep(0)

    flame = hass.states.get(_number_entity_id(entry.title, "Flame"))
    fan = hass.states.get(_number_entity_id(entry.title, "Fan"))
    primary = hass.states.get(_sensor_entity_id(entry.title))
    assert flame is not None and flame.state == "3"
    assert fan is not None and fan.state == "6"
    assert primary is not None
    assert primary.state == "On · Flame 1"
    assert primary.attributes["pending_state"] == "On · Flame 3 · Fan 6"
    assert primary.attributes["operational_status"] == "sending"

    allow_send_finish.set()
    await asyncio.sleep(0.03)
    await hass.async_block_till_done()

    flame = hass.states.get(_number_entity_id(entry.title, "Flame"))
    fan = hass.states.get(_number_entity_id(entry.title, "Fan"))
    primary = hass.states.get(_sensor_entity_id(entry.title))
    assert flame is not None and flame.state == "3"
    assert fan is not None and fan.state == "6"
    assert primary is not None
    assert primary.state == "On · Flame 3 · Fan 6"
    assert primary.attributes["pending_state"] is None
    assert primary.attributes["operational_status"] == "ready"


async def test_flame_control_forces_power_on(hass) -> None:
    """Setting flame while off should create an ON desired state."""

    hass.data.setdefault(DOMAIN, {})[DATA_CONTROL_DEBOUNCE_SECONDS] = 0.01
    hass.data.setdefault(DOMAIN, {})[DATA_CONFIRMATION_WINDOW_SECONDS] = 0.0

    entry = _add_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    runtime_entry = async_get_runtime_entries(hass)[entry.entry_id]
    backend = runtime_entry.backend
    assert isinstance(backend, FakeRFBackend)

    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": _switch_entity_id(entry.title, "Power")},
        blocking=True,
    )
    await asyncio.sleep(0.03)
    await hass.async_block_till_done()

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": _number_entity_id(entry.title, "Flame"), "value": 3},
        blocking=True,
    )
    await asyncio.sleep(0.03)
    await hass.async_block_till_done()

    assert backend.sent_packets[-1].state.power is True
    assert backend.sent_packets[-1].state.flame == 3


async def test_number_controls_normalize_integer_like_input_and_reject_fractional_values(
    hass,
) -> None:
    """Whole-number values should stage as ints, while fractional levels are rejected."""

    hass.data.setdefault(DOMAIN, {})[DATA_CONTROL_DEBOUNCE_SECONDS] = 0.01
    hass.data.setdefault(DOMAIN, {})[DATA_CONFIRMATION_WINDOW_SECONDS] = 0.0

    entry = _add_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    runtime_entry = async_get_runtime_entries(hass)[entry.entry_id]

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": _number_entity_id(entry.title, "Flame"), "value": 6.0},
        blocking=True,
    )

    assert runtime_entry.desired_state is not None
    assert runtime_entry.desired_state.flame == 6
    assert isinstance(runtime_entry.desired_state.flame, int)

    with pytest.raises(HomeAssistantError, match="Flame must be set to a whole-number level."):
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": _number_entity_id(entry.title, "Flame"), "value": 2.5},
            blocking=True,
        )

    assert runtime_entry.desired_state is not None
    assert runtime_entry.desired_state.flame == 6


async def test_power_off_preserves_existing_feature_values_in_controls_and_packet(
    hass,
) -> None:
    """Power OFF should preserve current feature values instead of resetting them."""

    hass.data.setdefault(DOMAIN, {})[DATA_CONTROL_DEBOUNCE_SECONDS] = 0.01
    hass.data.setdefault(DOMAIN, {})[DATA_CONFIRMATION_WINDOW_SECONDS] = 0.0

    entry = _add_entry(
        hass,
        options={
            CONF_FAN: True,
            CONF_LIGHT: True,
            CONF_FRONT: True,
            CONF_AUX: False,
            CONF_CPI: False,
        },
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    runtime_entry = async_get_runtime_entries(hass)[entry.entry_id]
    backend = runtime_entry.backend
    assert isinstance(backend, FakeRFBackend)
    await async_set_runtime_current_state(
        hass,
        runtime_entry,
        FireplaceState(
            power=True,
            flame=5,
            fan=3,
            light=2,
            front=True,
        ),
        source="observed_packet",
        confidence=STATE_CONFIDENCE_OBSERVED,
    )
    backend.sent_packets.clear()

    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": _switch_entity_id(entry.title, "Power")},
        blocking=True,
    )

    primary = hass.states.get(_sensor_entity_id(entry.title))
    flame = hass.states.get(_number_entity_id(entry.title, "Flame"))
    fan = hass.states.get(_number_entity_id(entry.title, "Fan"))
    light = hass.states.get(_number_entity_id(entry.title, "Light"))
    front = hass.states.get(_switch_entity_id(entry.title, "Front Burner"))
    power = hass.states.get(_switch_entity_id(entry.title, "Power"))
    assert primary is not None
    assert primary.state == "On · Flame 5 · Fan 3 · Light 2 · Front On"
    assert primary.attributes["pending_state"] == "Off"
    assert power is not None and power.state == "off"
    assert flame is not None and flame.state == "5"
    assert fan is not None and fan.state == "3"
    assert light is not None and light.state == "2"
    assert front is not None and front.state == "on"

    await asyncio.sleep(0.03)
    await hass.async_block_till_done()

    packet = backend.sent_packets[-1]
    assert packet.state.power is False
    assert packet.state.flame == 5
    assert packet.state.fan == 3
    assert packet.state.light == 2
    assert packet.state.front is True

    primary = hass.states.get(_sensor_entity_id(entry.title))
    flame = hass.states.get(_number_entity_id(entry.title, "Flame"))
    fan = hass.states.get(_number_entity_id(entry.title, "Fan"))
    light = hass.states.get(_number_entity_id(entry.title, "Light"))
    front = hass.states.get(_switch_entity_id(entry.title, "Front Burner"))
    power = hass.states.get(_switch_entity_id(entry.title, "Power"))
    assert primary is not None
    assert primary.state == "Off"
    assert primary.attributes["power"] == "Off"
    assert primary.attributes["flame"] == "Level 5"
    assert primary.attributes["fan"] == "Level 3"
    assert primary.attributes["light"] == "Level 2"
    assert primary.attributes["front_burner"] == "On"
    assert primary.attributes["pending_state"] is None
    assert power is not None and power.state == "off"
    assert flame is not None and flame.state == "5"
    assert fan is not None and fan.state == "3"
    assert light is not None and light.state == "2"
    assert front is not None and front.state == "on"


async def test_power_on_after_power_off_preserves_prior_feature_values(hass) -> None:
    """Turning power back on should reuse the preserved off-state values."""

    hass.data.setdefault(DOMAIN, {})[DATA_CONTROL_DEBOUNCE_SECONDS] = 0.01
    hass.data.setdefault(DOMAIN, {})[DATA_CONFIRMATION_WINDOW_SECONDS] = 0.0

    entry = _add_entry(
        hass,
        options={
            CONF_FAN: True,
            CONF_LIGHT: True,
            CONF_FRONT: True,
            CONF_AUX: False,
            CONF_CPI: False,
        },
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    runtime_entry = async_get_runtime_entries(hass)[entry.entry_id]
    backend = runtime_entry.backend
    assert isinstance(backend, FakeRFBackend)
    await async_set_runtime_current_state(
        hass,
        runtime_entry,
        FireplaceState(
            power=False,
            flame=5,
            fan=3,
            light=2,
            front=True,
        ),
        source="observed_packet",
        confidence=STATE_CONFIDENCE_OBSERVED,
        packet=ProflamePacket.from_frame(
            ProflameFrame(
                serial_id=0x3B3F02,
                cmd1=0x20,
                err1=0x00,
                cmd2=0xB5,
                err2=0x00,
            ),
            source="observed_packet",
        ),
    )
    backend.sent_packets.clear()

    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": _switch_entity_id(entry.title, "Power")},
        blocking=True,
    )
    await asyncio.sleep(0.03)
    await hass.async_block_till_done()

    packet = backend.sent_packets[-1]
    assert packet.state.power is True
    assert packet.state.flame == 5
    assert packet.state.fan == 3
    assert packet.state.light == 2
    assert packet.state.front is True


async def test_tx_failure_rolls_back_pending_controls(hass, monkeypatch) -> None:
    """A failed debounced TX should clear pending state and revert controls."""

    hass.data.setdefault(DOMAIN, {})[DATA_CONTROL_DEBOUNCE_SECONDS] = 0.01
    hass.data.setdefault(DOMAIN, {})[DATA_CONFIRMATION_WINDOW_SECONDS] = 0.0

    entry = _add_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    runtime_entry = async_get_runtime_entries(hass)[entry.entry_id]

    async def fake_send(self, packet):
        raise RuntimeError("boom")

    monkeypatch.setattr(FakeRFBackend, "send", fake_send)

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": _number_entity_id(entry.title, "Flame"), "value": 4},
        blocking=True,
    )
    await asyncio.sleep(0.03)
    await hass.async_block_till_done()

    primary = hass.states.get(_sensor_entity_id(entry.title))
    assert primary is not None
    assert primary.state == "On · Flame 1"
    assert primary.attributes["operational_status"] == "failed"
    assert primary.attributes["pending_state"] is None
    assert (
        primary.attributes["last_issue"]
        == "Transmit failed because boom; controls reverted to last known state."
    )
    assert runtime_entry.desired_state is None


async def test_switch_turn_on_schedules_one_debounced_send_and_stays_on(hass) -> None:
    """Turning power on through the switch should debounce once and remain on after send."""

    hass.data.setdefault(DOMAIN, {})[DATA_CONTROL_DEBOUNCE_SECONDS] = 0.01
    hass.data.setdefault(DOMAIN, {})[DATA_CONFIRMATION_WINDOW_SECONDS] = 0.0

    entry = _add_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    runtime_entry = async_get_runtime_entries(hass)[entry.entry_id]
    backend = runtime_entry.backend
    assert isinstance(backend, FakeRFBackend)

    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": _switch_entity_id(entry.title, "Power")},
        blocking=True,
    )
    await asyncio.sleep(0.03)
    await hass.async_block_till_done()

    backend.sent_packets.clear()

    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": _switch_entity_id(entry.title, "Power")},
        blocking=True,
    )

    assert runtime_entry.debounce_task is not None
    power_state = hass.states.get(_switch_entity_id(entry.title, "Power"))
    assert power_state is not None
    assert power_state.state == "on"

    await asyncio.sleep(0.03)
    await hass.async_block_till_done()

    assert len(backend.sent_packets) == 1
    assert backend.sent_packets[0].state.power is True
    power_state = hass.states.get(_switch_entity_id(entry.title, "Power"))
    assert power_state is not None
    assert power_state.state == "on"
    primary = hass.states.get(_sensor_entity_id(entry.title))
    assert primary is not None
    assert primary.attributes["operational_status"] == "ready"
    assert primary.attributes["state_confidence"] == STATE_CONFIDENCE_REQUESTED


async def test_successful_learning_bootstrap_creates_known_observed_state(hass) -> None:
    """A learned entry should start from its final observed packet, not unknown."""

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Living Room Fireplace",
        data={
            "name": "Living Room Fireplace",
            CONF_BACKEND_TYPE: BACKEND_YARDSTICK,
            CONF_REMOTE_ID: 0x3B3F02,
            CONF_C1: 5,
            CONF_D1: 7,
            CONF_C2: 1,
            CONF_D2: 8,
            CONF_INITIAL_FRAME: {
                "serial_id": 0x3B3F02,
                "cmd1": 0x01,
                "err1": 0x76,
                "cmd2": 0x05,
                "err2": 0xBD,
            },
            CONF_INITIAL_PACKET_SOURCE: "observed_packet",
        },
        options={
            CONF_FAN: True,
            CONF_LIGHT: True,
            CONF_FRONT: False,
            CONF_AUX: False,
            CONF_CPI: False,
        },
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    runtime_entry = async_get_runtime_entries(hass)[entry.entry_id]
    primary = hass.states.get(_sensor_entity_id(entry.title))
    assert primary is not None
    assert primary.state == "On · Flame 5"
    assert primary.attributes["state_confidence"] == STATE_CONFIDENCE_OBSERVED
    assert primary.attributes["operational_status"] == "ready"
    assert runtime_entry.last_packet is not None
    assert runtime_entry.last_packet.state.flame == 5


async def test_debounced_control_with_unknown_current_state_fails_visibly(hass) -> None:
    """Controls should fail loudly instead of staging from an unknown state."""

    entry = _add_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    runtime_entry = async_get_runtime_entries(hass)[entry.entry_id]
    backend = runtime_entry.backend
    assert isinstance(backend, FakeRFBackend)
    runtime_entry.last_packet = None

    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": _switch_entity_id(entry.title, "Power")},
        blocking=True,
    )
    await hass.async_block_till_done()

    primary = hass.states.get(_sensor_entity_id(entry.title))
    assert primary is not None
    assert primary.attributes["operational_status"] == "failed"
    assert primary.attributes["last_issue"] == "Cannot stage fireplace control because current state is unknown."
    assert primary.attributes["pending_state"] is None
    assert runtime_entry.desired_state is None
    assert len(backend.sent_packets) == 0


async def test_existing_entry_without_persisted_state_initializes_safe_off_and_controls_work(
    hass,
) -> None:
    """Older learned entries without runtime state should fall back to safe OFF."""

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Legacy Fireplace",
        data={
            "name": "Legacy Fireplace",
            CONF_BACKEND_TYPE: BACKEND_YARDSTICK,
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
        },
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    primary = hass.states.get(_sensor_entity_id(entry.title))
    assert primary is not None
    assert primary.state == "Off"
    assert primary.attributes["state_confidence"] == STATE_CONFIDENCE_RESTORED
    assert (
        primary.attributes["last_issue"]
        == "State initialized from safe defaults; no observed fireplace state was available."
    )


async def test_flame_change_from_safe_off_fallback_forces_power_on_and_sends(hass, monkeypatch) -> None:
    """Fallback OFF state should still allow normal staged control sends."""

    hass.data.setdefault(DOMAIN, {})[DATA_CONTROL_DEBOUNCE_SECONDS] = 0.01
    hass.data.setdefault(DOMAIN, {})[DATA_CONFIRMATION_WINDOW_SECONDS] = 0.0

    sent_packets = []

    async def fake_send(self, packet):
        sent_packets.append(packet)
        from custom_components.proflame2.rf.base import SendResult

        return SendResult(packet=packet, backend_name="yardstick")

    monkeypatch.setattr("custom_components.proflame2.rf.yardstick.YardStickBackend.send", fake_send)

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Legacy Fireplace",
        data={
            "name": "Legacy Fireplace",
            CONF_BACKEND_TYPE: BACKEND_YARDSTICK,
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
        },
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": _number_entity_id(entry.title, "Flame"), "value": 6},
        blocking=True,
    )
    await asyncio.sleep(0.03)
    await hass.async_block_till_done()

    assert len(sent_packets) == 1
    assert sent_packets[0].state.power is True
    assert sent_packets[0].state.flame == 6


async def test_debounce_task_exception_is_logged_and_surfaces_last_issue(
    hass, monkeypatch, caplog
) -> None:
    """Unexpected debounce-task failures should be logged and reflected in runtime state."""

    hass.data.setdefault(DOMAIN, {})[DATA_CONTROL_DEBOUNCE_SECONDS] = 0.01
    hass.data.setdefault(DOMAIN, {})[DATA_CONFIRMATION_WINDOW_SECONDS] = 0.0

    entry = _add_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    runtime_entry = async_get_runtime_entries(hass)[entry.entry_id]

    async def fake_execute(*args, **kwargs):
        raise ValueError("explode")

    monkeypatch.setattr(
        "custom_components.proflame2.services._async_execute_requested_state",
        fake_execute,
    )
    caplog.set_level(logging.INFO, logger="custom_components.proflame2.services")

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": _number_entity_id(entry.title, "Flame"), "value": 4},
        blocking=True,
    )
    await asyncio.sleep(0.03)
    await hass.async_block_till_done()

    primary = hass.states.get(_sensor_entity_id(entry.title))
    assert primary is not None
    assert primary.attributes["operational_status"] == "failed"
    assert primary.attributes["pending_state"] is None
    assert primary.attributes["last_issue"] == "ValueError: explode."
    assert runtime_entry.desired_state is None
    assert "debounce task failed" in caplog.text
    assert "Traceback" in caplog.text
    assert "debounced send terminal result=exception" in caplog.text


async def test_debounced_send_logs_terminal_success(hass, caplog) -> None:
    """A successful debounced send should always emit a terminal success log."""

    hass.data.setdefault(DOMAIN, {})[DATA_CONTROL_DEBOUNCE_SECONDS] = 0.01
    hass.data.setdefault(DOMAIN, {})[DATA_CONFIRMATION_WINDOW_SECONDS] = 0.0

    entry = _add_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)

    caplog.set_level(logging.WARNING, logger="custom_components.proflame2.services")

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": _number_entity_id(entry.title, "Flame"), "value": 4},
        blocking=True,
    )
    await asyncio.sleep(0.03)
    await hass.async_block_till_done()

    assert "packet build start" in caplog.text
    assert "backend send start" in caplog.text
    assert "send execution succeeded" in caplog.text
    assert "debounced send terminal result=succeeded" in caplog.text


async def test_restarting_pre_send_debounce_logs_timer_cancel_without_terminal_cancel(
    hass, caplog
) -> None:
    """Restarting the debounce window should not look like a cancelled send."""

    hass.data.setdefault(DOMAIN, {})[DATA_CONTROL_DEBOUNCE_SECONDS] = 0.05
    hass.data.setdefault(DOMAIN, {})[DATA_CONFIRMATION_WINDOW_SECONDS] = 0.0

    entry = _add_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)

    caplog.set_level(logging.WARNING, logger="custom_components.proflame2.services")

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": _number_entity_id(entry.title, "Flame"), "value": 4},
        blocking=True,
    )
    await asyncio.sleep(0.01)
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": _number_entity_id(entry.title, "Fan"), "value": 2},
        blocking=True,
    )
    await asyncio.sleep(0.08)
    await hass.async_block_till_done()

    assert "debounce_timer_cancelled reason=restart_debounce_after_new_user_edit" in caplog.text
    assert "debounced send terminal result=cancelled" not in caplog.text


async def test_power_off_from_on_state_transmits_without_cancellation(hass, caplog) -> None:
    """Once debounce fires, the active send must not cancel itself before backend TX."""

    hass.data.setdefault(DOMAIN, {})[DATA_CONTROL_DEBOUNCE_SECONDS] = 0.01
    hass.data.setdefault(DOMAIN, {})[DATA_CONFIRMATION_WINDOW_SECONDS] = 0.0

    entry = _add_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    runtime_entry = async_get_runtime_entries(hass)[entry.entry_id]
    backend = runtime_entry.backend
    assert isinstance(backend, FakeRFBackend)

    caplog.set_level(logging.WARNING, logger="custom_components.proflame2.services")

    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": _switch_entity_id(entry.title, "Power")},
        blocking=True,
    )
    await asyncio.sleep(0.03)
    await hass.async_block_till_done()

    assert len(backend.sent_packets) == 1
    assert backend.sent_packets[0].state.power is False
    primary = hass.states.get(_sensor_entity_id(entry.title))
    assert primary is not None
    assert primary.state == "Off"
    assert "backend send start" in caplog.text
    assert "debounced send terminal result=succeeded" in caplog.text
    assert "send execution cancelled" not in caplog.text


async def test_debounced_send_timeout_fails_visibly(hass, monkeypatch) -> None:
    """A hung backend send should end in a visible timeout, not a silent rollback."""

    hass.data.setdefault(DOMAIN, {})[DATA_CONTROL_DEBOUNCE_SECONDS] = 0.01
    hass.data.setdefault(DOMAIN, {})[DATA_CONFIRMATION_WINDOW_SECONDS] = 0.0
    monkeypatch.setattr("custom_components.proflame2.services.BACKEND_SEND_TIMEOUT_SECONDS", 0.01)

    async def fake_send(self, packet):
        await asyncio.sleep(1)

    monkeypatch.setattr("custom_components.proflame2.rf.yardstick.YardStickBackend.send", fake_send)

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Living Room Fireplace",
        data={
            "name": "Living Room Fireplace",
            CONF_BACKEND_TYPE: BACKEND_YARDSTICK,
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
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": _number_entity_id(entry.title, "Flame"), "value": 6},
        blocking=True,
    )
    await asyncio.sleep(0.05)
    await hass.async_block_till_done()

    primary = hass.states.get(_sensor_entity_id(entry.title))
    assert primary is not None
    assert primary.attributes["operational_status"] == "failed"
    assert (
        primary.attributes["last_issue"]
        == "Transmit timed out after 0 seconds; controls reverted to last known state."
    )


async def test_send_failure_logs_backend_cause_without_packet_debug(hass, monkeypatch, caplog) -> None:
    """Normal HA logs should show the backend/controller failure cause without packet debug."""

    hass.data.setdefault(DOMAIN, {})[DATA_CONTROL_DEBOUNCE_SECONDS] = 0.01
    hass.data.setdefault(DOMAIN, {})[DATA_CONFIRMATION_WINDOW_SECONDS] = 0.0

    entry = _add_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)

    async def fake_send(self, packet):
        raise RuntimeError("controller unavailable")

    monkeypatch.setattr(FakeRFBackend, "send", fake_send)
    caplog.set_level(logging.ERROR, logger="custom_components.proflame2.services")

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": _number_entity_id(entry.title, "Flame"), "value": 4},
        blocking=True,
    )
    await asyncio.sleep(0.03)
    await hass.async_block_till_done()

    assert "send execution failed" in caplog.text
    assert "RuntimeError: controller unavailable" in caplog.text


async def test_debug_logging_enables_packet_debug_for_control_send_path(
    hass, monkeypatch
) -> None:
    """The config-entry debug flag should activate packet debug outside learning too."""

    hass.data.setdefault(DOMAIN, {})[DATA_CONTROL_DEBOUNCE_SECONDS] = 0.01
    hass.data.setdefault(DOMAIN, {})[DATA_CONFIRMATION_WINDOW_SECONDS] = 0.0

    enable_calls: list[str] = []
    packet_logs: list[str] = []

    async def fake_enable_packet_debug_logging(_hass):
        enable_calls.append("enabled")
        return PacketDebugLogPaths(
            primary_log_path=Path("/config/proflame2_debug.log"),
            decode_failure_log_path=Path("/config/proflame2_decode_failures.log"),
        )

    class FakePacketLogger:
        def log(self, level, message, *args):
            packet_logs.append(message % args if args else message)

        def exception(self, message, *args):
            packet_logs.append(message % args if args else message)

    monkeypatch.setattr(
        "custom_components.proflame2.runtime.async_enable_packet_debug_logging",
        fake_enable_packet_debug_logging,
    )
    monkeypatch.setattr(
        "custom_components.proflame2.services.get_packet_debug_logger",
        lambda: FakePacketLogger(),
    )

    entry = _add_entry(
        hass,
        options={
            CONF_FAN: True,
            CONF_LIGHT: True,
            CONF_FRONT: False,
            CONF_AUX: False,
            CONF_CPI: False,
            CONF_DEBUG_LOGGING: True,
        },
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert enable_calls == ["enabled"]

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": _number_entity_id(entry.title, "Flame"), "value": 2},
        blocking=True,
    )
    await asyncio.sleep(0.03)
    await hass.async_block_till_done()

    assert any("desired state staged" in message for message in packet_logs)
    assert any("debounce task fired" in message for message in packet_logs)
    assert any("send execution started" in message for message in packet_logs)
    assert any("send execution succeeded" in message for message in packet_logs)


async def test_post_tx_confirmation_updates_state_confidence_to_observed(hass) -> None:
    """A valid queued echo should flip state_confidence from requested to observed."""

    hass.data.setdefault(DOMAIN, {})[DATA_CONTROL_DEBOUNCE_SECONDS] = 0.01
    hass.data.setdefault(DOMAIN, {})[DATA_CONFIRMATION_WINDOW_SECONDS] = 0.05
    hass.data.setdefault(DOMAIN, {})[DATA_CONFIRMATION_RECEIVE_TIMEOUT_SECONDS] = 0.01

    entry = _add_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    runtime_entry = async_get_runtime_entries(hass)[entry.entry_id]
    backend = runtime_entry.backend
    assert isinstance(backend, FakeRFBackend)
    backend.queue_packets(
        ProflamePacket.from_frame(
            ProflameFrame(
                serial_id=0x3B3F02,
                cmd1=0x01,
                err1=0x76,
                cmd2=0x02,
                err2=0x6A,
            ),
            source="observed_packet",
        )
    )

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": _number_entity_id(entry.title, "Flame"), "value": 2},
        blocking=True,
    )
    await asyncio.sleep(0.03)
    await hass.async_block_till_done()

    primary = hass.states.get(_sensor_entity_id(entry.title))
    assert primary is not None
    assert primary.attributes["state_confidence"] == STATE_CONFIDENCE_OBSERVED
    assert primary.attributes["operational_status"] == "ready"


async def test_active_listening_updates_current_state_when_forced_in_tests(hass) -> None:
    """The hidden active-listening mode should update current state from observed packets."""

    hass.data.setdefault(DOMAIN, {})[DATA_ACTIVE_LISTENING] = True
    hass.data.setdefault(DOMAIN, {})[DATA_CONFIRMATION_RECEIVE_TIMEOUT_SECONDS] = 0.01

    entry = _add_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    runtime_entry = async_get_runtime_entries(hass)[entry.entry_id]
    backend = runtime_entry.backend
    assert isinstance(backend, FakeRFBackend)

    backend.queue_packets(
        ProflamePacket.from_frame(
            ProflameFrame(
                serial_id=0x3B3F02,
                cmd1=0x01,
                err1=0x76,
                cmd2=0x03,
                err2=0x8B,
            ),
            source="observed_packet",
        )
    )
    await asyncio.sleep(0.03)
    await hass.async_block_till_done()

    primary = hass.states.get(_sensor_entity_id(entry.title))
    assert primary is not None
    assert primary.state == "On · Flame 3"
    assert primary.attributes["state_confidence"] == STATE_CONFIDENCE_OBSERVED


async def test_control_actions_fail_quickly_when_runtime_entry_is_shutting_down(hass) -> None:
    """Controls should not stage or send new work once shutdown has started."""

    entry = _add_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    runtime_entry = async_get_runtime_entries(hass)[entry.entry_id]
    runtime_entry.shutting_down = True
    runtime_entry.shutdown_reason = "config_entry_unload"

    with pytest.raises(HomeAssistantError, match="shutting down"):
        await hass.services.async_call(
            "switch",
            "turn_on",
            {"entity_id": _switch_entity_id(entry.title, "Power")},
            blocking=True,
        )


async def test_homeassistant_stop_closes_backend_and_marks_shutdown(hass, monkeypatch) -> None:
    """Home Assistant stop should shut down runtime backends without unloading entries first."""

    entry = _add_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    runtime_entry = async_get_runtime_entries(hass)[entry.entry_id]
    close_reasons: list[str | None] = []

    async def fake_close(*, reason: str | None = None) -> None:
        close_reasons.append(reason)

    monkeypatch.setattr(runtime_entry.backend, "close", fake_close)

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()

    assert runtime_entry.shutting_down is True
    assert runtime_entry.shutdown_reason == "ha_shutdown"
    assert close_reasons == ["ha_shutdown"]


async def test_startup_restores_last_known_state_without_transmitting(hass) -> None:
    """Persisted runtime state should restore on startup with restored confidence."""

    entry = _add_entry(hass)
    await _runtime_store(hass).async_save(
        {
            entry.entry_id: {
                "state": {
                    "power": True,
                    "flame": 4,
                    "fan": 1,
                    "light": 0,
                    "front": False,
                    "aux": False,
                    "thermostat": False,
                    "cpi": False,
                },
                "state_confidence": STATE_CONFIDENCE_RESTORED,
                "source": "restored_state",
            }
        }
    )

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    runtime_entry = async_get_runtime_entries(hass)[entry.entry_id]
    backend = runtime_entry.backend
    assert isinstance(backend, FakeRFBackend)
    assert len(backend.sent_packets) == 0

    primary = hass.states.get(_sensor_entity_id(entry.title))
    assert primary is not None
    assert primary.state == "On · Flame 4 · Fan 1"
    assert primary.attributes["state_confidence"] == STATE_CONFIDENCE_RESTORED
    assert primary.attributes["operational_status"] == "ready"
