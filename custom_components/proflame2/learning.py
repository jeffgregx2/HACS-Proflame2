"""Backend-independent remote learning orchestration for Proflame2.

This module intentionally works only with the RF backend abstraction and the
unified ``ProflamePacket`` model. It does not know or care whether packets come
from a Yard Stick One, a fake test backend, or a future networked RF node.
"""

from __future__ import annotations

import inspect
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

from .const import (
    BACKEND_ESPHOME,
    BACKEND_FAKE,
    BACKEND_YARDSTICK,
    CONF_C1,
    CONF_C2,
    CONF_D1,
    CONF_D2,
    CONF_REMOTE_ID,
    DATA_ESPHOME_TRANSPORT_FACTORY,
    DATA_FAKE_LEARNING_DELAY,
    DATA_LEARNING_BACKEND_FACTORY,
    DATA_YARDSTICK_LEARNING_FREQUENCY_HZ,
    DATA_YARDSTICK_LEARNING_PACKET_LENGTH_BYTES,
    DATA_YARDSTICK_LEARNING_SWEEP_ENABLED,
    DOMAIN,
)
from .packet_debug import (
    async_disable_packet_debug_logging,
    async_enable_packet_debug_logging,
    get_packet_debug_logger,
)
from .protocol.ecc import derive_ecc_profile
from .protocol.packet import ProflameFrame, ProflamePacket
from .rf.base import RFBackend
from .rf.esphome.transport import HomeAssistantESPHomeTransport
from .rf.esphome_api import ESPHomeAPIBackend
from .rf.fake import FakeRFBackend
from .rf.yardstick import (
    YARDSTICK_RX_LEARNING_FREQUENCY_HZ,
    YARDSTICK_RX_LEARNING_PACKET_BYTES,
    YARDSTICK_RX_LEARNING_SWEEP_ENABLED,
    YardStickBackend,
)

_LOGGER = logging.getLogger(__name__)

MIN_UNIQUE_CMD1_SAMPLES = 2
MIN_UNIQUE_CMD2_SAMPLES = 2
MIN_VALID_PACKETS = 3
DEFAULT_LEARN_TIMEOUT_SECONDS = 120.0
DEFAULT_RECEIVE_TIMEOUT_SECONDS = 1.0
DEFAULT_FAKE_LEARN_DELAY_SECONDS = 2.0

ERROR_TIMEOUT = "timeout"
ERROR_INCONSISTENT_REMOTE_ID = "inconsistent_remote_id"
ERROR_CONTRADICTORY_PROFILE = "contradictory_profile"
ERROR_AMBIGUOUS_PROFILE = "ambiguous_profile"
ERROR_BACKEND_UNAVAILABLE = "backend_unavailable"
RESTORE_PROMPT_LABELS = {"restore_power_on"}

LearningBackendFactory = Callable[[str], RFBackend | Awaitable[RFBackend]]


@dataclass
class LearnResult:
    """Structured result for backend-independent remote learning."""

    success: bool
    remote_id: int | None = None
    c1: int | None = None
    d1: int | None = None
    c2: int | None = None
    d2: int | None = None
    packets_seen: int = 0
    valid_packets: int = 0
    warnings: list[str] = field(default_factory=list)
    error_code: str | None = None
    error: str | None = None
    final_packet: ProflamePacket | None = None

    @property
    def data(self) -> dict[str, int]:
        """Return learned permanent profile fields for config-entry storage."""

        if not self.success or None in (
            self.remote_id,
            self.c1,
            self.d1,
            self.c2,
            self.d2,
        ):
            raise ValueError("Learn result does not contain a complete remote profile.")
        return {
            CONF_REMOTE_ID: self.remote_id,
            CONF_C1: self.c1,
            CONF_D1: self.d1,
            CONF_C2: self.c2,
            CONF_D2: self.d2,
        }


