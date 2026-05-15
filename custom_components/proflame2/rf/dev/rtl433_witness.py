"""Development-only rtl_433 witness scaffold.

This module is a placeholder for future R820T/rtl_433 bench validation.  It
must remain outside production runtime imports so HACS users do not need SDR
tooling installed.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RTL433WitnessConfig:
    """Configuration for a future rtl_433 witness process."""

    frequency_hz: int = 315_000_000
    protocol_id: int = 207
    output_format: str = "json"
