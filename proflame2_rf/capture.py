"""Helpers for Proflame 2 RF capture decoding.

The receive-side parsing here intentionally decodes the same on-air symbol
structure reproduced from the SmartFire transmitter implementation:

- repository: ``https://github.com/JoelB/smartfire``
- transmitter reference:
  ``https://github.com/JoelB/smartfire/blob/main/smartfire_controller/fireplace.py``
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from proflame2_protocol.packet import ProflameFrame, ProflamePacket
from .waveform import (
    AIR_PACKET_BYTES,
    PROFLAME_WORD_COUNT,
    SYMBOLS_PER_WORD,
    TOTAL_SYMBOLS,
    TRAILING_ZERO_SYMBOLS,
    air_bytes_to_symbols as waveform_air_bytes_to_symbols,
    frame_to_air_bytes as waveform_frame_to_air_bytes,
)


@dataclass(frozen=True)
class CaptureSample:
    """A successfully decoded Proflame 2 capture."""

    remote_id: int
    cmd1: int
    err1: int
    cmd2: int
    err2: int
    raw_payload: bytes
    symbols: str

    @property
    def cmd1_tuple(self) -> tuple[int, int]:
        """Return the tuple used for Cmd1 ECC derivation."""

        return (self.cmd1, self.err1)

    @property
    def cmd2_tuple(self) -> tuple[int, int]:
        """Return the tuple used for Cmd2 ECC derivation."""

        return (self.cmd2, self.err2)

    def as_frame(self) -> ProflameFrame:
        """Return this sample as a protocol frame."""

        return ProflameFrame(
            serial_id=self.remote_id,
            cmd1=self.cmd1,
            err1=self.err1,
            cmd2=self.cmd2,
            err2=self.err2,
        )

    def as_packet(
        self,
        *,
        source: str | None = "capture",
        received_at: datetime | None = None,
        rssi: float | None = None,
    ) -> ProflamePacket:
        """Return this sample as a unified operational packet."""

        return ProflamePacket.from_frame(
            self.as_frame(),
            source=source,
            raw=self.raw_payload,
            received_at=received_at,
            rssi=rssi,
        )


def frames_from_fixture_rows(rows: Iterable[dict]) -> list[ProflameFrame]:
    """Parse simple fixture rows into frame objects."""

    frames: list[ProflameFrame] = []
    for row in rows:
        frames.append(
            ProflameFrame(
                serial_id=row["serial_id"],
                cmd1=row["cmd1"],
                err1=row["err1"],
                cmd2=row["cmd2"],
                err2=row["err2"],
            )
        )
    return frames


def frame_to_capture_sample(frame: ProflameFrame) -> CaptureSample:
    """Build a capture sample from a protocol frame.

    This uses the SmartFire-faithful waveform serializer so transmit-side and
    receive-side tests share one source of truth derived from:

    - ``https://github.com/JoelB/smartfire``
    """

    raw_payload = waveform_frame_to_air_bytes(frame)
    return CaptureSample(
        remote_id=frame.serial_id,
        cmd1=frame.cmd1,
        err1=frame.err1,
        cmd2=frame.cmd2,
        err2=frame.err2,
        raw_payload=raw_payload,
        symbols=air_bytes_to_symbols(raw_payload),
    )


def frame_to_air_bytes(frame: ProflameFrame) -> bytes:
    """Backward-compatible wrapper around the RF waveform module."""

    return waveform_frame_to_air_bytes(frame)


def air_bytes_to_symbols(raw_payload: bytes) -> str:
    """Backward-compatible wrapper around the RF waveform module."""

    return waveform_air_bytes_to_symbols(raw_payload)


def extract_samples_from_air_bytes(raw_payload: bytes) -> list[CaptureSample]:
    """Extract every valid Proflame capture from a raw air payload."""

    symbols = air_bytes_to_symbols(raw_payload)
    samples: list[CaptureSample] = []
    seen_frames: set[tuple[int, int, int, int, int]] = set()

    for start in range(0, max(0, len(symbols) - TOTAL_SYMBOLS + 1)):
        sample = _sample_from_symbol_window(symbols[start : start + TOTAL_SYMBOLS], raw_payload)
        if sample is None:
            continue
        key = (
            sample.remote_id,
            sample.cmd1,
            sample.err1,
            sample.cmd2,
            sample.err2,
        )
        if key in seen_frames:
            continue
        seen_frames.add(key)
        samples.append(sample)
    return samples


def decode_single_sample(raw_payload: bytes) -> CaptureSample:
    """Return exactly one decoded sample from a raw payload."""

    samples = extract_samples_from_air_bytes(raw_payload)
    if not samples:
        raise ValueError("No Proflame 2 packet could be decoded from the raw payload.")
    if len(samples) > 1:
        raise ValueError(f"Expected one Proflame packet, found {len(samples)}.")
    return samples[0]


def _sample_from_symbol_window(symbols: str, raw_payload: bytes) -> CaptureSample | None:
    """Parse one candidate symbol window into a capture sample.

    The expected symbol layout is the receive-side inverse of the SmartFire
    transmit structure documented in:

    - ``https://github.com/JoelB/smartfire``
    - ``https://github.com/JoelB/smartfire/blob/main/smartfire_controller/fireplace.py``
    """

    if len(symbols) != TOTAL_SYMBOLS:
        return None

    words: list[str] = []
    for word_index in range(PROFLAME_WORD_COUNT):
        offset = word_index * SYMBOLS_PER_WORD
        chunk = symbols[offset : offset + SYMBOLS_PER_WORD]
        if chunk[0] != "S" or chunk[1] != "1" or chunk[-1] != "1":
            return None

        word_bits = chunk[2:11]
        parity_bit = chunk[11]
        if parity_bit not in {"0", "1"}:
            return None
        if int(parity_bit) != (word_bits.count("1") % 2):
            return None
        words.append(word_bits)

    if symbols[PROFLAME_WORD_COUNT * SYMBOLS_PER_WORD :] != ("Z" * TRAILING_ZERO_SYMBOLS):
        return None

    if words[0][-1] != "1" or words[1][-1] != "0" or words[2][-1] != "0":
        return None
    if any(word[-1] != "0" for word in words[3:]):
        return None

    remote_id = (int(words[0][:8], 2) << 16) | (int(words[1][:8], 2) << 8) | int(words[2][:8], 2)
    return CaptureSample(
        remote_id=remote_id,
        cmd1=int(words[3][:8], 2),
        cmd2=int(words[4][:8], 2),
        err1=int(words[5][:8], 2),
        err2=int(words[6][:8], 2),
        raw_payload=raw_payload,
        symbols=symbols,
    )
