"""Concurrency and failure policy for one replaceable full-state control intent."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

pytest.importorskip("homeassistant")
pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.proflame2 import services
from custom_components.proflame2.const import (
    BACKEND_ESPHOME,
    BACKEND_FAKE,
    CONF_BACKEND_TYPE,
    CONF_C1,
    CONF_C2,
    CONF_D1,
    CONF_D2,
    CONF_FAN,
    CONF_LIGHT,
    CONF_REMOTE_ID,
    DATA_CONFIRMATION_WINDOW_SECONDS,
    DATA_CONTROL_DEBOUNCE_SECONDS,
    DOMAIN,
    STATE_CONFIDENCE_OBSERVED,
)
from custom_components.proflame2.protocol.encoder import encode_packet
from custom_components.proflame2.protocol.models import FireplaceState
from custom_components.proflame2.runtime import (
    async_get_runtime_entries,
    async_set_runtime_current_state,
    runtime_current_state,
)

pytestmark = pytest.mark.ha


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    yield


async def ready(hass, *, name="Test Fireplace"):
    hass.data.setdefault(DOMAIN, {})[DATA_CONTROL_DEBOUNCE_SECONDS] = 0
    hass.data[DOMAIN][DATA_CONFIRMATION_WINDOW_SECONDS] = 0
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=name,
        data={
            "name": name,
            CONF_BACKEND_TYPE: BACKEND_FAKE,
            CONF_REMOTE_ID: 0x123456,
            CONF_C1: 5,
            CONF_D1: 7,
            CONF_C2: 1,
            CONF_D2: 8,
        },
        options={CONF_FAN: True, CONF_LIGHT: True},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    runtime = async_get_runtime_entries(hass)[entry.entry_id]
    await async_set_runtime_current_state(
        hass, runtime, FireplaceState(power=True, flame=1), source="test", confidence=STATE_CONFIDENCE_OBSERVED
    )
    return entry, runtime


async def wait_for(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0)


async def test_default_debounce_is_one_second_after_the_latest_edit(hass):
    _, runtime = await ready(hass)
    hass.data[DOMAIN].pop(DATA_CONTROL_DEBOUNCE_SECONDS)
    loop = asyncio.get_running_loop()
    before = loop.time()
    await services.async_stage_control_change(hass, runtime, {"flame": 3})
    after = loop.time()
    first = runtime.control_intents.pending
    assert first is not None
    assert before + 1.0 <= first.eligible_at <= after + 1.0
    before = loop.time()
    await services.async_stage_control_change(hass, runtime, {"light": 6})
    after = loop.time()
    pending = runtime.control_intents.pending
    assert pending is not None
    assert before + 1.0 <= pending.eligible_at <= after + 1.0
    assert pending.eligible_at >= first.eligible_at
    assert pending.state == FireplaceState(power=True, flame=3, light=6)
    await services.async_stage_control_change(hass, runtime, {"flame": 1, "light": 0})
    await wait_for(lambda: runtime.control_task is None)
    assert not runtime.backend.sent_packets


def held_send(monkeypatch, runtime, *, fail_at=None):
    original = runtime.backend.send
    states = []
    release = asyncio.Event()

    async def send(packet):
        states.append(packet.state)
        if len(states) == 1:
            await release.wait()
        if len(states) == fail_at:
            raise RuntimeError("test controller failure")
        return await original(packet)

    monkeypatch.setattr(runtime.backend, "send", send)
    return states, release


@pytest.mark.parametrize("fail_at", [1, 2])
async def test_failed_send_discards_pending_and_publishes_rollback(hass, monkeypatch, fail_at):
    _, runtime = await ready(hass)
    states, release = held_send(monkeypatch, runtime, fail_at=fail_at)
    await services.async_stage_control_change(hass, runtime, {"flame": 3})
    await wait_for(lambda: len(states) == 1)
    await services.async_stage_control_change(hass, runtime, {"light": 6})
    assert runtime.desired_state.light == 6
    release.set()
    await wait_for(lambda: runtime.control_task is None)
    await hass.async_block_till_done()
    assert runtime_current_state(runtime).flame == (1 if fail_at == 1 else 3)
    assert runtime_current_state(runtime).light == 0
    assert len(states) == fail_at
    assert runtime.desired_state is None
    assert runtime.operational_status == "failed"
    assert not runtime.sending_in_progress
    assert hass.states.get("number.test_fireplace_light").state == "0"


async def test_native_observation_does_not_erase_pending_intent(hass, monkeypatch):
    _, runtime = await ready(hass)
    states, release = held_send(monkeypatch, runtime)
    await services.async_stage_control_change(hass, runtime, {"flame": 3})
    await wait_for(lambda: bool(states))
    await services.async_stage_control_change(hass, runtime, {"light": 6})
    observed = encode_packet(FireplaceState(power=True, flame=2, fan=4), runtime.remote_profile)
    await services.async_apply_observed_packet(hass, runtime, observed)
    assert runtime_current_state(runtime) == observed.state
    assert runtime.desired_state == FireplaceState(power=True, flame=3, light=6)
    assert runtime.sending_in_progress
    assert runtime.operational_status == "sending"
    release.set()
    await wait_for(lambda: runtime.control_task is None)
    assert len(states) == 2
    assert runtime_current_state(runtime) == states[-1]
    # Normal RX wins again once local work has finished.
    await services.async_apply_observed_packet(hass, runtime, observed)
    assert runtime_current_state(runtime) == observed.state
    assert runtime.operational_status == "ready"


async def test_failure_rolls_back_to_newer_native_observation(hass, monkeypatch):
    _, runtime = await ready(hass)
    states, release = held_send(monkeypatch, runtime, fail_at=1)
    await services.async_stage_control_change(hass, runtime, {"flame": 3})
    await wait_for(lambda: bool(states))
    await services.async_stage_control_change(hass, runtime, {"light": 6})
    observed = encode_packet(FireplaceState(power=True, flame=2, fan=4), runtime.remote_profile)
    await services.async_apply_observed_packet(hass, runtime, observed)
    release.set()
    await wait_for(lambda: runtime.control_task is None)
    assert runtime_current_state(runtime) == observed.state
    assert runtime.desired_state is None


@pytest.mark.parametrize("late_failure", [False, True])
async def test_disconnect_fences_late_results_and_never_replays(hass, monkeypatch, late_failure):
    _, runtime = await ready(hass)
    states, release = held_send(monkeypatch, runtime, fail_at=1 if late_failure else None)
    baseline = runtime_current_state(runtime)
    await services.async_stage_control_change(hass, runtime, {"flame": 3})
    await wait_for(lambda: bool(states))
    await services.async_stage_control_change(hass, runtime, {"light": 6})
    services.async_controller_connection_lost(hass, runtime)
    assert runtime.desired_state is None
    assert runtime.operational_status == "failed"
    release.set()
    await wait_for(lambda: runtime.control_task is None)
    assert runtime_current_state(runtime) == baseline
    assert len(states) == 1
    runtime.backend.connected = True
    await asyncio.sleep(0)
    assert len(states) == 1
    # A deliberate new edit, not reconnection alone, starts another sequence.
    await services.async_stage_control_change(hass, runtime, {"fan": 4})
    await wait_for(lambda: runtime.control_task is None)
    assert len(states) == 2
    assert runtime_current_state(runtime).fan == 4


async def test_pending_explicit_service_reports_superseded_not_success(hass, monkeypatch):
    _, runtime = await ready(hass)
    states, release = held_send(monkeypatch, runtime)
    await services.async_stage_control_change(hass, runtime, {"flame": 3})
    await wait_for(lambda: bool(states))
    caller = asyncio.create_task(
        services.async_execute_set_state(hass, runtime, {"power": True, "flame": 4}, source="service")
    )
    await wait_for(lambda: runtime.control_intents.pending is not None)
    await services.async_stage_control_change(hass, runtime, {"light": 6})
    with pytest.raises(HomeAssistantError, match="superseded"):
        await caller
    release.set()
    await wait_for(lambda: runtime.control_task is None)
    assert states[-1].flame == 4 and states[-1].light == 6


@pytest.mark.parametrize("shared", [False, True])
async def test_physical_controller_serialization(hass, monkeypatch, shared):
    _, first = await ready(hass, name="First Fireplace")
    _, second = await ready(hass, name="Second Fireplace")
    first.controller_id = "test:one"
    second.controller_id = "test:one" if shared else "test:two"
    states, release = held_send(monkeypatch, first)
    await services.async_stage_control_change(hass, first, {"flame": 3})
    await wait_for(lambda: bool(states))
    await services.async_stage_control_change(hass, second, {"flame": 4})
    if shared:
        await asyncio.sleep(0.01)
        assert not second.backend.sent_packets
        # Replacement while waiting for the controller must not send obsolete state.
        await services.async_stage_control_change(hass, second, {"light": 6})
    else:
        await wait_for(lambda: bool(second.backend.sent_packets))
    release.set()
    await wait_for(lambda: first.control_task is None and second.control_task is None)
    assert len(second.backend.sent_packets) == 1
    assert second.backend.sent_packets[0].state.flame == 4
    assert second.backend.sent_packets[0].state.light == (6 if shared else 0)


async def test_debounce_expires_while_original_send_is_in_flight(hass, monkeypatch):
    _, runtime = await ready(hass)
    states, release = held_send(monkeypatch, runtime)
    await services.async_stage_control_change(hass, runtime, {"flame": 3})
    await wait_for(lambda: bool(states))
    hass.data[DOMAIN][DATA_CONTROL_DEBOUNCE_SECONDS] = 30
    await services.async_stage_control_change(hass, runtime, {"light": 6})
    # Move the monotonic deadline into the past, without a wall-clock wait.
    runtime.control_intents.pending = replace(runtime.control_intents.pending, eligible_at=0)
    release.set()
    await wait_for(lambda: runtime.control_task is None)
    assert len(states) == 2


async def test_unload_abandons_pending_without_replay(hass, monkeypatch):
    entry, runtime = await ready(hass)
    states, release = held_send(monkeypatch, runtime)
    await services.async_stage_control_change(hass, runtime, {"flame": 3})
    await wait_for(lambda: bool(states))
    await services.async_stage_control_change(hass, runtime, {"light": 6})
    assert await hass.config_entries.async_unload(entry.entry_id)
    release.set()
    assert runtime.control_intents.pending is None
    assert runtime.control_intents.in_flight is None
    assert runtime.desired_state is None
    assert len(states) == 1


async def test_post_tx_persistence_failure_does_not_discard_newer_intent(hass, monkeypatch):
    _, runtime = await ready(hass)
    states, release = held_send(monkeypatch, runtime)

    async def broken_persist(*_args, **_kwargs):
        raise OSError("test storage unavailable")

    monkeypatch.setattr("custom_components.proflame2.runtime.async_persist_runtime_entry_state", broken_persist)
    await services.async_stage_control_change(hass, runtime, {"flame": 3})
    await wait_for(lambda: bool(states))
    await services.async_stage_control_change(hass, runtime, {"light": 6})
    release.set()
    await wait_for(lambda: runtime.control_task is None)
    assert len(states) == 2
    assert runtime_current_state(runtime).light == 6
    assert runtime.operational_status == "ready"
    assert "Transmit completed" in runtime.last_error


async def test_timeout_reverts_and_requires_explicit_readiness(hass, monkeypatch):
    _, runtime = await ready(hass)
    baseline = runtime_current_state(runtime)
    states, release = held_send(monkeypatch, runtime)
    monkeypatch.setattr(services, "BACKEND_SEND_TIMEOUT_SECONDS", 0.02)
    await services.async_stage_control_change(hass, runtime, {"flame": 3})
    await wait_for(lambda: bool(states))
    await services.async_stage_control_change(hass, runtime, {"light": 6})
    await wait_for(lambda: runtime.control_task is None)
    assert runtime_current_state(runtime) == baseline
    assert runtime.desired_state is None
    assert services._control_arbiter(hass, runtime).readiness_unknown
    runtime.backend.connected = False
    await services.async_stage_control_change(hass, runtime, {"fan": 4})
    await wait_for(lambda: runtime.control_task is None)
    assert len(states) == 1
    assert "readiness" in runtime.last_error
    runtime.backend.connected = True
    release.set()
    await services.async_stage_control_change(hass, runtime, {"fan": 4})
    await wait_for(lambda: runtime.control_task is None)
    assert len(states) == 2


async def test_cancel_pending_then_immediately_stage_again(hass):
    _, runtime = await ready(hass)
    hass.data[DOMAIN][DATA_CONTROL_DEBOUNCE_SECONDS] = 30
    await services.async_stage_control_change(hass, runtime, {"flame": 3})
    await services.async_stage_control_change(hass, runtime, {"flame": 1})
    assert runtime.desired_state is None
    hass.data[DOMAIN][DATA_CONTROL_DEBOUNCE_SECONDS] = 0
    await services.async_stage_control_change(hass, runtime, {"light": 6})
    await wait_for(lambda: runtime.control_task is None)
    assert len(runtime.backend.sent_packets) == 1
    assert runtime_current_state(runtime).light == 6


async def test_late_completion_after_disconnect_cannot_erase_new_user_edit(hass, monkeypatch):
    _, runtime = await ready(hass)
    states, release = held_send(monkeypatch, runtime)
    await services.async_stage_control_change(hass, runtime, {"flame": 3})
    await wait_for(lambda: bool(states))
    services.async_controller_connection_lost(hass, runtime)
    runtime.backend.connected = True
    await services.async_stage_control_change(hass, runtime, {"light": 6})
    release.set()
    await wait_for(lambda: runtime.control_task is None)
    assert len(states) == 2
    assert runtime_current_state(runtime) == FireplaceState(power=True, flame=1, light=6)


@pytest.mark.parametrize("discard_pending", [False, True])
async def test_pending_intent_does_not_cancel_controller_retry(hass, monkeypatch, discard_pending):
    from unittest.mock import AsyncMock

    _, runtime = await ready(hass)
    states, release = held_send(monkeypatch, runtime)
    cancel = AsyncMock()
    monkeypatch.setattr(runtime.backend, "cancel_tx_retry", cancel, raising=False)
    monkeypatch.setattr(runtime.backend, "latest_request_id", "original", raising=False)
    await services.async_stage_control_change(hass, runtime, {"flame": 3})
    await wait_for(lambda: bool(states))
    hass.data[DOMAIN][DATA_CONTROL_DEBOUNCE_SECONDS] = 30
    await services.async_stage_control_change(hass, runtime, {"light": 6})
    release.set()
    await wait_for(lambda: runtime.control_intents.in_flight is None)
    cancel.assert_not_called()
    assert len(states) == 1
    assert runtime_current_state(runtime).flame == 3
    if discard_pending:
        services.async_controller_connection_lost(hass, runtime)
    else:
        runtime.control_intents.pending = replace(runtime.control_intents.pending, eligible_at=0)
        runtime.control_intents.wake.set()
    await wait_for(lambda: runtime.control_task is None)
    cancel.assert_not_called()
    assert len(states) == (1 if discard_pending else 2)


async def test_display_sync_does_not_publish_pending_settings_to_existing_firmware(hass, monkeypatch):
    from unittest.mock import AsyncMock

    _, runtime = await ready(hass)
    update = AsyncMock()
    monkeypatch.setattr(runtime.backend, "update_display_state", update, raising=False)
    hass.data[DOMAIN][DATA_CONTROL_DEBOUNCE_SECONDS] = 30
    await services.async_stage_control_change(hass, runtime, {"light": 6})
    await services._async_push_runtime_display_state(runtime, force=True)
    update.assert_not_called()
    await services.async_stage_control_change(hass, runtime, {"light": 0})
    await wait_for(lambda: runtime.control_task is None)
    await services._async_push_runtime_display_state(runtime, force=True)
    update.assert_awaited_once()
    assert update.call_args.args[0].light == 0


async def test_learning_and_control_cannot_share_an_active_radio(hass, monkeypatch):
    from custom_components.proflame2 import learning

    _, runtime = await ready(hass)
    runtime.controller_id = "esphome:test-controller"
    backend = runtime.backend

    async def open_backend(*args, **kwargs):
        return backend

    monkeypatch.setattr(learning, "async_create_learning_backend", open_backend)
    hass.data[DOMAIN][DATA_CONTROL_DEBOUNCE_SECONDS] = 30
    await services.async_stage_control_change(hass, runtime, {"flame": 3})
    with pytest.raises(RuntimeError, match="busy"):
        await learning.async_start_learning_session(hass, BACKEND_ESPHOME, esphome_entry_id="test-controller")
    await services.async_stage_control_change(hass, runtime, {"flame": 1})
    await wait_for(lambda: runtime.control_task is None)
    session = await learning.async_start_learning_session(hass, BACKEND_ESPHOME, esphome_entry_id="test-controller")
    assert runtime.learning_in_progress
    with pytest.raises(HomeAssistantError, match="learning"):
        await services.async_stage_control_change(hass, runtime, {"light": 6})
    with pytest.raises(RuntimeError, match="busy"):
        await learning.async_start_learning_session(hass, BACKEND_ESPHOME, esphome_entry_id="test-controller")
    await learning.async_close_learning_session(session)
    assert not runtime.learning_in_progress
    assert services._control_arbiter(hass, runtime).learning_owner is None


async def test_failed_learning_setup_releases_controller(hass, monkeypatch):
    from custom_components.proflame2 import learning

    _, runtime = await ready(hass)
    runtime.controller_id = "esphome:test-controller"

    async def fail(*args, **kwargs):
        raise RuntimeError("test open failure")

    monkeypatch.setattr(learning, "async_create_learning_backend", fail)
    with pytest.raises(RuntimeError, match="open failure"):
        await learning.async_start_learning_session(hass, BACKEND_ESPHOME, esphome_entry_id="test-controller")
    assert not runtime.learning_in_progress
    assert services._control_arbiter(hass, runtime).learning_owner is None


async def test_public_endpoint_unavailability_discards_pending_intent(hass):
    from homeassistant.helpers import entity_registry as er

    _, runtime = await ready(hass)
    linked = MockConfigEntry(domain="esphome", title="Test Radio")
    linked.add_to_hass(hass)
    endpoint = er.async_get(hass).async_get_or_create(
        "sensor", "esphome", "test-radio-proflame2_endpoint_status", config_entry=linked
    )
    hass.states.async_set(endpoint.entity_id, "ready")
    runtime.backend_type = BACKEND_ESPHOME
    runtime.controller_id = f"esphome:{linked.entry_id}"
    services.async_observe_controller_connection(hass, runtime)
    hass.data[DOMAIN][DATA_CONTROL_DEBOUNCE_SECONDS] = 30
    await services.async_stage_control_change(hass, runtime, {"light": 6})
    hass.states.async_set(endpoint.entity_id, "unavailable")
    await wait_for(lambda: runtime.control_task is None)
    assert runtime.desired_state is None
    assert runtime.operational_status == "failed"
    assert not runtime.backend.sent_packets
    hass.states.async_set(endpoint.entity_id, "ready")
    await hass.async_block_till_done()
    assert not runtime.backend.sent_packets


async def test_edit_during_completion_persistence_is_not_cleared(hass, monkeypatch):
    from custom_components.proflame2 import runtime as runtime_module

    _, runtime = await ready(hass)
    original = runtime_module.async_persist_runtime_entry_state
    entered, release = asyncio.Event(), asyncio.Event()
    count = 0

    async def persist(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 1:
            entered.set()
            await release.wait()
        await original(*args, **kwargs)

    monkeypatch.setattr(runtime_module, "async_persist_runtime_entry_state", persist)
    await services.async_stage_control_change(hass, runtime, {"flame": 3})
    await entered.wait()
    await services.async_stage_control_change(hass, runtime, {"light": 6})
    release.set()
    await wait_for(lambda: runtime.control_task is None)
    assert len(runtime.backend.sent_packets) == 2
    assert runtime_current_state(runtime) == FireplaceState(power=True, flame=3, light=6)


async def test_shared_controller_quarantine_carries_the_uncertain_request(hass, monkeypatch):
    _, first = await ready(hass, name="First Fireplace")
    _, second = await ready(hass, name="Second Fireplace")
    first.controller_id = second.controller_id = "test:shared"
    monkeypatch.setattr(first.backend, "latest_request_id", "uncertain-request", raising=False)
    states, _ = held_send(monkeypatch, first)
    monkeypatch.setattr(services, "BACKEND_SEND_TIMEOUT_SECONDS", 0.02)
    await services.async_stage_control_change(hass, first, {"flame": 3})
    await wait_for(lambda: first.control_task is None)
    recovered = []

    async def recover(request_id):
        recovered.append(request_id)
        return False

    monkeypatch.setattr(second.backend, "recover_send_readiness", recover)
    await services.async_stage_control_change(hass, second, {"light": 6})
    await wait_for(lambda: second.control_task is None)
    assert recovered == ["uncertain-request"]
    assert len(states) == 1
    assert not second.backend.sent_packets
