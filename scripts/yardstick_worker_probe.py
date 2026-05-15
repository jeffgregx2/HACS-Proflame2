"""Prototype a process-isolated Yard Stick One / rflib worker.

This script intentionally does not touch the Home Assistant integration path.
It exists to validate whether keeping rflib inside a dedicated worker process
gives us a safer lifecycle boundary for open / TX / timeout / restart testing.
"""

from __future__ import annotations

import argparse
import multiprocessing as mp
import os
import signal
import time
import traceback
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from multiprocessing.connection import Connection
from typing import Any

PROFLAME2_FREQUENCY_HZ = 314_973_000
PROFLAME2_DATA_RATE = 2400
DEFAULT_TRANSMISSIONS = 5
DEFAULT_OPERATION_TIMEOUT_SECONDS = 10.0
DEFAULT_WORKER_STARTUP_TIMEOUT_SECONDS = 15.0

COMMAND_OPEN = "OPEN"
COMMAND_SEND = "SEND"
COMMAND_SET_IDLE = "SET_IDLE"
COMMAND_PING = "PING"
COMMAND_STOP = "STOP"

STATUS_OK = "OK"
STATUS_ERROR = "ERROR"
STATUS_TIMEOUT = "TIMEOUT"
STATUS_STATUS = "STATUS"


@dataclass(slots=True)
class WorkerStatus:
    """Serializable worker/supervisor state attached to every response."""

    worker_pid: int
    worker_generation: int
    backend_available: bool
    radio_open: bool
    last_error: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(prefix: str, message: str) -> None:
    print(f"{_now()} {prefix} {message}", flush=True)


class MockRfCat:
    """Tiny stand-in for rflib so we can validate worker behavior without hardware."""

    MOD_ASK_OOK = 0x30

    def __init__(self, idx: int = 0) -> None:
        self.idx = idx
        self.frequency_hz: int | None = None
        self.data_rate: int | None = None
        self.modulation: int | None = None
        self.sync_mode: int | None = None
        self.pqt: int | None = None
        self.manchester_enabled: bool | None = None
        self.max_power_requested = False
        self.idle_count = 0
        self.transmit_calls = 0

    def setModeIDLE(self) -> None:
        self.idle_count += 1

    def setFreq(self, frequency_hz: int) -> None:
        self.frequency_hz = frequency_hz

    def setMdmModulation(self, modulation: int) -> None:
        self.modulation = modulation

    def setMdmDRate(self, data_rate: int) -> None:
        self.data_rate = data_rate

    def setMdmSyncMode(self, sync_mode: int) -> None:
        self.sync_mode = sync_mode

    def setPktPQT(self, value: int) -> None:
        self.pqt = value

    def setEnableMdmManchester(self, enabled: bool) -> None:
        self.manchester_enabled = enabled

    def setMaxPower(self) -> None:
        self.max_power_requested = True

    def RFxmit(self, payload: bytes) -> None:
        self.transmit_calls += 1
        time.sleep(0.01)


def _response(
    *,
    request_id: int,
    kind: str,
    status: WorkerStatus,
    timing_ms: float,
    payload: dict[str, Any] | None = None,
    error_class: str | None = None,
    error: str | None = None,
    tb: str | None = None,
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "kind": kind,
        "success": kind in {STATUS_OK, STATUS_STATUS},
        "error_class": error_class,
        "error": error,
        "traceback": tb,
        "timing_ms": round(timing_ms, 2),
        "worker_status": asdict(status),
        "payload": payload or {},
    }


