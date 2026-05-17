"""LilyGO ESPHome syslog collector for coordinated capture sessions."""

from __future__ import annotations

import json
import re
import socket
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from custom_components.proflame2.rf.capture import find_proflame_candidates

from .models import CollectorArtifact, CollectorResult, SampleContext, SessionContext, utc_now

DEVICE_TIME_RE = re.compile(r"^\[(?P<time>\d{2}:\d{2}:\d{2}\.\d{3})\]")
FIFO_PROBE_BEGIN_TEXT = "RX fifo probe begin"
FIFO_PROBE_META_TEXT = "RX fifo probe meta"
FIFO_PROBE_CHUNK_TEXT = "RX fifo probe chunk"
FIFO_PROBE_END_TEXT = "RX fifo probe end"


def _fifo_candidate_to_dict(candidate) -> dict[str, object]:
    sample = candidate.sample
    return {
        "bit_offset": candidate.bit_offset,
        "symbol_offset": candidate.symbol_offset,
        "absolute_bit_offset": candidate.absolute_bit_offset,
        "repeat_count": candidate.repeat_count,
        "confidence": candidate.confidence,
        "remote_id": f"{sample.remote_id:06x}",
        "cmd1": f"{sample.cmd1:02x}",
        "cmd2": f"{sample.cmd2:02x}",
        "err1": f"{sample.err1:02x}",
        "err2": f"{sample.err2:02x}",
        "raw_slice_hex": candidate.raw_slice.hex(),
        "symbols": sample.symbols,
        "validation_notes": list(candidate.validation_notes),
    }


def _scan_fifo_payload(payload_hex: str) -> dict[str, object]:
    normalized = "".join(payload_hex.split())
    if not normalized:
        return {"payload_byte_count": 0, "candidate_count": 0, "candidates": []}
    raw_payload = bytes.fromhex(normalized)
    candidates = find_proflame_candidates(raw_payload)
    candidate_rows = [_fifo_candidate_to_dict(candidate) for candidate in candidates]
    return {
        "payload_byte_count": len(raw_payload),
        "candidate_count": len(candidate_rows),
        "candidates": candidate_rows,
    }


def _select_best_fifo_candidate(candidates: list[dict[str, object]]) -> dict[str, object] | None:
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda candidate: (
            int(candidate.get("confidence") or 0),
            int(candidate.get("repeat_count") or 0),
            -int(candidate.get("absolute_bit_offset") or 0),
        ),
    )


def _build_semantic_fifo_artifact(
    *,
    candidate: dict[str, object],
    metadata: dict[str, object],
    payload_byte_count: int,
    begin_host_time_utc: object,
    end_host_time_utc: object,
) -> dict[str, object]:
    decoded_fields = {
        "remote_id": candidate.get("remote_id"),
        "cmd1": candidate.get("cmd1"),
        "cmd2": candidate.get("cmd2"),
        "err1": candidate.get("err1"),
        "err2": candidate.get("err2"),
    }
    return {
        "artifact_class": "semantic_fifo_candidate",
        "semantic_comparable": True,
        "decode_success": True,
        "packet_normalized": True,
        "source": "lilygo_cc1101_fifo",
        "provenance": {
            "capture_mode": metadata.get("capture_mode"),
            "profile": metadata.get("profile"),
            "metadata_format": metadata.get("metadata_format"),
            "probe_id": metadata.get("probe_id"),
            "frequency_hz": metadata.get("frequency_hz"),
            "data_rate_bps": metadata.get("data_rate_bps"),
            "begin_host_time_utc": begin_host_time_utc,
            "end_host_time_utc": end_host_time_utc,
        },
        "decoded_fields": decoded_fields,
        "remote_id": decoded_fields["remote_id"],
        "cmd1": decoded_fields["cmd1"],
        "cmd2": decoded_fields["cmd2"],
        "err1": decoded_fields["err1"],
        "err2": decoded_fields["err2"],
        "candidate": candidate,
        "payload_byte_count": payload_byte_count,
        "rx_metadata": metadata,
    }


@dataclass(frozen=True)
class ReceivedSyslogLine:
    """One received syslog line with host metadata."""

    line: str
    source_host: str | None
    source_port: int | None
    host_monotonic: float
    host_received_at_utc: str

    @property
    def device_timestamp(self) -> str | None:
        match = DEVICE_TIME_RE.match(self.line)
        return None if match is None else match.group("time")


