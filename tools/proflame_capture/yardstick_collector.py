"""YardStick diagnostic collector with injected and live receive-only modes."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from concurrent.futures import Future
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from custom_components.proflame2.rf.artifacts import is_yardstick_semantic_artifact

from .models import CollectorArtifact, CollectorResult, SampleContext, SessionContext


@dataclass
class YardStickDiagnosticResult:
    """Normalized YardStick transport/symbol diagnostic payload."""

    capture_complete: bool | None = None
    raw_payload_hex: str | None = None
    payload_length_bytes: int | None = None
    bit_stream: str | None = None
    symbol_stream: str | None = None
    decoded: str | None = None
    decoded_id: str | None = None
    decoded_cmd1: str | None = None
    decoded_cmd2: str | None = None
    decoded_err1: str | None = None
    decoded_err2: str | None = None
    decoded_fields: dict[str, object] | None = None
    decode_success: bool | None = None
    decode_failure_reason: str | None = None
    best_failure_reason: str | None = None
    reason_counts: dict[str, int] | None = None
    selected_bit_offset: int | None = None
    selected_symbol_offset: int | None = None
    repeat_count: int | None = None
    occurrence_offsets: list[list[int]] | None = None
    candidate_count: int | None = None
    artifact_layer: str | None = None
    symbol_stream_layer: str | None = None
    bit_stream_layer: str | None = None
    packet_normalized: bool | None = None
    contains_multiple_repeats: bool | None = None
    contains_partial_window: bool | None = None
    candidate_search_performed: bool | None = None
    candidate_windows_retained: bool | None = None
    selected_window_available: bool | None = None
    diagnostic_limitations: list[str] = field(default_factory=list)
    candidate_windows: list[dict[str, Any]] = field(default_factory=list)
    failed_candidate_windows: list[dict[str, Any]] = field(default_factory=list)
    best_candidate_window: dict[str, Any] | None = None
    selected_candidate_window: dict[str, Any] | None = None
    diagnostic_candidate_windows: list[dict[str, Any]] = field(default_factory=list)
    diagnostic_candidate_offsets: list[int] = field(default_factory=list)
    diagnostic_candidate_reason: str | None = None
    diagnostic_candidate_confidence: str | None = None
    active_frequency_hz: int | None = None
    rx_settings: dict[str, object] | None = None
    host_start_ns: int | None = None
    host_complete_ns: int | None = None
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    worker_diagnostics: dict[str, Any] | None = None
    source_backend: str | None = None
    semantic_artifact: dict[str, Any] | None = None
    semantic_comparable: bool = False
    artifact_class: str | None = None
    learning_attempt_count: int | None = None
    failed_attempt_count_before_success: int | None = None
    failed_attempts: list[dict[str, Any]] = field(default_factory=list)

    def has_useful_artifact(self) -> bool:
        return any(
            [
                bool(self.semantic_artifact),
                bool(self.raw_payload_hex),
                bool(self.bit_stream),
                bool(self.symbol_stream),
                bool(self.decoded),
                bool(self.decoded_fields),
                bool(self.candidate_windows),
                bool(self.failed_candidate_windows),
                bool(self.diagnostic_candidate_windows),
            ]
        )

    def has_semantic_artifact(self) -> bool:
        return is_yardstick_semantic_artifact(self.semantic_artifact)


class YardStickDiagnosticSource(Protocol):
    """Abstract source of YardStick diagnostics."""

    def start(self) -> None:
        """Initialize the source."""

    def stop(self) -> None:
        """Stop the source."""

    def begin_sample(self, *, timeout_seconds: float) -> None:
        """Start one sample receive window."""

    def poll(self) -> YardStickDiagnosticResult | None:
        """Return one diagnostic result when available."""


class InjectedYardStickDiagnosticSource:
    """Injected diagnostic source for unit tests."""

    def __init__(self, diagnostics: list[YardStickDiagnosticResult] | None = None) -> None:
        self._diagnostics = list(diagnostics or [])
        self._index = 0

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def begin_sample(self, *, timeout_seconds: float) -> None:
        return None

    def poll(self) -> YardStickDiagnosticResult | None:
        if self._index >= len(self._diagnostics):
            return None
        diagnostic = self._diagnostics[self._index]
        self._index += 1
        return diagnostic


class LiveYardStickDiagnosticSource:
    """Receive-only live YardStick source using the repo backend on a private loop."""

    def __init__(
        self,
        *,
        backend_factory: Any | None = None,
    ) -> None:
        self._backend_factory = backend_factory
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._sample_future: Future | None = None
        self._start_error: Exception | None = None
        self._stop_error: Exception | None = None
        self._closed = False

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run_loop, name="yardstick-live-source", daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 5.0
        while self._loop is None and time.monotonic() < deadline:
            time.sleep(0.01)
        if self._loop is None:
            raise RuntimeError("Failed to start YardStick live event loop.")

    def stop(self) -> None:
        self._closed = True
        if self._sample_future is not None and not self._sample_future.done():
            self._sample_future.cancel()
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._thread = None
        self._loop = None

    def begin_sample(self, *, timeout_seconds: float) -> None:
        if self._loop is None:
            raise RuntimeError("YardStick live loop is not running.")
        if self._sample_future is not None and not self._sample_future.done():
            raise RuntimeError("Previous YardStick live sample is still running.")
        self._start_error = None
        self._stop_error = None
        self._sample_future = asyncio.run_coroutine_threadsafe(
            self._receive_once(timeout_seconds=timeout_seconds),
            self._loop,
        )

    def poll(self) -> YardStickDiagnosticResult | None:
        if self._start_error is not None:
            error = self._start_error
            self._start_error = None
            raise error
        if self._sample_future is None or not self._sample_future.done():
            return None
        future = self._sample_future
        self._sample_future = None
        return future.result()

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()

    async def _receive_once(self, *, timeout_seconds: float) -> YardStickDiagnosticResult:
        backend = None
        try:
            backend = self._build_backend()
            await backend.connect()
            if hasattr(backend, "receive_learning_diagnostics"):
                result = await backend.receive_learning_diagnostics(timeout=timeout_seconds)
            else:
                result = await backend.receive_diagnostics(timeout=timeout_seconds)
            return _normalize_backend_diagnostics(
                result,
                worker_diagnostics=backend.serialize_worker_diagnostics(),
            )
        finally:
            if backend is not None:
                await backend.close(reason="capture_session_sample")

    def _build_backend(self):
        if self._backend_factory is not None:
            return self._backend_factory()
        from custom_components.proflame2.rf.yardstick import (
            YARDSTICK_RX_LEARNING_FREQUENCY_HZ,
            YARDSTICK_RX_LEARNING_PACKET_BYTES,
            YARDSTICK_RX_LEARNING_SWEEP_ENABLED,
            YardStickBackend,
        )

        return YardStickBackend(
            frequency_hz=YARDSTICK_RX_LEARNING_FREQUENCY_HZ,
            packet_length_bytes=YARDSTICK_RX_LEARNING_PACKET_BYTES,
            sweep_enabled=YARDSTICK_RX_LEARNING_SWEEP_ENABLED,
            worker_mode=True,
        )


@dataclass
class YardStickCollectorState:
    """Mutable state for one sample collection pass."""

    host_start_ns: int | None = None
    diagnostic: YardStickDiagnosticResult | None = None
    reject_reason: str | None = None
    complete: bool = False


class YardStickDiagnosticCollector:
    """Collector contract for YardStick transport/symbol diagnostics."""

    source_name = "yardstick"

    def __init__(
        self,
        *,
        mode: str = "live",
        source: YardStickDiagnosticSource | None = None,
    ) -> None:
        self._mode = mode
        self.source_mode = mode
        self._source = source or LiveYardStickDiagnosticSource()
        self._session_context: SessionContext | None = None
        self._sample_context: SampleContext | None = None
        self._state = YardStickCollectorState()

    def start_session(self, session_context: SessionContext) -> None:
        self._session_context = session_context
        self._source.start()

    def start_sample(self, sample_context: SampleContext) -> None:
        self._sample_context = sample_context
        self._state = YardStickCollectorState(host_start_ns=time.monotonic_ns())
        timeout_seconds = 6.0
        if self._session_context is not None:
            config = getattr(self._session_context, "config", None)
            if config is not None and getattr(config, "sample_timeout_seconds", None) is not None:
                timeout_seconds = float(config.sample_timeout_seconds)
        try:
            self._source.begin_sample(timeout_seconds=timeout_seconds)
        except Exception as exc:
            self._state.reject_reason = _map_live_error_to_reject_reason(exc)
            self._state.complete = True
            self._state.diagnostic = YardStickDiagnosticResult(
                capture_complete=False,
                host_start_ns=self._state.host_start_ns,
                host_complete_ns=time.monotonic_ns(),
                errors=[f"{type(exc).__name__}: {exc}"],
                source_backend="yardstick_backend",
            )

    def poll(self) -> None:
        if self._state.complete:
            return
        try:
            diagnostic = self._source.poll()
        except Exception as exc:
            self._state.reject_reason = _map_live_error_to_reject_reason(exc)
            self._state.diagnostic = YardStickDiagnosticResult(
                capture_complete=False,
                host_start_ns=self._state.host_start_ns,
                host_complete_ns=time.monotonic_ns(),
                errors=[f"{type(exc).__name__}: {exc}"],
                source_backend="yardstick_backend",
            )
            self._state.complete = True
            return
        if diagnostic is None:
            return
        if diagnostic.host_start_ns is None:
            diagnostic.host_start_ns = self._state.host_start_ns
        if diagnostic.host_complete_ns is None:
            diagnostic.host_complete_ns = time.monotonic_ns()
        self._state.diagnostic = diagnostic
        if self._is_malformed(diagnostic):
            self._state.reject_reason = "malformed_diagnostic"
            self._state.complete = True
            return
        if self._mode == "live" and not diagnostic.has_semantic_artifact():
            self._state.reject_reason = "no_semantic_artifact"
            self._state.complete = True
            return
        if diagnostic.has_useful_artifact():
            self._state.reject_reason = None
            self._state.complete = True
            return
        self._state.reject_reason = "no_useful_artifact"
        self._state.complete = True

    def is_complete(self) -> bool:
        return self._state.complete

    def finalize_sample(self, sample_context: SampleContext) -> CollectorResult:
        self.poll()
        if self._state.diagnostic is None and self._state.reject_reason is None:
            self._state.reject_reason = "receive_timeout" if self._mode == "live" else "timeout"
        artifacts = self.write_artifacts(sample_context.sample_dir)
        diagnostic = self._state.diagnostic
        if self._mode == "live":
            valid = diagnostic is not None and diagnostic.has_semantic_artifact() and self._state.reject_reason is None
        else:
            valid = diagnostic is not None and diagnostic.has_useful_artifact() and self._state.reject_reason is None
        return CollectorResult(
            source_name=self.source_name,
            complete=diagnostic is not None,
            valid=valid,
            reject_reason=self._state.reject_reason,
            artifact_paths=artifacts,
            metadata=self._build_diagnostic_payload(),
        )

    def write_artifacts(self, sample_dir: Path) -> tuple[CollectorArtifact, ...]:
        yardstick_dir = sample_dir / "yardstick"
        yardstick_dir.mkdir(parents=True, exist_ok=True)

        diagnostic_path = yardstick_dir / "diagnostic.json"
        diagnostic_path.write_text(
            json.dumps(self._build_diagnostic_payload(), indent=2, sort_keys=True),
            encoding="utf-8",
        )

        debug_path = yardstick_dir / "collector_debug.json"
        debug_path.write_text(
            json.dumps(
                {
                    "mode": self._mode,
                    "reject_reason": self._state.reject_reason,
                    "complete": self._state.complete,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )

        artifacts = [
            CollectorArtifact(path="yardstick/diagnostic.json", kind="yardstick_diagnostic"),
            CollectorArtifact(path="yardstick/collector_debug.json", kind="yardstick_debug"),
        ]

        diagnostic = self._state.diagnostic
        if diagnostic is not None and diagnostic.raw_payload_hex:
            raw_hex_path = yardstick_dir / "raw_payload.hex"
            raw_hex_path.write_text(diagnostic.raw_payload_hex + "\n", encoding="utf-8")
            artifacts.append(CollectorArtifact(path="yardstick/raw_payload.hex", kind="yardstick_raw_payload"))
        if diagnostic is not None and diagnostic.bit_stream:
            bit_stream_path = yardstick_dir / "bit_stream.txt"
            bit_stream_path.write_text(diagnostic.bit_stream + "\n", encoding="utf-8")
            artifacts.append(CollectorArtifact(path="yardstick/bit_stream.txt", kind="yardstick_bit_stream"))
        if diagnostic is not None and diagnostic.symbol_stream:
            symbol_stream_path = yardstick_dir / "symbol_stream.txt"
            symbol_stream_path.write_text(diagnostic.symbol_stream + "\n", encoding="utf-8")
            artifacts.append(CollectorArtifact(path="yardstick/symbol_stream.txt", kind="yardstick_symbol_stream"))
        if diagnostic is not None and diagnostic.decoded_fields:
            decoded_path = yardstick_dir / "decoded.json"
            decoded_path.write_text(
                json.dumps(diagnostic.decoded_fields, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            artifacts.append(CollectorArtifact(path="yardstick/decoded.json", kind="yardstick_decoded"))
        if diagnostic is not None and diagnostic.semantic_artifact:
            semantic_path = yardstick_dir / "semantic_artifact.json"
            semantic_path.write_text(
                json.dumps(diagnostic.semantic_artifact, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            artifacts.append(
                CollectorArtifact(path="yardstick/semantic_artifact.json", kind="yardstick_semantic_artifact")
            )
            semantic_symbol_stream = diagnostic.semantic_artifact.get("candidate_symbol_stream")
            semantic_bit_stream = diagnostic.semantic_artifact.get("candidate_bit_stream")
            if semantic_symbol_stream:
                path = yardstick_dir / "semantic_symbol_stream.txt"
                path.write_text(str(semantic_symbol_stream) + "\n", encoding="utf-8")
                artifacts.append(
                    CollectorArtifact(
                        path="yardstick/semantic_symbol_stream.txt", kind="yardstick_semantic_symbol_stream"
                    )
                )
            if semantic_bit_stream:
                path = yardstick_dir / "semantic_bit_stream.txt"
                path.write_text(str(semantic_bit_stream) + "\n", encoding="utf-8")
                artifacts.append(
                    CollectorArtifact(path="yardstick/semantic_bit_stream.txt", kind="yardstick_semantic_bit_stream")
                )
            semantic_decoded = {
                key: diagnostic.semantic_artifact.get(key)
                for key in ("remote_id", "id", "cmd1", "cmd2", "err1", "err2", "power", "flame", "fan")
                if key in diagnostic.semantic_artifact
            }
            semantic_decoded_path = yardstick_dir / "semantic_decoded.json"
            semantic_decoded_path.write_text(
                json.dumps(semantic_decoded, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            artifacts.append(
                CollectorArtifact(path="yardstick/semantic_decoded.json", kind="yardstick_semantic_decoded")
            )
        if diagnostic is not None and diagnostic.failed_attempts:
            debug_failures_path = yardstick_dir / "debug_failures.json"
            debug_failures_path.write_text(
                json.dumps(diagnostic.failed_attempts, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            artifacts.append(CollectorArtifact(path="yardstick/debug_failures.json", kind="yardstick_debug_failures"))
        if diagnostic is not None and (
            diagnostic.candidate_windows
            or diagnostic.failed_candidate_windows
            or diagnostic.diagnostic_candidate_windows
        ):
            candidate_payload = {
                "candidate_windows": diagnostic.candidate_windows,
                "failed_candidate_windows": diagnostic.failed_candidate_windows,
                "best_candidate_window": diagnostic.best_candidate_window,
                "selected_candidate_window": diagnostic.selected_candidate_window,
                "diagnostic_candidate_windows": diagnostic.diagnostic_candidate_windows,
                "diagnostic_candidate_offsets": diagnostic.diagnostic_candidate_offsets,
                "diagnostic_candidate_reason": diagnostic.diagnostic_candidate_reason,
                "diagnostic_candidate_confidence": diagnostic.diagnostic_candidate_confidence,
            }
            candidate_windows_path = yardstick_dir / "candidate_windows.json"
            candidate_windows_path.write_text(
                json.dumps(candidate_payload, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            artifacts.append(
                CollectorArtifact(path="yardstick/candidate_windows.json", kind="yardstick_candidate_windows")
            )
            for index, candidate in enumerate(diagnostic.candidate_windows[:3]):
                symbol_stream = candidate.get("symbol_stream")
                bit_stream = candidate.get("bit_stream")
                if symbol_stream:
                    path = yardstick_dir / f"candidate_{index:03d}_symbol_stream.txt"
                    path.write_text(str(symbol_stream) + "\n", encoding="utf-8")
                    artifacts.append(
                        CollectorArtifact(
                            path=f"yardstick/candidate_{index:03d}_symbol_stream.txt",
                            kind="yardstick_candidate_symbol_stream",
                        )
                    )
                if bit_stream:
                    path = yardstick_dir / f"candidate_{index:03d}_bit_stream.txt"
                    path.write_text(str(bit_stream) + "\n", encoding="utf-8")
                    artifacts.append(
                        CollectorArtifact(
                            path=f"yardstick/candidate_{index:03d}_bit_stream.txt",
                            kind="yardstick_candidate_bit_stream",
                        )
                    )
        return tuple(artifacts)

    def close(self) -> None:
        self._source.stop()

    def _build_diagnostic_payload(self) -> dict[str, object]:
        payload = {"mode": self._mode, "reject_reason": self._state.reject_reason}
        diagnostic = self._state.diagnostic
        if diagnostic is None:
            payload.update({"diagnostic_present": False})
        else:
            payload.update(asdict(diagnostic))
            payload["diagnostic_present"] = True
        return payload

    def _is_malformed(self, diagnostic: YardStickDiagnosticResult) -> bool:
        if diagnostic.raw_payload_hex is not None and not all(
            character in "0123456789abcdefABCDEF" for character in diagnostic.raw_payload_hex
        ):
            return True
        return False


def _normalize_backend_diagnostics(result, *, worker_diagnostics: dict[str, Any] | None) -> YardStickDiagnosticResult:
    sample = result.sample
    decoded_fields = None
    if result.decoded_fields is not None:
        decoded_fields = dict(result.decoded_fields)
    elif sample is not None:
        decoded_fields = {
            "remote_id": sample.remote_id,
            "id": f"{sample.remote_id:06x}",
            "cmd1": f"{sample.cmd1:02x}",
            "cmd2": f"{sample.cmd2:02x}",
            "err1": f"{sample.err1:02x}",
            "err2": f"{sample.err2:02x}",
        }
    return YardStickDiagnosticResult(
        capture_complete=result.capture_complete,
        raw_payload_hex=result.raw_payload_hex,
        payload_length_bytes=result.payload_length_bytes,
        bit_stream=result.bit_stream,
        symbol_stream=result.symbol_stream,
        decoded=None if sample is None else f"{sample.remote_id:06x}:{sample.cmd1:02x}:{sample.cmd2:02x}",
        decoded_id=None if sample is None else f"{sample.remote_id:06x}",
        decoded_cmd1=None if sample is None else f"{sample.cmd1:02x}",
        decoded_cmd2=None if sample is None else f"{sample.cmd2:02x}",
        decoded_err1=None if sample is None else f"{sample.err1:02x}",
        decoded_err2=None if sample is None else f"{sample.err2:02x}",
        decoded_fields=decoded_fields,
        decode_success=result.decode_success,
        decode_failure_reason=result.decode_failure_reason,
        best_failure_reason=result.best_failure_reason,
        reason_counts=result.reason_counts,
        selected_bit_offset=result.selected_bit_offset,
        selected_symbol_offset=result.selected_symbol_offset,
        repeat_count=result.repeat_count,
        occurrence_offsets=[list(offset) for offset in result.occurrence_offsets],
        candidate_count=result.candidate_count,
        semantic_artifact=getattr(result, "semantic_artifact", None),
        semantic_comparable=getattr(result, "semantic_comparable", False),
        artifact_class=getattr(result, "artifact_class", None),
        learning_attempt_count=getattr(result, "learning_attempt_count", None),
        failed_attempt_count_before_success=getattr(result, "failed_attempt_count_before_success", None),
        failed_attempts=[dict(attempt) for attempt in getattr(result, "failed_attempts", ()) or []],
        artifact_layer=getattr(result, "artifact_layer", None),
        symbol_stream_layer=getattr(result, "symbol_stream_layer", None),
        bit_stream_layer=getattr(result, "bit_stream_layer", None),
        packet_normalized=getattr(result, "packet_normalized", None),
        contains_multiple_repeats=getattr(result, "contains_multiple_repeats", None),
        contains_partial_window=getattr(result, "contains_partial_window", None),
        candidate_search_performed=getattr(result, "candidate_search_performed", None),
        candidate_windows_retained=getattr(result, "candidate_windows_retained", None),
        selected_window_available=getattr(result, "selected_window_available", None),
        diagnostic_limitations=list(getattr(result, "diagnostic_limitations", ()) or []),
        candidate_windows=[dict(window) for window in getattr(result, "candidate_windows", ()) or []],
        failed_candidate_windows=[dict(window) for window in getattr(result, "failed_candidate_windows", ()) or []],
        best_candidate_window=getattr(result, "best_candidate_window", None),
        selected_candidate_window=getattr(result, "selected_candidate_window", None),
        diagnostic_candidate_windows=[
            dict(window) for window in getattr(result, "diagnostic_candidate_windows", ()) or []
        ],
        diagnostic_candidate_offsets=list(getattr(result, "diagnostic_candidate_offsets", ()) or []),
        diagnostic_candidate_reason=getattr(result, "diagnostic_candidate_reason", None),
        diagnostic_candidate_confidence=getattr(result, "diagnostic_candidate_confidence", None),
        active_frequency_hz=result.active_frequency_hz,
        rx_settings=result.rx_settings,
        host_start_ns=result.host_start_ns,
        host_complete_ns=result.host_complete_ns,
        notes=[],
        errors=[],
        worker_diagnostics=worker_diagnostics,
        source_backend="yardstick_backend",
    )


def _map_live_error_to_reject_reason(exc: BaseException) -> str:
    message = str(exc).lower()
    if "permission" in message or "access was denied" in message:
        return "backend_open_failed"
    if "device was found" in message or "device not found" in message:
        return "backend_open_failed"
    if "timed out" in message:
        return "receive_timeout"
    if "shutdown" in message or "cleanup" in message:
        return "cleanup_failed"
    return "backend_error"
