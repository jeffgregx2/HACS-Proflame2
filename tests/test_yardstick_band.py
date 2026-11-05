"""YardStick band configuration and HA device ownership, without RF hardware."""

import asyncio
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("homeassistant")
pytest.importorskip("pytest_homeassistant_custom_component")
pytestmark = pytest.mark.ha

from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from test_yardstick_extended import backend_with_rows, packet

from custom_components.proflame2.config_flow import Proflame2ConfigFlow
from custom_components.proflame2.const import (
    BACKEND_YARDSTICK,
    CONF_BACKEND_TYPE,
    CONF_C1,
    CONF_C2,
    CONF_D1,
    CONF_D2,
    CONF_REMOTE_ID,
    CONF_RF_BAND,
    DATA_YARDSTICK_LEARNING_FREQUENCY_HZ,
    DOMAIN,
)
from custom_components.proflame2.learning import async_create_learning_backend
from custom_components.proflame2.rf.band import yardstick_band
from custom_components.proflame2.rf.yardstick import YardStickBackend
from custom_components.proflame2.runtime import (
    async_get_runtime_entries,
    async_setup_runtime_entry,
    async_unload_runtime_entry,
)
from custom_components.proflame2.select import YardStickBandSelect
from custom_components.proflame2.services import _control_arbiter, async_stage_control_change
from custom_components.proflame2.yardstick_controller import async_set_yardstick_band


@pytest.fixture(autouse=True)
def enable_integrations(enable_custom_integrations):
    yield


def entry(hass, band=None):
    data = {
        CONF_BACKEND_TYPE: BACKEND_YARDSTICK,
        CONF_REMOTE_ID: 0x123456,
        CONF_C1: 5,
        CONF_D1: 7,
        CONF_C2: 1,
        CONF_D2: 8,
    }
    if band is not None:
        data[CONF_RF_BAND] = band
    config = MockConfigEntry(domain=DOMAIN, title="Fireplace", data=data)
    config.add_to_hass(hass)
    return config


@pytest.mark.parametrize("band,rx,tx", [("315", 315000000, 314973000), ("433", 433920000, 433920000)])
@pytest.mark.parametrize("worker", [False, True])
@pytest.mark.parametrize("extended", [False, True])
async def test_frequencies_reach_real_direct_and_worker_paths(band, rx, tx, worker, extended, remote_profile):
    original = packet()
    if not extended:
        original = packet(profile=remote_profile)
    backend, radio = backend_with_rows([original.transmission_plan.air_payload], worker)
    await backend.configure_frequencies(rx_frequency_hz=rx, tx_frequency_hz=tx)
    assert radio.sent == []
    assert radio.calls[-1][0] == "setModeIDLE"
    assert ("setFreq", (rx,)) in radio.calls
    radio.calls.clear()
    received = await backend.receive(timeout=0.01)
    assert received.frame == original.frame
    await backend.send(original)
    assert ("setFreq", (tx,)) in radio.calls
    assert radio.sent
    await backend.close()


@pytest.mark.parametrize("value", [None, "434", "", 315, True])
def test_invalid_bands_never_default(value):
    with pytest.raises(ValueError):
        yardstick_band(value)


@pytest.mark.parametrize("band,override", [("315", 433920000), ("433", 315000000)])
async def test_cross_band_learning_override_rejected_before_open(hass, monkeypatch, band, override):
    constructor = AsyncMock()
    monkeypatch.setattr("custom_components.proflame2.learning.YardStickBackend", constructor)
    hass.data.setdefault(DOMAIN, {})[DATA_YARDSTICK_LEARNING_FREQUENCY_HZ] = override
    with pytest.raises(ValueError, match="conflicts"):
        await async_create_learning_backend(hass, BACKEND_YARDSTICK, rf_band=band)
    constructor.assert_not_called()


@pytest.mark.parametrize("band,rx,tx", [("315", 315000000, 314973000), ("433", 433920000, 433920000)])
async def test_learning_backend_uses_selected_band(hass, monkeypatch, band, rx, tx):
    monkeypatch.setattr(YardStickBackend, "connect", AsyncMock())
    backend = await async_create_learning_backend(hass, BACKEND_YARDSTICK, rf_band=band)
    assert (backend._frequency_hz, backend._tx_frequency_hz) == (rx, tx)


