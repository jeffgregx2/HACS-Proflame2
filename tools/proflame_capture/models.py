"""Models for prompt-driven Proflame2 capture sessions."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any


class CaptureCommand(StrEnum):
    """Supported operator actions for a capture sample."""

    SETUP_STATE = "setup_state"
    POWER_TOGGLE = "power_toggle"
    FLAME_UP = "flame_up"
    FLAME_DOWN = "flame_down"
    FAN_UP = "fan_up"
    FAN_DOWN = "fan_down"


def utc_now() -> datetime:
    """Return the current UTC timestamp."""

    return datetime.now(timezone.utc)


def utc_timestamp_id(now: datetime | None = None) -> str:
    """Return one robust UTC timestamp identifier."""

    value = now or utc_now()
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


@dataclass(frozen=True)
class CaptureSessionConfig:
    """Configurable session settings for orchestration."""

    valid_samples_target: int = 10
    max_attempts: int = 20
    echo_capture_enabled: bool = False
    commands: tuple[CaptureCommand, ...] = (
        CaptureCommand.POWER_TOGGLE,
        CaptureCommand.FLAME_UP,
        CaptureCommand.FLAME_DOWN,
        CaptureCommand.FAN_UP,
        CaptureCommand.FAN_DOWN,
    )
    command_plan: tuple[CaptureCommand, ...] = ()
    semantic_targets: tuple[dict[str, Any], ...] = ()
    semantic_target_fields: tuple[str, ...] = ()
    semantic_replicates_per_target: int = 0
    semantic_expected_id: str | None = None
    initial_state: FireplaceState = field(default_factory=lambda: FireplaceState())
    flame_min: int = 0
    flame_max: int = 6
    fan_min: int = 0
    fan_max: int = 6
    output_root: Path = Path("analysis/captures")
    source_names: tuple[str, ...] = ("lilygo", "yardstick", "rtl433")
    prearm_timeout_seconds: float = 30.0
    sample_timeout_seconds: float = 6.0
    poll_interval_seconds: float = 0.01
    lilygo_capture_flow: str = "fifo_rolling_complete"
    setup_state_sample: bool = False
    non_interactive: bool = False
    dry_run: bool = False

    def to_manifest_dict(self) -> dict[str, Any]:
        """Return one JSON-friendly config mapping."""

        data = asdict(self)
        data["commands"] = [command.value for command in self.commands]
        data["command_plan"] = [command.value for command in self.command_plan]
        data["semantic_targets"] = [dict(target) for target in self.semantic_targets]
        data["semantic_target_fields"] = list(self.semantic_target_fields)
        data["initial_state"] = self.initial_state.to_manifest_dict()
        data["output_root"] = str(self.output_root)
        return data


@dataclass(frozen=True)
class FireplaceState:
    """Minimal fireplace state used by the orchestration planner."""

    power: int | None = None
    flame: int | None = None
    fan: int | None = None

    def to_manifest_dict(self) -> dict[str, Any]:
        """Return one JSON-friendly state mapping."""

        return {
            "power": self.power if self.power is not None else "unknown",
            "flame": self.flame if self.flame is not None else "unknown",
            "fan": self.fan if self.fan is not None else "unknown",
        }


@dataclass(frozen=True)
class SampleIdentity:
    """Stable identity and bookkeeping for one sample attempt."""

    session_id: str
    sample_id: str
    sample_index: int
    attempt_index: int
    requested_action: CaptureCommand
    operator_prompt: str
    coordinator_started_at_utc: str
    coordinator_finished_at_utc: str | None = None
    source_status: dict[str, str] = field(default_factory=dict)
    collection_valid: bool = False
    collection_reject_reason: str | None = None
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_manifest_dict(self) -> dict[str, Any]:
        """Return one JSON-friendly identity mapping."""

        data = asdict(self)
        data["requested_action"] = self.requested_action.value
        return data


def build_sample_id(session_id: str, sample_index: int, attempt_index: int) -> str:
    """Return one unique sample identifier."""

    return f"{session_id}-s{sample_index:03d}-a{attempt_index:03d}"


@dataclass(frozen=True)
class SessionContext:
    """Runtime context for one capture session."""

    session_id: str
    session_dir: Path
    config: CaptureSessionConfig
    started_at_utc: str

    def to_manifest_dict(self) -> dict[str, Any]:
        """Return one JSON-friendly session mapping."""

        return {
            "session_id": self.session_id,
            "session_dir": str(self.session_dir),
            "started_at_utc": self.started_at_utc,
            "config": self.config.to_manifest_dict(),
        }


@dataclass(frozen=True)
class SampleContext:
    """Runtime context for one sample attempt."""

    identity: SampleIdentity
    sample_dir: Path
    state_before: FireplaceState


@dataclass(frozen=True)
class CollectorArtifact:
    """One collector-produced artifact."""

    path: str
    kind: str


@dataclass(frozen=True)
class CollectorResult:
    """Final status for one collector on one sample."""

    source_name: str
    complete: bool
    selected: bool = True
    mode: str | None = None
    valid: bool = True
    reject_reason: str | None = None
    notes: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    artifact_paths: tuple[CollectorArtifact, ...] = ()
    artifact_dir: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_manifest_dict(self) -> dict[str, Any]:
        """Return one JSON-friendly collector result mapping."""

        return {
            "source_name": self.source_name,
            "selected": self.selected,
            "mode": self.mode,
            "complete": self.complete,
            "valid": self.valid,
            "reject_reason": self.reject_reason,
            "notes": list(self.notes),
            "errors": list(self.errors),
            "artifact_paths": [asdict(item) for item in self.artifact_paths],
            "artifact_dir": self.artifact_dir,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class SampleOutcome:
    """Final persisted result for one sample attempt."""

    identity: SampleIdentity
    collector_results: dict[str, CollectorResult]
    state_before: FireplaceState
    state_after: FireplaceState

    def to_manifest_dict(self) -> dict[str, Any]:
        """Return one JSON-friendly sample outcome mapping."""

        return {
            "identity": self.identity.to_manifest_dict(),
            "state_before": self.state_before.to_manifest_dict(),
            "state_after": self.state_after.to_manifest_dict(),
            "collector_results": {name: result.to_manifest_dict() for name, result in self.collector_results.items()},
        }