@dataclass
class LearnSession:
    """Stateful guided-learning session shared across multiple UI prompts.

    ``step_timeout`` is intentionally a per-prompt timeout, not a whole-session
    timeout. If the user answers one prompt within the allowed window, the next
    prompt receives a fresh timeout window of its own.
    """

    backend: RFBackend
    step_timeout: float
    receive_timeout: float
    packets_seen: int = 0
    valid_packets: int = 0
    warnings: list[str] = field(default_factory=list)
    remote_id: int | None = None
    packets: list[ProflamePacket] = field(default_factory=list)
    debug_logging_enabled: bool = False
    hass: HomeAssistant | None = None
    prompt_index: int = 0
    prompt_label: str = "unknown"
    prompt_instruction: str = ""
    _seen_frames: set[tuple[int, int, int, int, int]] = field(default_factory=set)


@dataclass(frozen=True)
class _LearningPromptContext:
    """Timing context for one guided-learning prompt window."""

    step_deadline: float


@dataclass(frozen=True)
class _SeenLearningFrameResult:
    """Decision for an already-seen guided-learning frame."""

    should_continue: bool
    packet: ProflamePacket | None = None


async def async_create_learning_backend(
    hass: HomeAssistant,
    backend_type: str,
    *,
    esphome_entry_id: str | None = None,
    debug_logging_enabled: bool = False,
) -> RFBackend:
    """Create and connect a backend for config-flow learning."""

    domain_data = hass.data.setdefault(DOMAIN, {})
    factory = domain_data.get(DATA_LEARNING_BACKEND_FACTORY)
    backend: RFBackend

    if factory is not None:
        maybe_backend = factory(backend_type)
        backend = await maybe_backend if inspect.isawaitable(maybe_backend) else maybe_backend
    elif backend_type == BACKEND_FAKE:
        backend = FakeRFBackend()
        _seed_fake_learning_packets(backend)
    elif backend_type == BACKEND_YARDSTICK:
        backend = YardStickBackend(
            hass=hass,
            frequency_hz=int(
                domain_data.get(
                    DATA_YARDSTICK_LEARNING_FREQUENCY_HZ,
                    YARDSTICK_RX_LEARNING_FREQUENCY_HZ,
                )
            ),
            packet_length_bytes=int(
                domain_data.get(
                    DATA_YARDSTICK_LEARNING_PACKET_LENGTH_BYTES,
                    YARDSTICK_RX_LEARNING_PACKET_BYTES,
                )
            ),
            sweep_enabled=bool(
                domain_data.get(
                    DATA_YARDSTICK_LEARNING_SWEEP_ENABLED,
                    YARDSTICK_RX_LEARNING_SWEEP_ENABLED,
                )
            ),
        )
    elif backend_type == BACKEND_ESPHOME:
        if not esphome_entry_id:
            raise ValueError("ESPHome learning requires a linked ESPHome entry id.")
        transport_factory = domain_data.get(DATA_ESPHOME_TRANSPORT_FACTORY)
        if transport_factory is not None:
            maybe_transport = transport_factory(hass, None)
            transport = await maybe_transport if inspect.isawaitable(maybe_transport) else maybe_transport
        else:
            transport = HomeAssistantESPHomeTransport(
                hass,
                linked_entry_id=esphome_entry_id,
                controller_id=f"esphome-learning:{esphome_entry_id}",
                debug_logging_enabled=debug_logging_enabled,
            )
        backend = ESPHomeAPIBackend(
            transport=transport,
            debug_logging_enabled=debug_logging_enabled,
        )
    else:
        raise ValueError(f"Unsupported backend type: {backend_type}")

    if backend_type == BACKEND_FAKE and isinstance(backend, FakeRFBackend):
        backend.receive_delay_seconds = float(
            domain_data.get(DATA_FAKE_LEARNING_DELAY, DEFAULT_FAKE_LEARN_DELAY_SECONDS)
        )

    await backend.connect()
    return backend