def _configure_transmit_radio(radio: Any, frequency_hz: int) -> dict[str, Any]:
    if hasattr(radio, "setModeIDLE"):
        radio.setModeIDLE()
    modulation = getattr(radio, "MOD_ASK_OOK", 0x30)
    radio.setFreq(frequency_hz)
    radio.setMdmModulation(modulation)
    radio.setMdmDRate(PROFLAME2_DATA_RATE)
    if hasattr(radio, "setMdmSyncMode"):
        radio.setMdmSyncMode(0)
    if hasattr(radio, "setPktPQT"):
        radio.setPktPQT(0)
    if hasattr(radio, "setEnableMdmManchester"):
        radio.setEnableMdmManchester(False)
    if hasattr(radio, "setMaxPower"):
        radio.setMaxPower()
    return {
        "frequency_hz": frequency_hz,
        "modulation_mode": modulation,
        "data_rate": PROFLAME2_DATA_RATE,
        "sync_mode": 0,
        "packet_quality_threshold": 0,
        "manchester_enabled": False,
        "max_power_requested": hasattr(radio, "setMaxPower"),
    }


def _send_software_burst(
    radio: Any,
    payload: bytes,
    transmissions: int,
    inter_frame_gap_ms: float,
) -> dict[str, Any]:
    frame_timings_ms: list[float] = []
    try:
        for frame_index in range(transmissions):
            start = time.monotonic()
            _log("WORKER", f"RFxmit frame_start index={frame_index + 1}/{transmissions}")
            radio.RFxmit(payload)
            elapsed_ms = (time.monotonic() - start) * 1000
            frame_timings_ms.append(round(elapsed_ms, 2))
            _log("WORKER", f"RFxmit frame_complete index={frame_index + 1}/{transmissions} elapsed_ms={elapsed_ms:.2f}")
            if inter_frame_gap_ms > 0 and frame_index < transmissions - 1:
                time.sleep(inter_frame_gap_ms / 1000.0)
    finally:
        _log("WORKER", "post_tx_idle_requested")
        if hasattr(radio, "setModeIDLE"):
            radio.setModeIDLE()
        _log("WORKER", "post_tx_idle_complete")
    return {
        "software_transmissions": transmissions,
        "inter_frame_gap_ms": inter_frame_gap_ms,
        "frame_timings_ms": frame_timings_ms,
    }


def _build_status(
    *,
    generation: int,
    radio: Any | None,
    last_error: str | None,
) -> WorkerStatus:
    return WorkerStatus(
        worker_pid=os.getpid(),
        worker_generation=generation,
        backend_available=radio is not None,
        radio_open=radio is not None,
        last_error=last_error,
    )


def _open_radio(device_index: int, mock: bool) -> tuple[Any, int]:
    if mock:
        return MockRfCat(idx=device_index), MockRfCat.MOD_ASK_OOK

    from rflib import RfCat  # Imported only inside the worker process.

    radio = RfCat(idx=device_index)
    modulation = getattr(radio, "MOD_ASK_OOK", 0x30)
    return radio, modulation


