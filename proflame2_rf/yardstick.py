"""Yard Stick One receive/learn backend."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import time
from typing import Any

from .base import BackendCapabilities, CaptureResult, RFBackend
from .capture import CaptureSample, extract_samples_from_air_bytes

PROFLAME2_FREQUENCY_HZ = 315_000_000
PROFLAME2_DATA_RATE = 2_400
PROFLAME2_PACKET_BYTES = 25


class YardStickDependencyError(RuntimeError):
    """Raised when the Yard Stick runtime dependencies are missing."""


class YardStickBackend(RFBackend):
    """Backend used for development-time RX and learning."""

    def __init__(
        self,
        *,
        device_index: int = 0,
        frequency_hz: int = PROFLAME2_FREQUENCY_HZ,
        data_rate: int = PROFLAME2_DATA_RATE,
        radio: Any | None = None,
    ) -> None:
        self._device_index = device_index
        self._frequency_hz = frequency_hz
        self._data_rate = data_rate
        self._radio = radio
        self._timeout_exception: type[Exception] | None = None
        self._owns_radio = radio is None

    async def connect(self) -> None:
        """Open the Yard Stick and configure it for Proflame2 receive."""

        if self._radio is None:
            try:
                from rflib import MOD_ASK_OOK, RfCat
                from rflib import ChipconUsbTimeoutException
            except ImportError as exc:
                raise YardStickDependencyError(
                    "Yard Stick One support requires the 'rflib' package in the active Python environment."
                ) from exc

            self._radio = RfCat(idx=self._device_index)
            self._timeout_exception = ChipconUsbTimeoutException
            modulation = MOD_ASK_OOK
        else:
            modulation = getattr(self._radio, "MOD_ASK_OOK", None)
            if modulation is None:
                try:
                    from rflib import MOD_ASK_OOK
                except ImportError:
                    modulation = 0x30
                else:
                    modulation = MOD_ASK_OOK

        await asyncio.to_thread(self._configure_radio, modulation)

    async def close(self) -> None:
        """Close the backend connection."""

        if self._radio is None:
            return None
        try:
            if hasattr(self._radio, "setModeIDLE"):
                await asyncio.to_thread(self._radio.setModeIDLE)
            if self._owns_radio and hasattr(self._radio, "close"):
                await asyncio.to_thread(self._radio.close)
        finally:
            if self._owns_radio:
                self._radio = None

    async def send(self, packet):
        raise NotImplementedError("Yard Stick send is not implemented yet.")

    async def receive(self, timeout: float | None = None):
        """Receive and decode one Proflame2 frame when possible."""

        sample = await self.receive_sample(timeout=timeout)
        return (
            None
            if sample is None
            else sample.as_packet(
                source="yardstick",
                received_at=datetime.now(timezone.utc),
            )
        )

    async def receive_sample(self, timeout: float | None = None) -> CaptureSample | None:
        """Receive and decode one Proflame2 sample when possible."""

        if self._radio is None:
            raise RuntimeError("YardStickBackend.connect() must be called before receive().")

        raw_payload = await asyncio.to_thread(self._recv_once, timeout)
        if raw_payload is None:
            return None

        samples = extract_samples_from_air_bytes(raw_payload)
        if not samples:
            return None
        return samples[0]

    async def learn(self, timeout: float | None = None) -> CaptureResult:
        """Collect decodable samples until the timeout expires."""

        deadline = None if timeout is None else (time.monotonic() + timeout)
        samples: list[CaptureSample] = []
        raw_payloads = 0
        decode_failures = 0

        while deadline is None or time.monotonic() < deadline:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            raw_payload = await asyncio.to_thread(self._recv_once, remaining)
            if raw_payload is None:
                break
            raw_payloads += 1
            decoded = extract_samples_from_air_bytes(raw_payload)
            if not decoded:
                decode_failures += 1
                continue
            samples.extend(decoded)

        serial_id = samples[-1].remote_id if samples else 0
        return CaptureResult(
            serial_id=serial_id,
            packets=tuple(
                sample.as_packet(
                    source="yardstick",
                    received_at=datetime.now(timezone.utc),
                )
                for sample in samples
            ),
            samples=tuple(samples),
            metadata={
                "frequency_hz": self._frequency_hz,
                "data_rate": self._data_rate,
                "raw_payloads_seen": raw_payloads,
                "decode_failures": decode_failures,
            },
        )

    async def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            can_send=False,
            can_receive=True,
            can_learn=True,
            notes=(
                "Receive path tunes the Yard Stick One for ASK/OOK at 315 MHz.",
                "Successful decodes are normalized into Proflame remote_id/cmd/err samples.",
                "Undecodable payloads are counted in learn-mode metadata for follow-up decoder work.",
            ),
        )

    def _configure_radio(self, modulation: int) -> None:
        """Apply the radio settings used for Proflame2 receive."""

        assert self._radio is not None
        if hasattr(self._radio, "setModeIDLE"):
            self._radio.setModeIDLE()
        self._radio.setFreq(self._frequency_hz)
        self._radio.setMdmModulation(modulation)
        self._radio.setMdmDRate(self._data_rate)
        if hasattr(self._radio, "makePktFLEN"):
            self._radio.makePktFLEN(PROFLAME2_PACKET_BYTES)
        if hasattr(self._radio, "setPktPQT"):
            self._radio.setPktPQT(0)
        if hasattr(self._radio, "setMdmSyncMode"):
            self._radio.setMdmSyncMode(0)
        if hasattr(self._radio, "setEnableMdmManchester"):
            self._radio.setEnableMdmManchester(False)

    def _recv_once(self, timeout: float | None) -> bytes | None:
        """Perform one blocking RF receive call."""

        assert self._radio is not None
        rf_timeout = 0 if timeout is None else max(1, int(timeout * 1000))
        try:
            payload, _ = self._radio.RFrecv(timeout=rf_timeout)
        except Exception as exc:
            if self._timeout_exception is not None and isinstance(exc, self._timeout_exception):
                return None
            raise

        if isinstance(payload, str):
            return payload.encode("latin1")
        return bytes(payload)
