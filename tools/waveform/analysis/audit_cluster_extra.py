"""Preserve source/register evidence, rare high-pulse outliers and replay provenance."""

import argparse
import json
import shutil
import subprocess
from collections import Counter
from pathlib import Path

import numpy as np

from tools.waveform.analysis.settings import datasets
from tools.waveform.environment import capture_root
from tools.waveform.waveform_cluster import cu8_window, smooth_power
from tools.waveform.waveform_match import file_digest


def prefix_overlay(output):
    runs = datasets()
    root = capture_root() / "runs"
    colors = {"native": "#00856a", "baseline": "#b64a00", "candidate": "#1956bb"}
    svg = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1100" height="430">',
        '<rect width="1100" height="430" fill="white"/>',
        '<text x="60" y="25" font-size="17">First Light-1 burst prefix; normalized raw power, 5-us average</text>',
        '<text x="60" y="48" font-size="13">LilyGO traces are clipped: this is not a physical RF edge-shape calibration.</text>',
    ]
    for label, name in runs.items():
        run = root / name
        event = json.loads((run / "analysis-buffered/trial-001-light_6/events.json").read_text())[0]
        z = cu8_window(
            run / "session/trial-001-light_6/signal.cu8", event["start_sample"] - 1000, event["start_sample"] + 6000
        )
        power = smooth_power(z, 5)
        floor, top = np.quantile(power, [0.1, 0.9])
        normalized = np.clip((power - floor) / (top - floor), 0, 1.2)
        # Keep actual sample coordinates; each plotted point advances 3 us.
        points = " ".join(f"{60+i/5:.2f},{350-normalized[1000+i]*220:.2f}" for i in range(0, 5001, 3))
        svg.append(f'<polyline points="{points}" fill="none" stroke="{colors[label]}" stroke-width="1.3"/>')
    for value in range(0, 5001, 1000):
        x = 60 + value / 5
        svg.append(f'<text x="{x-8}" y="375">{value}</text>')
    for index, (label, color) in enumerate(colors.items()):
        svg.append(f'<text x="{60+index*170}" y="405" fill="{color}">{label}</text>')
    svg.append('<text x="780" y="405">Time after detector onset (us)</text></svg>')
    (output / "prefix-overlay.svg").write_text("\n".join(svg) + "\n")


def run(output, checkout):
    datasets()
    root = capture_root() / "runs"
    audit = json.loads((output / "audit.json").read_text())
    extra = {"source": file_digest(Path(__file__)), "high_tick_outliers": {}, "tagged_source": {}}
    for label in ("baseline", "candidate"):
        source = audit["datasets"][label]
        anomalies, counts = [], Counter()
        for row in source["events"]:
            events = json.loads((root / source["run"] / "analysis-buffered" / row["trial"] / "events.json").read_text())
            event = next(event for event in events if event["event_id"] == row["event_id"])
            tick = row["lattice"]["period_us"]
            for index, value in enumerate(event["signed_samples"]):
                if value <= 0:
                    continue
                kind = (value + 208) // 417
                if kind not in (1, 2, 3):
                    continue
                count = round((value - 4) / tick)
                counts[f"high_{kind}_ticks_{count}"] += 1
                if count not in {1: {7, 8}, 2: {15, 16}, 3: {23, 24}}[kind]:
                    anomalies.append(
                        {
                            "trial": row["trial"],
                            "event_id": row["event_id"],
                            "run_index": index,
                            "high_us": value,
                            "ticks": count,
                            "nearby_signed_runs_us": event["signed_samples"][max(0, index - 2) : index + 3],
                        }
                    )
        extra["high_tick_outliers"][label] = {
            "normal_family_model": "396/808/1216-us GPIO HIGHs sampled in 52.109-us ticks; about +4-us detector polarity bias",
            "counts": dict(counts),
            "high_pulses": sum(counts.values()),
            "outlier_count": len(anomalies),
            "outliers": anomalies,
            "causality": "Not proof of GPIO lateness or fireplace rejection",
        }
    files = ["radio_cc1101.cpp", "radio_cc1101_tx.cpp", "proflame2_tembed.h", "rf_band.cpp"]
    for tag in ("v0.6.0", "v0.7.0-beta3"):
        sources = {}
        for name in files:
            path = "esphome/components/proflame2_tembed/" + name
            process = subprocess.run(["git", "show", f"{tag}:{path}"], cwd=checkout, capture_output=True)
            if process.returncode:
                if tag == "v0.6.0" and name == "rf_band.cpp":
                    continue
                raise ValueError("tagged source read failed")
            destination = output / f"{tag}-{name}.txt"
            destination.write_bytes(process.stdout)
            sources[name] = file_digest(destination)
        sha = subprocess.check_output(["git", "rev-parse", tag + "^{commit}"], cwd=checkout, text=True).strip()
        extra["tagged_source"][tag] = {"commit": sha, "files": sources}
    prefix_overlay(output)
    snapshots = output / "replay-source"
    snapshots.mkdir()
    sources = [
        Path(__file__),
        Path(__file__).with_name("audit_clusters.py"),
        Path(__file__).with_name("audit_cluster_controls.py"),
        checkout / "tools/waveform/waveform_cluster.py",
        checkout / "tools/waveform/waveform_pretrigger_review.py",
        checkout / "tools/waveform/waveform_protocol.py",
        checkout / "tools/waveform/waveform_diagnostics.py",
    ]
    for source in sources:
        shutil.copyfile(source, snapshots / source.name)
    extra["replay_source_snapshots"] = [file_digest(path) for path in sorted(snapshots.iterdir())]
    (output / "source-and-outliers.json").write_text(json.dumps(extra, indent=2) + "\n")
    print(
        json.dumps(
            {
                key: {k: v for k, v in value.items() if k in ("high_pulses", "outlier_count")}
                for key, value in extra["high_tick_outliers"].items()
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("checkout", type=Path)
    args = parser.parse_args()
    run(args.output, args.checkout)
