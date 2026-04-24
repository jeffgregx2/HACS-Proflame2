"""ESPHome-backed RF node placeholder."""

from __future__ import annotations

from .base import BackendCapabilities, CaptureResult, RFBackend


class ESPHomeAPIBackend(RFBackend):
    """Backend for a future ESP32 + CC1101 production node."""

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def send(self, packet):
        raise NotImplementedError("ESPHome backend send is not implemented yet.")

    async def receive(self, timeout: float | None = None):
        raise NotImplementedError("ESPHome backend receive is not implemented yet.")

    async def learn(self, timeout: float | None = None) -> CaptureResult:
        raise NotImplementedError("ESPHome backend learning is not implemented yet.")

    async def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            can_send=True,
            can_receive=True,
            can_learn=False,
            notes=("Production-oriented network backend for ESP32 + CC1101 nodes.",),
        )
