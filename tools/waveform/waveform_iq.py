"""Extract explicit SDR sample windows without loading complete IQ recordings."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

BYTES_PER_SAMPLE = {"cu8": 2, "cs16": 4, "cf32": 8}


def sample_window(
    start_us: float,
    end_us: float,
    alignment: dict[str, float],
    *,
    margin_us: float = 0,
    uncertainty_samples: float = 0,
) -> dict[str, int | bool]:
    """Expand independently aligned receiver times conservatively to sample indexes.

    Alignment must come from matched RF events, not firmware poll/log times.
    This function does not establish whether supplied anchors are valid.
    """
    slope = alignment["samples_per_us"]
    offset = alignment["sample_offset"]
    residual = alignment["max_residual_samples"]
    if not all(
        math.isfinite(value) for value in (start_us, end_us, margin_us, uncertainty_samples, slope, offset, residual)
    ):
        raise ValueError("finite IQ window parameters required")
    if end_us <= start_us or slope <= 0 or min(margin_us, uncertainty_samples, residual) < 0:
        raise ValueError("invalid IQ window bounds or uncertainty")
    padding = uncertainty_samples + residual + margin_us * slope
    start = math.floor(offset + slope * start_us - padding)
    end = math.ceil(offset + slope * end_us + padding)
    if end <= 0:
        raise ValueError("IQ window precedes recording")
    return {"start_sample": max(0, start), "end_sample": end, "pretrigger_clipped": start < 0}


def extract_iq(source: Path, destination: Path, *, sample_format: str, start_sample: int, end_sample: int) -> dict:
    """Write an exclusive output; remove only our own incomplete file on failure."""
    if sample_format not in BYTES_PER_SAMPLE:
        raise ValueError("unsupported IQ sample format")
    if type(start_sample) is not int or type(end_sample) is not int or not 0 <= start_sample < end_sample:
        raise ValueError("sample indexes must be increasing nonnegative integers")
    if source.resolve() == destination.resolve():
        raise ValueError("source and destination must differ")
    width = BYTES_PER_SAMPLE[sample_format]
    digest = hashlib.sha256()
    with source.open("rb") as recording:
        total_bytes = source.stat().st_size
        if total_bytes % width or end_sample * width > total_bytes:
            raise ValueError("IQ recording is truncated or window exceeds recording")
        recording.seek(start_sample * width)
        remaining = (end_sample - start_sample) * width
        created = False
        try:
            with destination.open("xb") as output:
                created = True
                while remaining:
                    block = recording.read(min(remaining, 1024 * 1024))
                    if not block:
                        raise ValueError("IQ recording ended during extraction")
                    output.write(block)
                    digest.update(block)
                    remaining -= len(block)
        except BaseException:
            if created:
                destination.unlink(missing_ok=True)
            raise
    return {
        "filename": destination.name,
        "sample_format": sample_format,
        "start_sample": start_sample,
        "end_sample": end_sample,
        "bytes": (end_sample - start_sample) * width,
        "sha256": digest.hexdigest(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--format", choices=tuple(BYTES_PER_SAMPLE), required=True)
    parser.add_argument("--start-sample", type=int, required=True)
    parser.add_argument("--end-sample", type=int, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            extract_iq(
                args.source,
                args.destination,
                sample_format=args.format,
                start_sample=args.start_sample,
                end_sample=args.end_sample,
            )
        )
    )


if __name__ == "__main__":
    main()
