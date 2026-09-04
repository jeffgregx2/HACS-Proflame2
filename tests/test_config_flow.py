"""Home Assistant config flow tests for Proflame2."""

from __future__ import annotations

import asyncio
from collections import deque
from pathlib import Path

import pytest

homeassistant = pytest.importorskip("homeassistant")
pytest.importorskip("pytest_homeassistant_custom_component")

pytestmark = pytest.mark.ha

from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.proflame2 import CONFIG_SCHEMA, async_setup
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
    CONF_INITIAL_FRAME,
    CONF_INITIAL_PACKET_SOURCE,
    CONF_LEARNING_DEBUG_LOGGING,
    CONF_LIGHT,
    CONF_NAME,
    CONF_POWER,
    CONF_PROFILE_ID,
    CONF_PROFILES,
    CONF_REMOTE_ID,
    CONF_RTL433_SAMPLES,
    CONF_YARDSTICK_LEARNING_FREQUENCY_HZ,
    CONF_YARDSTICK_LEARNING_SWEEP_ENABLED,
    DATA_FAKE_LEARNING_DELAY,
    DATA_LEARNING_BACKEND_FACTORY,
    DATA_LEARNING_DEBUG_LOGGING,
    DATA_LEARNING_RECEIVE_TIMEOUT,
    DATA_LEARNING_TIMEOUT,
    DATA_YARDSTICK_LEARNING_FREQUENCY_HZ,
    DATA_YARDSTICK_LEARNING_SWEEP_ENABLED,
    DOMAIN,
)
from custom_components.proflame2.learning import LearnSession
from custom_components.proflame2.packet_debug import PacketDebugLogPaths
from custom_components.proflame2.protocol.packet import ProflameFrame, ProflamePacket
from custom_components.proflame2.rf.fake import FakeRFBackend
from custom_components.proflame2.rf.yardstick import YardStickBackendUnavailableError
from custom_components.proflame2.version import ENABLE_FAKE_BACKEND_ENV


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading integrations from the local custom_components directory."""

    yield


def _form_schema_fields(data_schema):
    """Return config-flow fields keyed by their submitted name.

    Home Assistant 2026.9 uses probatio-backed schemas, which are consumed by
    the frontend directly and are no longer compatible with the old standalone
    voluptuous serializer used by these tests.
    """

    return {str(marker.schema): selector for marker, selector in data_schema.schema.items()}


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


class ExtendedFrameFakeRFBackend(FakeRFBackend):
    """Fake backend that supplies RMT extended-frame diagnostic metadata."""

    async def receive(self, timeout: float | None = None) -> ProflamePacket | None:
        packet = await super().receive(timeout)
        if packet is not None:
            self.last_fifo_semantic_artifact = {
                "event_id": f"extended-{len(self.sent_packets) + len(self.receive_queue)}",
                "capture_metadata": {"pcm_bit_length": "259"},
                "raw_payload_hex": "e55959",
                "validation_notes": (
                    "frame_format=extended_10_word_truncated_end_guard",
                    "extension_hex=004177",
                ),
            }
        return packet


def _backend_factory(*backends: FakeRFBackend):
    queued = deque(backends)

    def factory(backend_type: str) -> FakeRFBackend:
        assert backend_type == "fake"
        return queued.popleft()

    return factory


def _enable_fake_backend(monkeypatch) -> None:
    """Opt into Fake for tests that intentionally exercise simulated hardware."""

    monkeypatch.setenv("PROFLAME2_BUILD", "dev")
    monkeypatch.setenv(ENABLE_FAKE_BACKEND_ENV, "true")


def test_yaml_learning_debug_config_schema_accepts_hidden_override() -> None:
    """Domain YAML should support the support-only guided-learning debug override."""

    enabled = CONFIG_SCHEMA({DOMAIN: {CONF_LEARNING_DEBUG_LOGGING: "true"}})
    empty = CONFIG_SCHEMA({DOMAIN: None})

    assert enabled[DOMAIN][CONF_LEARNING_DEBUG_LOGGING] is True
    assert empty[DOMAIN] is None


def test_yaml_yardstick_learning_tuning_schema_accepts_hidden_overrides() -> None:
    """Domain YAML should support support-only YardStick learning RX tuning."""

    configured = CONFIG_SCHEMA(
        {
            DOMAIN: {
                CONF_YARDSTICK_LEARNING_FREQUENCY_HZ: "314973000",
                CONF_YARDSTICK_LEARNING_SWEEP_ENABLED: "true",
            }
        }
    )

    assert configured[DOMAIN][CONF_YARDSTICK_LEARNING_FREQUENCY_HZ] == 314_973_000
    assert configured[DOMAIN][CONF_YARDSTICK_LEARNING_SWEEP_ENABLED] is True


async def test_yaml_yardstick_learning_tuning_overrides_are_stored(hass) -> None:
    """YardStick support tuning should be available before guided learning starts."""

    assert await async_setup(
        hass,
        {
            DOMAIN: {
                CONF_YARDSTICK_LEARNING_FREQUENCY_HZ: 314_973_000,
                CONF_YARDSTICK_LEARNING_SWEEP_ENABLED: True,
            }
        },
    )

    assert hass.data[DOMAIN][DATA_YARDSTICK_LEARNING_FREQUENCY_HZ] == 314_973_000
    assert hass.data[DOMAIN][DATA_YARDSTICK_LEARNING_SWEEP_ENABLED] is True


async def _advance_guided_learning(
    hass,
    flow_id: str,
    result: dict | None = None,
):
    """Advance guided learning until it reaches a non-progress step."""

    current = result or await hass.config_entries.flow.async_configure(flow_id)

    for _ in range(100):
        if current["type"] is FlowResultType.SHOW_PROGRESS:
            flow = hass.config_entries.flow._progress[flow_id]
            progress_task = flow.async_get_progress_task()
            if progress_task is not None:
                await progress_task
                await hass.async_block_till_done()
                current = flow.cur_step
            else:
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


async def test_manual_entry_form_schema_exposes_fields(hass) -> None:
    """The manual-entry form should expose fields to the HA frontend."""

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
    assert _form_schema_fields(result["data_schema"])


async def test_manual_rtl433_learning_captures_buttons_then_confirms_remote_learned(hass) -> None:
    """rtl_433 manual learning should collect pasted rows before feature setup."""

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )
    assert "manual_rtl433" in result["menu_options"]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "manual_rtl433"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manual_rtl433"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_NAME: "Living Room Fireplace",
            CONF_FIREPLACE_SHORT_NAME: "---",
            CONF_BACKEND_TYPE: BACKEND_YARDSTICK,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manual_rtl433_prompt"
    assert "**Power**" in result["description_placeholders"]["instruction"]
    assert result["description_placeholders"]["rtl433_command"] == "rtl_433 -f 315M -R 207 -M level -F json"
    assert (
        "rtl_433-assisted manual learning guide" in result["description_placeholders"]["rtl433_manual_learning_guide"]
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_RTL433_SAMPLES: "id=3b3f02 cmd1=01 cmd2=16 err1=76 err2=ef"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manual_rtl433_prompt"
    assert "**Temp Down**" in result["description_placeholders"]["instruction"]
    assert result["description_placeholders"]["sample_count"] == "1"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_RTL433_SAMPLES: "id=3b3f02 cmd1=31 cmd2=26 err1=25 err2=bc"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manual_rtl433_prompt"
    assert "**Temp Up**" in result["description_placeholders"]["instruction"]
    assert result["description_placeholders"]["sample_count"] == "2"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_RTL433_SAMPLES: "id=3b3f02 cmd1=51 cmd2=36 err1=83 err2=8d"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manual_rtl433_power_off"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input={})
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
            CONF_ACTIVE_LISTENING: False,
            CONF_FIREPLACE_SHORT_NAME: "---",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_BACKEND_TYPE] == BACKEND_YARDSTICK
    assert result["data"][CONF_REMOTE_ID] == 0x3B3F02
    assert result["data"][CONF_C1] == 5
    assert result["data"][CONF_D1] == 7
    assert result["data"][CONF_C2] == 1
    assert result["data"][CONF_D2] == 8
    assert result["data"][CONF_INITIAL_PACKET_SOURCE] == "rtl433_manual"
    assert result["data"][CONF_INITIAL_FRAME]["cmd1"] == 0x51
    assert result["data"][CONF_INITIAL_FRAME]["cmd2"] == 0x36


async def test_manual_rtl433_learning_rejects_invalid_paste_without_advancing(hass) -> None:
    """Invalid rtl_433 rows should keep the user on the same prompt."""

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "manual_rtl433"},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_NAME: "Living Room Fireplace",
            CONF_FIREPLACE_SHORT_NAME: "---",
            CONF_BACKEND_TYPE: BACKEND_YARDSTICK,
        },
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_RTL433_SAMPLES: "id=3b3f02 cmd1=01 cmd2=16 err1=76"},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manual_rtl433_prompt"
    assert result["errors"] == {CONF_RTL433_SAMPLES: "invalid_rtl433_samples"}
    assert result["description_placeholders"]["sample_count"] == "0"
    assert "**Power**" in result["description_placeholders"]["instruction"]


def test_manual_rtl433_prompt_translation_warns_about_duplicate_output() -> None:
    """The user-facing paste prompt should warn about delayed duplicate rtl_433 rows."""

    translations = Path("custom_components/proflame2/translations/en.json").read_text(encoding="utf-8")

    assert "Paste the newest rtl_433 JSON line" in translations
    assert "Ignore duplicate lines from earlier button presses" in translations
    assert "{rtl433_manual_learning_guide}" in translations
    assert "github.com/jeffgregx2/HACS-Proflame2/blob/main/docs/rtl433_manual_learning.md" not in translations


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

    fields = _form_schema_fields(result["data_schema"])
    backend_field = fields[CONF_BACKEND_TYPE]
    option_values = {option["value"] for option in backend_field.config["options"]}
    assert option_values == {BACKEND_YARDSTICK, BACKEND_ESPHOME}
    assert CONF_ESPHOME_ENTRY_ID not in fields
    assert CONF_DEBUG_LOGGING not in fields
    assert CONF_ACTIVE_LISTENING not in fields


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

    fields = _form_schema_fields(result["data_schema"])
    backend_field = fields[CONF_BACKEND_TYPE]
    option_values = {option["value"] for option in backend_field.config["options"]}
    assert option_values == {BACKEND_YARDSTICK, BACKEND_ESPHOME}
    assert CONF_ESPHOME_ENTRY_ID not in fields
    assert CONF_DEBUG_LOGGING not in fields


async def test_yaml_learning_debug_override_enables_initial_guided_learning_debug(hass, monkeypatch) -> None:
    """A hidden YAML override should enable packet diagnostics before an entry exists."""

    captured_debug_flags: list[bool] = []

    async def fake_start_learning_session(
        hass,
        backend_type: str,
        *,
        debug_logging: bool = False,
        esphome_entry_id: str | None = None,
        timeout: float,
        receive_timeout: float,
    ) -> LearnSession:
        assert backend_type == BACKEND_YARDSTICK
        assert esphome_entry_id is None
        captured_debug_flags.append(debug_logging)
        backend = FakeRFBackend()
        await backend.connect()
        backend.queue_packets(_packet(remote_id=0x3B3F02, cmd1=0x01, err1=0x76, cmd2=0x06, err2=0xDE))
        return LearnSession(
            backend=backend,
            step_timeout=timeout,
            receive_timeout=receive_timeout,
            debug_logging_enabled=debug_logging,
            hass=hass,
        )

    monkeypatch.setattr(
        "custom_components.proflame2.config_flow.async_start_learning_session",
        fake_start_learning_session,
    )

    assert await async_setup(hass, {DOMAIN: {CONF_LEARNING_DEBUG_LOGGING: True}})
    assert hass.data[DOMAIN][DATA_LEARNING_DEBUG_LOGGING] is True

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "learn"},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_NAME: "Living Room Fireplace",
            CONF_FIREPLACE_SHORT_NAME: "---",
            CONF_BACKEND_TYPE: BACKEND_YARDSTICK,
        },
    )

    assert result["type"] is FlowResultType.SHOW_PROGRESS
    assert captured_debug_flags == [True]


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
    assert "docs/lilygo_cc1101_controller.md" in LILYGO_ESPHOME_LINK_HELP
    fields = _form_schema_fields(result["data_schema"])
    assert list(fields) == [CONF_ESPHOME_ENTRY_ID]
    assert fields[CONF_ESPHOME_ENTRY_ID].config["options"] == [
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
    fields = _form_schema_fields(result["data_schema"])
    assert list(fields) == [CONF_ESPHOME_ENTRY_ID]
    assert fields[CONF_ESPHOME_ENTRY_ID].config["options"] == [
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


async def test_extended_rmt_contradiction_collects_diagnostic_captures(hass, monkeypatch) -> None:
    """Extended RMT frames should collect labeled diagnostics before failing."""

    _enable_fake_backend(monkeypatch)
    backend = ExtendedFrameFakeRFBackend()
    backend.queue_packets(
        _packet(remote_id=0x08E905, cmd1=0x81, err1=0x15, cmd2=0x06, err2=0xE6),
        _packet(remote_id=0x08E905, cmd1=0x80, err1=0x15, cmd2=0x06, err2=0xE6),
        _packet(remote_id=0x08E905, cmd1=0x81, err1=0x15, cmd2=0x06, err2=0xE6),
        _packet(remote_id=0x08E905, cmd1=0x81, err1=0x15, cmd2=0x05, err2=0xE6),
        _packet(remote_id=0x08E905, cmd1=0x81, err1=0x15, cmd2=0x04, err2=0xE6),
        _packet(remote_id=0x08E905, cmd1=0x91, err1=0x15, cmd2=0x04, err2=0xE6),
        _packet(remote_id=0x08E905, cmd1=0x91, err1=0x16, cmd2=0x0C, err2=0xE6),
        _packet(remote_id=0x08E905, cmd1=0x82, err1=0x16, cmd2=0x00, err2=0xE6),
    )
    hass.data.setdefault(DOMAIN, {})[DATA_LEARNING_BACKEND_FACTORY] = _backend_factory(backend)
    hass.data[DOMAIN][DATA_LEARNING_TIMEOUT] = 0.2
    hass.data[DOMAIN][DATA_LEARNING_RECEIVE_TIMEOUT] = 0.01
    hass.data[DOMAIN][DATA_FAKE_LEARNING_DELAY] = 0.01

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input={"next_step_id": "learn"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"name": "Extended Remote", CONF_BACKEND_TYPE: "fake"},
    )

    result = await _advance_guided_learning(hass, result["flow_id"], result)

    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "learn_failed"
    assert "extended frame" in result["description_placeholders"]["error"]
    debug_log = Path(hass.config.path("proflame2_debug.log")).read_text(encoding="utf-8")
    assert "prompt_label': 'power_on'" in debug_log
    assert "prompt_label': 'diagnostic_flame_change'" in debug_log
    assert "prompt_label': 'diagnostic_mode_change'" in debug_log
    assert "pcm_bit_length': '259'" in debug_log


async def test_guided_learning_prompt_reuses_pending_capture_task(hass, monkeypatch) -> None:
    """Repeated progress callbacks must not start concurrent backend receives."""

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
    hass.data[DOMAIN][DATA_FAKE_LEARNING_DELAY] = 0.05

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input={"next_step_id": "learn"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"name": "Concurrent Prompt", CONF_BACKEND_TYPE: "fake"},
    )
    flow = hass.config_entries.flow._progress[result["flow_id"]]
    original_task = flow._learn_task
    assert original_task is not None

    duplicate_result = await flow.async_step_learn_prompt()

    assert duplicate_result["type"] is FlowResultType.SHOW_PROGRESS
    assert flow._learn_task is original_task
    assert len(backend.receive_queue) == 4

    result = await _advance_guided_learning(hass, result["flow_id"], result)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "learn_features"


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
