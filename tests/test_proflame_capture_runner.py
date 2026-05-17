from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from tools.proflame_capture.collectors import (
    StubCollector,
    StubLilyGoCollector,
    StubRtl433Collector,
    StubYardStickCollector,
)
from tools.proflame_capture.models import CaptureCommand, CaptureSessionConfig, FireplaceState
from tools.proflame_capture.planner import ActionPlanner
from tools.proflame_capture.run_capture_session import main as run_capture_session_main
from tools.proflame_capture.runner import CaptureSessionRunner


class SemanticStubRtl433Collector(StubRtl433Collector):
    def __init__(self, semantics: list[dict], **kwargs) -> None:
        super().__init__(**kwargs)
        self._semantics = semantics
        self._index = 0

    def finalize_sample(self, sample_context):
        result = super().finalize_sample(sample_context)
        semantic = self._semantics[min(self._index, len(self._semantics) - 1)]
        self._index += 1
        return replace(result, metadata={**result.metadata, **semantic})


def _run_runner(
    tmp_path: Path,
    *,
    valid_samples_target: int = 2,
    max_attempts: int = 4,
    collectors: list[StubCollector] | None = None,
) -> tuple[dict, Path]:
    config = CaptureSessionConfig(
        output_root=tmp_path,
        valid_samples_target=valid_samples_target,
        max_attempts=max_attempts,
        non_interactive=True,
        sample_timeout_seconds=0.01,
        poll_interval_seconds=0.0,
    )
    session_runner = CaptureSessionRunner(
        config=config,
        collectors=collectors
        or [
            StubLilyGoCollector(),
            StubYardStickCollector(),
            StubRtl433Collector(),
        ],
    )
    summary = session_runner.run()
    return summary, Path(summary["session_dir"])


def test_action_planner_avoids_no_ops() -> None:
    config = CaptureSessionConfig()
    planner = ActionPlanner(config)

    assert planner.choose_next_action(FireplaceState(power=None)) == "power_toggle"
    assert planner.choose_next_action(FireplaceState(power=0, flame=0, fan=0)) == "power_toggle"
    assert planner.choose_next_action(FireplaceState(power=1, flame=6, fan=6)) in {
        "flame_down",
        "fan_down",
        "power_toggle",
    }
    assert planner.choose_next_action(FireplaceState(power=1, flame=6, fan=0)) == "flame_down"
    assert planner.choose_next_action(FireplaceState(power=1, flame=0, fan=6)) in {
        "flame_up",
        "power_toggle",
        "fan_down",
    }


def test_sample_ids_and_metadata_are_generated(tmp_path: Path) -> None:
    summary, session_dir = _run_runner(tmp_path, valid_samples_target=1, max_attempts=1)

    manifest = json.loads((session_dir / "session_manifest.json").read_text(encoding="utf-8"))
    sample_id = manifest["sample_ids"][0]
    sample_dir = session_dir / sample_id
    sample_manifest = json.loads((sample_dir / "sample_manifest.json").read_text(encoding="utf-8"))

    assert summary["valid_samples_collected"] == 1
    assert sample_manifest["identity"]["session_id"] == summary["session_id"]
    assert sample_manifest["identity"]["sample_id"] == sample_id
    assert sample_manifest["identity"]["sample_index"] == 1
    assert sample_manifest["identity"]["attempt_index"] == 1
    assert sample_manifest["identity"]["collection_valid"] is True


def test_invalid_sample_does_not_count_toward_target(tmp_path: Path) -> None:
    collectors = [
        StubLilyGoCollector(),
        StubYardStickCollector(should_validate=False),
        StubRtl433Collector(),
    ]
    summary, session_dir = _run_runner(
        tmp_path,
        valid_samples_target=1,
        max_attempts=2,
        collectors=collectors,
    )

    manifest = json.loads((session_dir / "session_manifest.json").read_text(encoding="utf-8"))

    assert summary["valid_samples_collected"] == 0
    assert summary["max_attempts_exhausted"] is True
    assert len(manifest["sample_ids"]) == 2


