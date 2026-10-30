"""Bounded file/live JSON collection; stdout contains only a resulting Learning ID."""

from __future__ import annotations

import argparse
import json
import math
import os
import select
import stat
import sys
import time
from collections.abc import Iterator
from contextlib import nullcontext
from typing import BinaryIO

from proflame2_protocol.derivation import ProfileConflictError
from proflame2_protocol.learning_id import decode_learning_id, encode_learning_id
from proflame2_protocol.models import FireplaceFeatures
from proflame2_protocol.rtl433 import normalize_rtl433_record

from .evidence import Evidence

MAX_LINE = 16384
MAX_INPUT = 16 * 1024 * 1024
MAX_RECORDS = 100000
GUIDANCE = (
    "Use your fireplace remote to change several settings, such as Light Up or Down\n"
    "and Fan Up or Down. Pause after each change to give the remote time to send it\n"
    "again. If a change still needs confirmation, make that same change again."
)
GATE_MESSAGES = {
    1: "Step 1 of 3: Your remote has been identified. Keep making different changes.",
    2: "Step 2 of 3: The additional commands match. A few more checks are needed.",
    3: "Step 3 of 3: All checks passed. Your Learning ID is ready.",
}


def records(stream: BinaryIO, *, live: bool, timeout: float) -> Iterator[bytes]:
    """Bound partial lines as well as complete records and enforce live deadlines."""
    pending = bytearray()
    total = count = 0
    deadline = time.monotonic() + timeout
    while True:
        if live:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([stream.fileno()], [], [], remaining)[0]:
                raise ValueError("Collection timed out before confirmation was complete.")
            chunk = os.read(stream.fileno(), 4096)
        else:
            chunk = stream.read(4096)
        total += len(chunk)
        if total > MAX_INPUT:
            raise ValueError("Input size limit reached.")
        pending.extend(chunk)
        while b"\n" in pending or (not chunk and pending):
            index = pending.find(b"\n")
            length = index + 1 if index >= 0 else len(pending)
            if length > MAX_LINE:
                raise ValueError("A JSON input line exceeds the length limit.")
            line = bytes(pending[:length])
            del pending[:length]
            count += 1
            if count > MAX_RECORDS:
                raise ValueError("Input record limit reached.")
            if line.strip():
                yield line
        if len(pending) > MAX_LINE:
            raise ValueError("A JSON input line exceeds the length limit.")
        if not chunk:
            return


def main(argv: list[str] | None = None) -> int:
    """Collect evidence without transmitting or changing Home Assistant state."""
    parser = argparse.ArgumentParser(description="Create a Proflame2 Learning ID from rtl_433 JSON commands.")
    parser.add_argument("file", nargs="?", help="Saved JSON file; omit or use - for standard input.")
    parser.add_argument(
        "--input-mode", choices=("file", "live"), help="Pipes default to live; saved files default to file."
    )
    parser.add_argument("--timeout", type=float, default=120, help="Live collection limit in seconds (default: 120).")
    parser.add_argument("--verbose", "-v", action="store_true", help="Explain progress and missing evidence.")
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or not 0 < args.timeout <= 3600:
        parser.error("--timeout must be greater than zero and at most 3600 seconds.")
    # Derivation's RemoteProfile model carries local feature defaults, but the
    # Learning ID codec exports remote data only. HA asks for features at import.
    evidence = Evidence(FireplaceFeatures())
    named = args.file not in (None, "-")
    try:
        with open(args.file, "rb") if named else nullcontext(sys.stdin.buffer) as stream:
            mode = args.input_mode or ("file" if named or stat.S_ISREG(os.fstat(stream.fileno()).st_mode) else "live")
            live = mode == "live"
            if live and args.verbose:
                print(GUIDANCE, file=sys.stderr)
            for line_number, line in enumerate(records(stream, live=live, timeout=args.timeout), 1):
                try:
                    record = json.loads(line)
                except (ValueError, UnicodeError, RecursionError) as exc:
                    raise ValueError(f"Malformed JSON on input line {line_number}.") from exc
                normalized = normalize_rtl433_record(record)
                if normalized is None:
                    continue
                previous_gate = evidence.gate
                evidence.add(normalized.frame)
                if args.verbose:
                    if live and evidence.gate != previous_gate:
                        print(GATE_MESSAGES[evidence.gate], file=sys.stderr)
                    print(evidence.progress(), file=sys.stderr)
                if live and evidence.gate == 3:
                    break
            if not live:
                print(evidence.file_summary(), file=sys.stderr)
            elif evidence.gate != 3:
                print(
                    "No Learning ID was generated. Input ended before all confirmation checks passed.", file=sys.stderr
                )
                print(evidence.progress(), file=sys.stderr)
                return 1
            if evidence.gate == 0 or evidence.profile is None:
                return 1
            learning_id = encode_learning_id(evidence.profile)
            if decode_learning_id(learning_id) != evidence.profile:
                raise ValueError("Learning ID verification failed.")
            print(learning_id)
            sys.stdout.flush()
            return 0
    except ProfileConflictError:
        print(
            "No Learning ID was generated.\n"
            "Some commands in this input disagree about your remote's settings.\n"
            "The input could contain commands from different remotes or damaged recordings.",
            file=sys.stderr,
        )
        return 1
    except (ValueError, OSError) as exc:
        # OSError filenames may contain private local paths; disclose only the kind.
        message = str(exc) if isinstance(exc, ValueError) else "Unable to read the input or write the result."
        print(f"No Learning ID was generated.\n{message}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("No Learning ID was generated. Collection was cancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
