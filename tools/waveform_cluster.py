"""Offline pulse-clock and CU8 envelope checks; no RF or device access.

NumPy is an optional analysis dependency. Results describe recorded samples,
not independently calibrated RF timing or fireplace acceptance tolerances.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

MAX_WINDOW_SAMPLES = 2_000_000


def summary(values) -> dict | None:
    data = np.asarray(values, dtype=float)
    if data.size == 0:
        return None
    if data.ndim != 1 or not np.isfinite(data).all():
        raise ValueError("finite one-dimensional measurements required")
    return {
        "count": int(data.size),
        "min": float(data.min()),
        "max": float(data.max()),
        "median": float(np.median(data)),
        "mean": float(data.mean()),
        "std": float(data.std()),
        "p05": float(np.quantile(data, 0.05)),
        "p95": float(np.quantile(data, 0.95)),
    }


def cu8_window(path: Path, start: int, end: int) -> np.ndarray:
    if type(start) is not int or type(end) is not int or not 0 <= start < end:
        raise ValueError("increasing sample bounds required")
    if end - start > MAX_WINDOW_SAMPLES:
        raise ValueError("IQ window exceeds analysis budget")
    size = path.stat().st_size
    if size % 2 or end * 2 > size:
        raise ValueError("truncated or out-of-range CU8")
    with path.open("rb") as source:
        source.seek(start * 2)
        data = source.read((end - start) * 2)
    if len(data) != (end - start) * 2:
        raise ValueError("short CU8 read")
    iq = np.frombuffer(data, dtype=np.uint8).reshape(-1, 2).astype(float) - 127.5
    return iq[:, 0] + 1j * iq[:, 1]


def smooth_power(iq, width: int) -> np.ndarray:
    if type(width) is not int or not 1 <= width <= 101 or width % 2 == 0:
        raise ValueError("odd smoothing width from 1 to 101 required")
    power = np.abs(iq) ** 2
    if power.ndim != 1 or power.size < width or not np.isfinite(power).all():
        raise ValueError("finite IQ vector longer than smoothing width required")
    if width == 1:
        return power
    # Keep length and avoid zero-padding creating false edges near the borders.
    pad = width // 2
    return np.convolve(np.pad(power, (pad, pad), mode="edge"), np.ones(width) / width, mode="valid")


def crossing_edges(power, threshold: float) -> tuple[np.ndarray, np.ndarray]:
    data = np.asarray(power, dtype=float)
    if data.ndim != 1 or data.size < 2 or not np.isfinite(data).all() or not math.isfinite(threshold):
        raise ValueError("finite envelope and threshold required")
    high = data >= threshold
    indexes = np.flatnonzero(high[1:] != high[:-1]) + 1
    return indexes, high[indexes]


def signed_runs(times, rising) -> tuple[int, ...]:
    times = np.asarray(times)
    rising = np.asarray(rising, dtype=bool)
    if times.ndim != 1 or rising.ndim != 1 or len(times) != len(rising):
        raise ValueError("matching edge arrays required")
    if not np.isfinite(times).all() or (np.diff(times) <= 0).any() or (times != np.floor(times)).any():
        raise ValueError("increasing finite edge times required")
    if (rising[1:] == rising[:-1]).any():
        raise ValueError("alternating edge polarity required")
    # First and last exterior levels are censored; never invent their durations.
    return tuple(int(width) * (1 if level else -1) for width, level in zip(np.diff(times), rising[:-1], strict=True))


def lattice(times_us, rising, period_us: float) -> dict:
    times = np.asarray(times_us, dtype=float)
    levels = np.asarray(rising, dtype=bool)
    if times.ndim != 1 or levels.ndim != 1 or len(times) != len(levels) or not np.isfinite(times).all():
        raise ValueError("matching finite edge arrays required")
    if not math.isfinite(period_us) or period_us <= 0:
        raise ValueError("positive finite clock period required")
    result = {}
    for label, mask in (("rising", levels), ("falling", ~levels)):
        sample = times[mask]
        if len(sample) < 4:
            raise ValueError("at least four edges per polarity required")
        vector = np.exp(2j * np.pi * sample / period_us).mean()
        phase = float(np.angle(vector) * period_us / (2 * np.pi))
        residual = (sample - phase + period_us / 2) % period_us - period_us / 2
        result[label] = {
            "edges": len(sample),
            "coherence": float(abs(vector)),
            "phase_us": phase,
            "residual_rms_us": float(np.sqrt(np.mean(residual**2))),
            "residual_p95_abs_us": float(np.quantile(np.abs(residual), 0.95)),
        }
    result["mean_coherence"] = (result["rising"]["coherence"] + result["falling"]["coherence"]) / 2
    return result


def best_lattice(edge_sets, periods) -> dict:
    periods = np.asarray(periods, dtype=float)
    if periods.ndim != 1 or len(periods) == 0 or len(periods) > 20001 or not np.isfinite(periods).all():
        raise ValueError("bounded finite period search required")
    if (periods <= 0).any() or not edge_sets:
        raise ValueError("positive periods and observed edges required")
    scores = np.zeros(len(periods))
    count = 0
    for times, levels in edge_sets:
        times = np.asarray(times, dtype=float)
        levels = np.asarray(levels, dtype=bool)
        # Validate once and keep each burst/polarity's independent phase offset.
        lattice(times, levels, float(periods[0]))
        for mask in (levels, ~levels):
            sample = times[mask]
            for offset in range(0, len(periods), 128):
                grid = periods[offset : offset + 128]
                scores[offset : offset + 128] += np.abs(np.exp(2j * np.pi * sample[:, None] / grid).mean(axis=0))
            count += 1
    best = int(np.argmax(scores))
    return {
        "period_us": float(periods[best]),
        "coherence": float(scores[best] / count),
        "search_min_us": float(periods.min()),
        "search_max_us": float(periods.max()),
        "search_boundary": best in (0, len(periods) - 1),
        "burst_count": len(edge_sets),
    }


def carrier_metrics(iq, power, threshold: float, sample_rate: int) -> dict:
    """Lag-one estimate for clean narrowband IQ; clipping/harmonics can bias it."""
    z = np.asarray(iq)
    p = np.asarray(power)
    if z.ndim != 1 or p.shape != z.shape or len(z) < 2 or not np.isfinite(z).all() or not np.isfinite(p).all():
        raise ValueError("matching finite IQ/envelope arrays required")
    if type(sample_rate) is not int or sample_rate <= 0 or not math.isfinite(threshold):
        raise ValueError("positive sample rate and finite threshold required")
    selected = (p[:-1] > threshold) & (p[1:] > threshold)
    if selected.sum() < 16:
        raise ValueError("too few on-carrier sample pairs")
    cross = z[1:][selected] * np.conj(z[:-1][selected])
    total = cross.sum()
    return {
        "selected_pairs": int(selected.sum()),
        "frequency_offset_hz": float(np.angle(total) * sample_rate / (2 * np.pi)),
        "lag_one_coherence": float(abs(total) / max(float(np.abs(cross).sum()), 1e-30)),
    }