def test_runner_stops_at_valid_target(tmp_path: Path) -> None:
    summary, session_dir = _run_runner(tmp_path, valid_samples_target=2, max_attempts=5)

    assert summary["target_reached"] is True
    assert summary["attempts_used"] == 2
    assert len(json.loads((session_dir / "session_manifest.json").read_text(encoding="utf-8"))["sample_ids"]) == 2


def test_runner_stops_at_max_attempts(tmp_path: Path) -> None:
    collectors = [
        StubLilyGoCollector(should_complete=False),
        StubYardStickCollector(should_complete=False),
        StubRtl433Collector(should_complete=False),
    ]
    summary, _ = _run_runner(
        tmp_path,
        valid_samples_target=2,
        max_attempts=3,
        collectors=collectors,
    )

    assert summary["valid_samples_collected"] == 0
    assert summary["attempts_used"] == 3
    assert summary["max_attempts_exhausted"] is True


def test_session_and_sample_manifests_are_written(tmp_path: Path) -> None:
    _, session_dir = _run_runner(tmp_path, valid_samples_target=1, max_attempts=1)

    session_manifest = session_dir / "session_manifest.json"
    sample_id = json.loads(session_manifest.read_text(encoding="utf-8"))["sample_ids"][0]
    sample_dir = session_dir / sample_id

    assert session_manifest.is_file()
    assert (sample_dir / "sample_manifest.json").is_file()
    assert (sample_dir / "quick_validation.json").is_file()


def test_stub_collectors_integrate_with_runner(tmp_path: Path) -> None:
    _, session_dir = _run_runner(tmp_path, valid_samples_target=1, max_attempts=1)
    sample_id = json.loads((session_dir / "session_manifest.json").read_text(encoding="utf-8"))["sample_ids"][0]
    sample_dir = session_dir / sample_id

    assert (sample_dir / "lilygo_stub.json").is_file()
    assert (sample_dir / "yardstick_stub.json").is_file()
    assert (sample_dir / "rtl433_stub.json").is_file()


def test_operator_status_files_are_written(tmp_path: Path) -> None:
    summary, session_dir = _run_runner(tmp_path, valid_samples_target=1, max_attempts=1)

    session_status = json.loads((session_dir / "operator_status.json").read_text(encoding="utf-8"))
    latest_status = json.loads((tmp_path / "operator_status_latest.json").read_text(encoding="utf-8"))

    assert summary["session_id"] == session_status["session_id"] == latest_status["session_id"]
    assert session_status["phase"] == "session_complete"
    assert latest_status["phase"] == "session_complete"


def test_action_planner_can_be_limited_to_flame_controls() -> None:
    config = CaptureSessionConfig(
        commands=(CaptureCommand.FLAME_UP, CaptureCommand.FLAME_DOWN),
        initial_state=FireplaceState(power=1, flame=3, fan=2),
    )
    planner = ActionPlanner(config)

    assert planner.choose_next_action(FireplaceState(power=1, flame=3, fan=2)) == "flame_up"
    assert planner.choose_next_action(FireplaceState(power=1, flame=6, fan=2)) == "flame_down"


def test_action_planner_rotates_across_enabled_non_noop_commands() -> None:
    config = CaptureSessionConfig(
        commands=(
            CaptureCommand.FLAME_UP,
            CaptureCommand.FLAME_DOWN,
            CaptureCommand.FAN_UP,
            CaptureCommand.FAN_DOWN,
        ),
        initial_state=FireplaceState(power=1, flame=3, fan=2),
    )
    planner = ActionPlanner(config)

    actions = [
        planner.choose_next_action(FireplaceState(power=1, flame=3, fan=2)).value,
        planner.choose_next_action(FireplaceState(power=1, flame=4, fan=2)).value,
        planner.choose_next_action(FireplaceState(power=1, flame=4, fan=3)).value,
        planner.choose_next_action(FireplaceState(power=1, flame=4, fan=3)).value,
    ]

    assert actions == ["flame_up", "flame_down", "fan_up", "fan_down"]


