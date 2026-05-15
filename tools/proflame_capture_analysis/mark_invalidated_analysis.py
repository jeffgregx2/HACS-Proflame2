"""Mark historical analysis outputs invalidated by later artifact policy.

This maintenance tool is historical, not semantic evidence generation. It
preserves old outputs while warning that whole-stream/debug artifacts are
unsafe for packet-owned semantic interpretation.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

INVALIDATED_DIR_NAMES = {
    "mapping_inference",
    "boundary_alignment",
    "word_grid_alignment",
    "later_word_transform",
    "region_cluster_refinement",
    "feature_trajectory",
    "hypothesis_comparison",
}


MARKER_TEXT = """# Invalidated By Stage 5Q

This generated analysis output is preserved for tooling/debug history only.

Stage 5Q proved that YardStick `symbol_stream` is the full fixed-length RFrecv
buffer converted at bit_offset=0, not a packet-normalized stream. Any conclusion
that treated whole YardStick `symbol_stream` as a packet/comparable transport
sequence is not valid protocol-mapping evidence.

Re-run this analysis using YardStick candidate windows after Stage 5R.
"""


def _is_old_replicate_stability(path: Path) -> bool:
    if path.name != "replicate_stability":
        return False
    report_path = path / "replicate_stability_report.json"
    if not report_path.exists():
        return True
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return True
    policy = report.get("yardstick_comparison_policy")
    return not policy or policy.get("whole_symbol_stream_used") is not False


def find_invalidated_dirs(root: str | Path) -> list[Path]:
    root_path = Path(root)
    candidates: list[Path] = []
    for path in root_path.rglob("*"):
        if not path.is_dir():
            continue
        if path.name in INVALIDATED_DIR_NAMES or _is_old_replicate_stability(path):
            candidates.append(path)
    return sorted(candidates)


def _marker_content(path: Path, reason: str) -> str:
    return MARKER_TEXT + "\n" + f"- Reason: `{reason}`\n" + f"- Marked directory: `{path}`\n"


def write_invalidation_markers(root: str | Path, *, reason: str, dry_run: bool) -> dict[str, object]:
    root_path = Path(root)
    invalidated = find_invalidated_dirs(root_path)
    marker_paths = [path / "INVALIDATED_BY_STAGE5Q.md" for path in invalidated]
    if not dry_run:
        for path, marker_path in zip(invalidated, marker_paths, strict=False):
            marker_path.write_text(_marker_content(path, reason), encoding="utf-8")
        index_path = root_path / "INVALIDATED_ANALYSIS_INDEX.md"
        index_path.write_text(render_invalidation_index(root_path, invalidated, reason=reason), encoding="utf-8")
    return {
        "root": str(root_path),
        "dry_run": dry_run,
        "reason": reason,
        "invalidated_dirs": [str(path) for path in invalidated],
        "marker_paths": [str(path) for path in marker_paths],
        "index_path": str(root_path / "INVALIDATED_ANALYSIS_INDEX.md"),
    }


def render_invalidation_index(root: Path, invalidated: list[Path], *, reason: str) -> str:
    lines = [
        "# Stage 5Q Invalidated Analysis Index",
        "",
        f"- Root: `{root}`",
        f"- Reason: `{reason}`",
        "",
        "## Invalidated Generated Analysis",
        "",
    ]
    for path in invalidated:
        lines.append(f"- `{path}`")
    if not invalidated:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Still Valid",
            "",
            "- Raw live capture data and source artifacts under `lilygo/`, `rtl433/`, and `yardstick/` remain preserved.",
            "- `session_manifest.json`, `sample_manifest.json`, `quick_validation.json`, and `run_summary.json` remain valid acquisition records.",
            "- Stage 5A session summaries remain valid as artifact summaries.",
            "- Stage 5B readiness remains valid only as artifact-readiness, not transform-readiness.",
            "- YardStick diagnostic audit reports from Stage 5P/5Q remain valid.",
            "- Stage 5Q candidate-window probe/check outputs remain valid.",
            "",
            "## Regenerate",
            "",
            "- Mapping, boundary, word-grid, later-word, region-cluster, feature-trajectory, and hypothesis comparison reports must be regenerated using YardStick candidate windows.",
            "- Legacy replicate-stability reports based on whole YardStick streams must be regenerated with Stage 5R candidate-window logic.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mark invalidated Stage 5 analysis outputs without deleting raw data.")
    parser.add_argument("--root", default="analysis/live", help="Root directory to scan.")
    parser.add_argument(
        "--reason", default="stage5q_yardstick_whole_stream_not_packet_normalized", help="Invalidation reason."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Print directories that would be marked.")
    mode.add_argument("--apply", action="store_true", help="Write invalidation markers and index.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = write_invalidation_markers(args.root, reason=args.reason, dry_run=args.dry_run)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
