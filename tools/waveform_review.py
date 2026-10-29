"""Supplement an offline replay with conservative, longer-burst diagnostic fits.

Only already-unique payload/event associations are eligible. Repeated events are
not assigned by ordering or callback timestamps. This is not physical calibration.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from difflib import SequenceMatcher
from itertools import accumulate
from pathlib import Path

from tools.waveform_collect import write_json
from tools.waveform_diagnostics import read_windows
from tools.waveform_match import _occurrences, _tokens, file_digest, fit_local_alignment


def review_pair(window, event: dict, *, sample_rate: int) -> dict | None:
    left = _tokens(window.edges)
    right_us = [
        (1 if run > 0 else -1) * max(1, round(abs(run) * 1_000_000 / sample_rate)) for run in event["signed_samples"]
    ]
    right = _tokens(right_us)
    block = SequenceMatcher(None, left, right, autojunk=False).find_longest_match()
    if block.size < 96:
        return None
    signature = left[block.a : block.a + block.size]
    if len(_occurrences(left, signature)) != 1 or len(_occurrences(right, signature)) != 1:
        return None
    lt = list(accumulate(map(abs, window.edges), initial=0))
    rt = list(accumulate(map(abs, event["signed_samples"]), initial=event["start_sample"]))
    count = min(96, block.size + 1)
    indexes = sorted({round(i * block.size / (count - 1)) for i in range(count)})
    fit = fit_local_alignment([(lt[block.a + i], rt[block.b + i]) for i in indexes], sample_rate_hz=sample_rate)
    return {
        "matching_runs": block.size,
        "receiver_offset": block.a,
        "sdr_offset": block.b,
        "receiver_runs": len(left),
        "sdr_runs": len(right),
        "fit": fit,
    }


def review_session(session: Path, analysis: Path) -> dict:
    report = {
        "schema_version": 1,
        "precision_calibrated": False,
        "trials": [],
        "diagnostic_alignments": [],
        "method": "longer_unique_signatures_for_already_unique_payload_event_matches",
        "limitations": [
            "Not an independent RF calibration",
            "No repeated-event ownership inferred",
            "Width differences include both receive/demodulation paths",
            "Boundary estimates extrapolate local fits beyond their anchor spans",
        ],
        "review_code": file_digest(Path(__file__)),
    }
    widths = defaultdict(list)
    for folder in sorted(analysis.glob("trial-*")):
        if not folder.is_dir():
            continue
        baseline = json.loads((folder / "matches.json").read_text())
        events = {e["event_id"]: e for e in json.loads((folder / "events.json").read_text())}
        with (session / folder.name / "receiver.stdout").open() as source:
            windows = {w.metadata["id"]: w for w in read_windows(source, require_complete_sessions=True)}
        sample_rate = baseline["provenance"]["sample_rate_hz"]
        statuses = defaultdict(int)
        for match in baseline["matches"]:
            statuses[match["status"]] += 1
            if match["status"] != "matched" or len(match["candidates"]) != 1:
                continue
            candidate = match["candidates"][0]
            if candidate["evidence"] != "decoded_payload_and_edges":
                continue
            window = windows[match["capture_id"]]
            event = events[candidate["event_id"]]
            row = review_pair(window, event, sample_rate=sample_rate)
            if row is None:
                continue
            row.update(trial=folder.name, capture_id=match["capture_id"], event_id=event["event_id"])
            fit = row["fit"]
            if fit["status"] == "fitted":
                row["estimated_missing_start_us"] = (
                    (fit["sample_offset"] - event["start_sample"]) * 1_000_000 / sample_rate
                )
                row["estimated_missing_end_us"] = (
                    (event["end_sample"] - fit["sample_offset"] - fit["samples_per_us"] * sum(map(abs, window.edges)))
                    * 1_000_000
                    / sample_rate
                )
                for i in range(row["matching_runs"]):
                    receiver_run = window.edges[row["receiver_offset"] + i]
                    sdr_run = event["signed_samples"][row["sdr_offset"] + i] * 1_000_000 / sample_rate
                    unit = round(abs(sdr_run) / 417)
                    if unit in (1, 2, 3):
                        widths[("high" if sdr_run > 0 else "low") + f"_{unit}"].append(
                            (abs(receiver_run), abs(sdr_run), abs(receiver_run) - abs(sdr_run))
                        )
            report["diagnostic_alignments"].append(row)
        report["trials"].append(
            {
                "trial": folder.name,
                "receiver_windows": len(windows),
                "sdr_events": len(events),
                "baseline_statuses": dict(statuses),
                "provenance": baseline["provenance"],
            }
        )
    report["widths"] = {}
    for key, values in widths.items():
        deltas = sorted(value[2] for value in values)
        report["widths"][key] = {
            "runs": len(values),
            "rmt_median_us": statistics.median(v[0] for v in values),
            "sdr_median_us": statistics.median(v[1] for v in values),
            "delta_median_us": statistics.median(deltas),
            "delta_p05_us": deltas[round((len(deltas) - 1) * 0.05)],
            "delta_p95_us": deltas[round((len(deltas) - 1) * 0.95)],
        }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("analysis", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    report = review_session(args.session, args.analysis)
    write_json(args.output, report)
    print(f"Saved {len(report['diagnostic_alignments'])} diagnostic fits; physical calibration remains unestablished.")


if __name__ == "__main__":
    main()