class SyslogLineSource(Protocol):
    """Line source used by the collector."""

    def start(self) -> None:
        """Open the underlying source."""

    def stop(self) -> None:
        """Close the underlying source."""

    def recv_lines(self) -> list[ReceivedSyslogLine]:
        """Return any lines currently available."""


class UdpSyslogReceiver:
    """Small UDP syslog receiver suitable for CLI collection."""

    def __init__(self, *, bind_host: str, bind_port: int) -> None:
        self._bind_host = bind_host
        self._bind_port = bind_port
        self._socket: socket.socket | None = None

    def start(self) -> None:
        if self._socket is not None:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self._bind_host, self._bind_port))
        sock.setblocking(False)
        self._socket = sock

    def stop(self) -> None:
        if self._socket is None:
            return
        self._socket.close()
        self._socket = None

    def recv_lines(self) -> list[ReceivedSyslogLine]:
        if self._socket is None:
            return []
        lines: list[ReceivedSyslogLine] = []
        while True:
            try:
                payload, address = self._socket.recvfrom(65535)
            except BlockingIOError:
                break
            decoded = payload.decode("utf-8", errors="replace").rstrip("\r\n")
            if not decoded:
                continue
            now = utc_now().isoformat()
            host_monotonic = time.monotonic()
            lines.append(
                ReceivedSyslogLine(
                    line=decoded,
                    source_host=address[0],
                    source_port=address[1],
                    host_monotonic=host_monotonic,
                    host_received_at_utc=now,
                )
            )
        return lines


class InjectedSyslogReceiver:
    """Test line source that does not require sockets."""

    def __init__(self, lines: list[ReceivedSyslogLine] | None = None) -> None:
        self._lines: deque[ReceivedSyslogLine] = deque(lines or [])

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def feed_line(
        self,
        line: str,
        *,
        source_host: str | None = "127.0.0.1",
        source_port: int | None = None,
        host_monotonic: float | None = None,
        host_received_at_utc: str | None = None,
    ) -> None:
        self._lines.append(
            ReceivedSyslogLine(
                line=line,
                source_host=source_host,
                source_port=source_port,
                host_monotonic=time.monotonic() if host_monotonic is None else host_monotonic,
                host_received_at_utc=utc_now().isoformat() if host_received_at_utc is None else host_received_at_utc,
            )
        )

    def recv_lines(self) -> list[ReceivedSyslogLine]:
        if not self._lines:
            return []
        return [self._lines.popleft()]


@dataclass
class LilyGoParserState:
    """State accumulated while parsing LilyGO syslog lines."""

    source_host: str | None = None
    source_port: int | None = None
    device_timestamps: list[str] = field(default_factory=list)
    raw_lines: list[ReceivedSyslogLine] = field(default_factory=list)
    reject_reason: str | None = None
    timed_out: bool = False
    fifo_probe_current: dict[str, object] | None = None
    fifo_probe_records: list[dict[str, object]] = field(default_factory=list)


