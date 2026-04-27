"""Unit tests for the Yard Stick backend async/executor integration."""

from __future__ import annotations

import asyncio
import multiprocessing as mp
import time

import pytest

pytestmark = pytest.mark.protocol

from custom_components.proflame2.protocol.encoder import encode_packet
from custom_components.proflame2.protocol.models import FireplaceState
from custom_components.proflame2.rf import yardstick
from custom_components.proflame2.rf.capture import diagnose_air_payload
from custom_components.proflame2.rf.waveform import build_transmission_plan
from custom_components.proflame2.rf.yardstick import (
    DEFAULT_RX_SCAN_OFFSETS_HZ,
    DIAGNOSTIC_PACKET_BYTES,
    PROFLAME2_FREQUENCY_HZ,
    PROFLAME2_DATA_RATE,
    REASON_DEVICE_NOT_FOUND,
    REASON_LIBUSB_UNAVAILABLE,
    REASON_PERMISSION_DENIED,
    REASON_RFLIB_MISSING,
    YARDSTICK_RX_LEARNING_FREQUENCY_HZ,
    YARDSTICK_RX_LEARNING_PACKET_BYTES,
    YARDSTICK_RX_LEARNING_SWEEP_ENABLED,
    YardStickBackend,
    _should_suppress_verbose_failure,
    normalize_yardstick_backend_error,
)
from custom_components.proflame2.rf.yardstick_worker import (
    COMMAND_OPEN,
    COMMAND_PING,
    COMMAND_RECEIVE,
    COMMAND_SEND,
    COMMAND_STOP,
    YardStickWorkerSupervisor,
    _cleanup_radio_for_exit,
)


class _FakeRadio:
    """Minimal radio stub for backend executor tests."""

    def __init__(self) -> None:
        self.MOD_ASK_OOK = 0x30
        self.calls: list[tuple[str, int | bool | None]] = []

    def setModeIDLE(self) -> None:
        self.calls.append(("setModeIDLE", None))

    def setFreq(self, value: int) -> None:
        self.calls.append(("setFreq", value))

    def setMdmModulation(self, value: int) -> None:
        self.calls.append(("setMdmModulation", value))

    def setMdmDRate(self, value: int) -> None:
        self.calls.append(("setMdmDRate", value))

    def makePktFLEN(self, value: int) -> None:
        self.calls.append(("makePktFLEN", value))

    def setPktPQT(self, value: int) -> None:
        self.calls.append(("setPktPQT", value))

    def setMdmSyncMode(self, value: int) -> None:
        self.calls.append(("setMdmSyncMode", value))

    def setEnableMdmManchester(self, value: bool) -> None:
        self.calls.append(("setEnableMdmManchester", value))

    def setMaxPower(self) -> None:
        self.calls.append(("setMaxPower", None))

    def setModeRX(self) -> None:
        self.calls.append(("setModeRX", None))

    def RFxmit(self, payload: bytes, repeat: int = 0) -> None:
        self.calls.append(("RFxmit", repeat))
        self.last_payload = payload
        self.last_repeat = repeat


class _FakeRadioRfXmitFailure(_FakeRadio):
    """Radio stub whose RFxmit fails after being invoked."""

    def RFxmit(self, payload: bytes, repeat: int = 0) -> None:
        super().RFxmit(payload, repeat=repeat)
        raise RuntimeError("rfxmit boom")


class _FakeRadioPostTxIdleFailure(_FakeRadio):
    """Radio stub whose post-TX idle cleanup fails on the second idle call."""

    def __init__(self) -> None:
        super().__init__()
        self._idle_calls = 0

    def setModeIDLE(self) -> None:
        self._idle_calls += 1
        self.calls.append(("setModeIDLE", None))
        if self._idle_calls >= 2:
            raise RuntimeError("idle cleanup boom")


class _FakeLogger:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.handlers = [object()]

    def info(self, message: str, *args) -> None:
        self.messages.append(message % args if args else message)

    def exception(self, message: str, *args) -> None:
        self.messages.append(message % args if args else message)

    def isEnabledFor(self, _level: int) -> bool:
        return True


