"""CLI entry point for coordinated Proflame2 capture sessions."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.proflame_capture.composition import (
    build_collector_plan,
    render_plan_lines,
    summarize_plan,
)
from tools.proflame_capture.models import CaptureCommand, CaptureSessionConfig, FireplaceState
from tools.proflame_capture.rtl433_collector import Rtl433OwnershipError
from tools.proflame_capture.runner import CaptureSessionRunner

DEFAULT_COMMANDS = (
    CaptureCommand.POWER_TOGGLE,
    CaptureCommand.FLAME_UP,
    CaptureCommand.FLAME_DOWN,
    CaptureCommand.FAN_UP,
    CaptureCommand.FAN_DOWN,
)
CAPTURE_COMMANDS = tuple(command for command in CaptureCommand if command != CaptureCommand.SETUP_STATE)

STATE_TARGET_FIELDS = ("power", "flame", "fan")
EXACT_TARGET_FIELDS = ("power", "flame", "fan", "cmd1", "cmd2", "err1", "err2")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one coordinated Proflame2 capture session.")
    parser.add_argument("--output-root", default="analysis/captures", help="Root directory for session output.")
    parser.add_argument("--valid-samples-target", type=int, default=10, help="Number of valid samples to collect.")
    parser.add_argument("--max-attempts", type=int, default=20, help="Maximum number of sample attempts.")
    parser.add_argument(
        "--command",
        action="append",
        choices=[command.value for command in CAPTURE_COMMANDS],
        default=[],
        help="Repeatable allowed capture command.",
    )
    parser.add_argument(
        "--command-sequence",
        action="append",
        choices=[command.value for command in CAPTURE_COMMANDS],
        default=[],
        help="Repeatable exact valid-sample action sequence. Invalid attempts retry the current planned action.",
    )
    parser.add_argument(
        "--semantic-state-target",
        action="append",
        default=[],
        help="Repeatable semantic state target, for example power=1,flame=2,fan=2.",
    )
    parser.add_argument(
        "--semantic-target",
        action="append",
        default=[],
        help="Repeatable exact semantic target, for example power=1,flame=2,fan=2,cmd1=01,cmd2=22,err1=76,err2=38.",
    )
    parser.add_argument(
        "--semantic-target-fields",
        default=",".join(EXACT_TARGET_FIELDS),
        help="Comma-separated fields for --semantic-target matching.",
    )
    parser.add_argument(
        "--replicates-per-target", type=int, default=0, help="Required accepted replicates per semantic target."
    )
    parser.add_argument(
        "--semantic-expected-id", default="3b3f02", help="Expected rtl_433 id for semantic-target acceptance."
    )
    parser.add_argument("--initial-power", type=int, choices=(0, 1), default=None, help=argparse.SUPPRESS)
    parser.add_argument("--initial-flame", type=int, choices=tuple(range(0, 7)), default=None, help=argparse.SUPPRESS)
    parser.add_argument("--initial-fan", type=int, choices=tuple(range(0, 7)), default=None, help=argparse.SUPPRESS)
    parser.add_argument(
        "--prearm-timeout-seconds",
        type=float,
        default=30.0,
        help="Maximum wait time for the LilyGO capture-arm marker before each interactive sample.",
    )
    parser.add_argument("--sample-timeout-seconds", type=float, default=6.0, help="Maximum wait time per sample.")
    parser.add_argument(
        "--poll-interval-seconds", type=float, default=0.01, help="Collector poll interval while waiting."
    )
    parser.add_argument("--non-interactive", action="store_true", help="Suppress blocking operator prompts.")
    parser.add_argument("--stub-sources", action="store_true", help="Use only stub collectors.")
    parser.add_argument(
        "--dry-run", action="store_true", help="Skip collector wait loops and finish samples immediately."
    )
    parser.add_argument("--lilygo-syslog", action="store_true", help="Use the real LilyGO UDP syslog collector.")
    parser.add_argument("--lilygo-syslog-host", default="0.0.0.0", help="UDP bind host for LilyGO syslog.")
    parser.add_argument("--lilygo-syslog-port", type=int, default=5514, help="UDP bind port for LilyGO syslog.")
    parser.add_argument("--lilygo-source-host", default=None, help="Optional expected LilyGO source host filter.")
    parser.add_argument(
        "--lilygo-capture-flow",
        choices=("fifo_rolling_complete",),
        default="fifo_rolling_complete",
        help="Operator flow for LilyGO syslog capture. FIFO semantic rolling capture is the active live mode.",
    )
    parser.add_argument(
        "--setup-state-sample",
        dest="setup_state_sample",
        action="store_true",
        default=None,
        help="Collect a setup-only sample first to establish rtl_433 canonical state.",
    )
    parser.add_argument(
        "--no-setup-state-sample",
        dest="setup_state_sample",
        action="store_false",
        help="Disable the default setup-only state sample.",
    )
    parser.add_argument("--rtl433", action="store_true", help="Use the real rtl_433 subprocess collector.")
    parser.add_argument("--rtl433-path", default="/usr/local/bin/rtl_433", help="Path to rtl_433 executable.")
    parser.add_argument("--rtl433-frequency", default="315M", help="rtl_433 frequency.")
    parser.add_argument("--rtl433-gain", default="40", help="rtl_433 gain.")
    parser.add_argument("--rtl433-protocol", default="207", help="rtl_433 protocol id.")
    parser.add_argument("--rtl433-extra-arg", action="append", default=[], help="Repeatable extra rtl_433 arg.")
    parser.add_argument("--yardstick", action="store_true", help="Use the YardStick diagnostic collector.")
    parser.add_argument(
        "--yardstick-mode", choices=("stub", "injected", "live"), default="live", help="YardStick collector mode."
    )
    return parser


def _parse_semantic_target(value: str) -> dict[str, object]:
    target: dict[str, object] = {}
    for raw_part in value.split(","):
        part = raw_part.strip()
        if not part:
            continue
        if "=" not in part:
            raise argparse.ArgumentTypeError(f"Invalid semantic target part {part!r}; expected key=value.")
        key, raw_value = part.split("=", 1)
        key = key.strip()
        parsed: object = raw_value.strip()
        if isinstance(parsed, str) and parsed.isdecimal():
            parsed = int(parsed)
        if not key:
            raise argparse.ArgumentTypeError("Semantic target field name cannot be empty.")
        target[key] = parsed
    if not target:
        raise argparse.ArgumentTypeError("Semantic target cannot be empty.")
    return target


def _semantic_target_config(args: argparse.Namespace) -> tuple[tuple[dict[str, object], ...], tuple[str, ...]]:
    if args.semantic_state_target and args.semantic_target:
        raise SystemExit("Use either --semantic-state-target or --semantic-target, not both.")
    if args.semantic_state_target:
        targets = tuple(_parse_semantic_target(value) for value in args.semantic_state_target)
        if any(any(field not in target for field in STATE_TARGET_FIELDS) for target in targets):
            raise SystemExit("--semantic-state-target requires power, flame, and fan fields.")
        return targets, STATE_TARGET_FIELDS
    if args.semantic_target:
        fields = tuple(field.strip() for field in args.semantic_target_fields.split(",") if field.strip())
        if not fields:
            raise SystemExit("--semantic-target-fields cannot be empty.")
        targets = tuple(_parse_semantic_target(value) for value in args.semantic_target)
        if any(any(field not in target for field in fields) for target in targets):
            raise SystemExit(f"--semantic-target entries must include fields: {', '.join(fields)}")
        return targets, fields
    return (), ()


def _interactive_prompt(prompt: str) -> None:
    print(prompt)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if not args.stub_sources and not args.lilygo_syslog and not args.rtl433 and not args.yardstick:
        raise SystemExit(
            "Select at least one collector source, such as --stub-sources, --lilygo-syslog, --rtl433, or --yardstick."
        )

    semantic_targets, semantic_target_fields = _semantic_target_config(args)
    if any(value is not None for value in (args.initial_power, args.initial_flame, args.initial_fan)):
        raise SystemExit(
            "Initial fireplace state must not be specified. "
            "rtl_433 decoded packets are the canonical state source during collection."
        )
    setup_state_sample = args.setup_state_sample
    if setup_state_sample is None:
        setup_state_sample = not args.stub_sources and not args.non_interactive
    config = CaptureSessionConfig(
        output_root=Path(args.output_root),
        valid_samples_target=args.valid_samples_target,
        max_attempts=args.max_attempts,
        commands=tuple(CaptureCommand(value) for value in args.command) if args.command else DEFAULT_COMMANDS,
        command_plan=tuple(CaptureCommand(value) for value in args.command_sequence),
        semantic_targets=semantic_targets,
        semantic_target_fields=semantic_target_fields,
        semantic_replicates_per_target=args.replicates_per_target,
        semantic_expected_id=args.semantic_expected_id if semantic_targets else None,
        initial_state=FireplaceState(),
        prearm_timeout_seconds=args.prearm_timeout_seconds,
        sample_timeout_seconds=args.sample_timeout_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
        lilygo_capture_flow=args.lilygo_capture_flow,
        setup_state_sample=setup_state_sample,
        non_interactive=args.non_interactive,
        dry_run=args.dry_run,
    )
    plan = build_collector_plan(args)
    collectors = [entry.collector for entry in plan]
    for line in render_plan_lines(args, plan):
        print(line)
    print(f"Operator prompt file: {Path(args.output_root) / 'operator_prompt_latest.txt'}")
    print(f"Operator status file: {Path(args.output_root) / 'operator_status_latest.json'}")

    prompt_handler = print if args.non_interactive else _interactive_prompt
    runner = CaptureSessionRunner(
        config=config,
        collectors=collectors,
        prompt_handler=prompt_handler,
        status_handler=print,
        session_metadata=summarize_plan(plan),
    )
    try:
        try:
            summary = runner.run()
        except Rtl433OwnershipError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(summary)
        return 0
    finally:
        for collector in collectors:
            close = getattr(collector, "close", None)
            if callable(close):
                close()


if __name__ == "__main__":
    raise SystemExit(main())