async def async_start_learning_session(
    hass: HomeAssistant,
    backend_type: str,
    *,
    debug_logging: bool = False,
    esphome_entry_id: str | None = None,
    timeout: float = DEFAULT_LEARN_TIMEOUT_SECONDS,
    receive_timeout: float = DEFAULT_RECEIVE_TIMEOUT_SECONDS,
) -> LearnSession:
    """Create a backend and wrap it in a guided-learning session."""

    _LOGGER.warning(
        "Proflame2 learning debug logging is %s for backend=%s",
        "ENABLED" if debug_logging else "DISABLED",
        backend_type,
    )
    if debug_logging:
        log_paths = await async_enable_packet_debug_logging(hass)
        _LOGGER.warning(
            "Proflame2 packet debug files enabled primary=%s decode_failures=%s for backend=%s",
            log_paths.primary_log_path,
            log_paths.decode_failure_log_path,
            backend_type,
        )
        get_packet_debug_logger().info(
            "Enabled packet debug logging for learning session backend=%s primary_file=%s decode_failure_file=%s",
            backend_type,
            log_paths.primary_log_path,
            log_paths.decode_failure_log_path,
        )
    try:
        backend = await async_create_learning_backend(
            hass,
            backend_type,
            esphome_entry_id=esphome_entry_id,
            debug_logging_enabled=debug_logging,
        )
    except Exception:
        if debug_logging:
            await async_disable_packet_debug_logging(hass)
        raise
    set_active_listening_enabled = getattr(backend, "set_active_listening_enabled", None)
    if callable(set_active_listening_enabled):
        maybe_awaitable = set_active_listening_enabled(True)
        if inspect.isawaitable(maybe_awaitable):
            await maybe_awaitable
    update_learning_mode = getattr(backend, "update_learning_mode", None)
    if callable(update_learning_mode):
        maybe_awaitable = update_learning_mode(
            active=True,
            step_title="Learn",
            instruction="Waiting for guided learning prompt",
            status="Listening",
        )
        if inspect.isawaitable(maybe_awaitable):
            await maybe_awaitable
    return LearnSession(
        backend=backend,
        step_timeout=timeout,
        receive_timeout=receive_timeout,
        debug_logging_enabled=debug_logging,
        hass=hass,
    )


async def async_close_learning_session(session: LearnSession | None) -> None:
    """Close a guided-learning session backend if it exists."""

    if session is not None:
        _LOGGER.warning(
            "Proflame2 closing learning session for backend=%s debug_logging=%s",
            getattr(session.backend, "name", session.backend.__class__.__name__),
            session.debug_logging_enabled,
        )
        try:
            update_learning_mode = getattr(session.backend, "update_learning_mode", None)
            if callable(update_learning_mode):
                try:
                    maybe_awaitable = update_learning_mode(
                        active=False,
                        step_title="Learn",
                        instruction="Not active",
                        status="Idle",
                    )
                    if inspect.isawaitable(maybe_awaitable):
                        await maybe_awaitable
                except Exception:
                    _LOGGER.exception("Proflame2 learning-mode shutdown update failed")
            await session.backend.close()
        finally:
            if session.debug_logging_enabled and session.hass is not None:
                get_packet_debug_logger().info("Closed packet debug learning session")
                await async_disable_packet_debug_logging(session.hass)
            _LOGGER.warning(
                "Proflame2 learning session closed for backend=%s",
                getattr(session.backend, "name", session.backend.__class__.__name__),
            )


def _seed_fake_learning_packets(backend: FakeRFBackend) -> None:
    """Preload deterministic packets so fake learn mode completes without hardware."""

    backend.queue_packets(
        ProflamePacket.from_frame(
            ProflameFrame(
                serial_id=0x3B3F02,
                cmd1=0x01,
                err1=0x76,
                cmd2=0x06,
                err2=0xDE,
            ),
            source="fake_learn",
        ),
        ProflamePacket.from_frame(
            ProflameFrame(
                serial_id=0x3B3F02,
                cmd1=0x00,
                err1=0x57,
                cmd2=0x06,
                err2=0xDE,
            ),
            source="fake_learn",
        ),
        ProflamePacket.from_frame(
            ProflameFrame(
                serial_id=0x3B3F02,
                cmd1=0x01,
                err1=0x76,
                cmd2=0x06,
                err2=0xDE,
            ),
            source="fake_learn",
        ),
        ProflamePacket.from_frame(
            ProflameFrame(
                serial_id=0x3B3F02,
                cmd1=0x01,
                err1=0x76,
                cmd2=0x05,
                err2=0xBD,
            ),
            source="fake_learn",
        ),
    )


