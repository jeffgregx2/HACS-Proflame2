"""Packet decoder for Proflame 2 fireplace frames."""

from __future__ import annotations

from .ecc import err1_for, err2_for
from .models import FireplaceState, RemoteProfile
from .packet import ProflameFrame, ProflamePacket, state_from_frame


def decode_state(frame: ProflameFrame, profile: RemoteProfile) -> FireplaceState:
    """Decode a frame into fireplace state and validate ECC bytes."""

    expected_err1 = err1_for(frame.cmd1, profile.ecc)
    expected_err2 = err2_for(frame.cmd2, profile.ecc)
    if frame.err1 != expected_err1:
        raise ValueError("Cmd1 validation byte does not match profile constants.")
    if frame.err2 != expected_err2:
        raise ValueError("Cmd2 validation byte does not match profile constants.")
    if frame.serial_id != profile.serial_id:
        raise ValueError("Frame serial_id does not match the configured remote profile.")

    return state_from_frame(frame)


def decode_bytes(payload: bytes, profile: RemoteProfile) -> FireplaceState:
    """Decode the canonical byte payload into state."""

    return decode_state(ProflameFrame.from_bytes(payload), profile)


def decode_packet(
    frame: ProflameFrame,
    profile: RemoteProfile,
    *,
    source: str | None = None,
    raw: bytes | str | None = None,
) -> ProflamePacket:
    """Decode a validated frame into the unified operational packet model."""

    decode_state(frame, profile)
    return ProflamePacket.from_frame(frame, source=source, raw=raw)
