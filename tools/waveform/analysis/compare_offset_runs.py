"""Compare retained per-state low-gain metrics, without opening devices."""

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path


def grouped(document):
    states = defaultdict(list)
    for row in document["events"]:
        if row["structural_frame_count"] != 5 or len(row["wire_words"]) != 1:
            raise ValueError("expected one five-copy command per event")
        states[(row["trial"], tuple(row["wire_words"][0]))].append(row)
    return states


def mean(stats):
    return sum(row["count"] * row["mean"] for row in stats) / sum(row["count"] for row in stats)


def compare(baseline, offset):
    a, b = grouped(baseline), grouped(offset)
    if set(a) != set(b):
        raise ValueError("captured command states differ")
    families = sorted({key for row in baseline["events"] for key in row["raw_half_power_widths"]})
    widths = {}
    for family in families:
        changes = []
        for key in a:
            old = [row["raw_half_power_widths"][family] for row in a[key] if family in row["raw_half_power_widths"]]
            new = [row["raw_half_power_widths"][family] for row in b[key] if family in row["raw_half_power_widths"]]
            if old and new:
                changes.append(mean(new) - mean(old))
        widths[family] = {
            "compared_states": len(changes),
            "min_state_mean_change_us": min(changes) if changes else None,
            "max_state_mean_change_us": max(changes) if changes else None,
        }
    return {
        "source_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "baseline_run": baseline["source_run"],
        "offset_run": offset["source_run"],
        "measured_carrier_shift_hz": offset["metrics"]["carrier_relative_center_hz"]["median"]
        - baseline["metrics"]["carrier_relative_center_hz"]["median"],
        "offset_minus_native_reference_hz": (
            offset["metrics"]["carrier_relative_center_hz"]["median"] - offset["prior_carriers"]["native"]["median"]
            if "native" in offset.get("prior_carriers", {})
            else None
        ),
        "state_count": len(a),
        "all_requested_state_words_match": True,
        "width_comparison": widths,
        "burst_counts": {"baseline": len(baseline["events"]), "offset": len(offset["events"])},
        "single_burst_states": [
            {"trial": key[0], "wire_words": key[1], "baseline_bursts": len(a[key]), "offset_bursts": len(b[key])}
            for key in a
            if len(b[key]) != len(a[key])
        ],
        "limitations": [
            "Received-envelope widths are not independently calibrated GPIO/RF measurements.",
            "A missing delayed burst does not establish cancellation versus capture/reception loss.",
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("offset", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = compare(json.loads(args.baseline.read_text()), json.loads(args.offset.read_text()))
    with args.output.open("x") as destination:
        json.dump(result, destination, indent=2)
        destination.write("\n")
    print(json.dumps(result, indent=2))