def _worker_main(conn: Connection, args: dict[str, Any]) -> None:
    generation = int(args["generation"])
    device_index = int(args["device_index"])
    frequency_hz = int(args["frequency_hz"])
    mock = bool(args["mock"])
    radio: Any | None = None
    last_error: str | None = None

    _log(
        "WORKER",
        f"started pid={os.getpid()} generation={generation} device_index={device_index} mock={mock}",
    )

    while True:
        try:
            request = conn.recv()
        except EOFError:
            _log("WORKER", "control_pipe_closed exiting")
            break

        request_id = int(request["request_id"])
        command = str(request["command"])
        payload = request.get("payload", {})
        started = time.monotonic()
        _log("WORKER", f"request_start id={request_id} command={command}")

        try:
            if command == COMMAND_PING:
                response = _response(
                    request_id=request_id,
                    kind=STATUS_STATUS,
                    status=_build_status(generation=generation, radio=radio, last_error=last_error),
                    timing_ms=(time.monotonic() - started) * 1000,
                    payload={"message": "pong"},
                )
            elif command == COMMAND_OPEN:
                _log("WORKER", f"open_start device_index={device_index}")
                radio, modulation = _open_radio(device_index, mock)
                _log("WORKER", "open_complete")
                settings = _configure_transmit_radio(radio, frequency_hz)
                settings["modulation_mode"] = modulation
                response = _response(
                    request_id=request_id,
                    kind=STATUS_OK,
                    status=_build_status(generation=generation, radio=radio, last_error=None),
                    timing_ms=(time.monotonic() - started) * 1000,
                    payload={"settings": settings},
                )
                last_error = None
            elif command == COMMAND_SET_IDLE:
                if radio is None:
                    raise RuntimeError("Worker radio is not open.")
                _log("WORKER", "set_idle_requested")
                if hasattr(radio, "setModeIDLE"):
                    radio.setModeIDLE()
                _log("WORKER", "set_idle_complete")
                response = _response(
                    request_id=request_id,
                    kind=STATUS_OK,
                    status=_build_status(generation=generation, radio=radio, last_error=None),
                    timing_ms=(time.monotonic() - started) * 1000,
                    payload={"message": "idle"},
                )
                last_error = None
            elif command == COMMAND_SEND:
                if radio is None:
                    raise RuntimeError("Worker radio is not open.")
                tx_payload = bytes.fromhex(str(payload["payload_hex"]))
                transmissions = int(payload["transmissions"])
                inter_frame_gap_ms = float(payload["inter_frame_gap_ms"])
                _log(
                    "WORKER",
                    "send_start "
                    f"payload_length={len(tx_payload)} frequency_hz={frequency_hz} "
                    f"software_transmissions={transmissions} inter_frame_gap_ms={inter_frame_gap_ms}",
                )
                settings = _configure_transmit_radio(radio, frequency_hz)
                burst_info = _send_software_burst(
                    radio,
                    tx_payload,
                    transmissions,
                    inter_frame_gap_ms,
                )
                _log("WORKER", "send_complete")
                response = _response(
                    request_id=request_id,
                    kind=STATUS_OK,
                    status=_build_status(generation=generation, radio=radio, last_error=None),
                    timing_ms=(time.monotonic() - started) * 1000,
                    payload={
                        "settings": settings,
                        "burst": burst_info,
                    },
                )
                last_error = None
            elif command == COMMAND_STOP:
                _log("WORKER", "stop_requested")
                if radio is not None and hasattr(radio, "setModeIDLE"):
                    try:
                        radio.setModeIDLE()
                    except Exception:
                        pass
                response = _response(
                    request_id=request_id,
                    kind=STATUS_OK,
                    status=_build_status(generation=generation, radio=radio, last_error=last_error),
                    timing_ms=(time.monotonic() - started) * 1000,
                    payload={"message": "stopping"},
                )
                conn.send(response)
                _log("WORKER", "stop_complete")
                break
            else:
                raise RuntimeError(f"Unsupported command: {command}")
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            response = _response(
                request_id=request_id,
                kind=STATUS_ERROR,
                status=_build_status(generation=generation, radio=radio, last_error=last_error),
                timing_ms=(time.monotonic() - started) * 1000,
                error_class=type(exc).__name__,
                error=str(exc),
                tb=traceback.format_exc(),
            )
            _log(
                "WORKER",
                f"request_error id={request_id} command={command} exception_type={type(exc).__name__} error={exc}",
            )

        conn.send(response)

    conn.close()
    _log("WORKER", "exiting")


