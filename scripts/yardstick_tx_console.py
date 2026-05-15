"""Long-lived interactive Proflame2 TX console for Yard Stick One bench tests."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from pathlib import Path
import shlex
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from custom_components.proflame2.protocol.models import FireplaceState, RemoteProfile
from custom_components.proflame2.rf.yardstick import YardStickBackend, YardStickBackendUnavailableError
from yardstick_send_test import _build_packet_for_cli, _build_parser as _build_send_parser, _build_profile, _confirm_send, _print_tx_preview


@dataclass
class TxConsoleSession:
    """Mutable state for one long-lived Yard Stick TX bench session."""

    profile: RemoteProfile
    backend: YardStickBackend
    tx_frequency_hz: int
    transmissions: int
    inter_frame_gap_ms: float
    yes: bool
    no_close: bool
    last_power: str = "on"
    last_flame: int = 1
    last_preserve_off_flame: bool = False


def _build_parser() -> argparse.ArgumentParser:
    parser = _build_send_parser()
    parser.description = "Interactive Proflame2 Yard Stick TX console."
    return parser


def _command_namespace(
    session: TxConsoleSession,
    *,
    power: str,
    flame: int,
    preserve_off_flame: bool,
) -> argparse.Namespace:
    return argparse.Namespace(
        id=f"{session.profile.serial_id:06x}",
        c1=session.profile.ecc.c1,
        d1=session.profile.ecc.d1,
        c2=session.profile.ecc.c2,
        d2=session.profile.ecc.d2,
        power=power,
        flame=flame,
        fan=0,
        light=0,
        front=False,
        aux=False,
        cpi=False,
        device_index=0,
        tx_frequency=session.tx_frequency_hz,
        transmissions=session.transmissions,
        inter_frame_gap_ms=session.inter_frame_gap_ms,
        preserve_off_flame=preserve_off_flame,
        no_close=session.no_close,
        yes=session.yes,
    )


def _print_status(session: TxConsoleSession) -> None:
    print(
        "Status: "
        f"remote_id=0x{session.profile.serial_id:06X} "
        f"tx_frequency_hz={session.tx_frequency_hz} "
        "mode=software_repeat "
        f"transmissions={session.transmissions} "
        f"inter_frame_gap_ms={session.inter_frame_gap_ms} "
        f"yes={session.yes} "
        f"no_close={session.no_close} "
        f"last_power={session.last_power} "
        f"last_flame={session.last_flame} "
        f"last_preserve_off_flame={session.last_preserve_off_flame}"
    )


def _print_help() -> None:
    print("Commands:")
    print("  on <flame>            Send Power On with a flame level (default 1 if omitted).")
    print("  off                   Send normalized Power Off (flame 0).")
    print("  off <flame>           Send remote-like Power Off preserving the provided flame bits.")
    print("  send                  Re-send the last command.")
    print("  freq <hz>             Set TX frequency override for later sends.")
    print("  transmissions <n>     Set explicit software burst frame count.")
    print("  gap <ms>              Set inter-frame software burst gap in milliseconds.")
    print("  status                Show current session settings.")
    print("  help                  Show this help.")
    print("  quit / exit           Leave the console.")


async def _send_current_state(session: TxConsoleSession) -> None:
    args = _command_namespace(
        session,
        power=session.last_power,
        flame=session.last_flame,
        preserve_off_flame=session.last_preserve_off_flame,
    )
    requested_state, effective_state, packet = _build_packet_for_cli(args, session.profile)
    _print_tx_preview(
        requested_state,
        effective_state,
        packet,
        tx_frequency_hz=session.tx_frequency_hz,
        transmissions=session.transmissions,
        inter_frame_gap_ms=session.inter_frame_gap_ms,
        preserve_off_flame=session.last_preserve_off_flame,
        no_close=session.no_close,
    )
    if not _confirm_send(args):
        print("Transmit cancelled.")
        return
    result = await session.backend.send(packet)
    print(
        "Transmit complete: "
        f"backend={result.backend_name} "
        f"remote=0x{result.packet.remote_id:06X}"
    )


async def _handle_command(session: TxConsoleSession, line: str) -> bool:
    """Handle one interactive command. Return False to exit."""

    tokens = shlex.split(line)
    if not tokens:
        return True

    command = tokens[0].lower()
    if command in {"quit", "exit"}:
        return False
    if command == "help":
        _print_help()
        return True
    if command == "status":
        _print_status(session)
        return True
    if command == "freq":
        if len(tokens) != 2:
            print("Usage: freq <hz>")
            return True
        session.tx_frequency_hz = int(tokens[1])
        session.backend._tx_frequency_hz = session.tx_frequency_hz
        print(f"TX frequency set to {session.tx_frequency_hz} Hz")
        return True
    if command == "transmissions":
        if len(tokens) != 2:
            print("Usage: transmissions <n>")
            return True
        session.transmissions = int(tokens[1])
        session.backend._tx_transmissions = session.transmissions
        print(f"Software transmissions set to {session.transmissions}")
        return True
    if command == "gap":
        if len(tokens) != 2:
            print("Usage: gap <ms>")
            return True
        session.inter_frame_gap_ms = float(tokens[1])
        session.backend._tx_inter_frame_gap_ms = session.inter_frame_gap_ms
        print(f"Inter-frame gap set to {session.inter_frame_gap_ms} ms")
        return True
    if command == "on":
        session.last_power = "on"
        session.last_flame = int(tokens[1]) if len(tokens) > 1 else 1
        session.last_preserve_off_flame = False
        await _send_current_state(session)
        return True
    if command == "off":
        session.last_power = "off"
        if len(tokens) > 1:
            session.last_flame = int(tokens[1])
            session.last_preserve_off_flame = True
        else:
            session.last_flame = 0
            session.last_preserve_off_flame = False
        await _send_current_state(session)
        return True
    if command == "send":
        await _send_current_state(session)
        return True

    print(f"Unknown command: {command}")
    _print_help()
    return True


async def _run(args: argparse.Namespace) -> int:
    profile = _build_profile(args)
    backend = YardStickBackend(
        device_index=args.device_index,
        tx_frequency_hz=args.tx_frequency,
        tx_transmissions=args.transmissions,
        tx_inter_frame_gap_ms=args.inter_frame_gap_ms,
    )
    session = TxConsoleSession(
        profile=profile,
        backend=backend,
        tx_frequency_hz=args.tx_frequency,
        transmissions=args.transmissions,
        inter_frame_gap_ms=args.inter_frame_gap_ms,
        yes=args.yes,
        no_close=args.no_close,
        last_power=args.power,
        last_flame=args.flame,
        last_preserve_off_flame=args.preserve_off_flame,
    )

    if args.no_close:
        print("Warning: rflib does not expose a reliable close operation; skipping close is intentional.")

    try:
        await backend.connect()
    except YardStickBackendUnavailableError as exc:
        print(exc)
        return 2

    print("Yard Stick TX console ready. Type 'help' for commands.")
    _print_status(session)
    try:
        while True:
            line = input("tx> ")
            if not await _handle_command(session, line):
                break
    finally:
        if not args.no_close:
            await backend.close()
    return 0


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
