"""CLI for cross-source capture readiness comparison reports.

This decision-gating command compares rtl_433, YardStick, and LilyGO artifact
availability while preserving the semantic/debug/raw artifact boundary.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from tools.proflame_capture_analysis.comparison import (
    build_session_comparison_report,
    write_session_comparison_report,
)
from tools.proflame_capture_analysis.session_report import (
    build_session_report,
    load_session_report_input,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a cross-source comparison readiness report.")
    parser.add_argument("session_dir", help="Path to the captured session directory.")
    parser.add_argument(
        "--output-dir",
        help="Directory to write report artifacts to. Defaults to SESSION_DIR/analysis_report.",
    )
    parser.add_argument("--expected-id", default="3b3f02", help="Expected Proflame2 remote id for rtl_433 comparisons.")
    parser.add_argument(
        "--include-invalid", action="store_true", help="Include invalid samples in the comparison report."
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
    session_report = build_session_report(loaded, include_invalid=args.include_invalid)
    comparison_report = build_session_comparison_report(session_report, expected_id=args.expected_id)
    output_dir = Path(args.output_dir) if args.output_dir else Path(args.session_dir) / "analysis_report"
    written = write_session_comparison_report(
        comparison_report,
        output_dir=output_dir,
        json_only=args.json_only,
        markdown_only=args.markdown_only,
    )
    print(f"Session: {loaded.session_dir}")
    print(
        "Readiness counts: "
        f"YES={comparison_report['comparison_ready_yes_count']} "
        f"PARTIAL={comparison_report['comparison_ready_partial_count']} "
        f"NO={comparison_report['comparison_ready_no_count']}"
    )
    for kind, path in written.items():
        print(f"{kind}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