async def async_capture_next_learning_packet(session: LearnSession) -> ProflamePacket | LearnResult:
    """Capture the next distinct packet for one guided-learning prompt.

    The timeout applies only to the current prompted action. A slow but active
    user should be able to complete the full guided flow as long as each prompt
    is answered within its own timeout window.
    """

    context = _start_learning_prompt_wait(session)
    await _async_update_learning_prompt_status(session, "Listening")

    while time.monotonic() < context.step_deadline:
        receive_timeout = _learning_prompt_remaining_timeout(session, context)
        packet = await _receive_learning_packet_for_prompt(session, receive_timeout)
        if packet is None:
            _record_no_learning_packet(session, receive_timeout)
            continue

        _record_learning_packet_observed(session, receive_timeout, packet)
        remote_failure = _ensure_learning_remote_id(session, receive_timeout, packet)
        if remote_failure is not None:
            return remote_failure

        frame_key = _learning_frame_key(packet)
        if frame_key in session._seen_frames:
            seen_result = _handle_seen_learning_frame(session, receive_timeout, packet)
            if seen_result.packet is not None:
                return seen_result.packet
            if seen_result.should_continue:
                continue

        return await _accept_distinct_learning_packet(session, receive_timeout, packet, frame_key)

    return await _finish_learning_prompt_timeout(session)


def _start_learning_prompt_wait(session: LearnSession) -> _LearningPromptContext:
    context = _LearningPromptContext(step_deadline=time.monotonic() + session.step_timeout)
    _session_debug(
        session,
        "prompt wait started backend=%s step_timeout=%.3fs receive_timeout=%.3fs "
        "packets_seen=%s valid_packets=%s distinct_packets=%s",
        getattr(session.backend, "name", session.backend.__class__.__name__),
        session.step_timeout,
        session.receive_timeout,
        session.packets_seen,
        session.valid_packets,
        len(session.packets),
    )
    return context


def _learning_prompt_remaining_timeout(session: LearnSession, context: _LearningPromptContext) -> float:
    return min(session.receive_timeout, max(0.0, context.step_deadline - time.monotonic()))


async def _receive_learning_packet_for_prompt(
    session: LearnSession,
    receive_timeout: float,
) -> ProflamePacket | None:
    try:
        return await session.backend.receive(timeout=receive_timeout)
    except Exception as exc:
        _handle_learning_receive_exception(session, receive_timeout, exc)
        raise


def _handle_learning_receive_exception(session: LearnSession, receive_timeout: float, exc: Exception) -> None:
    _LOGGER.exception(
        "Proflame2 guided learning receive failed prompt_index=%s prompt_label=%s packets_seen=%s valid_packets=%s distinct_packets=%s",
        session.prompt_index,
        session.prompt_label,
        session.packets_seen,
        session.valid_packets,
        len(session.packets),
    )
    if session.debug_logging_enabled:
        get_packet_debug_logger().exception(
            "learning: receive exception prompt_index=%s prompt_label=%s packets_seen=%s valid_packets=%s distinct_packets=%s exception_type=%s error=%s",
            session.prompt_index,
            session.prompt_label,
            session.packets_seen,
            session.valid_packets,
            len(session.packets),
            type(exc).__name__,
            exc,
        )
    _log_learning_receive_heartbeat(
        session,
        outcome="exception",
        receive_timeout=receive_timeout,
        error=str(exc),
        exception_type=type(exc).__name__,
    )


