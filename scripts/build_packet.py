"""Dry-run builder for Proflame2 logical frames."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from custom_components.proflame2.protocol.encoder import encode_state
from custom_components.proflame2.protocol.models import ECCProfile, FireplaceFeatures, FireplaceState, RemoteProfile
from custom_components.proflame2.rf.waveform import build_transmission_plan


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a Proflame2 logical frame.")
    parser.add_argument("--id", required=True, help="Remote ID in hex, for example 3b3f02.")
    parser.add_argument("--c1", required=True, type=int, help="Cmd1 C nibble.")
    parser.add_argument("--d1", required=True, type=int, help="Cmd1 D nibble.")
    parser.add_argument("--c2", required=True, type=int, help="Cmd2 C nibble.")
    parser.add_argument("--d2", required=True, type=int, help="Cmd2 D nibble.")
    parser.add_argument("--power", choices=("on", "off"), required=True)
    parser.add_argument("--flame", type=int, default=1)
    parser.add_argument("--fan", type=int, default=0)
    parser.add_argument("--light", type=int, default=0)
    parser.add_argument("--front", action="store_true")
    parser.add_argument("--aux", action="store_true")
    parser.add_argument("--cpi", action="store_true")
    return parser


def _build_state(args: argparse.Namespace) -> FireplaceState:
    flame = 0 if args.power == "off" else args.flame
    return FireplaceState(
        power=(args.power == "on"),
        flame=flame,
        fan=args.fan,
        light=args.light,
        front=args.front,
        aux=args.aux,
        cpi=args.cpi,
    )


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    profile = RemoteProfile(
        serial_id=int(args.id, 16),
        ecc=ECCProfile(c1=args.c1, d1=args.d1, c2=args.c2, d2=args.d2),
        features=FireplaceFeatures(),
    )
    state = _build_state(args)
    frame = encode_state(state, profile)
    tx_plan = build_transmission_plan(frame)

    print(
        "State: "
        f"power={'on' if state.power else 'off'} "
        f"flame={state.flame} fan={state.fan} light={state.light} "
        f"front={state.front} aux={state.aux} cpi={state.cpi}"
    )
    print(f"Id: 0x{profile.serial_id:06X}")
    print(f"Cmd1/Err1: 0x{frame.cmd1:02X} / 0x{frame.err1:02X}")
    print(f"Cmd2/Err2: 0x{frame.cmd2:02X} / 0x{frame.err2:02X}")
    print(
        "Logical frame: "
        f"ProflameFrame(serial_id=0x{frame.serial_id:06X}, "
        f"cmd1=0x{frame.cmd1:02X}, err1=0x{frame.err1:02X}, "
        f"cmd2=0x{frame.cmd2:02X}, err2=0x{frame.err2:02X})"
    )
    print(f"Logical payload bytes: {frame.as_bytes().hex()}")
    print(f"SmartFire symbol string: {tx_plan.symbol_string}")
    print(f"RF air payload bytes: {tx_plan.air_payload.hex()}")
    print(
        "Repeat behavior: "
        f"total_transmissions={tx_plan.repeat_count} "
        f"backend_repeat_argument={tx_plan.backend_repeat_argument}"
    )
    if tx_plan.notes:
        print("RF TODOs:")
        for note in tx_plan.notes:
            print(f"- {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