def test_action_planner_can_follow_explicit_valid_sample_sequence() -> None:
    config = CaptureSessionConfig(
        commands=(
            CaptureCommand.POWER_TOGGLE,
            CaptureCommand.FLAME_UP,
            CaptureCommand.FLAME_DOWN,
            CaptureCommand.FAN_UP,
            CaptureCommand.FAN_DOWN,
        ),
        command_plan=(
            CaptureCommand.POWER_TOGGLE,
            CaptureCommand.FLAME_UP,
            CaptureCommand.FAN_UP,
            CaptureCommand.FLAME_DOWN,
            CaptureCommand.FAN_DOWN,
            CaptureCommand.POWER_TOGGLE,
        ),
        initial_state=FireplaceState(power=0, flame=3, fan=2),
    )
    planner = ActionPlanner(config)

    assert planner.choose_next_action(FireplaceState(power=0, flame=3, fan=2)) == "power_toggle"
    assert planner.choose_next_action(FireplaceState(power=0, flame=3, fan=2)) == "power_toggle"
    planner.record_valid_action(CaptureCommand.POWER_TOGGLE)

    assert planner.choose_next_action(FireplaceState(power=1, flame=3, fan=2)) == "flame_up"
    planner.record_valid_action(CaptureCommand.FLAME_UP)
    assert planner.choose_next_action(FireplaceState(power=1, flame=4, fan=2)) == "fan_up"
    planner.record_valid_action(CaptureCommand.FAN_UP)
    assert planner.choose_next_action(FireplaceState(power=1, flame=4, fan=3)) == "flame_down"
    planner.record_valid_action(CaptureCommand.FLAME_DOWN)
    assert planner.choose_next_action(FireplaceState(power=1, flame=3, fan=3)) == "fan_down"
    planner.record_valid_action(CaptureCommand.FAN_DOWN)
    assert planner.choose_next_action(FireplaceState(power=1, flame=3, fan=2)) == "power_toggle"


def test_operator_prompt_stream_uses_one_block_per_attempt() -> None:
    runner = CaptureSessionRunner(
        config=CaptureSessionConfig(non_interactive=False),
        collectors=[],
    )

    first = runner._render_operator_prompt_text(
        {
            "phase": "waiting_for_lilygo_marker",
            "sample_index": 1,
            "requested_action": "flame_up",
            "updated_at_utc": "2026-05-12T10:53:16+00:00",
            "message": "",
        }
    )
    second = runner._render_operator_prompt_text(
        {
            "phase": "press_remote_now",
            "sample_index": 1,
            "requested_action": "flame_up",
            "updated_at_utc": "2026-05-12T10:53:25+00:00",
            "message": "Press Flame Up once.",
        }
    )
    third = runner._render_operator_prompt_text(
        {
            "phase": "sample_valid",
            "sample_index": 1,
            "requested_action": "flame_up",
            "updated_at_utc": "2026-05-12T10:53:32+00:00",
            "message": "Sample collected successfully.",
        }
    )

    assert "===== PROMPT UPDATE =====" in first
    assert "===== PROMPT UPDATE =====" not in second
    assert "===== PROMPT UPDATE =====" not in third


def test_default_lilygo_capture_flow_is_fifo_semantic() -> None:
    assert CaptureSessionConfig().lilygo_capture_flow == "fifo_rolling_complete"


