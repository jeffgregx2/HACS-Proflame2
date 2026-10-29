"""Per-fireplace inactivity deadlines for optional ON-state keep-alive sends.

HA owns one scheduler per enabled entry. It submits through the ordinary control
dispatcher; it never owns the radio or retains an encoded packet. All mutation
and callbacks run on HA's event loop, using monotonic seconds.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from time import monotonic
from typing import TYPE_CHECKING

from .protocol.decoder import decode_state

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .protocol.models import FireplaceState
    from .protocol.packet import ProflamePacket
    from .runtime import Proflame2RuntimeEntry

SOURCE_REFRESH = "inactivity_refresh"
_LOGGER = logging.getLogger(__name__)


@dataclass
class RefreshSchedule:
    """An inactivity deadline with separate automatic-attempt pacing.

    Failed or ineligible attempts postpone automatic work without claiming RF
    activity. Deadlines are transient and start fresh on setup or reconnect.
    """

    period_seconds: float = 0
    activity_deadline: float = 0
    attempt_not_before: float = 0
    suspended: bool = False

    @property
    def deadline(self) -> float:
        return max(self.activity_deadline, self.attempt_not_before)

    def restart(self, now: float | None = None) -> None:
        """Start a full interval without replaying elapsed downtime."""
        self.activity_deadline = (monotonic() if now is None else now) + self.period_seconds
        self.attempt_not_before = 0

    def record_activity(self, now: float | None = None) -> None:
        """Postpone refresh after a successful TX or accepted matching RX."""
        self.activity_deadline = (monotonic() if now is None else now) + self.period_seconds

    def pace_attempt(self, now: float) -> None:
        """Bound automatic attempts even when no successful TX occurred."""
        self.attempt_not_before = now + self.period_seconds

    def due(self, now: float) -> bool:
        return self.period_seconds > 0 and not self.suspended and now >= self.deadline


class RefreshSkipped(Exception):
    """A refresh lost eligibility while awaiting controller or RX handoff."""


def record_received_activity(entry: Proflame2RuntimeEntry, packet: ProflamePacket) -> None:
    """Count valid matching commands even when their state is unchanged."""
    try:
        decode_state(packet.frame, entry.remote_profile)
    except ValueError:
        return
    entry.refresh_schedule.record_activity(monotonic())


def current_refresh_state(entry: Proflame2RuntimeEntry) -> FireplaceState | None:
    """Select an eligible ON state without displacing ordinary control work."""
    if (
        entry.shutting_down
        or entry.learning_in_progress
        or entry.backend is None
        or not entry.refresh_schedule.due(monotonic())
    ):
        return None
    intents = entry.control_intents
    if any(intent is not None and intent.source != SOURCE_REFRESH for intent in (intents.pending, intents.in_flight)):
        return None
    state = entry.last_packet.state if entry.last_packet is not None else None
    if state is None or not state.power:
        return None
    try:
        state.validate_transmit()
    except ValueError:
        return None
    return state


def async_start_refresh(hass: HomeAssistant, entry: Proflame2RuntimeEntry) -> None:
    """Start one enabled scheduler; unload/shutdown owns cancellation."""
    if entry.shutting_down or entry.refresh_schedule.period_seconds <= 0:
        return
    if entry.refresh_task is not None and not entry.refresh_task.done():
        return
    entry.refresh_schedule.restart(monotonic())
    entry.refresh_task = hass.async_create_background_task(
        _async_refresh_loop(hass, entry), f"proflame2_{entry.config_entry_id}_refresh"
    )


async def _async_refresh_loop(hass: HomeAssistant, entry: Proflame2RuntimeEntry) -> None:
    from .services import async_request_refresh

    schedule = entry.refresh_schedule
    try:
        while not entry.shutting_down and schedule.period_seconds > 0:
            await _async_wait_until(schedule.deadline)
            if entry.shutting_down:
                return
            now = monotonic()
            if now < schedule.deadline:
                continue
            try:
                await async_request_refresh(hass, entry)
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.warning(
                    "Proflame2 inactivity refresh failed config_entry_id=%s", entry.config_entry_id, exc_info=True
                )
            finally:
                schedule.pace_attempt(monotonic())
    finally:
        if entry.refresh_task is asyncio.current_task():
            entry.refresh_task = None


async def _async_wait_until(deadline: float) -> None:
    """Wait on a monotonic deadline, rechecked by the owner after waking."""
    await asyncio.sleep(max(0, deadline - monotonic()))