class _FakeWorkerSupervisor:
    def __init__(self, responses: dict[str, dict] | None = None, *, error: Exception | None = None) -> None:
        self.responses = responses or {}
        self.error = error
        self.requests: list[tuple[str, dict, float]] = []
        self.stopped = False
        self.stop_reason: str | None = None

    def request(self, command: str, payload: dict | None = None, *, timeout: float):
        self.requests.append((command, payload or {}, timeout))
        if self.error is not None:
            raise self.error
        if command == COMMAND_RECEIVE and command not in self.responses:
            return {
                "request_id": len(self.requests),
                "kind": "NO_PACKET",
                "success": True,
                "timing_ms": 1.0,
                "worker_pid": 1234,
                "worker_generation": 1,
                "status": {"backend_available": True},
                "payload": {},
            }
        return self.responses.get(
            command,
            {
                "request_id": len(self.requests),
                "kind": "OK",
                "success": True,
                "timing_ms": 1.0,
                "worker_pid": 1234,
                "worker_generation": 1,
                "status": {"backend_available": True},
                "payload": {},
            },
        )

    def stop(self, *, reason: str = "stop") -> None:
        self.stopped = True
        self.stop_reason = reason

    def serialize_diagnostics(self) -> dict:
        return {
            "backend_available": True,
            "worker_alive": True,
            "worker_pid": 1234,
            "worker_generation": 1,
            "last_restart_reason": None,
            "last_error": None,
            "last_success_time": None,
            "consecutive_failures": 0,
            "last_operation": self.requests[-1][0] if self.requests else None,
            "last_operation_duration_ms": 1.0,
        }


class _FakeEvent:
    def __init__(self) -> None:
        self.cleared = False
        self.set_called = False

    def clear(self) -> None:
        self.cleared = True

    def set(self) -> None:
        self.set_called = True


class _FakeUsbHandle:
    def __init__(self) -> None:
        self.released = False

    def releaseInterface(self, index: int = 0) -> None:
        assert index == 0
        self.released = True


class _CleanupRadio:
    def __init__(self) -> None:
        self.idle_called = False
        self._threadGo = _FakeEvent()
        self.reset_event = _FakeEvent()
        self.xmit_event = _FakeEvent()
        self._do = _FakeUsbHandle()

    def setModeIDLE(self) -> None:
        self.idle_called = True


class _FakeConn:
    def __init__(self, *, poll_result: bool = True, recv_value: dict | None = None) -> None:
        self.poll_result = poll_result
        self.recv_value = recv_value or {
            "request_id": 1,
            "kind": "OK",
            "success": True,
            "timing_ms": 1.0,
            "worker_pid": 999,
            "worker_generation": 1,
            "status": {"backend_available": True},
            "payload": {},
        }
        self.sent: list[dict] = []
        self.closed = False

    def send(self, value: dict) -> None:
        self.sent.append(value)

    def poll(self, _timeout: float) -> bool:
        return self.poll_result

    def recv(self) -> dict:
        return self.recv_value

    def close(self) -> None:
        self.closed = True


class _FakeProcess:
    def __init__(self, *, alive: bool = True, pid: int = 111, exitcode: int | None = None) -> None:
        self._alive = alive
        self.pid = pid
        self.exitcode = exitcode
        self.terminate_calls = 0
        self.join_calls: list[float] = []

    def is_alive(self) -> bool:
        return self._alive

    def terminate(self) -> None:
        self.terminate_calls += 1
        self._alive = False
        self.exitcode = -15

    def join(self, timeout: float | None = None) -> None:
        self.join_calls.append(timeout or 0.0)


def test_connect_uses_executor_for_radio_open_and_configuration(monkeypatch) -> None:
    """RfCat construction and radio setup should stay off the HA event loop."""

    async def _run() -> None:
        fake_radio = _FakeRadio()
        executor_calls: list[tuple[object, tuple[object, ...]]] = []

        def fake_open_radio(device_index: int):
            assert device_index == 3
            return fake_radio, TimeoutError, 0x30

        async def fake_executor_job(func, *args):
            executor_calls.append((func, args))
            return func(*args)

        monkeypatch.setattr(yardstick, "_open_radio", fake_open_radio)

        backend = YardStickBackend(
            device_index=3,
            executor_job=fake_executor_job,
            worker_mode=False,
        )
        await backend.connect()

        assert executor_calls[0][0] is fake_open_radio
        assert executor_calls[0][1] == (3,)
        assert getattr(executor_calls[1][0], "__name__", "") == "_configure_radio"
        assert fake_radio.calls

    asyncio.run(_run())


