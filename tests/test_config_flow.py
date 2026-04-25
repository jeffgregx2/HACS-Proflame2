"""Home Assistant config flow tests for Proflame2."""

from __future__ import annotations

import asyncio
from collections import deque

import pytest

homeassistant = pytest.importorskip("homeassistant")
pytest.importorskip("pytest_homeassistant_custom_component")

pytestmark = pytest.mark.ha

from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType

from custom_components.proflame2.const import (
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
    DATA_FAKE_LEARNING_DELAY,
    DATA_LEARNING_BACKEND_FACTORY,
    DATA_LEARNING_RECEIVE_TIMEOUT,
    DATA_LEARNING_TIMEOUT,
    DOMAIN,
)
from custom_components.proflame2.protocol.packet import ProflameFrame, ProflamePacket
from custom_components.proflame2.rf.fake import FakeRFBackend


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
        },
    )

    assert options_result["type"] is FlowResultType.CREATE_ENTRY
    assert options_result["data"] == {
        CONF_FAN: False,
        CONF_LIGHT: True,
        CONF_FRONT: True,
        CONF_AUX: True,
        CONF_CPI: False,
        CONF_PROFILES: {},
    }


async def test_multiple_config_entries_are_allowed(hass) -> None:
    """Different fireplace remote IDs should allow multiple entries."""

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


async def test_config_flow_can_learn_profile_and_create_entry(hass) -> None:
    """Guided learning should derive the remote profile and create one entry."""

    backend = FakeRFBackend()
    backend.queue_packets(
        _packet(remote_id=0x3B3F02, cmd1=0x01, err1=0x76, cmd2=0x16, err2=0xEF),
        _packet(remote_id=0x3B3F02, cmd1=0x31, err1=0x25, cmd2=0x26, err2=0xBC),
        _packet(remote_id=0x3B3F02, cmd1=0x51, err1=0x83, cmd2=0x36, err2=0x8D),
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
    assert result["title"] == "Living Room Fireplace"
    assert result["data"][CONF_REMOTE_ID] == 0x3B3F02
    assert result["data"][CONF_C1] == 5
    assert result["data"][CONF_D1] == 7
    assert result["data"][CONF_C2] == 1
    assert result["data"][CONF_D2] == 8
    assert result["options"][CONF_PROFILES] == {}


async def test_guided_learning_timeout_is_per_prompt_not_overall(hass) -> None:
    """Each guided prompt should get its own timeout window."""

    backend = DelayedFakeRFBackend([0.03, 0.03, 0.03])
    backend.queue_packets(
        _packet(remote_id=0x3B3F02, cmd1=0x01, err1=0x76, cmd2=0x16, err2=0xEF),
        _packet(remote_id=0x3B3F02, cmd1=0x31, err1=0x25, cmd2=0x26, err2=0xBC),
        _packet(remote_id=0x3B3F02, cmd1=0x51, err1=0x83, cmd2=0x36, err2=0x8D),
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


async def test_builtin_fake_backend_auto_completes_learning(hass) -> None:
    """The built-in fake backend should auto-supply packets and complete learning."""

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


async def test_config_flow_can_fallback_to_manual_after_learn_failure(hass) -> None:
    """Failed guided learning should allow a manual-entry fallback."""

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


async def test_config_flow_retry_can_succeed_after_initial_failure(hass) -> None:
    """Retrying learn mode should restart learning and allow success."""

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


async def test_options_flow_can_add_edit_and_delete_saved_profile(hass) -> None:
    """Options flow should manage saved profiles as first-class entry options."""

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


async def test_options_flow_rejects_duplicate_profile_ids(hass) -> None:
    """Two saved profiles with the same derived internal id should be rejected."""

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
