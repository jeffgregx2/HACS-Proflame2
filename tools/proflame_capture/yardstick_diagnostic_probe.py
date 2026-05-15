"""Receive-only YardStick diagnostic probe."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools.proflame_capture.yardstick_collector import (
    LiveYardStickDiagnosticSource,
    YardStickDiagnosticCollector,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one receive-only YardStick diagnostic probe.")
    parser.add_argument("--timeout-seconds", type=float, default=5.0, help="Receive window duration.")
    parser.add_argument("--output-dir", default=None, help="Optional directory for artifacts.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    source = LiveYardStickDiagnosticSource()
    collector = YardStickDiagnosticCollector(mode="live", source=source)
    session = type(
        "Session",
        (),
        {
            "session_id": "probe",
            "session_dir": Path("."),
            "config": type("Config", (), {"sample_timeout_seconds": args.timeout_seconds})(),
            "started_at_utc": "now",
        },
    )()
    sample_dir = Path(args.output_dir) if args.output_dir else Path("analysis/yardstick_probe")
    sample_dir.mkdir(parents=True, exist_ok=True)
    sample = type("Sample", (), {"sample_dir": sample_dir, "identity": None, "state_before": None})()

    collector.start_session(session)
    collector.start_sample(sample)
    while not collector.is_complete():
        collector.poll()
    result = collector.finalize_sample(sample)
    collector.close()
    print(
        json.dumps(
            {"reject_reason": result.reject_reason, "valid": result.valid, "metadata": result.metadata},
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if result.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
