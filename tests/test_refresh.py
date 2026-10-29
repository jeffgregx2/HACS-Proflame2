"""ON-only inactivity refresh through real HA setup and command dispatch."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("homeassistant")
pytest.importorskip("pytest_homeassistant_custom_component")

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.proflame2 import refresh, services
from custom_components.proflame2.const import (
    BACKEND_FAKE,
    CONF_BACKEND_TYPE,
    CONF_C1,
    CONF_C2,
    CONF_D1,
    CONF_D2,
    CONF_FAN,
    CONF_LIGHT,
    CONF_REFRESH_PERIOD_MINUTES,
    CONF_REMOTE_ID,
    DATA_CONFIRMATION_WINDOW_SECONDS,
    DATA_CONTROL_DEBOUNCE_SECONDS,
    DOMAIN,
    STATE_CONFIDENCE_OBSERVED,
)
from custom_components.proflame2.profile import normalize_entry_options
from custom_components.proflame2.protocol.encoder import encode_packet
from custom_components.proflame2.protocol.models import FireplaceState
from custom_components.proflame2.refresh import RefreshSchedule
from custom_components.proflame2.runtime import (
    async_get_runtime_entries,
    async_handle_homeassistant_stop,
    async_set_runtime_current_state,
)

pytestmark = pytest.mark.ha


class Clock:
    """Advance only this feature's monotonic clock and deadline waits."""

    def __init__(self):
        self.now = 10000.0
        self.waits = asyncio.Queue()

    async def wait_until(self, deadline):
        release = asyncio.Event()
        await self.waits.put((deadline, release))
        await release.wait()

    async def wake_at(self, now):
        deadline, release = await asyncio.wait_for(self.waits.get(), 2)
        self.now = now
        release.set()
        return deadline

    async def next_deadline(self):
        item = await asyncio.wait_for(self.waits.get(), 2)
        self.waits.put_nowait(item)
        return item[0]


@pytest.fixture(autouse=True)
def enable_integration(enable_custom_integrations):
    yield


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(refresh, "monotonic", lambda: clock.now)
    monkeypatch.setattr(refresh, "_async_wait_until", clock.wait_until)
    return clock


async def setup(hass, *, period=10, name="Refresh Fireplace", options=None):
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
        options={CONF_FAN: True, CONF_LIGHT: True, CONF_REFRESH_PERIOD_MINUTES: period, **(options or {})},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    return entry, async_get_runtime_entries(hass)[entry.entry_id]


async def wait_for(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0)


@pytest.mark.parametrize("value", [-1, 0.5, 10.5, True, False, "10", "invalid", None])
def test_invalid_period_is_not_coerced(value):
    with pytest.raises(ValueError):
        normalize_entry_options({CONF_REFRESH_PERIOD_MINUTES: value})


@pytest.mark.parametrize("value", [0, 1, 10, 60, 120])
def test_valid_period_and_legacy_default(value):
    assert normalize_entry_options({CONF_REFRESH_PERIOD_MINUTES: value})[CONF_REFRESH_PERIOD_MINUTES] == value
    assert normalize_entry_options({})[CONF_REFRESH_PERIOD_MINUTES] == 0


def test_deadlines_and_failure_pacing_are_separate():
    schedule = RefreshSchedule(period_seconds=600)
    schedule.restart(100)
    assert not schedule.due(699)
    assert schedule.due(700)
    schedule.pace_attempt(710)
    assert schedule.activity_deadline == 700
    assert not schedule.due(1309)
    schedule.record_activity(1000)
    assert schedule.deadline == 1600
    schedule.suspended = True
    assert not schedule.due(2000)
    schedule.suspended = False
    schedule.restart(2000)
    assert schedule.deadline == 2600


async def test_disabled_creates_no_scheduler_or_refresh(hass, clock):
    _, runtime = await setup(hass, period=0)
    assert runtime.refresh_task is None
    clock.now += 3600
    await services.async_request_refresh(hass, runtime)
    assert not runtime.backend.sent_packets


