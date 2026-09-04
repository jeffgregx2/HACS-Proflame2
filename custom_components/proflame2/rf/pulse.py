"""Bounded OOK pulse/PCM decoding for LilyGO RMT receive.

The normal LilyGO path quantizes demodulated CC1101 GDO0 edge durations into a
PCM row. FIFO remains an explicit rollback path.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..protocol.packet import ProflameFrame
from .waveform import BITS_TO_SYMBOL, PROFLAME_WORD_COUNT, SYMBOLS_PER_WORD

PCM_UNIT_US = 417
STANDARD_WORD_BITS = PROFLAME_WORD_COUNT * SYMBOLS_PER_WORD * 2
EXTENDED_WORD_COUNT = 10
EXTENDED_WORD_BITS = EXTENDED_WORD_COUNT * SYMBOLS_PER_WORD * 2
MIN_REPEAT_GAP_BITS = 10
MAX_RUN_UNITS = 3
MAX_REPEAT_GAP_UNITS = 16


@dataclass(frozen=True)
class PulseDecodeCandidate:
    """One validated standard or extended frame reconstructed from PCM bits."""

    frame: ProflameFrame
    frame_format: str
    extension_words: tuple[int, ...]
    bit_offset: int
    repeat_gap_bits: int


def pulse_durations_to_bits(
    durations_us: Sequence[int],
    *,
    unit_us: int = PCM_UNIT_US,
) -> str:
    """Quantize signed OOK runs to a PCM bit row.

    Positive durations are high runs; negative durations are low runs. Long
    low runs are retained because a Proflame repeat boundary supplies the final
    low bit for the preceding word and the following separator bits.
    """

    if unit_us <= 0:
        raise ValueError("unit_us must be positive")

    bits: list[str] = []
    for duration_us in durations_us:
        if duration_us == 0:
            raise ValueError("zero-duration OOK run")
        units = int((abs(duration_us) + (unit_us // 2)) // unit_us)
        if units <= 0:
            raise ValueError(f"OOK run below one unit: {duration_us}")
        if units > MAX_RUN_UNITS:
            if duration_us > 0 or units > MAX_REPEAT_GAP_UNITS:
                raise ValueError(f"unsupported OOK run length: {duration_us}")
        bits.append(("1" if duration_us > 0 else "0") * units)
    return "".join(bits)


def find_proflame_pulse_candidates(
    durations_us: Sequence[int],
    *,
    unit_us: int = PCM_UNIT_US,
) -> list[PulseDecodeCandidate]:
    """Find validated Proflame2 frames in signed GDO0 pulse durations."""

    return find_proflame_pcm_candidates(pulse_durations_to_bits(durations_us, unit_us=unit_us))


def find_proflame_pcm_candidates(bit_stream: str) -> list[PulseDecodeCandidate]:
    """Find standard and explicit ten-word extended frames in a PCM row."""

    candidates: list[PulseDecodeCandidate] = []
    # The ESP32 RMT can end exactly after the first bit of the final `1`
    # Manchester symbol. `_decode_words` accepts only that one-bit truncation.
    for bit_offset in range(0, len(bit_stream) - (STANDARD_WORD_BITS - 1) + 1):
        words = _decode_words(bit_stream, bit_offset, PROFLAME_WORD_COUNT)
        if words is None or not _has_standard_word_layout(words):
            continue
        frame = ProflameFrame(
            serial_id=(words[0] << 16) | (words[1] << 8) | words[2],
            cmd1=words[3],
            cmd2=words[4],
            err1=words[5],
            err2=words[6],
        )
        standard_end = bit_offset + STANDARD_WORD_BITS
        if standard_end == len(bit_stream) + 1:
            candidates.append(
                PulseDecodeCandidate(
                    frame=frame,
                    frame_format="standard_7_word_truncated_end_guard",
                    extension_words=(),
                    bit_offset=bit_offset,
                    repeat_gap_bits=0,
                )
            )
            continue
        if bit_stream[standard_end : standard_end + 18] == "0" * 18:
            candidates.append(
                PulseDecodeCandidate(
                    frame=frame,
                    frame_format="standard_7_word",
                    extension_words=(),
                    bit_offset=bit_offset,
                    repeat_gap_bits=_leading_zero_count(bit_stream, standard_end),
                )
            )
            continue

        words = _decode_words(bit_stream, bit_offset, EXTENDED_WORD_COUNT)
        if words is None or not _has_extended_word_layout(words):
            continue
        extended_end = bit_offset + EXTENDED_WORD_BITS
        if extended_end == len(bit_stream) + 1:
            candidates.append(
                PulseDecodeCandidate(
                    frame=frame,
                    frame_format="extended_10_word_truncated_end_guard",
                    extension_words=tuple(words[7:]),
                    bit_offset=bit_offset,
                    repeat_gap_bits=0,
                )
            )
            continue
        repeat_gap_bits = _leading_zero_count(bit_stream, extended_end)
        if repeat_gap_bits < MIN_REPEAT_GAP_BITS:
            continue
        candidates.append(
            PulseDecodeCandidate(
                frame=frame,
                frame_format="extended_10_word",
                extension_words=tuple(words[7:]),
                bit_offset=bit_offset,
                repeat_gap_bits=repeat_gap_bits,
            )
        )

    return _deduplicate_candidates(candidates)


def _decode_words(bit_stream: str, bit_offset: int, word_count: int) -> tuple[int, ...] | None:
    end = bit_offset + (word_count * SYMBOLS_PER_WORD * 2)
    terminal_end_guard_truncated = end == len(bit_stream) + 1
    if end > len(bit_stream) and not terminal_end_guard_truncated:
        return None
    values: list[int] = []
    for word_index in range(word_count):
        start = bit_offset + (word_index * SYMBOLS_PER_WORD * 2)
        truncated_last_word = terminal_end_guard_truncated and word_index == word_count - 1
        symbol_end = start + ((SYMBOLS_PER_WORD - 1 if truncated_last_word else SYMBOLS_PER_WORD) * 2)
        symbols = "".join(
            BITS_TO_SYMBOL.get(bit_stream[index : index + 2], "?") for index in range(start, symbol_end, 2)
        )
        if symbols[0] != "S" or symbols[1] != "1":
            return None
        if truncated_last_word:
            if bit_stream[symbol_end:] != "1":
                return None
        elif symbols[-1] != "1":
            return None
        data_symbols = symbols[2:11]
        parity_symbol = symbols[11]
        if any(symbol not in {"0", "1"} for symbol in data_symbols) or parity_symbol not in {"0", "1"}:
            return None
        if int(parity_symbol) != (data_symbols.count("1") % 2):
            return None
        values.append(int(data_symbols[:8], 2))
        trailing_bit = data_symbols[8]
        if word_index < 3:
            expected_trailing = "1" if word_index == 0 else "0"
        else:
            expected_trailing = "0"
        if trailing_bit != expected_trailing:
            return None
    return tuple(values)


def _has_standard_word_layout(words: tuple[int, ...]) -> bool:
    return len(words) == PROFLAME_WORD_COUNT


def _has_extended_word_layout(words: tuple[int, ...]) -> bool:
    return len(words) == EXTENDED_WORD_COUNT


def _leading_zero_count(bit_stream: str, start: int) -> int:
    count = 0
    for bit in bit_stream[start:]:
        if bit != "0":
            break
        count += 1
    return count


def _deduplicate_candidates(candidates: list[PulseDecodeCandidate]) -> list[PulseDecodeCandidate]:
    """Keep one candidate for each frame/format pair at its earliest bit offset."""

    deduplicated: dict[tuple[ProflameFrame, str, tuple[int, ...]], PulseDecodeCandidate] = {}
    for candidate in candidates:
        key = (candidate.frame, candidate.frame_format, candidate.extension_words)
        existing = deduplicated.get(key)
        if existing is None or candidate.bit_offset < existing.bit_offset:
            deduplicated[key] = candidate
    return sorted(deduplicated.values(), key=lambda candidate: candidate.bit_offset)
