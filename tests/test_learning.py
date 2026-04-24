"""Tests for backend-independent Proflame2 remote learning."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.protocol

from custom_components.proflame2.learning import (
    ERROR_AMBIGUOUS_PROFILE,
    ERROR_CONTRADICTORY_PROFILE,
    ERROR_INCONSISTENT_REMOTE_ID,
    ERROR_TIMEOUT,
    async_learn_remote_profile,
)
from proflame2_protocol.packet import ProflameFrame, ProflamePacket
from proflame2_rf.fake import FakeRFBackend


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
