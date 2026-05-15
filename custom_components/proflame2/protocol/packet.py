"""Packet structures for encoded Proflame 2 state frames."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .models import FireplaceState

if TYPE_CHECKING:
    from ..rf.waveform import ProflameTransmissionPlan


@dataclass(frozen=True)
class ProflameFrame:
    """Encoded Proflame 2 frame content."""

    serial_id: int
    cmd1: int
    err1: int
    cmd2: int
    err2: int

    def as_bytes(self) -> bytes:
        """Return the canonical seven-byte payload."""

        return bytes(
            [
                (self.serial_id >> 16) & 0xFF,
                (self.serial_id >> 8) & 0xFF,
                self.serial_id & 0xFF,
                self.cmd1 & 0xFF,
                self.err1 & 0xFF,
                self.cmd2 & 0xFF,
                self.err2 & 0xFF,
            ]
        )

    @classmethod
    def from_bytes(cls, payload: bytes) -> "ProflameFrame":
        """Build a frame from the canonical byte payload."""

        if len(payload) != 7:
            raise ValueError("Proflame payloads must be 7 bytes long.")
        return cls(
            serial_id=(payload[0] << 16) | (payload[1] << 8) | payload[2],
            cmd1=payload[3],
            err1=payload[4],
            cmd2=payload[5],
            err2=payload[6],
        )


@dataclass
class ProflamePacket:
    """Operational packet model used by both transmit and receive paths.

    The packet ties together:

    - semantic fireplace state
    - protocol frame bytes
    - transport/runtime metadata such as raw RF payloads, receive source,
      waveform plan, and warning annotations
    """

    remote_id: int
    state: FireplaceState
    frame: ProflameFrame
    raw: bytes | str | None = None
    source: str | None = None
    received_at: datetime | None = None
    rssi: float | None = None
    transmission_plan: "ProflameTransmissionPlan | None" = None
    is_echo_candidate: bool = False
    echo_delay_ms: float | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        """Keep the operational packet aligned with the logical frame."""

        if self.remote_id != self.frame.serial_id:
            raise ValueError("Packet remote_id must match frame.serial_id.")

    @classmethod
    def from_frame(
        cls,
        frame: ProflameFrame,
        *,
        source: str | None = None,
        raw: bytes | str | None = None,
        received_at: datetime | None = None,
        rssi: float | None = None,
        transmission_plan: "ProflameTransmissionPlan | None" = None,
        is_echo_candidate: bool = False,
        echo_delay_ms: float | None = None,
        warnings: tuple[str, ...] | list[str] | None = None,
    ) -> "ProflamePacket":
        """Build an operational packet from a logical frame.

        State decoding here intentionally does not require ECC constants. The
        command bytes are sufficient to recover the semantic fireplace state,
        which lets receive/learning paths normalize packets before a remote
        profile is fully known.
        """

        return cls(
            remote_id=frame.serial_id,
            state=state_from_frame(frame),
            frame=frame,
            raw=raw,
            source=source,
            received_at=received_at,
            rssi=rssi,
            transmission_plan=transmission_plan,
            is_echo_candidate=is_echo_candidate,
            echo_delay_ms=echo_delay_ms,
            warnings=tuple(warnings or ()),
        )


def state_from_frame(frame: ProflameFrame) -> FireplaceState:
    """Decode semantic state directly from a logical frame.

    This is the state-only interpretation of the protocol. It intentionally
    ignores ECC/profile validation because the command bytes alone define the
    semantic fireplace state.
    """

    state = FireplaceState(
        power=bool(frame.cmd1 & 0x01),
        thermostat=bool(frame.cmd1 & 0x02),
        light=(frame.cmd1 >> 4) & 0x07,
        cpi=bool(frame.cmd1 & 0x80),
        flame=frame.cmd2 & 0x07,
        aux=bool(frame.cmd2 & 0x08),
        fan=(frame.cmd2 >> 4) & 0x07,
        front=bool(frame.cmd2 & 0x80),
    )
    state.validate_observed(allow_thermostat=True)
    return state
