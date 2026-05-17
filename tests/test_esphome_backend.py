"""Tests for the ESPHome/T-Embed backend adapter skeleton."""

from __future__ import annotations

import sys

import pytest

from custom_components.proflame2.const import BACKEND_ESPHOME
from custom_components.proflame2.protocol.encoder import encode_packet
from custom_components.proflame2.protocol.models import ECCProfile, FireplaceState, RemoteProfile
from custom_components.proflame2.protocol.packet import ProflameFrame
from custom_components.proflame2.rf.base import SendResult
from custom_components.proflame2.rf.capture import frame_to_air_bytes
from custom_components.proflame2.rf.esphome.contract import (
    ESPHomeDisplayState,
    ESPHomeEndpointStatus,
    ESPHomeRXEvent,
    ESPHomeTXRequest,
    ESPHomeTXResponse,
)
from custom_components.proflame2.rf.esphome.transport import MockESPHomeTransport
from custom_components.proflame2.rf.esphome_api import (
    ESPHOME_FIFO_MAX_SCAN_PAYLOAD_BYTES,
    ESPHomeAPIBackend,
    _stringify_enum_values,
)
from custom_components.proflame2.rf.waveform import build_transmission_plan


@pytest.fixture
def remote_profile() -> RemoteProfile:
    return RemoteProfile(
        serial_id=0x3B3F03,
        ecc=ECCProfile(c1=0x10, d1=0x20, c2=0x30, d2=0x40),
    )


def _prepared_packet(remote_profile: RemoteProfile):
    packet = encode_packet(
        FireplaceState(power=True, flame=3, fan=2, light=1),
        remote_profile,
    )
    packet.transmission_plan = build_transmission_plan(packet.frame)
    return packet


def _fifo_event(
    event_id: str,
    frame: ProflameFrame,
    *,
    event_kind: str = "fifo_capture",
) -> ESPHomeRXEvent:
    return ESPHomeRXEvent(
        event_id=event_id,
        raw_payload=frame_to_air_bytes(frame),
        rssi=-41.0,
        lqi=87,
        frequency_hz=314_973_000,
        capture_metadata={
            "event_kind": event_kind,
            "artifact_class": "raw_fifo_window",
            "source": "lilygo_cc1101_fifo",
        },
    )


@pytest.mark.asyncio
async def test_backend_send_consumes_exact_prepared_air_payload(
    remote_profile: RemoteProfile,
) -> None:
    transport = MockESPHomeTransport()
    backend = ESPHomeAPIBackend(transport=transport)
    packet = _prepared_packet(remote_profile)
    packet.display_state = ESPHomeDisplayState(power=True, flame=3, fan=2, light=1, action_label="Flame 3")

    await backend.connect()
    result = await backend.send(packet)

    assert isinstance(result, SendResult)
    assert result.packet is packet
    assert result.backend_name == BACKEND_ESPHOME
    assert transport.tx_requests[-1].air_payload == packet.transmission_plan.air_payload
    assert transport.tx_requests[-1].air_payload_hex == packet.transmission_plan.air_payload.hex()
    assert transport.tx_requests[-1].air_payload_bit_length == packet.transmission_plan.air_payload_bit_length
    assert transport.tx_requests[-1].repeat_count == 5
    assert transport.tx_requests[-1].display_state is not None
    assert transport.tx_requests[-1].display_state.power is True
    assert transport.tx_requests[-1].display_state.flame == 3
    assert transport.tx_requests[-1].display_state.fan == 2