def test_operator_prompt_supports_fifo_rolling_capture_complete_flow() -> None:
    runner = CaptureSessionRunner(
        config=CaptureSessionConfig(non_interactive=False, lilygo_capture_flow="fifo_rolling_complete"),
        collectors=[],
    )

    prompt = runner._render_operator_prompt_text(
        {
            "phase": "rolling_capture_ready",
            "sample_index": 1,
            "requested_action": "fan_up",
            "updated_at_utc": "2026-05-13T10:00:00+00:00",
            "message": "Ensure Enable Capture is set to `fifo_trailing_window`. Press Fan Up once.",
        }
    )

    assert "fifo_trailing_window" in prompt
    assert "RX FIFO Capture Complete" in prompt
    assert "RX Capture Complete" not in prompt
    assert "RX Capture Next Window" not in prompt


def test_semantic_state_target_accepts_matching_rtl433_state(tmp_path: Path) -> None:
    config = CaptureSessionConfig(
        output_root=tmp_path,
        max_attempts=3,
        non_interactive=True,
        sample_timeout_seconds=0.01,
        poll_interval_seconds=0.0,
        semantic_targets=({"power": 1, "flame": 2, "fan": 2},),
        semantic_target_fields=("power", "flame", "fan"),
        semantic_replicates_per_target=1,
        semantic_expected_id="3b3f02",
    )
    runner = CaptureSessionRunner(
        config=config,
        collectors=[
            StubLilyGoCollector(),
            StubYardStickCollector(),
            SemanticStubRtl433Collector(
                [
                    {
                        "id": "3b3f02",
                        "integrity": "CHECKSUM",
                        "power": 1,
                        "flame": 2,
                        "fan": 2,
                    }
                ]
            ),
        ],
    )

    summary = runner.run()

    assert summary["valid_samples_collected"] == 1
    assert summary["semantic_target_summary"]["target_matches"] == 1
    assert summary["semantic_target_summary"]["targets_satisfied"] is True


def test_semantic_state_target_rejects_and_persists_mismatch(tmp_path: Path) -> None:
    config = CaptureSessionConfig(
        output_root=tmp_path,
        max_attempts=2,
        non_interactive=True,
        sample_timeout_seconds=0.01,
        poll_interval_seconds=0.0,
        semantic_targets=({"power": 1, "flame": 2, "fan": 2},),
        semantic_target_fields=("power", "flame", "fan"),
        semantic_replicates_per_target=1,
        semantic_expected_id="3b3f02",
    )
    runner = CaptureSessionRunner(
        config=config,
        collectors=[
            StubLilyGoCollector(),
            StubYardStickCollector(),
            SemanticStubRtl433Collector(
                [
                    {"id": "3b3f02", "integrity": "CHECKSUM", "power": 1, "flame": 3, "fan": 2},
                    {"id": "3b3f02", "integrity": "CHECKSUM", "power": 1, "flame": 2, "fan": 2},
                ]
            ),
        ],
    )

    summary = runner.run()
    session_dir = Path(summary["session_dir"])
    manifest = json.loads((session_dir / "session_manifest.json").read_text(encoding="utf-8"))
    first_sample = json.loads(
        (session_dir / manifest["sample_ids"][0] / "quick_validation.json").read_text(encoding="utf-8")
    )

    assert summary["valid_samples_collected"] == 1
    assert summary["attempts_used"] == 2
    assert summary["semantic_target_summary"]["target_mismatches"] == 1
    assert first_sample["collection_valid"] is False
    assert "semantic_target_mismatch" in first_sample["collection_reject_reasons"]