@pytest.mark.parametrize("band", [None, "315", "433"])
async def test_entries_expose_only_fireplace_and_keep_identity(hass, band):
    config = entry(hass, band)
    original_data = dict(config.data)
    runtime = await async_setup_runtime_entry(hass, config)
    registry = dr.async_get(hass)
    devices = dr.async_entries_for_config_entry(registry, config.entry_id)
    assert len(devices) == 1
    fireplace = registry.async_get(runtime.device_id)
    assert fireplace.via_device_id is None
    assert runtime.controller_device_id is None
    assert runtime.controller_device_identifier is None
    assert runtime.rf_band == (band or "315")
    frequencies = yardstick_band(band or "315")
    assert runtime.backend._frequency_hz == frequencies.rx_frequency_hz
    assert runtime.backend._tx_frequency_hz == frequencies.tx_frequency_hz
    selector = YardStickBandSelect(config, runtime)
    assert selector.device_info["identifiers"] == fireplace.identifiers
    assert (DOMAIN, f"fireplace:{config.entry_id}") in fireplace.identifiers
    await async_unload_runtime_entry(hass, config)
    again = await async_setup_runtime_entry(hass, config)
    assert (again.device_id, again.controller_device_id) == (runtime.device_id, runtime.controller_device_id)
    assert len(dr.async_entries_for_config_entry(registry, config.entry_id)) == 1
    assert dict(config.data) == original_data
    await async_unload_runtime_entry(hass, config)


async def test_real_ha_setup_registers_selector_on_fireplace_without_controller_device(hass):
    config = entry(hass)
    assert await hass.config_entries.async_setup(config.entry_id)
    await hass.async_block_till_done()
    registry = dr.async_get(hass)
    entities = er.async_get(hass)
    selected = entities.async_get_entity_id("select", DOMAIN, f"{config.entry_id}_yardstick_rf_band")
    assert selected is not None
    selector = entities.async_get(selected)
    device = registry.async_get(selector.device_id)
    assert (DOMAIN, f"fireplace:{config.entry_id}") in device.identifiers
    assert len(dr.async_entries_for_config_entry(registry, config.entry_id)) == 1
    assert hass.states.get(selected).state == "315"
    runtime = async_get_runtime_entries(hass)[config.entry_id]
    runtime.backend, radio = backend_with_rows([])
    await hass.services.async_call("select", "select_option", {"entity_id": selected, "option": "433"}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(selected).state == "433"
    assert config.data[CONF_RF_BAND] == "433"
    assert not radio.sent
    await hass.config_entries.async_remove(config.entry_id)
    await hass.async_block_till_done()
    assert not dr.async_entries_for_config_entry(registry, config.entry_id)
    assert registry.async_get(device.id) is None


async def test_band_form_precedes_learning_and_validates_without_opening_radio(hass, monkeypatch):
    open_session = AsyncMock()
    monkeypatch.setattr("custom_components.proflame2.config_flow.async_start_learning_session", open_session)
    flow = Proflame2ConfigFlow()
    flow.hass = hass
    form = await flow.async_step_learn({"name": "Fireplace", CONF_BACKEND_TYPE: BACKEND_YARDSTICK})
    assert form["step_id"] == "yardstick_band"
    assert not hass.config_entries.async_entries(DOMAIN)
    open_session.assert_not_called()
    invalid = await flow.async_step_yardstick_band({CONF_RF_BAND: "434"})
    assert invalid["errors"] == {CONF_RF_BAND: "invalid_rf_band"}
    open_session.assert_not_called()


async def test_manual_setup_stores_band_and_needs_no_separate_controller_setup(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data={
            "name": "Fireplace",
            CONF_BACKEND_TYPE: BACKEND_YARDSTICK,
            CONF_REMOTE_ID: "123456",
            CONF_C1: "5",
            CONF_D1: "7",
            CONF_C2: "1",
            CONF_D2: "8",
        },
    )
    assert result["step_id"] == "yardstick_band"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_RF_BAND: "433"})
    assert result["step_id"] == "manual_features"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "optional_settings"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_RF_BAND] == "433"
    await hass.async_block_till_done()
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    assert len(dr.async_entries_for_config_entry(dr.async_get(hass), result["result"].entry_id)) == 1


async def test_feature_options_reload_preserves_controller_band_and_entity_ids(hass):
    config = entry(hass, "433")
    assert await hass.config_entries.async_setup(config.entry_id)
    await hass.async_block_till_done()
    entities = er.async_get(hass)
    before = {item.unique_id: item.entity_id for item in er.async_entries_for_config_entry(entities, config.entry_id)}
    devices = dr.async_get(hass)
    before_devices = {item.id for item in dr.async_entries_for_config_entry(devices, config.entry_id)}
    options = await hass.config_entries.options.async_init(config.entry_id)
    options = await hass.config_entries.options.async_configure(options["flow_id"], {"next_step_id": "features"})
    options = await hass.config_entries.options.async_configure(options["flow_id"], {"fan": False})
    assert options["step_id"] == "optional_settings"
    await hass.config_entries.options.async_configure(options["flow_id"], {})
    await hass.async_block_till_done()
    assert config.data[CONF_RF_BAND] == "433"
    after = {item.unique_id: item.entity_id for item in er.async_entries_for_config_entry(entities, config.entry_id)}
    assert all(after[unique_id] == entity_id for unique_id, entity_id in before.items())
    assert {item.id for item in dr.async_entries_for_config_entry(devices, config.entry_id)} == before_devices


