"""Standalone live monitor for rtl_433 Proflame2 output."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.proflame_capture.models import CaptureCommand, SampleIdentity, utc_now
from tools.proflame_capture.rtl433_collector import Rtl433Collector


class _MonitorSampleContext:
    def __init__(self, sample_dir: Path) -> None:
        self.sample_dir = sample_dir
        self.identity = SampleIdentity(
            session_id="monitor",
            sample_id="monitor_sample_0001",
            sample_index=1,
            attempt_index=1,
            requested_action=CaptureCommand.POWER_TOGGLE,
            operator_prompt="monitor",
            coordinator_started_at_utc=utc_now().isoformat(),
        )
        self.state_before = None


class _MonitorSessionContext:
    def __init__(self, session_dir: Path) -> None:
        self.session_id = "monitor"
        self.session_dir = session_dir
        self.config = None
        self.started_at_utc = utc_now().isoformat()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Monitor live rtl_433 Proflame2 blocks.")
    parser.add_argument("--rtl433-path", default="/usr/local/bin/rtl_433", help="Path to rtl_433.")
    parser.add_argument("--frequency", default="315M", help="rtl_433 frequency.")
    parser.add_argument("--gain", default="40", help="rtl_433 gain.")
    parser.add_argument("--protocol", default="207", help="rtl_433 protocol id.")
    parser.add_argument("--extra-arg", action="append", default=[], help="Repeatable extra rtl_433 argument.")
    parser.add_argument("--duration", type=float, default=30.0, help="Monitor duration in seconds.")
    parser.add_argument("--sample-dir", default=None, help="Optional sample directory for collector artifacts.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    collector = Rtl433Collector(
        executable_path=args.rtl433_path,
        frequency=args.frequency,
        gain=args.gain,
        protocol=args.protocol,
        extra_args=args.extra_arg,
    )
    sample_dir = Path(args.sample_dir) if args.sample_dir else None
    session_context = _MonitorSessionContext(sample_dir or Path.cwd())
    sample_context = _MonitorSampleContext(sample_dir or Path.cwd())
    collector.start_session(session_context)
    collector.start_sample(sample_context)

    deadline = time.monotonic() + args.duration
    try:
        while time.monotonic() < deadline and not collector.is_complete():
            collector.poll()
            time.sleep(0.05)
    finally:
        result = collector.finalize_sample(sample_context)
        if sample_dir is not None:
            print(f"Artifacts written to {sample_dir / 'rtl433'}")
        print(json.dumps(result.metadata, indent=2, sort_keys=True))
        collector.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
