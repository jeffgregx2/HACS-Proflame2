"""Safely transmit one Proflame2 packet with a Yard Stick One."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from custom_components.proflame2.protocol.ecc import err1_for, err2_for
from custom_components.proflame2.protocol.encoder import encode_packet
from custom_components.proflame2.protocol.models import (
    ECCProfile,
    FireplaceFeatures,
    FireplaceState,
    RemoteProfile,
)
from custom_components.proflame2.protocol.packet import ProflameFrame, ProflamePacket
from custom_components.proflame2.rf.waveform import build_transmission_plan
from custom_components.proflame2.rf.yardstick import (
    PROFLAME2_DATA_RATE,
    PROFLAME2_FREQUENCY_HZ,
    YARDSTICK_TX_DEFAULT_INTER_FRAME_GAP_MS,
    YARDSTICK_TX_DEFAULT_REPEAT_STRATEGY,
    YARDSTICK_TX_DEFAULT_TRANSMISSIONS,
    YARDSTICK_TX_REPEAT_STRATEGIES,
    YardStickBackend,
    YardStickBackendUnavailableError,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Transmit one Proflame2 packet through a Yard Stick One.")
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
    parser.add_argument("--device-index", type=int, default=0, help="RfCat device index to open.")
    parser.add_argument(
        "--tx-frequency",
        type=int,
        default=PROFLAME2_FREQUENCY_HZ,
        help=f"Transmit frequency in Hz. Defaults to {PROFLAME2_FREQUENCY_HZ}.",
    )
    parser.add_argument(
        "--transmissions",
        type=int,
        default=YARDSTICK_TX_DEFAULT_TRANSMISSIONS,
        help=(
            "Number of logical repeats embedded in one RFxmit payload. "
            f"Defaults to {YARDSTICK_TX_DEFAULT_TRANSMISSIONS} to mirror the stock remote burst."
        ),
    )
    parser.add_argument(
        "--inter-frame-gap-ms",
        type=float,
        default=YARDSTICK_TX_DEFAULT_INTER_FRAME_GAP_MS,
        help="Optional embedded repeat gap in milliseconds. Defaults to the native Proflame2 gap.",
    )
    parser.add_argument(
        "--repeat-strategy",
        choices=YARDSTICK_TX_REPEAT_STRATEGIES,
        default=YARDSTICK_TX_DEFAULT_REPEAT_STRATEGY,
        help="Yard Stick rfcat repeat implementation strategy. This is a bench/debug knob, not a HA UI option.",
    )
    parser.add_argument(
        "--allow-off-flame",
        "--preserve-off-flame",
        dest="preserve_off_flame",
        action="store_true",
        help="Allow --power off --flame N and preserve the off-state flame bits for this CLI test.",
    )
    parser.add_argument(
        "--no-close",
        dest="no_close",
        action="store_true",
        default=True,
        help="Skip explicit backend close after TX to avoid standalone rflib teardown issues. This is the default.",
    )
    parser.add_argument(
        "--close",
        dest="no_close",
        action="store_false",
        help="Attempt explicit backend cleanup on exit.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive confirmation prompt and transmit immediately.",
    )
    return parser


def _build_state(args: argparse.Namespace) -> FireplaceState:
    flame = (
        args.flame if (args.power == "off" and args.preserve_off_flame) else (0 if args.power == "off" else args.flame)
    )
    return FireplaceState(
        power=(args.power == "on"),
        flame=flame,
        fan=args.fan,
        light=args.light,
        front=args.front,
        aux=args.aux,
        cpi=args.cpi,
    )


def _confirm_send(args: argparse.Namespace) -> bool:
    if args.yes:
        return True
    response = input("Transmit this packet with the Yard Stick One? Type 'yes' to continue: ")
    return response.strip().lower() == "yes"


def _build_profile(args: argparse.Namespace) -> RemoteProfile:
    return RemoteProfile(
        serial_id=int(args.id, 16),
        ecc=ECCProfile(c1=args.c1, d1=args.d1, c2=args.c2, d2=args.d2),
        features=FireplaceFeatures(),
    )


def _build_packet_for_cli(
    args: argparse.Namespace, profile: RemoteProfile
) -> tuple[FireplaceState, FireplaceState, ProflamePacket]:
    """Build the requested/effective TX packet for the standalone Yard Stick CLI."""

    requested_state = _build_state(args)
    if requested_state.power or not args.preserve_off_flame:
        packet = encode_packet(requested_state, profile, source="yardstick_send_test")
        effective_state = packet.state
    else:
        effective_state = requested_state
        frame = ProflameFrame(
            serial_id=profile.serial_id,
            cmd1=0x00,
            err1=err1_for(0x00, profile.ecc),
            cmd2=(requested_state.flame & 0x07)
            | ((1 if requested_state.aux else 0) << 3)
            | ((requested_state.fan & 0x07) << 4)
            | ((1 if requested_state.front else 0) << 7),
            err2=err2_for(
                (requested_state.flame & 0x07)
                | ((1 if requested_state.aux else 0) << 3)
                | ((requested_state.fan & 0x07) << 4)
                | ((1 if requested_state.front else 0) << 7),
                profile.ecc,
            ),
        )
        packet = ProflamePacket(
            remote_id=profile.serial_id,
            state=effective_state,
            frame=frame,
            source="yardstick_send_test",
        )

    packet.transmission_plan = build_transmission_plan(packet.frame)
    return requested_state, effective_state, packet


def _print_tx_preview(
    requested_state: FireplaceState,
    effective_state: FireplaceState,
    packet: ProflamePacket,
    *,
    tx_frequency_hz: int,
    transmissions: int,
    inter_frame_gap_ms: float,
    repeat_strategy: str,
    preserve_off_flame: bool,
    no_close: bool,
) -> None:
    plan = packet.transmission_plan
    assert plan is not None

    print(
        "Requested state: "
        f"power={'on' if requested_state.power else 'off'} "
        f"flame={requested_state.flame} fan={requested_state.fan} light={requested_state.light} "
        f"front={requested_state.front} aux={requested_state.aux} cpi={requested_state.cpi}"
    )
    print(
        "Effective encoded state: "
        f"power={'on' if effective_state.power else 'off'} "
        f"flame={effective_state.flame} fan={effective_state.fan} light={effective_state.light} "
        f"front={effective_state.front} aux={effective_state.aux} cpi={effective_state.cpi}"
    )
    print(f"Remote ID: 0x{packet.remote_id:06X}")
    print(f"Cmd1/Err1: 0x{packet.frame.cmd1:02X} / 0x{packet.frame.err1:02X}")
    print(f"Cmd2/Err2: 0x{packet.frame.cmd2:02X} / 0x{packet.frame.err2:02X}")
    print(f"Air payload hex: {plan.air_payload.hex()}")
    print(f"Plan total transmissions: {plan.repeat_count}")
    print(f"Transmission mode: {repeat_strategy}")
    print("Effective burst settings: " f"logical_repeats={transmissions} " f"repeat_gap_ms={inter_frame_gap_ms}")
    print(
        "TX settings: " f"frequency_hz={tx_frequency_hz} " f"modulation=MOD_ASK_OOK " f"data_rate={PROFLAME2_DATA_RATE}"
    )
    print(f"Preserve off flame: {preserve_off_flame}")
    print(f"Skip close after TX: {no_close}")
    if no_close:
        print("Warning: rflib does not expose a reliable close operation; skipping close is intentional.")


async def _run(args: argparse.Namespace) -> int:
    profile = _build_profile(args)
    requested_state, effective_state, packet = _build_packet_for_cli(args, profile)
    _print_tx_preview(
        requested_state,
        effective_state,
        packet,
        tx_frequency_hz=args.tx_frequency,
        transmissions=args.transmissions,
        inter_frame_gap_ms=args.inter_frame_gap_ms,
        repeat_strategy=args.repeat_strategy,
        preserve_off_flame=args.preserve_off_flame,
        no_close=args.no_close,
    )

    if not _confirm_send(args):
        print("Transmit cancelled.")
        return 1

    backend = YardStickBackend(
        device_index=args.device_index,
        tx_frequency_hz=args.tx_frequency,
        tx_transmissions=args.transmissions,
        tx_inter_frame_gap_ms=args.inter_frame_gap_ms,
        tx_repeat_strategy=args.repeat_strategy,
    )
    try:
        result = await backend.send(packet)
    except YardStickBackendUnavailableError as exc:
        print(exc)
        return 2
    except RuntimeError as exc:
        print(exc)
        return 3
    finally:
        if not args.no_close:
            await backend.close()

    print("Transmit complete: " f"backend={result.backend_name} " f"remote=0x{result.packet.remote_id:06X}")
    return 0


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
