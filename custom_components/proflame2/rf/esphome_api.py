"""ESPHome-backed RF node adapter skeleton."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from itertools import count
from typing import Any, cast

from ..const import BACKEND_ESPHOME
from ..packet_debug import get_packet_debug_logger
from ..protocol.ecc import build_err_byte, derive_ecc_profile
from ..protocol.models import RemoteProfile
from ..protocol.packet import ProflameFrame, ProflamePacket
from .artifacts import ESPHomeAcceptedRXPacketMetadata, FifoDebugFailure, LilyGoFifoSemanticArtifact
from .base import BackendCapabilities, CaptureResult, RFBackend, SendResult
from .capture import CaptureSample, DecodeCandidate, find_proflame_candidates
from .esphome.contract import (
    ESPHomeDisplayState,
    ESPHomeEndpointStatusReport,
    ESPHomeRadioConfig,
    ESPHomeRXEvent,
    ESPHomeTXRequest,
    ESPHomeTXResponse,
)
from .esphome.transport import ESPHomeTransport

ESPHOME_CONNECT_TIMEOUT_SECONDS = 10.0
ESPHOME_CLOSE_TIMEOUT_SECONDS = 5.0
ESPHOME_SEND_TIMEOUT_SECONDS = 15.0
ESPHOME_LEARN_TIMEOUT_SECONDS = 30.0
ESPHOME_LEARN_RECEIVE_TIMEOUT_SECONDS = 1.0
ESPHOME_LEARN_MIN_VALID_PACKETS = 3
ESPHOME_LEARN_MIN_UNIQUE_CMD1_SAMPLES = 2
ESPHOME_LEARN_MIN_UNIQUE_CMD2_SAMPLES = 2
ESPHOME_FIFO_EVENT_WAIT_POLL_SECONDS = 0.75
ESPHOME_FIFO_MAX_SCAN_PAYLOAD_BYTES = 16_384

_FIFO_RX_EVENT_KINDS = {"fifo_capture", "rx_packet"}

_LOGGER = logging.getLogger(__name__)
_LearningReceiveEvent = Callable[..., Awaitable[ESPHomeRXEvent | None]]


@dataclass(slots=True)
class FifoLearningAttempt:
    """One LilyGO FIFO learning event after policy and scanner evaluation."""

    event: ESPHomeRXEvent
    candidates: tuple[DecodeCandidate, ...] = ()
    accepted_candidate: DecodeCandidate | None = None
    debug_failure: FifoDebugFailure | None = None


@dataclass(slots=True)
class FifoLearningAccumulator:
    """Mutable state accumulated while learning from FIFO capture events."""

    packets: list[ProflamePacket] = field(default_factory=list)
    samples: list[CaptureSample] = field(default_factory=list)
    semantic_artifacts: list[LilyGoFifoSemanticArtifact] = field(default_factory=list)
    debug_failures: list[FifoDebugFailure] = field(default_factory=list)
    raw_payloads_seen: int = 0
    decode_failures: int = 0
    remote_id: int | None = None


class ESPHomeAPIBackend(RFBackend):
    """Backend adapter for a future ESPHome/T-Embed CC1101 endpoint."""

    name = BACKEND_ESPHOME

    def __init__(
        self,
        *,
        transport: ESPHomeTransport | None = None,
        radio_config: ESPHomeRadioConfig | None = None,
        remote_profile: RemoteProfile | None = None,
        debug_logging_enabled: bool = False,
        connect_timeout_seconds: float = ESPHOME_CONNECT_TIMEOUT_SECONDS,
        send_timeout_seconds: float = ESPHOME_SEND_TIMEOUT_SECONDS,
        close_timeout_seconds: float = ESPHOME_CLOSE_TIMEOUT_SECONDS,
    ) -> None:
        self.transport = transport
        self.radio_config = radio_config or ESPHomeRadioConfig()
        self.remote_profile = remote_profile
        self.active_listening_profile = remote_profile
        self.debug_logging_enabled = debug_logging_enabled
        self.connected = False
        self.last_endpoint_status: ESPHomeEndpointStatusReport | None = None
        self.last_tx_response: ESPHomeTXResponse | None = None
        self.active_listening_enabled = False
        self.last_fifo_semantic_artifact: LilyGoFifoSemanticArtifact | None = None
        self.last_fifo_debug_failure: FifoDebugFailure | None = None
        self._request_counter = count(1)
        self._connect_timeout_seconds = connect_timeout_seconds
        self._send_timeout_seconds = send_timeout_seconds
        self._close_timeout_seconds = close_timeout_seconds

    async def connect(self) -> None:
        """Open/configure the ESPHome transport boundary."""

        if self.transport is None:
            raise RuntimeError("ESPHome backend is unavailable; no transport is configured.")
        self._log_debug(
            "connect start controller_id=%s radio_config=%s transport=%s",
            self.name,
            _serialize_contract_value(self.radio_config),
            self.transport.__class__.__name__,
        )
        try:
            await asyncio.wait_for(
                self.transport.connect(),
                timeout=self._connect_timeout_seconds,
            )
            await asyncio.wait_for(
                self.transport.configure_radio(self.radio_config),
                timeout=self._connect_timeout_seconds,
            )
            await asyncio.wait_for(
                self.transport.set_active_listening(
                    self.active_listening_enabled,
                    self.active_listening_profile if self.active_listening_enabled else None,
                ),
                timeout=self._connect_timeout_seconds,
            )
            self.last_endpoint_status = await asyncio.wait_for(
                self.transport.get_status(),
                timeout=self._connect_timeout_seconds,
            )
        except Exception as exc:
            self.connected = False
            raise RuntimeError(f"ESPHome backend is unavailable: {exc}") from exc
        self.connected = True
        self._log_debug(
            "connect complete controller_id=%s endpoint_status=%s",
            self.name,
            _serialize_contract_value(self.last_endpoint_status),
        )

    async def close(self, *, reason: str | None = None) -> None:
        """Close the ESPHome transport boundary."""

        if self.transport is None:
            self.connected = False
            return
        try:
            await self.stop_rx()
            await asyncio.wait_for(
                self.transport.close(),
                timeout=self._close_timeout_seconds,
            )
        finally:
            self.connected = False
            self.active_listening_enabled = False

    async def send(self, packet):
        """Send one HA-prepared Proflame2 transmission plan."""

        if packet.transmission_plan is None:
            raise RuntimeError("ESPHome transmit requires packet.transmission_plan to be present.")
        if not self.connected:
            await self.connect()
        assert self.transport is not None
        restore_rx = self.active_listening_enabled
        await self.stop_rx()

        request = ESPHomeTXRequest.from_packet(
            packet,
            request_id=self._next_request_id(),
            display_state=getattr(packet, "display_state", None),
        )
        if request.repeat_count is None:
            request = ESPHomeTXRequest(
                request_id=request.request_id,
                air_payload=request.air_payload,
                repeat_count=self.radio_config.tx_repeat_count,
                remote_id=request.remote_id,
                cmd1=request.cmd1,
                err1=request.err1,
                cmd2=request.cmd2,
                err2=request.err2,
                display_state=request.display_state,
            )
        self._log_debug(
            "send start controller_id=%s request_id=%s payload_length=%s payload_bit_length=%s repeat_count=%s air_payload_hex=%s",
            self.name,
            request.request_id,
            len(request.air_payload),
            request.air_payload_bit_length,
            request.repeat_count,
            request.air_payload_hex,
        )
        response = None
        try:
            response = await asyncio.wait_for(
                self.transport.send_tx(request),
                timeout=self._send_timeout_seconds,
            )
        except Exception as exc:
            raise RuntimeError(f"ESPHome backend send failed: {exc}") from exc
        finally:
            if restore_rx:
                try:
                    await self.set_active_listening_enabled(True)
                    transport_diagnostics = (
                        self.transport.serialize_diagnostics()
                        if hasattr(self.transport, "serialize_diagnostics")
                        else None
                    )
                    self._log_debug(
                        "active listening restored after tx controller_id=%s request_id=%s transport=%s",
                        self.name,
                        request.request_id,
                        transport_diagnostics,
                    )
                except Exception as exc:
                    self._log_debug(
                        "active listening restore after tx failed controller_id=%s error=%s",
                        self.name,
                        exc,
                    )
        if response is None:
            raise RuntimeError("ESPHome backend send failed: no response")
        if not response.ok:
            error = response.error_message or response.error_code or "unknown error"
            raise RuntimeError(f"ESPHome backend send failed: {error}")
        self.last_tx_response = response
        try:
            self.last_endpoint_status = await self.transport.get_status()
        except Exception:
            self.last_endpoint_status = None
        transport_diagnostics = (
            self.transport.serialize_diagnostics() if hasattr(self.transport, "serialize_diagnostics") else None
        )
        self._log_debug(
            "send complete controller_id=%s request_id=%s response=%s endpoint_status=%s transport_confirmation=%s",
            self.name,
            request.request_id,
            _serialize_contract_value(response),
            _serialize_contract_value(self.last_endpoint_status),
            transport_diagnostics,
        )
        return SendResult(
            packet=packet,
            backend_name=self.name,
            warnings=packet.warnings,
        )

    async def update_display_state(self, display_state: ESPHomeDisplayState) -> None:
        """Push display-only state to the ESPHome endpoint without transmitting RF."""

        if not self.connected:
            await self.connect()
        assert self.transport is not None
        try:
            await asyncio.wait_for(
                self.transport.update_display_state(display_state),
                timeout=self._send_timeout_seconds,
            )
        except Exception as exc:
            raise RuntimeError(f"ESPHome display state update failed: {exc}") from exc
        try:
            self.last_endpoint_status = await self.transport.get_status()
        except Exception:
            self.last_endpoint_status = None

    async def update_learning_mode(
        self,
        *,
        active: bool,
        step_title: str,
        instruction: str,
        status: str,
    ) -> None:
        """Push guided-learning UI/status to the ESPHome endpoint."""

        if not self.connected:
            await self.connect()
        assert self.transport is not None
        update_learning_mode = getattr(self.transport, "update_learning_mode", None)
        if not callable(update_learning_mode):
            return
        try:
            await asyncio.wait_for(
                update_learning_mode(
                    active=active,
                    step_title=step_title,
                    instruction=instruction,
                    status=status,
                ),
                timeout=self._send_timeout_seconds,
            )
        except Exception as exc:
            raise RuntimeError(f"ESPHome learning mode update failed: {exc}") from exc

    async def receive(self, timeout: float | None = None):
        if self.transport is None:
            return None
        if not self.connected:
            await self.connect()
        receive_rx_event = getattr(self.transport, "receive_rx_event", None)
        if not callable(receive_rx_event):
            return None

        deadline = None if timeout is None else (asyncio.get_running_loop().time() + timeout)
        while True:
            remaining = None if deadline is None else max(0.0, deadline - asyncio.get_running_loop().time())
            if deadline is not None and remaining <= 0.0:
                return None
            receive_timeout = (
                ESPHOME_FIFO_EVENT_WAIT_POLL_SECONDS
                if remaining is None
                else min(remaining, ESPHOME_FIFO_EVENT_WAIT_POLL_SECONDS)
            )
            event = await receive_rx_event(timeout=receive_timeout)
            if event is None:
                continue
            if not self._rx_event_is_decodable(event, strict_packet_policy=True):
                continue
            direct_packet = self._packet_from_decoded_rx_event(event)
            if (event.capture_metadata or {}).get("event_kind") == "rx_packet":
                if direct_packet is not None:
                    self._log_debug(
                        "receive accepted decoded rx event controller_id=%s event_id=%s remote_id=%06x",
                        self.name,
                        event.event_id,
                        direct_packet.remote_id,
                    )
                    return direct_packet
                self._log_debug(
                    "receive ignored invalid decoded rx event controller_id=%s event_id=%s reason=%s",
                    self.name,
                    event.event_id,
                    (self.last_fifo_debug_failure or {}).get("reason"),
                )
                continue
            candidates = self._scan_fifo_event(event)
            if not candidates:
                self._log_debug(
                    "receive ignored undecodable rx event controller_id=%s event_id=%s payload_length_bytes=%s payload_hex_preview=%s",
                    self.name,
                    event.event_id,
                    len(event.raw_payload),
                    _abbreviate_hex(event.raw_payload_hex),
                )
                continue
            candidate = candidates[0]
            packet = candidate.sample.as_packet(
                source="esphome_rx",
                received_at=datetime.now(timezone.utc),
                rssi=event.rssi,
                warnings=candidate.packet.warnings,
            )
            self.last_fifo_semantic_artifact = self._build_fifo_semantic_artifact(
                event=event,
                candidate=candidate,
                source="esphome_rx",
                learning_accepted=False,
            )
            self._log_debug(
                "receive decoded rx event controller_id=%s event_id=%s remote_id=%06x payload_length_bytes=%s payload_hex_preview=%s",
                self.name,
                event.event_id,
                packet.remote_id,
                len(event.raw_payload),
                _abbreviate_hex(event.raw_payload_hex),
            )
            return packet

    async def set_active_listening_enabled(
        self,
        enabled: bool,
        remote_profile: RemoteProfile | None = None,
    ) -> None:
        self.active_listening_enabled = enabled
        if remote_profile is not None:
            self.active_listening_profile = remote_profile
        if self.transport is None:
            return
        if not self.connected:
            return
        set_active_listening = getattr(self.transport, "set_active_listening", None)
        if not callable(set_active_listening):
            return
        await asyncio.wait_for(
            set_active_listening(enabled, self.active_listening_profile if enabled else None),
            timeout=self._send_timeout_seconds,
        )
        transport_diagnostics = (
            self.transport.serialize_diagnostics() if hasattr(self.transport, "serialize_diagnostics") else None
        )
        profile = self.active_listening_profile if enabled else None
        self._log_debug(
            "active listening policy applied controller_id=%s enabled=%s serial_id=%s transport=%s",
            self.name,
            enabled,
            f"{profile.serial_id:06x}" if profile is not None else None,
            transport_diagnostics,
        )

    async def stop_rx(self) -> None:
        if self.transport is None:
            return
        stop_rx = getattr(self.transport, "stop_rx", None)
        if not callable(stop_rx):
            return
        try:
            await asyncio.wait_for(
                stop_rx(),
                timeout=self._send_timeout_seconds,
            )
        except Exception:
            self._log_debug("stop_rx ignored controller_id=%s", self.name)

    async def end_confirmation_rx(self) -> None:
        if self.transport is None:
            return
        end_confirmation_rx = getattr(self.transport, "end_confirmation_rx", None)
        if not callable(end_confirmation_rx):
            return
        try:
            await asyncio.wait_for(
                end_confirmation_rx(),
                timeout=self._send_timeout_seconds,
            )
        except Exception:
            self._log_debug("end_confirmation_rx ignored controller_id=%s", self.name)

    async def learn(self, timeout: float | None = None) -> CaptureResult:
        """Learn a remote profile from accepted LilyGO FIFO candidate windows."""

        if self.transport is None:
            raise RuntimeError("ESPHome backend is unavailable; no transport is configured.")
        if not self.connected:
            await self.connect()
        receive_rx_event = getattr(self.transport, "receive_rx_event", None)
        if not callable(receive_rx_event):
            raise RuntimeError("ESPHome backend learning requires RX event transport support.")

        deadline = time.monotonic() + (ESPHOME_LEARN_TIMEOUT_SECONDS if timeout is None else timeout)
        accumulator = FifoLearningAccumulator()

        while time.monotonic() < deadline:
            event = await self._async_receive_learning_event(receive_rx_event, deadline)
            if event is None:
                continue
            attempt = self._evaluate_learning_event(event, accumulator)
            if attempt.accepted_candidate is None:
                continue
            self._accept_learning_candidate(attempt, accumulator)

            if self._learning_sample_set_is_sufficient(accumulator.samples):
                break

        return self._build_fifo_learning_capture_result(accumulator)

    async def _async_receive_learning_event(
        self,
        receive_rx_event: _LearningReceiveEvent,
        deadline: float,
    ) -> ESPHomeRXEvent | None:
        """Wait for one FIFO learning RX event within the remaining deadline."""

        remaining = max(0.0, deadline - time.monotonic())
        receive_timeout = min(remaining, ESPHOME_LEARN_RECEIVE_TIMEOUT_SECONDS)
        if receive_timeout <= 0.0:
            return None
        return await receive_rx_event(timeout=receive_timeout)

    def _evaluate_learning_event(
        self,
        event: ESPHomeRXEvent,
        accumulator: FifoLearningAccumulator,
    ) -> FifoLearningAttempt:
        """Apply FIFO learning acceptance policy to one raw RX event."""

        if not self._rx_event_is_decodable(event, strict_packet_policy=False):
            failure: FifoDebugFailure = self.last_fifo_debug_failure or {
                "event_id": event.event_id,
                "reason": "non_decodable_event",
            }
            accumulator.debug_failures.append(failure)
            return FifoLearningAttempt(event=event, debug_failure=failure)

        accumulator.raw_payloads_seen += 1
        candidates = self._scan_fifo_event(event)
        if not candidates:
            accumulator.decode_failures += 1
            failure: FifoDebugFailure = self.last_fifo_debug_failure or {
                "event_id": event.event_id,
                "reason": "no_valid_candidate",
                "payload_length_bytes": len(event.raw_payload),
            }
            accumulator.debug_failures.append(failure)
            return FifoLearningAttempt(event=event, debug_failure=failure)

        candidate = candidates[0]
        sample = candidate.sample
        if accumulator.remote_id is not None and sample.remote_id != accumulator.remote_id:
            failure: FifoDebugFailure = {
                "event_id": event.event_id,
                "reason": "remote_id_mismatch",
                "expected_remote_id": f"{accumulator.remote_id:06x}",
                "observed_remote_id": f"{sample.remote_id:06x}",
            }
            accumulator.debug_failures.append(failure)
            return FifoLearningAttempt(event=event, candidates=candidates, debug_failure=failure)

        return FifoLearningAttempt(event=event, candidates=candidates, accepted_candidate=candidate)

    def _accept_learning_candidate(
        self,
        attempt: FifoLearningAttempt,
        accumulator: FifoLearningAccumulator,
    ) -> None:
        """Promote one accepted scanner candidate into learning artifacts."""

        candidate = attempt.accepted_candidate
        if candidate is None:
            return
        event = attempt.event
        sample = candidate.sample
        if accumulator.remote_id is None:
            accumulator.remote_id = sample.remote_id

        artifact = self._build_fifo_semantic_artifact(
            event=event,
            candidate=candidate,
            source="esphome_fifo_learning",
            learning_accepted=True,
        )
        self.last_fifo_semantic_artifact = artifact
        accumulator.semantic_artifacts.append(artifact)
        accumulator.samples.append(sample)
        accumulator.packets.append(
            sample.as_packet(
                source="esphome_fifo_learning",
                received_at=datetime.now(timezone.utc),
                rssi=event.rssi,
                warnings=candidate.packet.warnings,
            )
        )

    def _derive_learning_profile_metadata(
        self, samples: list[CaptureSample]
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Return learned ECC profile metadata or the derivation error."""

        if not samples:
            return None, None
        try:
            return (
                asdict(
                    derive_ecc_profile(
                        (sample.cmd1_tuple for sample in samples),
                        (sample.cmd2_tuple for sample in samples),
                    )
                ),
                None,
            )
        except ValueError as exc:
            return None, str(exc)

    def _build_fifo_learning_capture_result(self, accumulator: FifoLearningAccumulator) -> CaptureResult:
        """Build the public learning result from accumulated FIFO candidates."""

        learned_profile, profile_error = self._derive_learning_profile_metadata(accumulator.samples)

        return CaptureResult(
            serial_id=accumulator.remote_id or 0,
            packets=tuple(accumulator.packets),
            samples=tuple(accumulator.samples),
            metadata={
                "source": "esphome_fifo_learning",
                "artifact_class": "semantic",
                "semantic_comparable": bool(accumulator.samples) and profile_error is None,
                "decode_success_count": len(accumulator.samples),
                "raw_payloads_seen": accumulator.raw_payloads_seen,
                "decode_failures": accumulator.decode_failures,
                "debug_failures": tuple(accumulator.debug_failures),
                "semantic_artifacts": tuple(accumulator.semantic_artifacts),
                "learned_profile": learned_profile,
                "profile_error": profile_error,
            },
        )

    async def get_status(self) -> ESPHomeEndpointStatusReport:
        """Return endpoint status from the configured transport."""

        if self.transport is None:
            raise RuntimeError("ESPHome backend is unavailable; no transport is configured.")
        self.last_endpoint_status = await self.transport.get_status()
        return self.last_endpoint_status

    async def capabilities(self) -> BackendCapabilities:
        if self.transport is not None and self.connected:
            try:
                self.last_endpoint_status = await asyncio.wait_for(
                    self.transport.get_status(),
                    timeout=min(self._send_timeout_seconds, 2.0),
                )
            except Exception as exc:
                self._log_debug(
                    "capabilities status refresh failed controller_id=%s error=%s",
                    self.name,
                    exc,
                )
        can_receive = bool(getattr(self.transport, "can_receive", False))
        self._log_debug(
            "capabilities controller_id=%s can_receive=%s endpoint_status=%s",
            self.name,
            can_receive,
            _serialize_contract_value(self.last_endpoint_status),
        )
        return BackendCapabilities(
            can_send=True,
            can_receive=can_receive,
            can_learn=can_receive,
            notes=(
                "ESPHome/T-Embed backend sends HA-generated air payloads through a transport boundary.",
                "Learning emits raw LilyGO FIFO capture bytes and HA owns candidate promotion.",
                "Active listening uses HA-provided profile constants so firmware filters to matching Proflame2 packets before emitting events.",
                "Learning promotes only accepted Proflame candidates into semantic FIFO artifacts.",
            ),
        )

    def _next_request_id(self) -> str:
        """Return a local correlation id for HA/transport/debug logs."""

        return f"proflame2-{next(self._request_counter)}"

    def serialize_diagnostics(self) -> dict[str, Any]:
        """Return diagnostics for the HA-side ESPHome backend adapter."""

        transport = self.transport
        transport_diagnostics: dict[str, Any] | None = None
        if transport is not None:
            if hasattr(transport, "serialize_diagnostics"):
                transport_diagnostics = transport.serialize_diagnostics()
            else:
                transport_diagnostics = {
                    "class": transport.__class__.__name__,
                    "connected": getattr(transport, "connected", None),
                    "configured": getattr(transport, "configured", None),
                    "connect_count": getattr(transport, "connect_count", None),
                    "close_count": getattr(transport, "close_count", None),
                    "tx_request_count": len(getattr(transport, "tx_requests", ())),
                    "tx_response_count": len(getattr(transport, "tx_responses", ())),
                    "available": getattr(transport, "available", None),
                }

        return {
            "backend_name": self.name,
            "controller_id": self.name,
            "connected": self.connected,
            "radio_config": _serialize_contract_value(self.radio_config),
            "endpoint_status": _serialize_contract_value(self.last_endpoint_status),
            "last_tx_response": _serialize_contract_value(self.last_tx_response),
            "last_fifo_semantic_artifact": self.last_fifo_semantic_artifact,
            "last_fifo_debug_failure": self.last_fifo_debug_failure,
            "transport": transport_diagnostics,
        }

    def _log_debug(self, message: str, *args: object) -> None:
        """Emit ESPHome adapter debug logs only when explicitly enabled."""

        if not self.debug_logging_enabled:
            return
        _LOGGER.warning("Proflame2 ESPHome backend: " + message, *args)
        get_packet_debug_logger().warning("esphome_backend: " + message, *args)

    def _rx_event_is_decodable(
        self,
        event: ESPHomeRXEvent,
        *,
        strict_packet_policy: bool,
    ) -> bool:
        metadata = event.capture_metadata or {}
        event_kind = metadata.get("event_kind")
        accepted = metadata.get("accepted")
        qualifier = metadata.get("qualifier")
        reject_reason = metadata.get("reject_reason")
        if event_kind not in _FIFO_RX_EVENT_KINDS:
            self.last_fifo_debug_failure = {
                "event_id": event.event_id,
                "reason": "unsupported_event_kind",
                "event_kind": event_kind,
                "reject_reason": reject_reason,
            }
            self._log_debug(
                "receive ignored non-fifo rx event controller_id=%s event_id=%s event_kind=%s qualifier=%s reject_reason=%s payload_length_bytes=%s payload_hex_preview=%s",
                self.name,
                event.event_id,
                event_kind,
                qualifier,
                reject_reason,
                len(event.raw_payload),
                _abbreviate_hex(event.raw_payload_hex),
            )
            return False
        if event_kind == "rx_packet" and strict_packet_policy:
            if accepted != "true":
                self.last_fifo_debug_failure = {
                    "event_id": event.event_id,
                    "reason": "rx_packet_not_accepted",
                    "qualifier": qualifier,
                    "reject_reason": reject_reason,
                }
                self._log_debug(
                    "receive ignored unaccepted rx event controller_id=%s event_id=%s qualifier=%s reject_reason=%s payload_length_bytes=%s payload_hex_preview=%s",
                    self.name,
                    event.event_id,
                    qualifier,
                    reject_reason,
                    len(event.raw_payload),
                    _abbreviate_hex(event.raw_payload_hex),
                )
                return False
            if qualifier != "strict":
                self.last_fifo_debug_failure = {
                    "event_id": event.event_id,
                    "reason": "rx_packet_not_strict",
                    "qualifier": qualifier,
                }
                self._log_debug(
                    "receive ignored non-strict rx event controller_id=%s event_id=%s qualifier=%s payload_length_bytes=%s payload_hex_preview=%s",
                    self.name,
                    event.event_id,
                    qualifier,
                    len(event.raw_payload),
                    _abbreviate_hex(event.raw_payload_hex),
                )
                return False
        return True

    def _scan_fifo_event(self, event: ESPHomeRXEvent) -> tuple[DecodeCandidate, ...]:
        if len(event.raw_payload) > ESPHOME_FIFO_MAX_SCAN_PAYLOAD_BYTES:
            self.last_fifo_debug_failure = {
                "event_id": event.event_id,
                "reason": "fifo_payload_too_large_for_scanner",
                "payload_length_bytes": len(event.raw_payload),
                "max_payload_length_bytes": ESPHOME_FIFO_MAX_SCAN_PAYLOAD_BYTES,
                "capture_metadata": event.capture_metadata,
            }
            self._log_debug(
                "receive ignored oversized fifo event controller_id=%s event_id=%s payload_length_bytes=%s max_payload_length_bytes=%s",
                self.name,
                event.event_id,
                len(event.raw_payload),
                ESPHOME_FIFO_MAX_SCAN_PAYLOAD_BYTES,
            )
            return ()
        candidates = find_proflame_candidates(event.raw_payload)
        if not candidates:
            self.last_fifo_debug_failure = {
                "event_id": event.event_id,
                "reason": "no_valid_proflame_candidate",
                "payload_length_bytes": len(event.raw_payload),
                "raw_payload_hex": event.raw_payload_hex,
                "capture_metadata": event.capture_metadata,
            }
        return tuple(candidates)

    def _packet_from_decoded_rx_event(self, event: ESPHomeRXEvent):
        metadata = event.capture_metadata or {}
        if metadata.get("event_kind") != "rx_packet":
            return None
        if metadata.get("accepted") != "true" or metadata.get("qualifier") != "strict":
            return None
        try:
            remote_id = _parse_event_int(metadata.get("remote_id"))
            cmd1 = _parse_event_int(metadata.get("cmd1"))
            cmd2 = _parse_event_int(metadata.get("cmd2"))
            err1 = _parse_event_int(metadata.get("err1"))
            err2 = _parse_event_int(metadata.get("err2"))
        except (TypeError, ValueError):
            self.last_fifo_debug_failure = {
                "event_id": event.event_id,
                "reason": "decoded_rx_event_missing_fields",
                "capture_metadata": metadata,
            }
            return None
        if None in (remote_id, cmd1, cmd2, err1, err2):
            return None

        profile = self.active_listening_profile or self.remote_profile
        if profile is not None:
            if remote_id != profile.serial_id:
                self.last_fifo_debug_failure = {
                    "event_id": event.event_id,
                    "reason": "decoded_rx_event_wrong_serial_id",
                    "expected_remote_id": f"{profile.serial_id:06x}",
                    "observed_remote_id": f"{remote_id:06x}",
                }
                return None
            if err1 != build_err_byte(cmd1, profile.ecc.c1, profile.ecc.d1) or err2 != build_err_byte(
                cmd2, profile.ecc.c2, profile.ecc.d2
            ):
                self.last_fifo_debug_failure = {
                    "event_id": event.event_id,
                    "reason": "decoded_rx_event_ecc_mismatch",
                }
                return None

        frame = ProflameFrame(
            serial_id=remote_id,
            cmd1=cmd1,
            err1=err1,
            cmd2=cmd2,
            err2=err2,
        )
        try:
            packet = ProflamePacket.from_frame(
                frame,
                source="esphome_active_listening",
                raw=event.raw_payload,
                received_at=datetime.now(timezone.utc),
                rssi=event.rssi,
            )
        except ValueError as exc:
            self.last_fifo_debug_failure = {
                "event_id": event.event_id,
                "reason": "decoded_rx_event_invalid_state",
                "error": str(exc),
            }
            return None
        self.last_fifo_semantic_artifact = self._build_active_listening_semantic_artifact(
            event=event,
            remote_id=remote_id,
            cmd1=cmd1,
            cmd2=cmd2,
            err1=err1,
            err2=err2,
            metadata=cast(ESPHomeAcceptedRXPacketMetadata, metadata),
        )
        return packet

    def _build_active_listening_semantic_artifact(
        self,
        *,
        event: ESPHomeRXEvent,
        remote_id: int,
        cmd1: int,
        cmd2: int,
        err1: int,
        err2: int,
        metadata: ESPHomeAcceptedRXPacketMetadata,
    ) -> LilyGoFifoSemanticArtifact:
        """Build the semantic artifact for a firmware-filtered RX packet."""

        return {
            "artifact_type": "lilygo_fifo_active_listening_packet",
            "artifact_class": "semantic",
            "semantic_comparable": True,
            "decode_success": True,
            "packet_normalized": True,
            "source": "esphome_active_listening",
            "provenance": "lilygo_cc1101_fifo_firmware_decoder",
            "event_id": event.event_id,
            "remote_id": f"{remote_id:06x}",
            "cmd1": f"{cmd1:02x}",
            "cmd2": f"{cmd2:02x}",
            "err1": f"{err1:02x}",
            "err2": f"{err2:02x}",
            "raw_payload_hex": event.raw_payload_hex,
            "capture_metadata": metadata,
        }

    def _build_fifo_semantic_artifact(
        self,
        *,
        event: ESPHomeRXEvent,
        candidate: DecodeCandidate,
        source: str,
        learning_accepted: bool,
    ) -> LilyGoFifoSemanticArtifact:
        sample = candidate.sample
        return {
            "artifact_type": "lilygo_fifo_semantic_candidate",
            "artifact_class": "semantic",
            "semantic_comparable": True,
            "decode_success": True,
            "packet_normalized": True,
            "source": source,
            "provenance": "lilygo_cc1101_fifo_candidate_scanner",
            "learning_equivalent_acceptance_path": learning_accepted,
            "learning_accepted": learning_accepted,
            "acceptance_policy": "candidate_scanner_success",
            "event_id": event.event_id,
            "remote_id": f"{sample.remote_id:06x}",
            "cmd1": f"{sample.cmd1:02x}",
            "cmd2": f"{sample.cmd2:02x}",
            "err1": f"{sample.err1:02x}",
            "err2": f"{sample.err2:02x}",
            "bit_offset": candidate.bit_offset,
            "symbol_offset": candidate.symbol_offset,
            "absolute_bit_offset": candidate.absolute_bit_offset,
            "repeat_count": candidate.repeat_count,
            "confidence": candidate.confidence,
            "occurrence_offsets": tuple(candidate.occurrence_offsets),
            "trailing_guard_valid": candidate.trailing_guard_valid,
            "trailing_guard_observed": candidate.trailing_guard_observed,
            "trailing_guard_warning": candidate.trailing_guard_warning,
            "validation_notes": tuple(candidate.validation_notes),
            "raw_payload_hex": event.raw_payload_hex,
            "candidate_raw_slice_hex": candidate.raw_slice.hex(),
            "symbol_stream": sample.symbols,
            "rssi": event.rssi,
            "lqi": event.lqi,
            "frequency_hz": event.frequency_hz,
            "timestamp_ms": event.timestamp_ms,
            "device_tick_ms": event.device_tick_ms,
            "capture_metadata": event.capture_metadata,
        }

    @staticmethod
    def _learning_sample_set_is_sufficient(samples: list[Any]) -> bool:
        if len(samples) < ESPHOME_LEARN_MIN_VALID_PACKETS:
            return False
        cmd1_samples = {sample.cmd1_tuple for sample in samples}
        cmd2_samples = {sample.cmd2_tuple for sample in samples}
        return (
            len(cmd1_samples) >= ESPHOME_LEARN_MIN_UNIQUE_CMD1_SAMPLES
            and len(cmd2_samples) >= ESPHOME_LEARN_MIN_UNIQUE_CMD2_SAMPLES
        )


def _serialize_contract_value(value: Any) -> Any:
    """Serialize contract dataclasses and StrEnum values for diagnostics."""

    if value is None:
        return None
    serialized = asdict(value)
    return _stringify_enum_values(serialized)


def _stringify_enum_values(value: Any) -> Any:
    """Convert enum-like values nested in diagnostics to plain strings."""

    if isinstance(value, dict):
        return {key: _stringify_enum_values(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_stringify_enum_values(item) for item in value]
    if hasattr(value, "value"):
        return value.value
    return value


def _abbreviate_hex(value: str, *, edge_chars: int = 32) -> str:
    """Return a log-safe preview of a potentially large hex payload."""

    if len(value) <= edge_chars * 2:
        return value
    return f"{value[:edge_chars]}...{value[-edge_chars:]}"


def _parse_event_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip().lower()
    if not text:
        return None
    return int(text[2:] if text.startswith("0x") else text, 16)