def test_cleanup_radio_for_exit_idles_and_releases_interface() -> None:
    """Worker cleanup should idle the radio and release USB interface if exposed."""

    radio = _CleanupRadio()

    _cleanup_radio_for_exit(radio)

    assert radio.idle_called is True
    assert radio._threadGo.cleared is True
    assert radio.reset_event.cleared is True
    assert radio.xmit_event.set_called is True
    assert radio._do.released is True


def test_worker_supervisor_mock_mode_start_send_ping_stop() -> None:
    """The production supervisor should work through a spawned mock worker."""

    supervisor = YardStickWorkerSupervisor(
        device_index=0,
        cooldown_seconds=0.01,
        mock_mode=True,
        mp_context=mp.get_context("spawn"),
    )
    try:
        open_response = supervisor.request(COMMAND_OPEN, timeout=5.0)
        assert open_response["success"] is True
        ping_response = supervisor.request(COMMAND_PING, timeout=5.0)
        assert ping_response["kind"] == "STATUS"
        send_response = supervisor.request(
            COMMAND_SEND,
            {
                "air_payload_hex": "e5a9a9b96aa96e55596b95559ae55695b9a9a5aea6a9580000",
                "tx_frequency_hz": PROFLAME2_FREQUENCY_HZ,
                "software_transmissions": 5,
                "inter_frame_gap_ms": 0.0,
            },
            timeout=5.0,
        )
        assert send_response["success"] is True
        assert send_response["payload"]["burst"]["software_transmissions"] == 5
        diagnostics = supervisor.serialize_diagnostics()
        assert diagnostics["backend_available"] is True
        assert diagnostics["worker_alive"] is True
        assert diagnostics["worker_generation"] >= 1
    finally:
        supervisor.stop()


def test_worker_supervisor_honors_cooldown_after_timeout() -> None:
    """A timed out worker request should terminate the worker and activate cooldown."""

    supervisor = YardStickWorkerSupervisor(
        device_index=0,
        cooldown_seconds=0.5,
        mock_mode=True,
        mp_context=mp.get_context("spawn"),
    )
    try:
        with pytest.raises(RuntimeError):
            supervisor.request(COMMAND_OPEN, timeout=0.0001)
        diagnostics = supervisor.serialize_diagnostics()
        assert diagnostics["backend_available"] is False
        assert diagnostics["consecutive_failures"] >= 1
        assert diagnostics["last_restart_reason"] == "timeout:OPEN"
        with pytest.raises(RuntimeError, match="retry after cooldown"):
            supervisor.request(COMMAND_OPEN, timeout=1.0)
    finally:
        supervisor.stop()


def test_worker_supervisor_stop_sends_graceful_stop_and_records_reason() -> None:
    """Supervisor stop should send STOP when no request is in flight."""

    supervisor = YardStickWorkerSupervisor(mock_mode=True)
    supervisor._process = _FakeProcess()
    supervisor._conn = _FakeConn(
        recv_value={
            "request_id": 1,
            "kind": "OK",
            "success": True,
            "timing_ms": 1.0,
            "worker_pid": 111,
            "worker_generation": 1,
            "status": {"backend_available": True},
            "payload": {},
        }
    )
    supervisor.stop(reason="config_entry_unload")

    assert supervisor.diagnostics.last_shutdown_reason == "config_entry_unload"
    assert supervisor.diagnostics.final_exit_code == -15
    assert supervisor.diagnostics.worker_alive is False


def test_worker_supervisor_rejects_new_requests_during_shutdown() -> None:
    """Requests should fail quickly once shutdown has begun."""

    supervisor = YardStickWorkerSupervisor(mock_mode=True)
    supervisor._shutdown_requested = True
    supervisor._shutdown_reason = "ha_shutdown"

    with pytest.raises(Exception, match="shutdown in progress"):
        supervisor.request(COMMAND_PING, timeout=1.0)


def test_yardstick_defaults_match_smartfire_reference_frequency() -> None:
    """The default Proflame2 Yard Stick frequency should match SmartFire."""

    assert PROFLAME2_FREQUENCY_HZ == 314_973_000


