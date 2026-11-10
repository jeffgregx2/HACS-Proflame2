"""Import bounded PFRAW1/PFRAW2 windows without altering measured durations.

Run: python -m tools.waveform.waveform_diagnostics capture.log --output selected.json
Use --include-rejected to retain bounded candidates without a decodable frame.
The input log and original SDR recording remain the source artifacts.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from tools.waveform.waveform_buffered import read_buffered
from tools.waveform.waveform_gpio_edges import GPIOEdgeCapture, read_gpio_edges
from tools.waveform.waveform_protocol import find_proflame_pcm_candidates

MAX_SYMBOLS = 4096


def checksum(words: Sequence[int]) -> int:
    """FNV-1a of packed words in little-endian byte order (transport check)."""
    value = 2166136261
    for word in words:
        for byte in word.to_bytes(4, "little"):
            value = ((value ^ byte) * 16777619) & 0xFFFFFFFF
    return value


@dataclass(frozen=True)
class RawWindow:
    """One complete exported record, not necessarily a complete RF burst."""

    metadata: dict[str, int]
    words: tuple[int, ...]
    pretrigger: tuple[RawWindow, ...] = ()
    pretrigger_info: dict[str, int] = field(default_factory=dict)
    gpio_edges: GPIOEdgeCapture | None = None

    @property
    def edges(self) -> tuple[int, ...]:
        runs = []
        for word in self.words:
            for shift in (0, 16):
                duration = (word >> shift) & 0x7FFF
                if duration:
                    runs.append(duration if word & (1 << (shift + 15)) else -duration)
        return tuple(runs)

    def frames(self) -> list[dict]:
        """Classify derived bits; retain raw edge indexes and original durations."""
        return classify_edges(self.edges)


def classify_edges(edges: Sequence[int]) -> list[dict]:
    """Classify a derived copy of signed microsecond runs, never edit measurements."""
    frames = []
    bits = ""
    edge_indexes: list[int] = []

    def finish() -> None:
        offset = 0
        while offset < len(bits):
            candidates = find_proflame_pcm_candidates(bits[offset:])
            if not candidates:
                break
            # Normal decoding deduplicates identical repeats; waveform analysis must not.
            candidate = candidates[0]
            length = 260 if candidate.extension_words else 182
            bit_start = offset + candidate.bit_offset
            bit_end = min(bit_start + length, len(bits))
            frames.append(
                {
                    "format": candidate.frame_format,
                    "wire_words": list(candidate.words),
                    "edge_start": edge_indexes[bit_start],
                    "edge_end": edge_indexes[bit_end - 1] + 1,
                    "selection": "extended_integrity" if candidate.extension_words else "legacy_structure",
                }
            )
            offset = bit_end

    for index, duration in enumerate(edges):
        units = (abs(duration) + 208) // 417
        if not 1 <= units <= (80 if duration < 0 else 3):
            finish()
            bits, edge_indexes = "", []
            continue
        bits += ("1" if duration > 0 else "0") * units
        edge_indexes.extend([index] * units)
    finish()
    return frames


def read_windows(lines: Iterable[str], *, require_complete_sessions: bool = False) -> list[RawWindow]:
    """Reject missing, duplicate, out-of-order, oversized, or corrupted chunks."""
    source = list(lines)
    if any(re.search(r"\bPF(?:GPIO|EDGE)1 ", line) for line in source):
        plain, sessions, captures = read_gpio_edges(source)
        windows = read_windows(plain, require_complete_sessions=require_complete_sessions)
        result = []
        for window in windows:
            identity = (window.metadata.get("session", 0), window.metadata["id"])
            observer = captures.pop(identity, None)
            if identity[0] in sessions and observer is None:
                raise ValueError("missing GPIO observer capture")
            if observer is not None and (
                observer.metadata["request_us"]
                != max(0, window.metadata.get("gpio_anchor_us", window.metadata["arm_us"]) - 75000)
                or observer.metadata["snapshot_us"] < window.metadata["seal_us"]
            ):
                raise ValueError("GPIO observer timestamps do not match RMT capture")
            result.append(replace(window, gpio_edges=observer))
        if captures:
            raise ValueError("orphan GPIO observer capture")
        return result
    if any(re.search(r"\bPF(?:BATCH|ACQ|BOUND|STOP)1 ", line) for line in source):
        plain, announced, health, bounds = read_buffered(source)
        windows = read_windows(plain, require_complete_sessions=require_complete_sessions)
        result = []
        for window in windows:
            identity = (window.metadata.get("session", 0), window.metadata["id"])
            boundary = bounds.pop(identity, None)
            if identity[0] in announced:
                if boundary is None or boundary["arm_before_us"] != window.metadata["arm_us"]:
                    raise ValueError("missing or mismatched arm-call bounds")
                if (
                    not boundary["arm_after_us"] <= window.metadata["seal_us"] <= health[identity[0]]["ended_us"]
                    or boundary["gpio_anchor_us"] > window.metadata["seal_us"]
                ):
                    raise ValueError("invalid buffered-acquisition timing")
                window = replace(
                    window,
                    metadata={
                        **window.metadata,
                        **{key: boundary[key] for key in ("arm_after_us", "gpio_anchor_us", "gpio_anchor_observed")},
                    },
                )
            result.append(window)
        if bounds:
            raise ValueError("orphan buffered-acquisition arm bounds")
        return result
    if any(re.search(r"PF(?:HIST|PRE)1 ", line) for line in source):
        plain, histories = _read_pretrigger(source)
        windows = read_windows(plain, require_complete_sessions=require_complete_sessions)
        result = []
        for window in windows:
            identity = (window.metadata.get("session", 0), window.metadata["id"])
            entry = histories.pop(identity, None)
            if entry and any(segment.metadata["seal_us"] > window.metadata["arm_us"] for segment in entry[1]):
                raise ValueError("pretrigger overlaps capture acquisition")
            result.append(replace(window, pretrigger=entry[1], pretrigger_info=entry[0]) if entry else window)
        if histories:
            raise ValueError("incomplete capture after pretrigger")
        return result
    if require_complete_sessions:
        windows = read_windows(source)
        validate_sessions(source, windows)
        return windows
    windows = []
    header: dict[str, int] | None = None
    words: list[int] = []
    version = 1
    health_seen = False
    seen: set[tuple[int, int, int]] = set()
    for line in source:
        match = re.search(r"PFRAW([12]) (.*)", line)
        if match is None:
            continue
        record_version = int(match[1])
        payload = re.sub(r"\x1b\[[0-9;]*m", "", match[2]).strip()
        fields = {}
        for token in payload.split():
            pair = token.split("=", 1)
            if len(pair) != 2 or not re.fullmatch(r"[a-z_]+", pair[0]) or not re.fullmatch(r"[0-9a-f]+", pair[1]):
                raise ValueError("malformed raw field")
            key, value = pair
            if key in fields:
                raise ValueError("duplicate raw field")
            fields[key] = value
        if "count" in fields:
            if header is not None:
                raise ValueError("previous raw window is incomplete")
            required = {
                "id",
                "count",
                "saturated",
                "poll_us",
                "rearm_us",
                "resolution_hz",
                "filter_ns",
                "idle_ns",
                "checksum",
            }
            version = record_version
            health_seen = False
            if version == 2:
                required.add("session")
            if fields.keys() != required:
                raise ValueError("invalid raw window header")
            header = {key: int(value, 16 if key == "checksum" else 10) for key, value in fields.items()}
            identity = (version, header.get("session", 0), header["id"])
            if identity in seen:
                raise ValueError("duplicate exported capture identity")
            seen.add(identity)
            if not 0 < header["count"] <= MAX_SYMBOLS or header["resolution_hz"] != 1000000:
                raise ValueError("unsupported raw window bounds or resolution")
            if header["saturated"] not in (0, 1) or (version == 1 and header["rearm_us"] < header["poll_us"]):
                raise ValueError("invalid raw window health metadata")
            words = []
            continue
        if header is None or fields.get("id") != str(header["id"]) or record_version != version:
            raise ValueError("raw chunk does not match active window")
        if "arm_us" in fields and version == 2:
            expected = {
                "id",
                "arm_us",
                "callback_us",
                "seal_us",
                "gap_us",
                "callbacks",
                "queue_losses",
                "ignored_callbacks",
                "ignored_symbols",
                "final",
                "timeout",
            }
            if set(fields) != expected or health_seen or words:
                raise ValueError("invalid or duplicate raw health record")
            health = {key: int(value) for key, value in fields.items() if key != "id"}
            if health["final"] not in (0, 1) or health["timeout"] not in (0, 1):
                raise ValueError("invalid raw terminal flags")
            if not health["arm_us"] <= health["callback_us"] <= header["poll_us"] <= health["seal_us"]:
                raise ValueError("invalid raw timestamp order")
            if health["arm_us"] != header["rearm_us"]:
                raise ValueError("raw arm anchor mismatch")
            header.update(health)
            health_seen = True
        elif "words" in fields:
            if version == 2 and not health_seen:
                raise ValueError("missing raw health record")
            encoded = fields["words"]
            if set(fields) != {"id", "offset", "words"} or len(encoded) % 8 or not 0 < len(encoded) <= 128:
                raise ValueError("invalid raw chunk")
            if int(fields["offset"]) != len(words):
                raise ValueError("raw chunk missing, duplicated or reordered")
            words.extend(int(encoded[index : index + 8], 16) for index in range(0, len(encoded), 8))
            if len(words) > header["count"]:
                raise ValueError("raw chunk exceeds announced length")
        elif fields == {"id": str(header["id"]), "end": "1"}:
            if (
                (version == 2 and not health_seen)
                or len(words) != header["count"]
                or checksum(words) != header["checksum"]
            ):
                raise ValueError("raw window length/checksum mismatch")
            header["schema_version"] = version
            windows.append(RawWindow(header, tuple(words)))
            header = None
        else:
            raise ValueError("invalid raw chunk or terminator")
    if header is not None:
        raise ValueError("last raw window is incomplete")
    return windows


def _read_pretrigger(lines: Sequence[str]) -> tuple[list[str], dict]:
    """Verify separate history segments without inventing edges across blind gaps."""
    plain = []
    histories = {}
    segments: list[RawWindow] = []
    block: list[str] = []
    parent: int | None = None
    source_offset = 0
    active = False
    for line in lines:
        clean = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
        marker = re.search(r"PFHIST1 parent=(\d+) source_offset=(\d+)$", clean)
        history_line = re.search(r"PFHIST1 (.*)$", clean)
        summary = re.search(
            r"PFPRE1 session=(\d+) id=(\d+) requested_us=(\d+) duration_us=(\d+) segments=(\d+) short=([01]) limited=([01])$",
            clean,
        )
        if marker:
            if active or (parent is not None and parent != int(marker[1])):
                raise ValueError("incomplete or mismatched pretrigger segment")
            parent, source_offset = int(marker[1]), int(marker[2])
            if source_offset >= MAX_SYMBOLS:
                raise ValueError("invalid pretrigger source offset")
            block, active = [], True
        elif history_line:
            if not active:
                raise ValueError("pretrigger segment missing parent")
            block.append("PFRAW2 " + history_line[1])
            if re.fullmatch(r"id=\d+ end=1", history_line[1]):
                (segment,) = read_windows(block)
                if source_offset + len(segment.words) > MAX_SYMBOLS:
                    raise ValueError("pretrigger segment exceeds source window")
                segment.metadata["source_offset"] = source_offset
                segments.append(segment)
                if len(segments) > 32 or sum(len(item.words) for item in segments) > MAX_SYMBOLS:
                    raise ValueError("pretrigger storage bounds exceeded")
                active = False
        elif summary:
            session, identity, requested, duration, count, short, limited = map(int, summary.groups())
            key = (session, identity)
            measured = sum(abs(run) for segment in segments for run in segment.edges)
            if (
                active
                or key in histories
                or (parent is not None and parent != identity)
                or requested != 75000
                or count != len(segments)
                or measured != duration
                or short != int(duration < requested)
                or (limited and not short)
            ):
                raise ValueError("invalid pretrigger summary")
            previous = None
            for segment in segments:
                meta = segment.metadata
                if meta["session"] != session or meta["id"] >= identity:
                    raise ValueError("pretrigger source identity mismatch")
                if previous is not None and (meta["id"] <= previous["id"] or meta["arm_us"] < previous["seal_us"]):
                    raise ValueError("pretrigger segments reordered")
                previous = meta
            histories[key] = (
                {"requested_us": requested, "duration_us": duration, "short": short, "limited": limited},
                tuple(segments),
            )
            segments, parent = [], None
        elif "PFHIST1 " in clean or "PFPRE1 " in clean:
            raise ValueError("malformed pretrigger record")
        else:
            if active and "PFRAW" in clean:
                raise ValueError("interrupted pretrigger segment")
            plain.append(line)
    if active or segments:
        raise ValueError("incomplete pretrigger history")
    return plain, histories


def validate_sessions(lines: Iterable[str], windows: Sequence[RawWindow]) -> list[dict[str, str]]:
    """Require bounded session closure and detect loss of entire exported records.

    Does not prove RF continuity or detect an entire session missing from the log.
    Keep one boot/receiver per input. Aborting between complete records is valid;
    aborting mid-record is rejected by read_windows before this check.
    """
    counts: dict[int, int] = {}
    for window in windows:
        if window.metadata["schema_version"] != 2:
            raise ValueError("session verification requires PFRAW2")
        session = window.metadata["session"]
        counts[session] = counts.get(session, 0) + 1
    active: int | None = None
    seen: set[int] = set()
    sessions = []
    start: dict[str, str] = {}
    for line in lines:
        raw = re.search(r"PFRAW2 session=(\d+) ", line)
        if raw and (active is None or int(raw[1]) != active):
            raise ValueError("raw window outside its armed session")
        match = re.search(r"PFSESSION (.*)", line)
        if match is None:
            continue
        fields: dict[str, str] = {}
        payload = re.sub(r"\x1b\[[0-9;]*m", "", match[1]).strip()
        for token in payload.split():
            pair = token.split("=", 1)
            if len(pair) != 2 or pair[0] in fields:
                raise ValueError("malformed session record")
            fields[pair[0]] = pair[1]
        if not fields.get("session", "").isdigit():
            raise ValueError("invalid session identity")
        session = int(fields["session"])
        state = fields.get("state")
        if state == "armed":
            if (
                active is not None
                or session in seen
                or set(fields) != {"session", "state", "duration_ms"}
                or not fields["duration_ms"].isdigit()
                or not 1000 <= int(fields["duration_ms"]) <= 120000
            ):
                raise ValueError("invalid session start")
            active = session
            seen.add(session)
            start = fields
        elif state in {"paused", "aborted"}:
            if (
                active != session
                or set(fields) != {"session", "state", "exports", "suppressed", "end_us"}
                or any(not fields[key].isdigit() for key in ("exports", "suppressed", "end_us"))
                or int(fields["exports"]) != counts.get(session, 0)
            ):
                raise ValueError("session closure or exported-record count mismatch")
            active = None
            sessions.append({**start, **fields})
        else:
            raise ValueError("failed or unknown session state")
    if active is not None or not seen or counts.keys() - seen:
        raise ValueError("missing session start or closure")
    return sessions


def fit_sample_alignment(anchors: Sequence[tuple[float, float]]) -> dict[str, float]:
    """Fit SDR sample = offset + receiver_us * slope from independent RF anchors.

    Do not use log arrival or firmware poll timestamps as measured edge anchors.
    At least three anchors are needed to expose nonzero fitting residuals.
    """
    if len(anchors) < 2 or not all(math.isfinite(value) for pair in anchors for value in pair):
        raise ValueError("at least two finite alignment anchors required")
    x0, y0 = anchors[0]
    points = [(x - x0, y - y0) for x, y in anchors]
    mx = sum(x for x, _ in points) / len(points)
    my = sum(y for _, y in points) / len(points)
    denominator = sum((x - mx) ** 2 for x, _ in points)
    if denominator == 0:
        raise ValueError("alignment anchors need distinct times")
    slope = sum((x - mx) * (y - my) for x, y in points) / denominator
    if slope <= 0:
        raise ValueError("alignment must preserve time order")
    offset = y0 + my - slope * (x0 + mx)
    residual = max(abs(y - (offset + slope * x)) for x, y in anchors)
    return {"sample_offset": offset, "samples_per_us": slope, "max_residual_samples": residual}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--include-rejected", action="store_true")
    parser.add_argument("--require-complete-sessions", action="store_true")
    args = parser.parse_args()
    with args.log.open() as source:
        lines = list(source)
    windows = read_windows(lines, require_complete_sessions=args.require_complete_sessions)
    selected = []
    for window in windows:
        frames = window.frames()
        if frames or args.include_rejected:
            selected.append(
                {
                    "metadata": window.metadata,
                    "gpio_observer": window.gpio_edges.as_json() if window.gpio_edges else None,
                    "complete_burst": False,
                    "timing_anchor": (
                        "callback_upper_bound_only" if "callback_us" in window.metadata else "poll_upper_bound_only"
                    ),
                    "words": window.words,
                    "signed_durations_us": window.edges,
                    "frames": frames,
                    "pretrigger": {
                        "metadata": window.pretrigger_info,
                        "continuous": False,
                        "segments": [
                            {"metadata": segment.metadata, "words": segment.words, "signed_durations_us": segment.edges}
                            for segment in window.pretrigger
                        ],
                    },
                }
            )
    with args.output.open("x") as destination:
        json.dump(
            {
                "schema": "proflame_raw_ook_v1",
                "session_completeness_verified": args.require_complete_sessions,
                "sessions": validate_sessions(lines, windows) if args.require_complete_sessions else [],
                "windows": selected,
            },
            destination,
            indent=2,
        )


if __name__ == "__main__":
    main()
