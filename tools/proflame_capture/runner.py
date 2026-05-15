"""Session runner for coordinated Proflame2 capture orchestration."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from .collectors import Collector
from .models import (
    CaptureCommand,
    CaptureSessionConfig,
    FireplaceState,
    SampleContext,
    SampleIdentity,
    SampleOutcome,
    SessionContext,
    build_sample_id,
    utc_now,
    utc_timestamp_id,
)
from .planner import ActionPlanner, build_operator_prompt
from .quick_validation import build_quick_validation

PromptHandler = Callable[[str], None]
StatusHandler = Callable[[str], None]


@dataclass
class _RunState:
    """Mutable counters for one capture session run."""

    valid_samples: int
    attempts: int
    outcomes: list[SampleOutcome]
    reject_reason_counts: dict[str, int]
    semantic_target_counts: dict[str, int]
    semantic_target_mismatches: int
    setup_state_complete: bool


@dataclass(frozen=True)
class _PreparedSample:
    """All static context needed to collect and finalize one sample attempt."""

    sample_is_setup: bool
    sample_index: int
    action: CaptureCommand
    prompt: str
    sample_dir: Path
    identity: SampleIdentity
    sample_context: SampleContext


@dataclass(frozen=True)
class _SampleEvaluation:
    """Validation and accounting result for one finalized sample attempt."""

    outcome: SampleOutcome
    quick_validation_payload: dict
    reject_reasons: list[str]
    source_collection_valid: bool
    semantic_target_result: dict
    action_transition_result: dict
    collection_valid: bool
    setup_state_accepted: bool
    state_after: FireplaceState


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _append_text(path: Path, content: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(content)


class CaptureSessionRunner:
    """Coordinate one finite prompt-driven capture session."""

    def __init__(
        self,
        *,
        config: CaptureSessionConfig,
        collectors: list[Collector],
        prompt_handler: PromptHandler | None = None,
        status_handler: StatusHandler | None = None,
        planner: ActionPlanner | None = None,
        session_metadata: dict | None = None,
    ) -> None:
        self._config = config
        self._collectors = collectors
        self._prompt_handler = prompt_handler or (lambda _prompt: None)
        self._status_handler = status_handler or (lambda _message: None)
        self._planner = planner or ActionPlanner(config)
        self._state = config.initial_state
        self._session_metadata = session_metadata or {}
        self._last_prompt_stream_key: tuple[str, int | None] | None = None

    def run(self) -> dict:
        """Run one complete capture session and persist its manifests."""

        session_context = self._create_session_context()
        run_state = self._initialize_run_state()
        self._start_collectors_for_session(session_context)
        self._write_session_manifest(
            session_context,
            valid_samples=run_state.valid_samples,
            attempts=run_state.attempts,
            outcomes=run_state.outcomes,
        )

        while self._should_continue_run(run_state):
            run_state.attempts += 1
            prepared = self._prepare_sample(session_context, run_state)
            self._start_collectors_for_sample(prepared.sample_context)
            self._prompt_operator_for_sample(session_context, prepared)
            self._wait_for_collectors()

            evaluation = self._finalize_sample(prepared)
            self._record_sample_evaluation(session_context, run_state, prepared, evaluation)
            self._write_session_manifest(
                session_context,
                valid_samples=run_state.valid_samples,
                attempts=run_state.attempts,
                outcomes=run_state.outcomes,
            )

        summary = self._build_run_summary(session_context, run_state)
        self._write_run_summary(session_context, summary)
        return summary

    def _create_session_context(self) -> SessionContext:
        started_at = utc_now()
        session_id = utc_timestamp_id(started_at)
        session_dir = self._config.output_root / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        session_context = SessionContext(
            session_id=session_id,
            session_dir=session_dir,
            config=self._config,
            started_at_utc=started_at.isoformat(),
        )
        self._write_operator_status(
            session_context,
            phase="session_started",
            message="Session created.",
            sample_index=None,
            requested_action=None,
        )
        return session_context

    def _initialize_run_state(self) -> _RunState:
        return _RunState(
            valid_samples=0,
            attempts=0,
            outcomes=[],
            reject_reason_counts={},
            semantic_target_counts=self._initial_semantic_target_counts(),
            semantic_target_mismatches=0,
            setup_state_complete=not self._config.setup_state_sample,
        )

    def _start_collectors_for_session(self, session_context: SessionContext) -> None:
        for collector in self._collectors:
            collector.start_session(session_context)

    def _should_continue_run(self, run_state: _RunState) -> bool:
        targets_reached = self._target_reached(run_state.valid_samples, run_state.semantic_target_counts)
        return (
            not run_state.setup_state_complete or not targets_reached
        ) and run_state.attempts < self._config.max_attempts

    def _prepare_sample(self, session_context: SessionContext, run_state: _RunState) -> _PreparedSample:
        sample_is_setup = not run_state.setup_state_complete
        sample_index = 0 if sample_is_setup else run_state.valid_samples + 1
        action = (
            CaptureCommand.SETUP_STATE
            if sample_is_setup
            else self._choose_next_action(run_state.semantic_target_counts)
        )
        prompt = build_operator_prompt(action)
        sample_id = build_sample_id(
            session_context.session_id,
            sample_index=sample_index,
            attempt_index=run_state.attempts,
        )
        sample_dir = session_context.session_dir / sample_id
        sample_dir.mkdir(parents=True, exist_ok=True)
        identity = SampleIdentity(
            session_id=session_context.session_id,
            sample_id=sample_id,
            sample_index=sample_index,
            attempt_index=run_state.attempts,
            requested_action=action,
            operator_prompt=prompt,
            coordinator_started_at_utc=utc_now().isoformat(),
        )
        sample_context = SampleContext(
            identity=identity,
            sample_dir=sample_dir,
            state_before=self._state,
        )
        return _PreparedSample(
            sample_is_setup=sample_is_setup,
            sample_index=sample_index,
            action=action,
            prompt=prompt,
            sample_dir=sample_dir,
            identity=identity,
            sample_context=sample_context,
        )

    def _start_collectors_for_sample(self, sample_context: SampleContext) -> None:
        for collector in self._collectors:
            collector.start_sample(sample_context)

    def _prompt_operator_for_sample(self, session_context: SessionContext, prepared: _PreparedSample) -> None:
        if self._is_rolling_capture_flow() and not self._config.non_interactive:
            self._prompt_for_rolling_capture_sample(session_context, prepared)
            return
        if self._should_gate_on_lilygo_marker():
            self._prompt_for_lilygo_marker_sample(session_context, prepared)
            return
        if not self._config.non_interactive:
            self._prompt_handler(prepared.prompt)

    def _prompt_for_rolling_capture_sample(self, session_context: SessionContext, prepared: _PreparedSample) -> None:
        complete_button = self._capture_complete_button_name()
        rolling_message = (
            f"Ensure Enable Capture is set to {self._capture_enable_mode_label()}. {prepared.prompt} "
            f"Wait briefly for the RF burst to finish, then press {complete_button}."
        )
        if prepared.sample_is_setup:
            rolling_message = (
                "Setup-only state sample. "
                f"{rolling_message} This setup sample will not count toward analysis replicates."
            )
        self._write_operator_status(
            session_context,
            phase="rolling_capture_ready",
            message=rolling_message,
            sample_index=prepared.sample_index,
            requested_action=prepared.action.value,
        )

    def _prompt_for_lilygo_marker_sample(self, session_context: SessionContext, prepared: _PreparedSample) -> None:
        for collector in self._collectors:
            defer_anchor = getattr(collector, "defer_sample_anchor", None)
            if callable(defer_anchor):
                defer_anchor()
        self._prompt_handler(
            f"Prepare sample {prepared.sample_index} for {prepared.action.value}. "
            "Press the RX Capture Next Window button."
        )
        self._status_handler("waiting_for_lilygo_marker")
        self._write_operator_status(
            session_context,
            phase="waiting_for_lilygo_marker",
            message="Waiting for the RX Capture Next Window marker.",
            sample_index=prepared.sample_index,
            requested_action=prepared.action.value,
        )
        anchor = self._await_sample_trigger()
        if anchor is not None:
            self._propagate_sample_anchor(anchor)
            self._status_handler(f"lilygo_marker_seen action={prepared.action.value} prompt={prepared.prompt}")
            self._write_operator_status(
                session_context,
                phase="press_remote_now",
                message=prepared.prompt,
                sample_index=prepared.sample_index,
                requested_action=prepared.action.value,
            )
            return
        self._status_handler("lilygo_marker_timeout")
        self._write_operator_status(
            session_context,
            phase="lilygo_marker_timeout",
            message="Timed out waiting for LilyGO capture-arm marker.",
            sample_index=prepared.sample_index,
            requested_action=prepared.action.value,
        )

    def _finalize_sample(self, prepared: _PreparedSample) -> _SampleEvaluation:
        collector_results = self._collect_sample_results(prepared.sample_context)
        quick_validation = build_quick_validation(
            sample_context=prepared.sample_context,
            collector_results=collector_results,
        )
        reject_reasons = quick_validation.collection_reject_reasons
        source_collection_valid = quick_validation.collection_valid
        semantic_target_result = self._evaluate_semantic_target(
            collector_results=collector_results,
            source_collection_valid=source_collection_valid,
        )
        canonical_state_after = self._canonical_state_from_collector_results(collector_results)
        action_transition_result = self._evaluate_requested_action_transition(
            requested_action=prepared.action,
            state_before=self._state,
            state_after=canonical_state_after,
            source_collection_valid=source_collection_valid,
        )
        setup_state_accepted = (
            prepared.sample_is_setup and source_collection_valid and canonical_state_after is not None
        )
        collection_valid = (
            source_collection_valid
            and semantic_target_result["accepted"]
            and action_transition_result["accepted"]
            and not prepared.sample_is_setup
        )
        reject_reasons = self._sample_reject_reasons(
            reject_reasons=reject_reasons,
            semantic_target_result=semantic_target_result,
            action_transition_result=action_transition_result,
            sample_is_setup=prepared.sample_is_setup,
            source_collection_valid=source_collection_valid,
            canonical_state_after=canonical_state_after,
        )
        quick_validation_payload = self._build_quick_validation_payload(
            quick_validation=quick_validation,
            collection_valid=collection_valid,
            reject_reasons=reject_reasons,
            semantic_target_result=semantic_target_result,
            action_transition_result=action_transition_result,
            sample_is_setup=prepared.sample_is_setup,
            setup_state_accepted=setup_state_accepted,
        )
        finalized_identity = self._build_finalized_identity(
            prepared.identity,
            collector_results=collector_results,
            collection_valid=collection_valid,
            reject_reasons=reject_reasons,
        )
        state_after = self._resolve_state_after_sample(
            requested_action=prepared.action,
            source_collection_valid=source_collection_valid,
            canonical_state_after=canonical_state_after,
            proposed_state_after=quick_validation.proposed_state_after,
        )
        if collection_valid:
            self._planner.record_valid_action(prepared.action)
        outcome = SampleOutcome(
            identity=finalized_identity,
            collector_results=collector_results,
            state_before=self._state,
            state_after=state_after,
        )
        return _SampleEvaluation(
            outcome=outcome,
            quick_validation_payload=quick_validation_payload,
            reject_reasons=reject_reasons,
            source_collection_valid=source_collection_valid,
            semantic_target_result=semantic_target_result,
            action_transition_result=action_transition_result,
            collection_valid=collection_valid,
            setup_state_accepted=setup_state_accepted,
            state_after=state_after,
        )

    def _collect_sample_results(self, sample_context: SampleContext) -> dict:
        collector_results = {}
        for collector in self._collectors:
            raw_result = collector.finalize_sample(sample_context)
            collector_results[collector.source_name] = self._enrich_collector_result(collector, raw_result)
        return collector_results

    def _sample_reject_reasons(
        self,
        *,
        reject_reasons: list[str],
        semantic_target_result: dict,
        action_transition_result: dict,
        sample_is_setup: bool,
        source_collection_valid: bool,
        canonical_state_after: FireplaceState | None,
    ) -> list[str]:
        reasons = list(reject_reasons)
        if not semantic_target_result["accepted"] and semantic_target_result["reject_reason"]:
            reasons.append(semantic_target_result["reject_reason"])
        if not action_transition_result["accepted"] and action_transition_result["reject_reason"]:
            reasons.append(action_transition_result["reject_reason"])
        if sample_is_setup:
            reasons.append("setup_state_sample_excluded")
            if source_collection_valid and canonical_state_after is None:
                reasons.append("setup_state_missing_canonical_rtl433")
        return sorted(set(reasons))

    def _build_quick_validation_payload(
        self,
        *,
        quick_validation,
        collection_valid: bool,
        reject_reasons: list[str],
        semantic_target_result: dict,
        action_transition_result: dict,
        sample_is_setup: bool,
        setup_state_accepted: bool,
    ) -> dict:
        payload = dict(quick_validation.payload)
        payload["collection_valid"] = collection_valid
        payload["collection_reject_reasons"] = reject_reasons
        payload["semantic_target"] = semantic_target_result
        payload["requested_action_transition"] = action_transition_result
        payload["setup_state_sample"] = {
            "enabled": self._config.setup_state_sample,
            "setup_only": sample_is_setup,
            "accepted": setup_state_accepted,
            "excluded_from_analysis_replicates": sample_is_setup,
        }
        if semantic_target_result["enabled"] and not semantic_target_result["accepted"]:
            payload.setdefault("notes", []).append("Sample did not count toward semantic target replicates.")
        if action_transition_result["enabled"] and not action_transition_result["accepted"]:
            payload.setdefault("notes", []).append("Sample did not match the requested rtl_433 state transition.")
        if sample_is_setup:
            payload.setdefault("notes", []).append("Setup-only sample excluded from analysis replicates.")
        return payload

    def _build_finalized_identity(
        self,
        identity: SampleIdentity,
        *,
        collector_results: dict,
        collection_valid: bool,
        reject_reasons: list[str],
    ) -> SampleIdentity:
        return SampleIdentity(
            session_id=identity.session_id,
            sample_id=identity.sample_id,
            sample_index=identity.sample_index,
            attempt_index=identity.attempt_index,
            requested_action=identity.requested_action,
            operator_prompt=identity.operator_prompt,
            coordinator_started_at_utc=identity.coordinator_started_at_utc,
            coordinator_finished_at_utc=utc_now().isoformat(),
            source_status={
                name: (
                    "complete"
                    if result.complete and result.valid
                    else "incomplete" if not result.complete else "invalid"
                )
                for name, result in collector_results.items()
            },
            collection_valid=collection_valid,
            collection_reject_reason="; ".join(reject_reasons) if reject_reasons else None,
            notes=[],
            errors=[reason for reason in reject_reasons],
        )

    def _resolve_state_after_sample(
        self,
        *,
        requested_action: CaptureCommand,
        source_collection_valid: bool,
        canonical_state_after: FireplaceState | None,
        proposed_state_after: FireplaceState | None,
    ) -> FireplaceState:
        if canonical_state_after is not None:
            return canonical_state_after
        if source_collection_valid and proposed_state_after is not None:
            return FireplaceState(
                power=proposed_state_after.power if proposed_state_after.power is not None else self._state.power,
                flame=proposed_state_after.flame if proposed_state_after.flame is not None else self._state.flame,
                fan=proposed_state_after.fan if proposed_state_after.fan is not None else self._state.fan,
            )
        if source_collection_valid:
            return self._planner.apply_action(self._state, requested_action)
        if self._semantic_targets_enabled():
            return FireplaceState()
        return self._state

    def _record_sample_evaluation(
        self,
        session_context: SessionContext,
        run_state: _RunState,
        prepared: _PreparedSample,
        evaluation: _SampleEvaluation,
    ) -> None:
        run_state.outcomes.append(evaluation.outcome)
        self._write_sample_manifests(
            prepared.sample_dir,
            evaluation.outcome,
            evaluation.quick_validation_payload,
        )
        if evaluation.collection_valid:
            self._record_valid_sample(session_context, run_state, prepared, evaluation)
        elif prepared.sample_is_setup and evaluation.setup_state_accepted:
            self._record_valid_setup_sample(session_context, run_state, prepared, evaluation)
        else:
            self._record_invalid_sample(session_context, run_state, prepared, evaluation)
        self._status_handler(self._build_sample_status_line(evaluation.outcome))

    def _record_valid_sample(
        self,
        session_context: SessionContext,
        run_state: _RunState,
        prepared: _PreparedSample,
        evaluation: _SampleEvaluation,
    ) -> None:
        run_state.valid_samples += 1
        matched_target_label = evaluation.semantic_target_result["matched_target_label"]
        if matched_target_label:
            run_state.semantic_target_counts[matched_target_label] += 1
        self._state = evaluation.state_after
        self._write_operator_status(
            session_context,
            phase="sample_valid",
            message="Sample collected successfully.",
            sample_index=prepared.sample_index,
            requested_action=prepared.action.value,
            extra={
                "reject_reasons": [],
                "source_status": evaluation.outcome.identity.source_status,
            },
        )

    def _record_valid_setup_sample(
        self,
        session_context: SessionContext,
        run_state: _RunState,
        prepared: _PreparedSample,
        evaluation: _SampleEvaluation,
    ) -> None:
        run_state.setup_state_complete = True
        self._state = evaluation.state_after
        self._increment_reject_reason_counts(run_state, evaluation.reject_reasons)
        self._write_operator_status(
            session_context,
            phase="setup_state_valid",
            message="Setup state established from rtl_433; sample excluded from analysis replicates.",
            sample_index=prepared.sample_index,
            requested_action=prepared.action.value,
            extra={
                "reject_reasons": evaluation.reject_reasons,
                "source_status": evaluation.outcome.identity.source_status,
                "state_after": evaluation.state_after.to_manifest_dict(),
            },
        )

    def _record_invalid_sample(
        self,
        session_context: SessionContext,
        run_state: _RunState,
        prepared: _PreparedSample,
        evaluation: _SampleEvaluation,
    ) -> None:
        if evaluation.semantic_target_result["enabled"] and evaluation.source_collection_valid:
            run_state.semantic_target_mismatches += 1
        if evaluation.action_transition_result["enabled"] or evaluation.semantic_target_result["enabled"]:
            self._state = evaluation.state_after
        self._increment_reject_reason_counts(run_state, evaluation.reject_reasons)
        self._write_operator_status(
            session_context,
            phase="setup_state_invalid" if prepared.sample_is_setup else "sample_invalid",
            message=(
                "Setup state sample invalid; retry setup keypress."
                if prepared.sample_is_setup
                else "Sample invalid; see reject reasons."
            ),
            sample_index=prepared.sample_index,
            requested_action=prepared.action.value,
            extra={
                "reject_reasons": evaluation.reject_reasons,
                "source_status": evaluation.outcome.identity.source_status,
            },
        )

    def _increment_reject_reason_counts(self, run_state: _RunState, reject_reasons: list[str]) -> None:
        for reason in reject_reasons:
            run_state.reject_reason_counts[reason] = run_state.reject_reason_counts.get(reason, 0) + 1

    def _build_run_summary(self, session_context: SessionContext, run_state: _RunState) -> dict:
        target_reached = self._target_reached(run_state.valid_samples, run_state.semantic_target_counts)
        return {
            "session_id": session_context.session_id,
            "session_dir": str(session_context.session_dir),
            "valid_samples_collected": run_state.valid_samples,
            "attempts_used": run_state.attempts,
            "target_reached": target_reached,
            "max_attempts_exhausted": run_state.attempts >= self._config.max_attempts and not target_reached,
            "invalid_attempts": run_state.attempts - run_state.valid_samples,
            "setup_state_sample": {
                "enabled": self._config.setup_state_sample,
                "complete": run_state.setup_state_complete,
                "canonical_state": self._state.to_manifest_dict() if run_state.setup_state_complete else None,
            },
            "reject_reason_counts": run_state.reject_reason_counts,
            "semantic_target_summary": self._semantic_target_summary(
                semantic_target_counts=run_state.semantic_target_counts,
                semantic_target_mismatches=run_state.semantic_target_mismatches,
            ),
            "selected_collectors": [collector.source_name for collector in self._collectors],
            "average_sample_duration_seconds": self._average_sample_duration_seconds(run_state.outcomes),
        }

    def _write_run_summary(self, session_context: SessionContext, summary: dict) -> None:
        _write_json(session_context.session_dir / "run_summary.json", summary)
        self._write_operator_status(
            session_context,
            phase="session_complete",
            message="Session finished.",
            sample_index=None,
            requested_action=None,
            extra=summary,
        )
        self._status_handler(self._build_session_status_line(summary))

    def _wait_for_collectors(self) -> None:
        deadline = time.monotonic() + self._config.sample_timeout_seconds
        while time.monotonic() < deadline:
            all_complete = True
            for collector in self._collectors:
                if not collector.is_complete():
                    collector.poll()
                if not collector.is_complete():
                    all_complete = False
            if all_complete or self._config.dry_run:
                return
            time.sleep(self._config.poll_interval_seconds)

    def _write_session_manifest(
        self,
        session_context: SessionContext,
        *,
        valid_samples: int,
        attempts: int,
        outcomes: list[SampleOutcome],
    ) -> None:
        manifest = session_context.to_manifest_dict()
        manifest.update(
            {
                "valid_samples_collected": valid_samples,
                "attempts_used": attempts,
                "sample_ids": [outcome.identity.sample_id for outcome in outcomes],
                "semantic_targets": [dict(target) for target in self._config.semantic_targets],
                "semantic_target_fields": list(self._config.semantic_target_fields),
                "semantic_replicates_per_target": self._config.semantic_replicates_per_target,
            }
        )
        manifest.update(self._session_metadata)
        _write_json(session_context.session_dir / "session_manifest.json", manifest)

    def _write_sample_manifests(self, sample_dir: Path, outcome: SampleOutcome, quick_validation: dict) -> None:
        _write_json(sample_dir / "sample_manifest.json", outcome.to_manifest_dict())
        analysis_dir = sample_dir / "analysis"
        analysis_dir.mkdir(parents=True, exist_ok=True)
        _write_json(sample_dir / "quick_validation.json", quick_validation)
        _write_json(analysis_dir / "quick_validation.json", quick_validation)

    def _enrich_collector_result(self, collector: Collector, result):
        mode = getattr(collector, "source_mode", None)
        artifact_dir = None
        if result.artifact_paths:
            artifact_dir = result.artifact_paths[0].path.split("/", 1)[0]
        return replace(
            result,
            selected=True,
            mode=mode,
            artifact_dir=artifact_dir or result.source_name,
        )

    def _build_sample_status_line(self, outcome: SampleOutcome) -> str:
        status = "VALID" if outcome.identity.collection_valid else "INVALID"
        source_tokens = []
        for name, result in outcome.collector_results.items():
            value = "ok" if result.complete and result.valid else result.reject_reason or "invalid"
            source_tokens.append(f"{name}:{value}")
        reason = outcome.identity.collection_reject_reason or "-"
        return (
            f"sample={outcome.identity.sample_index} action={outcome.identity.requested_action.value} "
            f"collection={status} sources={' '.join(source_tokens)} reason={reason}"
        )

    def _build_session_status_line(self, summary: dict) -> str:
        collectors = ",".join(summary["selected_collectors"])
        return (
            f"session_complete valid_samples={summary['valid_samples_collected']} "
            f"invalid_attempts={summary['invalid_attempts']} "
            f"reject_reason_counts={summary['reject_reason_counts']} "
            f"selected_collectors={collectors} "
            f"semantic_targets={summary.get('semantic_target_summary', {}).get('target_counts', {})} "
            f"avg_sample_duration={summary['average_sample_duration_seconds']:.3f}s "
            f"output_dir={summary['session_dir']}"
        )

    def _choose_next_action(self, semantic_target_counts: dict[str, int]) -> CaptureCommand:
        if not self._semantic_targets_enabled():
            return self._planner.choose_next_action(self._state)
        targeted_action = self._choose_semantic_target_action(semantic_target_counts)
        if targeted_action is not None:
            return targeted_action
        return self._planner.choose_next_action(self._state)

    def _choose_semantic_target_action(self, semantic_target_counts: dict[str, int]) -> CaptureCommand | None:
        goal = self._semantic_target_goal()
        underfilled_targets = [
            target
            for target in self._config.semantic_targets
            if semantic_target_counts.get(self._semantic_target_label(target), 0) < goal
        ]
        if not underfilled_targets:
            return None
        for target in underfilled_targets:
            action = self._action_toward_state_target(target)
            if action is not None:
                return action
        return None

    def _action_toward_state_target(self, target: dict) -> CaptureCommand | None:
        enabled = set(self._config.commands)
        if target.get("power") == 1 and self._state.power == 0 and CaptureCommand.POWER_TOGGLE in enabled:
            return CaptureCommand.POWER_TOGGLE
        if target.get("power") == 0 and self._state.power == 1 and CaptureCommand.POWER_TOGGLE in enabled:
            return CaptureCommand.POWER_TOGGLE
        if self._state.power not in (1, None):
            return None

        flame_target = target.get("flame")
        if isinstance(flame_target, int) and self._state.flame is not None:
            if self._state.flame < flame_target and CaptureCommand.FLAME_UP in enabled:
                return CaptureCommand.FLAME_UP
            if self._state.flame > flame_target and CaptureCommand.FLAME_DOWN in enabled:
                return CaptureCommand.FLAME_DOWN

        fan_target = target.get("fan")
        if isinstance(fan_target, int) and self._state.fan is not None:
            if self._state.fan < fan_target and CaptureCommand.FAN_UP in enabled:
                return CaptureCommand.FAN_UP
            if self._state.fan > fan_target and CaptureCommand.FAN_DOWN in enabled:
                return CaptureCommand.FAN_DOWN
        return None

    def _canonical_state_from_collector_results(self, collector_results: dict) -> FireplaceState | None:
        rtl433 = collector_results.get("rtl433")
        metadata = rtl433.metadata if rtl433 is not None else {}
        if metadata.get("id") in (None, ""):
            return None
        expected_id = self._config.semantic_expected_id
        if expected_id and metadata.get("id") != expected_id:
            return None
        if metadata.get("integrity") != "CHECKSUM":
            return None
        return FireplaceState(
            power=metadata.get("power"),
            flame=metadata.get("flame"),
            fan=metadata.get("fan"),
        )

    def _semantic_targets_enabled(self) -> bool:
        return bool(self._config.semantic_targets and self._config.semantic_target_fields)

    def _semantic_target_label(self, target: dict) -> str:
        return ",".join(f"{field}={target.get(field)}" for field in self._config.semantic_target_fields)

    def _initial_semantic_target_counts(self) -> dict[str, int]:
        if not self._semantic_targets_enabled():
            return {}
        return {self._semantic_target_label(target): 0 for target in self._config.semantic_targets}

    def _semantic_target_goal(self) -> int:
        return self._config.semantic_replicates_per_target if self._config.semantic_replicates_per_target > 0 else 1

    def _target_reached(self, valid_samples: int, semantic_target_counts: dict[str, int]) -> bool:
        if not self._semantic_targets_enabled():
            return valid_samples >= self._config.valid_samples_target
        goal = self._semantic_target_goal()
        return bool(semantic_target_counts) and all(count >= goal for count in semantic_target_counts.values())

    def _evaluate_semantic_target(
        self,
        *,
        collector_results: dict,
        source_collection_valid: bool,
    ) -> dict:
        if not self._semantic_targets_enabled():
            return {
                "enabled": False,
                "accepted": True,
                "reject_reason": None,
                "matched_target_label": None,
            }
        result = {
            "enabled": True,
            "accepted": False,
            "reject_reason": None,
            "matched_target_label": None,
            "target_fields": list(self._config.semantic_target_fields),
            "targets": [dict(target) for target in self._config.semantic_targets],
            "observed": {},
        }
        if not source_collection_valid:
            result["reject_reason"] = "source_invalid"
            return result
        rtl433 = collector_results.get("rtl433")
        metadata = rtl433.metadata if rtl433 is not None else {}
        result["observed"] = {field: metadata.get(field) for field in self._config.semantic_target_fields}
        expected_id = self._config.semantic_expected_id
        if expected_id and metadata.get("id") != expected_id:
            result["reject_reason"] = "semantic_id_mismatch"
            return result
        if metadata.get("integrity") not in ("CHECKSUM",):
            result["reject_reason"] = "semantic_integrity_missing"
            return result
        for target in self._config.semantic_targets:
            if all(metadata.get(field) == target.get(field) for field in self._config.semantic_target_fields):
                result["accepted"] = True
                result["matched_target_label"] = self._semantic_target_label(target)
                return result
        result["reject_reason"] = "semantic_target_mismatch"
        return result

    def _evaluate_requested_action_transition(
        self,
        *,
        requested_action: CaptureCommand,
        state_before: FireplaceState,
        state_after: FireplaceState | None,
        source_collection_valid: bool,
    ) -> dict:
        result = {
            "enabled": True,
            "accepted": True,
            "reject_reason": None,
            "requested_action": requested_action.value,
            "state_before": state_before.to_manifest_dict(),
            "state_after": state_after.to_manifest_dict() if state_after is not None else None,
            "expected_delta": None,
            "observed_delta": None,
            "checked_field": None,
        }
        if not source_collection_valid or state_after is None:
            return result

        checks = {
            CaptureCommand.FLAME_UP: ("flame", 1),
            CaptureCommand.FLAME_DOWN: ("flame", -1),
            CaptureCommand.FAN_UP: ("fan", 1),
            CaptureCommand.FAN_DOWN: ("fan", -1),
        }
        if requested_action == CaptureCommand.POWER_TOGGLE:
            if state_before.power is None or state_after.power is None:
                return result
            result["checked_field"] = "power"
            result["expected_delta"] = "toggle"
            result["observed_delta"] = state_after.power - state_before.power
            if state_after.power == state_before.power:
                result["accepted"] = False
                result["reject_reason"] = "requested_action_transition_mismatch"
            return result

        check = checks.get(requested_action)
        if check is None:
            return result
        field, expected_delta = check
        before_value = getattr(state_before, field)
        after_value = getattr(state_after, field)
        result["checked_field"] = field
        result["expected_delta"] = expected_delta
        if before_value is None or after_value is None:
            return result
        observed_delta = after_value - before_value
        result["observed_delta"] = observed_delta
        if observed_delta != expected_delta:
            result["accepted"] = False
            result["reject_reason"] = "requested_action_transition_mismatch"
        return result

    def _semantic_target_summary(
        self,
        *,
        semantic_target_counts: dict[str, int],
        semantic_target_mismatches: int,
    ) -> dict:
        if not self._semantic_targets_enabled():
            return {"enabled": False}
        return {
            "enabled": True,
            "target_fields": list(self._config.semantic_target_fields),
            "targets": [dict(target) for target in self._config.semantic_targets],
            "replicates_per_target": self._semantic_target_goal(),
            "target_counts": dict(semantic_target_counts),
            "target_matches": sum(semantic_target_counts.values()),
            "target_mismatches": semantic_target_mismatches,
            "targets_satisfied": self._target_reached(0, semantic_target_counts),
        }

    def _should_gate_on_lilygo_marker(self) -> bool:
        if self._config.non_interactive:
            return False
        if self._is_rolling_capture_flow():
            return False
        return any(hasattr(collector, "await_sample_trigger") for collector in self._collectors)

    def _is_rolling_capture_flow(self) -> bool:
        return self._config.lilygo_capture_flow == "fifo_rolling_complete"

    def _capture_enable_mode_label(self) -> str:
        return "`fifo_trailing_window`"

    def _capture_complete_button_name(self) -> str:
        return "RX FIFO Capture Complete"

    def _await_sample_trigger(self) -> float | None:
        for collector in self._collectors:
            wait_for_trigger = getattr(collector, "await_sample_trigger", None)
            if callable(wait_for_trigger):
                return wait_for_trigger(
                    timeout_seconds=self._config.prearm_timeout_seconds,
                    poll_interval_seconds=self._config.poll_interval_seconds,
                )
        return None

    def _propagate_sample_anchor(self, host_monotonic: float) -> None:
        for collector in self._collectors:
            set_anchor = getattr(collector, "set_sample_anchor_monotonic", None)
            if callable(set_anchor):
                set_anchor(host_monotonic)

    def _average_sample_duration_seconds(self, outcomes: list[SampleOutcome]) -> float:
        from datetime import datetime

        durations: list[float] = []
        for outcome in outcomes:
            started = outcome.identity.coordinator_started_at_utc
            finished = outcome.identity.coordinator_finished_at_utc
            if not started or not finished:
                continue
            try:
                durations.append((datetime.fromisoformat(finished) - datetime.fromisoformat(started)).total_seconds())
            except ValueError:
                continue
        if not durations:
            return 0.0
        return sum(durations) / len(durations)

    def _write_operator_status(
        self,
        session_context: SessionContext,
        *,
        phase: str,
        message: str,
        sample_index: int | None,
        requested_action: str | None,
        extra: dict | None = None,
    ) -> None:
        payload = {
            "phase": phase,
            "message": message,
            "session_id": session_context.session_id,
            "session_dir": str(session_context.session_dir),
            "sample_index": sample_index,
            "requested_action": requested_action,
            "updated_at_utc": utc_now().isoformat(),
            "selected_collectors": [collector.source_name for collector in self._collectors],
        }
        if extra:
            payload.update(extra)
        _write_json(session_context.session_dir / "operator_status.json", payload)
        _write_json(self._config.output_root / "operator_status_latest.json", payload)
        actionable_prompt_phases = {
            "waiting_for_lilygo_marker",
            "rolling_capture_ready",
            "press_remote_now",
            "lilygo_marker_timeout",
            "sample_valid",
            "sample_invalid",
            "setup_state_valid",
            "setup_state_invalid",
            "session_complete",
        }
        if phase in actionable_prompt_phases:
            text_payload = self._render_operator_prompt_text(payload)
            _append_text(session_context.session_dir / "operator_prompt.txt", text_payload)
            _append_text(self._config.output_root / "operator_prompt_latest.txt", text_payload)
            if not self._config.non_interactive:
                self._status_handler(text_payload.rstrip())

    def _render_operator_prompt_text(self, payload: dict) -> str:
        phase = payload["phase"]
        sample_index = payload.get("sample_index")
        requested_action = payload.get("requested_action")
        updated_at = payload.get("updated_at_utc", "")
        starts_new_block = phase in {"waiting_for_lilygo_marker", "rolling_capture_ready", "session_complete"}
        lines: list[str] = []
        if starts_new_block:
            lines.extend(["", "===== PROMPT UPDATE ====="])
            if updated_at:
                lines.append(f"Updated: {updated_at}")
            lines.append(f"Phase: {phase}")
            if sample_index is not None:
                lines.append(f"Sample: {sample_index}")
            if requested_action:
                lines.append(f"Requested action: {requested_action}")
        else:
            if updated_at:
                lines.append(f"Updated: {updated_at}")
            lines.append(f"Phase: {phase}")
        if phase == "waiting_for_lilygo_marker":
            lines.append("Press the RX Capture Next Window button now.")
            lines.append("--> waiting for LilyGO capture-arm marker")
        elif phase == "rolling_capture_ready":
            lines.append(f"Ensure Enable Capture is set to {self._capture_enable_mode_label()} and leave it there.")
            lines.append(f"Now {payload['message']}")
            lines.append(f"After the RF burst finishes, press {self._capture_complete_button_name()} once.")
            lines.append("--> waiting for LilyGO rolling capture export")
        elif phase == "press_remote_now":
            lines.append("RX Capture Next Window marker seen.")
            lines.append(f"Now {payload['message']}")
            lines.append("--> waiting for requested remote action")
        elif phase == "lilygo_marker_timeout":
            lines.append("Timed out waiting for the RX Capture Next Window marker.")
            lines.append("--> attempt failed")
        elif phase == "sample_valid":
            lines.append("Sample collected successfully.")
            lines.append("--> sample valid")
        elif phase == "sample_invalid":
            lines.append("Sample invalid.")
            reject_reasons = payload.get("reject_reasons", [])
            if reject_reasons:
                lines.append(f"Reasons: {', '.join(reject_reasons)}")
            lines.append("--> attempt failed")
        elif phase == "session_complete":
            lines.append("Session finished.")
            lines.append(f"Valid samples: {payload.get('valid_samples_collected', 0)}")
            lines.append(f"Invalid attempts: {payload.get('invalid_attempts', 0)}")
            lines.append("--> session complete")
        else:
            lines.append(payload["message"])
            lines.append(f"--> {phase}")
        lines.append("")
        return "\n".join(lines)