def test_yardstick_learning_profile_constants_match_hardware_results() -> None:
    """The Yard Stick learning profile should track the proven RX settings."""

    assert YARDSTICK_RX_LEARNING_FREQUENCY_HZ == 315_000_000
    assert YARDSTICK_RX_LEARNING_PACKET_BYTES == 255
    assert YARDSTICK_RX_LEARNING_SWEEP_ENABLED is False
    assert YARDSTICK_RX_LEARNING_FREQUENCY_HZ != PROFLAME2_FREQUENCY_HZ


def test_default_frequency_scan_includes_smartfire_and_rtl433_centers() -> None:
    """The default scan window should include both known relevant frequencies."""

    backend = YardStickBackend()

    assert backend._frequency_scan_hz == tuple(
        PROFLAME2_FREQUENCY_HZ + offset_hz for offset_hz in DEFAULT_RX_SCAN_OFFSETS_HZ
    )
    assert PROFLAME2_FREQUENCY_HZ in backend._frequency_scan_hz
    assert 315_000_000 in backend._frequency_scan_hz


def test_receive_timeout_advances_to_next_frequency() -> None:
    """A timeout should move the Yard Stick backend to the next scan frequency."""

    async def _run() -> None:
        fake_radio = _FakeRadio()

        async def fake_executor_job(func, *args):
            return func(*args)

        backend = YardStickBackend(
            radio=fake_radio,
            executor_job=fake_executor_job,
            frequency_scan_hz=(314_973_000, 315_000_000),
        )
        backend._timeout_exception = TimeoutError

        def fake_recv_once(timeout):
            return None

        backend._recv_once = fake_recv_once  # type: ignore[method-assign]

        sample = await backend.receive_sample(timeout=0.5)

        assert sample is None
        assert backend._active_frequency_hz == 315_000_000
        assert ("setFreq", 315_000_000) in fake_radio.calls

    asyncio.run(_run())


def test_fixed_frequency_mode_does_not_retune_after_decode_failure() -> None:
    """Diagnostic fixed-frequency mode should not retune after a failed decode."""

    async def _run() -> None:
        fake_radio = _FakeRadio()

        async def fake_executor_job(func, *args):
            return func(*args)

        backend = YardStickBackend(
            radio=fake_radio,
            executor_job=fake_executor_job,
            frequency_scan_hz=(314_973_000, 315_000_000),
            sweep_enabled=False,
        )
        backend._timeout_exception = TimeoutError

        backend._recv_once = lambda timeout: bytes.fromhex(  # type: ignore[method-assign]
            "fffffff47bfffffffffffefffffd6bffffffffffffbffffff7"
        )

        sample = await backend.receive_sample(timeout=0.5)

        assert sample is None
        assert backend._active_frequency_hz == 314_973_000
        assert ("setFreq", 315_000_000) not in fake_radio.calls
        assert backend.last_receive_status is not None
        assert backend.last_receive_status.outcome == "payload_no_candidates"

    asyncio.run(_run())


def test_diagnostic_packet_length_is_applied_in_probe_mode() -> None:
    """Probe mode should still apply the configured receive payload length."""

    async def _run() -> None:
        fake_radio = _FakeRadio()

        async def fake_executor_job(func, *args):
            return func(*args)

        backend = YardStickBackend(
            radio=fake_radio,
            executor_job=fake_executor_job,
            probe_mode=True,
            packet_length_bytes=DIAGNOSTIC_PACKET_BYTES,
        )
        await backend.connect()

        assert ("makePktFLEN", DIAGNOSTIC_PACKET_BYTES) in fake_radio.calls

    asyncio.run(_run())


def test_receive_status_reports_no_payload() -> None:
    """A timeout should leave a structured no-payload status behind."""

    async def _run() -> None:
        fake_radio = _FakeRadio()

        async def fake_executor_job(func, *args):
            return func(*args)

        backend = YardStickBackend(
            radio=fake_radio,
            executor_job=fake_executor_job,
            sweep_enabled=False,
        )
        backend._timeout_exception = TimeoutError
        backend._recv_once = lambda timeout: None  # type: ignore[method-assign]

        sample = await backend.receive_sample(timeout=1.0)

        assert sample is None
        assert backend.last_receive_status is not None
        assert backend.last_receive_status.outcome == "no_payload"

    asyncio.run(_run())


