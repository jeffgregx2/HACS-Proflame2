"""Compare separate pre-trigger segments with offline native legacy RF events.

The operational decoder's repeat-gap acceptance is not used for structural
debugging. No RF edges are synthesized between acquisitions; no physical clock
calibration or transmitter identity is inferred. Original artifacts are read-only.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

from tools.waveform.waveform_collect import write_json
from tools.waveform.waveform_diagnostics import read_windows
from tools.waveform.waveform_match import MAX_EVENTS, _occurrences, _tokens, file_digest
from tools.waveform.waveform_protocol import decode_structural_legacy_words
from tools.waveform.waveform_review import review_pair


def structural_frames(edges: tuple[int, ...] | list[int]) -> list[dict]:
    """Keep every seven-word structural occurrence, including short repeat gaps.

    These may be legacy words or an extended frame's prefix. Do not feed these
    debug-only results into learning/control as accepted semantic frames.
    """
    frames = []
    bits = ""
    indexes = []

    def finish():
        offset = 0
        while offset <= len(bits) - 181:
            words = decode_structural_legacy_words(bits, offset)
            if words is None:
                offset += 1
                continue
            end = min(offset + 182, len(bits))
            gap = 0
            for bit in bits[end:]:
                if bit != "0":
                    break
                gap += 1
            frames.append(
                {
                    "wire_words": list(words),
                    "edge_start": indexes[offset],
                    "edge_end": indexes[end - 1] + 1,
                    "gap_bits": gap,
                }
            )
            offset = end

    for index, run in enumerate(edges):
        units = (abs(run) + 208) // 417
        if not 1 <= units <= (3 if run > 0 else 80):
            finish()
            bits, indexes = "", []
        else:
            bits += ("1" if run > 0 else "0") * units
            indexes.extend([index] * units)
    finish()
    return frames


def words(frames: list[dict]) -> set[tuple]:
    return {tuple(frame["wire_words"]) for frame in frames}


def review(session: Path, analysis: Path) -> dict:
    report = {
        "schema_version": 1,
        "precision_calibrated": False,
        "history_is_continuous": False,
        "method": "shared_seven_word_structure_plus_unique_long_burst_signature",
        "limitations": [
            "Structural words alone are not semantic acceptance or legacy integrity validation",
            "Seven words can also be the prefix of an extended frame",
            "Repeat-gap policy intentionally differs from the operational decoder",
            "No samples synthesized across RMT acquisition gaps",
            "A 75 ms sum of retained durations is not 75 ms of contiguous pre-trigger time",
            "Reported boundary estimates extrapolate local fits and are not calibrated edge timestamps",
            "SDR threshold and CC1101 demodulation both affect measured widths",
        ],
        "code": file_digest(Path(__file__)),
        "protocol_adapter": file_digest(Path(__file__).with_name("waveform_protocol.py")),
        "trials": [],
    }
    for folder in sorted(analysis.glob("trial-*")):
        if not folder.is_dir():
            continue
        print(f"Reviewing raw structures/history: {folder.name}", flush=True)
        baseline = json.loads((folder / "matches.json").read_text())
        rate = baseline["provenance"]["sample_rate_hz"]
        events = json.loads((folder / "events.json").read_text())
        if len(events) > MAX_EVENTS:
            raise ValueError("event review budget exceeded")
        for event in events:
            runs = tuple(
                (1 if run > 0 else -1) * max(1, round(abs(run) * 1000000 / rate)) for run in event["signed_samples"]
            )
            event["structural_frames"] = structural_frames(runs)
        with (session / folder.name / "receiver.stdout").open() as source:
            windows = read_windows(source, require_complete_sessions=True)
        if len(windows) * len(events) > 16384:
            raise ValueError("comparison review budget exceeded")
        rows = []
        for window in windows:
            frames = structural_frames(window.edges)
            candidates = []
            for event in events:
                if event["capacity_reached"] or not words(frames) & words(event["structural_frames"]):
                    continue
                fit = review_pair(window, event, sample_rate=rate)
                candidates.append({"event_id": event["event_id"], "alignment": fit})
            history = []
            for segment in window.pretrigger:
                meta = segment.metadata
                hints = []
                left = _tokens(segment.edges)
                for event in events:
                    right = _tokens([run * 1000000 / rate for run in event["signed_samples"]])
                    block = SequenceMatcher(None, left, right, autojunk=False).find_longest_match()
                    if block.size >= 32:
                        signature = left[block.a : block.a + block.size]
                        hints.append(
                            {
                                "event_id": event["event_id"],
                                "matching_runs": block.size,
                                "source_run": block.a,
                                "sdr_run": block.b,
                                "source_occurrences": len(_occurrences(left, signature)),
                                "sdr_occurrences": len(_occurrences(right, signature)),
                            }
                        )
                history.append(
                    {
                        "source_capture_id": meta["id"],
                        "source_word_offset": meta["source_offset"],
                        "duration_us": sum(map(abs, segment.edges)),
                        "words": len(segment.words),
                        "seal_age_before_main_arm_us": window.metadata["arm_us"] - meta["seal_us"],
                        "callback_to_main_arm_us": window.metadata["arm_us"] - meta["callback_us"],
                        "structural_frames": structural_frames(segment.edges),
                        "edge_signature_hints_not_associations": hints,
                    }
                )
            rows.append(
                {
                    "capture_id": window.metadata["id"],
                    "structural_frames": frames,
                    "candidates": candidates,
                    "pretrigger_summary": window.pretrigger_info,
                    "history": history,
                }
            )
        for row in rows:
            row["status"] = "unmatched" if not row["candidates"] else "ambiguous"
            if len(row["candidates"]) != 1:
                continue
            candidate = row["candidates"][0]
            identity = candidate["event_id"]
            claims = sum(any(item["event_id"] == identity for item in other["candidates"]) for other in rows)
            if claims != 1:
                continue
            row["status"] = "unique_structural_event"
            aligned = candidate["alignment"]
            if aligned is None or aligned["fit"]["status"] != "fitted":
                continue
            event = next(event for event in events if event["event_id"] == identity)
            aligned["estimated_missing_start_us"] = (
                (aligned["fit"]["sample_offset"] - event["start_sample"]) * 1000000 / rate
            )
            row["status"] = "unique_structural_event_with_local_fit"
        report["trials"].append(
            {
                "trial": folder.name,
                "provenance": baseline["provenance"],
                "statuses": dict(Counter(row["status"] for row in rows)),
                "sdr_events": events,
                "windows": rows,
            }
        )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("analysis", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    report = review(args.session, args.analysis)
    write_json(args.output, report)
    print("Comparison saved separately; no capture/production decoder changes made.")


if __name__ == "__main__":
    main()
