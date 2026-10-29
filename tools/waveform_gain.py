"""Dependency-free, bounded-memory checks for manually gained CU8 captures.

Clipping statistics sample every 32nd IQ pair. They are a screening check, not
proof that a valid RF burst was received or that its waveform is calibrated.
"""

from __future__ import annotations

import math
import re
from pathlib import Path


def manual_gain(value: float) -> float:
    value = float(value)
    # rtl_433 converts to tenths of a dB; a result of zero selects AGC.
    if not math.isfinite(value) or abs(value) < 0.1:
        raise ValueError("fixed rtl_433 gain must be finite and nonzero; gain 0 selects automatic gain")
    return value


def inspect_capture(iq: Path, diagnostics: Path, requested_gain: float, sample_rate: int) -> dict:
    size = iq.stat().st_size
    if size == 0 or size % 2:
        raise ValueError("CU8 capture must contain complete, nonempty IQ pairs")
    if sample_rate <= 0:
        raise ValueError("sample rate must be positive")
    requested_gain = manual_gain(requested_gain)
    # Recorder logs are already size-bounded by the collector.
    text = diagnostics.read_text(errors="replace")
    actual = re.findall(r"Tuner gain set to\s+(-?\d+(?:\.\d+)?)\s+dB", text)
    reported = float(actual[-1]) if actual else None
    stride = 32
    sampled = clipped = peak_excursion = peak_block_clipped = 0
    # Read 100 ms blocks, aligned to the sampling stride for reproducibility.
    pairs_per_block = max(stride, sample_rate // 10 // stride * stride)
    with iq.open("rb") as source:
        while block := source.read(2 * pairs_per_block):
            block_clipped = 0
            block_sampled = 0
            for index in range(0, len(block), stride * 2):
                i, q = block[index], block[index + 1]
                rail = i in (0, 255) or q in (0, 255)
                block_clipped += rail
                block_sampled += 1
                peak_excursion = max(peak_excursion, abs(2 * i - 255), abs(2 * q - 255))
            sampled += block_sampled
            clipped += block_clipped
            peak_block_clipped = max(peak_block_clipped, block_clipped / block_sampled)
    return {
        "schema_version": 1,
        "requested_gain_db": requested_gain,
        "reported_gain_db": reported,
        "manual_gain_confirmed": reported is not None
        and not re.search(r"automatic gain|gain set to auto|failed to .*gain", text, re.IGNORECASE),
        "duration_s": size / 2 / sample_rate,
        "sample_stride_iq_pairs": stride,
        "sampled_iq_pairs": sampled,
        "clipped_iq_pairs": clipped,
        "clipped_fraction": clipped / sampled,
        "peak_100ms_clipped_fraction": peak_block_clipped,
        "peak_component_excursion": peak_excursion / 2,
        "screening": "clipping_detected" if clipped else "no_clipping_observed",
        "limitation": "Sampled screening only; no-clipping does not establish RF reception or decode success.",
    }
