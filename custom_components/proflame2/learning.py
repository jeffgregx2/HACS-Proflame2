"""Backend-independent remote learning orchestration for Proflame2.

This module intentionally works only with the RF backend abstraction and the
unified ``ProflamePacket`` model. It does not know or care whether packets come
from a Yard Stick One, a fake test backend, or a future networked RF node.
"""

from __future__ import annotations

import inspect
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

from .protocol.ecc import derive_ecc_profile
from .protocol.packet import ProflameFrame, ProflamePacket
from .rf.base import RFBackend
from .rf.fake import FakeRFBackend
from .rf.yardstick import YardStickBackend

from .const import (
    BACKEND_FAKE,
    BACKEND_YARDSTICK,
    CONF_C1,
    CONF_C2,
    CONF_D1,
    CONF_D2,
    CONF_REMOTE_ID,
    DATA_FAKE_LEARNING_DELAY,
    DATA_LEARNING_BACKEND_FACTORY,
    DOMAIN,
)

MIN_UNIQUE_CMD1_SAMPLES = 2
MIN_UNIQUE_CMD2_SAMPLES = 2
MIN_VALID_PACKETS = 3
DEFAULT_LEARN_TIMEOUT_SECONDS = 120.0
DEFAULT_RECEIVE_TIMEOUT_SECONDS = 0.5
DEFAULT_FAKE_LEARN_DELAY_SECONDS = 2.0

ERROR_TIMEOUT = "timeout"
ERROR_INCONSISTENT_REMOTE_ID = "inconsistent_remote_id"
ERROR_CONTRADICTORY_PROFILE = "contradictory_profile"
ERROR_AMBIGUOUS_PROFILE = "ambiguous_profile"
ERROR_BACKEND_UNAVAILABLE = "backend_unavailable"

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
    _seen_frames: set[tuple[int, int, int, int, int]] = field(default_factory=set)


async def async_create_learning_backend(
    hass: "HomeAssistant",
    backend_type: str,
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
        backend = YardStickBackend()
    else:
        raise ValueError(f"Unsupported backend type: {backend_type}")

    if backend_type == BACKEND_FAKE and isinstance(backend, FakeRFBackend):
        backend.receive_delay_seconds = float(
            domain_data.get(DATA_FAKE_LEARNING_DELAY, DEFAULT_FAKE_LEARN_DELAY_SECONDS)
        )

    await backend.connect()
    return backend


async def async_start_learning_session(
    hass: "HomeAssistant",
    backend_type: str,
    *,
    timeout: float = DEFAULT_LEARN_TIMEOUT_SECONDS,
    receive_timeout: float = DEFAULT_RECEIVE_TIMEOUT_SECONDS,
) -> LearnSession:
    """Create a backend and wrap it in a guided-learning session."""

    backend = await async_create_learning_backend(hass, backend_type)
    return LearnSession(
        backend=backend,
        step_timeout=timeout,
        receive_timeout=receive_timeout,
    )


async def async_close_learning_session(session: LearnSession | None) -> None:
    """Close a guided-learning session backend if it exists."""

    if session is not None:
        await session.backend.close()


def _seed_fake_learning_packets(backend: FakeRFBackend) -> None:
    """Preload deterministic packets so fake learn mode completes without hardware."""

    backend.queue_packets(
        ProflamePacket.from_frame(
            ProflameFrame(
                serial_id=0x3B3F02,
                cmd1=0x01,
                err1=0x76,
                cmd2=0x16,
                err2=0xEF,
            ),
            source="fake_learn",
        ),
        ProflamePacket.from_frame(
            ProflameFrame(
                serial_id=0x3B3F02,
                cmd1=0x31,
                err1=0x25,
                cmd2=0x26,
                err2=0xBC,
            ),
            source="fake_learn",
        ),
        ProflamePacket.from_frame(
            ProflameFrame(
                serial_id=0x3B3F02,
                cmd1=0x51,
                err1=0x83,
                cmd2=0x36,
                err2=0x8D,
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

    step_deadline = time.monotonic() + session.step_timeout

    while time.monotonic() < step_deadline:
        packet = await session.backend.receive(
            timeout=min(
                session.receive_timeout,
                max(0.0, step_deadline - time.monotonic()),
            )
        )
        if packet is None:
            continue

        session.packets_seen += 1
        session.valid_packets += 1

        if session.remote_id is None:
            session.remote_id = packet.remote_id
        elif packet.remote_id != session.remote_id:
            return LearnResult(
                success=False,
                remote_id=session.remote_id,
                packets_seen=session.packets_seen,
                valid_packets=session.valid_packets,
                warnings=session.warnings,
                error_code=ERROR_INCONSISTENT_REMOTE_ID,
                error=(
                    "Learn mode observed packets from multiple remote IDs and "
                    "cannot determine one stable fireplace profile."
                ),
            )

        frame_key = (
            packet.remote_id,
            packet.frame.cmd1,
            packet.frame.err1,
            packet.frame.cmd2,
            packet.frame.err2,
        )
        if frame_key in session._seen_frames:
            continue

        session._seen_frames.add(frame_key)
        session.packets.append(packet)
        return packet

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
        return None

    try:
        ecc = derive_ecc_profile(sorted(cmd1_samples), sorted(cmd2_samples))
    except ValueError as exc:
        message = str(exc)
        if "No stable" in message:
            return LearnResult(
                success=False,
                remote_id=session.remote_id,
                packets_seen=session.packets_seen,
                valid_packets=session.valid_packets,
                warnings=session.warnings,
                error_code=ERROR_CONTRADICTORY_PROFILE,
                error=(
                    "Observed packets contradict each other and do not converge "
                    "to one stable C/D profile."
                ),
            )
        if "Ambiguous stable" in message:
            return None
        return LearnResult(
            success=False,
            remote_id=session.remote_id,
            packets_seen=session.packets_seen,
            valid_packets=session.valid_packets,
            warnings=session.warnings,
            error_code=ERROR_CONTRADICTORY_PROFILE,
            error=message,
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
        packet = await backend.receive(
            timeout=min(receive_timeout, max(0.0, deadline - time.monotonic()))
        )
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
            or
            len(cmd1_samples) < min_unique_cmd1_samples
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
                    error=(
                        "Observed packets contradict each other and do not converge "
                        "to one stable C/D profile."
                    ),
                )
            if "Ambiguous stable" in message:
                last_ambiguity = (
                    "Observed packets are still ambiguous and do not yet prove one "
                    "stable C/D profile."
                )
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
    error = (
        last_ambiguity
        if last_ambiguity
        else "Timed out before learning enough stable Proflame2 packets."
    )
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
    hass: "HomeAssistant",
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
            error=str(exc),
        )
    finally:
        if backend is not None:
            await backend.close()
