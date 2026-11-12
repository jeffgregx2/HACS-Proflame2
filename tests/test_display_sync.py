"""Display recovery must bound logs without losing reconnection or user intent."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest
from test_esphome_runtime import _add_entry, _add_linked_esphome_entry, _fake_esphome_runtime_data

from custom_components.proflame2 import services
from custom_components.proflame2.const import BACKEND_ESPHOME, CONF_ACTIVE_LISTENING, CONF_ESPHOME_ENTRY_ID
from custom_components.proflame2.display_sync import DisplaySyncOutcome, DisplaySyncRecovery
from custom_components.proflame2.protocol.encoder import encode_packet
from custom_components.proflame2.protocol.models import FireplaceState
from custom_components.proflame2.rf.esphome.transport import ESPHomeControllerUnavailableError
from custom_components.proflame2.rf.esphome_api import is_display_connection_error
from custom_components.proflame2.runtime import async_setup_runtime_entry, async_unload_runtime_entry

pytestmark = pytest.mark.ha


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    yield


async def run_display_loop(hass, monkeypatch, runtime, steps, sync):
    """Advance only the display loop's clock; do not replace HA's asyncio clock."""
    clock = SimpleNamespace(now=0.0)
    actions = iter(steps)

    async def sleep(delay):
        clock.now += delay
        action = next(actions, None)
        if action is None:
            runtime.shutting_down = True
        else:
            action()

    monkeypatch.setattr(
        services,
        "asyncio",
        SimpleNamespace(
            sleep=sleep,
            get_running_loop=lambda: SimpleNamespace(time=lambda: clock.now),
            current_task=asyncio.current_task,
            CancelledError=asyncio.CancelledError,
        ),
    )
    if sync is not None:
        monkeypatch.setattr(services, "async_sync_runtime_display_state", sync)
    await services._async_display_sync_loop(hass, runtime)
    return clock


def loop_runtime(backend=None):
    return SimpleNamespace(
        config_entry_id="synthetic-fireplace",
        controller_id="synthetic-controller",
        backend_type=BACKEND_ESPHOME,
        backend=backend or SimpleNamespace(),
        last_display_sync_monotonic=None,
        desired_state=None,
        learning_in_progress=False,
        sending_in_progress=False,
        display_sync_task=None,
        shutting_down=False,
    )


@pytest.mark.parametrize(
    "error,error_count,warning_count",
    [
        (TimeoutError("Synthetic native API timeout"), 0, 1),
        (ValueError("Synthetic bug"), 1, 0),
        (RuntimeError("Missing action"), 1, 0),
    ],
)
async def test_periodic_failures_do_not_flood_errors(hass, monkeypatch, caplog, error, error_count, warning_count):
    runtime = loop_runtime()

    async def sync(*args, **kwargs):
        raise error

    caplog.set_level(logging.DEBUG, logger=services.__name__)
    await run_display_loop(hass, monkeypatch, runtime, [lambda: None] * 20, sync)
    errors = [r for r in caplog.records if r.name == services.__name__ and r.levelno >= logging.ERROR]
    assert len(errors) == error_count
    assert all(r.exc_info[2] is not None for r in errors)
    warnings = [r for r in caplog.records if r.name == services.__name__ and r.levelno == logging.WARNING]
    assert len(warnings) == warning_count


async def test_display_rebinds_replaced_runtime_without_active_listening(hass):
    old = _fake_esphome_runtime_data()
    linked = _add_linked_esphome_entry(hass, old)
    entry = _add_entry(hass, options={CONF_ESPHOME_ENTRY_ID: linked.entry_id})
    runtime = await async_setup_runtime_entry(hass, entry)
    try:
        runtime.last_packet = encode_packet(FireplaceState(True, flame=3), runtime.remote_profile)
        await services.async_sync_runtime_display_state(hass, runtime, force=True)
        old_calls = len(old.client.calls)
        replacement = _fake_esphome_runtime_data()
        linked.runtime_data = replacement
        old.available = False
        await services.async_sync_runtime_display_state(hass, runtime, force=True)
        assert any(name == "proflame2_display_state_update" for name, _ in replacement.client.calls)
        assert not any(name == "proflame2_tx_stateful" for name, _ in replacement.client.calls)
        assert len(old.client.calls) == old_calls
    finally:
        await async_unload_runtime_entry(hass, entry)


async def test_known_offline_skips_api_and_bounds_debug_summaries(hass, monkeypatch, caplog):
    runtime = loop_runtime(SimpleNamespace(controller_available=False))
    calls = []

    async def sync(*args, **kwargs):
        calls.append(kwargs)

    caplog.set_level(logging.DEBUG, logger=services.__name__)
    await run_display_loop(hass, monkeypatch, runtime, [lambda: None] * 100, sync)
    records = [r for r in caplog.records if r.name == services.__name__]
    assert not calls
    assert sum(r.levelno == logging.WARNING for r in records) == 1
    assert not any(r.levelno >= logging.ERROR for r in records)
    assert sum(r.levelno == logging.DEBUG for r in records) == 24


async def test_flaps_remain_one_episode_until_stable_real_confirmation(hass, monkeypatch, caplog):
    backend = SimpleNamespace(controller_available=False)
    runtime = loop_runtime(backend)
    calls = []

    async def sync(*args, force=False, **kwargs):
        calls.append(force)
        return DisplaySyncOutcome.APPLIED if force else DisplaySyncOutcome.UNCHANGED

    flags = [False, True, False, True, False, True, True, True, True, True, True]
    steps = [lambda flag=flag: setattr(backend, "controller_available", flag) for flag in flags]
    runtime.last_display_sync_monotonic = 1.0
    caplog.set_level(logging.DEBUG, logger=services.__name__)
    await run_display_loop(hass, monkeypatch, runtime, steps, sync)
    records = [r for r in caplog.records if r.name == services.__name__]
    assert sum(r.levelno == logging.WARNING for r in records) == 1
    assert sum(r.levelno == logging.INFO for r in records) == 1
    assert calls == [True, True, True, False, False, False, True, False]


@pytest.mark.parametrize("outcome", [DisplaySyncOutcome.UNCHANGED, DisplaySyncOutcome.DEFERRED])
async def test_noop_cannot_claim_recovery(hass, monkeypatch, caplog, outcome):
    runtime = loop_runtime()
    first = True

    async def sync(*args, **kwargs):
        nonlocal first
        if first:
            first = False
            raise ConnectionError("Synthetic disconnection")
        return outcome

    caplog.set_level(logging.DEBUG, logger=services.__name__)
    await run_display_loop(hass, monkeypatch, runtime, [lambda: None] * 20, sync)
    assert not any(r.levelno == logging.INFO and r.name == services.__name__ for r in caplog.records)


def test_episode_preserves_one_unexpected_traceback_through_changing_failures(caplog):
    logger = logging.getLogger("custom_components.proflame2.display_sync")
    caplog.set_level(logging.DEBUG, logger=logger.name)
    policy = DisplaySyncRecovery(logger, "synthetic-fireplace")
    policy.failure(0, "Offline")
    for second in range(1, 181):
        error = ValueError(f"Synthetic failure {second}")
        policy.failure(second, str(error), error=error, expected=False)
    records = [r for r in caplog.records if r.name == logger.name]
    assert sum(r.levelno == logging.WARNING for r in records) == 1
    assert sum(r.levelno == logging.ERROR and bool(r.exc_info) for r in records) == 1
    assert sum(r.levelno == logging.DEBUG for r in records) == 3
    policy.applied(181)
    assert not policy.confirmation_due(240.999)
    assert policy.confirmation_due(241)
    policy.applied(241)
    assert sum(r.levelno == logging.INFO and r.name == logger.name for r in caplog.records) == 1
    policy.failure(242, "Another genuine outage")
    assert sum(r.levelno == logging.WARNING and r.name == logger.name for r in caplog.records) == 2


@pytest.mark.parametrize(
    "field",
    [
        "learning_in_progress",
        "desired_state",
        "sending_in_progress",
        "arbiter_lock",
        "uncertain_transmit",
        "learning_owner",
    ],
)
async def test_periodic_sync_yields_to_user_work(hass, monkeypatch, field):
    runtime = loop_runtime()
    arbiter = services._control_arbiter(hass, runtime)
    if field == "arbiter_lock":
        await arbiter.lock.acquire()
    elif field == "uncertain_transmit":
        arbiter.readiness_unknown = True
        arbiter.uncertain_request_id = "synthetic-request"
    elif field == "learning_owner":
        arbiter.learning_owner = object()
    else:
        setattr(runtime, field, FireplaceState(True, flame=2) if field == "desired_state" else True)

    async def sync(*args, **kwargs):
        pytest.fail("Background sync interrupted user work")

    try:
        await run_display_loop(hass, monkeypatch, runtime, [lambda: None] * 5, sync)
    finally:
        if arbiter.lock.locked():
            arbiter.lock.release()


async def test_cancelled_display_task_releases_ownership_without_recovery(hass, monkeypatch, caplog):
    runtime = loop_runtime(SimpleNamespace(controller_available=False))
    runtime.display_sync_task = asyncio.current_task()

    def cancel():
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await run_display_loop(hass, monkeypatch, runtime, [lambda: None, cancel], None)
    assert runtime.display_sync_task is None
    assert not any("recovered" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("listening", [False, True])
@pytest.mark.parametrize("replace", [False, True])
async def test_full_periodic_recovery_uses_current_actions_without_tx(hass, monkeypatch, listening, replace):
    old = _fake_esphome_runtime_data()
    linked = _add_linked_esphome_entry(hass, old)
    entry = _add_entry(hass, options={CONF_ESPHOME_ENTRY_ID: linked.entry_id, CONF_ACTIVE_LISTENING: listening})
    runtime = await async_setup_runtime_entry(hass, entry)
    runtime.last_packet = encode_packet(FireplaceState(True, flame=3), runtime.remote_profile)

    # Keep the independent RX owner alive without advancing the display clock.
    async def listener(*args):
        await asyncio.Event().wait()

    monkeypatch.setattr(services, "_async_active_listener_loop", listener)
    await services.async_sync_runtime_display_state(hass, runtime, force=True)
    owner = runtime.active_listener_task
    old_calls = len(old.client.calls)
    current = _fake_esphome_runtime_data() if replace else old

    def offline():
        old.available = False
        services.async_controller_connection_lost(hass, runtime)

    def online():
        linked.runtime_data = current
        current.available = True

    try:
        await run_display_loop(hass, monkeypatch, runtime, [offline, offline, online, lambda: None], None)
        calls = current.client.calls if replace else current.client.calls[old_calls:]
        assert sum(name == "proflame2_display_state_update" for name, _ in calls) == 1
        assert not any(name == "proflame2_tx_stateful" for name, _ in calls)
        assert current.active_listening_enabled is listening
        assert runtime.active_listener_task is owner
        if replace:
            assert len(old.client.calls) == old_calls
        assert runtime.backend.transport.connection_is_current
        # An ordinary disconnect fences sends, but no uncertain TX prevents
        # display recovery. The display path does not clear the TX fence.
        arbiter = services._control_arbiter(hass, runtime)
        assert arbiter.readiness_unknown and arbiter.uncertain_request_id is None
    finally:
        await async_unload_runtime_entry(hass, entry)


@pytest.mark.parametrize("kind", ["missing_entry", "missing_runtime", "offline", "replacement"])
async def test_availability_uses_only_fresh_in_memory_state(hass, kind):
    old = _fake_esphome_runtime_data()
    linked = _add_linked_esphome_entry(hass, old)
    entry = _add_entry(hass, options={CONF_ESPHOME_ENTRY_ID: linked.entry_id})
    runtime = await async_setup_runtime_entry(hass, entry)
    try:
        await runtime.backend.connect()
        transport = runtime.backend.transport
        old_calls = len(old.client.calls)
        if kind == "missing_entry":
            transport.linked_entry_id = "synthetic-missing-entry"
        elif kind == "missing_runtime":
            linked.runtime_data = None
        elif kind == "offline":
            old.available = False
        else:
            old.available = False
            linked.runtime_data = _fake_esphome_runtime_data()
        for _ in range(10):
            assert transport.device_available is (kind == "replacement")
            assert not transport.connection_is_current
        assert len(old.client.calls) == old_calls
    finally:
        await async_unload_runtime_entry(hass, entry)


@pytest.mark.parametrize(
    "error,expected",
    [
        (TimeoutError(), True),
        (ConnectionError(), True),
        (ValueError(), False),
        (RuntimeError("Missing action"), False),
        (ESPHomeControllerUnavailableError(), True),
    ],
)
def test_connection_error_classification_preserves_wrapped_causes(error, expected):
    wrapped = RuntimeError("Display action failed")
    wrapped.__cause__ = error
    assert is_display_connection_error(wrapped) is expected


def test_native_api_exception_subclasses_are_expected_disconnects():
    base = type("APIConnectionError", (Exception,), {"__module__": "aioesphomeapi.core"})
    child = type("SocketClosedAPIError", (base,), {})
    assert is_display_connection_error(child())


def test_implicit_network_context_does_not_hide_a_new_programming_error():
    error = ValueError("Synthetic cleanup bug")
    error.__context__ = TimeoutError("Earlier unrelated operation timed out")
    assert not is_display_connection_error(error)


async def test_unknown_state_is_deferred_without_fake_display_success(hass):
    linked = _add_linked_esphome_entry(hass, _fake_esphome_runtime_data())
    entry = _add_entry(hass, options={CONF_ESPHOME_ENTRY_ID: linked.entry_id})
    runtime = await async_setup_runtime_entry(hass, entry)
    try:
        runtime.last_packet = None
        assert await services.async_sync_runtime_display_state(hass, runtime, force=True) is DisplaySyncOutcome.DEFERRED
        assert runtime.last_display_sync_signature is None
        assert runtime.last_display_sync_monotonic is None
        assert not any(
            name in ("proflame2_tx_stateful", "proflame2_display_state_update")
            for name, _ in linked.runtime_data.client.calls
        )
    finally:
        await async_unload_runtime_entry(hass, entry)


async def test_new_user_intent_during_prepare_defers_policy_and_display(hass):
    linked = _add_linked_esphome_entry(hass, _fake_esphome_runtime_data())
    entry = _add_entry(hass, options={CONF_ESPHOME_ENTRY_ID: linked.entry_id})
    runtime = await async_setup_runtime_entry(hass, entry)

    async def prepare():
        runtime.desired_state = FireplaceState(True, flame=4)
        return True

    runtime.backend = SimpleNamespace(prepare_display_sync=prepare)
    assert await services.async_sync_runtime_display_state(hass, runtime, force=True) is DisplaySyncOutcome.DEFERRED
    assert not linked.runtime_data.client.calls


async def test_background_rebinding_forces_display_even_with_unchanged_signature(hass, monkeypatch, caplog):
    backend = SimpleNamespace(display_sync_generation=1)
    runtime = loop_runtime(backend)
    runtime.last_display_sync_monotonic = 1.0
    calls = []

    async def sync(*args, force=False, **kwargs):
        calls.append(force)
        return DisplaySyncOutcome.APPLIED if force else DisplaySyncOutcome.UNCHANGED

    caplog.set_level(logging.DEBUG, logger=services.__name__)
    steps = [lambda: None, lambda: setattr(backend, "display_sync_generation", 2)] + [lambda: None] * 4
    await run_display_loop(hass, monkeypatch, runtime, steps, sync)
    assert calls == [False, True, False, False, False, True]
    assert sum(r.levelno == logging.INFO and r.name == services.__name__ for r in caplog.records) == 1


async def test_api_race_after_available_check_recovers_without_sending_rf(hass, monkeypatch, caplog):
    native = _fake_esphome_runtime_data()
    linked = _add_linked_esphome_entry(hass, native)
    entry = _add_entry(hass, options={CONF_ESPHOME_ENTRY_ID: linked.entry_id})
    runtime = await async_setup_runtime_entry(hass, entry)
    runtime.last_packet = encode_packet(FireplaceState(True, flame=3), runtime.remote_profile)
    await services.async_sync_runtime_display_state(hass, runtime, force=True)
    real_execute = native.client.execute_service
    fail = True

    async def execute(action, payload):
        if fail:
            raise TimeoutError("Connection lost after availability check")
        await real_execute(action, payload)

    native.client.execute_service = execute

    def returned():
        nonlocal fail
        fail = False

    caplog.set_level(logging.DEBUG, logger=services.__name__)
    try:
        await run_display_loop(hass, monkeypatch, runtime, [lambda: None] * 8 + [returned] + [lambda: None] * 4, None)
        records = [r for r in caplog.records if r.name == services.__name__]
        assert sum(r.levelno == logging.WARNING for r in records) == 1
        assert not any(r.levelno >= logging.ERROR for r in records)
        assert sum(r.levelno == logging.INFO for r in records) == 1
        assert sum(name == "proflame2_display_state_update" for name, _ in native.client.calls) == 3
        assert not any(name == "proflame2_tx_stateful" for name, _ in native.client.calls)
        assert runtime.backend.connected
    finally:
        await async_unload_runtime_entry(hass, entry)


async def test_cancel_during_action_preserves_replacement_task_owner(hass, monkeypatch):
    runtime = loop_runtime()
    owner = object()
    runtime.display_sync_task = owner

    async def sync(*args, **kwargs):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await run_display_loop(hass, monkeypatch, runtime, [lambda: None], sync)
    assert runtime.display_sync_task is owner


@pytest.mark.parametrize("stop", ["backend_removed", "shutdown"])
async def test_intentional_stop_after_poll_wait_does_not_attempt_sync(hass, monkeypatch, stop):
    runtime = loop_runtime()

    def stopped():
        if stop == "backend_removed":
            runtime.backend = None
        else:
            runtime.shutting_down = True

    async def sync(*args, **kwargs):
        pytest.fail("Display loop continued after intentional stop")

    await run_display_loop(hass, monkeypatch, runtime, [stopped], sync)


async def test_failed_forced_display_retains_caches_and_is_retried(hass, monkeypatch, caplog):
    native = _fake_esphome_runtime_data()
    linked = _add_linked_esphome_entry(hass, native)
    entry = _add_entry(hass, options={CONF_ESPHOME_ENTRY_ID: linked.entry_id})
    runtime = await async_setup_runtime_entry(hass, entry)
    runtime.last_packet = encode_packet(FireplaceState(True, flame=3), runtime.remote_profile)
    await services.async_sync_runtime_display_state(hass, runtime, force=True)
    signature = runtime.last_display_sync_signature
    runtime.last_display_sync_monotonic = -300.0
    real_execute = native.client.execute_service
    fail = True

    async def execute(action, payload):
        if fail and action.name == "proflame2_display_state_update":
            raise TimeoutError("Synthetic display-only failure")
        await real_execute(action, payload)

    native.client.execute_service = execute

    def repair():
        nonlocal fail
        assert runtime.last_display_sync_signature == signature
        assert runtime.last_display_sync_monotonic == -300.0
        fail = False

    caplog.set_level(logging.DEBUG, logger=services.__name__)
    try:
        await run_display_loop(hass, monkeypatch, runtime, [lambda: None, repair] + [lambda: None] * 4, None)
        assert runtime.last_display_sync_monotonic == 90.0
        assert runtime.last_display_sync_signature == signature
        assert sum(name == "proflame2_display_state_update" for name, _ in native.client.calls) == 3
        assert not any(name == "proflame2_tx_stateful" for name, _ in native.client.calls)
        assert sum(r.levelno == logging.INFO and r.name == services.__name__ for r in caplog.records) == 1
    finally:
        await async_unload_runtime_entry(hass, entry)
