"""Replay the three private pilots without device access or source modification.

Run with PYTHONPATH set to the diagnostic worktree and NumPy installed.
Output must be a new directory under the persistent capture analysis root.
"""

import argparse
import csv
import hashlib
import json
import time
from collections import Counter, defaultdict
from itertools import accumulate
from pathlib import Path

import numpy as np

from tools.waveform.analysis.settings import datasets
from tools.waveform.environment import capture_root
from tools.waveform.waveform_cluster import (
    best_lattice,
    carrier_metrics,
    crossing_edges,
    cu8_window,
    lattice,
    signed_runs,
    smooth_power,
    summary,
)
from tools.waveform.waveform_match import file_digest
from tools.waveform.waveform_pretrigger_review import structural_frames

RATE = 1_000_000
RATE_REGISTER = 2398.967742919922
PREDICTED_TICK = 1e6 / (8 * RATE_REGISTER)


def pulse_groups(runs):
    groups = defaultdict(list)
    positions = []
    for frame in structural_frames(runs):
        words = "".join(f"{word:02x}" for word in frame["wire_words"])
        start, end = frame["edge_start"], frame["edge_end"]
        shape = tuple((1 if value > 0 else -1) * ((abs(value) + 208) // 417) for value in runs[start + 2 : end - 2])
        signature = hashlib.sha256(str(shape).encode()).hexdigest()[:16]
        for index, value in enumerate(runs[start + 2 : end - 2], 2):
            unit = (abs(value) + 208) // 417
            if unit not in (1, 2, 3):
                continue
            kind = ("high" if value > 0 else "low") + f"_{unit}"
            groups[kind].append(abs(value))
            positions.append(((words, signature, index, kind), abs(value)))
    return groups, positions


def raw_widths(power, threshold):
    edges, levels = crossing_edges(power, threshold)
    # Analyze only measured transitions. Do not discard short glitches or stitch
    # runs before structural decoding; that would manufacture valid frames.
    runs = signed_runs(edges, levels)
    groups, _ = pulse_groups(runs)
    return {kind: summary(values) for kind, values in groups.items()}


def fft_metrics(z):
    n = 1 << (len(z) - 1).bit_length()
    power = np.abs(np.fft.fft(z * np.hanning(len(z)), n)) ** 2
    frequency = np.fft.fftfreq(n, 1 / RATE)
    eligible = (np.abs(frequency) > 1000) & (np.abs(frequency) < 200000)
    peak = float(frequency[np.argmax(np.where(eligible, power, -1))])
    selected = np.abs(frequency - peak) < 500
    centroid = float(np.sum(power[selected] * frequency[selected]) / np.sum(power[selected]))
    return {
        "dominant_peak_hz": peak,
        "local_500hz_centroid_hz": centroid,
        "resolution_hz": RATE / n,
        "note": "Relative to recording center, not independently calibrated absolute frequency",
    }


def plot_histograms(path, histograms):
    colors = {"native": "#00856a", "baseline": "#b64a00", "candidate": "#1956bb"}
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="430" viewBox="0 0 1000 430">',
        '<rect width="1000" height="430" fill="white"/>',
        '<text x="60" y="28" font-size="18">SDR short HIGH pulse distribution (each source normalized)</text>',
    ]
    peak = max(count / sum(bucket.values()) for bucket in histograms.values() for count in bucket.values())
    for value in range(340, 481, 20):
        x = 60 + (value - 340) * 6
        parts.append(f'<path d="M{x} 50V360" stroke="#ddd"/><text x="{x-12}" y="380">{value}</text>')
    for label, histogram in histograms.items():
        total = sum(histogram.values())
        points = " ".join(
            f"{60+(value-340)*6:.1f},{360-300*histogram.get(value,0)/total/peak:.1f}" for value in range(340, 481)
        )
        parts.append(f'<polyline points="{points}" fill="none" stroke="{colors[label]}" stroke-width="2"/>')
    for index, (label, color) in enumerate(colors.items()):
        parts.append(f'<text x="{70+index*170}" y="408" fill="{color}">{label}</text>')
    parts.append('<text x="760" y="408">Width (microseconds)</text></svg>')
    path.write_text("\n".join(parts) + "\n")


def run(output):
    runs = datasets()
    root = capture_root() / "runs"
    output.mkdir()
    report = {
        "source": file_digest(Path(__file__)),
        "numpy_version": np.__version__,
        "predicted_sample_tick_us": PREDICTED_TICK,
        "datasets": {},
        "limitations": [
            "SDR frequency/time are uncalibrated",
            "No fireplace outcome labels",
            "LilyGO clipping limits RF amplitude and edge-shape inferences",
            "Same source setup is operator reported; repeated bursts are not independent commands",
        ],
    }
    all_positions, histograms = {}, {}
    for label, name in runs.items():
        run_path = root / name
        manifest = json.loads((run_path / "session/session.json").read_text())
        verified = []
        for artifact in manifest["artifacts"]:
            digest = file_digest(run_path / "session" / artifact["filename"])
            if digest["bytes"] != artifact["bytes"] or digest["sha256"] != artifact["sha256"]:
                raise ValueError("original artifact differs from manifest")
            verified.append(digest)
        rows, positions, histogram = [], defaultdict(list), Counter()
        all_edges = []
        for path in sorted((run_path / "analysis-buffered").glob("trial-*/events.json")):
            print(f"Auditing {label} {path.parent.name}", flush=True)
            for event in json.loads(path.read_text()):
                if event["capacity_reached"] or event["sample_rate_hz"] != RATE:
                    raise ValueError("saturated detector or unexpected sample rate")
                runs = event["signed_samples"]
                times = np.asarray(list(accumulate(map(abs, runs), initial=0)), dtype=float)
                levels = np.arange(len(times)) % 2 == 0
                all_edges.append((times, levels))
                tick = best_lattice([(times, levels)], np.linspace(52.08, 52.13, 1001))
                coarse_tick = tick["period_us"]
                tick = best_lattice([(times, levels)], np.linspace(coarse_tick - 0.00006, coarse_tick + 0.00006, 121))
                fit = lattice(times, levels, tick["period_us"])
                groups, pulse_positions = pulse_groups(runs)
                histogram.update(groups.get("high_1", []))
                for key, value in pulse_positions:
                    positions[key].append(value)
                source = run_path / "session" / path.parent.name / "signal.cu8"
                margin = 5000
                start, end = event["start_sample"], event["end_sample"]
                if start < margin or end + margin > source.stat().st_size // 2:
                    raise ValueError("insufficient raw IQ context")
                z = cu8_window(source, start - margin, end + margin)
                p = smooth_power(z, 5)
                off, on = np.quantile(p, [0.1, 0.9])
                threshold = off + 0.5 * (on - off)
                high = p > threshold
                rail = (np.abs(z.real) == 127.5) | (np.abs(z.imag) == 127.5)
                central = z[margin:-margin]
                fft = fft_metrics(central)
                # Independent ratio check: both frequency and baud use XOSC.
                freq_word = (314973000 << 16) // 26000000
                nominal_carrier = freq_word * 26000000 / 65536
                predicted_carrier = nominal_carrier * PREDICTED_TICK / tick["period_us"] - 315000000
                row = {
                    "trial": path.parent.name,
                    "event_id": event["event_id"],
                    "start_sample": start,
                    "end_sample": end,
                    "structural_words": sorted({tuple(f["wire_words"]) for f in structural_frames(runs)}),
                    "lattice": tick,
                    "fitted_lattice_metrics": fit,
                    "predicted_tick_coherence": lattice(times, levels, PREDICTED_TICK),
                    "sdr_widths_us": {key: summary(values) for key, values in groups.items()},
                    "raw_iq": {
                        "noise_power_p10": float(off),
                        "on_power_p90": float(on),
                        "rail_fraction_all": float(rail.mean()),
                        "rail_fraction_on": float(rail[high].mean()),
                        "fft": fft,
                        "lag_one": carrier_metrics(z, p, off + 0.7 * (on - off), RATE),
                        "predicted_carrier_from_tick_hz": predicted_carrier if label != "native" else None,
                        "carrier_minus_prediction_hz": (
                            fft["local_500hz_centroid_hz"] - predicted_carrier if label != "native" else None
                        ),
                    },
                    "threshold_checks": [],
                }
                for width in (1, 5, 15):
                    power = smooth_power(z, width)
                    for fraction in (0.2, 0.35, 0.5, 0.65, 0.8):
                        measurements = raw_widths(power, float(off + fraction * (on - off)))
                        row["threshold_checks"].append(
                            {"smoothing_samples": width, "on_fraction": fraction, "widths": measurements}
                        )
                # Interior amplitude by 20-ms block; relative only, heavily
                # clipped datasets cannot provide physical RF power stability.
                blocks = []
                for offset in range(margin, len(p) - margin - 20000, 20000):
                    sample = p[offset : offset + 20000]
                    active = sample[sample > threshold]
                    if len(active):
                        blocks.append(float(np.median(active)))
                row["raw_iq"]["on_power_block_medians"] = summary(blocks)
                rows.append(row)
        histograms[label] = histogram
        all_positions[label] = positions
        metrics = {
            "tick_us": summary([r["lattice"]["period_us"] for r in rows]),
            "tick_coherence": summary([r["lattice"]["coherence"] for r in rows]),
            "lattice_rms_us": summary(
                [r["fitted_lattice_metrics"][pol]["residual_rms_us"] for r in rows for pol in ("rising", "falling")]
            ),
            "rail_fraction_on": summary([r["raw_iq"]["rail_fraction_on"] for r in rows]),
            "carrier_relative_center_hz": summary([r["raw_iq"]["fft"]["local_500hz_centroid_hz"] for r in rows]),
        }
        if label != "native":
            metrics["carrier_minus_clock_prediction_hz"] = summary(
                [r["raw_iq"]["carrier_minus_prediction_hz"] for r in rows]
            )
        report["datasets"][label] = {
            "run": name,
            "verified_original_artifacts": verified,
            "metrics": metrics,
            "events": rows,
        }
        # Periodic immutable checkpoint permits interruption without losing all
        # completed source analysis. It is not a completed audit report.
        (output / f"{label}.json").write_text(json.dumps(report["datasets"][label], indent=2) + "\n")
    common = set.intersection(*(set(values) for values in all_positions.values()))
    comparisons = []
    for key in sorted(common):
        row = {"words": key[0], "shape": key[1], "position": key[2], "kind": key[3]}
        for label, positions in all_positions.items():
            stats = summary(positions[key])
            for metric in ("count", "mean", "median", "p05", "p95"):
                row[f"{label}_{metric}"] = stats[metric]
        row["baseline_minus_native_mean_us"] = row["baseline_mean"] - row["native_mean"]
        row["candidate_minus_baseline_mean_us"] = row["candidate_mean"] - row["baseline_mean"]
        comparisons.append(row)
    if not comparisons:
        raise ValueError("no matching pulse positions across the selected datasets")
    with (output / "matching-positions.csv").open("x", newline="") as destination:
        writer = csv.DictWriter(destination, fieldnames=list(comparisons[0]))
        writer.writeheader()
        writer.writerows(comparisons)
    report["matching_positions"] = {
        "count": len(comparisons),
        "baseline_minus_native_mean_us": summary([r["baseline_minus_native_mean_us"] for r in comparisons]),
        "candidate_minus_baseline_mean_us": summary([r["candidate_minus_baseline_mean_us"] for r in comparisons]),
    }
    plot_histograms(output / "short-high-histogram.svg", histograms)
    (output / "audit.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v["metrics"] for k, v in report["datasets"].items()}, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    started = time.monotonic()
    run(args.output)
    print(f"Completed in {time.monotonic()-started:.1f} seconds.", flush=True)
