"""rtl_433 subprocess collector for coordinated Proflame2 capture sessions."""

from __future__ import annotations

import fcntl
import json
import os
import re
import selectors
import signal
import subprocess
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .models import CollectorArtifact, CollectorResult, SampleContext, SessionContext, utc_now

PROFLAME_MODEL = "Proflame2-Remote"
COMMENT_LABEL_PREFIX = "# rtl_433 - "
FIELD_RE = re.compile(r"([A-Za-z0-9_]+)\s*:\s*(.*?)(?=(?:\s{2,}[A-Za-z][A-Za-z0-9_]*\s*:)|$)")


@dataclass(frozen=True)
class Rtl433OutputLine:
    """One rtl_433 output line with host timing metadata."""

    line: str
    stream: str
    host_monotonic: float
    host_received_at_utc: str


class Rtl433LineSource(Protocol):
    """Abstract source of rtl_433 stdout/stderr lines."""

    def start(self) -> None:
        """Initialize the source."""

    def stop(self) -> None:
        """Stop the source and release resources."""

    def recv_lines(self) -> list[Rtl433OutputLine]:
        """Return any currently available output lines."""

    def is_running(self) -> bool:
        """Return whether the underlying source is still alive."""

    def exit_code(self) -> int | None:
        """Return the underlying exit code when available."""


class InjectedRtl433Source:
    """Injected line source for tests."""

    def __init__(self, lines: list[Rtl433OutputLine] | None = None) -> None:
        self._lines: deque[Rtl433OutputLine] = deque(lines or [])
        self._running = True
        self._exit_code: int | None = None

    def start(self) -> None:
        return None

    def stop(self) -> None:
        self._running = False

    def feed_line(
        self,
        line: str,
        *,
        stream: str = "stdout",
        host_monotonic: float | None = None,
        host_received_at_utc: str | None = None,
    ) -> None:
        self._lines.append(
            Rtl433OutputLine(
                line=line,
                stream=stream,
                host_monotonic=time.monotonic() if host_monotonic is None else host_monotonic,
                host_received_at_utc=utc_now().isoformat() if host_received_at_utc is None else host_received_at_utc,
            )
        )

    def set_running(self, running: bool, *, exit_code: int | None = None) -> None:
        self._running = running
        self._exit_code = exit_code

    def recv_lines(self) -> list[Rtl433OutputLine]:
        if not self._lines:
            return []
        return [self._lines.popleft()]

    def is_running(self) -> bool:
        return self._running

    def exit_code(self) -> int | None:
        return self._exit_code


class Rtl433ProcessSource:
    """Session-wide non-blocking rtl_433 subprocess source."""

    def __init__(self, command: list[str]) -> None:
        self._command = command
        self._process: subprocess.Popen[bytes] | None = None
        self._selector: selectors.BaseSelector | None = None
        self._buffers: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
        self._closed_streams: set[str] = set()

    def start(self) -> None:
        if self._process is not None:
            return
        process = subprocess.Popen(
            self._command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            bufsize=0,
        )
        selector = selectors.DefaultSelector()
        if process.stdout is not None:
            os.set_blocking(process.stdout.fileno(), False)
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        if process.stderr is not None:
            os.set_blocking(process.stderr.fileno(), False)
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        self._process = process
        self._selector = selector

    def stop(self) -> None:
        if self._process is None:
            return
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=2.0)
        if self._selector is not None:
            self._selector.close()
            self._selector = None
        self._process = None

    def recv_lines(self) -> list[Rtl433OutputLine]:
        if self._process is None or self._selector is None:
            return []
        lines: list[Rtl433OutputLine] = []
        for key, _mask in self._selector.select(timeout=0):
            stream = key.data
            chunk = os.read(key.fileobj.fileno(), 65536)
            if not chunk:
                self._flush_partial(stream, lines)
                self._selector.unregister(key.fileobj)
                self._closed_streams.add(stream)
                continue
            self._buffers[stream].extend(chunk)
            lines.extend(self._extract_complete_lines(stream))
        if self._process.poll() is not None:
            for stream in ("stdout", "stderr"):
                self._flush_partial(stream, lines)
        return lines

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def exit_code(self) -> int | None:
        return None if self._process is None else self._process.poll()

    def _extract_complete_lines(self, stream: str) -> list[Rtl433OutputLine]:
        lines: list[Rtl433OutputLine] = []
        buffer = self._buffers[stream]
        while True:
            newline_index = buffer.find(b"\n")
            if newline_index < 0:
                break
            chunk = bytes(buffer[:newline_index])
            del buffer[: newline_index + 1]
            line = chunk.decode("utf-8", errors="replace").rstrip("\r")
            lines.append(
                Rtl433OutputLine(
                    line=line,
                    stream=stream,
                    host_monotonic=time.monotonic(),
                    host_received_at_utc=utc_now().isoformat(),
                )
            )
        return lines

    def _flush_partial(self, stream: str, lines: list[Rtl433OutputLine]) -> None:
        buffer = self._buffers[stream]
        if not buffer:
            return
        line = bytes(buffer).decode("utf-8", errors="replace").rstrip("\r")
        buffer.clear()
        lines.append(
            Rtl433OutputLine(
                line=line,
                stream=stream,
                host_monotonic=time.monotonic(),
                host_received_at_utc=utc_now().isoformat(),
            )
        )