def test_receive_status_reports_decoded_packet() -> None:
    """A successful decode should publish compact receive status metadata."""

    async def _run() -> None:
        fake_radio = _FakeRadio()

        async def fake_executor_job(func, *args):
            return func(*args)

        backend = YardStickBackend(
            radio=fake_radio,
            executor_job=fake_executor_job,
            sweep_enabled=False,
        )
        backend._timeout_exception = TimeoutError
        backend._recv_once = lambda timeout: bytes.fromhex(  # type: ignore[method-assign]
            "e5a9a9b96aa96e55596b95559ae55695b9a9a5aea6a9580000"
        )

        sample = await backend.receive_sample(timeout=1.0)

        assert sample is not None
        assert backend.last_receive_status is not None
        assert backend.last_receive_status.outcome == "decoded_packet"
        assert backend.last_receive_status.candidate_count is not None
        assert backend.last_receive_status.candidate_count >= 1

    asyncio.run(_run())


def test_worker_backed_send_uses_supervisor_and_never_calls_rflib(remote_profile, monkeypatch) -> None:
    """Production worker mode should delegate send through the worker supervisor."""

    async def _run() -> None:
        fake_supervisor = _FakeWorkerSupervisor(
            responses={
                COMMAND_OPEN: {
                    "request_id": 1,
                    "kind": "OK",
                    "success": True,
                    "timing_ms": 2.0,
                    "worker_pid": 2001,
                    "worker_generation": 3,
                    "status": {"backend_available": True},
                    "payload": {},
                },
                COMMAND_SEND: {
                    "request_id": 2,
                    "kind": "OK",
                    "success": True,
                    "timing_ms": 5.0,
                    "worker_pid": 2001,
                    "worker_generation": 3,
                    "status": {"backend_available": True},
                    "payload": {
                        "tx_settings": {
                            "frequency_hz": PROFLAME2_FREQUENCY_HZ,
                            "modulation_mode": 0x30,
                            "data_rate": PROFLAME2_DATA_RATE,
                        },
                        "burst": {"software_transmissions": 5},
                    },
                },
            }
        )

        def explode_open_radio(_device_index: int):
            raise AssertionError("HA-side _open_radio should not run in worker mode")

        monkeypatch.setattr(yardstick, "_open_radio", explode_open_radio)

        backend = YardStickBackend(worker_supervisor=fake_supervisor)
        state = FireplaceState(power=True, flame=1, fan=0, light=0)
        packet = encode_packet(state, remote_profile, source="test")
        packet.transmission_plan = build_transmission_plan(packet.frame)

        result = await backend.send(packet)

        assert result.backend_name == "yardstick"
        assert [command for command, _payload, _timeout in fake_supervisor.requests] == [
            COMMAND_OPEN,
            COMMAND_SEND,
        ]
        assert fake_supervisor.requests[-1][1]["air_payload_hex"] == packet.transmission_plan.air_payload.hex()

    asyncio.run(_run())


def test_worker_backed_receive_decodes_payload(remote_profile) -> None:
    """Worker-mode receive should decode a raw payload returned from IPC."""

    async def _run() -> None:
        fake_supervisor = _FakeWorkerSupervisor(
            responses={
                COMMAND_OPEN: {
                    "request_id": 1,
                    "kind": "OK",
                    "success": True,
                    "timing_ms": 2.0,
                    "worker_pid": 2001,
                    "worker_generation": 3,
                    "status": {"backend_available": True},
                    "payload": {},
                },
                COMMAND_RECEIVE: {
                    "request_id": 2,
                    "kind": "OK",
                    "success": True,
                    "timing_ms": 5.0,
                    "worker_pid": 2001,
                    "worker_generation": 3,
                    "status": {"backend_available": True},
                    "payload": {
                        "raw_payload_hex": "e5a9a9b96aa96e55596b95559ae55695b9a9a5aea6a9580000",
                    },
                },
            }
        )
        backend = YardStickBackend(
            worker_supervisor=fake_supervisor,
            sweep_enabled=False,
        )

        packet = await backend.receive(timeout=1.0)

        assert packet is not None
        assert packet.remote_id == remote_profile.serial_id
        assert backend.last_receive_status is not None
        assert backend.last_receive_status.outcome == "decoded_packet"

    asyncio.run(_run())


def test_worker_backed_close_stops_supervisor() -> None:
    """Backend close should stop the worker supervisor in worker mode."""

    async def _run() -> None:
        fake_supervisor = _FakeWorkerSupervisor()
        backend = YardStickBackend(worker_supervisor=fake_supervisor)
        await backend.close(reason="config_entry_unload")
        assert fake_supervisor.stopped is True
        assert fake_supervisor.stop_reason == "config_entry_unload"

    asyncio.run(_run())


