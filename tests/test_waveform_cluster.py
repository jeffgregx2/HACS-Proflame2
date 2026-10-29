"""Synthetic controls for offline IQ/lattice measurements, not RF acceptance."""

import math

import numpy as np
import pytest

from tools.waveform_cluster import (
    best_lattice,
    carrier_metrics,
    crossing_edges,
    cu8_window,
    lattice,
    signed_runs,
    smooth_power,
    summary,
)


def edges(period=52.1057):
    ticks = np.cumsum(np.tile([8, 9, 16, 8, 24, 16], 100))
    rising = np.arange(len(ticks)) % 2 == 0
    return ticks * period + np.where(rising, 2.0, -2.0), rising


def test_coherent_radio_clock_with_polarity_delay():
    times, levels = edges()
    result = lattice(times, levels, 52.1057)
    assert result["mean_coherence"] > 0.999999
    assert result["rising"]["residual_rms_us"] < 1e-8
    assert result["falling"]["residual_rms_us"] < 1e-8
    grid = np.linspace(52.10, 52.11, 101)
    result = best_lattice([(times, levels)], grid)
    assert result["period_us"] == pytest.approx(52.1057)
    assert result["coherence"] > 0.999999
    assert not result["search_boundary"]


def test_unrelated_edges_do_not_falsely_establish_clock():
    rng = np.random.default_rng(23)
    times = np.cumsum(rng.uniform(300, 900, 1000))
    levels = np.arange(len(times)) % 2 == 0
    assert lattice(times, levels, 52.1057)["mean_coherence"] < 0.12


def test_fractional_threshold_and_censored_edges():
    power = np.array([0, 0, 0, 10, 10, 10, 0, 0, 0, 10, 10, 0], dtype=float)
    times, levels = crossing_edges(power, 5)
    assert times.tolist() == [3, 6, 9, 11]
    assert levels.tolist() == [True, False, True, False]
    assert signed_runs(times, levels) == (3, -3, 2)
    assert len(signed_runs([], [])) == 0


def test_power_smoothing_preserves_length_and_constant_edges():
    iq = np.ones(10, dtype=complex) * (3 + 4j)
    assert np.allclose(smooth_power(iq, 5), 25)
    assert len(smooth_power(iq, 5)) == len(iq)
    assert np.allclose(smooth_power(iq, 1), 25)


def test_cu8_exact_bounds_and_center(tmp_path):
    path = tmp_path / "signal.cu8"
    path.write_bytes(bytes([127, 128, 130, 131, 120, 121]))
    assert cu8_window(path, 1, 3).tolist() == [2.5 + 3.5j, -7.5 - 6.5j]
    for start, end in [(-1, 2), (True, 2), (1, 1), (0, 4), (0, 2_000_001)]:
        with pytest.raises(ValueError):
            cu8_window(path, start, end)
    path.write_bytes(bytes(3))
    with pytest.raises(ValueError):
        cu8_window(path, 0, 1)


def test_carrier_signed_frequency_and_mask():
    rate = 1_000_000
    for frequency in (-27000, 32000):
        z = 10 * np.exp(2j * np.pi * frequency * np.arange(10000) / rate)
        result = carrier_metrics(z, np.abs(z) ** 2, 50, rate)
        assert result["frequency_offset_hz"] == pytest.approx(frequency)
        assert result["lag_one_coherence"] == pytest.approx(1)


def test_summaries_and_bad_inputs():
    assert summary([]) is None
    assert summary([1, 2, 3])["median"] == 2
    for values in ([math.nan], [[1, 2]]):
        with pytest.raises(ValueError):
            summary(values)
    for width in (0, 2, 103, True):
        with pytest.raises(ValueError):
            smooth_power(np.ones(200), width)
    for times, levels in (([1, 1], [True, False]), ([1, 2], [True, True]), ([1], [])):
        with pytest.raises(ValueError):
            signed_runs(times, levels)
    with pytest.raises(ValueError):
        lattice([1, 2], [True, False], 52)
    with pytest.raises(ValueError):
        best_lattice([], [52])
    with pytest.raises(ValueError):
        carrier_metrics(np.zeros(100), np.zeros(100), 1, 1_000_000)


def test_rejects_nonfinite_mismatched_and_unbounded_numeric_inputs():
    times, levels = edges()
    cases = [
        (smooth_power, (np.array([np.nan]), 1)),
        (crossing_edges, (np.array([0, np.nan]), 1)),
        (signed_runs, ([[1, 2]], [True, False])),
        (signed_runs, ([1.1, 2.1], [True, False])),
        (lattice, ([np.nan] * 8, [True, False] * 4, 52)),
        (lattice, (times, levels, 0)),
        (best_lattice, ([(times, levels)], [])),
        (best_lattice, ([(times, levels)], [-1])),
        (carrier_metrics, (np.ones(20), np.ones(19), 1, 1_000_000)),
        (carrier_metrics, (np.ones(20), np.ones(20), 1, 0)),
    ]
    for function, arguments in cases:
        with pytest.raises(ValueError):
            function(*arguments)