@dataclass
class ParsedRtl433Block:
    """Parsed rtl_433 text block for one sample candidate."""

    label: str | None
    fields: dict[str, object]
    first_seen_monotonic: float
    last_seen_monotonic: float
    first_seen_utc: str
    last_seen_utc: str
    raw_lines: list[str]
    valid: bool
    reject_reason: str | None = None

    def to_debug_dict(self) -> dict[str, object]:
        payload = {
            "label": self.label,
            "fields": self.fields,
            "first_seen_monotonic": self.first_seen_monotonic,
            "last_seen_monotonic": self.last_seen_monotonic,
            "first_seen_utc": self.first_seen_utc,
            "last_seen_utc": self.last_seen_utc,
            "raw_lines": self.raw_lines,
            "valid": self.valid,
            "reject_reason": self.reject_reason,
        }
        return payload


@dataclass
class _PendingBlock:
    label: str | None = None
    lines: list[str] = field(default_factory=list)
    first_seen_monotonic: float | None = None
    last_seen_monotonic: float | None = None
    first_seen_utc: str | None = None
    last_seen_utc: str | None = None


@dataclass
class Rtl433CollectorState:
    """Accumulated session/sample state for rtl_433 collection."""

    sample_started_monotonic: float | None = None
    raw_stdout: list[Rtl433OutputLine] = field(default_factory=list)
    raw_stderr: list[Rtl433OutputLine] = field(default_factory=list)
    pending_label: str | None = None
    pending_block: _PendingBlock = field(default_factory=_PendingBlock)
    parsed_blocks: list[ParsedRtl433Block] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)
    selected_block_index: int | None = None
    selected_reason: str | None = None
    reject_reason: str | None = None
    complete: bool = False


class Rtl433OwnershipError(RuntimeError):
    """Raised when another live session already owns the rtl_433 SDR process."""


