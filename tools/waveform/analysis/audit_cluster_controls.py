"""Independent raw-threshold, first/later-frame and RX-clock controls."""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from tools.waveform.analysis.audit_clusters import fft_metrics
from tools.waveform.environment import capture_root, load_environment
from tools.waveform.waveform_cluster import best_lattice, crossing_edges, cu8_window, lattice, smooth_power, summary
from tools.waveform.waveform_diagnostics import read_windows
from tools.waveform.waveform_match import file_digest
from tools.waveform.waveform_pretrigger_review import structural_frames


def run(audit, destination):
    load_environment()
    root = capture_root() / "runs"
    data = json.loads(audit.read_text())
    report = {"source": file_digest(Path(__file__)), "input_audit": file_digest(audit), "datasets": {}}
    for label, source in data["datasets"].items():
        print(f"Raw/clock controls: {label}", flush=True)
        rows, highs, threshold_summary = [], defaultdict(Counter), defaultdict(list)
        for row in source["events"]:
            path = root / source["run"]
            events = json.loads((path / "analysis-buffered" / row["trial"] / "events.json").read_text())
            event = next(event for event in events if event["event_id"] == row["event_id"])
            runs = event["signed_samples"]
            if (len(runs) + 1) % 5:
                raise ValueError("five-copy partition is not supported by edge count")
            per_frame = (len(runs) + 1) // 5
            tick = row["lattice"]["period_us"]
            high_ticks = defaultdict(Counter)
            for index, width in enumerate(runs):
                if width <= 0:
                    continue
                unit = (width + 208) // 417
                if unit not in (1, 2, 3):
                    continue
                ticks = round((width - 4) / tick)
                group = "first_frame" if index < per_frame else "later_frames"
                high_ticks[f"{group}_high_{unit}"][ticks] += 1
                highs[f"{group}_high_{unit}"][ticks] += 1
            iq = path / "session" / row["trial"] / "signal.cu8"
            z = cu8_window(iq, row["start_sample"] - 5000, row["end_sample"] + 5000)
            off, on = row["raw_iq"]["noise_power_p10"], row["raw_iq"]["on_power_p90"]
            raw_clock = []
            for width, fraction in ((1, 0.5), (5, 0.2), (5, 0.5), (5, 0.8), (15, 0.5)):
                power = smooth_power(z, width)
                times, levels = crossing_edges(power, off + fraction * (on - off))
                fit = lattice(times, levels, tick)
                raw_clock.append(
                    {
                        "smoothing": width,
                        "fraction": fraction,
                        "edge_count": len(times),
                        "coherence": fit["mean_coherence"],
                    }
                )
            carrier_segments = []
            for begin in (0, 180000, len(z) - 10000 - 30000):
                clip = z[5000 + begin : 5000 + begin + 30000]
                carrier_segments.append(fft_metrics(clip)["local_500hz_centroid_hz"])
            rows.append(
                {
                    "trial": row["trial"],
                    "event_id": row["event_id"],
                    "high_ticks": {key: dict(value) for key, value in high_ticks.items()},
                    "raw_clock": raw_clock,
                    "carrier_start_mid_end_hz": carrier_segments,
                    "carrier_end_minus_start_hz": carrier_segments[-1] - carrier_segments[0],
                }
            )
            for check in row["threshold_checks"]:
                stats = check["widths"].get("high_1")
                if stats:
                    threshold_summary[(check["smoothing_samples"], check["on_fraction"])].append(
                        stats["p95"] - stats["p05"]
                    )
        rx_clocks = []
        for path in sorted((root / source["run"] / "session").glob("trial-*/receiver.stdout")):
            for window in read_windows(path.read_text().splitlines(), require_complete_sessions=True):
                spans = structural_frames(window.edges)
                offsets = np.r_[0, np.cumsum(np.abs(window.edges))]
                indexes = sorted({i for frame in spans for i in range(frame["edge_start"], frame["edge_end"])})
                times = offsets[indexes]
                levels = np.array([window.edges[i] > 0 for i in indexes])
                fit = best_lattice([(times, levels)], np.linspace(52.08, 52.13, 1001))
                rx_clocks.append({"trial": path.parent.name, "capture_id": window.metadata["id"], **fit})
        report["datasets"][label] = {
            "raw_checks": rows,
            "high_tick_counts": {key: dict(value) for key, value in highs.items()},
            "threshold_short_high_p95_p05_span_us": [
                {"smoothing": key[0], "fraction": key[1], "valid_events": len(values), "span": summary(values)}
                for key, values in sorted(threshold_summary.items())
            ],
            "raw_half_power_coherence": summary([r["raw_clock"][0]["coherence"] for r in rows]),
            "carrier_end_minus_start_hz": summary([r["carrier_end_minus_start_hz"] for r in rows]),
            "rx_clocks": rx_clocks,
            "rx_clock_coherence": summary([r["coherence"] for r in rx_clocks]),
            "rx_clock_period_us": summary([r["period_us"] for r in rx_clocks]),
        }
    with destination.open("x") as target:
        json.dump(report, target, indent=2)
        target.write("\n")
    print(
        json.dumps(
            {
                key: {
                    name: value[name]
                    for name in (
                        "raw_half_power_coherence",
                        "rx_clock_coherence",
                        "rx_clock_period_us",
                        "carrier_end_minus_start_hz",
                    )
                }
                for key, value in report["datasets"].items()
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audit", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    run(args.audit, args.output)
