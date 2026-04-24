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

from proflame2_protocol.ecc import derive_ecc_profile
from proflame2_rf.base import RFBackend
from proflame2_rf.fake import FakeRFBackend
from proflame2_rf.yardstick import YardStickBackend

from .const import (
    BACKEND_FAKE,
    BACKEND_YARDSTICK,
    CONF_C1,
    CONF_C2,
    CONF_D1,
    CONF_D2,
    CONF_REMOTE_ID,
    DATA_LEARNING_BACKEND_FACTORY,
    DOMAIN,
)

MIN_UNIQUE_CMD1_SAMPLES = 2
MIN_UNIQUE_CMD2_SAMPLES = 2
MIN_VALID_PACKETS = 3
DEFAULT_LEARN_TIMEOUT_SECONDS = 10.0
DEFAULT_RECEIVE_TIMEOUT_SECONDS = 0.5

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
    elif backend_type == BACKEND_YARDSTICK:
        backend = YardStickBackend()
    else:
        raise ValueError(f"Unsupported backend type: {backend_type}")

    await backend.connect()
    return backend


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