def test_receive_status_reports_post_processing_exception(monkeypatch) -> None:
    """Decoder/post-processing exceptions should populate structured exception details."""

    async def _run() -> None:
        fake_radio = _FakeRadio()

        async def fake_executor_job(func, *args):
            return func(*args)

        backend = YardStickBackend(
            radio=fake_radio,
            executor_job=fake_executor_job,
            sweep_enabled=False,
        )
        backend._timeout_exception = TimeoutError
        backend._recv_once = lambda timeout: bytes.fromhex(  # type: ignore[method-assign]
            "e5a9a9b96aa96e55596b95559ae55695b9a9a5aea6a9580000"
        )

        def explode(_raw_payload):
            raise RuntimeError("decode boom")

        monkeypatch.setattr(yardstick, "diagnose_air_payload", explode)

        with pytest.raises(RuntimeError, match="decode boom"):
            await backend.receive_sample(timeout=1.0)

        assert backend.last_receive_status is not None
        assert backend.last_receive_status.outcome == "exception"
        assert backend.last_receive_status.reason == "RuntimeError"
        assert backend.last_receive_status.exception_type == "RuntimeError"
        assert backend.last_receive_status.exception_message == "decode boom"

    asyncio.run(_run())


def test_send_uses_transmission_plan_payload_and_software_burst(remote_profile) -> None:
    """Yard Stick TX should default to five explicit software transmissions."""

    async def _run() -> None:
        fake_radio = _FakeRadio()
        executor_calls: list[tuple[str, tuple[object, ...]]] = []

        async def fake_executor_job(func, *args):
            executor_calls.append((getattr(func, "__name__", repr(func)), args))
            return func(*args)

        backend = YardStickBackend(
            radio=fake_radio,
            executor_job=fake_executor_job,
            sweep_enabled=False,
        )
        state = FireplaceState(power=True, flame=1, fan=0, light=0)
        packet = encode_packet(state, remote_profile, source="test")
        packet.transmission_plan = build_transmission_plan(packet.frame)

        result = await backend.send(packet)

        assert result.backend_name == "yardstick"
        assert fake_radio.last_payload == packet.transmission_plan.air_payload
        assert fake_radio.last_repeat == 0
        assert fake_radio.calls.count(("RFxmit", 0)) == packet.transmission_plan.repeat_count
        assert ("setFreq", PROFLAME2_FREQUENCY_HZ) in fake_radio.calls
        assert ("setMdmDRate", PROFLAME2_DATA_RATE) in fake_radio.calls
        assert ("setPktPQT", 0) in fake_radio.calls
        assert ("setMaxPower", None) in fake_radio.calls
        assert any(name == "_configure_transmit_radio" for name, _args in executor_calls)
        assert any(name == "_transmit_air_payload" for name, _args in executor_calls)
        assert fake_radio.calls.count(("setModeIDLE", None)) >= 2
        assert fake_radio.calls[-1] == ("setModeIDLE", None)

    asyncio.run(_run())


def test_send_software_gap_sleep_occurs_only_when_configured(remote_profile, monkeypatch) -> None:
    """Inter-frame sleep should be used only for nonzero software burst gaps."""

    async def _run() -> None:
        fake_radio = _FakeRadio()
        sleep_calls: list[float] = []

        async def fake_executor_job(func, *args):
            return func(*args)

        monkeypatch.setattr(yardstick.time, "sleep", lambda seconds: sleep_calls.append(seconds))

        backend = YardStickBackend(
            radio=fake_radio,
            executor_job=fake_executor_job,
            sweep_enabled=False,
            tx_transmissions=3,
            tx_inter_frame_gap_ms=10,
        )
        state = FireplaceState(power=True, flame=1, fan=0, light=0)
        packet = encode_packet(state, remote_profile, source="test")
        packet.transmission_plan = build_transmission_plan(packet.frame)

        await backend.send(packet)

        assert fake_radio.calls.count(("RFxmit", 0)) == 3
        assert sleep_calls == [0.01, 0.01]

    asyncio.run(_run())


