from __future__ import annotations

import json
from pathlib import Path

from tools.proflame_capture_analysis.mark_invalidated_analysis import (
    find_invalidated_dirs,
    write_invalidation_markers,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_dry_run_does_not_write_markers(tmp_path: Path) -> None:
    target = tmp_path / "analysis" / "live" / "session" / "alignment_workspace" / "mapping_inference"
    target.mkdir(parents=True)

    result = write_invalidation_markers(tmp_path / "analysis" / "live", reason="test", dry_run=True)

    assert str(target) in result["invalidated_dirs"]
    assert not (target / "INVALIDATED_BY_STAGE5Q.md").exists()
    assert not (tmp_path / "analysis" / "live" / "INVALIDATED_ANALYSIS_INDEX.md").exists()


def test_apply_writes_markers_and_index_without_touching_raw_artifacts(tmp_path: Path) -> None:
    root = tmp_path / "analysis" / "live"
    target = root / "session" / "alignment_workspace" / "word_grid_alignment"
    raw = root / "session" / "sample" / "yardstick" / "diagnostic.json"
    target.mkdir(parents=True)
    _write_json(raw, {"raw": True})

    write_invalidation_markers(root, reason="test", dry_run=False)

    assert (target / "INVALIDATED_BY_STAGE5Q.md").exists()
    assert (root / "INVALIDATED_ANALYSIS_INDEX.md").exists()
    assert json.loads(raw.read_text(encoding="utf-8")) == {"raw": True}


def test_new_candidate_window_replicate_report_is_not_invalidated(tmp_path: Path) -> None:
    old_dir = tmp_path / "live" / "old" / "replicate_stability"
    new_dir = tmp_path / "live" / "new" / "replicate_stability"
    _write_json(old_dir / "replicate_stability_report.json", {"session_conclusion": {}})
    _write_json(
        new_dir / "replicate_stability_report.json",
        {"yardstick_comparison_policy": {"whole_symbol_stream_used": False}},
    )

    invalidated = find_invalidated_dirs(tmp_path / "live")

    assert old_dir in invalidated
    assert new_dir not in invalidated
