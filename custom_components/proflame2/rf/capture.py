"""Helpers for Proflame 2 RF capture decoding.

The receive-side parsing here intentionally decodes the same on-air symbol
structure reproduced from the SmartFire transmitter implementation:

- repository: ``https://github.com/JoelB/smartfire``
- transmitter reference:
  ``https://github.com/JoelB/smartfire/blob/main/smartfire_controller/fireplace.py``

The acquisition strategy is also informed by rtl_433's Proflame2 decoder
(``protocol 207``), which treats Proflame2 as:

- OOK pulse PCM
- 2400 baud (417 us short/long width)
- 7 words x 13 bits = 91 decoded bits
- each word begins with sync/start ``1110``
- 5 repeated transmissions separated by 12 low bits

rtl_433 does not assume the protocol begins at byte offset zero inside raw SDR
data; it first acquires candidate pulse rows, then validates structure. Our
Yard Stick receive path mirrors that principle by scanning candidate bit and
symbol offsets inside each ``RFrecv()`` payload instead of assuming that the
first byte returned by ``rflib`` is already aligned to a valid Proflame2 word.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from ..protocol.packet import ProflameFrame, ProflamePacket
from .waveform import (
    AIR_PACKET_BYTES,
    BITS_TO_SYMBOL,
    PROFLAME_WORD_COUNT,
    SYMBOLS_PER_WORD,
    TOTAL_SYMBOLS,
    TRAILING_ZERO_SYMBOLS,
)
from .waveform import (
    air_bytes_to_symbols as waveform_air_bytes_to_symbols,
)
from .waveform import (
    frame_to_air_bytes as waveform_frame_to_air_bytes,
)

REASON_PAYLOAD_TOO_SHORT = "payload_too_short"
REASON_INVALID_FRAME_LENGTH = "invalid_frame_length"
REASON_INVALID_MANCHESTER_PAIR = "invalid_manchester_pair"
REASON_INVALID_MANCHESTER_SYMBOLS = "invalid_manchester_symbols"
REASON_BAD_START_END_GUARD = "bad_start_end_guard"
REASON_BAD_PARITY = "bad_parity"
REASON_BAD_TRAILING_ZERO_GUARD = "bad_trailing_zero_guard"
REASON_INVALID_COMMAND_LAYOUT = "invalid_command_layout"
REASON_WORD_COUNT_MISMATCH = "word_count_mismatch"
REASON_UNKNOWN_DECODE_FAILURE = "unknown_decode_failure"


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
        warnings: tuple[str, ...] | list[str] | None = None,
    ) -> ProflamePacket:
        """Return this sample as a unified operational packet."""

        return ProflamePacket.from_frame(
            self.as_frame(),
            source=source,
            raw=self.raw_payload,
            received_at=received_at,
            rssi=rssi,
            warnings=warnings,
        )


@dataclass(frozen=True)
class DecodeFailure:
    """Detailed reason why one candidate packet window failed to decode."""

    reason: str
    detail: str
    stage_score: int = 0
    bit_offset: int = 0
    symbol_offset: int = 0
    symbol_window: str = ""
    extracted_words: tuple[str, ...] = ()
    candidate_remote_id: int | None = None
    candidate_cmd1: int | None = None
    candidate_err1: int | None = None
    candidate_cmd2: int | None = None
    candidate_err2: int | None = None


@dataclass(frozen=True)
class DecodeDiagnostics:
    """Verbose receive/decode analysis for troubleshooting RF capture issues."""

    payload_length: int
    raw_payload_hex: str
    bit_stream: str
    symbols: str | None
    samples_found: int
    candidates: tuple[DecodeCandidate, ...] = ()
    reason_counts: dict[str, int] = field(default_factory=dict)
    best_failure: DecodeFailure | None = None


@dataclass(frozen=True)
class DecodeCandidate:
    """One valid Proflame2 frame found inside a raw RF payload."""

    bit_offset: int
    symbol_offset: int
    absolute_bit_offset: int
    raw_slice: bytes
    sample: CaptureSample
    frame: ProflameFrame
    packet: ProflamePacket
    repeat_count: int = 1
    confidence: int = 100
    trailing_guard_valid: bool = True
    trailing_guard_observed: str = ""
    trailing_guard_warning: str | None = None
    validation_notes: tuple[str, ...] = ()
    occurrence_offsets: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True)
class DecodeAcceptance:
    """A structurally valid frame candidate accepted by the RX scanner."""

    sample: CaptureSample
    trailing_guard_valid: bool
    trailing_guard_observed: str
    trailing_guard_warning: str | None
    validation_notes: tuple[str, ...]


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


def raw_payload_to_bit_stream(raw_payload: bytes) -> str:
    """Return the payload as a contiguous bit stream for debug output."""

    return "".join(f"{byte:08b}" for byte in raw_payload)


def extract_samples_from_air_bytes(raw_payload: bytes) -> list[CaptureSample]:
    """Extract every valid Proflame capture from a raw air payload."""

    return [candidate.sample for candidate in find_proflame_candidates(raw_payload)]


def decode_single_sample(raw_payload: bytes) -> CaptureSample:
    """Return the best valid decoded sample from a raw payload."""

    candidates = find_proflame_candidates(raw_payload)
    if not candidates:
        raise ValueError("No Proflame 2 packet could be decoded from the raw payload.")
    return candidates[0].sample


def find_proflame_candidates(raw_payload: bytes) -> list[DecodeCandidate]:
    """Scan one raw RF payload for valid embedded Proflame2 frames.

    The raw payload returned by ``RFrecv()`` may:

    - begin before the actual Proflame burst
    - begin in the middle of a burst
    - contain trailing noise after a valid frame
    - contain more than one repeated frame

    This helper therefore scans all plausible bit alignments and symbol windows
    and returns the strongest valid frame candidates it can find.
    """

    bit_stream = raw_payload_to_bit_stream(raw_payload)
    if len(bit_stream) < TOTAL_SYMBOLS * 2:
        return []

    occurrences: dict[
        tuple[int, int, int, int, int],
        list[tuple[int, int, int, DecodeAcceptance]],
    ] = {}
    for bit_offset in range(min(8, len(bit_stream))):
        symbols = tolerant_symbols_from_bit_stream(bit_stream, bit_offset=bit_offset)
        if len(symbols) < TOTAL_SYMBOLS:
            continue
        for symbol_offset in range(0, len(symbols) - TOTAL_SYMBOLS + 1):
            acceptance, _failure = _sample_from_symbol_window(
                symbols[symbol_offset : symbol_offset + TOTAL_SYMBOLS],
                raw_payload,
                bit_offset=bit_offset,
                symbol_offset=symbol_offset,
            )
            if acceptance is None:
                continue
            sample = acceptance.sample
            key = (
                sample.remote_id,
                sample.cmd1,
                sample.err1,
                sample.cmd2,
                sample.err2,
            )
            occurrences.setdefault(key, []).append(
                (bit_offset, symbol_offset, bit_offset + (symbol_offset * 2), acceptance)
            )

    candidates: list[DecodeCandidate] = []
    for hits in occurrences.values():
        bit_offset, symbol_offset, absolute_bit_offset, acceptance = hits[0]
        sample = acceptance.sample
        frame = sample.as_frame()
        repeat_count = len(hits)
        raw_slice = _payload_slice_for_bit_range(
            raw_payload,
            absolute_bit_offset,
            TOTAL_SYMBOLS * 2,
        )
        validation_notes = list(acceptance.validation_notes)
        if repeat_count > 1:
            validation_notes.append(f"repeat_agreement={repeat_count}")
        if absolute_bit_offset % 8 != 0:
            validation_notes.append("non_byte_aligned_start")
        confidence = 100 + (repeat_count * 20)
        packet_warnings: list[str] = []
        if acceptance.trailing_guard_warning is not None:
            validation_notes.append("accepted_despite_trailing_guard_mismatch")
            packet_warnings.append(acceptance.trailing_guard_warning)
            confidence -= 20
            if repeat_count > 1:
                confidence += 10
        packet = sample.as_packet(warnings=packet_warnings)

        candidates.append(
            DecodeCandidate(
                bit_offset=bit_offset,
                symbol_offset=symbol_offset,
                absolute_bit_offset=absolute_bit_offset,
                raw_slice=raw_slice,
                sample=sample,
                frame=frame,
                packet=packet,
                repeat_count=repeat_count,
                confidence=confidence,
                trailing_guard_valid=acceptance.trailing_guard_valid,
                trailing_guard_observed=acceptance.trailing_guard_observed,
                trailing_guard_warning=acceptance.trailing_guard_warning,
                validation_notes=tuple(validation_notes),
                occurrence_offsets=tuple((hit[0], hit[1]) for hit in hits),
            )
        )

    candidates.sort(
        key=lambda candidate: (
            candidate.repeat_count,
            candidate.confidence,
            -candidate.absolute_bit_offset,
        ),
        reverse=True,
    )
    return candidates


def diagnose_air_payload(raw_payload: bytes) -> DecodeDiagnostics:
    """Return verbose decode diagnostics for a raw RF payload."""

    bit_stream = raw_payload_to_bit_stream(raw_payload)
    if len(bit_stream) < TOTAL_SYMBOLS * 2:
        best_failure = DecodeFailure(
            reason=REASON_PAYLOAD_TOO_SHORT,
            detail=(
                f"Payload too short: expected at least {TOTAL_SYMBOLS * 2} bits "
                f"({AIR_PACKET_BYTES} bytes), got {len(bit_stream)} bits "
                f"({len(raw_payload)} bytes)."
            ),
            stage_score=0,
        )
        return DecodeDiagnostics(
            payload_length=len(raw_payload),
            raw_payload_hex=raw_payload.hex(),
            bit_stream=bit_stream,
            symbols=None,
            samples_found=0,
            reason_counts={REASON_PAYLOAD_TOO_SHORT: 1},
            best_failure=best_failure,
        )

    failures: list[DecodeFailure] = []
    for bit_offset in range(min(8, len(bit_stream))):
        symbols = tolerant_symbols_from_bit_stream(bit_stream, bit_offset=bit_offset)
        if len(symbols) < TOTAL_SYMBOLS:
            failures.append(
                DecodeFailure(
                    reason=REASON_WORD_COUNT_MISMATCH,
                    detail=(
                        f"Bit alignment {bit_offset} leaves only {len(symbols)} symbols; "
                        f"expected at least {TOTAL_SYMBOLS}."
                    ),
                    stage_score=0,
                    bit_offset=bit_offset,
                )
            )
            continue
        for symbol_offset in range(0, len(symbols) - TOTAL_SYMBOLS + 1):
            acceptance, failure = _sample_from_symbol_window(
                symbols[symbol_offset : symbol_offset + TOTAL_SYMBOLS],
                raw_payload,
                bit_offset=bit_offset,
                symbol_offset=symbol_offset,
            )
            if acceptance is None and failure is not None:
                failures.append(failure)

    candidates = tuple(find_proflame_candidates(raw_payload))
    symbols = tolerant_symbols_from_bit_stream(bit_stream, bit_offset=0) if bit_stream else None
    reason_counts = dict(Counter(failure.reason for failure in failures))
    best_failure = max(failures, key=lambda failure: failure.stage_score, default=None)
    if best_failure is None and not candidates:
        best_failure = DecodeFailure(
            reason=REASON_UNKNOWN_DECODE_FAILURE,
            detail="The payload did not match any supported Proflame2 framing.",
            stage_score=0,
        )
        reason_counts = {REASON_UNKNOWN_DECODE_FAILURE: 1}

    return DecodeDiagnostics(
        payload_length=len(raw_payload),
        raw_payload_hex=raw_payload.hex(),
        bit_stream=bit_stream,
        symbols=symbols,
        samples_found=len(candidates),
        candidates=candidates,
        reason_counts=reason_counts,
        best_failure=best_failure,
    )


def _sample_from_symbol_window(
    symbols: str,
    raw_payload: bytes,
    *,
    bit_offset: int,
    symbol_offset: int,
) -> tuple[DecodeAcceptance | None, DecodeFailure | None]:
    """Parse one candidate symbol window into a capture sample or failure.

    The expected symbol layout is the receive-side inverse of the SmartFire
    transmit structure documented in:

    - ``https://github.com/JoelB/smartfire``
    - ``https://github.com/JoelB/smartfire/blob/main/smartfire_controller/fireplace.py``
    """

    if len(symbols) != TOTAL_SYMBOLS:
        return None, DecodeFailure(
            reason=REASON_INVALID_FRAME_LENGTH,
            detail=f"Expected symbol window of length {TOTAL_SYMBOLS}, got {len(symbols)}.",
            stage_score=0,
            bit_offset=bit_offset,
            symbol_offset=symbol_offset,
            symbol_window=symbols,
        )

    words: list[str] = []
    for word_index in range(PROFLAME_WORD_COUNT):
        offset = word_index * SYMBOLS_PER_WORD
        chunk = symbols[offset : offset + SYMBOLS_PER_WORD]
        if len(chunk) != SYMBOLS_PER_WORD:
            return None, DecodeFailure(
                reason=REASON_WORD_COUNT_MISMATCH,
                detail=(f"Word {word_index} expected {SYMBOLS_PER_WORD} symbols, got {len(chunk)}."),
                stage_score=1,
                bit_offset=bit_offset,
                symbol_offset=symbol_offset,
                symbol_window=symbols,
                extracted_words=tuple(words),
            )
        if chunk[0] != "S" or chunk[1] != "1" or chunk[-1] != "1":
            return None, DecodeFailure(
                reason=REASON_BAD_START_END_GUARD,
                detail=f"Word {word_index} has invalid start/end guards: {chunk!r}.",
                stage_score=1,
                bit_offset=bit_offset,
                symbol_offset=symbol_offset,
                symbol_window=symbols,
                extracted_words=tuple(words),
            )

        word_bits = chunk[2:11]
        parity_bit = chunk[11]
        if any(bit not in {"0", "1"} for bit in word_bits):
            return None, DecodeFailure(
                reason=REASON_INVALID_MANCHESTER_SYMBOLS,
                detail=f"Word {word_index} contains non-binary symbol data: {word_bits!r}.",
                stage_score=2,
                bit_offset=bit_offset,
                symbol_offset=symbol_offset,
                symbol_window=symbols,
                extracted_words=tuple(words),
            )
        if parity_bit not in {"0", "1"}:
            return None, DecodeFailure(
                reason=REASON_BAD_PARITY,
                detail=f"Word {word_index} has invalid parity symbol: {parity_bit!r}.",
                stage_score=2,
                bit_offset=bit_offset,
                symbol_offset=symbol_offset,
                symbol_window=symbols,
                extracted_words=tuple(words),
            )
        if int(parity_bit) != (word_bits.count("1") % 2):
            return None, DecodeFailure(
                reason=REASON_BAD_PARITY,
                detail=(
                    f"Word {word_index} parity mismatch: expected {word_bits.count('1') % 2}, " f"got {parity_bit}."
                ),
                stage_score=2,
                bit_offset=bit_offset,
                symbol_offset=symbol_offset,
                symbol_window=symbols,
                extracted_words=tuple(words + [word_bits]),
            )
        words.append(word_bits)

    trailer = symbols[PROFLAME_WORD_COUNT * SYMBOLS_PER_WORD :]
    trailing_guard_valid = trailer == ("Z" * TRAILING_ZERO_SYMBOLS)
    trailing_guard_warning: str | None = None
    if not trailing_guard_valid:
        accepted_trailer = _accepted_partial_trailing_guard(trailer)
        if accepted_trailer is None:
            return None, DecodeFailure(
                reason=REASON_BAD_TRAILING_ZERO_GUARD,
                detail=f"Trailing symbols do not match {'Z' * TRAILING_ZERO_SYMBOLS!r}: {trailer!r}.",
                stage_score=3,
                bit_offset=bit_offset,
                symbol_offset=symbol_offset,
                symbol_window=symbols,
                extracted_words=tuple(words),
            )
        trailing_guard_warning = accepted_trailer

    candidate_remote_id = (int(words[0][:8], 2) << 16) | (int(words[1][:8], 2) << 8) | int(words[2][:8], 2)
    candidate_cmd1 = int(words[3][:8], 2)
    candidate_cmd2 = int(words[4][:8], 2)
    candidate_err1 = int(words[5][:8], 2)
    candidate_err2 = int(words[6][:8], 2)

    if words[0][-1] != "1" or words[1][-1] != "0" or words[2][-1] != "0":
        return None, DecodeFailure(
            reason=REASON_INVALID_COMMAND_LAYOUT,
            detail="Remote serial words do not carry the expected trailing-bit pattern 1/0/0.",
            stage_score=4,
            bit_offset=bit_offset,
            symbol_offset=symbol_offset,
            symbol_window=symbols,
            extracted_words=tuple(words),
            candidate_remote_id=candidate_remote_id,
            candidate_cmd1=candidate_cmd1,
            candidate_cmd2=candidate_cmd2,
            candidate_err1=candidate_err1,
            candidate_err2=candidate_err2,
        )
    if any(word[-1] != "0" for word in words[3:]):
        return None, DecodeFailure(
            reason=REASON_INVALID_COMMAND_LAYOUT,
            detail="Cmd/Err words do not carry the expected trailing zero bit layout.",
            stage_score=4,
            bit_offset=bit_offset,
            symbol_offset=symbol_offset,
            symbol_window=symbols,
            extracted_words=tuple(words),
            candidate_remote_id=candidate_remote_id,
            candidate_cmd1=candidate_cmd1,
            candidate_cmd2=candidate_cmd2,
            candidate_err1=candidate_err1,
            candidate_err2=candidate_err2,
        )

    sample = CaptureSample(
        remote_id=candidate_remote_id,
        cmd1=candidate_cmd1,
        err1=candidate_err1,
        cmd2=candidate_cmd2,
        err2=candidate_err2,
        raw_payload=raw_payload,
        symbols=symbols,
    )
    return (
        DecodeAcceptance(
            sample=sample,
            trailing_guard_valid=trailing_guard_valid,
            trailing_guard_observed=trailer,
            trailing_guard_warning=trailing_guard_warning,
            validation_notes=(
                "rtl_433_style_scanned_alignment",
                "sync_start_guards_valid",
                "per_word_parity_valid",
                "13_bit_word_count_valid",
                "command_layout_valid",
                "trailing_guard_valid" if trailing_guard_valid else "trailing_guard_partial",
            ),
        ),
        None,
    )


def tolerant_symbols_from_bit_stream(bit_stream: str, *, bit_offset: int) -> str:
    """Return a tolerant symbol stream for one bit alignment.

    Valid Manchester pairs are mapped to the Proflame symbol alphabet.
    Invalid pairs are mapped to ``?`` so the scanning logic can continue past
    local corruption or misalignment and still find later valid frames.
    """

    symbols: list[str] = []
    for index in range(bit_offset, len(bit_stream) - 1, 2):
        pair = bit_stream[index : index + 2]
        symbols.append(BITS_TO_SYMBOL.get(pair, "?"))
    return "".join(symbols)


def _payload_slice_for_bit_range(raw_payload: bytes, start_bit: int, length_bits: int) -> bytes:
    """Return the enclosing byte slice for one candidate bit range."""

    start_byte = start_bit // 8
    end_byte = (start_bit + length_bits + 7) // 8
    return raw_payload[start_byte:end_byte]


def _accepted_partial_trailing_guard(trailer: str) -> str | None:
    """Return a warning string if one imperfect trailer is still acceptable."""

    leading_z_count = 0
    for symbol in trailer:
        if symbol != "Z":
            break
        leading_z_count += 1

    if leading_z_count >= 4:
        return (
            "Trailing guard was partially clipped/noisy but the frame body "
            f"validated successfully: observed {trailer!r}."
        )
    return None
