"""Home Assistant config flow tests for Proflame2."""

from __future__ import annotations

import asyncio
from collections import deque
from pathlib import Path

import pytest
import voluptuous_serialize

homeassistant = pytest.importorskip("homeassistant")
pytest.importorskip("pytest_homeassistant_custom_component")

pytestmark = pytest.mark.ha

from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import config_validation as cv
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.proflame2.config_flow import LILYGO_ESPHOME_LINK_HELP
from custom_components.proflame2.const import (
    BACKEND_ESPHOME,
    BACKEND_YARDSTICK,
    CONF_ACTIVE_LISTENING,
    CONF_AUX,
    CONF_BACKEND_TYPE,
    CONF_C1,
    CONF_C2,
    CONF_CPI,
    CONF_D1,
    CONF_D2,
    CONF_DEBUG_LOGGING,
    CONF_ESPHOME_ENTRY_ID,
    CONF_FAN,
    CONF_FIREPLACE_SHORT_NAME,
    CONF_FLAME,
    CONF_FRONT,
    CONF_LIGHT,
    CONF_NAME,
    CONF_POWER,
    CONF_PROFILE_ID,
    CONF_PROFILES,
    CONF_REMOTE_ID,
    DATA_FAKE_LEARNING_DELAY,
    DATA_LEARNING_BACKEND_FACTORY,
    DATA_LEARNING_RECEIVE_TIMEOUT,
    DATA_LEARNING_TIMEOUT,
    DOMAIN,
)
from custom_components.proflame2.packet_debug import PacketDebugLogPaths
from custom_components.proflame2.protocol.packet import ProflameFrame, ProflamePacket
from custom_components.proflame2.rf.fake import FakeRFBackend
from custom_components.proflame2.rf.yardstick import YardStickBackendUnavailableError
from custom_components.proflame2.version import ENABLE_FAKE_BACKEND_ENV


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading integrations from the local custom_components directory."""

    yield


def _packet(
    *,
    remote_id: int,
    cmd1: int,
    err1: int,
    cmd2: int,
    err2: int,
) -> ProflamePacket:
    return ProflamePacket.from_frame(
        ProflameFrame(
            serial_id=remote_id,
            cmd1=cmd1,
            err1=err1,
            cmd2=cmd2,
            err2=err2,
        ),
        source="fake",
    )


class DelayedFakeRFBackend(FakeRFBackend):
    """Fake backend that delays each queued receive result for timing tests."""

    def __init__(self, delays: list[float]):
        super().__init__()
        self._delays = delays

    async def receive(self, timeout: float | None = None) -> ProflamePacket | None:
        if self._delays:
            await asyncio.sleep(self._delays.pop(0))
        return await super().receive(timeout)


def _backend_factory(*backends: FakeRFBackend):
    queued = deque(backends)

    def factory(backend_type: str) -> FakeRFBackend:
        assert backend_type == "fake"
        return queued.popleft()

    return factory


def _enable_fake_backend(monkeypatch) -> None:
    """Opt into Fake for tests that intentionally exercise simulated hardware."""

    monkeypatch.setenv(ENABLE_FAKE_BACKEND_ENV, "true")


async def _advance_guided_learning(
    hass,
    flow_id: str,
    result: dict | None = None,
):
    """Advance guided learning until it reaches a non-progress step."""

    current = result or await hass.config_entries.flow.async_configure(flow_id)

    for _ in range(100):
        if current["type"] is FlowResultType.SHOW_PROGRESS:
            await asyncio.sleep(0.01)
            current = await hass.config_entries.flow.async_configure(flow_id)
            continue
        if current["type"] is FlowResultType.SHOW_PROGRESS_DONE:
            current = await hass.config_entries.flow.async_configure(flow_id)
            continue
        return current

    raise AssertionError("Guided learning did not reach a terminal flow step in time during test.")


async def test_config_flow_creates_entry_with_normalized_profile_data(hass) -> None:
    """User flow should store permanent identity in config entry data."""

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data={
            "name": "Living Room Fireplace",
            CONF_BACKEND_TYPE: "yardstick",
            CONF_REMOTE_ID: "3b3f02",
            CONF_C1: "5",
            CONF_D1: "7",
            CONF_C2: "1",
            CONF_D2: "8",
            CONF_FAN: True,
            CONF_LIGHT: True,
            CONF_FRONT: False,
            CONF_AUX: False,
            CONF_CPI: False,
            CONF_DEBUG_LOGGING: False,
            CONF_FIREPLACE_SHORT_NAME: "---",
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Living Room Fireplace"
    assert result["data"][CONF_REMOTE_ID] == 0x3B3F02
    assert result["data"][CONF_C1] == 5
    assert result["data"][CONF_D1] == 7
    assert result["data"][CONF_C2] == 1
    assert result["data"][CONF_D2] == 8
    assert result["options"] == {
        CONF_FAN: True,
        CONF_LIGHT: True,
        CONF_FRONT: False,
        CONF_AUX: False,
        CONF_CPI: False,
        CONF_DEBUG_LOGGING: False,
        CONF_ACTIVE_LISTENING: False,
        CONF_FIREPLACE_SHORT_NAME: "---",
        CONF_PROFILES: {},
    }


async def test_invalid_remote_id_is_rejected(hass) -> None:
    """Remote IDs should be validated before entry creation."""

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data={
            "name": "Living Room Fireplace",
            CONF_BACKEND_TYPE: "yardstick",
            CONF_REMOTE_ID: "zzzzzz",
            CONF_C1: "5",
            CONF_D1: "7",
            CONF_C2: "1",
            CONF_D2: "8",
            CONF_FAN: True,
            CONF_LIGHT: True,
            CONF_FRONT: False,
            CONF_AUX: False,
            CONF_CPI: False,
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_REMOTE_ID: "invalid_remote_id"}


async def test_invalid_cd_value_is_rejected(hass) -> None:
    """C/D values should be constrained to one nibble."""

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data={
            "name": "Living Room Fireplace",
            CONF_BACKEND_TYPE: "yardstick",
            CONF_REMOTE_ID: "3b3f02",
            CONF_C1: "16",
            CONF_D1: "7",
            CONF_C2: "1",
            CONF_D2: "8",
            CONF_FAN: True,
            CONF_LIGHT: True,
            CONF_FRONT: False,
            CONF_AUX: False,
            CONF_CPI: False,
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_C1: "invalid_nibble"}


async def test_manual_entry_form_schema_is_ui_serializable(hass) -> None:
    """The manual-entry form schema should serialize for the frontend."""

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )
    assert result["type"] is FlowResultType.MENU

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "manual"},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manual"
    serialized = voluptuous_serialize.convert(
        result["data_schema"],
        custom_serializer=cv.custom_serializer,
    )
    assert serialized


async def test_manual_entry_form_exposes_only_hardware_backends_by_default(hass) -> None:
    """Manual setup should not expose the Fake backend by default."""

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "manual"},
    )

    serialized = voluptuous_serialize.convert(
        result["data_schema"],
        custom_serializer=cv.custom_serializer,
    )
    backend_field = next(field for field in serialized if field["name"] == CONF_BACKEND_TYPE)
    option_values = {option["value"] for option in backend_field["selector"]["select"]["options"]}
    assert option_values == {BACKEND_YARDSTICK, BACKEND_ESPHOME}
    assert not any(field["name"] == CONF_ESPHOME_ENTRY_ID for field in serialized)
    assert not any(field["name"] == CONF_DEBUG_LOGGING for field in serialized)
    assert not any(field["name"] == CONF_ACTIVE_LISTENING for field in serialized)


async def test_learning_form_includes_only_hardware_backends_by_default(hass) -> None:
    """Guided learning should offer hardware RX backends and hide Fake by default."""

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "learn"},
    )

    serialized = voluptuous_serialize.convert(
        result["data_schema"],
        custom_serializer=cv.custom_serializer,
    )
    backend_field = next(field for field in serialized if field["name"] == CONF_BACKEND_TYPE)
    option_values = {option["value"] for option in backend_field["selector"]["select"]["options"]}
    assert option_values == {BACKEND_YARDSTICK, BACKEND_ESPHOME}
    assert not any(field["name"] == CONF_ESPHOME_ENTRY_ID for field in serialized)
    assert not any(field["name"] == CONF_DEBUG_LOGGING for field in serialized)


async def test_learning_esphome_entry_requires_linked_esphome_config_entry(hass) -> None:
    """LilyGO guided learning must know which ESPHome device supplies FIFO RX."""

    linked_entry = MockConfigEntry(
        domain="esphome",
        title="LilyGO Controller",
        data={"host": "192.0.2.10", "port": 6053},
    )
    linked_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "learn"},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_NAME: "Living Room Fireplace",
            CONF_FIREPLACE_SHORT_NAME: "LR",
            CONF_BACKEND_TYPE: BACKEND_ESPHOME,
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "learn_esphome"
    assert result["description_placeholders"] == {"setup_text": LILYGO_ESPHOME_LINK_HELP}
    serialized = voluptuous_serialize.convert(
        result["data_schema"],
        custom_serializer=cv.custom_serializer,
    )
    assert [field["name"] for field in serialized] == [CONF_ESPHOME_ENTRY_ID]
    esphome_field = serialized[0]
    assert esphome_field["selector"]["select"]["options"] == [
        {"value": linked_entry.entry_id, "label": "LilyGO Controller"}
    ]


async def test_manual_esphome_entry_requires_linked_esphome_config_entry(hass) -> None:
    """Manual ESPHome setup must collect a linked ESPHome config entry id."""

    linked_entry = MockConfigEntry(
        domain="esphome",
        title="LilyGO Controller",
        data={"host": "192.0.2.10", "port": 6053},
    )
    linked_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data={
            "name": "Living Room Fireplace",
            CONF_BACKEND_TYPE: BACKEND_ESPHOME,
            CONF_REMOTE_ID: "3b3f02",
            CONF_C1: "5",
            CONF_D1: "7",
            CONF_C2: "1",
            CONF_D2: "8",
            CONF_FAN: True,
            CONF_LIGHT: True,
            CONF_FRONT: False,
            CONF_AUX: False,
            CONF_CPI: False,
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manual_esphome"
    assert result["description_placeholders"] == {"setup_text": LILYGO_ESPHOME_LINK_HELP}
    serialized = voluptuous_serialize.convert(
        result["data_schema"],
        custom_serializer=cv.custom_serializer,
    )
    assert [field["name"] for field in serialized] == [CONF_ESPHOME_ENTRY_ID]
    esphome_field = serialized[0]
    assert esphome_field["selector"]["select"]["options"] == [
        {"value": linked_entry.entry_id, "label": "LilyGO Controller"}
    ]


async def test_manual_esphome_entry_can_create_entry_with_linked_device(hass) -> None:
    """Manual ESPHome setup should store the linked ESPHome config entry id."""

    linked_entry = MockConfigEntry(
        domain="esphome",
        title="T-Embed",
        data={"host": "192.0.2.10", "port": 6053},
    )
    linked_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data={
            "name": "Living Room Fireplace",
            CONF_BACKEND_TYPE: BACKEND_ESPHOME,
            CONF_ESPHOME_ENTRY_ID: linked_entry.entry_id,
            CONF_REMOTE_ID: "3b3f02",
            CONF_C1: "5",
            CONF_D1: "7",
            CONF_C2: "1",
            CONF_D2: "8",
            CONF_FAN: True,
            CONF_LIGHT: True,
            CONF_FRONT: False,
            CONF_AUX: False,
            CONF_CPI: False,
            CONF_DEBUG_LOGGING: False,
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_BACKEND_TYPE] == BACKEND_ESPHOME
    assert result["data"][CONF_ESPHOME_ENTRY_ID] == linked_entry.entry_id
    assert result["options"][CONF_ACTIVE_LISTENING] is True


async def test_duplicate_remote_id_is_rejected_for_same_controller(hass) -> None:
    """The same remote id cannot be added twice for the same concrete controller."""

    existing = MockConfigEntry(
        domain=DOMAIN,
        title="Existing Yard Stick Fireplace",
        data={
            "name": "Existing Yard Stick Fireplace",
            CONF_BACKEND_TYPE: BACKEND_YARDSTICK,
            CONF_REMOTE_ID: 0x3B3F02,
            CONF_C1: 5,
            CONF_D1: 7,
            CONF_C2: 1,
            CONF_D2: 8,
        },
    )
    existing.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data={
            "name": "Duplicate Yard Stick Fireplace",
            CONF_BACKEND_TYPE: BACKEND_YARDSTICK,
            CONF_REMOTE_ID: "3b3f02",
            CONF_C1: "5",
            CONF_D1: "7",
            CONF_C2: "1",
            CONF_D2: "8",
            CONF_FAN: True,
            CONF_LIGHT: True,
            CONF_FRONT: False,
            CONF_AUX: False,
            CONF_CPI: False,
            CONF_DEBUG_LOGGING: False,
        },
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_duplicate_remote_id_is_allowed_for_different_controller(hass) -> None:
    """The same remote id can be bench-configured across different controllers."""

    existing = MockConfigEntry(
        domain=DOMAIN,
        title="Existing Yard Stick Fireplace",
        data={
            "name": "Existing Yard Stick Fireplace",
            CONF_BACKEND_TYPE: BACKEND_YARDSTICK,
            CONF_REMOTE_ID: 0x3B3F02,
            CONF_C1: 5,
            CONF_D1: 7,
            CONF_C2: 1,
            CONF_D2: 8,
        },
    )
    existing.add_to_hass(hass)

    linked_entry = MockConfigEntry(
        domain="esphome",
        title="T-Embed",
        data={"host": "192.0.2.10", "port": 6053},
    )
    linked_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data={
            "name": "Bench LilyGO Fireplace",
            CONF_BACKEND_TYPE: BACKEND_ESPHOME,
            CONF_ESPHOME_ENTRY_ID: linked_entry.entry_id,
            CONF_REMOTE_ID: "3b3f02",
            CONF_C1: "5",
            CONF_D1: "7",
            CONF_C2: "1",
            CONF_D2: "8",
            CONF_FAN: True,
            CONF_LIGHT: True,
            CONF_FRONT: False,
            CONF_AUX: False,
            CONF_CPI: False,
            CONF_DEBUG_LOGGING: False,
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_BACKEND_TYPE] == BACKEND_ESPHOME


async def test_options_flow_updates_feature_flags(hass) -> None:
    """Options flow should update only mutable feature flags."""

    entry = hass.config_entries.async_entries(DOMAIN)
    if not entry:
        create_result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data={
                "name": "Living Room Fireplace",
                CONF_BACKEND_TYPE: "yardstick",
                CONF_REMOTE_ID: "3b3f02",
                CONF_C1: "5",
                CONF_D1: "7",
                CONF_C2: "1",
                CONF_D2: "8",
                CONF_FAN: True,
                CONF_LIGHT: True,
                CONF_FRONT: False,
                CONF_AUX: False,
                CONF_CPI: False,
            },
        )
        config_entry = create_result["result"]
    else:
        config_entry = entry[0]

    options_result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert options_result["type"] is FlowResultType.MENU

    options_result = await hass.config_entries.options.async_configure(
        options_result["flow_id"],
        user_input={"next_step_id": "features"},
    )
    assert options_result["type"] is FlowResultType.FORM
    assert options_result["step_id"] == "features"

    options_result = await hass.config_entries.options.async_configure(
        options_result["flow_id"],
        user_input={
            CONF_FAN: False,
            CONF_LIGHT: True,
            CONF_FRONT: True,
            CONF_AUX: True,
            CONF_CPI: False,
            CONF_DEBUG_LOGGING: False,
        },
    )

    assert options_result["type"] is FlowResultType.CREATE_ENTRY
    assert options_result["data"] == {
        CONF_FAN: False,
        CONF_LIGHT: True,
        CONF_FRONT: True,
        CONF_AUX: True,
        CONF_CPI: False,
        CONF_DEBUG_LOGGING: False,
        CONF_ACTIVE_LISTENING: False,
        CONF_FIREPLACE_SHORT_NAME: "---",
        CONF_PROFILES: {},
    }


async def test_options_flow_persists_and_reopens_debug_logging(hass, monkeypatch) -> None:
    """Feature options should save debug_logging and show it again when reopened."""

    _enable_fake_backend(monkeypatch)

    async def fake_enable_packet_debug_logging(_hass):
        return PacketDebugLogPaths(
            primary_log_path=Path("/config/proflame2_debug.log"),
            decode_failure_log_path=Path("/config/proflame2_decode_failures.log"),
        )

    monkeypatch.setattr(
        "custom_components.proflame2.runtime.async_enable_packet_debug_logging",
        fake_enable_packet_debug_logging,
    )

    create_result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data={
            "name": "Living Room Fireplace",
            CONF_BACKEND_TYPE: "fake",
            CONF_REMOTE_ID: "3b3f02",
            CONF_C1: "5",
            CONF_D1: "7",
            CONF_C2: "1",
            CONF_D2: "8",
            CONF_FAN: True,
            CONF_LIGHT: True,
            CONF_FRONT: False,
            CONF_AUX: False,
            CONF_CPI: False,
        },
    )
    config_entry = create_result["result"]

    options_result = await hass.config_entries.options.async_init(config_entry.entry_id)
    options_result = await hass.config_entries.options.async_configure(
        options_result["flow_id"],
        user_input={"next_step_id": "features"},
    )
    options_result = await hass.config_entries.options.async_configure(
        options_result["flow_id"],
        user_input={
            CONF_FAN: True,
            CONF_LIGHT: True,
            CONF_FRONT: False,
            CONF_AUX: False,
            CONF_CPI: False,
            CONF_DEBUG_LOGGING: True,
        },
    )
    assert options_result["type"] is FlowResultType.CREATE_ENTRY
    assert options_result["data"][CONF_DEBUG_LOGGING] is True

    hass.config_entries.async_update_entry(config_entry, options=options_result["data"])

    reopened = await hass.config_entries.options.async_init(config_entry.entry_id)
    reopened = await hass.config_entries.options.async_configure(
        reopened["flow_id"],
        user_input={"next_step_id": "features"},
    )
    assert reopened["type"] is FlowResultType.FORM
    debug_marker = next(
        key for key in reopened["data_schema"].schema if getattr(key, "schema", None) == CONF_DEBUG_LOGGING
    )
    assert debug_marker.description["suggested_value"] is True


async def test_options_profile_edits_preserve_debug_logging(hass, monkeypatch) -> None:
    """Editing unrelated profile options should not reset debug_logging."""

    _enable_fake_backend(monkeypatch)

    async def fake_enable_packet_debug_logging(_hass):
        return PacketDebugLogPaths(
            primary_log_path=Path("/config/proflame2_debug.log"),
            decode_failure_log_path=Path("/config/proflame2_decode_failures.log"),
        )

    monkeypatch.setattr(
        "custom_components.proflame2.runtime.async_enable_packet_debug_logging",
        fake_enable_packet_debug_logging,
    )

    create_result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data={
            "name": "Living Room Fireplace",
            CONF_BACKEND_TYPE: "fake",
            CONF_REMOTE_ID: "3b3f02",
            CONF_C1: "5",
            CONF_D1: "7",
            CONF_C2: "1",
            CONF_D2: "8",
            CONF_FAN: True,
            CONF_LIGHT: True,
            CONF_FRONT: False,
            CONF_AUX: False,
            CONF_CPI: False,
        },
    )
    config_entry = create_result["result"]
    hass.config_entries.async_update_entry(
        config_entry,
        options={
            CONF_FAN: True,
            CONF_LIGHT: True,
            CONF_FRONT: False,
            CONF_AUX: False,
            CONF_CPI: False,
            CONF_DEBUG_LOGGING: True,
            CONF_PROFILES: {},
        },
    )

    options_result = await hass.config_entries.options.async_init(config_entry.entry_id)
    options_result = await hass.config_entries.options.async_configure(
        options_result["flow_id"],
        user_input={"next_step_id": "profiles"},
    )
    options_result = await hass.config_entries.options.async_configure(
        options_result["flow_id"],
        user_input={"next_step_id": "add_profile"},
    )
    options_result = await hass.config_entries.options.async_configure(
        options_result["flow_id"],
        user_input={
            CONF_NAME: "Movie Night",
            CONF_POWER: True,
            CONF_FLAME: 1,
            CONF_FAN: 0,
            CONF_LIGHT: 2,
        },
    )
    assert options_result["type"] is FlowResultType.CREATE_ENTRY
    assert options_result["data"][CONF_DEBUG_LOGGING] is True


async def test_multiple_config_entries_are_allowed(hass, monkeypatch) -> None:
    """Different fireplace remote IDs should allow multiple entries."""

    _enable_fake_backend(monkeypatch)

    first = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data={
            "name": "Living Room Fireplace",
            CONF_BACKEND_TYPE: "yardstick",
            CONF_REMOTE_ID: "3b3f02",
            CONF_C1: "5",
            CONF_D1: "7",
            CONF_C2: "1",
            CONF_D2: "8",
            CONF_FAN: True,
            CONF_LIGHT: True,
            CONF_FRONT: False,
            CONF_AUX: False,
            CONF_CPI: False,
        },
    )
    second = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data={
            "name": "Bedroom Fireplace",
            CONF_BACKEND_TYPE: "fake",
            CONF_REMOTE_ID: "3b3f03",
            CONF_C1: "5",
            CONF_D1: "7",
            CONF_C2: "1",
            CONF_D2: "8",
            CONF_FAN: True,
            CONF_LIGHT: False,
            CONF_FRONT: False,
            CONF_AUX: False,
            CONF_CPI: False,
        },
    )

    assert first["type"] is FlowResultType.CREATE_ENTRY
    assert second["type"] is FlowResultType.CREATE_ENTRY


async def test_config_flow_can_learn_profile_and_create_entry(hass, monkeypatch) -> None:
    """Guided learning should derive the remote profile and create one entry."""

    _enable_fake_backend(monkeypatch)

    backend = FakeRFBackend()
    backend.queue_packets(
        _packet(remote_id=0x3B3F02, cmd1=0x01, err1=0x76, cmd2=0x06, err2=0xDE),
        _packet(remote_id=0x3B3F02, cmd1=0x00, err1=0x57, cmd2=0x06, err2=0xDE),
        _packet(remote_id=0x3B3F02, cmd1=0x01, err1=0x76, cmd2=0x06, err2=0xDE),
        _packet(remote_id=0x3B3F02, cmd1=0x01, err1=0x76, cmd2=0x05, err2=0xBD),
    )
    hass.data.setdefault(DOMAIN, {})[DATA_LEARNING_BACKEND_FACTORY] = _backend_factory(backend)
    hass.data[DOMAIN][DATA_LEARNING_TIMEOUT] = 0.2
    hass.data[DOMAIN][DATA_LEARNING_RECEIVE_TIMEOUT] = 0.01
    hass.data[DOMAIN][DATA_FAKE_LEARNING_DELAY] = 0.01

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.MENU

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "learn"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "learn"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "name": "Living Room Fireplace",
            CONF_BACKEND_TYPE: "fake",
        },
    )
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    assert result["step_id"] == "learn_progress"
    assert result["description_placeholders"]["instruction"] == (
        "Press the Power button once. The fireplace does not need to start in any specific state."
    )

    result = await _advance_guided_learning(hass, result["flow_id"], result)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "learn_features"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_FAN: True,
            CONF_LIGHT: True,
            CONF_FRONT: False,
            CONF_AUX: False,
            CONF_CPI: False,
            CONF_DEBUG_LOGGING: False,
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Living Room Fireplace"
    assert result["data"][CONF_REMOTE_ID] == 0x3B3F02
    assert result["data"][CONF_C1] == 5
    assert result["data"][CONF_D1] == 7
    assert result["data"][CONF_C2] == 1
    assert result["data"][CONF_D2] == 8
    assert result["options"][CONF_PROFILES] == {}


async def test_guided_learning_timeout_is_per_prompt_not_overall(hass, monkeypatch) -> None:
    """Each guided prompt should get its own timeout window."""

    _enable_fake_backend(monkeypatch)

    backend = DelayedFakeRFBackend([0.03, 0.03, 0.03, 0.03])
    backend.queue_packets(
        _packet(remote_id=0x3B3F02, cmd1=0x01, err1=0x76, cmd2=0x06, err2=0xDE),
        _packet(remote_id=0x3B3F02, cmd1=0x00, err1=0x57, cmd2=0x06, err2=0xDE),
        _packet(remote_id=0x3B3F02, cmd1=0x01, err1=0x76, cmd2=0x06, err2=0xDE),
        _packet(remote_id=0x3B3F02, cmd1=0x01, err1=0x76, cmd2=0x05, err2=0xBD),
    )
    hass.data.setdefault(DOMAIN, {})[DATA_LEARNING_BACKEND_FACTORY] = _backend_factory(backend)
    hass.data[DOMAIN][DATA_LEARNING_TIMEOUT] = 0.04
    hass.data[DOMAIN][DATA_LEARNING_RECEIVE_TIMEOUT] = 0.04
    hass.data[DOMAIN][DATA_FAKE_LEARNING_DELAY] = 0.01

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "learn"},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "name": "Living Room Fireplace",
            CONF_BACKEND_TYPE: "fake",
        },
    )
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    assert result["step_id"] == "learn_progress"

    result = await _advance_guided_learning(hass, result["flow_id"], result)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "learn_features"


async def test_builtin_fake_backend_auto_completes_learning(hass, monkeypatch) -> None:
    """The built-in fake backend should auto-supply packets and complete learning."""

    _enable_fake_backend(monkeypatch)

    hass.data.setdefault(DOMAIN, {})[DATA_LEARNING_TIMEOUT] = 0.2
    hass.data[DOMAIN][DATA_LEARNING_RECEIVE_TIMEOUT] = 0.01
    hass.data[DOMAIN][DATA_FAKE_LEARNING_DELAY] = 0.01

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "learn"},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "name": "Living Room Fireplace",
            CONF_BACKEND_TYPE: "fake",
        },
    )
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    assert result["step_id"] == "learn_progress"

    result = await _advance_guided_learning(hass, result["flow_id"], result)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "learn_features"


async def test_config_flow_can_fallback_to_manual_after_learn_failure(hass, monkeypatch) -> None:
    """Failed guided learning should allow a manual-entry fallback."""

    _enable_fake_backend(monkeypatch)

    backend = FakeRFBackend()
    hass.data.setdefault(DOMAIN, {})[DATA_LEARNING_BACKEND_FACTORY] = _backend_factory(backend)
    hass.data[DOMAIN][DATA_LEARNING_TIMEOUT] = 0.02
    hass.data[DOMAIN][DATA_LEARNING_RECEIVE_TIMEOUT] = 0.01
    hass.data[DOMAIN][DATA_FAKE_LEARNING_DELAY] = 0.01

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "learn"},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "name": "Living Room Fireplace",
            CONF_BACKEND_TYPE: "fake",
        },
    )
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    assert result["step_id"] == "learn_progress"

    result = await _advance_guided_learning(hass, result["flow_id"], result)
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "learn_failed"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "manual"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manual"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "name": "Living Room Fireplace",
            CONF_BACKEND_TYPE: "fake",
            CONF_REMOTE_ID: "3b3f02",
            CONF_C1: "5",
            CONF_D1: "7",
            CONF_C2: "1",
            CONF_D2: "8",
            CONF_FAN: True,
            CONF_LIGHT: True,
            CONF_FRONT: False,
            CONF_AUX: False,
            CONF_CPI: False,
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_BACKEND_TYPE] == "fake"


async def test_config_flow_surfaces_clean_yardstick_backend_unavailable_error(hass) -> None:
    """Learning should fail cleanly when the Yard Stick backend cannot start."""

    def failing_factory(backend_type: str):
        assert backend_type == "yardstick"
        raise YardStickBackendUnavailableError("No YARD Stick One device was found.")

    hass.data.setdefault(DOMAIN, {})[DATA_LEARNING_BACKEND_FACTORY] = failing_factory

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "learn"},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "name": "Living Room Fireplace",
            CONF_BACKEND_TYPE: "yardstick",
        },
    )

    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "learn_failed"
    assert "No YARD Stick One device was found." in result["description_placeholders"]["error"]


async def test_config_flow_retry_can_succeed_after_initial_failure(hass, monkeypatch) -> None:
    """Retrying learn mode should restart learning and allow success."""

    _enable_fake_backend(monkeypatch)

    failing_backend = FakeRFBackend()
    succeeding_backend = FakeRFBackend()
    succeeding_backend.queue_packets(
        _packet(remote_id=0x3B3F02, cmd1=0x01, err1=0x76, cmd2=0x16, err2=0xEF),
        _packet(remote_id=0x3B3F02, cmd1=0x31, err1=0x25, cmd2=0x26, err2=0xBC),
        _packet(remote_id=0x3B3F02, cmd1=0x51, err1=0x83, cmd2=0x36, err2=0x8D),
    )
    hass.data.setdefault(DOMAIN, {})[DATA_LEARNING_BACKEND_FACTORY] = _backend_factory(
        failing_backend,
        succeeding_backend,
    )
    hass.data[DOMAIN][DATA_LEARNING_TIMEOUT] = 0.02
    hass.data[DOMAIN][DATA_LEARNING_RECEIVE_TIMEOUT] = 0.01
    hass.data[DOMAIN][DATA_FAKE_LEARNING_DELAY] = 0.01

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "learn"},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "name": "Living Room Fireplace",
            CONF_BACKEND_TYPE: "fake",
        },
    )
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    assert result["step_id"] == "learn_progress"

    result = await _advance_guided_learning(hass, result["flow_id"], result)
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "learn_failed"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "retry_learn"},
    )
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    assert result["step_id"] == "learn_progress"

    result = await _advance_guided_learning(hass, result["flow_id"], result)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "learn_features"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_FAN: True,
            CONF_LIGHT: True,
            CONF_FRONT: False,
            CONF_AUX: False,
            CONF_CPI: False,
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_REMOTE_ID] == 0x3B3F02


async def test_options_flow_can_add_edit_and_delete_saved_profile(hass, monkeypatch) -> None:
    """Options flow should manage saved profiles as first-class entry options."""

    _enable_fake_backend(monkeypatch)

    create_result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data={
            "name": "Living Room Fireplace",
            CONF_BACKEND_TYPE: "fake",
            CONF_REMOTE_ID: "3b3f02",
            CONF_C1: "5",
            CONF_D1: "7",
            CONF_C2: "1",
            CONF_D2: "8",
            CONF_FAN: True,
            CONF_LIGHT: True,
            CONF_FRONT: False,
            CONF_AUX: False,
            CONF_CPI: False,
        },
    )
    config_entry = create_result["result"]

    options_result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert options_result["type"] is FlowResultType.MENU

    options_result = await hass.config_entries.options.async_configure(
        options_result["flow_id"],
        user_input={"next_step_id": "profiles"},
    )
    assert options_result["type"] is FlowResultType.MENU

    options_result = await hass.config_entries.options.async_configure(
        options_result["flow_id"],
        user_input={"next_step_id": "add_profile"},
    )
    assert options_result["type"] is FlowResultType.FORM

    options_result = await hass.config_entries.options.async_configure(
        options_result["flow_id"],
        user_input={
            CONF_NAME: "Movie Night",
            CONF_POWER: True,
            CONF_FLAME: 1,
            CONF_FAN: 0,
            CONF_LIGHT: 2,
        },
    )
    assert options_result["type"] is FlowResultType.CREATE_ENTRY
    assert options_result["data"][CONF_PROFILES]["movie_night"] == {
        CONF_PROFILE_ID: "movie_night",
        CONF_NAME: "Movie Night",
        CONF_POWER: True,
        CONF_FLAME: 1,
        CONF_FAN: 0,
        CONF_LIGHT: 2,
        CONF_FRONT: False,
        CONF_AUX: False,
        CONF_CPI: False,
    }

    hass.config_entries.async_update_entry(config_entry, options=options_result["data"])

    options_result = await hass.config_entries.options.async_init(config_entry.entry_id)
    options_result = await hass.config_entries.options.async_configure(
        options_result["flow_id"],
        user_input={"next_step_id": "profiles"},
    )
    options_result = await hass.config_entries.options.async_configure(
        options_result["flow_id"],
        user_input={"next_step_id": "select_profile"},
    )
    options_result = await hass.config_entries.options.async_configure(
        options_result["flow_id"],
        user_input={CONF_PROFILE_ID: "movie_night", "action": "edit_profile"},
    )
    assert options_result["type"] is FlowResultType.FORM

    options_result = await hass.config_entries.options.async_configure(
        options_result["flow_id"],
        user_input={
            CONF_NAME: "Evening Relax",
            CONF_POWER: True,
            CONF_FLAME: 2,
            CONF_FAN: 1,
            CONF_LIGHT: 1,
        },
    )
    assert options_result["type"] is FlowResultType.CREATE_ENTRY
    assert options_result["data"][CONF_PROFILES]["movie_night"][CONF_NAME] == "Evening Relax"
    assert options_result["data"][CONF_PROFILES]["movie_night"][CONF_FLAME] == 2

    hass.config_entries.async_update_entry(config_entry, options=options_result["data"])

    options_result = await hass.config_entries.options.async_init(config_entry.entry_id)
    options_result = await hass.config_entries.options.async_configure(
        options_result["flow_id"],
        user_input={"next_step_id": "profiles"},
    )
    options_result = await hass.config_entries.options.async_configure(
        options_result["flow_id"],
        user_input={"next_step_id": "select_profile"},
    )
    options_result = await hass.config_entries.options.async_configure(
        options_result["flow_id"],
        user_input={CONF_PROFILE_ID: "movie_night", "action": "delete_profile"},
    )
    options_result = await hass.config_entries.options.async_configure(
        options_result["flow_id"],
        user_input={"confirm": True},
    )
    assert options_result["type"] is FlowResultType.CREATE_ENTRY
    assert options_result["data"][CONF_PROFILES] == {}


async def test_options_flow_rejects_duplicate_profile_ids(hass, monkeypatch) -> None:
    """Two saved profiles with the same derived internal id should be rejected."""

    _enable_fake_backend(monkeypatch)

    create_result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data={
            "name": "Living Room Fireplace",
            CONF_BACKEND_TYPE: "fake",
            CONF_REMOTE_ID: "3b3f02",
            CONF_C1: "5",
            CONF_D1: "7",
            CONF_C2: "1",
            CONF_D2: "8",
            CONF_FAN: True,
            CONF_LIGHT: True,
            CONF_FRONT: False,
            CONF_AUX: False,
            CONF_CPI: False,
        },
    )
    config_entry = create_result["result"]
    hass.config_entries.async_update_entry(
        config_entry,
        options={
            CONF_FAN: True,
            CONF_LIGHT: True,
            CONF_FRONT: False,
            CONF_AUX: False,
            CONF_CPI: False,
            CONF_PROFILES: {
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
        },
    )

    options_result = await hass.config_entries.options.async_init(config_entry.entry_id)
    options_result = await hass.config_entries.options.async_configure(
        options_result["flow_id"],
        user_input={"next_step_id": "profiles"},
    )
    options_result = await hass.config_entries.options.async_configure(
        options_result["flow_id"],
        user_input={"next_step_id": "add_profile"},
    )
    options_result = await hass.config_entries.options.async_configure(
        options_result["flow_id"],
        user_input={
            CONF_NAME: "Movie Night",
            CONF_POWER: True,
            CONF_FLAME: 2,
            CONF_FAN: 1,
            CONF_LIGHT: 1,
        },
    )

    assert options_result["type"] is FlowResultType.FORM
    assert options_result["errors"] == {CONF_NAME: "duplicate_profile_id"}


async def test_same_remote_id_can_be_used_with_two_different_linked_lilygo_entries(hass) -> None:
    """ESPHome-backed entries should de-duplicate by linked controller, not generic backend type."""

    first_controller = MockConfigEntry(domain="esphome", title="LilyGO One", data={"host": "192.0.2.11"})
    second_controller = MockConfigEntry(domain="esphome", title="LilyGO Two", data={"host": "192.0.2.12"})
    first_controller.add_to_hass(hass)
    second_controller.add_to_hass(hass)

    first = MockConfigEntry(
        domain=DOMAIN,
        title="Living Room Fireplace",
        data={
            CONF_NAME: "Living Room Fireplace",
            CONF_BACKEND_TYPE: BACKEND_ESPHOME,
            CONF_REMOTE_ID: 0x3B3F02,
            CONF_C1: 5,
            CONF_D1: 7,
            CONF_C2: 1,
            CONF_D2: 8,
            CONF_ESPHOME_ENTRY_ID: first_controller.entry_id,
        },
        options={
            CONF_FAN: True,
            CONF_LIGHT: True,
            CONF_FRONT: False,
            CONF_AUX: False,
            CONF_CPI: False,
            CONF_DEBUG_LOGGING: False,
            CONF_FIREPLACE_SHORT_NAME: "---",
            CONF_PROFILES: {},
        },
    )
    first.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data={
            CONF_NAME: "Bedroom Fireplace",
            CONF_BACKEND_TYPE: BACKEND_ESPHOME,
            CONF_REMOTE_ID: "3b3f02",
            CONF_C1: "5",
            CONF_D1: "7",
            CONF_C2: "1",
            CONF_D2: "8",
            CONF_FAN: True,
            CONF_LIGHT: True,
            CONF_FRONT: False,
            CONF_AUX: False,
            CONF_CPI: False,
            CONF_DEBUG_LOGGING: False,
            CONF_FIREPLACE_SHORT_NAME: "BR",
            CONF_ESPHOME_ENTRY_ID: second_controller.entry_id,
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ESPHOME_ENTRY_ID] == second_controller.entry_id
    assert result["options"][CONF_FIREPLACE_SHORT_NAME] == "BR"
