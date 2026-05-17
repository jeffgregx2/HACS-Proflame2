"""CLI for LilyGO FIFO semantic replicate stability reports.

This decision-gating command accepts only semantic FIFO/YardStick artifacts for
replicate comparison; raw FIFO windows and debug diagnostics remain supporting
context only.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from tools.proflame_capture_analysis.fifo_semantic_replicate_stability import (
    build_fifo_semantic_replicate_stability_report,
    write_fifo_semantic_replicate_stability_report,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze LilyGO FIFO semantic replicate stability.")
    parser.add_argument("input_dir", help="Alignment workspace or session directory.")
    parser.add_argument("--output-dir", help="Output directory for reports.")
    parser.add_argument("--expected-id", default="3b3f02", help="Expected rtl_433 remote id.")
    parser.add_argument("--min-group-size", type=int, default=2, help="Minimum exact semantic group size.")
    parser.add_argument("--similarity-threshold", type=float, default=0.95, help="Minimum per-group similarity gate.")
    parser.add_argument("--json-only", action="store_true", help="Write only JSON report.")
    parser.add_argument("--markdown-only", action="store_true", help="Write only Markdown report.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir) if args.output_dir else input_dir / "fifo_semantic_replicate_stability"
    report = build_fifo_semantic_replicate_stability_report(
        input_dir,
        expected_id=args.expected_id,
        min_group_size=args.min_group_size,
        similarity_threshold=args.similarity_threshold,
    )
    written = write_fifo_semantic_replicate_stability_report(
        report,
        output_dir=output_dir,
        json_only=args.json_only,
        markdown_only=args.markdown_only,
    )
    print(f"Input: {input_dir}")
    print(f"Samples analyzed: {report['samples_analyzed']}")
    print(f"Repeated exact groups: {report['repeated_group_count']}")
    print(f"Pass gate: {report['pass_gate']['passed']}")
    print(f"Recommendation: {report['pass_gate']['recommendation']}")
    for kind, path in written.items():
        print(f"{kind}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
