"""Issue #36: quiet backend recovery must republish HA readiness without RF."""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.setup import async_setup_component
from test_esphome_runtime import (
    _add_entry,
    _add_linked_esphome_device,
    _add_linked_esphome_entry,
    _fake_esphome_runtime_data,
)

import custom_components.proflame2 as integration
from custom_components.proflame2 import services
from custom_components.proflame2.const import CONF_ACTIVE_LISTENING, CONF_ESPHOME_ENTRY_ID
from custom_components.proflame2.runtime import async_get_runtime_entries, runtime_current_state

pytestmark = pytest.mark.ha


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    yield


@pytest.mark.parametrize("recovery", ["active_listener", "display_sync"])
async def test_quiet_recovery_republishes_primary_readiness(hass, monkeypatch, recovery):
    """Check the published State object, not merely the live backend/property."""

    # Isolate listener/display recovery from the unrelated foreground startup
    # retry task, whose real delays would add 50 seconds to each test case.
    monkeypatch.setattr(integration, "_DISPLAY_SYNC_RETRY_DELAYS_SECONDS", ())
    monkeypatch.setattr(services, "_ACTIVE_LISTENER_RETRY_INITIAL_SECONDS", 0.001)
    monkeypatch.setattr(services, "_ACTIVE_LISTENER_RETRY_MAX_SECONDS", 0.004)
    assert await async_setup_component(hass, "homeassistant", {})
    native = _fake_esphome_runtime_data()
    native.available = False
    linked = _add_linked_esphome_entry(hass, native)
    _add_linked_esphome_device(hass, linked, identifier=("esphome", "readiness-test-controller"))
    entry = _add_entry(
        hass,
        title="Readiness Test",
        remote_id=0x123456,
        options={CONF_ESPHOME_ENTRY_ID: linked.entry_id, CONF_ACTIVE_LISTENING: recovery == "active_listener"},
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    runtime = async_get_runtime_entries(hass)[entry.entry_id]
    entity_id = "sensor.readiness_test"
    assert hass.states.get(entity_id).attributes["operational_status"] == "unavailable"
    assert runtime.backend.connected is False
    initial_state = runtime_current_state(runtime)
    initial_confidence = runtime.state_confidence
    counters = tuple(
        native.object_states[key].state for key in ("proflame2_tx_success_count", "proflame2_tx_failure_count")
    )
    native.available = True
    try:
        if recovery == "active_listener":
            # Recovery belongs to the real listener/backend/transport path.
            # Do not queue a received frame: it would mask this missing update.
            async with asyncio.timeout(2):
                while not runtime.backend.connected:
                    await asyncio.sleep(0.001)
            assert native.active_listening_enabled
        else:
            # Exercise the existing periodic recovery entry point without a
            # user command or waiting its real 15-second scheduling interval.
            await services.async_sync_runtime_display_state(hass, runtime, force=True)
        await asyncio.sleep(0.25)  # Allow a future short transition-triggered notification.
        await hass.async_block_till_done()
        assert runtime.backend.connected is True
        assert runtime.operational_status == "ready"
        # Retain the pre-refresh published value. The workaround below must
        # not turn a stale-state regression into a passing test.
        published_status = hass.states.get(entity_id).attributes["operational_status"]
        await hass.services.async_call("homeassistant", "update_entity", {"entity_id": entity_id}, blocking=True)
        assert hass.states.get(entity_id).attributes["operational_status"] == "ready"
        assert runtime_current_state(runtime) == initial_state
        assert runtime.state_confidence == initial_confidence
        assert (
            tuple(
                native.object_states[key].state for key in ("proflame2_tx_success_count", "proflame2_tx_failure_count")
            )
            == counters
        )
        assert not any(name == "proflame2_tx_stateful" for name, _ in native.client.calls)
        assert published_status == "ready", "Backend recovered, but HA retained stale readiness until update_entity."
    finally:
        await hass.config_entries.async_unload(entry.entry_id)


async def connected_fireplace(hass, monkeypatch, *, fail_platform_setup=False):
    """Load actual HA entities unless intentionally testing setup cleanup."""

    monkeypatch.setattr(integration, "_DISPLAY_SYNC_RETRY_DELAYS_SECONDS", ())
    native = _fake_esphome_runtime_data()
    linked = _add_linked_esphome_entry(hass, native)
    _add_linked_esphome_device(hass, linked, identifier=("esphome", "readiness-test-controller"))
    entry = _add_entry(
        hass,
        title="Readiness Test",
        remote_id=0x123456,
        options={CONF_ESPHOME_ENTRY_ID: linked.entry_id, CONF_ACTIVE_LISTENING: False},
    )
    captured = []
    if fail_platform_setup:

        async def fail_forward(*_args, **_kwargs):
            captured.append(async_get_runtime_entries(hass)[entry.entry_id])
            raise RuntimeError("synthetic platform setup failure")

        monkeypatch.setattr(hass.config_entries, "async_forward_entry_setups", fail_forward)
    loaded = await hass.config_entries.async_setup(entry.entry_id)
    assert loaded is not fail_platform_setup
    runtime = captured[0] if fail_platform_setup else async_get_runtime_entries(hass)[entry.entry_id]
    return entry, runtime, native


@pytest.mark.parametrize("status", ["ready", "pending", "sending", "failed", "learning"])
async def test_connection_events_republish_derived_status_without_changing_confidence(hass, monkeypatch, status):
    entry, runtime, native = await connected_fireplace(hass, monkeypatch)
    entity_id = "sensor.readiness_test"
    await asyncio.sleep(0.15)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["operational_status"] == "ready"
    state, confidence = runtime_current_state(runtime), runtime.state_confidence
    try:
        native.available = False
        runtime.backend.invalidate_display_connection()
        await asyncio.sleep(0.15)
        await hass.async_block_till_done()
        assert hass.states.get(entity_id).attributes["operational_status"] == "unavailable"
        runtime.learning_in_progress = status == "learning"
        runtime.operational_status = "ready" if status == "learning" else status
        native.available = True
        await runtime.backend.connect()
        await asyncio.sleep(0.15)
        await hass.async_block_till_done()
        assert hass.states.get(entity_id).attributes["operational_status"] == status
        assert runtime_current_state(runtime) == state
        assert runtime.state_confidence == confidence
        assert not any(name == "proflame2_tx_stateful" for name, _ in native.client.calls)
    finally:
        await hass.config_entries.async_unload(entry.entry_id)


async def test_unload_and_reload_discard_pending_old_connection_notifications(hass, monkeypatch):
    from homeassistant.helpers.dispatcher import async_dispatcher_connect

    from custom_components.proflame2.runtime import async_runtime_signal

    entry, runtime, _native = await connected_fireplace(hass, monkeypatch)
    notifications = []
    unsubscribe = async_dispatcher_connect(hass, async_runtime_signal(entry.entry_id), lambda: notifications.append(1))
    runtime.backend.connected = False
    assert await hass.config_entries.async_unload(entry.entry_id)
    after_unload = len(notifications)
    runtime.backend.connected = True
    await asyncio.sleep(0.15)
    await hass.async_block_till_done()
    assert len(notifications) == after_unload
    assert await hass.config_entries.async_setup(entry.entry_id)
    await asyncio.sleep(0.15)
    await hass.async_block_till_done()
    current = async_get_runtime_entries(hass)[entry.entry_id]
    assert current is not runtime
    after_reload = len(notifications)
    runtime.backend.connected = False
    runtime.backend.connected = True
    await asyncio.sleep(0.15)
    await hass.async_block_till_done()
    assert len(notifications) == after_reload
    assert hass.states.get("sensor.readiness_test").attributes["operational_status"] == "ready"
    unsubscribe()
    await hass.config_entries.async_unload(entry.entry_id)


async def test_failed_setup_cancels_notification_and_detaches_backend_observer(hass, monkeypatch):
    from homeassistant.helpers.dispatcher import async_dispatcher_connect

    from custom_components.proflame2.runtime import async_runtime_signal, async_unload_runtime_entry

    entry, runtime, _native = await connected_fireplace(hass, monkeypatch, fail_platform_setup=True)
    notifications = []
    unsubscribe = async_dispatcher_connect(hass, async_runtime_signal(entry.entry_id), lambda: notifications.append(1))
    try:
        runtime.backend.connected = False
        runtime.backend.connected = True
        await asyncio.sleep(0.15)
        await hass.async_block_till_done()
        assert notifications == []
    finally:
        unsubscribe()
        await async_unload_runtime_entry(hass, entry)


async def test_ha_shutdown_cancels_pending_connection_publication(hass, monkeypatch):
    from homeassistant.helpers.dispatcher import async_dispatcher_connect

    from custom_components.proflame2.runtime import async_handle_homeassistant_stop, async_runtime_signal

    entry, runtime, _native = await connected_fireplace(hass, monkeypatch)
    notifications = []
    unsubscribe = async_dispatcher_connect(hass, async_runtime_signal(entry.entry_id), lambda: notifications.append(1))
    try:
        runtime.backend.connected = False
        await async_handle_homeassistant_stop(hass)
        after_stop = len(notifications)
        runtime.backend.connected = True
        await asyncio.sleep(0.15)
        await hass.async_block_till_done()
        assert len(notifications) == after_stop
        assert runtime.connection_updates_unsub is None
    finally:
        unsubscribe()
        await hass.config_entries.async_unload(entry.entry_id)
