"""Preserve a private controller log and summarize local TX/retry completion.

Local completion is never interpreted as a fireplace acknowledgement.
"""

import argparse
import hashlib
import json
import re
import shutil
import statistics
from collections import Counter
from pathlib import Path


def analyze(text):
    counts = Counter()
    events = {key: {} for key in ("tx", "scheduled", "queued", "retry")}
    elapsed = {key: [] for key in ("tx", "retry")}
    patterns = {
        "tx": r"TX result status=(\S+) request_id=(\S+)",
        "retry": r"TX retransmission result status=(\S+) original_request_id=(\S+)",
        "scheduled": r"TX retransmission scheduled original_request_id=(\S+)",
        "queued": r"TX retransmission queued original_request_id=(\S+)",
    }
    warnings = []
    tuning = []
    for line in text.splitlines():
        timestamp = re.search(r"\[(\d+):(\d+):(\d+)\.(\d+)\]", line)
        seconds = (
            (int(timestamp[1]) * 3600 + int(timestamp[2]) * 60 + int(timestamp[3]) + int(timestamp[4]) / 1000)
            if timestamp
            else None
        )
        match = re.search(r"RF tuning: tx_hz=(\d+) rx_hz=(\d+) tx_offset_hz=(-?\d+)", line)
        if match:
            tuning.append(dict(zip(("tx_hz", "rx_hz", "offset_hz"), map(int, match.groups()), strict=True)))
        for key, pattern in patterns.items():
            match = re.search(pattern, line)
            if not match:
                continue
            request = match[2] if key in ("tx", "retry") else match[1]
            if request in events[key]:
                raise ValueError("duplicate request event; segment log before analysis")
            events[key][request] = seconds
            counts[key] += 1
            if key in elapsed:
                counts[key + "_" + match[1]] += 1
                duration = re.search(r"elapsed_ms=(\d+)", line)
                if duration:
                    elapsed[key].append(int(duration[1]))
        if "[W]" in line:
            warnings.append(line)
        if "[E]" in line:
            counts["error_lines"] += 1
        cancellation = re.search(r"TX retransmission cancelled reason=(\S+)", line)
        if cancellation:
            counts["cancelled_" + cancellation[1]] += 1

    def stats(values):
        return (
            {"count": len(values), "min": min(values), "median": statistics.median(values), "max": max(values)}
            if values
            else None
        )

    return {
        "counts": dict(counts),
        "configured_tuning": tuning,
        "warnings": warnings,
        "missing_retry_results": sorted(set(events["tx"]) - set(events["retry"])),
        "retry_result_after_tx_result_seconds": stats(
            [
                events["retry"][key] - when
                for key, when in events["tx"].items()
                if when is not None and events["retry"].get(key) is not None
            ]
        ),
        "elapsed_ms": {key: stats(values) for key, values in elapsed.items()},
        "local_request_ids": list(events["tx"]),
        "limits": [
            "No fireplace ACK; TX ok is local controller completion.",
            "No operator slow-response timestamps were supplied.",
            "A completed scheduled repeat does not imply the initial burst was missed.",
            "No HA service-entry timing is present to measure UI-to-TX latency.",
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    data = args.log.read_bytes()
    report = analyze(data.decode(errors="replace"))
    report["source_log_sha256"] = hashlib.sha256(data).hexdigest()
    report["analysis_source_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    args.output.mkdir(mode=0o700)
    shutil.copyfile(args.log, args.output / "controller.log")
    (args.output / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {key: value for key, value in report.items() if key not in {"local_request_ids", "warnings"}}, indent=2
        )
    )
