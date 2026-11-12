"""Connection events must publish current state without polling or lifecycle leaks."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from custom_components.proflame2.connection_updates import ConnectionStatePublisher
from custom_components.proflame2.rf.esphome_api import ESPHomeAPIBackend
from custom_components.proflame2.rf.fake import FakeRFBackend


class Clock:
    """Advance one-shot callbacks deterministically, without sleeping or hardware."""

    def __init__(self):
        self.now = 0.0
        self.handles = []

    def call_later(self, delay, callback):
        handle = SimpleNamespace(deadline=self.now + delay, callback=callback, cancelled=False, fired=False)
        handle.cancel = lambda: setattr(handle, "cancelled", True)
        self.handles.append(handle)
        return handle

    def advance(self, target):
        while due := [h for h in self.pending if h.deadline <= target]:
            handle = min(due, key=lambda h: h.deadline)
            self.now = handle.deadline
            handle.fired = True
            handle.callback()
        self.now = target

    @property
    def pending(self):
        return [h for h in self.handles if not h.cancelled and not h.fired]


def publisher(clock, backend, notifications, *, current=lambda: True, state=None):
    return ConnectionStatePublisher(
        SimpleNamespace(loop=clock),
        backend,
        is_current=current,
        publish=lambda: notifications.append((clock.now, backend.connected, None if state is None else state.value)),
    )


def test_backend_emits_only_transitions_and_unsubscribes_idempotently():
    backend = ESPHomeAPIBackend()
    events = []
    unsubscribe = backend.add_connection_listener(events.append)
    for value in [False, True, True, False, False]:
        backend.connected = value
    assert events == [True, False]
    unsubscribe()
    unsubscribe()
    backend.connected = True
    assert events == [True, False]


def test_observer_failure_cannot_fail_a_connection_or_block_other_observers(caplog):
    backend = ESPHomeAPIBackend()
    events = []

    def bad_observer(_value):
        raise RuntimeError("synthetic observer failure")

    backend.add_connection_listener(bad_observer)
    backend.add_connection_listener(events.append)
    with caplog.at_level(logging.ERROR):
        backend.connected = True
        backend.connected = False
    assert backend.connected is False
    assert events == [True, False]
    assert "backend connection listener failed" in caplog.text


def test_listener_can_unsubscribe_itself_during_delivery():
    backend = ESPHomeAPIBackend()
    events = []

    def first(value):
        events.append(("first", value))
        unsubscribe()

    unsubscribe = backend.add_connection_listener(first)
    backend.add_connection_listener(lambda value: events.append(("second", value)))
    backend.connected = True
    backend.connected = False
    assert events == [("first", True), ("second", True), ("second", False)]


def test_other_backend_keeps_its_existing_connection_behavior():
    backend = FakeRFBackend(connected=False)
    events = []
    unsubscribe = backend.add_connection_listener(events.append)
    backend.connected = True
    assert backend.connected is True
    assert events == []
    unsubscribe()
    unsubscribe()


def test_one_timer_keeps_first_deadline_and_reads_current_state():
    clock, backend, notifications = Clock(), ESPHomeAPIBackend(), []
    state = SimpleNamespace(value="pending")
    owner = publisher(clock, backend, notifications, state=state)
    backend.connected = True
    first = clock.pending[0]
    clock.advance(0.04)
    backend.connected = False
    clock.advance(0.08)
    backend.connected = True
    state.value = "failed"
    assert clock.pending == [first]
    assert first.deadline == pytest.approx(0.1)
    clock.advance(0.1)
    assert notifications == [(0.1, True, "failed")]
    assert not clock.pending
    clock.advance(10)
    assert len(notifications) == 1  # No recurring timer.
    owner.close()


@pytest.mark.parametrize("transition_before_expiry", [False, True])
def test_transition_on_either_side_of_expiry_cannot_lose_latest_state(transition_before_expiry):
    clock, backend, notifications = Clock(), ESPHomeAPIBackend(), []
    owner = publisher(clock, backend, notifications)
    backend.connected = True
    clock.advance(0.099 if transition_before_expiry else 0.1)
    backend.connected = False
    assert len(clock.pending) == 1
    clock.advance(0.2)
    assert notifications[-1][1] is False
    assert len(notifications) == (1 if transition_before_expiry else 2)
    assert not clock.pending
    owner.close()


def test_continuous_flapping_never_starves_publication():
    clock, backend, notifications = Clock(), ESPHomeAPIBackend(), []
    owner = publisher(clock, backend, notifications)
    backend.connected = True
    for index in range(1, 31):
        clock.advance(index * 0.02)
        backend.connected = not backend.connected
        assert len(clock.pending) <= 1
    clock.advance(0.8)
    assert notifications[0][0] == pytest.approx(0.1)
    assert len(notifications) >= 5
    assert notifications[-1][1] == backend.connected
    assert not clock.pending
    owner.close()


def test_close_cancels_pending_work_and_blocks_already_queued_callbacks():
    clock, backend, notifications = Clock(), ESPHomeAPIBackend(), []
    owner = publisher(clock, backend, notifications)
    backend.connected = True
    old_callback = clock.pending[0].callback
    owner.close()
    owner.close()
    backend.connected = False
    old_callback()  # Even an already queued callback must be harmless.
    clock.advance(1)
    assert notifications == []
    assert not clock.pending


def test_replaced_runtime_is_ignored_and_other_entry_is_independent():
    clock, notifications_a, notifications_b = Clock(), [], []
    backend_a, backend_b = ESPHomeAPIBackend(), ESPHomeAPIBackend()
    current = SimpleNamespace(value=True)
    owner_a = publisher(clock, backend_a, notifications_a, current=lambda: current.value)
    owner_b = publisher(clock, backend_b, notifications_b)
    backend_a.connected = True
    backend_b.connected = True
    assert len(clock.pending) == 2  # One per entry, not one globally.
    current.value = False
    clock.advance(0.1)
    assert notifications_a == []
    assert notifications_b == [(0.1, True, None)]
    backend_a.connected = False
    assert not clock.pending
    owner_a.close()
    backend_b.connected = False
    clock.advance(0.2)
    assert notifications_b[-1][1] is False
    owner_b.close()
