"""Bounded latest-state intent ownership, independent of RF and HA entities."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from .protocol.models import FireplaceState


@dataclass(frozen=True)
class ControlIntent:
    """An immutable full-state request and its local completion boundary."""

    revision: int
    epoch: int
    state: FireplaceState
    eligible_at: float
    source: str
    warnings: tuple[str, ...] = ()
    profile_id: str | None = None
    profile_name: str | None = None
    clear_active_profile: bool = True
    completion: asyncio.Future[None] | None = None


@dataclass
class ControlIntents:
    """One in-flight snapshot and one replaceable pending snapshot."""

    pending: ControlIntent | None = None
    in_flight: ControlIntent | None = None
    revision: int = 0
    epoch: int = 0
    wake: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def desired_state(self) -> FireplaceState | None:
        intent = self.pending or self.in_flight
        return None if intent is None else intent.state

    def invalidate(self) -> tuple[ControlIntent, ...]:
        """Fence old results and return abandoned requests for caller notification."""

        abandoned = tuple(intent for intent in (self.in_flight, self.pending) if intent is not None)
        self.epoch += 1
        self.pending = None
        self.in_flight = None
        self.wake.set()
        return abandoned

    def owns(self, intent: ControlIntent | None) -> bool:
        return intent is not None and self.in_flight is intent and intent.epoch == self.epoch


@dataclass
class ControllerArbiter:
    """FIFO asyncio ownership of one physical radio, without a packet backlog."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    readiness_unknown: bool = False
    uncertain_request_id: str | None = None
    recovery_deadline: float = 0.0
    learning_owner: object | None = None


def controller_arbiter(domain_data: dict, key: str) -> ControllerArbiter:
    """Share ownership by physical identity across runtime and learning adapters."""
    return domain_data.setdefault("control_arbiters", {}).setdefault(key, ControllerArbiter())
