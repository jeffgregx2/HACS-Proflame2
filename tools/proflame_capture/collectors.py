"""Collector interfaces and stub implementations for session orchestration."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .models import CollectorArtifact, CollectorResult, SampleContext, SessionContext


class Collector(Protocol):
    """Abstract interface for one capture data source."""

    source_name: str

    def start_session(self, session_context: SessionContext) -> None:
        """Initialize session-wide collector state."""

    def start_sample(self, sample_context: SampleContext) -> None:
        """Start one sample collection pass."""

    def poll(self) -> None:
        """Advance one sample collection pass."""

    def is_complete(self) -> bool:
        """Return whether the collector has finished the current sample."""

    def finalize_sample(self, sample_context: SampleContext) -> CollectorResult:
        """Return one finalized sample result."""

    def write_artifacts(self, sample_dir: Path) -> tuple[CollectorArtifact, ...]:
        """Persist sample artifacts and return their metadata."""


@dataclass
class StubCollector:
    """Collector stub for orchestration tests and dry runs."""

    source_name: str
    source_mode: str = "stub"
    polls_to_complete: int = 1
    should_complete: bool = True
    should_validate: bool = True
    artifact_suffix: str = "json"
    _session_context: SessionContext | None = field(default=None, init=False, repr=False)
    _sample_context: SampleContext | None = field(default=None, init=False, repr=False)
    _poll_count: int = field(default=0, init=False, repr=False)

    def start_session(self, session_context: SessionContext) -> None:
        self._session_context = session_context

    def start_sample(self, sample_context: SampleContext) -> None:
        self._sample_context = sample_context
        self._poll_count = 0

    def poll(self) -> None:
        if self.should_complete and self._poll_count < self.polls_to_complete:
            self._poll_count += 1

    def is_complete(self) -> bool:
        return self.should_complete and self._poll_count >= self.polls_to_complete

    def finalize_sample(self, sample_context: SampleContext) -> CollectorResult:
        artifacts = self.write_artifacts(sample_context.sample_dir)
        reject_reason = None
        if not self.is_complete():
            reject_reason = "collector_incomplete"
        elif not self.should_validate:
            reject_reason = "collector_marked_invalid"
        return CollectorResult(
            source_name=self.source_name,
            complete=self.is_complete(),
            valid=self.is_complete() and self.should_validate,
            reject_reason=reject_reason,
            artifact_paths=artifacts,
            metadata={
                "stub": True,
                "poll_count": self._poll_count,
                "polls_to_complete": self.polls_to_complete,
            },
        )

    def write_artifacts(self, sample_dir: Path) -> tuple[CollectorArtifact, ...]:
        payload = {
            "source_name": self.source_name,
            "stub": True,
            "complete": self.is_complete(),
            "poll_count": self._poll_count,
        }
        artifact_path = sample_dir / f"{self.source_name}_stub.{self.artifact_suffix}"
        artifact_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return (
            CollectorArtifact(
                path=str(artifact_path.relative_to(sample_dir)),
                kind="stub_status",
            ),
        )


class StubLilyGoCollector(StubCollector):
    """Stub collector for LilyGO capture coordination."""

    def __init__(self, **kwargs) -> None:
        super().__init__(source_name="lilygo", **kwargs)


class StubYardStickCollector(StubCollector):
    """Stub collector for YardStick capture coordination."""

    def __init__(self, **kwargs) -> None:
        super().__init__(source_name="yardstick", **kwargs)


class StubRtl433Collector(StubCollector):
    """Stub collector for rtl_433 capture coordination."""

    def __init__(self, **kwargs) -> None:
        super().__init__(source_name="rtl433", **kwargs)