def test_semantic_target_counts_stop_when_each_target_satisfied(tmp_path: Path) -> None:
    config = CaptureSessionConfig(
        output_root=tmp_path,
        max_attempts=5,
        non_interactive=True,
        sample_timeout_seconds=0.01,
        poll_interval_seconds=0.0,
        semantic_targets=(
            {"power": 1, "flame": 2, "fan": 2},
            {"power": 1, "flame": 2, "fan": 3},
        ),
        semantic_target_fields=("power", "flame", "fan"),
        semantic_replicates_per_target=2,
        semantic_expected_id="3b3f02",
    )
    runner = CaptureSessionRunner(
        config=config,
        collectors=[
            StubLilyGoCollector(),
            StubYardStickCollector(),
            SemanticStubRtl433Collector(
                [
                    {"id": "3b3f02", "integrity": "CHECKSUM", "power": 1, "flame": 2, "fan": 2},
                    {"id": "3b3f02", "integrity": "CHECKSUM", "power": 1, "flame": 2, "fan": 3},
                    {"id": "3b3f02", "integrity": "CHECKSUM", "power": 1, "flame": 2, "fan": 2},
                    {"id": "3b3f02", "integrity": "CHECKSUM", "power": 1, "flame": 2, "fan": 3},
                    {"id": "3b3f02", "integrity": "CHECKSUM", "power": 1, "flame": 1, "fan": 3},
                ]
            ),
        ],
    )

    summary = runner.run()

    assert summary["attempts_used"] == 4
    assert summary["valid_samples_collected"] == 4
    assert summary["target_reached"] is True
    assert summary["semantic_target_summary"]["target_counts"] == {
        "power=1,flame=2,fan=2": 2,
        "power=1,flame=2,fan=3": 2,
    }


def test_semantic_target_mode_stops_at_max_attempts_when_unsatisfied(tmp_path: Path) -> None:
    config = CaptureSessionConfig(
        output_root=tmp_path,
        max_attempts=2,
        non_interactive=True,
        sample_timeout_seconds=0.01,
        poll_interval_seconds=0.0,
        semantic_targets=({"power": 1, "flame": 2, "fan": 2},),
        semantic_target_fields=("power", "flame", "fan"),
        semantic_replicates_per_target=2,
        semantic_expected_id="3b3f02",
    )
    runner = CaptureSessionRunner(
        config=config,
        collectors=[
            StubLilyGoCollector(),
            StubYardStickCollector(),
            SemanticStubRtl433Collector(
                [
                    {"id": "3b3f02", "integrity": "CHECKSUM", "power": 1, "flame": 3, "fan": 2},
                    {"id": "3b3f02", "integrity": "CHECKSUM", "power": 1, "flame": 2, "fan": 2},
                ]
            ),
        ],
    )

    summary = runner.run()

    assert summary["valid_samples_collected"] == 1
    assert summary["target_reached"] is False
    assert summary["max_attempts_exhausted"] is True


def test_cli_rejects_initial_state_seed() -> None:
    try:
        run_capture_session_main(["--stub-sources", "--initial-fan", "1"])
    except SystemExit as exc:
        assert "Initial fireplace state must not be specified" in str(exc)
    else:
        raise AssertionError("initial state seed should be rejected")


def test_semantic_target_planning_uses_rtl433_state_after_source_invalid(tmp_path: Path) -> None:
    config = CaptureSessionConfig(
        output_root=tmp_path,
        max_attempts=2,
        non_interactive=True,
        sample_timeout_seconds=0.01,
        poll_interval_seconds=0.0,
        commands=(CaptureCommand.FAN_UP, CaptureCommand.FAN_DOWN),
        semantic_targets=({"power": 1, "flame": 1, "fan": 2},),
        semantic_target_fields=("power", "flame", "fan"),
        semantic_replicates_per_target=1,
        semantic_expected_id="3b3f02",
    )
    runner = CaptureSessionRunner(
        config=config,
        collectors=[
            StubLilyGoCollector(),
            StubYardStickCollector(should_validate=False),
            SemanticStubRtl433Collector(
                [
                    {"id": "3b3f02", "integrity": "CHECKSUM", "power": 1, "flame": 1, "fan": 3},
                    {"id": "3b3f02", "integrity": "CHECKSUM", "power": 1, "flame": 1, "fan": 2},
                ]
            ),
        ],
    )

    summary = runner.run()
    session_dir = Path(summary["session_dir"])
    manifest = json.loads((session_dir / "session_manifest.json").read_text(encoding="utf-8"))
    second_sample = json.loads(
        (session_dir / manifest["sample_ids"][1] / "sample_manifest.json").read_text(encoding="utf-8")
    )

    assert second_sample["identity"]["requested_action"] == "fan_down"


