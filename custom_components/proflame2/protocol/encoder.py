"""State encoder for Proflame 2 fireplace packets."""

from __future__ import annotations

from .ecc import err1_for, err2_for
from .models import FireplaceState, RemoteProfile
from .packet import ProflameFrame, ProflamePacket


def build_cmd1(state: FireplaceState) -> int:
    """Encode Cmd1 using the validated protocol mapping."""

    return (
        (1 if state.power else 0)
        | ((1 if state.thermostat else 0) << 1)
        | ((state.light & 0x07) << 4)
        | ((1 if state.cpi else 0) << 7)
    )


def build_cmd2(state: FireplaceState) -> int:
    """Encode Cmd2 using the validated protocol mapping."""

    return (
        (state.flame & 0x07)
        | ((1 if state.aux else 0) << 3)
        | ((state.fan & 0x07) << 4)
        | ((1 if state.front else 0) << 7)
    )


def encode_state(
    state: FireplaceState,
    profile: RemoteProfile,
    *,
    allow_power_off_flame: bool = False,
) -> ProflameFrame:
    """Encode a full-state fireplace command into a deterministic frame."""

    state.validate_transmit(allow_power_off_flame=allow_power_off_flame)
    cmd1 = build_cmd1(state)
    cmd2 = build_cmd2(state)

    return ProflameFrame(
        serial_id=profile.serial_id,
        cmd1=cmd1,
        err1=err1_for(cmd1, profile.ecc),
        cmd2=cmd2,
        err2=err2_for(cmd2, profile.ecc),
    )


def encode_packet(
    state: FireplaceState,
    profile: RemoteProfile,
    *,
    source: str | None = None,
    warnings: tuple[str, ...] | list[str] | None = None,
    allow_power_off_flame: bool = False,
) -> ProflamePacket:
    """Encode a semantic fireplace state into an operational packet."""

    frame = encode_state(
        state,
        profile,
        allow_power_off_flame=allow_power_off_flame,
    )
    return ProflamePacket(
        remote_id=profile.serial_id,
        state=state,
        frame=frame,
        source=source,
        warnings=tuple(warnings or ()),
    )