def _record_no_learning_packet(session: LearnSession, receive_timeout: float) -> None:
    _log_learning_receive_heartbeat(
        session,
        outcome="no_packet",
        receive_timeout=receive_timeout,
    )


def _record_learning_packet_observed(
    session: LearnSession,
    receive_timeout: float,
    packet: ProflamePacket,
) -> None:
    session.packets_seen += 1
    session.valid_packets += 1
    _log_learning_receive_heartbeat(
        session,
        outcome="decoded_packet",
        receive_timeout=receive_timeout,
        packet=packet,
    )
    _session_debug(
        session,
        "packet observed packets_seen=%s valid_packets=%s remote_id=%06x "
        "cmd1=0x%02X err1=0x%02X cmd2=0x%02X err2=0x%02X",
        session.packets_seen,
        session.valid_packets,
        packet.remote_id,
        packet.frame.cmd1,
        packet.frame.err1,
        packet.frame.cmd2,
        packet.frame.err2,
    )


def _ensure_learning_remote_id(
    session: LearnSession,
    receive_timeout: float,
    packet: ProflamePacket,
) -> LearnResult | None:
    if session.remote_id is None:
        session.remote_id = packet.remote_id
        _session_debug(session, "locked learning session to remote_id=%06x", packet.remote_id)
        return None
    if packet.remote_id == session.remote_id:
        return None
    _log_learning_receive_heartbeat(
        session,
        outcome="wrong_remote_id",
        receive_timeout=receive_timeout,
        packet=packet,
    )
    _session_debug(
        session,
        "remote_id mismatch expected=%06x observed=%06x",
        session.remote_id,
        packet.remote_id,
    )
    return _build_inconsistent_remote_result(session)


def _handle_seen_learning_frame(
    session: LearnSession,
    receive_timeout: float,
    packet: ProflamePacket,
) -> _SeenLearningFrameResult:
    if session.prompt_label in RESTORE_PROMPT_LABELS:
        _log_learning_receive_heartbeat(
            session,
            outcome="restore_packet_observed",
            receive_timeout=receive_timeout,
            packet=packet,
        )
        _session_debug(
            session,
            "restore-step packet observed remote_id=%06x cmd1=0x%02X err1=0x%02X cmd2=0x%02X err2=0x%02X",
            packet.remote_id,
            packet.frame.cmd1,
            packet.frame.err1,
            packet.frame.cmd2,
            packet.frame.err2,
        )
        return _SeenLearningFrameResult(should_continue=False, packet=packet)
    _log_learning_receive_heartbeat(
        session,
        outcome="duplicate_ignored",
        receive_timeout=receive_timeout,
        packet=packet,
    )
    _session_debug(
        session,
        "duplicate packet ignored remote_id=%06x cmd1=0x%02X err1=0x%02X cmd2=0x%02X err2=0x%02X",
        packet.remote_id,
        packet.frame.cmd1,
        packet.frame.err1,
        packet.frame.cmd2,
        packet.frame.err2,
    )
    return _SeenLearningFrameResult(should_continue=True)


async def _accept_distinct_learning_packet(
    session: LearnSession,
    receive_timeout: float,
    packet: ProflamePacket,
    frame_key: tuple[int, int, int, int, int],
) -> ProflamePacket:
    session._seen_frames.add(frame_key)
    session.packets.append(packet)
    await _async_update_learning_prompt_status(session, "Packet accepted")
    _log_learning_receive_heartbeat(
        session,
        outcome="accepted_distinct_packet",
        receive_timeout=receive_timeout,
        packet=packet,
    )
    _session_debug(
        session,
        "accepted distinct packet count=%s remote_id=%06x",
        len(session.packets),
        packet.remote_id,
    )
    return packet


async def _finish_learning_prompt_timeout(session: LearnSession) -> LearnResult:
    _session_debug(
        session,
        "prompt timed out after %.3fs packets_seen=%s valid_packets=%s distinct_packets=%s",
        session.step_timeout,
        session.packets_seen,
        session.valid_packets,
        len(session.packets),
    )
    await _async_update_learning_prompt_status(session, "Timed out")
    return _build_prompt_timeout_result(session)