def test_semantic_target_planning_moves_toward_underfilled_observed_target(tmp_path: Path) -> None:
    config = CaptureSessionConfig(
        output_root=tmp_path,
        max_attempts=2,
        non_interactive=True,
        sample_timeout_seconds=0.01,
        poll_interval_seconds=0.0,
        commands=(CaptureCommand.FAN_UP, CaptureCommand.FAN_DOWN),
        semantic_targets=(
            {"power": 1, "flame": 1, "fan": 2},
            {"power": 1, "flame": 1, "fan": 3},
        ),
        semantic_target_fields=("power", "flame", "fan"),
        semantic_replicates_per_target=1,
        semantic_expected_id="3b3f02",
    )
    runner = CaptureSessionRunner(
        config=config,
        collectors=[
            StubLilyGoCollector(),
            StubYardStickCollector(),
            SemanticStubRtl433Collector(
                [
                    {"id": "3b3f02", "integrity": "CHECKSUM", "power": 1, "flame": 1, "fan": 3},
                    {"id": "3b3f02", "integrity": "CHECKSUM", "power": 1, "flame": 1, "fan": 2},
                ]
            ),
        ],
    )

    summary = runner.run()
    session_dir = Path(summary["session_dir"])
    manifest = json.loads((session_dir / "session_manifest.json").read_text(encoding="utf-8"))
    second_sample = json.loads(
        (session_dir / manifest["sample_ids"][1] / "sample_manifest.json").read_text(encoding="utf-8")
    )

    assert second_sample["identity"]["requested_action"] == "fan_down"
    assert summary["semantic_target_summary"]["target_counts"] == {
        "power=1,flame=1,fan=2": 1,
        "power=1,flame=1,fan=3": 1,
    }


def test_requested_action_transition_rejects_wrong_fan_direction_without_absolute_targets(tmp_path: Path) -> None:
    config = CaptureSessionConfig(
        output_root=tmp_path,
        valid_samples_target=2,
        max_attempts=3,
        non_interactive=True,
        sample_timeout_seconds=0.01,
        poll_interval_seconds=0.0,
        commands=(CaptureCommand.FAN_UP, CaptureCommand.FAN_DOWN),
        command_plan=(CaptureCommand.FAN_UP, CaptureCommand.FAN_UP, CaptureCommand.FAN_DOWN),
        semantic_expected_id="3b3f02",
    )
    runner = CaptureSessionRunner(
        config=config,
        collectors=[
            StubLilyGoCollector(),
            StubYardStickCollector(),
            SemanticStubRtl433Collector(
                [
                    {"id": "3b3f02", "integrity": "CHECKSUM", "power": 1, "flame": 6, "fan": 3},
                    {"id": "3b3f02", "integrity": "CHECKSUM", "power": 1, "flame": 6, "fan": 2},
                    {"id": "3b3f02", "integrity": "CHECKSUM", "power": 1, "flame": 6, "fan": 3},
                ]
            ),
        ],
    )

    summary = runner.run()
    session_dir = Path(summary["session_dir"])
    manifest = json.loads((session_dir / "session_manifest.json").read_text(encoding="utf-8"))
    first = json.loads((session_dir / manifest["sample_ids"][0] / "quick_validation.json").read_text(encoding="utf-8"))
    second = json.loads((session_dir / manifest["sample_ids"][1] / "quick_validation.json").read_text(encoding="utf-8"))
    third = json.loads((session_dir / manifest["sample_ids"][2] / "quick_validation.json").read_text(encoding="utf-8"))

    assert summary["valid_samples_collected"] == 2
    assert first["collection_valid"] is True
    assert first["requested_action_transition"]["observed_delta"] is None
    assert second["collection_valid"] is False
    assert second["requested_action_transition"]["observed_delta"] == -1
    assert "requested_action_transition_mismatch" in second["collection_reject_reasons"]
    assert third["collection_valid"] is True
    assert third["requested_action_transition"]["requested_action"] == "fan_up"
    assert third["requested_action_transition"]["observed_delta"] == 1