async def test_scheduler_sends_once_when_due_and_preserves_profile(hass, clock):
    _, runtime = await setup(hass)
    runtime.last_applied_profile_id = "evening"
    runtime.last_applied_profile_name = "Evening"
    runtime.active_profile_state = runtime.last_packet.state
    assert await clock.next_deadline() == 10600
    await clock.wake_at(10599)
    assert await clock.next_deadline() == 10600
    assert not runtime.backend.sent_packets
    await clock.wake_at(10600)
    assert await clock.next_deadline() == 11200
    assert len(runtime.backend.sent_packets) == 1
    assert runtime.backend.sent_packets[0].source == refresh.SOURCE_REFRESH
    assert runtime.last_applied_profile_id == "evening"
    assert runtime.last_applied_profile_name == "Evening"
    assert runtime.active_profile_state == runtime.last_packet.state
    assert runtime.last_send_result is not None
    await clock.wake_at(11200)
    assert await clock.next_deadline() == 11800
    assert len(runtime.backend.sent_packets) == 2


async def test_normal_tx_resets_from_completion_not_submission(hass, clock, monkeypatch):
    _, runtime = await setup(hass)
    original = runtime.backend.send
    entered, release = asyncio.Event(), asyncio.Event()

    async def held_send(packet):
        entered.set()
        await release.wait()
        return await original(packet)

    monkeypatch.setattr(runtime.backend, "send", held_send)
    clock.now = 10300
    send = asyncio.create_task(
        services.async_execute_set_state(hass, runtime, {"power": True, "flame": 3}, source="test")
    )
    await entered.wait()
    assert runtime.refresh_schedule.activity_deadline == 10600
    clock.now = 10400
    release.set()
    await send
    assert runtime.refresh_schedule.activity_deadline == 11000
    await clock.wake_at(10600)
    assert await clock.next_deadline() == 11000
    assert len(runtime.backend.sent_packets) == 1


@pytest.mark.parametrize("flame", [1, 4])
async def test_matching_rx_restarts_even_when_unchanged(hass, clock, flame):
    _, runtime = await setup(hass)
    runtime.state_confidence = STATE_CONFIDENCE_OBSERVED
    packet = encode_packet(FireplaceState(power=True, flame=flame), runtime.remote_profile)
    clock.now = 10500
    await services.async_apply_observed_packet(hass, runtime, packet)
    assert runtime.refresh_schedule.activity_deadline == 11100
    await clock.wake_at(10600)
    assert await clock.next_deadline() == 11100
    assert not runtime.backend.sent_packets
    assert runtime.last_packet.state.flame == flame


@pytest.mark.parametrize("invalid", ["identity", "integrity"])
async def test_invalid_rx_does_not_postpone(hass, clock, invalid):
    _, runtime = await setup(hass)
    profile = runtime.remote_profile
    packet = encode_packet(FireplaceState(power=True, flame=1), profile)
    if invalid == "identity":
        packet = encode_packet(packet.state, replace(profile, serial_id=0x654321))
    else:
        packet.frame = replace(packet.frame, err1=packet.frame.err1 ^ 1)
    clock.now = 10500
    refresh.record_received_activity(runtime, packet)
    assert runtime.refresh_schedule.activity_deadline == 10600


async def test_off_rx_prevents_keep_alive(hass, clock):
    _, runtime = await setup(hass)
    clock.now = 10500
    off = encode_packet(FireplaceState(power=False, flame=0), runtime.remote_profile)
    await services.async_apply_observed_packet(hass, runtime, off)
    await clock.wake_at(11100)
    assert await clock.next_deadline() == 11700
    assert not runtime.backend.sent_packets
    assert runtime.last_packet.state.power is False
    # A later ordinary ON command starts its own full interval.
    clock.now = 11200
    await services.async_execute_set_state(hass, runtime, {"power": True, "flame": 2}, source="test")
    await clock.wake_at(11700)
    assert await clock.next_deadline() == 11800
    assert len(runtime.backend.sent_packets) == 1


@pytest.mark.parametrize(
    "state",
    [
        None,
        FireplaceState(power=False, flame=0),
        FireplaceState(power=True, flame=0),
        FireplaceState(power=True, flame=1, thermostat=True),
    ],
)
async def test_no_packet_for_missing_off_or_unsupported_state(hass, clock, state):
    _, runtime = await setup(hass)
    if state is None:
        runtime.last_packet = None
    else:
        runtime.last_packet.state = state
    await clock.wake_at(10600)
    assert await clock.next_deadline() == 11200
    assert not runtime.backend.sent_packets
    assert runtime.last_error is None