async def _async_update_learning_prompt_status(session: LearnSession, status: str) -> None:
    """Publish guided-learning prompt state when the backend has UI support."""

    update_learning_mode = getattr(session.backend, "update_learning_mode", None)
    if not callable(update_learning_mode):
        return
    maybe_awaitable = update_learning_mode(
        active=True,
        step_title=f"Learn {session.prompt_index + 1}",
        instruction=session.prompt_instruction or session.prompt_label,
        status=status,
    )
    if inspect.isawaitable(maybe_awaitable):
        await maybe_awaitable


def _learning_frame_key(packet: ProflamePacket) -> tuple[int, int, int, int, int]:
    """Return the identity used to suppress duplicate guided-learning packets."""

    return (
        packet.remote_id,
        packet.frame.cmd1,
        packet.frame.err1,
        packet.frame.cmd2,
        packet.frame.err2,
    )


def _build_inconsistent_remote_result(session: LearnSession) -> LearnResult:
    """Build the failure result for mixed-remote learning captures."""

    return LearnResult(
        success=False,
        remote_id=session.remote_id,
        packets_seen=session.packets_seen,
        valid_packets=session.valid_packets,
        warnings=session.warnings,
        error_code=ERROR_INCONSISTENT_REMOTE_ID,
        error=(
            "Learn mode observed packets from multiple remote IDs and " "cannot determine one stable fireplace profile."
        ),
    )


def _build_prompt_timeout_result(session: LearnSession) -> LearnResult:
    """Build the failure result for one unanswered guided-learning prompt."""

    return LearnResult(
        success=False,
        remote_id=session.remote_id,
        packets_seen=session.packets_seen,
        valid_packets=session.valid_packets,
        warnings=session.warnings,
        error_code=ERROR_TIMEOUT,
        error="Timed out waiting for the requested remote action before learning could continue.",
    )


def derive_learn_result_from_session(session: LearnSession) -> LearnResult | None:
    """Attempt to derive a stable profile from the packets seen so far."""

    cmd1_samples = {(packet.frame.cmd1, packet.frame.err1) for packet in session.packets}
    cmd2_samples = {(packet.frame.cmd2, packet.frame.err2) for packet in session.packets}

    if (
        session.valid_packets < MIN_VALID_PACKETS
        or len(cmd1_samples) < MIN_UNIQUE_CMD1_SAMPLES
        or len(cmd2_samples) < MIN_UNIQUE_CMD2_SAMPLES
    ):
        _session_debug(
            session,
            "insufficient distinct samples valid_packets=%s unique_cmd1=%s unique_cmd2=%s",
            session.valid_packets,
            len(cmd1_samples),
            len(cmd2_samples),
        )
        return None

    try:
        ecc = derive_ecc_profile(sorted(cmd1_samples), sorted(cmd2_samples))
    except ValueError as exc:
        message = str(exc)
        if "No stable" in message:
            _session_debug(session, "contradictory profile derivation: %s", message)
            return LearnResult(
                success=False,
                remote_id=session.remote_id,
                packets_seen=session.packets_seen,
                valid_packets=session.valid_packets,
                warnings=session.warnings,
                error_code=ERROR_CONTRADICTORY_PROFILE,
                error=("Observed packets contradict each other and do not converge " "to one stable C/D profile."),
            )
        if "Ambiguous stable" in message:
            _session_debug(session, "ambiguous profile derivation: %s", message)
            return None
        _session_debug(session, "unexpected profile derivation failure: %s", message)
        return LearnResult(
            success=False,
            remote_id=session.remote_id,
            packets_seen=session.packets_seen,
            valid_packets=session.valid_packets,
            warnings=session.warnings,
            error_code=ERROR_CONTRADICTORY_PROFILE,
            error=message,
        )

    _session_debug(
        session,
        "learned stable profile remote_id=%06x c1=%s d1=%s c2=%s d2=%s",
        session.remote_id,
        ecc.c1,
        ecc.d1,
        ecc.c2,
        ecc.d2,
    )
    return LearnResult(
        success=True,
        remote_id=session.remote_id,
        c1=ecc.c1,
        d1=ecc.d1,
        c2=ecc.c2,
        d2=ecc.d2,
        packets_seen=session.packets_seen,
        valid_packets=session.valid_packets,
        warnings=session.warnings,
        final_packet=session.packets[-1] if session.packets else None,
    )


