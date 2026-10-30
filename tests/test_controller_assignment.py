"""A physical controller must not be claimed by two fireplace entries."""

from unittest.mock import AsyncMock

import pytest

pytest.importorskip("homeassistant")
pytest.importorskip("pytest_homeassistant_custom_component")
pytestmark = pytest.mark.ha

from homeassistant.config_entries import SOURCE_USER, ConfigEntryDisabler
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ConfigEntryError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.proflame2 import async_setup_entry
from custom_components.proflame2.config_flow import Proflame2ConfigFlow
from custom_components.proflame2.const import (
    BACKEND_ESPHOME,
    BACKEND_FAKE,
    BACKEND_YARDSTICK,
    CONF_BACKEND_TYPE,
    CONF_C1,
    CONF_C2,
    CONF_D1,
    CONF_D2,
    CONF_ESPHOME_ENTRY_ID,
    CONF_LEARNING_ID,
    CONF_NAME,
    CONF_REMOTE_ID,
    DOMAIN,
)
from custom_components.proflame2.controller_assignment import controller_assignment_key, controller_assignments


@pytest.fixture(autouse=True)
def enable_integrations(enable_custom_integrations):
    yield


def saved_entry(hass, backend, *, linked="controller-one", options=None, disabled_by=None):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Existing fireplace",
        data={CONF_BACKEND_TYPE: backend, CONF_ESPHOME_ENTRY_ID: linked, CONF_REMOTE_ID: 0x654321},
        options=options or {},
        disabled_by=disabled_by,
    )
    entry.add_to_hass(hass)
    return entry