class YardStickWorkerSupervisor:
    """Simple parent-side process supervisor for the prototype worker."""

    def __init__(
        self,
        *,
        device_index: int,
        frequency_hz: int,
        operation_timeout_seconds: float,
        worker_startup_timeout_seconds: float,
        mock: bool,
    ) -> None:
        self._ctx = mp.get_context("spawn")
        self._device_index = device_index
        self._frequency_hz = frequency_hz
        self._operation_timeout_seconds = operation_timeout_seconds
        self._worker_startup_timeout_seconds = worker_startup_timeout_seconds
        self._mock = mock
        self._generation = 0
        self._request_id = 0
        self._process: mp.Process | None = None
        self._conn: Connection | None = None
        self.backend_available = False
        self.last_restart_reason: str | None = None
        self.last_error: str | None = None
        self.last_success_time: str | None = None
        self.consecutive_failures = 0

    def start(self) -> None:
        if self._process is not None and self._process.is_alive():
            return
        parent_conn, child_conn = self._ctx.Pipe()
        self._generation += 1
        self._process = self._ctx.Process(
            target=_worker_main,
            args=(
                child_conn,
                {
                    "generation": self._generation,
                    "device_index": self._device_index,
                    "frequency_hz": self._frequency_hz,
                    "mock": self._mock,
                },
            ),
            name=f"yardstick-worker-{self._generation}",
        )
        self._process.start()
        child_conn.close()
        self._conn = parent_conn
        self.backend_available = False
        _log(
            "PARENT",
            f"worker_start pid={self._process.pid} generation={self._generation} mock={self._mock}",
        )

    def stop(self) -> None:
        if self._process is None:
            return
        try:
            self.request(COMMAND_STOP, timeout=self._operation_timeout_seconds)
        except Exception as exc:
            _log("PARENT", f"worker_stop_soft_failed error={exc}")
        finally:
            self._terminate("explicit_stop")

    def request(
        self, command: str, payload: dict[str, Any] | None = None, *, timeout: float | None = None
    ) -> dict[str, Any]:
        if self._process is None or self._conn is None:
            raise RuntimeError("Worker has not been started.")
        if not self._process.is_alive():
            raise RuntimeError(f"Worker exited unexpectedly exitcode={self._process.exitcode} before {command}.")

        self._request_id += 1
        request = {
            "request_id": self._request_id,
            "command": command,
            "payload": payload or {},
        }
        request_timeout = (
            self._worker_startup_timeout_seconds
            if command == COMMAND_OPEN
            else (timeout if timeout is not None else self._operation_timeout_seconds)
        )
        _log(
            "PARENT",
            f"request_send id={self._request_id} command={command} timeout={request_timeout:.2f}s generation={self._generation}",
        )
        self._conn.send(request)
        if not self._conn.poll(request_timeout):
            self.consecutive_failures += 1
            self.backend_available = False
            self.last_error = f"Timed out waiting for worker response during {command}."
            self.last_restart_reason = f"timeout:{command}"
            self._terminate(self.last_restart_reason)
            raise TimeoutError(self.last_error)
        response = self._conn.recv()
        self.backend_available = bool(response["worker_status"]["backend_available"])
        if response["success"]:
            self.last_success_time = _now()
            self.last_error = None
            self.consecutive_failures = 0
        else:
            self.last_error = (
                f"{response.get('error_class')}: {response.get('error')}"
                if response.get("error_class")
                else response.get("error")
            )
            self.consecutive_failures += 1
        _log(
            "PARENT",
            f"response_recv id={response['request_id']} kind={response['kind']} success={response['success']} timing_ms={response['timing_ms']}",
        )
        return response

    def _terminate(self, reason: str) -> None:
        if self._process is None:
            return
        self.last_restart_reason = reason
        _log(
            "PARENT",
            f"worker_terminate_requested pid={self._process.pid} generation={self._generation} reason={reason}",
        )
        if self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=5)
            if self._process.is_alive():
                _log("PARENT", f"worker_kill_requested pid={self._process.pid}")
                try:
                    os.kill(self._process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self._process.join(timeout=5)
        if self._conn is not None:
            self._conn.close()
        exitcode = self._process.exitcode
        _log(
            "PARENT",
            f"worker_terminated pid={self._process.pid} generation={self._generation} exitcode={exitcode} reason={reason}",
        )
        self._process = None
        self._conn = None
        self.backend_available = False


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prototype a process-isolated Yard Stick One worker with explicit software burst TX.",
    )
    parser.add_argument(
        "--payload",
        required=True,
        help="Manchester air payload hex to transmit.",
    )
    parser.add_argument(
        "--frequency",
        type=int,
        default=PROFLAME2_FREQUENCY_HZ,
        help=f"Transmit frequency in Hz. Defaults to {PROFLAME2_FREQUENCY_HZ}.",
    )
    parser.add_argument(
        "--device-index",
        type=int,
        default=0,
        help="RfCat device index to open.",
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=1,
        help="Number of start/open/send/stop cycles to run.",
    )
    parser.add_argument(
        "--transmissions",
        type=int,
        default=DEFAULT_TRANSMISSIONS,
        help=f"Number of explicit RFxmit(payload) calls per SEND command. Defaults to {DEFAULT_TRANSMISSIONS}.",
    )
    parser.add_argument(
        "--inter-frame-gap-ms",
        type=float,
        default=0.0,
        help="Optional sleep between transmitted frames in milliseconds. Defaults to 0.",
    )
    parser.add_argument(
        "--operation-timeout",
        type=float,
        default=DEFAULT_OPERATION_TIMEOUT_SECONDS,
        help=f"Per-command parent-side timeout in seconds. Defaults to {DEFAULT_OPERATION_TIMEOUT_SECONDS}.",
    )
    parser.add_argument(
        "--worker-startup-timeout",
        type=float,
        default=DEFAULT_WORKER_STARTUP_TIMEOUT_SECONDS,
        help=(
            "Timeout in seconds for the worker OPEN command. " f"Defaults to {DEFAULT_WORKER_STARTUP_TIMEOUT_SECONDS}."
        ),
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use an in-process mock radio instead of real rflib hardware.",
    )
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    bytes.fromhex(args.payload)
    if args.cycles < 1:
        raise SystemExit("--cycles must be at least 1.")
    if args.transmissions < 1:
        raise SystemExit("--transmissions must be at least 1.")
    if args.operation_timeout <= 0:
        raise SystemExit("--operation-timeout must be positive.")
    if args.worker_startup_timeout <= 0:
        raise SystemExit("--worker-startup-timeout must be positive.")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _validate_args(args)

    supervisor = YardStickWorkerSupervisor(
        device_index=args.device_index,
        frequency_hz=args.frequency,
        operation_timeout_seconds=args.operation_timeout,
        worker_startup_timeout_seconds=args.worker_startup_timeout,
        mock=args.mock,
    )

    payload_bytes = bytes.fromhex(args.payload)
    _log(
        "PARENT",
        "probe_start "
        f"cycles={args.cycles} frequency_hz={args.frequency} payload_length={len(payload_bytes)} "
        f"transmissions={args.transmissions} inter_frame_gap_ms={args.inter_frame_gap_ms} mock={args.mock}",
    )

    for cycle in range(1, args.cycles + 1):
        _log("PARENT", f"cycle_start index={cycle}/{args.cycles}")
        supervisor.start()
        try:
            supervisor.request(COMMAND_OPEN)
            supervisor.request(COMMAND_PING)
            send_response = supervisor.request(
                COMMAND_SEND,
                payload={
                    "payload_hex": args.payload,
                    "transmissions": args.transmissions,
                    "inter_frame_gap_ms": args.inter_frame_gap_ms,
                },
            )
            burst = send_response["payload"]["burst"]
            _log(
                "PARENT",
                "cycle_send_complete "
                f"index={cycle}/{args.cycles} software_transmissions={burst['software_transmissions']} "
                f"frame_timings_ms={burst['frame_timings_ms']}",
            )
        except Exception as exc:
            _log("PARENT", f"cycle_failure index={cycle}/{args.cycles} exception_type={type(exc).__name__} error={exc}")
            supervisor._terminate(f"cycle_failure:{cycle}")
            return 1
        finally:
            supervisor.stop()
            _log("PARENT", f"cycle_stop_complete index={cycle}/{args.cycles}")

    _log(
        "PARENT",
        "probe_complete "
        f"cycles={args.cycles} last_success_time={supervisor.last_success_time} "
        f"consecutive_failures={supervisor.consecutive_failures}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