async def test_pending_user_edit_takes_priority(hass, clock):
    _, runtime = await setup(hass)
    hass.data[DOMAIN][DATA_CONTROL_DEBOUNCE_SECONDS] = 100
    await services.async_stage_control_change(hass, runtime, {"flame": 4})
    pending = runtime.control_intents.pending
    clock.now = 10600
    await services.async_request_refresh(hass, runtime)
    assert runtime.control_intents.pending is pending
    assert not runtime.backend.sent_packets


@pytest.mark.parametrize("change", ["off", "rx", "latest_state"])
async def test_recheck_after_waiting_for_radio(hass, clock, change):
    _, runtime = await setup(hass)
    arbiter = services._control_arbiter(hass, runtime)
    await arbiter.lock.acquire()
    clock.now = 10600
    request = asyncio.create_task(services.async_request_refresh(hass, runtime))
    await wait_for(lambda: runtime.control_intents.pending is not None)
    assert not runtime.backend.sent_packets
    if change in ("off", "rx"):
        state = FireplaceState(power=change != "off", flame=0 if change == "off" else 1)
        await services.async_apply_observed_packet(hass, runtime, encode_packet(state, runtime.remote_profile))
    else:
        await async_set_runtime_current_state(
            hass,
            runtime,
            FireplaceState(power=True, flame=5, light=3),
            source="test",
            confidence=STATE_CONFIDENCE_OBSERVED,
        )
    arbiter.lock.release()
    await request
    if change == "latest_state":
        assert len(runtime.backend.sent_packets) == 1
        assert runtime.backend.sent_packets[0].state == FireplaceState(power=True, flame=5, light=3)
    else:
        assert not runtime.backend.sent_packets


async def test_off_during_rx_handoff_skips_without_error(hass, clock, monkeypatch):
    _, runtime = await setup(hass)
    runtime.active_listening_enabled = True
    restore_rx = AsyncMock()
    monkeypatch.setattr(runtime.backend, "set_active_listening_enabled", restore_rx, raising=False)
    entered, release = asyncio.Event(), asyncio.Event()

    async def stop_rx():
        entered.set()
        await release.wait()

    monkeypatch.setattr(runtime.backend, "stop_rx", stop_rx, raising=False)
    clock.now = 10600
    request = asyncio.create_task(services.async_request_refresh(hass, runtime))
    await entered.wait()
    off = encode_packet(FireplaceState(power=False, flame=0), runtime.remote_profile)
    await services.async_apply_observed_packet(hass, runtime, off)
    release.set()
    await request
    assert not runtime.backend.sent_packets
    assert runtime.last_packet.state.power is False
    assert runtime.last_error is None
    assert runtime.operational_status == "ready"
    restore_rx.assert_awaited_with(True, runtime.remote_profile)


@pytest.mark.parametrize("power", [False, True])
async def test_rx_during_refresh_tx_remains_current_after_completion(hass, clock, monkeypatch, power):
    _, runtime = await setup(hass)
    entered, release = asyncio.Event(), asyncio.Event()
    original = runtime.backend.send

    async def send(packet):
        entered.set()
        await release.wait()
        return await original(packet)

    monkeypatch.setattr(runtime.backend, "send", send)
    clock.now = 10600
    request = asyncio.create_task(services.async_request_refresh(hass, runtime))
    await entered.wait()
    observed = encode_packet(FireplaceState(power=power, flame=4 if power else 0), runtime.remote_profile)
    clock.now = 10601
    await services.async_apply_observed_packet(hass, runtime, observed)
    release.set()
    await request
    assert len(runtime.backend.sent_packets) == 1
    assert runtime.last_packet is observed
    assert runtime.state_confidence == STATE_CONFIDENCE_OBSERVED
    assert runtime.confirmation_task is None
    assert runtime.last_send_result is not None
    clock.now = 11201
    await services.async_request_refresh(hass, runtime)
    assert len(runtime.backend.sent_packets) == (2 if power else 1)
    if power:
        assert runtime.backend.sent_packets[-1].state == observed.state


