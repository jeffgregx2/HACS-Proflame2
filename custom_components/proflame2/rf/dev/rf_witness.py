"""Development-only RF witness abstractions.

This scaffold is intentionally not used by production runtime code.  Future
bench tooling can use it to compare Yard Stick and ESPHome/T-Embed RF output
against an independent observer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RFWitnessObservation:
    """One development-only RF observation."""

    source: str
    payload: bytes | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class RFWitnessUnavailableError(RuntimeError):
    """Raised when a development-only RF witness cannot be started."""