async def async_learn_remote_profile(
    backend: RFBackend,
    *,
    timeout: float = DEFAULT_LEARN_TIMEOUT_SECONDS,
    receive_timeout: float = DEFAULT_RECEIVE_TIMEOUT_SECONDS,
    min_unique_cmd1_samples: int = MIN_UNIQUE_CMD1_SAMPLES,
    min_unique_cmd2_samples: int = MIN_UNIQUE_CMD2_SAMPLES,
    min_valid_packets: int = MIN_VALID_PACKETS,
) -> LearnResult:
    """Learn a stable Proflame2 remote profile from received packets.

    The learning algorithm is intentionally conservative:

    - every accepted packet must share the same remote_id
    - we require at least a small amount of command diversity before declaring
      success, even though the current ECC algorithm often resolves uniquely
      from fewer samples
    - contradictions fail immediately
    - ambiguity is allowed to continue until timeout, but never guessed
    """

    deadline = time.monotonic() + timeout
    packets_seen = 0
    valid_packets = 0
    warnings: list[str] = []

    remote_id: int | None = None
    cmd1_samples: set[tuple[int, int]] = set()
    cmd2_samples: set[tuple[int, int]] = set()
    last_ambiguity: str | None = None

    while time.monotonic() < deadline:
        packet = await backend.receive(timeout=min(receive_timeout, max(0.0, deadline - time.monotonic())))
        if packet is None:
            continue

        packets_seen += 1
        valid_packets += 1

        if remote_id is None:
            remote_id = packet.remote_id
        elif packet.remote_id != remote_id:
            return LearnResult(
                success=False,
                remote_id=remote_id,
                packets_seen=packets_seen,
                valid_packets=valid_packets,
                warnings=warnings,
                error_code=ERROR_INCONSISTENT_REMOTE_ID,
                error=(
                    "Learn mode observed packets from multiple remote IDs and "
                    "cannot determine one stable fireplace profile."
                ),
            )

        cmd1_samples.add((packet.frame.cmd1, packet.frame.err1))
        cmd2_samples.add((packet.frame.cmd2, packet.frame.err2))

        if (
            valid_packets < min_valid_packets
            or len(cmd1_samples) < min_unique_cmd1_samples
            or len(cmd2_samples) < min_unique_cmd2_samples
        ):
            continue

        try:
            ecc = derive_ecc_profile(sorted(cmd1_samples), sorted(cmd2_samples))
        except ValueError as exc:
            message = str(exc)
            if "No stable" in message:
                return LearnResult(
                    success=False,
                    remote_id=remote_id,
                    packets_seen=packets_seen,
                    valid_packets=valid_packets,
                    warnings=warnings,
                    error_code=ERROR_CONTRADICTORY_PROFILE,
                    error=("Observed packets contradict each other and do not converge " "to one stable C/D profile."),
                )
            if "Ambiguous stable" in message:
                last_ambiguity = "Observed packets are still ambiguous and do not yet prove one " "stable C/D profile."
                continue
            return LearnResult(
                success=False,
                remote_id=remote_id,
                packets_seen=packets_seen,
                valid_packets=valid_packets,
                warnings=warnings,
                error_code=ERROR_CONTRADICTORY_PROFILE,
                error=message,
            )

        return LearnResult(
            success=True,
            remote_id=remote_id,
            c1=ecc.c1,
            d1=ecc.d1,
            c2=ecc.c2,
            d2=ecc.d2,
            packets_seen=packets_seen,
            valid_packets=valid_packets,
            warnings=warnings,
        )

    error_code = ERROR_AMBIGUOUS_PROFILE if last_ambiguity else ERROR_TIMEOUT
    error = last_ambiguity if last_ambiguity else "Timed out before learning enough stable Proflame2 packets."
    return LearnResult(
        success=False,
        remote_id=remote_id,
        packets_seen=packets_seen,
        valid_packets=valid_packets,
        warnings=warnings,
        error_code=error_code,
        error=error,
    )


