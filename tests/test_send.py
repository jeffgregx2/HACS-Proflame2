"""Tests for transmit-side backend boundaries."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys

import pytest

pytestmark = pytest.mark.protocol

from custom_components.proflame2.protocol.encoder import encode_packet, encode_state
from custom_components.proflame2.protocol.models import ECCProfile, FireplaceFeatures, FireplaceState, RemoteProfile
from custom_components.proflame2.rf.base import SendResult
from custom_components.proflame2.rf.fake import FakeRFBackend
from custom_components.proflame2.rf.waveform import (
    AIR_PACKET_BYTES,
    SMARTFIRE_DEFAULT_RFCAT_REPEAT,
    SMARTFIRE_DEFAULT_TOTAL_TRANSMISSIONS,
    build_transmission_plan,
    frame_to_symbol_string,
)


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "yardstick_send_test.py"
SCRIPT_SPEC = importlib.util.spec_from_file_location("yardstick_send_test", SCRIPT_PATH)
assert SCRIPT_SPEC is not None
assert SCRIPT_SPEC.loader is not None
yardstick_send_test = importlib.util.module_from_spec(SCRIPT_SPEC)
sys.modules[SCRIPT_SPEC.name] = yardstick_send_test
SCRIPT_SPEC.loader.exec_module(yardstick_send_test)

CONSOLE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "yardstick_tx_console.py"
CONSOLE_SPEC = importlib.util.spec_from_file_location("yardstick_tx_console", CONSOLE_PATH)
assert CONSOLE_SPEC is not None
assert CONSOLE_SPEC.loader is not None
yardstick_tx_console = importlib.util.module_from_spec(CONSOLE_SPEC)
sys.modules[CONSOLE_SPEC.name] = yardstick_tx_console
CONSOLE_SPEC.loader.exec_module(yardstick_tx_console)


def test_fake_backend_records_send_result(remote_profile) -> None:
    """The fake backend should preserve the unified packet and derived views."""

    async def _run() -> None:
        backend = FakeRFBackend()
        await backend.connect()
        state = FireplaceState(power=True, flame=1, fan=0, light=0)
        packet = encode_packet(state, remote_profile, source="test")
        result = await backend.send(packet)

        assert result.requested_state == state
        assert result.packet == packet
        assert result.encoded_frame == packet.frame
        assert result.backend_name == "fake"
        assert backend.sent_packets == [packet]
        assert backend.sent_frames == [packet.frame]
        assert backend.sent_results == [result]

    asyncio.run(_run())


def test_transmission_plan_separates_logical_frame_from_rf_payload(remote_profile) -> None:
    """Logical frame bytes and RF serialization should remain distinct layers."""

    state = FireplaceState(power=True, flame=1, fan=0, light=0)
    frame = encode_state(state, remote_profile)
    plan = build_transmission_plan(frame)

    assert plan.frame == frame
    assert frame.as_bytes() == bytes.fromhex("3b3f0201760139")
    assert len(plan.air_payload) == AIR_PACKET_BYTES
    assert plan.air_payload != frame.as_bytes()
    assert plan.repeat_count == SMARTFIRE_DEFAULT_TOTAL_TRANSMISSIONS
    assert plan.backend_repeat_argument == SMARTFIRE_DEFAULT_RFCAT_REPEAT
    assert plan.preamble_bytes == b""
    assert plan.repeat_spacing_ms is None
    assert plan.sync_strategy == "embedded_symbol_sync"
    assert any("repeat" in note.lower() for note in plan.notes)


def test_waveform_matches_exact_smartfire_low_state_bytes(remote_profile) -> None:
    """Known low-state frame should serialize to the exact SmartFire byte stream."""

    state = FireplaceState(power=True, flame=1, fan=0, light=0)
    frame = encode_state(state, remote_profile)
    plan = build_transmission_plan(frame)

    assert plan.symbol_string == frame_to_symbol_string(frame)
    assert plan.air_payload.hex() == "e5a9a9b96aa96e55596b95559ae55566b9a9a5ae5a96580000"


def test_waveform_matches_exact_smartfire_high_state_bytes(remote_profile) -> None:
    """Known higher-state frame should serialize to the exact SmartFire byte stream."""

    state = FireplaceState(power=True, flame=6, fan=2, light=3)
    frame = encode_state(state, remote_profile)
    plan = build_transmission_plan(frame)

    assert plan.air_payload.hex() == "e5a9a9b96aa96e55596b96959ae59696b96599ae9aa5680000"


def test_cli_repeat_override_changes_effective_backend_repeat(remote_profile) -> None:
    """Transmission plan metadata should remain SmartFire-compatible."""

    args = yardstick_send_test._build_parser().parse_args(
        [
            "--id",
            "3b3f02",
            "--c1",
            "5",
            "--d1",
            "7",
            "--c2",
            "1",
            "--d2",
            "8",
            "--power",
            "on",
            "--flame",
            "1",
            "--yes",
        ]
    )
    profile = RemoteProfile(
        serial_id=0x3B3F02,
        ecc=ECCProfile(c1=5, d1=7, c2=1, d2=8),
        features=FireplaceFeatures(),
    )

    _requested_state, _effective_state, packet = yardstick_send_test._build_packet_for_cli(args, profile)

    assert packet.transmission_plan is not None
    assert packet.transmission_plan.backend_repeat_argument == SMARTFIRE_DEFAULT_RFCAT_REPEAT
    assert packet.transmission_plan.repeat_count == SMARTFIRE_DEFAULT_TOTAL_TRANSMISSIONS


def test_cli_defaults_to_stock_remote_style_software_burst() -> None:
    """The standalone sender should default to five software transmissions."""

    args = yardstick_send_test._build_parser().parse_args(
        [
            "--id",
            "3b3f02",
            "--c1",
            "5",
            "--d1",
            "7",
            "--c2",
            "1",
            "--d2",
            "8",
            "--power",
            "on",
            "--flame",
            "1",
            "--yes",
        ]
    )

    assert args.transmissions == SMARTFIRE_DEFAULT_TOTAL_TRANSMISSIONS
    assert args.inter_frame_gap_ms == 0


def test_cli_preserve_off_flame_generates_remote_like_off_packet() -> None:
    """The CLI should be able to preserve off-state flame bits for experiments."""

    args = yardstick_send_test._build_parser().parse_args(
        [
            "--id",
            "3b3f02",
            "--c1",
            "5",
            "--d1",
            "7",
            "--c2",
            "1",
            "--d2",
            "8",
            "--power",
            "off",
            "--flame",
            "6",
            "--preserve-off-flame",
            "--yes",
        ]
    )
    profile = RemoteProfile(
        serial_id=0x3B3F02,
        ecc=ECCProfile(c1=5, d1=7, c2=1, d2=8),
        features=FireplaceFeatures(),
    )

    requested_state, effective_state, packet = yardstick_send_test._build_packet_for_cli(args, profile)

    assert requested_state == FireplaceState(power=False, flame=6, fan=0, light=0)
    assert effective_state == FireplaceState(power=False, flame=6, fan=0, light=0)
    assert packet.frame.cmd1 == 0x00
    assert packet.frame.err1 == 0x57
    assert packet.frame.cmd2 == 0x06
    assert packet.frame.err2 == 0xDE


def test_cli_defaults_keep_normalized_power_off_behavior() -> None:
    """Without preserve-off-flame, the CLI should keep the normal transmit behavior."""

    args = yardstick_send_test._build_parser().parse_args(
        [
            "--id",
            "3b3f02",
            "--c1",
            "5",
            "--d1",
            "7",
            "--c2",
            "1",
            "--d2",
            "8",
            "--power",
            "off",
            "--flame",
            "6",
            "--yes",
        ]
    )

    state = yardstick_send_test._build_state(args)

    assert state == FireplaceState(power=False, flame=0, fan=0, light=0)


def test_cli_allow_off_flame_alias_sets_preserve_flag() -> None:
    """The original requested --allow-off-flame alias should be supported."""

    args = yardstick_send_test._build_parser().parse_args(
        [
            "--id",
            "3b3f02",
            "--c1",
            "5",
            "--d1",
            "7",
            "--c2",
            "1",
            "--d2",
            "8",
            "--power",
            "off",
            "--flame",
            "6",
            "--allow-off-flame",
            "--yes",
        ]
    )

    assert args.preserve_off_flame is True


def test_cli_defaults_to_no_close_and_does_not_call_close(monkeypatch) -> None:
    """One-shot Yard Stick TX should skip explicit close by default."""

    class _FakeBackend:
        close_calls = 0
        init_kwargs: dict | None = None

        def __init__(self, *args, **kwargs) -> None:
            self.name = "yardstick"
            type(self).init_kwargs = kwargs

        async def send(self, packet):
            return SendResult(packet=packet, backend_name="yardstick", warnings=packet.warnings)

        async def close(self) -> None:
            type(self).close_calls += 1

    async def _run() -> None:
        monkeypatch.setattr(yardstick_send_test, "YardStickBackend", _FakeBackend)
        args = yardstick_send_test._build_parser().parse_args(
            [
                "--id",
                "3b3f02",
                "--c1",
                "5",
                "--d1",
                "7",
                "--c2",
                "1",
                "--d2",
                "8",
                "--power",
                "on",
                "--flame",
                "1",
                "--yes",
            ]
        )

        assert args.no_close is True
        exit_code = await yardstick_send_test._run(args)

        assert exit_code == 0
        assert _FakeBackend.close_calls == 0
        assert _FakeBackend.init_kwargs is not None
        assert _FakeBackend.init_kwargs["tx_transmissions"] == SMARTFIRE_DEFAULT_TOTAL_TRANSMISSIONS
        assert _FakeBackend.init_kwargs["tx_inter_frame_gap_ms"] == 0

    asyncio.run(_run())


def test_cli_tx_knobs_propagate_to_backend(monkeypatch) -> None:
    """The one-shot sender should pass TX experiment knobs into the backend."""

    class _FakeBackend:
        init_kwargs: dict | None = None

        def __init__(self, *args, **kwargs) -> None:
            type(self).init_kwargs = kwargs

        async def send(self, packet):
            return SendResult(packet=packet, backend_name="yardstick", warnings=packet.warnings)

        async def close(self) -> None:
            return None

    async def _run() -> None:
        monkeypatch.setattr(yardstick_send_test, "YardStickBackend", _FakeBackend)
        args = yardstick_send_test._build_parser().parse_args(
            [
                "--id",
                "3b3f02",
                "--c1",
                "5",
                "--d1",
                "7",
                "--c2",
                "1",
                "--d2",
                "8",
                "--power",
                "on",
                "--flame",
                "1",
                "--tx-frequency",
                "315000000",
                "--transmissions",
                "7",
                "--inter-frame-gap-ms",
                "12.5",
                "--yes",
            ]
        )

        exit_code = await yardstick_send_test._run(args)

        assert exit_code == 0
        assert _FakeBackend.init_kwargs == {
            "device_index": 0,
            "tx_frequency_hz": 315000000,
            "tx_transmissions": 7,
            "tx_inter_frame_gap_ms": 12.5,
        }

    asyncio.run(_run())


def test_interactive_console_reuses_one_backend_for_multiple_sends() -> None:
    """The interactive console should send repeatedly through one backend instance."""

    class _FakeBackend:
        def __init__(self) -> None:
            self.sent_packets = []

        async def send(self, packet):
            self.sent_packets.append(packet)
            return SendResult(packet=packet, backend_name="yardstick", warnings=packet.warnings)

    async def _run() -> None:
        session = yardstick_tx_console.TxConsoleSession(
            profile=RemoteProfile(
                serial_id=0x3B3F02,
                ecc=ECCProfile(c1=5, d1=7, c2=1, d2=8),
                features=FireplaceFeatures(),
            ),
            backend=_FakeBackend(),
            tx_frequency_hz=314_973_000,
            transmissions=SMARTFIRE_DEFAULT_TOTAL_TRANSMISSIONS,
            inter_frame_gap_ms=0,
            yes=True,
            no_close=True,
        )

        assert await yardstick_tx_console._handle_command(session, "on 1") is True
        assert await yardstick_tx_console._handle_command(session, "off 6") is True
        assert len(session.backend.sent_packets) == 2
        assert session.backend.sent_packets[0].frame.cmd1 == 0x01
        assert session.backend.sent_packets[1].frame.cmd1 == 0x00
        assert session.backend.sent_packets[1].frame.cmd2 == 0x06

    asyncio.run(_run())


def test_console_commands_update_tx_experiment_settings() -> None:
    """The interactive console should expose software burst knobs."""

    async def _run() -> None:
        backend = type(
            "_FakeBackend",
            (),
            {
                "_tx_transmissions": SMARTFIRE_DEFAULT_TOTAL_TRANSMISSIONS,
                "_tx_inter_frame_gap_ms": 0.0,
            },
        )()
        session = yardstick_tx_console.TxConsoleSession(
            profile=RemoteProfile(
                serial_id=0x3B3F02,
                ecc=ECCProfile(c1=5, d1=7, c2=1, d2=8),
                features=FireplaceFeatures(),
            ),
            backend=backend,
            tx_frequency_hz=314_973_000,
            transmissions=SMARTFIRE_DEFAULT_TOTAL_TRANSMISSIONS,
            inter_frame_gap_ms=0,
            yes=True,
            no_close=True,
        )

        assert await yardstick_tx_console._handle_command(session, "transmissions 7") is True
        assert await yardstick_tx_console._handle_command(session, "gap 15") is True

        assert session.transmissions == 7
        assert session.inter_frame_gap_ms == 15.0
        assert session.backend._tx_transmissions == 7
        assert session.backend._tx_inter_frame_gap_ms == 15.0

    asyncio.run(_run())


def test_cli_help_describes_software_burst_controls() -> None:
    """The CLI help text should describe the software burst controls only."""

    help_text = yardstick_send_test._build_parser().format_help()

    assert "--transmissions" in help_text
    assert "--inter-frame-gap-ms" in help_text
    assert "software" in help_text
    assert "--repeat" not in help_text
    assert "--rfcat-repeat-mode" not in help_text