@pytest.mark.parametrize("backend", [BACKEND_YARDSTICK, BACKEND_ESPHOME])
@pytest.mark.parametrize("extended", [False, True])
async def test_learning_id_rejects_other_remote_on_assigned_controller(hass, backend, extended):
    linked = MockConfigEntry(domain="esphome", title="Controller", data={})
    linked.add_to_hass(hass)
    saved_entry(hass, backend, linked=linked.entry_id)
    flow = Proflame2ConfigFlow()
    flow.hass = hass
    result = await flow.async_step_manual(
        {
            CONF_NAME: "Second fireplace",
            CONF_BACKEND_TYPE: backend,
            CONF_ESPHOME_ENTRY_ID: linked.entry_id,
            CONF_LEARNING_ID: "PF2-1-E-CI2FNACCZAMYKTI4W4" if extended else "PF2-1-L-CI2FMVYYTNVRS7Q",
        }
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "controller_in_use"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


@pytest.mark.parametrize("backend", [BACKEND_YARDSTICK, BACKEND_ESPHOME])
async def test_legacy_manual_rejects_other_remote(hass, backend):
    linked = MockConfigEntry(domain="esphome", title="Controller", data={})
    linked.add_to_hass(hass)
    saved_entry(hass, backend, linked=linked.entry_id)
    flow = Proflame2ConfigFlow()
    flow.hass = hass
    result = await flow.async_step_manual_legacy(
        {
            CONF_NAME: "Second",
            CONF_BACKEND_TYPE: backend,
            CONF_ESPHOME_ENTRY_ID: linked.entry_id,
            CONF_REMOTE_ID: "123456",
            CONF_C1: "5",
            CONF_D1: "7",
            CONF_C2: "1",
            CONF_D2: "8",
        }
    )
    assert result["reason"] == "controller_in_use"


@pytest.mark.parametrize("backend", [BACKEND_YARDSTICK, BACKEND_ESPHOME])
@pytest.mark.parametrize("step", ["learn", "retry_learn"])
async def test_learning_conflict_does_not_open_radio(hass, monkeypatch, backend, step):
    saved_entry(hass, backend)
    start = AsyncMock()
    monkeypatch.setattr("custom_components.proflame2.config_flow.async_start_learning_session", start)
    flow = Proflame2ConfigFlow()
    flow.hass = hass
    data = {CONF_NAME: "Second", CONF_BACKEND_TYPE: backend, CONF_ESPHOME_ENTRY_ID: "controller-one"}
    flow._learn_input = data
    # Begin after controller selection; no remote identity is known yet.
    method = flow._async_begin_learning_from_current_input if step == "learn" else getattr(flow, f"async_step_{step}")
    result = await method()
    assert result["reason"] == "controller_in_use"
    start.assert_not_called()


def test_options_override_and_alias_match_runtime_controller(hass):
    owner = saved_entry(hass, "esphome", options={CONF_ESPHOME_ENTRY_ID: "controller-two"})
    assert not controller_assignments(
        hass, {CONF_BACKEND_TYPE: BACKEND_ESPHOME, CONF_ESPHOME_ENTRY_ID: "controller-one"}, {}
    )
    assert controller_assignments(
        hass, {CONF_BACKEND_TYPE: BACKEND_ESPHOME, CONF_ESPHOME_ENTRY_ID: "controller-two"}, {}
    ) == [owner]


def test_disabled_entry_still_reserves_controller(hass):
    owner = saved_entry(hass, BACKEND_YARDSTICK, disabled_by=ConfigEntryDisabler.USER)
    assert controller_assignments(hass, {CONF_BACKEND_TYPE: BACKEND_YARDSTICK}, {}) == [owner]


@pytest.mark.parametrize(
    "data",
    [{}, {CONF_BACKEND_TYPE: "unknown"}, {CONF_BACKEND_TYPE: BACKEND_ESPHOME}, {CONF_BACKEND_TYPE: BACKEND_FAKE}],
)
def test_no_physical_identity_for_invalid_or_simulated_backend(data):
    assert controller_assignment_key(data, {}) is None


@pytest.mark.parametrize("backend", [BACKEND_YARDSTICK, BACKEND_ESPHOME])
async def test_saved_duplicate_is_blocked_before_radio_or_registry_work(hass, monkeypatch, backend):
    owner = saved_entry(hass, backend)
    duplicate = saved_entry(hass, backend)
    setup = AsyncMock()
    migrate = AsyncMock()
    monkeypatch.setattr("custom_components.proflame2.runtime.async_setup_runtime_entry", setup)
    monkeypatch.setattr("custom_components.proflame2._async_migrate_registry_identity", migrate)
    with pytest.raises(ConfigEntryError, match="Controller already assigned"):
        await async_setup_entry(hass, duplicate)
    setup.assert_not_called()
    migrate.assert_not_called()
    assert hass.config_entries.async_entries(DOMAIN) == [owner, duplicate]


@pytest.mark.parametrize("backend", [BACKEND_YARDSTICK, BACKEND_ESPHOME])
@pytest.mark.parametrize("disabled_owner", [False, True])
async def test_one_enabled_assignment_can_still_start(hass, monkeypatch, backend, disabled_owner):
    first = saved_entry(hass, backend, disabled_by=ConfigEntryDisabler.USER if disabled_owner else None)
    second = saved_entry(hass, backend)
    selected = second if disabled_owner else first
    setup = AsyncMock(side_effect=RuntimeError("reached runtime setup"))
    monkeypatch.setattr("custom_components.proflame2.runtime.async_setup_runtime_entry", setup)
    monkeypatch.setattr("custom_components.proflame2._async_migrate_registry_identity", AsyncMock())
    with pytest.raises(RuntimeError, match="reached runtime setup"):
        await async_setup_entry(hass, selected)
    setup.assert_awaited_once_with(hass, selected)


async def test_independent_controllers_remain_available(hass):
    saved_entry(hass, BACKEND_ESPHOME)
    assert not controller_assignments(
        hass, {CONF_BACKEND_TYPE: BACKEND_ESPHOME, CONF_ESPHOME_ENTRY_ID: "controller-two"}, {}
    )
    assert not controller_assignments(hass, {CONF_BACKEND_TYPE: BACKEND_YARDSTICK}, {})


async def test_concurrent_manual_flows_cannot_both_claim_yardstick(hass, monkeypatch):
    import asyncio

    monkeypatch.setattr("custom_components.proflame2.async_setup_entry", AsyncMock(return_value=True))

    def data(remote):
        return {
            CONF_NAME: "Fireplace",
            CONF_BACKEND_TYPE: BACKEND_YARDSTICK,
            CONF_REMOTE_ID: remote,
            CONF_C1: "5",
            CONF_D1: "7",
            CONF_C2: "1",
            CONF_D2: "8",
        }

    results = await asyncio.gather(
        *(
            hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER}, data=data(remote))
            for remote in ("123456", "654321")
        )
    )
    assert sum(result["type"] is FlowResultType.CREATE_ENTRY for result in results) == 1
    assert sum(result.get("reason") == "controller_in_use" for result in results) == 1
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
