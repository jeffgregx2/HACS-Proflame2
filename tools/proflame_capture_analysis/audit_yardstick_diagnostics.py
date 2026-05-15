"""CLI for auditing YardStick diagnostic artifact provenance.

This decision-gating command identifies which YardStick artifacts are canonical
semantic evidence and marks candidate, failed, heuristic, and raw windows as
debug-only.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from tools.proflame_capture_analysis.yardstick_diagnostic_audit import (
    build_yardstick_diagnostic_audit_report,
    write_yardstick_diagnostic_audit_report,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit YardStick diagnostic artifacts for packet-window provenance.")
    parser.add_argument("input_dir", help="Capture session directory or Stage 5C alignment workspace directory.")
    parser.add_argument("--output-dir", help="Output directory for the audit report.")
    parser.add_argument("--json-only", action="store_true", help="Write only JSON output.")
    parser.add_argument("--markdown-only", action="store_true", help="Write only Markdown output.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_dir = Path(args.input_dir)
    report = build_yardstick_diagnostic_audit_report(input_dir)
    output_dir = Path(args.output_dir) if args.output_dir else input_dir / "yardstick_diagnostic_audit"
    written = write_yardstick_diagnostic_audit_report(
        report,
        output_dir=output_dir,
        json_only=args.json_only,
        markdown_only=args.markdown_only,
    )
    summary = report["summary"]
    print(f"Input: {input_dir}")
    print(f"Samples analyzed: {report['samples_analyzed']}")
    print(f"Candidate windows available: {summary['candidate_windows_available_count']}")
    print(f"Suitable for replicate comparison: {summary['suitable_for_replicate_comparison_count']}")
    print(f"Recommended future artifact: {summary['recommended_future_comparison_artifact']}")
    for key, path in written.items():
        print(f"{key}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
