"""Probe raw RF acquisition with a Yard Stick One without Proflame2 decoding."""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from custom_components.proflame2.rf.capture import find_proflame_candidates
from custom_components.proflame2.rf.yardstick import (
    PROBE_RX_BANDWIDTH,
    YARDSTICK_RX_LEARNING_FREQUENCY_HZ,
    YARDSTICK_RX_LEARNING_PACKET_BYTES,
    YARDSTICK_RX_LEARNING_SWEEP_ENABLED,
    YardStickBackend,
    YardStickBackendUnavailableError,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Open a YARD Stick One in wide-open probe mode and print raw RF payloads "
            "without attempting Proflame2 decode."
        )
    )
    parser.set_defaults(no_sweep=not YARDSTICK_RX_LEARNING_SWEEP_ENABLED)
    parser.add_argument("--device-index", type=int, default=0, help="RfCat device index to open.")
    parser.add_argument(
        "--frequency-hz",
        type=int,
        default=YARDSTICK_RX_LEARNING_FREQUENCY_HZ,
        help=f"Probe frequency in Hz. Defaults to {YARDSTICK_RX_LEARNING_FREQUENCY_HZ}.",
    )
    parser.add_argument(
        "--fixed-frequency",
        type=int,
        default=None,
        help="Override the probe to one fixed frequency in Hz.",
    )
    parser.add_argument(
        "--data-rate",
        type=int,
        default=2_400,
        help="Receive data rate in bits per second. Defaults to 2400.",
    )
    parser.add_argument(
        "--payload-length",
        type=int,
        default=YARDSTICK_RX_LEARNING_PACKET_BYTES,
        help=f"RFrecv payload length in bytes. Defaults to {YARDSTICK_RX_LEARNING_PACKET_BYTES}.",
    )
    parser.add_argument(
        "--sample-timeout",
        type=float,
        default=1.0,
        help="Seconds to wait per RF receive attempt before polling again.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Alias for --sample-timeout.",
    )
    parser.add_argument(
        "--no-sweep",
        action="store_true",
        help="Disable frequency sweeping and stay on one fixed frequency.",
    )
    parser.add_argument(
        "--sweep",
        dest="no_sweep",
        action="store_false",
        help="Enable frequency sweeping across the configured receive window.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print candidate summaries when Proflame-like frames are found.",
    )
    return parser


async def _run(args: argparse.Namespace) -> int:
    timeout = args.timeout if args.timeout is not None else args.sample_timeout
    fixed_frequency_hz = args.fixed_frequency if args.fixed_frequency is not None else args.frequency_hz
    sweep_enabled = not args.no_sweep
    backend = YardStickBackend(
        device_index=args.device_index,
        frequency_hz=fixed_frequency_hz,
        data_rate=args.data_rate,
        probe_mode=True,
        packet_length_bytes=args.payload_length,
        sweep_enabled=sweep_enabled,
    )

    try:
        await backend.connect()
    except YardStickBackendUnavailableError as exc:
        print(exc)
        return 2

    print("YARD Stick One connected.")
    print(
        "Probe mode: "
        f"{'fixed-frequency' if not sweep_enabled else 'sweeping'} raw sniff at {fixed_frequency_hz} Hz, "
        f"ASK/OOK, data_rate={args.data_rate}, payload_length={args.payload_length}."
    )
    print(
        "Requested probe characteristics: "
        f"wide bandwidth={PROBE_RX_BANDWIDTH}, minimal sync filtering, raw/variable packet mode when supported."
    )
    print("Listening for any RF payloads. Press remote buttons. Ctrl+C to stop.")

    try:
        while True:
            started = datetime.now(timezone.utc)
            raw_payload = await backend.receive_raw_payload(timeout=timeout)
            ended = datetime.now(timezone.utc)
            if raw_payload is None:
                print(f"{ended.isoformat()} timeout no_payload")
                continue

            candidates = find_proflame_candidates(raw_payload)
            print(
                f"{ended.isoformat()} payload "
                f"started={started.isoformat()} "
                f"bytes={len(raw_payload)} "
                f"candidates={len(candidates)} "
                f"hex={raw_payload.hex()}"
            )
            if args.verbose and candidates:
                for candidate in candidates[:5]:
                    print(
                        "  candidate "
                        f"bit_offset={candidate.bit_offset} "
                        f"symbol_offset={candidate.symbol_offset} "
                        f"repeat_count={candidate.repeat_count} "
                        f"remote=0x{candidate.sample.remote_id:06X} "
                        f"cmd1=0x{candidate.sample.cmd1:02X} "
                        f"err1=0x{candidate.sample.err1:02X} "
                        f"cmd2=0x{candidate.sample.cmd2:02X} "
                        f"err2=0x{candidate.sample.err2:02X}"
                    )
    except KeyboardInterrupt:
        print("\nStopping probe.")
        return 0
    finally:
        await backend.close()


def main() -> int:
    """Run the raw RF acquisition probe."""

    parser = _build_parser()
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
