"""Tests for backend-independent Proflame2 remote learning."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.protocol

from custom_components.proflame2.learning import (
    ERROR_AMBIGUOUS_PROFILE,
    ERROR_BACKEND_UNAVAILABLE,
    ERROR_CONTRADICTORY_PROFILE,
    ERROR_INCONSISTENT_REMOTE_ID,
    ERROR_TIMEOUT,
    LearnResult,
    LearnSession,
    async_capture_next_learning_packet,
    async_create_learning_backend,
    async_learn_remote_profile,
    async_run_learning_with_backend,
    derive_learn_result_from_session,
)
from custom_components.proflame2.const import (
    BACKEND_YARDSTICK,
    DOMAIN,
)
from custom_components.proflame2.protocol.packet import ProflameFrame, ProflamePacket
from custom_components.proflame2.rf.base import BackendCapabilities, ReceiveStatus, RFBackend
from custom_components.proflame2.rf.fake import FakeRFBackend
from custom_components.proflame2.rf.yardstick import (
    PROFLAME2_FREQUENCY_HZ,
    YARDSTICK_RX_LEARNING_FREQUENCY_HZ,
    YARDSTICK_RX_LEARNING_PACKET_BYTES,
    YARDSTICK_RX_LEARNING_SWEEP_ENABLED,
    YardStickBackend,
)


def _packet(
    *,
    remote_id: int,
    cmd1: int,
    err1: int,
    cmd2: int,
    err2: int,
) -> ProflamePacket:
    return ProflamePacket.from_frame(
        ProflameFrame(
            serial_id=remote_id,
            cmd1=cmd1,
            err1=err1,
            cmd2=cmd2,
            err2=err2,
        ),
        source="test",
    )


def test_learning_succeeds_from_valid_packets() -> None:
    """Repeated valid packets should converge on one stable remote profile."""

    async def _run() -> None:
        backend = FakeRFBackend()
        await backend.connect()
        backend.queue_packets(
            _packet(remote_id=0x3B3F02, cmd1=0x01, err1=0x76, cmd2=0x16, err2=0xEF),
            _packet(remote_id=0x3B3F02, cmd1=0x31, err1=0x25, cmd2=0x26, err2=0xBC),
            _packet(remote_id=0x3B3F02, cmd1=0x51, err1=0x83, cmd2=0x36, err2=0x8D),
        )

        result = await async_learn_remote_profile(backend, timeout=0.2, receive_timeout=0.01)

        assert result.success is True
        assert result.remote_id == 0x3B3F02
        assert (result.c1, result.d1, result.c2, result.d2) == (5, 7, 1, 8)
        assert result.packets_seen == 3
        assert result.valid_packets == 3

    asyncio.run(_run())


def test_learning_times_out_cleanly() -> None:
    """No packets should produce a clean timeout result."""

    async def _run() -> None:
        backend = FakeRFBackend()
        await backend.connect()

        result = await async_learn_remote_profile(backend, timeout=0.05, receive_timeout=0.01)

        assert result.success is False
        assert result.error_code == ERROR_TIMEOUT
        assert result.remote_id is None
        assert result.packets_seen == 0
        assert result.valid_packets == 0

    asyncio.run(_run())


def test_learning_rejects_inconsistent_remote_ids() -> None:
    """Mixed remote IDs must fail instead of being guessed."""

    async def _run() -> None:
        backend = FakeRFBackend()
        await backend.connect()
        backend.queue_packets(
            _packet(remote_id=0x3B3F02, cmd1=0x01, err1=0x76, cmd2=0x16, err2=0xEF),
            _packet(remote_id=0x3B3F02, cmd1=0x51, err1=0x83, cmd2=0x36, err2=0x8D),
            _packet(remote_id=0x3B3F03, cmd1=0x31, err1=0x25, cmd2=0x26, err2=0xBC),
        )

        result = await async_learn_remote_profile(backend, timeout=0.2, receive_timeout=0.01)

        assert result.success is False
        assert result.error_code == ERROR_INCONSISTENT_REMOTE_ID
        assert result.remote_id == 0x3B3F02
        assert result.packets_seen == 3

    asyncio.run(_run())


def test_learning_rejects_contradictory_samples() -> None:
    """Contradictory command/Err observations must fail clearly."""

    async def _run() -> None:
        backend = FakeRFBackend()
        await backend.connect()
        backend.queue_packets(
            _packet(remote_id=0x3B3F02, cmd1=0x01, err1=0x76, cmd2=0x16, err2=0xEF),
            _packet(remote_id=0x3B3F02, cmd1=0x31, err1=0x25, cmd2=0x26, err2=0xBC),
            _packet(remote_id=0x3B3F02, cmd1=0x01, err1=0x77, cmd2=0x16, err2=0xEF),
        )

        result = await async_learn_remote_profile(backend, timeout=0.2, receive_timeout=0.01)

        assert result.success is False
        assert result.error_code == ERROR_CONTRADICTORY_PROFILE
        assert result.remote_id == 0x3B3F02
        assert result.packets_seen == 3

    asyncio.run(_run())


def test_learning_reports_ambiguity_without_guessing() -> None:
    """Ambiguous derivation should time out with a specific ambiguity error."""

    async def _run() -> None:
        backend = FakeRFBackend()
        await backend.connect()
        backend.queue_packets(
            _packet(remote_id=0x3B3F02, cmd1=0x01, err1=0x76, cmd2=0x16, err2=0xEF),
            _packet(remote_id=0x3B3F02, cmd1=0x31, err1=0x25, cmd2=0x26, err2=0xBC),
            _packet(remote_id=0x3B3F02, cmd1=0x51, err1=0x83, cmd2=0x36, err2=0x8D),
            None,
            None,
        )

        with patch(
            "custom_components.proflame2.learning.derive_ecc_profile",
            side_effect=ValueError("Ambiguous stable C/D derivation: 2 candidates remain."),
        ):
            result = await async_learn_remote_profile(
                backend,
                timeout=0.05,
                receive_timeout=0.01,
            )

        assert result.success is False
        assert result.error_code == ERROR_AMBIGUOUS_PROFILE
        assert result.remote_id == 0x3B3F02
        assert result.packets_seen == 3

    asyncio.run(_run())


def test_yardstick_learning_backend_uses_proven_rx_defaults(monkeypatch) -> None:
    """Guided learning should use the hardware-proven Yard Stick RX profile."""

    class _FakeHass:
        def __init__(self) -> None:
            self.data = {DOMAIN: {}}

        async def async_add_executor_job(self, func, *args):
            return func(*args)

    async def _run() -> None:
        async def fake_connect(self) -> None:
            return None

        monkeypatch.setattr(YardStickBackend, "connect", fake_connect)

        backend = await async_create_learning_backend(_FakeHass(), BACKEND_YARDSTICK)

        assert isinstance(backend, YardStickBackend)
        assert backend._frequency_hz == YARDSTICK_RX_LEARNING_FREQUENCY_HZ
        assert backend._packet_length_bytes == YARDSTICK_RX_LEARNING_PACKET_BYTES
        assert backend._sweep_enabled is YARDSTICK_RX_LEARNING_SWEEP_ENABLED

    asyncio.run(_run())


def test_smartfire_tx_reference_frequency_remains_unchanged() -> None:
    """RX learning defaults should not change the SmartFire TX reference."""

    assert PROFLAME2_FREQUENCY_HZ == 314_973_000


def test_learning_accepts_power_on_and_power_off_remote_packets() -> None:
    """Guided learning should accept stock remote off packets as observed state."""

    async def _run() -> None:
        backend = FakeRFBackend()
        await backend.connect()
        backend.queue_packets(
            _packet(remote_id=0x3B3F02, cmd1=0x01, err1=0x76, cmd2=0x06, err2=0xDE),
            _packet(remote_id=0x3B3F02, cmd1=0x00, err1=0x57, cmd2=0x06, err2=0xDE),
            _packet(remote_id=0x3B3F02, cmd1=0x31, err1=0x25, cmd2=0x16, err2=0xEF),
        )

        result = await async_learn_remote_profile(backend, timeout=0.2, receive_timeout=0.01)

        assert result.success is True
        assert result.remote_id == 0x3B3F02
        assert (result.c1, result.d1, result.c2, result.d2) == (5, 7, 1, 8)

    asyncio.run(_run())


def test_guided_learning_restore_power_on_duplicate_advances() -> None:
    """Restore-power prompt should advance on the expected duplicate Power On packet."""

    async def _run() -> None:
        backend = FakeRFBackend()
        await backend.connect()
        power_on = _packet(remote_id=0x3B3F02, cmd1=0x01, err1=0x76, cmd2=0x06, err2=0xDE)
        power_off = _packet(remote_id=0x3B3F02, cmd1=0x00, err1=0x57, cmd2=0x06, err2=0xDE)
        backend.queue_packets(power_on, power_off, power_on)

        session = LearnSession(
            backend=backend,
            step_timeout=0.1,
            receive_timeout=0.01,
        )

        session.prompt_index = 0
        session.prompt_label = "power_on"
        first = await async_capture_next_learning_packet(session)
        assert isinstance(first, ProflamePacket)
        assert len(session.packets) == 1

        session.prompt_index = 1
        session.prompt_label = "power_off"
        second = await async_capture_next_learning_packet(session)
        assert isinstance(second, ProflamePacket)
        assert len(session.packets) == 2

        session.prompt_index = 2
        session.prompt_label = "restore_power_on"
        restored = await async_capture_next_learning_packet(session)
        assert isinstance(restored, ProflamePacket)
        assert restored.frame == power_on.frame
        assert len(session.packets) == 2

    asyncio.run(_run())


def test_guided_learning_succeeds_after_restore_duplicate_and_cmd2_change() -> None:
    """Power On + Power Off + restore duplicate + Flame Down should derive C/D."""

    async def _run() -> None:
        backend = FakeRFBackend()
        await backend.connect()
        backend.queue_packets(
            _packet(remote_id=0x3B3F02, cmd1=0x01, err1=0x76, cmd2=0x06, err2=0xDE),
            _packet(remote_id=0x3B3F02, cmd1=0x00, err1=0x57, cmd2=0x06, err2=0xDE),
            _packet(remote_id=0x3B3F02, cmd1=0x01, err1=0x76, cmd2=0x06, err2=0xDE),
            _packet(remote_id=0x3B3F02, cmd1=0x01, err1=0x76, cmd2=0x05, err2=0xBD),
        )

        session = LearnSession(
            backend=backend,
            step_timeout=0.1,
            receive_timeout=0.01,
        )

        for prompt_index, prompt_label in enumerate(
            ("power_on", "power_off", "restore_power_on", "flame_down")
        ):
            session.prompt_index = prompt_index
            session.prompt_label = prompt_label
            packet = await async_capture_next_learning_packet(session)
            assert isinstance(packet, ProflamePacket)

        result = derive_learn_result_from_session(session)
        assert result is not None
        assert result.success is True
        assert result.remote_id == 0x3B3F02
        assert (result.c1, result.d1, result.c2, result.d2) == (5, 7, 1, 8)

    asyncio.run(_run())


def test_guided_learning_plain_duplicate_still_ignored_outside_restore() -> None:
    """Non-restore duplicate packets should still be ignored as before."""

    async def _run() -> None:
        backend = FakeRFBackend()
        await backend.connect()
        power_on = _packet(remote_id=0x3B3F02, cmd1=0x01, err1=0x76, cmd2=0x06, err2=0xDE)
        backend.queue_packets(power_on, power_on, None)

        session = LearnSession(
            backend=backend,
            step_timeout=0.03,
            receive_timeout=0.01,
        )

        session.prompt_index = 0
        session.prompt_label = "power_on"
        first = await async_capture_next_learning_packet(session)
        assert isinstance(first, ProflamePacket)
        assert len(session.packets) == 1

        session.prompt_index = 1
        session.prompt_label = "power_off"
        second = await async_capture_next_learning_packet(session)
        assert isinstance(second, LearnResult)
        assert second.success is False
        assert second.error_code == ERROR_TIMEOUT
        assert len(session.packets) == 1

    asyncio.run(_run())


def test_guided_learning_logs_exception_details_in_heartbeat(caplog, monkeypatch) -> None:
    """Guided learning exceptions should include class/message in logs and heartbeat."""

    class _ExplodingBackend(RFBackend):
        def __init__(self) -> None:
            self.last_receive_status = ReceiveStatus(
                outcome="exception",
                exception_type="RuntimeError",
                exception_message="boom",
            )

        async def connect(self) -> None:
            return None

        async def close(self) -> None:
            return None

        async def send(self, packet):
            raise NotImplementedError

        async def receive(self, timeout: float | None = None):
            raise RuntimeError("boom")

        async def learn(self, timeout: float | None = None):
            raise NotImplementedError

        async def capabilities(self) -> BackendCapabilities:
            return BackendCapabilities()

    class _FakePacketLogger:
        def __init__(self) -> None:
            self.info_messages: list[str] = []
            self.exception_messages: list[str] = []

        def info(self, message: str, *args) -> None:
            self.info_messages.append(message % args if args else message)

        def exception(self, message: str, *args) -> None:
            self.exception_messages.append(message % args if args else message)

    async def _run() -> None:
        fake_packet_logger = _FakePacketLogger()
        monkeypatch.setattr(
            "custom_components.proflame2.learning.get_packet_debug_logger",
            lambda: fake_packet_logger,
        )
        session = LearnSession(
            backend=_ExplodingBackend(),
            step_timeout=0.05,
            receive_timeout=0.01,
            debug_logging_enabled=True,
            prompt_index=1,
            prompt_label="power_off",
        )

        with caplog.at_level(logging.ERROR):
            with pytest.raises(RuntimeError, match="boom"):
                await async_capture_next_learning_packet(session)

        assert "guided learning receive failed" in caplog.text
        assert "Traceback" in caplog.text
        assert any("exception_type=RuntimeError error=boom" in message for message in fake_packet_logger.exception_messages)
        assert any("outcome=exception" in message and "reason=RuntimeError" in message and "error=boom" in message for message in fake_packet_logger.info_messages)
        assert not any("reason=None" in message for message in fake_packet_logger.info_messages)

    asyncio.run(_run())


def test_learning_backend_failure_result_includes_exception_class() -> None:
    """Backend setup/receive failures should return meaningful class+message text."""

    class _FakeHass:
        def __init__(self) -> None:
            self.data = {DOMAIN: {}}

        async def async_add_executor_job(self, func, *args):
            return func(*args)

    async def _run() -> None:
        with patch(
            "custom_components.proflame2.learning.async_create_learning_backend",
            side_effect=RuntimeError("backend exploded"),
        ):
            result = await async_run_learning_with_backend(_FakeHass(), BACKEND_YARDSTICK)

        assert result.success is False
        assert result.error_code == ERROR_BACKEND_UNAVAILABLE
        assert result.error == "RuntimeError: backend exploded"

    asyncio.run(_run())