@pytest.mark.parametrize("failure", [RuntimeError("radio unavailable"), TimeoutError()])
async def test_failed_refresh_is_paced_and_does_not_claim_activity(hass, clock, monkeypatch, failure):
    _, runtime = await setup(hass)
    send = AsyncMock(side_effect=failure)
    monkeypatch.setattr(runtime.backend, "send", send)
    await clock.wake_at(10600)
    assert await clock.next_deadline() == 11200
    assert runtime.refresh_schedule.activity_deadline == 10600
    assert send.await_count == 1
    assert runtime.last_error is not None
    assert runtime.last_send_result is None
    assert runtime.last_packet.state.power is True
    await clock.wake_at(11199)
    assert await clock.next_deadline() == 11200
    assert send.await_count == 1
    if isinstance(failure, TimeoutError):
        assert services._control_arbiter(hass, runtime).readiness_unknown


@pytest.mark.parametrize("busy", ["entry_learning", "controller_learning", "suspended", "no_backend"])
async def test_unavailable_or_learning_refresh_is_skipped(hass, clock, busy):
    _, runtime = await setup(hass)
    backend = runtime.backend
    if busy == "entry_learning":
        runtime.learning_in_progress = True
    elif busy == "controller_learning":
        services._control_arbiter(hass, runtime).learning_owner = object()
    elif busy == "suspended":
        runtime.refresh_schedule.suspended = True
    else:
        runtime.backend = None
    await clock.wake_at(10600)
    assert await clock.next_deadline() == 11200
    assert not backend.sent_packets
    runtime.backend = backend


async def test_only_this_fireplace_activity_resets_deadline(hass, clock):
    _, first = await setup(hass, name="First")
    _, second = await setup(hass, name="Second")
    clock.now = 10500
    await services.async_execute_set_state(hass, first, {"power": True, "flame": 2}, source="test")
    assert first.refresh_schedule.activity_deadline == 11100
    assert second.refresh_schedule.activity_deadline == 10600
    clock.now = 10600
    await services.async_request_refresh(hass, second)
    assert len(second.backend.sent_packets) == 1


@pytest.mark.parametrize("shutdown", [False, True])
async def test_lifecycle_cancels_scheduler_and_fences_queued_work(hass, clock, shutdown):
    entry, runtime = await setup(hass)
    task = runtime.refresh_task
    refresh.async_start_refresh(hass, runtime)
    assert runtime.refresh_task is task
    if shutdown:
        await async_handle_homeassistant_stop(hass)
    else:
        assert await hass.config_entries.async_unload(entry.entry_id)
    assert task.done()
    assert runtime.refresh_task is None
    clock.now = 20000
    await services.async_request_refresh(hass, runtime)
    assert not runtime.backend.sent_packets


async def test_reload_replaces_timer_and_zero_disables(hass, clock):
    entry, runtime = await setup(hass)
    old_task = runtime.refresh_task
    clock.now = 10500
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_REFRESH_PERIOD_MINUTES: 3})
    assert await hass.config_entries.async_reload(entry.entry_id)
    new_runtime = async_get_runtime_entries(hass)[entry.entry_id]
    assert old_task.done()
    assert new_runtime.refresh_schedule.activity_deadline == 10680
    assert not new_runtime.backend.sent_packets
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_REFRESH_PERIOD_MINUTES: 0})
    assert await hass.config_entries.async_reload(entry.entry_id)
    assert async_get_runtime_entries(hass)[entry.entry_id].refresh_task is None


async def test_ordinary_redundant_edit_and_retained_off_edit_do_not_count(hass, clock):
    _, runtime = await setup(hass)
    clock.now = 10500
    await services.async_stage_control_change(hass, runtime, {"flame": 1})
    assert not runtime.backend.sent_packets
    off = encode_packet(FireplaceState(power=False, flame=0), runtime.remote_profile)
    await services.async_apply_observed_packet(hass, runtime, off)
    deadline = runtime.refresh_schedule.activity_deadline
    clock.now += 30
    await services.async_stage_control_change(hass, runtime, {"light": 3})
    assert not runtime.backend.sent_packets
    assert runtime.refresh_schedule.activity_deadline == deadline