async def test_band_change_waits_for_tx_fences_pending_controls_and_persists(hass):
    config = entry(hass)
    runtime = await async_setup_runtime_entry(hass, config)
    backend, radio = backend_with_rows([])
    runtime.backend = backend
    arbiter = _control_arbiter(hass, runtime)
    arbiter.readiness_unknown = True
    await arbiter.lock.acquire()
    await async_stage_control_change(hass, runtime, {"power": True})
    changing = asyncio.create_task(async_set_yardstick_band(hass, config, runtime, "433"))
    await asyncio.sleep(0)
    assert runtime.radio_configuration_in_progress
    assert runtime.control_intents.pending is None
    assert not radio.calls
    with pytest.raises(HomeAssistantError, match="changing"):
        await async_stage_control_change(hass, runtime, {"power": True})
    arbiter.lock.release()
    await changing
    assert runtime.rf_band == config.data[CONF_RF_BAND] == "433"
    assert radio.sent == []
    assert not runtime.radio_configuration_error
    assert not arbiter.readiness_unknown
    assert not runtime.refresh_schedule.suspended
    radio.calls.clear()
    await async_set_yardstick_band(hass, config, runtime, "433")
    assert not radio.calls
    await async_unload_runtime_entry(hass, config)


@pytest.mark.parametrize("worker", [False, True])
async def test_failure_keeps_saved_band_blocks_actions_and_allows_retry(hass, worker):
    config = entry(hass, "315")
    runtime = await async_setup_runtime_entry(hass, config)
    backend, radio = backend_with_rows([], worker)
    runtime.backend = backend
    original = radio.__getattr__("setFreq")
    radio.setFreq = lambda *_: (_ for _ in ()).throw(RuntimeError("configuration failed"))
    with pytest.raises(HomeAssistantError, match="Unable to configure YardStick RF band"):
        await async_set_yardstick_band(hass, config, runtime, "433")
    assert runtime.rf_band == config.data[CONF_RF_BAND] == "315"
    assert runtime.radio_configuration_error
    with pytest.raises(HomeAssistantError, match="configuration did not complete"):
        await async_stage_control_change(hass, runtime, {"power": True})
    with pytest.raises(RuntimeError, match="configuration failed"):
        await backend.send(packet())
    radio.setFreq = original
    await async_set_yardstick_band(hass, config, runtime, "433")
    assert runtime.rf_band == "433"
    assert not runtime.radio_configuration_error
    await async_unload_runtime_entry(hass, config)


async def test_learning_lease_prevents_retune(hass):
    config = entry(hass)
    runtime = await async_setup_runtime_entry(hass, config)
    arbiter = _control_arbiter(hass, runtime)
    arbiter.learning_owner = object()
    with pytest.raises(HomeAssistantError, match="learning"):
        await async_set_yardstick_band(hass, config, runtime, "433")
    assert not runtime.radio_configuration_in_progress
    assert CONF_RF_BAND not in config.data
    arbiter.learning_owner = None
    await async_unload_runtime_entry(hass, config)


async def test_cancelled_configuration_retains_ownership_until_native_call_finishes(hass):
    from threading import Event

    config = entry(hass, "315")
    runtime = await async_setup_runtime_entry(hass, config)
    backend, radio = backend_with_rows([])
    runtime.backend = backend
    entered = Event()
    release = Event()
    original = radio.__getattr__("setFreq")

    def set_frequency(value):
        entered.set()
        if not release.wait(2):
            raise RuntimeError("Test did not release its fake radio")
        original(value)

    radio.setFreq = set_frequency
    changing = asyncio.create_task(async_set_yardstick_band(hass, config, runtime, "433"))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        changing.cancel()
        await asyncio.sleep(0)
        assert not changing.done()
        assert backend._operation_lock.locked()
        assert _control_arbiter(hass, runtime).lock.locked()
        assert config.data[CONF_RF_BAND] == "315"
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await changing
    assert runtime.radio_configuration_error
    assert not backend._operation_lock.locked()
    await async_set_yardstick_band(hass, config, runtime, "315")
    assert not runtime.radio_configuration_error
    await async_unload_runtime_entry(hass, config)


async def test_band_change_ends_old_confirmation_task(hass):
    config = entry(hass)
    runtime = await async_setup_runtime_entry(hass, config)
    backend, _ = backend_with_rows([])
    runtime.backend = backend
    receiving = asyncio.create_task(asyncio.sleep(10))
    runtime.confirmation_task = receiving
    await async_set_yardstick_band(hass, config, runtime, "433")
    assert receiving.cancelled()
    assert runtime.confirmation_task is None
    await async_unload_runtime_entry(hass, config)
