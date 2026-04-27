"""Listen for Proflame2 packets on a Yard Stick One and derive remote ECC constants."""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from custom_components.proflame2.protocol.ecc import derive_ecc_profile
from custom_components.proflame2.rf.yardstick import (
    YARDSTICK_RX_LEARNING_FREQUENCY_HZ,
    YARDSTICK_RX_LEARNING_PACKET_BYTES,
    YARDSTICK_RX_LEARNING_SWEEP_ENABLED,
    YardStickBackend,
    YardStickDependencyError,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Receive Proflame2 packets with a Yard Stick One and derive a remote profile."
    )
    parser.add_argument("--device-index", type=int, default=0, help="RfCat device index to open.")
    parser.add_argument(
        "--frequency-hz",
        type=int,
        default=YARDSTICK_RX_LEARNING_FREQUENCY_HZ,
        help=f"Receive frequency in Hz. Defaults to {YARDSTICK_RX_LEARNING_FREQUENCY_HZ}.",
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
        "--no-sweep",
        action="store_true",
        default=not YARDSTICK_RX_LEARNING_SWEEP_ENABLED,
        help="Disable frequency sweeping and stay on the configured receive frequency.",
    )
    parser.add_argument(
        "--sweep",
        dest="no_sweep",
        action="store_false",
        help="Enable receive-frequency sweeping.",
    )
    return parser


async def _run(args: argparse.Namespace) -> int:
    backend = YardStickBackend(
        device_index=args.device_index,
        frequency_hz=args.frequency_hz,
        packet_length_bytes=args.payload_length,
        sweep_enabled=not args.no_sweep,
    )
    samples_by_remote: dict[int, list] = defaultdict(list)

    try:
        await backend.connect()
    except YardStickDependencyError as exc:
        print(exc)
        return 2

    print("Yard Stick One connected.")
    print("Listening for Proflame2 packets. Press remote buttons. Ctrl+C to stop.")

    try:
        while True:
            sample = await backend.receive_sample(timeout=args.sample_timeout)
            if sample is None:
                continue

            samples_by_remote[sample.remote_id].append(sample)
            print(
                "packet "
                f"remote=0x{sample.remote_id:06X} "
                f"cmd1=0x{sample.cmd1:02X} err1=0x{sample.err1:02X} "
                f"cmd2=0x{sample.cmd2:02X} err2=0x{sample.err2:02X}"
            )

            cmd1_samples = list({entry.cmd1_tuple for entry in samples_by_remote[sample.remote_id]})
            cmd2_samples = list({entry.cmd2_tuple for entry in samples_by_remote[sample.remote_id]})

            if len(cmd1_samples) < 2 or len(cmd2_samples) < 2:
                continue

            try:
                profile = derive_ecc_profile(cmd1_samples, cmd2_samples)
            except ValueError as exc:
                print(f"profile pending remote=0x{sample.remote_id:06X}: {exc}")
                continue

            print(
                "derived "
                f"remote=0x{sample.remote_id:06X} "
                f"C1=0x{profile.c1:X} D1=0x{profile.d1:X} "
                f"C2=0x{profile.c2:X} D2=0x{profile.d2:X}"
            )
    except KeyboardInterrupt:
        print("\nStopping listener.")
        return 0
    finally:
        await backend.close()


def main() -> int:
    """Run the Yard Stick One receive/learn loop."""

    parser = _build_parser()
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