def test_send_calls_set_mode_idle_after_rfxmit_raises(remote_profile) -> None:
    """Post-TX idle should still be requested after RFxmit raises."""

    async def _run() -> None:
        fake_radio = _FakeRadioRfXmitFailure()

        async def fake_executor_job(func, *args):
            return func(*args)

        backend = YardStickBackend(
            radio=fake_radio,
            executor_job=fake_executor_job,
            sweep_enabled=False,
        )
        state = FireplaceState(power=True, flame=1, fan=0, light=0)
        packet = encode_packet(state, remote_profile, source="test")
        packet.transmission_plan = build_transmission_plan(packet.frame)

        with pytest.raises(RuntimeError, match="rfxmit boom"):
            await backend.send(packet)

        assert fake_radio.calls.count(("setModeIDLE", None)) >= 2
        assert fake_radio.calls[-1] == ("setModeIDLE", None)

    asyncio.run(_run())


def test_send_success_is_not_masked_by_post_tx_idle_failure(remote_profile) -> None:
    """A post-TX idle cleanup failure should not erase a successful RFxmit result."""

    async def _run() -> None:
        fake_radio = _FakeRadioPostTxIdleFailure()

        async def fake_executor_job(func, *args):
            return func(*args)

        backend = YardStickBackend(
            radio=fake_radio,
            executor_job=fake_executor_job,
            sweep_enabled=False,
        )
        state = FireplaceState(power=True, flame=1, fan=0, light=0)
        packet = encode_packet(state, remote_profile, source="test")
        packet.transmission_plan = build_transmission_plan(packet.frame)

        result = await backend.send(packet)

        assert result.backend_name == "yardstick"
        assert fake_radio.last_payload == packet.transmission_plan.air_payload
        assert fake_radio.last_repeat == 0
        assert fake_radio.calls.count(("setModeIDLE", None)) >= 2
        assert fake_radio.calls[-1] == ("setModeIDLE", None)

    asyncio.run(_run())


def test_send_honors_transmit_frequency_override(remote_profile) -> None:
    """CLI/experimental TX should be able to override the transmit frequency."""

    async def _run() -> None:
        fake_radio = _FakeRadio()

        async def fake_executor_job(func, *args):
            return func(*args)

        backend = YardStickBackend(
            radio=fake_radio,
            executor_job=fake_executor_job,
            sweep_enabled=False,
            tx_frequency_hz=315_000_000,
        )
        state = FireplaceState(power=True, flame=1, fan=0, light=0)
        packet = encode_packet(state, remote_profile, source="test")
        packet.transmission_plan = build_transmission_plan(packet.frame)

        await backend.send(packet)

        assert ("setFreq", 315_000_000) in fake_radio.calls

    asyncio.run(_run())


def test_send_times_out_when_connect_hangs(remote_profile) -> None:
    """A stalled lazy connect should fail visibly instead of hanging forever."""

    async def _run() -> None:
        async def fake_executor_job(func, *args):
            if getattr(func, "__name__", "") == "_open_radio":
                await asyncio.sleep(1)
            return func(*args)

        backend = YardStickBackend(
            executor_job=fake_executor_job,
            sweep_enabled=False,
            connect_timeout_seconds=0.01,
        )
        packet = encode_packet(FireplaceState(power=True, flame=1), remote_profile, source="test")
        packet.transmission_plan = build_transmission_plan(packet.frame)

        with pytest.raises(RuntimeError, match="Yard Stick worker timed out during OPEN."):
            await backend.send(packet)

    asyncio.run(_run())


def test_send_lock_contention_times_out_cleanly(remote_profile) -> None:
    """A busy backend lock should fail instead of hanging silently."""

    async def _run() -> None:
        fake_radio = _FakeRadio()

        async def fake_executor_job(func, *args):
            return func(*args)

        backend = YardStickBackend(
            radio=fake_radio,
            executor_job=fake_executor_job,
            sweep_enabled=False,
            operation_lock_timeout_seconds=0.01,
        )
        await backend._operation_lock.acquire()
        try:
            packet = encode_packet(
                FireplaceState(power=True, flame=1), remote_profile, source="test"
            )
            packet.transmission_plan = build_transmission_plan(packet.frame)
            with pytest.raises(
                RuntimeError,
                match="Timed out while waiting for Yard Stick backend lock during send.",
            ):
                await backend.send(packet)
        finally:
            backend._operation_lock.release()

    asyncio.run(_run())


