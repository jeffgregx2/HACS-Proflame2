"""CLI for building alignment workspaces from capture sessions.

This decision-gating command preserves semantic labels and source artifacts for
offline comparison while keeping debug/raw captures separate from semantic
interpretation.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from tools.proflame_capture_analysis.alignment_workspace import build_alignment_workspace


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build Stage 5C alignment workspaces for capture sessions.")
    parser.add_argument("session_dir", help="Path to the captured session directory.")
    parser.add_argument("--comparison-report", help="Optional precomputed comparison report path.")
    parser.add_argument("--output-dir", help="Workspace output directory. Defaults to SESSION_DIR/alignment_workspace.")
    parser.add_argument("--include-partial", action="store_true", help="Include PARTIAL readiness samples.")
    parser.add_argument("--include-invalid", action="store_true", help="Include invalid samples.")
    parser.add_argument("--sample-id", action="append", dest="sample_ids", help="Restrict to specific sample ids.")
    parser.add_argument("--expected-id", default="3b3f02", help="Expected rtl_433 id for comparison generation.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    manifest = build_alignment_workspace(
        args.session_dir,
        comparison_report_path=args.comparison_report,
        output_dir=args.output_dir,
        include_partial=args.include_partial,
        include_invalid=args.include_invalid,
        sample_ids=args.sample_ids,
        expected_id=args.expected_id,
    )
    print(f"Workspace: {Path(args.output_dir) if args.output_dir else Path(args.session_dir) / 'alignment_workspace'}")
    print(f"Selected samples: {len(manifest['selected_sample_ids'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