async def test_public_connection_recovery_starts_full_interval(hass, clock, monkeypatch):
    from homeassistant.helpers import entity_registry as er

    from custom_components.proflame2.const import BACKEND_ESPHOME

    _, runtime = await setup(hass)
    linked = MockConfigEntry(domain="esphome", title="Test Radio")
    linked.add_to_hass(hass)
    endpoint = er.async_get(hass).async_get_or_create(
        "sensor", "esphome", "test-radio-proflame2_endpoint_status", config_entry=linked
    )
    hass.states.async_set(endpoint.entity_id, "ready")
    runtime.backend_type = BACKEND_ESPHOME
    runtime.controller_id = f"esphome:{linked.entry_id}"
    services.async_observe_controller_connection(hass, runtime)
    clock.now = 10500
    hass.states.async_set(endpoint.entity_id, "unavailable")
    await hass.async_block_till_done()
    assert runtime.refresh_schedule.suspended
    clock.now = 12000
    await services.async_request_refresh(hass, runtime)
    assert not runtime.backend.sent_packets
    hass.states.async_set(endpoint.entity_id, "ready")
    await hass.async_block_till_done()
    assert not runtime.refresh_schedule.suspended
    assert runtime.refresh_schedule.activity_deadline == 12600
    await services.async_request_refresh(hass, runtime)
    assert not runtime.backend.sent_packets
    monkeypatch.setattr(runtime.backend, "recover_send_readiness", AsyncMock(return_value=True), raising=False)
    # Preserve fake backend operation while exercising public connection policy.
    runtime.backend.connected = True
    clock.now = 12600
    await services.async_request_refresh(hass, runtime)
    assert len(runtime.backend.sent_packets) == 1


async def test_active_listener_counts_repeated_same_state_commands(hass, clock):
    _, runtime = await setup(hass)
    runtime.active_listening_enabled = True
    await services.async_start_active_listener(hass, runtime)
    packet = encode_packet(runtime.last_packet.state, runtime.remote_profile)
    clock.now = 10300
    runtime.backend.queue_packets(packet)
    await wait_for(lambda: runtime.refresh_schedule.activity_deadline == 10900)
    clock.now = 10500
    runtime.backend.queue_packets(packet)
    await wait_for(lambda: runtime.refresh_schedule.activity_deadline == 11100)
    assert not runtime.backend.sent_packets


async def test_new_user_command_replaces_queued_refresh(hass, clock):
    _, runtime = await setup(hass)
    arbiter = services._control_arbiter(hass, runtime)
    await arbiter.lock.acquire()
    clock.now = 10600
    request = asyncio.create_task(services.async_request_refresh(hass, runtime))
    await wait_for(lambda: runtime.control_intents.pending is not None)
    await services.async_stage_control_change(hass, runtime, {"power": False})
    arbiter.lock.release()
    # Supersession is a normal refresh skip, never a failed control operation.
    await request
    await wait_for(lambda: runtime.control_task is None)
    assert len(runtime.backend.sent_packets) == 1
    assert runtime.backend.sent_packets[0].state.power is False
    assert runtime.backend.sent_packets[0].source != refresh.SOURCE_REFRESH
    assert runtime.last_error is None


async def test_shared_controller_serializes_and_independent_controller_can_send(hass, clock):
    _, first = await setup(hass, name="First")
    _, shared = await setup(hass, name="Shared")
    _, independent = await setup(hass, name="Independent")
    shared.controller_id = first.controller_id = "shared-radio"
    independent.controller_id = "other-radio"
    arbiter = services._control_arbiter(hass, first)
    await arbiter.lock.acquire()
    clock.now = 10600
    waiting = asyncio.create_task(services.async_request_refresh(hass, shared))
    await wait_for(lambda: shared.control_intents.pending is not None)
    await services.async_request_refresh(hass, independent)
    assert len(independent.backend.sent_packets) == 1
    assert not shared.backend.sent_packets
    arbiter.lock.release()
    await waiting
    assert len(shared.backend.sent_packets) == 1


async def test_display_sync_is_not_rf_activity(hass, clock):
    _, runtime = await setup(hass)
    clock.now = 10500
    await services.async_sync_runtime_display_state(hass, runtime, force=True)
    await services.async_sync_runtime_rx_policy(runtime)
    assert runtime.refresh_schedule.activity_deadline == 10600
    assert not runtime.backend.sent_packets
