"""Standalone live monitor for LilyGO ESPHome UDP syslog traffic."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.proflame_capture.lilygo_syslog import LilyGoSyslogCollector
from tools.proflame_capture.models import CaptureCommand, SampleIdentity, utc_now


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
    parser = argparse.ArgumentParser(description="Monitor live LilyGO UDP syslog traffic.")
    parser.add_argument("--host", default="0.0.0.0", help="UDP bind host.")
    parser.add_argument("--port", type=int, default=5514, help="UDP bind port.")
    parser.add_argument("--source-host", default=None, help="Optional expected LilyGO source host filter.")
    parser.add_argument("--duration", type=float, default=30.0, help="Maximum monitor duration in seconds.")
    parser.add_argument("--poll-interval", type=float, default=0.05, help="Polling sleep interval in seconds.")
    parser.add_argument(
        "--sample-dir",
        default=None,
        help="Optional sample directory to write collector artifacts into after the run.",
    )
    parser.add_argument(
        "--stop-on-complete",
        action="store_true",
        help="Exit early once a complete export has been observed.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    collector = LilyGoSyslogCollector(
        bind_host=args.host,
        bind_port=args.port,
        source_host_filter=args.source_host,
    )

    sample_dir = Path(args.sample_dir) if args.sample_dir else None
    session_context = _MonitorSessionContext(sample_dir or Path.cwd())
    sample_context = _MonitorSampleContext(sample_dir or Path.cwd())

    collector.start_session(session_context)
    collector.start_sample(sample_context)

    print(
        f"Listening on {args.host}:{args.port} for up to {args.duration:.1f}s"
        + (f" from {args.source_host}" if args.source_host else "")
    )

    last_seen = 0
    deadline = time.monotonic() + args.duration
    try:
        while time.monotonic() < deadline:
            collector.poll()
            state = collector.get_live_status()
            raw_line_count = int(state["raw_line_count"])
            if raw_line_count != last_seen:
                debug_payload = collector._build_parser_debug_payload()  # narrow debug use for monitor only
                for entry in debug_payload["raw_lines"][last_seen:]:
                    prefix = f"{entry['host_received_at_utc']} {entry['source_host']}:{entry['source_port']}"
                    print(f"{prefix} {entry['line']}")
                last_seen = raw_line_count
            if args.stop_on_complete and collector.is_complete():
                break
            time.sleep(args.poll_interval)
    finally:
        result = collector.finalize_sample(sample_context)
        if sample_dir is not None:
            print(f"Artifacts written to {sample_dir / 'lilygo'}")
        print(json.dumps(result.metadata, indent=2, sort_keys=True))
        collector.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
