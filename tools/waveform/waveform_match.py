"""Match bounded raw RMT windows to offline rtl_433 RF events and extract IQ slices.

No receiver callback/log timestamp is treated as an RF edge. Local fits use
elapsed raw edge durations within one window; global receiver clock alignment
remains unsupported until its hardware timing anchor is independently calibrated.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import statistics
import subprocess
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from itertools import accumulate
from pathlib import Path

from tools.waveform.waveform_diagnostics import RawWindow, classify_edges, fit_sample_alignment, read_windows
from tools.waveform.waveform_iq import extract_iq

MAX_EVENTS = 4096
MAX_RUNS = 2399


@dataclass(frozen=True)
class RFEvent:
    event_id: int
    sample_rate_hz: int
    start_sample: int
    end_sample: int
    signed_samples: tuple[int, ...]
    terminal_gap_samples: int
    capacity_reached: bool

    @property
    def durations_us(self) -> tuple[int, ...]:
        return tuple(
            (1 if sample > 0 else -1) * max(1, round(abs(sample) * 1_000_000 / self.sample_rate_hz))
            for sample in self.signed_samples
        )

    def frames(self) -> list[dict]:
        return classify_edges(self.durations_us)


def read_events(lines: Iterable[str], *, sample_rate_hz: int, total_samples: int) -> list[RFEvent]:
    """Validate exact sample indexes and terminal footer, including empty recordings."""
    events = []
    header = False
    ended = False
    last_end = 0
    for line in lines:
        record = json.loads(line)
        if not isinstance(record, dict) or ended:
            raise ValueError("unexpected detector record")
        kind = record.get("kind")
        if kind == "engine":
            if header or record.get("schema_version") != 1 or record.get("sample_format") != "cu8":
                raise ValueError("invalid detector header")
            if record.get("sample_rate_hz") != sample_rate_hz or record.get("detector") != "rtl_433":
                raise ValueError("detector configuration mismatch")
            header = True
        elif kind == "event":
            if not header or len(events) >= MAX_EVENTS:
                raise ValueError("missing detector header or event budget exceeded")
            integer_fields = ("id", "start_sample", "end_sample", "pulse_count", "terminal_gap_samples")
            if any(type(record.get(key)) is not int for key in integer_fields):
                raise ValueError("invalid detector integer field")
            runs = record.get("signed_samples")
            if not isinstance(runs, list) or not runs or len(runs) > MAX_RUNS or len(runs) % 2 != 1:
                raise ValueError("invalid detector run count")
            if any(type(run) is not int or run == 0 or (run > 0) != (index % 2 == 0) for index, run in enumerate(runs)):
                raise ValueError("invalid detector polarity or duration")
            if (
                record["id"] != len(events) + 1
                or not last_end <= record["start_sample"] < record["end_sample"] <= total_samples
                or record["start_sample"] + sum(abs(run) for run in runs) != record["end_sample"]
                or record["pulse_count"] != (len(runs) + 1) // 2
                or record["terminal_gap_samples"] < 0
                or type(record.get("capacity_reached")) is not bool
            ):
                raise ValueError("inconsistent detector sample bounds")
            events.append(
                RFEvent(
                    record["id"],
                    sample_rate_hz,
                    record["start_sample"],
                    record["end_sample"],
                    tuple(runs),
                    record["terminal_gap_samples"],
                    record["capacity_reached"],
                )
            )
            last_end = record["end_sample"]
        elif kind == "end":
            if not header or record.get("samples_read") != total_samples or record.get("event_count") != len(events):
                raise ValueError("incomplete detector input or missing whole events")
            ended = True
        else:
            raise ValueError("unknown detector record")
    if not header or not ended:
        raise ValueError("missing detector header or footer")
    return events


def detect_iq(
    source: Path, detector: Path, *, sample_rate_hz: int, fixed_level_db: float | None = None
) -> list[RFEvent]:
    """Use the proven upstream detector offline, with bounded temporary output."""
    if type(sample_rate_hz) is not int or not 100000 <= sample_rate_hz <= 2400000:
        raise ValueError("unsupported sample rate")
    if fixed_level_db is not None and (not math.isfinite(fixed_level_db) or not -100 <= fixed_level_db < 0):
        raise ValueError("invalid fixed detector level")
    size = source.stat().st_size
    if size == 0 or size % 2:
        raise ValueError("empty or truncated CU8 recording")
    if size // 2 > sample_rate_hz * 3600:
        raise ValueError("split recordings longer than one hour before discovery")
    command = [str(detector.resolve()), str(source.resolve()), str(sample_rate_hz)]
    if fixed_level_db is not None:
        command.append(str(fixed_level_db))
    with tempfile.TemporaryDirectory(prefix="proflame2-waveform-detector-", dir="/tmp") as scratch:
        output = Path(scratch) / "events.jsonl"
        with output.open("wb") as destination, (Path(scratch) / "detector.stderr").open("wb") as errors:
            result = subprocess.run(command, stdout=destination, stderr=errors, timeout=300, check=False)
        if result.returncode != 0:
            raise ValueError("offline RF detector failed; no complete event data accepted")
        with output.open() as records:
            return read_events(records, sample_rate_hz=sample_rate_hz, total_samples=size // 2)


def fit_local_alignment(
    anchors: Sequence[tuple[float, float]],
    *,
    sample_rate_hz: int,
    residual_limit_us: float = 100,
    max_drift_ppm: float = 5000,
) -> dict:
    """Robust initialization then explicit inlier fit; retain all residuals/outliers.

    This is a window-relative fit, not calibration of the firmware's wall clock.
    Large deviations remain reportable evidence, not silently corrected widths.
    """
    if (
        len(anchors) < 6
        or len(anchors) > 96
        or sample_rate_hz <= 0
        or not all(math.isfinite(v) for pair in anchors for v in pair)
        or not math.isfinite(residual_limit_us)
        or residual_limit_us <= 0
        or not math.isfinite(max_drift_ppm)
        or max_drift_ppm < 0
    ):
        raise ValueError("invalid local alignment inputs")
    if any(x1 >= x2 or y1 >= y2 for (x1, y1), (x2, y2) in zip(anchors, anchors[1:], strict=False)):
        raise ValueError("edge anchors must preserve time order")
    if anchors[-1][0] - anchors[0][0] < 10000:
        return {"status": "unsupported", "reason": "insufficient_edge_span"}
    slopes = [(y2 - y1) / (x2 - x1) for index, (x1, y1) in enumerate(anchors) for x2, y2 in anchors[index + 1 :]]
    slope = statistics.median(slopes)
    offset = statistics.median(y - slope * x for x, y in anchors)
    tolerance = residual_limit_us * sample_rate_hz / 1_000_000
    indices = [i for i, (x, y) in enumerate(anchors) if abs(y - offset - slope * x) <= tolerance]
    if len(indices) < max(6, math.ceil(0.8 * len(anchors))):
        return {"status": "unsupported", "reason": "inconsistent_edge_correspondence"}
    fit = fit_sample_alignment([anchors[index] for index in indices])
    drift = (fit["samples_per_us"] / (sample_rate_hz / 1_000_000) - 1) * 1_000_000
    residuals = [y - fit["sample_offset"] - fit["samples_per_us"] * x for x, y in anchors]
    return {
        **fit,
        "status": "fitted" if abs(drift) <= max_drift_ppm else "unsupported",
        "reason": "window_relative_only" if abs(drift) <= max_drift_ppm else "drift_outside_limit",
        "coordinate": "receiver_window_relative_us",
        "drift_ppm": drift,
        "valid_span_us": [anchors[indices[0]][0], anchors[indices[-1]][0]],
        "anchors": [list(pair) for pair in anchors],
        "residual_samples": residuals,
        "outlier_indices": [i for i in range(len(anchors)) if i not in indices],
    }


def _tokens(durations: Sequence[int]) -> list[int]:
    return [(1 if value > 0 else -1) * max(1, round(abs(value) / 417)) for value in durations]


def _occurrences(sequence: Sequence[int], pattern: Sequence[int]) -> list[int]:
    # A finite 4096-word receiver window / 1200-pulse SDR package bounds this search.
    return [
        index for index in range(len(sequence) - len(pattern) + 1) if sequence[index : index + len(pattern)] == pattern
    ]


def _candidate(window: RawWindow, event: RFEvent) -> dict | None:
    left_frames, right_frames = window.frames(), event.frames()
    left_words = {tuple(frame["wire_words"]) for frame in left_frames}
    right_words = {tuple(frame["wire_words"]) for frame in right_frames}
    same_payload = bool(left_words & right_words)
    if event.capacity_reached or (left_words and right_words and not same_payload):
        return None
    left, right = _tokens(window.edges), _tokens(event.durations_us)
    if same_payload:
        blocks = []
        for left_frame in left_frames:
            for right_frame in right_frames:
                if left_frame["wire_words"] != right_frame["wire_words"]:
                    continue
                a, b = left_frame["edge_start"], right_frame["edge_start"]
                block = SequenceMatcher(
                    None, left[a : left_frame["edge_end"]], right[b : right_frame["edge_end"]], autojunk=False
                ).find_longest_match()
                blocks.append((block.size, block.a + a, block.b + b))
        size, a, b = max(blocks)
        block = (a, b, size)
    else:
        if len(set(left)) < 4 or len(set(right)) < 4:
            return None
        block = SequenceMatcher(None, left, right, autojunk=False).find_longest_match()
    a, b, size = block
    if size < 48:
        return None
    coverage = size / max(len(left), len(right))
    if not same_payload and (coverage < 0.8 or len(set(left[a : a + size])) < 4):
        return None
    signature = left[a : a + size]
    ambiguous = len(_occurrences(left, signature)) != 1 or len(_occurrences(right, signature)) != 1
    alignment = {"status": "unsupported", "reason": "repeated_edge_signature_ambiguous"}
    if not ambiguous:
        left_times = list(accumulate(map(abs, window.edges), initial=0))
        right_times = list(accumulate(map(abs, event.signed_samples), initial=event.start_sample))
        count = min(96, size + 1)
        selected = sorted({round(index * size / (count - 1)) for index in range(count)})
        anchors = [(left_times[a + index], right_times[b + index]) for index in selected]
        alignment = fit_local_alignment(anchors, sample_rate_hz=event.sample_rate_hz)
    return {
        "event_id": event.event_id,
        "evidence": "decoded_payload_and_edges" if same_payload else "edge_signature_only",
        "matching_edges": size,
        "signature_coverage": coverage,
        "local_alignment": alignment,
        "sdr_start_sample": event.start_sample,
        "sdr_end_sample": event.end_sample,
    }


def match_events(windows: Sequence[RawWindow], events: Sequence[RFEvent]) -> dict:
    """Keep ambiguous candidates; never guess ownership from UI/prompt order."""
    if len(events) > MAX_EVENTS or len(events) * len(windows) > 16384:
        raise ValueError("event matching budget exceeded")
    if len({event.event_id for event in events}) != len(events):
        raise ValueError("duplicate RF event identity")
    matches = []
    for window in windows:
        metadata = window.metadata
        unhealthy = any(
            metadata.get(key, 0)
            for key in ("saturated", "timeout", "queue_losses", "ignored_callbacks", "ignored_symbols")
        )
        candidates = [] if unhealthy else [candidate for event in events if (candidate := _candidate(window, event))]
        matches.append(
            {
                "session": metadata.get("session"),
                "capture_id": metadata["id"],
                "status": (
                    "capture_unhealthy"
                    if unhealthy
                    else "matched" if len(candidates) == 1 else "ambiguous" if candidates else "unmatched"
                ),
                "candidates": candidates,
            }
        )
    # Competing receiver windows cannot independently claim a single RF event.
    for match in matches:
        if match["status"] != "matched":
            continue
        event_id = match["candidates"][0]["event_id"]
        if sum(any(candidate["event_id"] == event_id for candidate in other["candidates"]) for other in matches) > 1:
            match["status"] = "ambiguous"
    return {
        "schema_version": 1,
        "matches": matches,
        "detector_limits": {
            "partial_events_at_eof_may_be_unreported": True,
            "terminal_gap_is_detector_cutoff": True,
            "short_glitches_may_be_suppressed": True,
            "sample_continuity_not_proven_by_cu8": True,
            "systematic_edge_bias_uncalibrated": True,
        },
        "global_clock_alignment": {
            "status": "unsupported",
            "reason": "callback_timestamps_are_not_calibrated_rf_edges",
        },
        "ready_for_precision_comparison": False,
    }


def write_comparison(
    source: Path,
    output: Path,
    windows: Sequence[RawWindow],
    events: Sequence[RFEvent],
    *,
    margin_ms: float = 20,
    provenance: dict | None = None,
) -> dict:
    """Extract matched event bounds directly, not through uncalibrated MCU clocks."""
    if not math.isfinite(margin_ms) or margin_ms < 0:
        raise ValueError("invalid IQ context margin")
    report = match_events(windows, events)
    total_samples = source.stat().st_size // 2
    if source.stat().st_size % 2 or any(event.end_sample > total_samples for event in events):
        raise ValueError("truncated CU8 input")
    report["provenance"] = provenance or {}
    output.mkdir()  # Never overwrite an existing directory, even if empty.
    try:
        slices = []
        by_id = {event.event_id: event for event in events}
        for match in report["matches"]:
            if match["status"] != "matched":
                continue
            event = by_id[match["candidates"][0]["event_id"]]
            padding = math.ceil(margin_ms * event.sample_rate_hz / 1000)
            start = max(0, event.start_sample - padding)
            end = min(source.stat().st_size // 2, event.end_sample + padding)
            artifact = extract_iq(
                source,
                output / f"event-{event.event_id:04d}.cu8",
                sample_format="cu8",
                start_sample=start,
                end_sample=end,
            )
            slices.append(
                {
                    **artifact,
                    "event_id": event.event_id,
                    "context_clipped": start == 0 or end == source.stat().st_size // 2,
                }
            )
        report["iq_windows"] = slices
        (output / "matches.json").write_text(json.dumps(report, indent=2) + "\n")
        (output / "events.json").write_text(
            json.dumps([{**event.__dict__, "frames": event.frames()} for event in events], indent=2) + "\n"
        )
    except BaseException:
        shutil.rmtree(output)
        raise
    return report


def file_digest(path: Path) -> dict:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return {"filename": path.name, "bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("iq", type=Path)
    parser.add_argument("receiver_log", type=Path)
    parser.add_argument("--detector", required=True, type=Path)
    parser.add_argument("--sample-rate", required=True, type=int)
    parser.add_argument("--fixed-level-db", type=float)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output directory already exists")
    with args.receiver_log.open() as source:
        windows = read_windows(source, require_complete_sessions=True)
    events = detect_iq(args.iq, args.detector, sample_rate_hz=args.sample_rate, fixed_level_db=args.fixed_level_db)
    provenance = {
        "detector": file_digest(args.detector),
        "iq": file_digest(args.iq),
        "receiver_log": file_digest(args.receiver_log),
        "analysis_code": file_digest(Path(__file__)),
        "classification_code": file_digest(Path(__file__).with_name("waveform_diagnostics.py")),
        "iq_slice_code": file_digest(Path(__file__).with_name("waveform_iq.py")),
        "sample_rate_hz": args.sample_rate,
        "sample_format": "cu8",
        "fixed_level_db": args.fixed_level_db,
        "detector_min_high_level_db": -12,
        "detector_min_snr_db": 9,
    }
    write_comparison(args.iq, args.output, windows, events, provenance=provenance)
    print(json.dumps({"events": len(events), "receiver_windows": len(windows), "precision_ready": False}))


if __name__ == "__main__":
    main()
