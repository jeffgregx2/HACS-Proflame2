"""Internal protocol/hardware profile definitions for Proflame2 transports.

These profiles describe RF transport assumptions shared by backend
implementations. They are intentionally separate from:

- learned remote identity and ECC values
- user-facing scene/profile concepts
- authoritative fireplace state

Home Assistant remains the protocol authority. Protocol profiles only define
transport-level behavior such as frequency, modulation, data rate, repeat
count, and capability gating.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ProtocolModulation(StrEnum):
    """Radio modulation values supported by Proflame2 transports."""

    ASK_OOK = "ask_ook"


class ProtocolFeature(StrEnum):
    """Feature flags exposed by the generic Proflame2 protocol surface."""

    FLAME = "flame"
    FAN = "fan"
    LIGHT = "light"
    FRONT = "front"
    AUX = "aux"
    CPI = "cpi"


@dataclass(frozen=True)
class ProtocolProfile:
    """Internal transport/protocol profile definition."""

    id: str
    label: str
    frequency_hz: int
    modulation: ProtocolModulation
    data_rate_bps: int
    tx_repeat_count: int
    supported_features: frozenset[ProtocolFeature] = field(default_factory=frozenset)
    default_rx_frequency_hz: int | None = None
    inter_frame_gap_ms: float | None = None
    air_payload_bit_length: int | None = None
    timing_quirks: tuple[str, ...] = ()


GENERIC_PROFLAME2_315 = ProtocolProfile(
    id="generic_proflame2_315",
    label="Generic Proflame2 / 315 MHz",
    frequency_hz=314_973_000,
    modulation=ProtocolModulation.ASK_OOK,
    data_rate_bps=2_400,
    tx_repeat_count=5,
    supported_features=frozenset(
        {
            ProtocolFeature.FLAME,
            ProtocolFeature.FAN,
            ProtocolFeature.LIGHT,
            ProtocolFeature.FRONT,
            ProtocolFeature.AUX,
            ProtocolFeature.CPI,
        }
    ),
    default_rx_frequency_hz=315_000_000,
    inter_frame_gap_ms=5.2,
    air_payload_bit_length=182,
)

DEFAULT_PROTOCOL_PROFILE = GENERIC_PROFLAME2_315
DEFAULT_PROTOCOL_PROFILE_ID = DEFAULT_PROTOCOL_PROFILE.id

PROTOCOL_PROFILES: dict[str, ProtocolProfile] = {
    GENERIC_PROFLAME2_315.id: GENERIC_PROFLAME2_315,
}


def get_protocol_profile(profile_id: str) -> ProtocolProfile:
    """Return a registered protocol profile by id."""

    try:
        return PROTOCOL_PROFILES[profile_id]
    except KeyError as exc:
        raise KeyError(f"Unknown Proflame2 protocol profile: {profile_id}") from exc