class LilyGoSyslogCollector:
    """Real LilyGO collector that owns syslog parsing and artifacts."""

    source_name = "lilygo"
    source_mode = "syslog"

    def __init__(
        self,
        *,
        bind_host: str = "0.0.0.0",
        bind_port: int = 5514,
        source_host_filter: str | None = None,
        receiver: SyslogLineSource | None = None,
    ) -> None:
        self._bind_host = bind_host
        self._bind_port = bind_port
        self._source_host_filter = source_host_filter
        self._receiver = receiver or UdpSyslogReceiver(bind_host=bind_host, bind_port=bind_port)
        self._session_context: SessionContext | None = None
        self._sample_context: SampleContext | None = None
        self._state = LilyGoParserState()
        self._complete = False

    def start_session(self, session_context: SessionContext) -> None:
        self._session_context = session_context
        self._receiver.start()

    def start_sample(self, sample_context: SampleContext) -> None:
        self._sample_context = sample_context
        self._state = LilyGoParserState()
        self._complete = False

    def poll(self) -> None:
        for received in self._receiver.recv_lines():
            if self._source_host_filter is not None and received.source_host != self._source_host_filter:
                continue
            self._feed_line(received)

    def is_complete(self) -> bool:
        return self._complete

    def finalize_sample(self, sample_context: SampleContext) -> CollectorResult:
        self.poll()
        if not self._complete:
            self._state.timed_out = True
            self._state.reject_reason = self._infer_timeout_reason()
        artifacts = self.write_artifacts(sample_context.sample_dir)
        valid = self._state.reject_reason is None and self._latest_semantic_fifo_artifact() is not None
        return CollectorResult(
            source_name=self.source_name,
            complete=self._complete,
            valid=valid,
            reject_reason=self._state.reject_reason,
            artifact_paths=artifacts,
            metadata=self._build_capture_export_payload(),
        )

    def await_sample_trigger(
        self,
        *,
        timeout_seconds: float,
        poll_interval_seconds: float,
    ) -> float | None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            self.poll()
            time.sleep(poll_interval_seconds)
        return None

    def write_artifacts(self, sample_dir: Path) -> tuple[CollectorArtifact, ...]:
        lilygo_dir = sample_dir / "lilygo"
        lilygo_dir.mkdir(parents=True, exist_ok=True)

        raw_log = lilygo_dir / "raw_syslog.log"
        raw_log.write_text(
            "\n".join(received.line for received in self._state.raw_lines) + ("\n" if self._state.raw_lines else ""),
            encoding="utf-8",
        )

        export_path = lilygo_dir / "capture_export.json"
        export_path.write_text(
            json.dumps(self._build_capture_export_payload(), indent=2, sort_keys=True),
            encoding="utf-8",
        )

        debug_path = lilygo_dir / "parser_debug.json"
        debug_path.write_text(
            json.dumps(self._build_parser_debug_payload(), indent=2, sort_keys=True),
            encoding="utf-8",
        )

        artifacts = [
            CollectorArtifact(path="lilygo/raw_syslog.log", kind="syslog_log"),
            CollectorArtifact(path="lilygo/capture_export.json", kind="capture_export"),
            CollectorArtifact(path="lilygo/parser_debug.json", kind="parser_debug"),
        ]

        if self._state.fifo_probe_records:
            fifo_payload = self._build_fifo_probe_payload()
            fifo_path = lilygo_dir / "fifo_probe.json"
            fifo_path.write_text(
                json.dumps(fifo_payload, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            artifacts.append(CollectorArtifact(path="lilygo/fifo_probe.json", kind="fifo_probe"))
            latest = fifo_payload["latest_probe"]
            if isinstance(latest, dict):
                payload_hex = str(latest.get("payload_hex") or "")
                payload_path = lilygo_dir / "fifo_probe_payload.hex"
                payload_path.write_text(payload_hex + ("\n" if payload_hex else ""), encoding="utf-8")
                artifacts.append(CollectorArtifact(path="lilygo/fifo_probe_payload.hex", kind="fifo_probe_payload_hex"))
                bit_stream = str(latest.get("bit_stream") or "")
                bit_path = lilygo_dir / "fifo_probe_bit_stream.txt"
                bit_path.write_text(bit_stream + ("\n" if bit_stream else ""), encoding="utf-8")
                artifacts.append(
                    CollectorArtifact(path="lilygo/fifo_probe_bit_stream.txt", kind="fifo_probe_bit_stream")
                )
                semantic_artifact = self._latest_semantic_fifo_artifact()
                if semantic_artifact is not None:
                    semantic_path = lilygo_dir / "semantic_fifo_artifact.json"
                    semantic_path.write_text(
                        json.dumps(semantic_artifact, indent=2, sort_keys=True),
                        encoding="utf-8",
                    )
                    artifacts.append(
                        CollectorArtifact(path="lilygo/semantic_fifo_artifact.json", kind="semantic_fifo_artifact")
                    )

        return tuple(artifacts)

    def close(self) -> None:
        self._receiver.stop()

    def _feed_line(self, received: ReceivedSyslogLine) -> None:
        self._state.raw_lines.append(received)
        if received.source_host is not None and self._state.source_host is None:
            self._state.source_host = received.source_host
        if received.source_port is not None and self._state.source_port is None:
            self._state.source_port = received.source_port
        if received.device_timestamp is not None:
            self._state.device_timestamps.append(received.device_timestamp)

        line = received.line
        if FIFO_PROBE_BEGIN_TEXT in line:
            self._begin_fifo_probe(line, received)
            return
        if FIFO_PROBE_META_TEXT in line:
            self._update_fifo_probe(line)
            return
        if FIFO_PROBE_CHUNK_TEXT in line:
            self._append_fifo_probe_chunk(line)
            return
        if FIFO_PROBE_END_TEXT in line:
            self._end_fifo_probe(line, received)
            return

    def _begin_fifo_probe(self, line: str, received: ReceivedSyslogLine) -> None:
        metadata = self._parse_export_metadata_line(line)
        metadata["metadata_format"] = "stage5ai_fifo_probe"
        self._state.fifo_probe_current = {
            "metadata": metadata,
            "chunks": [],
            "lines": [line],
            "begin_host_time_utc": received.host_received_at_utc,
            "end_host_time_utc": None,
        }

    def _ensure_fifo_probe_current(self) -> dict[str, object]:
        if self._state.fifo_probe_current is None:
            self._state.fifo_probe_current = {
                "metadata": {"metadata_format": "stage5ai_fifo_probe"},
                "chunks": [],
                "lines": [],
                "begin_host_time_utc": None,
                "end_host_time_utc": None,
            }
        return self._state.fifo_probe_current

    def _update_fifo_probe(self, line: str) -> None:
        current = self._ensure_fifo_probe_current()
        current["lines"].append(line)  # type: ignore[index,union-attr]
        metadata = current["metadata"]  # type: ignore[index]
        if isinstance(metadata, dict):
            metadata.update(self._parse_export_metadata_line(line))
            metadata["metadata_format"] = "stage5ai_fifo_probe"

    def _append_fifo_probe_chunk(self, line: str) -> None:
        current = self._ensure_fifo_probe_current()
        current["lines"].append(line)  # type: ignore[index,union-attr]
        fields = self._parse_export_metadata_line(line)
        chunk = {
            "chunk": fields.get("chunk"),
            "offset": fields.get("offset"),
            "count": fields.get("count"),
            "hex": fields.get("hex"),
        }
        chunks = current["chunks"]  # type: ignore[index]
        if isinstance(chunks, list):
            chunks.append(chunk)

    def _end_fifo_probe(self, line: str, received: ReceivedSyslogLine) -> None:
        current = self._ensure_fifo_probe_current()
        current["lines"].append(line)  # type: ignore[index,union-attr]
        current["end_host_time_utc"] = received.host_received_at_utc  # type: ignore[index]
        metadata = current["metadata"]  # type: ignore[index]
        if isinstance(metadata, dict):
            metadata.update(self._parse_export_metadata_line(line))
            metadata["metadata_format"] = "stage5ai_fifo_probe"
        finalized = self._finalize_fifo_probe_record(current)
        self._state.fifo_probe_records.append(finalized)
        self._state.fifo_probe_current = None
        self._state.reject_reason = None if finalized.get("semantic_comparable") is True else "fifo_probe_no_candidate"
        self._complete = True

    def _finalize_fifo_probe_record(self, current: dict[str, object]) -> dict[str, object]:
        chunks = current.get("chunks")
        chunk_rows = [chunk for chunk in chunks if isinstance(chunk, dict)] if isinstance(chunks, list) else []
        sorted_chunks = sorted(
            chunk_rows,
            key=lambda item: int(item.get("offset") or 0),
        )
        payload_hex = "".join(str(chunk.get("hex") or "") for chunk in sorted_chunks)
        byte_count = len(payload_hex) // 2 if re.fullmatch(r"[0-9A-Fa-f]*", payload_hex) else 0
        metadata = dict(current.get("metadata") or {})
        expected_count = metadata.get("byte_count")
        warnings: list[str] = []
        if expected_count is not None and expected_count != byte_count:
            warnings.append(f"fifo_probe_byte_count_mismatch:expected={expected_count}:actual={byte_count}")
        scan = (
            _scan_fifo_payload(payload_hex)
            if byte_count
            else {
                "payload_byte_count": byte_count,
                "candidate_count": 0,
                "candidates": [],
            }
        )
        candidates = scan.get("candidates") if isinstance(scan, dict) else []
        candidate_rows = (
            [candidate for candidate in candidates if isinstance(candidate, dict)]
            if isinstance(candidates, list)
            else []
        )
        best_candidate = _select_best_fifo_candidate(candidate_rows)
        semantic_artifact = (
            _build_semantic_fifo_artifact(
                candidate=best_candidate,
                metadata=metadata,
                payload_byte_count=byte_count,
                begin_host_time_utc=current.get("begin_host_time_utc"),
                end_host_time_utc=current.get("end_host_time_utc"),
            )
            if best_candidate is not None
            else None
        )
        return {
            "metadata": metadata,
            "begin_host_time_utc": current.get("begin_host_time_utc"),
            "end_host_time_utc": current.get("end_host_time_utc"),
            "chunk_count": len(sorted_chunks),
            "chunks": sorted_chunks,
            "payload_hex": payload_hex.upper(),
            "payload_byte_count": byte_count,
            "bit_stream": self._hex_to_bit_stream(payload_hex),
            "warnings": warnings,
            "semantic_scan": scan,
            "semantic_candidate_count": int(scan.get("candidate_count") or 0) if isinstance(scan, dict) else 0,
            "best_semantic_candidate": best_candidate,
            "semantic_artifact": semantic_artifact,
            "semantic_comparable": semantic_artifact is not None,
        }

    def _latest_semantic_fifo_artifact(self) -> dict[str, object] | None:
        if not self._state.fifo_probe_records:
            return None
        latest = self._state.fifo_probe_records[-1]
        artifact = latest.get("semantic_artifact")
        return artifact if isinstance(artifact, dict) else None

    def _infer_timeout_reason(self) -> str:
        return "missing_semantic_fifo_artifact"

    def _parse_export_metadata_line(self, line: str) -> dict[str, object]:
        fields: dict[str, object] = {}
        for match in re.finditer(r"\b(?P<key>[A-Za-z0-9_]+)=(?P<value>\S+)", line):
            key = match.group("key")
            value = match.group("value")
            fields[key] = value if key == "hex" else self._coerce_metadata_value(value)
        return fields

    def _coerce_metadata_value(self, value: str) -> object:
        if value == "NA":
            return None
        if value in {"YES", "NO"}:
            return value
        if re.fullmatch(r"-?\d+", value):
            return int(value)
        if re.fullmatch(r"-?\d+\.\d+", value):
            return float(value)
        return value

    def _hex_to_bit_stream(self, payload_hex: str) -> str:
        if not payload_hex or re.fullmatch(r"[0-9A-Fa-f]*", payload_hex) is None:
            return ""
        try:
            return "".join(f"{byte:08b}" for byte in bytes.fromhex(payload_hex))
        except ValueError:
            return ""

    def _build_capture_export_payload(self) -> dict[str, object]:
        return {
            "artifact_class": "lilygo_fifo_capture_export",
            "reject_reason": self._state.reject_reason,
            "device_timestamps": self._state.device_timestamps,
            "source_host": self._state.source_host,
            "source_port": self._state.source_port,
            "fifo_probe": self._build_fifo_probe_payload() if self._state.fifo_probe_records else None,
            "semantic_fifo_artifact": self._latest_semantic_fifo_artifact(),
            "semantic_fifo_present": self._latest_semantic_fifo_artifact() is not None,
            "timed_out": self._state.timed_out,
        }

    def _build_fifo_probe_payload(self) -> dict[str, object]:
        latest = self._state.fifo_probe_records[-1] if self._state.fifo_probe_records else None
        return {
            "artifact_class": "experimental_fifo_probe",
            "probe_count": len(self._state.fifo_probe_records),
            "latest_probe": latest,
            "probes": self._state.fifo_probe_records,
            "latest_semantic_artifact": self._latest_semantic_fifo_artifact(),
            "source_host": self._state.source_host,
            "source_port": self._state.source_port,
        }

    def _build_parser_debug_payload(self) -> dict[str, object]:
        return {
            "reject_reason": self._state.reject_reason,
            "raw_line_count": len(self._state.raw_lines),
            "fifo_probe": self._build_fifo_probe_payload(),
            "raw_lines": [
                {
                    "line": received.line,
                    "source_host": received.source_host,
                    "source_port": received.source_port,
                    "host_monotonic": received.host_monotonic,
                    "host_received_at_utc": received.host_received_at_utc,
                    "device_timestamp": received.device_timestamp,
                }
                for received in self._state.raw_lines
            ],
        }

    def get_live_status(self) -> dict[str, object]:
        return {
            "raw_line_count": len(self._state.raw_lines),
            "source_host": self._state.source_host,
            "source_port": self._state.source_port,
            "reject_reason": self._state.reject_reason,
            "semantic_fifo_present": self._latest_semantic_fifo_artifact() is not None,
        }
