"""Bound display-sync failure reporting without delaying operational recovery."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum, auto

DISPLAY_SYNC_INTERVAL_SECONDS = 15.0
DISPLAY_SYNC_RECONCILE_SECONDS = 300.0
DISPLAY_SYNC_HEALTHY_SECONDS = 60.0
DISPLAY_SYNC_SUMMARY_SECONDS = 60.0
_MAX_COUNT = 2**32 - 1


class DisplaySyncOutcome(Enum):
    """A returned coroutine alone is not evidence of a display action."""

    APPLIED = auto()
    UNCHANGED = auto()
    DEFERRED = auto()


@dataclass
class DisplaySyncRecovery:
    """One task-local interruption, including all short-lived recoveries/flaps."""

    logger: logging.Logger
    entry_id: str
    started_at: float | None = None
    healthy_since: float | None = None
    resync_pending: bool = False
    unexpected_reported: bool = False
    last_summary_at: float = 0.0
    attempts: int = 0
    failures: int = 0

    def attempting(self) -> None:
        self.attempts = 1 if self.started_at is None else min(_MAX_COUNT, self.attempts + 1)

    def failure(self, now: float, reason: str, *, error: Exception | None = None, expected: bool = True) -> None:
        """Report an episode once, plus at most one unexpected traceback."""
        first = self.started_at is None
        if first:
            self.started_at = now
            self.last_summary_at = now
        self.healthy_since = None
        self.resync_pending = True
        self.failures = min(_MAX_COUNT, self.failures + 1)
        reason = reason[:512]
        if not expected and not self.unexpected_reported:
            self.logger.error(
                "Proflame2 display synchronization interrupted config_entry_id=%s; retrying automatically: %s",
                self.entry_id,
                reason,
                exc_info=(type(error), error, error.__traceback__) if error is not None else None,
            )
            self.unexpected_reported = True
        elif first:
            self.logger.warning(
                "Proflame2 display synchronization interrupted config_entry_id=%s; retrying automatically: %s",
                self.entry_id,
                reason,
            )
        if now - self.last_summary_at >= DISPLAY_SYNC_SUMMARY_SECONDS:
            self.logger.debug(
                "Proflame2 display synchronization still interrupted config_entry_id=%s duration=%.0fs "
                "attempts=%s failures=%s latest=%s",
                self.entry_id,
                now - self.started_at,
                self.attempts,
                self.failures,
                reason,
            )
            self.last_summary_at = now

    def confirmation_due(self, now: float) -> bool:
        return self.healthy_since is not None and now - self.healthy_since >= DISPLAY_SYNC_HEALTHY_SECONDS

    def applied(self, now: float) -> None:
        """Only actual action completion can start or confirm recovery."""
        self.resync_pending = False
        if self.started_at is None:
            self.attempts = 0
            return
        if self.healthy_since is None:
            self.healthy_since = now
        elif self.confirmation_due(now):
            self.logger.info(
                "Proflame2 display synchronization recovered config_entry_id=%s duration=%.0fs "
                "attempts=%s failures=%s",
                self.entry_id,
                now - self.started_at,
                self.attempts,
                self.failures,
            )
            self.started_at = None
            self.healthy_since = None
            self.unexpected_reported = False
            self.attempts = 0
            self.failures = 0