def test_requested_action_transition_accepts_unexpected_absolute_flame_when_delta_matches(tmp_path: Path) -> None:
    config = CaptureSessionConfig(
        output_root=tmp_path,
        valid_samples_target=2,
        max_attempts=2,
        non_interactive=True,
        sample_timeout_seconds=0.01,
        poll_interval_seconds=0.0,
        commands=(CaptureCommand.FAN_UP, CaptureCommand.FAN_DOWN),
        command_plan=(CaptureCommand.FAN_UP, CaptureCommand.FAN_DOWN),
        semantic_expected_id="3b3f02",
    )
    runner = CaptureSessionRunner(
        config=config,
        collectors=[
            StubLilyGoCollector(),
            StubYardStickCollector(),
            SemanticStubRtl433Collector(
                [
                    {"id": "3b3f02", "integrity": "CHECKSUM", "power": 1, "flame": 6, "fan": 4},
                    {"id": "3b3f02", "integrity": "CHECKSUM", "power": 1, "flame": 6, "fan": 3},
                ]
            ),
        ],
    )

    summary = runner.run()
    session_dir = Path(summary["session_dir"])
    manifest = json.loads((session_dir / "session_manifest.json").read_text(encoding="utf-8"))
    second = json.loads((session_dir / manifest["sample_ids"][1] / "quick_validation.json").read_text(encoding="utf-8"))

    assert summary["valid_samples_collected"] == 2
    assert second["collection_valid"] is True
    assert second["requested_action_transition"]["state_before"]["flame"] == 6
    assert second["requested_action_transition"]["state_after"]["flame"] == 6
    assert second["requested_action_transition"]["observed_delta"] == -1


def test_setup_state_sample_establishes_rtl433_state_without_counting(tmp_path: Path) -> None:
    config = CaptureSessionConfig(
        output_root=tmp_path,
        valid_samples_target=1,
        max_attempts=3,
        non_interactive=True,
        sample_timeout_seconds=0.01,
        poll_interval_seconds=0.0,
        commands=(CaptureCommand.FAN_UP, CaptureCommand.FAN_DOWN),
        setup_state_sample=True,
        semantic_expected_id="3b3f02",
    )
    runner = CaptureSessionRunner(
        config=config,
        collectors=[
            StubLilyGoCollector(),
            StubYardStickCollector(),
            SemanticStubRtl433Collector(
                [
                    {"id": "3b3f02", "integrity": "CHECKSUM", "power": 1, "flame": 6, "fan": 2},
                    {"id": "3b3f02", "integrity": "CHECKSUM", "power": 1, "flame": 6, "fan": 3},
                ]
            ),
        ],
    )

    summary = runner.run()
    session_dir = Path(summary["session_dir"])
    manifest = json.loads((session_dir / "session_manifest.json").read_text(encoding="utf-8"))
    setup = json.loads((session_dir / manifest["sample_ids"][0] / "quick_validation.json").read_text(encoding="utf-8"))
    counted = json.loads(
        (session_dir / manifest["sample_ids"][1] / "quick_validation.json").read_text(encoding="utf-8")
    )

    assert summary["valid_samples_collected"] == 1
    assert summary["attempts_used"] == 2
    assert summary["setup_state_sample"]["complete"] is True
    assert manifest["sample_ids"][0].endswith("-s000-a001")
    assert setup["collection_valid"] is False
    assert setup["setup_state_sample"]["accepted"] is True
    assert setup["setup_state_sample"]["excluded_from_analysis_replicates"] is True
    assert "setup_state_sample_excluded" in setup["collection_reject_reasons"]
    assert counted["collection_valid"] is True
    assert counted["requested_action_transition"]["requested_action"] == "fan_up"
    assert counted["requested_action_transition"]["observed_delta"] == 1