async def async_run_learning_with_backend(
    hass: HomeAssistant,
    backend_type: str,
    *,
    timeout: float = DEFAULT_LEARN_TIMEOUT_SECONDS,
    receive_timeout: float = DEFAULT_RECEIVE_TIMEOUT_SECONDS,
) -> LearnResult:
    """Create a backend, run learning, and always close the backend."""

    backend: RFBackend | None = None
    try:
        backend = await async_create_learning_backend(hass, backend_type)
        return await async_learn_remote_profile(
            backend,
            timeout=timeout,
            receive_timeout=receive_timeout,
        )
    except Exception as exc:
        return LearnResult(
            success=False,
            packets_seen=0,
            valid_packets=0,
            error_code=ERROR_BACKEND_UNAVAILABLE,
            error=f"{type(exc).__name__}: {exc}",
        )
    finally:
        if backend is not None:
            await backend.close()


def _session_debug(session: LearnSession, message: str, *args: object) -> None:
    """Write one learning-session debug line when packet logging is enabled."""

    if not session.debug_logging_enabled:
        return
    get_packet_debug_logger().info("learning: " + message, *args)


def _log_learning_receive_heartbeat(
    session: LearnSession,
    *,
    outcome: str,
    receive_timeout: float,
    packet: ProflamePacket | None = None,
    error: str | None = None,
    exception_type: str | None = None,
) -> None:
    """Log one compact heartbeat for each guided-learning receive window."""

    if not session.debug_logging_enabled:
        return

    status = getattr(session.backend, "last_receive_status", None)
    active_frequency_hz = getattr(status, "active_frequency_hz", None)
    payload_length_bytes = getattr(status, "payload_length_bytes", None)
    candidate_count = getattr(status, "candidate_count", None)
    reason = getattr(status, "reason", None)
    status_exception_type = getattr(status, "exception_type", None)
    status_exception_message = getattr(status, "exception_message", None)
    if outcome == "wrong_remote_id":
        reason = "wrong_remote_id"
    elif outcome == "duplicate_ignored":
        reason = "duplicate_packet"
    elif outcome == "restore_packet_observed":
        reason = "restore_step_duplicate"
    elif outcome == "accepted_distinct_packet":
        reason = "useful_for_derivation"
    elif outcome == "exception":
        reason = reason or exception_type or status_exception_type or "exception"

    resolved_error = error or status_exception_message

    get_packet_debug_logger().info(
        "learning: heartbeat prompt_index=%s prompt_label=%s outcome=%s receive_timeout=%.3fs "
        "packets_seen=%s valid_packets=%s distinct_packets=%s active_freq_hz=%s payload_length_bytes=%s "
        "candidate_count=%s reason=%s error=%s remote_id=%s cmd1=%s err1=%s cmd2=%s err2=%s repeat_count=%s",
        session.prompt_index,
        session.prompt_label,
        outcome,
        receive_timeout,
        session.packets_seen,
        session.valid_packets,
        len(session.packets),
        active_frequency_hz,
        payload_length_bytes,
        candidate_count,
        reason,
        resolved_error,
        None if packet is None else f"{packet.remote_id:06x}",
        None if packet is None else f"0x{packet.frame.cmd1:02X}",
        None if packet is None else f"0x{packet.frame.err1:02X}",
        None if packet is None else f"0x{packet.frame.cmd2:02X}",
        None if packet is None else f"0x{packet.frame.err2:02X}",
        getattr(status, "repeat_count", None),
    )