def test_send_emits_breadcrumbs_through_connect_and_tx(remote_profile) -> None:
    """Send breadcrumbs should cover connect, configure, executor submit, and success."""

    async def _run() -> None:
        fake_radio = _FakeRadio()
        fake_logger = _FakeLogger()

        async def fake_executor_job(func, *args):
            return func(*args)

        backend = YardStickBackend(
            radio=fake_radio,
            executor_job=fake_executor_job,
            sweep_enabled=False,
        )
        backend._packet_logger = fake_logger
        packet = encode_packet(FireplaceState(power=True, flame=1), remote_profile, source="test")
        packet.transmission_plan = build_transmission_plan(packet.frame)

        await backend.send(packet)

        assert any("Entered YardStickBackend.send" in message for message in fake_logger.messages)
        assert any("send: lock acquired" in message for message in fake_logger.messages)
        assert any("send: TX configure start" in message for message in fake_logger.messages)
        assert any("send: TX configure complete" in message for message in fake_logger.messages)
        assert any("send: blocking TX executor submit" in message for message in fake_logger.messages)
        assert any("Entered blocking TX executor" in message for message in fake_logger.messages)
        assert any("TX success" in message for message in fake_logger.messages)
        assert any("send: returning success" in message for message in fake_logger.messages)

    asyncio.run(_run())


def test_send_rejects_missing_transmission_plan(remote_profile) -> None:
    """Real Yard Stick TX requires the already-built transmission plan."""

    async def _run() -> None:
        fake_radio = _FakeRadio()

        async def fake_executor_job(func, *args):
            return func(*args)

        backend = YardStickBackend(
            radio=fake_radio,
            executor_job=fake_executor_job,
            sweep_enabled=False,
        )
        packet = encode_packet(FireplaceState(power=True, flame=1), remote_profile, source="test")

        with pytest.raises(RuntimeError, match="transmission_plan"):
            await backend.send(packet)

    asyncio.run(_run())


def test_close_skips_nonexistent_radio_close_method() -> None:
    """Yard Stick teardown should not assume rflib exposes a close method."""

    async def _run() -> None:
        fake_radio = _FakeRadio()

        async def fake_executor_job(func, *args):
            return func(*args)

        backend = YardStickBackend(
            radio=fake_radio,
            executor_job=fake_executor_job,
            sweep_enabled=False,
        )

        await backend.close()

        assert ("setModeIDLE", None) in fake_radio.calls

    asyncio.run(_run())


def test_noise_like_idle_payload_suppresses_verbose_decode_output() -> None:
    """Overwhelmingly idle/noise-like payloads should not dump giant symbol logs."""

    raw_payload = bytes.fromhex(
        "f800000000000000000000000000000000000000000000000000000000000000"
    )
    diagnostics = diagnose_air_payload(raw_payload)

    assert diagnostics.candidates == ()
    assert _should_suppress_verbose_failure(raw_payload, diagnostics) is True


def test_structured_near_miss_payload_keeps_verbose_decode_output() -> None:
    """Plausible structured payloads should keep detailed diagnostics."""

    raw_payload = bytes.fromhex(
        "f8000000000000014dcb554b72aacb5caaacd72ab4adcd4d2d"
    )
    diagnostics = diagnose_air_payload(raw_payload)

    assert diagnostics.candidates == ()
    assert _should_suppress_verbose_failure(raw_payload, diagnostics) is False


@pytest.mark.parametrize(
    ("exc", "reason", "message"),
    [
        (
            ImportError("No module named rflib"),
            REASON_RFLIB_MISSING,
            "rflib Python package is not installed",
        ),
        (
            RuntimeError("No backend available"),
            REASON_LIBUSB_UNAVAILABLE,
            "libusb backend could not be loaded",
        ),
        (
            RuntimeError("Device not found"),
            REASON_DEVICE_NOT_FOUND,
            "No YARD Stick One device was found",
        ),
        (
            PermissionError("Operation not permitted"),
            REASON_PERMISSION_DENIED,
            "access was denied",
        ),
    ],
)
def test_yardstick_error_mapping_is_user_facing(exc, reason, message) -> None:
    """Low-level USB/import errors should map to stable UI-friendly messages."""

    normalized = normalize_yardstick_backend_error(exc)

    assert normalized.reason == reason
    assert message in str(normalized)
