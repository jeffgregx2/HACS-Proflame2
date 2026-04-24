"""Abstract RF backend definitions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from proflame2_protocol.models import FireplaceState
from proflame2_protocol.packet import ProflameFrame, ProflamePacket
from .capture import CaptureSample


@dataclass(frozen=True)
class BackendCapabilities:
    """Describes what an RF backend supports."""

    can_send: bool = True
    can_receive: bool = True
    can_learn: bool = True
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class CaptureResult:
    """Result of a remote-learning capture pass."""

    serial_id: int
    packets: tuple[ProflamePacket, ...] = field(default_factory=tuple)
    samples: tuple[CaptureSample, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def frames(self) -> tuple[ProflameFrame, ...]:
        """Backward-compatible view of learned logical frames."""

        return tuple(packet.frame for packet in self.packets)


@dataclass(frozen=True)
class SendResult:
    """Outcome of preparing or sending a Proflame 2 frame."""

    packet: ProflamePacket
    backend_name: str
    echo_seen: bool = False
    echo_delay_ms: float | None = None
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def requested_state(self) -> FireplaceState:
        """Return the semantic state requested for transmission."""

        return self.packet.state

    @property
    def encoded_frame(self) -> ProflameFrame:
        """Return the protocol frame prepared for transmission."""

        return self.packet.frame


class RFBackend(ABC):
    """Transport abstraction used by the Home Assistant coordinator layer."""

    @abstractmethod
    async def connect(self) -> None:
        """Open the backend connection."""

    @abstractmethod
    async def close(self) -> None:
        """Close the backend connection."""

    @abstractmethod
    async def send(self, packet: ProflamePacket) -> SendResult:
        """Transmit a packet to the fireplace."""

    @abstractmethod
    async def receive(self, timeout: float | None = None) -> ProflamePacket | None:
        """Receive a frame from the backend."""

    @abstractmethod
    async def learn(self, timeout: float | None = None) -> CaptureResult:
        """Capture enough data to learn a remote profile."""

    @abstractmethod
    async def capabilities(self) -> BackendCapabilities:
        """Return backend capabilities."""
