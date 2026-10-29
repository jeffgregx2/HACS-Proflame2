"""Record an explicit operator observation without rewriting captured evidence."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from tools.waveform_archive import inspect_directory, metadata, safe_name
from tools.waveform_collect import artifact, write_json
from tools.waveform_manifest import validate_manifest

OUTCOMES = ("before_retry", "after_retry", "no_response", "ambiguous", "unobserved")


def record_observation(
    root: Path,
    trial_id: str,
    output: Path,
    *,
    operator_state: dict | None,
    outcome: str = "unobserved",
    notes: str = "",
) -> dict:
    root = root.resolve()
    session = inspect_directory(root)
    safe_name(trial_id)
    if "/" in trial_id:
        raise ValueError("trial ID must be a single path component")
    source = root / trial_id / "trial.json"
    if not source.is_file():
        raise ValueError("trial artifact missing")
    trial = metadata(source.read_bytes())
    if trial["trial_id"] != trial_id:
        raise ValueError("trial ID mismatch")
    # Reuse the state schema for validation only: operator state is not decoded RF.
    validate_manifest("trial", {**trial, "decoded_state": operator_state, "outcome": outcome})
    if len(notes) > 4096:
        raise ValueError("observation notes exceed limit")
    if output.resolve().is_relative_to(root):
        raise ValueError("observations must be separate files outside the original session")
    observation = {
        "schema_version": 1,
        "kind": "operator_observation",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "session_id": session["session_id"],
        "trial_id": trial_id,
        "source_session_sha256": artifact(root / "session.json", root)["sha256"],
        "source_trial_sha256": artifact(source, root)["sha256"],
        "operator_state": operator_state,
        "physical_outcome": outcome,
        "notes": notes,
        "rf_association": None,
    }
    write_json(output, observation)
    return observation


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("trial_id")
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--operator-state", type=json.loads, default=None, help="actual selection, for example '{\"light\":5}'"
    )
    parser.add_argument("--outcome", choices=OUTCOMES, default="unobserved")
    parser.add_argument("--notes", default="")
    args = parser.parse_args()
    record_observation(
        args.session,
        args.trial_id,
        args.output,
        operator_state=args.operator_state,
        outcome=args.outcome,
        notes=args.notes,
    )
    print("Operator observation saved separately; original captures unchanged. No RF association inferred.")


if __name__ == "__main__":
    main()