@pytest.mark.asyncio
async def test_backend_does_not_regenerate_protocol_payload(
    remote_profile: RemoteProfile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = MockESPHomeTransport()
    backend = ESPHomeAPIBackend(transport=transport)
    packet = _prepared_packet(remote_profile)

    def fail_if_regenerated(*args, **kwargs):
        raise AssertionError("backend must not regenerate transmission plans")

    monkeypatch.setattr(
        "custom_components.proflame2.rf.waveform.build_transmission_plan",
        fail_if_regenerated,
    )

    await backend.connect()
    await backend.send(packet)

    assert transport.tx_requests[-1].air_payload == packet.transmission_plan.air_payload


@pytest.mark.asyncio
async def test_backend_configures_default_repeat_count_before_send(
    remote_profile: RemoteProfile,
) -> None:
    transport = MockESPHomeTransport()
    backend = ESPHomeAPIBackend(transport=transport)

    await backend.connect()
    await backend.send(_prepared_packet(remote_profile))

    assert transport.configurations[-1].tx_repeat_count == 5
    assert transport.tx_responses[-1].frames_sent == 5


@pytest.mark.asyncio
async def test_mock_transport_receives_expected_tx_request(
    remote_profile: RemoteProfile,
) -> None:
    transport = MockESPHomeTransport()
    backend = ESPHomeAPIBackend(transport=transport)
    packet = _prepared_packet(remote_profile)

    await backend.connect()
    await backend.send(packet)
    request = transport.tx_requests[-1]

    assert request.request_id.startswith("proflame2-")
    assert request.remote_id == packet.frame.serial_id
    assert request.cmd1 == packet.frame.cmd1
    assert request.err1 == packet.frame.err1
    assert request.cmd2 == packet.frame.cmd2
    assert request.err2 == packet.frame.err2


@pytest.mark.asyncio
async def test_backend_connect_close_are_clean_and_status_is_available() -> None:
    transport = MockESPHomeTransport()
    backend = ESPHomeAPIBackend(transport=transport)

    await backend.set_active_listening_enabled(True)
    await backend.connect()
    status = await backend.get_status()
    await backend.close(reason="test")

    assert transport.connected is False
    assert transport.connect_count == 1
    assert transport.close_count == 1
    assert transport.active_listening_updates == [True]
    assert transport.rx_stop_count == 1
    assert status.status == ESPHomeEndpointStatus.READY
    assert status.configured is True


@pytest.mark.asyncio
async def test_backend_active_listening_policy_and_stop_are_explicit() -> None:
    transport = MockESPHomeTransport()
    backend = ESPHomeAPIBackend(transport=transport)

    await backend.connect()
    await backend.set_active_listening_enabled(True)
    await backend.set_active_listening_enabled(False)
    await backend.stop_rx()

    assert transport.active_listening_updates == [False, True, False]
    assert transport.rx_stop_count == 1


@pytest.mark.asyncio
async def test_backend_send_fails_clearly_when_transport_unavailable(
    remote_profile: RemoteProfile,
) -> None:
    transport = MockESPHomeTransport(available=False)
    backend = ESPHomeAPIBackend(transport=transport)

    with pytest.raises(RuntimeError, match="ESPHome backend is unavailable"):
        await backend.send(_prepared_packet(remote_profile))


@pytest.mark.asyncio
async def test_backend_connect_and_status_fail_without_transport() -> None:
    backend = ESPHomeAPIBackend()

    with pytest.raises(RuntimeError, match="no transport is configured"):
        await backend.connect()

    with pytest.raises(RuntimeError, match="no transport is configured"):
        await backend.get_status()


@pytest.mark.asyncio
async def test_backend_close_without_transport_is_safe() -> None:
    backend = ESPHomeAPIBackend()
    backend.connected = True

    await backend.close(reason="test")

    assert backend.connected is False


@pytest.mark.asyncio
async def test_backend_rejects_missing_transmission_plan(remote_profile: RemoteProfile) -> None:
    backend = ESPHomeAPIBackend(transport=MockESPHomeTransport())
    packet = encode_packet(FireplaceState(power=True, flame=1), remote_profile)

    with pytest.raises(RuntimeError, match="transmission_plan"):
        await backend.send(packet)


class _FailingSendTransport(MockESPHomeTransport):
    async def send_tx(self, request):
        raise RuntimeError("send exploded")


@pytest.mark.asyncio
async def test_backend_wraps_transport_send_exception(
    remote_profile: RemoteProfile,
) -> None:
    backend = ESPHomeAPIBackend(transport=_FailingSendTransport())

    with pytest.raises(RuntimeError, match="ESPHome backend send failed: send exploded"):
        await backend.send(_prepared_packet(remote_profile))


class _UnsuccessfulSendTransport(MockESPHomeTransport):
    async def send_tx(self, request):
        self.tx_requests.append(request)
        return ESPHomeTXResponse(
            request_id=request.request_id,
            ok=False,
            payload_length=len(request.air_payload),
            frames_sent=0,
            error_code="radio_fault",
            error_message="cc1101 rejected payload",
        )


@pytest.mark.asyncio
async def test_backend_raises_unsuccessful_tx_response(
    remote_profile: RemoteProfile,
) -> None:
    backend = ESPHomeAPIBackend(transport=_UnsuccessfulSendTransport())

    with pytest.raises(RuntimeError, match="cc1101 rejected payload"):
        await backend.send(_prepared_packet(remote_profile))


class _StatusFailingTransport(MockESPHomeTransport):
    def __init__(self) -> None:
        super().__init__()
        self.status_calls = 0

    async def get_status(self):
        self.status_calls += 1
        if self.status_calls == 1:
            return await super().get_status()
        raise RuntimeError("status unavailable")


@pytest.mark.asyncio
async def test_backend_send_tolerates_post_tx_status_failure(
    remote_profile: RemoteProfile,
) -> None:
    backend = ESPHomeAPIBackend(transport=_StatusFailingTransport())

    result = await backend.send(_prepared_packet(remote_profile))

    assert result.backend_name == BACKEND_ESPHOME
    assert backend.last_tx_response is not None
    assert backend.last_endpoint_status is None


@pytest.mark.asyncio
async def test_backend_receive_capabilities_and_fifo_learning(remote_profile: RemoteProfile) -> None:
    backend = ESPHomeAPIBackend(transport=MockESPHomeTransport())

    await backend.connect()
    packet = _prepared_packet(remote_profile)
    backend.transport.push_rx_event(
        ESPHomeRXEvent(
            event_id="rx-1",
            raw_payload=packet.transmission_plan.air_payload,
            rssi=-41.0,
            lqi=87,
            capture_metadata={
                "event_kind": "fifo_capture",
                "accepted": "true",
                "qualifier": "strict",
            },
        )
    )
    received = await backend.receive(timeout=0.1)
    assert received is not None
    assert received.remote_id == remote_profile.serial_id
    assert received.state == packet.state
    capabilities = await backend.capabilities()
    assert capabilities.can_send is True
    assert capabilities.can_receive is True
    assert capabilities.can_learn is True

    backend.transport.push_rx_event(
        _fifo_event(
            "learn-1",
            ProflameFrame(
                serial_id=0x3B3F02,
                cmd1=0x01,
                err1=0x76,
                cmd2=0x16,
                err2=0xEF,
            ),
        )
    )
    backend.transport.push_rx_event(
        _fifo_event(
            "learn-2",
            ProflameFrame(
                serial_id=0x3B3F02,
                cmd1=0x31,
                err1=0x25,
                cmd2=0x26,
                err2=0xBC,
            ),
        )
    )
    backend.transport.push_rx_event(
        _fifo_event(
            "learn-3",
            ProflameFrame(
                serial_id=0x3B3F02,
                cmd1=0x51,
                err1=0x83,
                cmd2=0x36,
                err2=0x8D,
            ),
        )
    )

    learn_result = await backend.learn(timeout=0.5)

    assert learn_result.serial_id == 0x3B3F02
    assert len(learn_result.packets) == 3
    assert len(learn_result.samples) == 3
    assert learn_result.metadata["semantic_comparable"] is True
    assert learn_result.metadata["learned_profile"] == {
        "c1": 5,
        "d1": 7,
        "c2": 1,
        "d2": 8,
    }
    assert learn_result.metadata["profile_error"] is None
    artifacts = learn_result.metadata["semantic_artifacts"]
    assert len(artifacts) == 3
    assert all(artifact["artifact_class"] == "semantic" for artifact in artifacts)
    assert all(artifact["semantic_comparable"] is True for artifact in artifacts)
    assert all(artifact["decode_success"] is True for artifact in artifacts)
    assert all(artifact["learning_accepted"] is True for artifact in artifacts)


@pytest.mark.asyncio
async def test_backend_fifo_learning_keeps_failed_windows_debug_only() -> None:
    backend = ESPHomeAPIBackend(transport=MockESPHomeTransport())

    await backend.connect()
    backend.transport.push_rx_event(
        ESPHomeRXEvent(
            event_id="noise",
            raw_payload=b"\x00" * 32,
            capture_metadata={
                "event_kind": "fifo_capture",
                "artifact_class": "raw_fifo_window",
                "source": "lilygo_cc1101_fifo",
            },
        )
    )
    backend.transport.push_rx_event(
        _fifo_event(
            "learn-1",
            ProflameFrame(
                serial_id=0x3B3F02,
                cmd1=0x01,
                err1=0x76,
                cmd2=0x16,
                err2=0xEF,
            ),
        )
    )

    learn_result = await backend.learn(timeout=0.2)

    assert len(learn_result.samples) == 1
    assert learn_result.metadata["raw_payloads_seen"] == 2
    assert learn_result.metadata["decode_failures"] == 1
    assert learn_result.metadata["debug_failures"][0]["reason"] == "no_valid_proflame_candidate"
    assert learn_result.metadata["semantic_artifacts"][0]["artifact_class"] == "semantic"


@pytest.mark.asyncio
async def test_backend_fifo_learning_times_out_without_events() -> None:
    backend = ESPHomeAPIBackend(transport=MockESPHomeTransport())

    await backend.connect()

    learn_result = await backend.learn(timeout=0.01)

    assert learn_result.serial_id == 0
    assert learn_result.packets == ()
    assert learn_result.samples == ()
    assert learn_result.metadata["semantic_comparable"] is False
    assert learn_result.metadata["raw_payloads_seen"] == 0
    assert learn_result.metadata["decode_failures"] == 0
    assert learn_result.metadata["debug_failures"] == ()


@pytest.mark.asyncio
async def test_backend_fifo_learning_rejects_non_decodable_events() -> None:
    backend = ESPHomeAPIBackend(transport=MockESPHomeTransport())

    await backend.connect()
    backend.transport.push_rx_event(
        ESPHomeRXEvent(
            event_id="debug-only",
            raw_payload=b"\xaa\xbb",
            capture_metadata={
                "event_kind": "rx_debug_sample",
                "artifact_class": "debug",
            },
        )
    )

    learn_result = await backend.learn(timeout=0.05)

    assert learn_result.samples == ()
    assert learn_result.metadata["raw_payloads_seen"] == 0
    assert learn_result.metadata["decode_failures"] == 0
    assert learn_result.metadata["debug_failures"][0]["reason"] == "unsupported_event_kind"


@pytest.mark.asyncio
async def test_backend_fifo_learning_rejects_remote_id_mismatch() -> None:
    backend = ESPHomeAPIBackend(transport=MockESPHomeTransport())

    await backend.connect()
    backend.transport.push_rx_event(
        _fifo_event(
            "learn-1",
            ProflameFrame(
                serial_id=0x3B3F02,
                cmd1=0x01,
                err1=0x76,
                cmd2=0x16,
                err2=0xEF,
            ),
        )
    )
    backend.transport.push_rx_event(
        _fifo_event(
            "learn-2",
            ProflameFrame(
                serial_id=0x3B3F03,
                cmd1=0x31,
                err1=0x25,
                cmd2=0x26,
                err2=0xBC,
            ),
        )
    )

    learn_result = await backend.learn(timeout=0.1)

    assert learn_result.serial_id == 0x3B3F02
    assert len(learn_result.samples) == 1
    assert learn_result.metadata["raw_payloads_seen"] == 2
    assert learn_result.metadata["debug_failures"][0]["reason"] == "remote_id_mismatch"
    assert learn_result.metadata["debug_failures"][0]["expected_remote_id"] == "3b3f02"
    assert learn_result.metadata["debug_failures"][0]["observed_remote_id"] == "3b3f03"


@pytest.mark.asyncio
async def test_backend_fifo_learning_reports_profile_derivation_error() -> None:
    backend = ESPHomeAPIBackend(transport=MockESPHomeTransport())

    await backend.connect()
    backend.transport.push_rx_event(
        _fifo_event(
            "learn-1",
            ProflameFrame(
                serial_id=0x3B3F02,
                cmd1=0x01,
                err1=0x76,
                cmd2=0x16,
                err2=0xEF,
            ),
        )
    )
    backend.transport.push_rx_event(
        _fifo_event(
            "learn-2",
            ProflameFrame(
                serial_id=0x3B3F02,
                cmd1=0x01,
                err1=0x77,
                cmd2=0x16,
                err2=0xEE,
            ),
        )
    )

    learn_result = await backend.learn(timeout=0.05)

    assert len(learn_result.samples) == 2
    assert learn_result.metadata["semantic_comparable"] is False
    assert learn_result.metadata["profile_error"] is not None
    assert learn_result.metadata["learned_profile"] is None


@pytest.mark.asyncio
async def test_backend_receive_waits_for_firmware_emitted_fifo_capture_window() -> None:
    transport = MockESPHomeTransport()
    backend = ESPHomeAPIBackend(transport=transport)

    await backend.connect()
    await backend.set_active_listening_enabled(True)
    transport.push_rx_event(
        _fifo_event(
            "auto-complete-1",
            ProflameFrame(
                serial_id=0x3B3F02,
                cmd1=0x01,
                err1=0x76,
                cmd2=0x16,
                err2=0xEF,
            ),
        )
    )

    packet = await backend.receive(timeout=1.0)

    assert packet is not None
    assert packet.remote_id == 0x3B3F02
    assert transport.rx_end_confirmation_count == 0
    assert backend.last_fifo_semantic_artifact is not None
    assert backend.last_fifo_semantic_artifact["artifact_class"] == "semantic"


@pytest.mark.asyncio
async def test_backend_receive_accepts_firmware_filtered_active_listener_packet() -> None:
    profile = RemoteProfile(
        serial_id=0x3B3F02,
        ecc=ECCProfile(c1=5, d1=7, c2=1, d2=8),
    )
    transport = MockESPHomeTransport()
    backend = ESPHomeAPIBackend(transport=transport, remote_profile=profile)

    await backend.connect()
    await backend.set_active_listening_enabled(True, profile)
    transport.push_rx_event(
        ESPHomeRXEvent(
            event_id="active-1",
            raw_payload=b"\xaa",
            rssi=-39.0,
            lqi=88,
            capture_metadata={
                "event_kind": "rx_packet",
                "accepted": "true",
                "qualifier": "strict",
                "remote_id": "3b3f02",
                "cmd1": "01",
                "err1": "76",
                "cmd2": "16",
                "err2": "ef",
            },
        )
    )

    packet = await backend.receive(timeout=0.1)

    assert packet is not None
    assert packet.remote_id == profile.serial_id
    assert packet.frame.cmd1 == 0x01
    assert packet.frame.err1 == 0x76
    assert packet.frame.cmd2 == 0x16
    assert packet.frame.err2 == 0xEF
    assert packet.source == "esphome_active_listening"
    assert backend.last_fifo_semantic_artifact["provenance"] == "lilygo_cc1101_fifo_firmware_decoder"


@pytest.mark.asyncio
async def test_backend_receive_rejects_strict_rx_packet_without_decoded_fields(
    remote_profile: RemoteProfile,
) -> None:
    transport = MockESPHomeTransport()
    backend = ESPHomeAPIBackend(transport=transport, remote_profile=remote_profile)
    packet = _prepared_packet(remote_profile)

    await backend.connect()
    await backend.set_active_listening_enabled(True, remote_profile)
    transport.push_rx_event(
        ESPHomeRXEvent(
            event_id="active-missing-fields",
            raw_payload=packet.transmission_plan.air_payload,
            capture_metadata={
                "event_kind": "rx_packet",
                "accepted": "true",
                "qualifier": "strict",
            },
        )
    )

    received = await backend.receive(timeout=0.05)

    assert received is None
    assert backend.last_fifo_semantic_artifact is None


@pytest.mark.asyncio
async def test_backend_receive_does_not_request_fifo_completion_when_idle() -> None:
    transport = MockESPHomeTransport()
    backend = ESPHomeAPIBackend(transport=transport)

    await backend.connect()
    await backend.set_active_listening_enabled(True)

    packet = await backend.receive(timeout=0.25)

    assert packet is None
    assert transport.rx_end_confirmation_count == 0


@pytest.mark.asyncio
async def test_backend_updates_esphome_learning_mode_status() -> None:
    transport = MockESPHomeTransport()
    backend = ESPHomeAPIBackend(transport=transport)

    await backend.connect()
    await backend.update_learning_mode(
        active=True,
        step_title="Learn 1",
        instruction="Press Power ON",
        status="Listening",
    )

    assert transport.learning_mode_updates[-1] == {
        "active": True,
        "step_title": "Learn 1",
        "instruction": "Press Power ON",
        "status": "Listening",
    }


@pytest.mark.asyncio
async def test_backend_rejects_oversized_fifo_payload_before_scanning() -> None:
    transport = MockESPHomeTransport()
    backend = ESPHomeAPIBackend(transport=transport)

    await backend.connect()
    transport.push_rx_event(
        ESPHomeRXEvent(
            event_id="too-large",
            raw_payload=b"\x00" * (ESPHOME_FIFO_MAX_SCAN_PAYLOAD_BYTES + 1),
            capture_metadata={
                "event_kind": "fifo_capture",
                "artifact_class": "raw_fifo_window",
                "source": "lilygo_cc1101_fifo",
            },
        )
    )

    packet = await backend.receive(timeout=0.05)

    assert packet is None
    assert backend.last_fifo_debug_failure is not None
    assert backend.last_fifo_debug_failure["reason"] == "fifo_payload_too_large_for_scanner"


@pytest.mark.asyncio
async def test_mock_transport_state_failures_and_status_reports() -> None:
    transport = MockESPHomeTransport()

    status = await transport.get_status()
    assert status.status == ESPHomeEndpointStatus.NOT_CONFIGURED
    assert status.configured is False

    with pytest.raises(RuntimeError, match="connected before configure_radio"):
        await transport.configure_radio(ESPHomeAPIBackend().radio_config)

    await transport.connect()
    with pytest.raises(RuntimeError, match="not configured"):
        await transport.send_tx(
            ESPHomeTXRequest(
                request_id="tx-test",
                air_payload=b"\x01",
                air_payload_bit_length=8,
            )
        )

    unavailable = MockESPHomeTransport(available=False)
    fault_status = await unavailable.get_status()
    assert fault_status.status == ESPHomeEndpointStatus.FAULT
    assert fault_status.configured is False
    assert fault_status.last_error == "mock_transport_unavailable"


def test_dev_witness_modules_are_not_imported_by_production_paths() -> None:
    assert "custom_components.proflame2.rf.dev" not in sys.modules
    assert "custom_components.proflame2.rf.dev.rtl433_witness" not in sys.modules
    assert "custom_components.proflame2.rf.dev.rf_witness" not in sys.modules


def test_diagnostics_enum_stringifier_handles_lists() -> None:
    assert _stringify_enum_values([ESPHomeEndpointStatus.READY]) == ["ready"]


def test_tx_request_from_packet_preserves_display_state(remote_profile: RemoteProfile) -> None:
    packet = _prepared_packet(remote_profile)

    display_state = ESPHomeDisplayState(power=True, flame=3, fan=2, light=1, action_label="Flame 3")
    request = ESPHomeTXRequest.from_packet(packet, request_id="req-1", display_state=display_state)

    assert request.display_state == display_state


@pytest.mark.asyncio
async def test_backend_can_push_display_state_without_rf_send() -> None:
    transport = MockESPHomeTransport()
    backend = ESPHomeAPIBackend(transport=transport)

    await backend.connect()
    display_state = ESPHomeDisplayState(
        power=True,
        flame=4,
        fan=2,
        thermostat=True,
        action_label="Startup sync",
    )
    await backend.update_display_state(display_state)

    assert transport.display_state_updates[-1] == display_state


@pytest.mark.asyncio
async def test_backend_receive_ignores_rx_debug_sample_events(
    remote_profile: RemoteProfile,
) -> None:
    transport = MockESPHomeTransport()
    backend = ESPHomeAPIBackend(transport=transport)
    packet = _prepared_packet(remote_profile)

    await backend.connect()
    transport.push_rx_event(
        ESPHomeRXEvent(
            event_id="rx-debug-1",
            raw_payload=packet.transmission_plan.air_payload,
            capture_metadata={
                "event_kind": "rx_debug_sample",
                "accepted": "false",
                "qualifier": "rejected_shape_sample",
                "reject_reason": "shape_bad_transition_density",
            },
        )
    )
    transport.push_rx_event(
        ESPHomeRXEvent(
            event_id="rx-1",
            raw_payload=packet.transmission_plan.air_payload,
            capture_metadata={
                "event_kind": "fifo_capture",
                "accepted": "true",
                "qualifier": "strict",
            },
        )
    )

    received = await backend.receive(timeout=0.1)

    assert received is not None
    assert received.remote_id == remote_profile.serial_id


@pytest.mark.asyncio
async def test_backend_receive_ignores_non_strict_rx_packet_events(
    remote_profile: RemoteProfile,
) -> None:
    transport = MockESPHomeTransport()
    backend = ESPHomeAPIBackend(transport=transport)
    packet = _prepared_packet(remote_profile)

    await backend.connect()
    transport.push_rx_event(
        ESPHomeRXEvent(
            event_id="rx-loose-1",
            raw_payload=packet.transmission_plan.air_payload,
            capture_metadata={
                "event_kind": "rx_packet",
                "accepted": "true",
                "qualifier": "loose",
            },
        )
    )

    received = await backend.receive(timeout=0.05)

    assert received is None
