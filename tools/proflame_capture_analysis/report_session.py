"""CLI for offline multi-source capture session reports.

This decision-gating command summarizes capture readiness and artifact
availability without treating raw/debug source artifacts as semantic packet
evidence.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from tools.proflame_capture_analysis.session_report import (
    build_session_report,
    load_session_report_input,
    write_session_report,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate an offline multi-source session report.")
    parser.add_argument("session_dir", help="Path to the captured session directory.")
    parser.add_argument(
        "--output-dir",
        help="Directory to write report artifacts to. Defaults to SESSION_DIR/analysis_report.",
    )
    parser.add_argument(
        "--include-invalid",
        action="store_true",
        help="Include invalid samples in the report output.",
    )
    parser.add_argument("--json-only", action="store_true", help="Write only JSON output.")
    parser.add_argument("--markdown-only", action="store_true", help="Write only Markdown output.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.json_only and args.markdown_only:
        parser.error("--json-only and --markdown-only cannot be used together")

    loaded = load_session_report_input(args.session_dir)
    report = build_session_report(loaded, include_invalid=args.include_invalid)
    output_dir = Path(args.output_dir) if args.output_dir else Path(args.session_dir) / "analysis_report"
    written = write_session_report(
        report,
        output_dir=output_dir,
        json_only=args.json_only,
        markdown_only=args.markdown_only,
    )

    print(f"Session: {loaded.session_dir}")
    print(f"Samples included: {report['total_sample_count']} / {report['all_sample_count']}")
    for kind, path in written.items():
        print(f"{kind}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