class Rtl433Collector:
    """Real rtl_433 collector using a session-wide subprocess or injected lines."""

    source_name = "rtl433"

    def __init__(
        self,
        *,
        executable_path: str = "/usr/local/bin/rtl_433",
        frequency: str = "315M",
        gain: str = "40",
        protocol: str = "207",
        extra_args: list[str] | None = None,
        source: Rtl433LineSource | None = None,
        lock_path: str | Path = "/tmp/proflame_capture_rtl433.lock",
    ) -> None:
        command = [
            executable_path,
            "-f",
            frequency,
            "-g",
            gain,
            "-R",
            protocol,
        ]
        if extra_args:
            command.extend(extra_args)
        self._command = command
        self._source = source or Rtl433ProcessSource(command)
        self.source_mode = "injected" if source is not None else "subprocess"
        self._lock_path = Path(lock_path)
        self._lock_file = None
        self._session_context: SessionContext | None = None
        self._sample_context: SampleContext | None = None
        self._state = Rtl433CollectorState()

    def start_session(self, session_context: SessionContext) -> None:
        self._session_context = session_context
        self._acquire_runtime_lock()
        try:
            self._cleanup_stale_rtl433_processes()
            self._source.start()
        except Exception:
            self._release_runtime_lock()
            raise

    def start_sample(self, sample_context: SampleContext) -> None:
        self._sample_context = sample_context
        self._state = Rtl433CollectorState(sample_started_monotonic=time.monotonic())

    def poll(self) -> None:
        for output in self._source.recv_lines():
            if output.stream == "stdout":
                self._state.raw_stdout.append(output)
                self._feed_stdout_line(output)
            else:
                self._state.raw_stderr.append(output)
        self._select_candidate()

    def is_complete(self) -> bool:
        return self._state.complete

    def finalize_sample(self, sample_context: SampleContext) -> CollectorResult:
        self.poll()
        self._finalize_pending_block()
        self._select_candidate()
        if self._state.selected_block_index is None:
            self._state.reject_reason = self._infer_reject_reason()
        artifacts = self.write_artifacts(sample_context.sample_dir)
        selected = self._selected_block()
        return CollectorResult(
            source_name=self.source_name,
            complete=self._state.selected_block_index is not None,
            valid=self._state.selected_block_index is not None,
            reject_reason=self._state.reject_reason,
            artifact_paths=artifacts,
            metadata=self._build_decoded_payload(selected),
        )

    def write_artifacts(self, sample_dir: Path) -> tuple[CollectorArtifact, ...]:
        rtl433_dir = sample_dir / "rtl433"
        rtl433_dir.mkdir(parents=True, exist_ok=True)

        stdout_path = rtl433_dir / "raw_stdout.log"
        stdout_path.write_text(
            "\n".join(line.line for line in self._state.raw_stdout) + ("\n" if self._state.raw_stdout else ""),
            encoding="utf-8",
        )

        decoded_path = rtl433_dir / "decoded.json"
        selected = self._selected_block()
        decoded_path.write_text(
            json.dumps(self._build_decoded_payload(selected), indent=2, sort_keys=True),
            encoding="utf-8",
        )

        debug_path = rtl433_dir / "parser_debug.json"
        debug_path.write_text(
            json.dumps(self._build_parser_debug_payload(), indent=2, sort_keys=True),
            encoding="utf-8",
        )

        artifacts = [
            CollectorArtifact(path="rtl433/raw_stdout.log", kind="rtl433_stdout"),
            CollectorArtifact(path="rtl433/decoded.json", kind="rtl433_decoded"),
            CollectorArtifact(path="rtl433/parser_debug.json", kind="rtl433_parser_debug"),
        ]

        if self._state.raw_stderr:
            stderr_path = rtl433_dir / "stderr.log"
            stderr_path.write_text(
                "\n".join(line.line for line in self._state.raw_stderr) + "\n",
                encoding="utf-8",
            )
            artifacts.append(CollectorArtifact(path="rtl433/stderr.log", kind="rtl433_stderr"))

        return tuple(artifacts)

    def close(self) -> None:
        try:
            self._source.stop()
        finally:
            self._release_runtime_lock()

    def _acquire_runtime_lock(self) -> None:
        if self.source_mode != "subprocess" or self._lock_file is not None:
            return
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = self._lock_path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            lock_file.seek(0)
            owner = lock_file.read().strip()
            lock_file.close()
            owner_suffix = f" ({owner})" if owner else ""
            raise Rtl433OwnershipError(
                "rtl_433 SDR already in use by another capture session"
                f"{owner_suffix}. Stop the other session before starting a new one."
            ) from exc
        session_id = self._session_context.session_id if self._session_context is not None else "unknown"
        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(f"pid={os.getpid()} session_id={session_id} command={' '.join(self._command)}")
        lock_file.flush()
        self._lock_file = lock_file

    def _release_runtime_lock(self) -> None:
        if self._lock_file is None:
            return
        try:
            self._lock_file.seek(0)
            self._lock_file.truncate()
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            self._lock_file.close()
            self._lock_file = None

    def _cleanup_stale_rtl433_processes(self) -> None:
        if self.source_mode != "subprocess":
            return
        stale_pids = self._find_stale_rtl433_pids()
        if not stale_pids:
            return
        for pid in stale_pids:
            self._terminate_pid(pid, signal.SIGTERM)
        deadline = time.monotonic() + 2.0
        remaining = stale_pids
        while remaining and time.monotonic() < deadline:
            time.sleep(0.05)
            active = set(self._find_stale_rtl433_pids())
            remaining = [pid for pid in stale_pids if pid in active]
        for pid in remaining:
            self._terminate_pid(pid, signal.SIGKILL)

    def _find_stale_rtl433_pids(self) -> list[int]:
        executable = self._command[0]
        try:
            result = subprocess.run(
                ["ps", "-eo", "pid=,args="],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.SubprocessError:
            return []
        stale_pids: list[int] = []
        current_pid = os.getpid()
        for raw_line in result.stdout.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            pid_text, _sep, args = line.partition(" ")
            if not pid_text or not args:
                continue
            try:
                pid = int(pid_text)
            except ValueError:
                continue
            if pid == current_pid:
                continue
            if executable not in args:
                continue
            stale_pids.append(pid)
        return stale_pids

    def _terminate_pid(self, pid: int, sig: signal.Signals) -> None:
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            return

    def _feed_stdout_line(self, output: Rtl433OutputLine) -> None:
        line = output.line
        if line.startswith(COMMENT_LABEL_PREFIX):
            self._state.pending_label = line.removeprefix(COMMENT_LABEL_PREFIX).strip()
            return
        if not line.strip():
            self._finalize_pending_block()
            return
        if not self._state.pending_block.lines and not line.startswith("time"):
            return
        if line.lstrip().startswith("{"):
            self._state.parse_errors.append("json_output_not_supported_in_text_parser")
            return
        if line.startswith("time") and self._state.pending_block.lines:
            self._finalize_pending_block()
        pending = self._state.pending_block
        if not pending.lines:
            pending.label = self._state.pending_label
            self._state.pending_label = None
            pending.first_seen_monotonic = output.host_monotonic
            pending.first_seen_utc = output.host_received_at_utc
        pending.lines.append(line)
        pending.last_seen_monotonic = output.host_monotonic
        pending.last_seen_utc = output.host_received_at_utc

    def _finalize_pending_block(self) -> None:
        pending = self._state.pending_block
        if not pending.lines:
            return
        try:
            parsed = self._parse_block(pending)
        except ValueError as exc:
            self._state.parse_errors.append(str(exc))
        else:
            self._state.parsed_blocks.append(parsed)
        self._state.pending_block = _PendingBlock()

    def _parse_block(self, pending: _PendingBlock) -> ParsedRtl433Block:
        if pending.first_seen_monotonic is None or pending.last_seen_monotonic is None:
            raise ValueError("Missing block timing metadata.")
        raw_fields: dict[str, str] = {}
        for line in pending.lines:
            for key, value in FIELD_RE.findall(line):
                raw_fields[key.lower()] = value.strip()
        required_fields = {
            "time",
            "model",
            "id",
            "cmd1",
            "cmd2",
            "err1",
            "err2",
            "pilot",
            "light",
            "thermostat",
            "power",
            "front",
            "fan",
            "aux",
            "flame",
            "integrity",
        }
        missing = sorted(required_fields - raw_fields.keys())
        reject_reason: str | None = None
        valid = True
        if missing:
            reject_reason = "incomplete_block"
            valid = False
        elif raw_fields["model"] != PROFLAME_MODEL:
            reject_reason = "wrong_model"
            valid = False
        fields: dict[str, object] = {
            "label": pending.label,
            "time": raw_fields.get("time"),
            "model": raw_fields.get("model"),
            "id": raw_fields.get("id"),
            "cmd1": raw_fields.get("cmd1"),
            "cmd2": raw_fields.get("cmd2"),
            "err1": raw_fields.get("err1"),
            "err2": raw_fields.get("err2"),
            "pilot": _as_int(raw_fields.get("pilot")),
            "light": _as_int(raw_fields.get("light")),
            "thermostat": _as_int(raw_fields.get("thermostat")),
            "power": _as_int(raw_fields.get("power")),
            "front": _as_int(raw_fields.get("front")),
            "fan": _as_int(raw_fields.get("fan")),
            "aux": _as_int(raw_fields.get("aux")),
            "flame": _as_int(raw_fields.get("flame")),
            "integrity": raw_fields.get("integrity"),
        }
        return ParsedRtl433Block(
            label=pending.label,
            fields=fields,
            first_seen_monotonic=pending.first_seen_monotonic,
            last_seen_monotonic=pending.last_seen_monotonic,
            first_seen_utc=pending.first_seen_utc or "",
            last_seen_utc=pending.last_seen_utc or "",
            raw_lines=list(pending.lines),
            valid=valid,
            reject_reason=reject_reason,
        )

    def _select_candidate(self) -> None:
        if self._state.selected_block_index is not None:
            self._state.complete = True
            return
        sample_start = self._state.sample_started_monotonic
        if sample_start is None:
            return
        for index, block in enumerate(self._state.parsed_blocks):
            if block.last_seen_monotonic < sample_start:
                continue
            if not block.valid:
                continue
            self._state.selected_block_index = index
            self._state.selected_reason = "first_complete_in_sample_window"
            self._state.complete = True
            self._state.reject_reason = None
            return

    def _selected_block(self) -> ParsedRtl433Block | None:
        if self._state.selected_block_index is None:
            return None
        return self._state.parsed_blocks[self._state.selected_block_index]

    def _infer_reject_reason(self) -> str:
        if self._state.sample_started_monotonic is None:
            return "timeout"
        relevant = [
            block
            for block in self._state.parsed_blocks
            if self._state.sample_started_monotonic is None
            or block.last_seen_monotonic >= self._state.sample_started_monotonic
        ]
        if relevant:
            if any(block.reject_reason == "wrong_model" for block in relevant):
                return "wrong_model"
            if any(block.reject_reason == "incomplete_block" for block in relevant):
                return "incomplete_block"
        if self._state.parse_errors:
            return "parse_error"
        if not self._source.is_running():
            return "process_not_running"
        if self._state.raw_stdout:
            return "no_decode"
        return "timeout"

    def set_sample_anchor_monotonic(self, host_monotonic: float) -> None:
        self._state.sample_started_monotonic = host_monotonic
        self._select_candidate()

    def defer_sample_anchor(self) -> None:
        self._state.sample_started_monotonic = None
        self._state.complete = False

    def _build_decoded_payload(self, block: ParsedRtl433Block | None) -> dict[str, object]:
        payload = {
            "label": None if block is None else block.label,
            "host_received_utc": None if block is None else block.first_seen_utc,
            "host_received_ns": None if block is None else int(block.first_seen_monotonic * 1_000_000_000),
            "selected_block_index": self._state.selected_block_index,
            "selected_reason": self._state.selected_reason,
            "reject_reason": self._state.reject_reason,
            "source_running": self._source.is_running(),
            "process_exit_code": self._source.exit_code(),
        }
        if block is not None:
            payload.update(block.fields)
        return payload

    def _build_parser_debug_payload(self) -> dict[str, object]:
        return {
            "command": self._command,
            "selected_block_index": self._state.selected_block_index,
            "selected_reason": self._state.selected_reason,
            "reject_reason": self._state.reject_reason,
            "parse_errors": self._state.parse_errors,
            "source_running": self._source.is_running(),
            "process_exit_code": self._source.exit_code(),
            "candidate_blocks": [block.to_debug_dict() for block in self._state.parsed_blocks],
            "stdout_line_count": len(self._state.raw_stdout),
            "stderr_line_count": len(self._state.raw_stderr),
        }


def _as_int(value: str | None) -> int | None:
    if value is None:
        return None
    return int(value)
