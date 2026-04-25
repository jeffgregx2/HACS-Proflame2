"""Fake RF backend for protocol and service-layer testing."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from ..protocol.packet import ProflameFrame, ProflamePacket

from .base import BackendCapabilities, CaptureResult, RFBackend, SendResult
from .capture import CaptureSample


@dataclass
class FakeRFBackend(RFBackend):
    """In-memory backend that records sent frames without touching hardware."""

    name: str = "fake"
    connected: bool = False
    sent_packets: list[ProflamePacket] = field(default_factory=list)
    sent_results: list[SendResult] = field(default_factory=list)
    receive_queue: list[ProflamePacket | None] = field(default_factory=list)
    learned_samples: list[CaptureSample] = field(default_factory=list)
    receive_delay_seconds: float = 0.0
    _next_receive_ready_monotonic: float | None = None

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.connected = False

    @property
    def sent_frames(self) -> list[ProflameFrame]:
        """Backward-compatible view of recorded protocol frames."""

        return [packet.frame for packet in self.sent_packets]

    async def send(self, packet: ProflamePacket) -> SendResult:
        if not self.connected:
            raise RuntimeError("FakeRFBackend.connect() must be called before send().")

        result = SendResult(
            packet=packet,
            backend_name=self.name,
            warnings=packet.warnings,
        )
        self.sent_packets.append(packet)
        self.sent_results.append(result)
        return result

    async def receive(self, timeout: float | None = None) -> ProflamePacket | None:
        if not self.connected:
            raise RuntimeError("FakeRFBackend.connect() must be called before receive().")
        if not self.receive_queue:
            self._next_receive_ready_monotonic = None
            return None
        if self.receive_delay_seconds > 0:
            now = time.monotonic()
            if self._next_receive_ready_monotonic is None:
                self._next_receive_ready_monotonic = now + self.receive_delay_seconds
            remaining = self._next_receive_ready_monotonic - now
            if remaining > 0:
                wait_time = remaining if timeout is None else min(timeout, remaining)
                if wait_time > 0:
                    await asyncio.sleep(wait_time)
                if time.monotonic() < self._next_receive_ready_monotonic:
                    return None
        packet = self.receive_queue.pop(0)
        self._next_receive_ready_monotonic = None
        return packet

    def queue_packets(self, *packets: ProflamePacket | None) -> None:
        """Append deterministic receive results for tests and dry runs."""

        self.receive_queue.extend(packets)

    async def learn(self, timeout: float | None = None) -> CaptureResult:
        packets = tuple(sample.as_packet(source=self.name) for sample in self.learned_samples)
        serial_id = self.learned_samples[-1].remote_id if self.learned_samples else 0
        return CaptureResult(
            serial_id=serial_id,
            packets=packets,
            samples=tuple(self.learned_samples),
            metadata={"backend": self.name},
        )

    async def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            can_send=True,
            can_receive=True,
            can_learn=True,
            notes=("In-memory backend for tests and dry-run integrations.",),
        )
