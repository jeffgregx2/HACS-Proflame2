"""Process-isolated Yard Stick One / rflib worker support."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import logging
import multiprocessing as mp
from multiprocessing.connection import Connection
import os
import signal
import threading
import time
import traceback
from typing import Any, Callable

from ..packet_debug import (
    get_packet_debug_logger,
)

_LOGGER = logging.getLogger(__name__)

PROFLAME2_DATA_RATE = 2400
PROFLAME2_FREQUENCY_HZ = 314_973_000
PROBE_RX_BANDWIDTH = 325_000
REASON_UNKNOWN = "unknown"

COMMAND_OPEN = "OPEN"
COMMAND_SEND = "SEND"
COMMAND_RECEIVE = "RECEIVE"
COMMAND_SET_IDLE = "SET_IDLE"
COMMAND_PING = "PING"
COMMAND_STATUS = "STATUS"
COMMAND_STOP = "STOP"

KIND_OK = "OK"
KIND_ERROR = "ERROR"
KIND_NO_PACKET = "NO_PACKET"
KIND_STATUS = "STATUS"

DEFAULT_WORKER_COOLDOWN_SECONDS = 5.0
DEFAULT_STOP_TIMEOUT_SECONDS = 2.0


class MockRfCat:
    MOD_ASK_OOK = 0x30

    def __init__(self, idx: int = 0) -> None:
        self.idx = idx

    def setModeIDLE(self) -> None:
        return None

    def setFreq(self, _frequency_hz: int) -> None:
        return None

    def setMdmModulation(self, _modulation: int) -> None:
        return None

    def setMdmDRate(self, _data_rate: int) -> None:
        return None

    def setMdmSyncMode(self, _sync_mode: int) -> None:
        return None

    def setPktPQT(self, _value: int) -> None:
        return None

    def setEnableMdmManchester(self, _enabled: bool) -> None:
        return None

    def setMaxPower(self) -> None:
        return None

    def makePktFLEN(self, _value: int) -> None:
        return None

    def setModeRX(self) -> None:
        return None

    def RFxmit(self, _payload: bytes) -> None:
        time.sleep(0.01)

    def RFrecv(self, timeout: int = 0):  # pragma: no cover - simple mock transport
        time.sleep(min(timeout / 1000.0, 0.01) if timeout else 0.01)
        raise TimeoutError("mock timeout")


@dataclass(slots=True)
class WorkerBackendStatus:
    worker_pid: int
    worker_generation: int
    backend_available: bool
    radio_open: bool
    last_error: str | None = None


@dataclass(slots=True)
class WorkerDiagnostics:
    backend_available: bool = False
    worker_alive: bool = False
    worker_pid: int | None = None
    worker_generation: int = 0
    last_restart_reason: str | None = None
    last_error: str | None = None
    last_success_time: str | None = None
    consecutive_failures: int = 0
    last_operation: str | None = None
    last_operation_duration_ms: float | None = None
    last_shutdown_reason: str | None = None
    final_exit_code: int | None = None
    shutdown_in_progress: bool = False
    outstanding_request_id: int | None = None
    outstanding_command: str | None = None


class YardStickBackendUnavailableError(RuntimeError):
    """Raised when the Yard Stick backend cannot be used."""

    def __init__(self, message: str, *, reason: str = REASON_UNKNOWN) -> None:
        super().__init__(message)
        self.reason = reason


def normalize_yardstick_backend_error(exc: BaseException) -> YardStickBackendUnavailableError:
    if isinstance(exc, YardStickBackendUnavailableError):
        return exc
    if isinstance(exc, ImportError):
        return YardStickBackendUnavailableError(
            "YARD Stick One support is unavailable because the rflib Python package is not installed."
        )
    if isinstance(exc, PermissionError):
        return YardStickBackendUnavailableError(
            "The YARD Stick One could not be opened because access was denied."
        )

    message = str(exc).strip()
    lowered = message.lower()
    if "no backend available" in lowered or "libusb" in lowered:
        return YardStickBackendUnavailableError(
            "YARD Stick One support is unavailable because the libusb backend could not be loaded."
        )
    if (
        "no such device" in lowered
        or "device not found" in lowered
        or "not found" in lowered
        or "could not find device" in lowered
        or "no dongle found" in lowered
    ):
        return YardStickBackendUnavailableError("No YARD Stick One device was found.")
    if (
        "permission denied" in lowered
        or "access denied" in lowered
        or "operation not permitted" in lowered
        or "insufficient permissions" in lowered
        or "resource busy" in lowered
    ):
        return YardStickBackendUnavailableError(
            "The YARD Stick One could not be opened because access was denied."
        )

    return YardStickBackendUnavailableError("YARD Stick One support is unavailable.")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _worker_status(
    *,
    generation: int,
    radio: Any | None,
    last_error: str | None,
) -> WorkerBackendStatus:
    return WorkerBackendStatus(
        worker_pid=os.getpid(),
        worker_generation=generation,
        backend_available=radio is not None,
        radio_open=radio is not None,
        last_error=last_error,
    )


def _response(
    *,
    request_id: int,
    kind: str,
    status: WorkerBackendStatus,
    timing_ms: float,
    payload: dict[str, Any] | None = None,
    error_class: str | None = None,
    error: str | None = None,
    tb: str | None = None,
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "kind": kind,
        "success": kind in {KIND_OK, KIND_STATUS, KIND_NO_PACKET},
        "timing_ms": round(timing_ms, 2),
        "worker_pid": status.worker_pid,
        "worker_generation": status.worker_generation,
        "error_class": error_class,
        "error": error,
        "traceback": tb,
        "status": asdict(status),
        "payload": payload or {},
    }


def _worker_log(level: int, message: str, *args: Any) -> None:
    _LOGGER.log(level, "Yard Stick worker: " + message, *args)


def _cleanup_radio_for_exit(radio: Any) -> None:
    """Best-effort rflib cleanup before worker exit.

    The rflib codebase does not expose a documented close lifecycle. The only
    clearly intentional radio-side cleanup hook is setModeIDLE(), which rflib
    itself uses in its interactive atexit path. We therefore keep cleanup
    conservative:
    - force radio idle
    - prevent further worker-thread activity where possible
    - clear pending resetup triggers
    - release the claimed interface only if a low-level handle exposes it
    """

    _worker_log(logging.INFO, "cleanup start")
    try:
        if hasattr(radio, "setModeIDLE"):
            _worker_log(logging.INFO, "idle start")
            radio.setModeIDLE()
            _worker_log(logging.INFO, "idle success")
    except Exception as exc:
        _worker_log(logging.ERROR, "idle failure exception_type=%s error=%s", type(exc).__name__, exc)

    try:
        thread_go = getattr(radio, "_threadGo", None)
        if thread_go is not None and hasattr(thread_go, "clear"):
            thread_go.clear()
        reset_event = getattr(radio, "reset_event", None)
        if reset_event is not None and hasattr(reset_event, "clear"):
            reset_event.clear()
        xmit_event = getattr(radio, "xmit_event", None)
        if xmit_event is not None and hasattr(xmit_event, "set"):
            xmit_event.set()
    except Exception as exc:
        _worker_log(
            logging.ERROR,
            "thread/reset cleanup failure exception_type=%s error=%s",
            type(exc).__name__,
            exc,
        )

    try:
        usb_handle = getattr(radio, "_do", None)
        if usb_handle is not None:
            release_interface = getattr(usb_handle, "releaseInterface", None)
            if callable(release_interface):
                try:
                    release_interface(0)
                except TypeError:
                    release_interface()
                _worker_log(logging.INFO, "usb release interface success")
    except Exception as exc:
        _worker_log(
            logging.ERROR,
            "usb release interface failure exception_type=%s error=%s",
            type(exc).__name__,
            exc,
        )

    _worker_log(logging.INFO, "cleanup complete")


def _open_radio(device_index: int, *, mock_mode: bool = False) -> tuple[Any, type[Exception], int]:
    if mock_mode:
        return MockRfCat(idx=device_index), TimeoutError, MockRfCat.MOD_ASK_OOK
    from rflib import MOD_ASK_OOK, RfCat
    from rflib import ChipconUsbTimeoutException

    return RfCat(idx=device_index), ChipconUsbTimeoutException, MOD_ASK_OOK


def _configure_tx_radio(
    radio: Any,
    *,
    frequency_hz: int,
    modulation: int,
) -> dict[str, Any]:
    if hasattr(radio, "setModeIDLE"):
        radio.setModeIDLE()
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


def _configure_rx_radio(
    radio: Any,
    *,
    frequency_hz: int,
    data_rate: int,
    packet_length_bytes: int,
    probe_mode: bool,
    modulation: int,
) -> dict[str, Any]:
    if hasattr(radio, "setModeIDLE"):
        radio.setModeIDLE()
    radio.setFreq(frequency_hz)
    radio.setMdmModulation(modulation)
    radio.setMdmDRate(data_rate)
    packet_length_mode = "fixed_length_packet"
    if hasattr(radio, "makePktFLEN"):
        radio.makePktFLEN(packet_length_bytes)
        if probe_mode:
            packet_length_mode = "fixed_length_probe_capture"
    elif probe_mode:
        packet_length_mode = "raw_or_variable_probe"
    if probe_mode and hasattr(radio, "setMdmChanBW"):
        try:
            radio.setMdmChanBW(PROBE_RX_BANDWIDTH)
        except Exception:
            pass
    if hasattr(radio, "setPktPQT"):
        radio.setPktPQT(0)
    if hasattr(radio, "setMdmSyncMode"):
        radio.setMdmSyncMode(0)
    if probe_mode and hasattr(radio, "setEnablePktCRC"):
        try:
            radio.setEnablePktCRC(False)
        except Exception:
            pass
    if probe_mode and hasattr(radio, "setPktAddr"):
        try:
            radio.setPktAddr(0)
        except Exception:
            pass
    if hasattr(radio, "setEnableMdmManchester"):
        radio.setEnableMdmManchester(False)
    entered_rx_mode = False
    if hasattr(radio, "setModeRX"):
        try:
            radio.setModeRX()
            entered_rx_mode = True
        except Exception:
            entered_rx_mode = False
    return {
        "frequency_hz": frequency_hz,
        "data_rate": data_rate,
        "packet_length_bytes": packet_length_bytes,
        "probe_mode": probe_mode,
        "packet_length_mode": packet_length_mode,
        "rx_mode_explicitly_set": entered_rx_mode,
    }


def _receive_once(
    radio: Any,
    timeout_exception: type[Exception] | None,
    timeout: float | None,
) -> bytes | None:
    rf_timeout = 0 if timeout is None else max(1, int(timeout * 1000))
    try:
        payload, _ = radio.RFrecv(timeout=rf_timeout)
    except Exception as exc:
        if timeout_exception is not None and isinstance(exc, timeout_exception):
            return None
        raise
    if isinstance(payload, str):
        return payload.encode("latin1")
    return bytes(payload)


def _send_software_burst(
    radio: Any,
    *,
    air_payload: bytes,
    software_transmissions: int,
    inter_frame_gap_ms: float,
) -> dict[str, Any]:
    frame_timings_ms: list[float] = []
    try:
        _worker_log(
            logging.INFO,
            "send start payload_length_bytes=%s transmission_mode=software_repeat software_transmissions=%s inter_frame_gap_ms=%s",
            len(air_payload),
            software_transmissions,
            inter_frame_gap_ms,
        )
        for frame_index in range(software_transmissions):
            frame_start = time.monotonic()
            _worker_log(
                logging.INFO,
                "RFxmit frame start mode=software_repeat frame_index=%s/%s payload_length_bytes=%s",
                frame_index + 1,
                software_transmissions,
                len(air_payload),
            )
            radio.RFxmit(air_payload)
            frame_elapsed_ms = (time.monotonic() - frame_start) * 1000
            frame_timings_ms.append(round(frame_elapsed_ms, 2))
            _worker_log(
                logging.INFO,
                "RFxmit frame complete mode=software_repeat frame_index=%s/%s payload_length_bytes=%s elapsed_ms=%.2f",
                frame_index + 1,
                software_transmissions,
                len(air_payload),
                frame_elapsed_ms,
            )
            if inter_frame_gap_ms > 0 and frame_index + 1 < software_transmissions:
                time.sleep(inter_frame_gap_ms / 1000.0)
    finally:
        _worker_log(
            logging.INFO,
            "post_tx_idle start payload_length_bytes=%s software_transmissions=%s",
            len(air_payload),
            software_transmissions,
        )
        if hasattr(radio, "setModeIDLE"):
            radio.setModeIDLE()
        _worker_log(
            logging.INFO,
            "post_tx_idle complete payload_length_bytes=%s software_transmissions=%s",
            len(air_payload),
            software_transmissions,
        )
    return {
        "software_transmissions": software_transmissions,
        "inter_frame_gap_ms": inter_frame_gap_ms,
        "frame_timings_ms": frame_timings_ms,
    }


def yardstick_worker_main(conn: Connection, args: dict[str, Any]) -> None:
    generation = int(args["generation"])
    device_index = int(args["device_index"])
    mock_mode = bool(args.get("mock_mode", False))
    radio: Any | None = None
    timeout_exception: type[Exception] | None = None
    modulation: int | None = None
    last_error: str | None = None

    _worker_log(logging.INFO, "started pid=%s generation=%s device_index=%s mock_mode=%s", os.getpid(), generation, device_index, mock_mode)

    while True:
        try:
            request = conn.recv()
        except EOFError:
            _worker_log(logging.INFO, "control pipe closed; exiting")
            break

        request_id = int(request["request_id"])
        command = str(request["command"])
        payload = request.get("payload", {})
        started = time.monotonic()

        try:
            if command in {COMMAND_PING, COMMAND_STATUS}:
                response = _response(
                    request_id=request_id,
                    kind=KIND_STATUS,
                    status=_worker_status(generation=generation, radio=radio, last_error=last_error),
                    timing_ms=(time.monotonic() - started) * 1000,
                    payload={"message": "pong"},
                )
            elif command == COMMAND_OPEN:
                if radio is None:
                    _worker_log(logging.INFO, "open start device_index=%s", device_index)
                    radio, timeout_exception, modulation = _open_radio(device_index, mock_mode=mock_mode)
                    _worker_log(logging.INFO, "open complete device_index=%s", device_index)
                response = _response(
                    request_id=request_id,
                    kind=KIND_OK,
                    status=_worker_status(generation=generation, radio=radio, last_error=None),
                    timing_ms=(time.monotonic() - started) * 1000,
                )
                last_error = None
            elif command == COMMAND_SET_IDLE:
                if radio is None:
                    raise RuntimeError("Worker radio is not open.")
                _worker_log(logging.INFO, "set_idle start")
                if hasattr(radio, "setModeIDLE"):
                    radio.setModeIDLE()
                _worker_log(logging.INFO, "set_idle complete")
                response = _response(
                    request_id=request_id,
                    kind=KIND_OK,
                    status=_worker_status(generation=generation, radio=radio, last_error=None),
                    timing_ms=(time.monotonic() - started) * 1000,
                )
                last_error = None
            elif command == COMMAND_SEND:
                if radio is None or modulation is None:
                    raise RuntimeError("Worker radio is not open.")
                air_payload = bytes.fromhex(str(payload["air_payload_hex"]))
                tx_settings = _configure_tx_radio(
                    radio,
                    frequency_hz=int(payload["tx_frequency_hz"]),
                    modulation=modulation,
                )
                burst_info = _send_software_burst(
                    radio,
                    air_payload=air_payload,
                    software_transmissions=int(payload["software_transmissions"]),
                    inter_frame_gap_ms=float(payload["inter_frame_gap_ms"]),
                )
                response = _response(
                    request_id=request_id,
                    kind=KIND_OK,
                    status=_worker_status(generation=generation, radio=radio, last_error=None),
                    timing_ms=(time.monotonic() - started) * 1000,
                    payload={"tx_settings": tx_settings, "burst": burst_info},
                )
                last_error = None
            elif command == COMMAND_RECEIVE:
                if radio is None or modulation is None:
                    raise RuntimeError("Worker radio is not open.")
                _worker_log(
                    logging.INFO,
                    "receive start timeout=%s frequency_hz=%s packet_length_bytes=%s probe_mode=%s",
                    payload["timeout"],
                    payload["frequency_hz"],
                    payload["packet_length_bytes"],
                    payload["probe_mode"],
                )
                rx_settings = _configure_rx_radio(
                    radio,
                    frequency_hz=int(payload["frequency_hz"]),
                    data_rate=int(payload["data_rate"]),
                    packet_length_bytes=int(payload["packet_length_bytes"]),
                    probe_mode=bool(payload["probe_mode"]),
                    modulation=modulation,
                )
                raw_payload = _receive_once(radio, timeout_exception, payload.get("timeout"))
                if raw_payload is None:
                    response = _response(
                        request_id=request_id,
                        kind=KIND_NO_PACKET,
                        status=_worker_status(generation=generation, radio=radio, last_error=None),
                        timing_ms=(time.monotonic() - started) * 1000,
                        payload={"rx_settings": rx_settings},
                    )
                else:
                    _worker_log(
                        logging.INFO,
                        "receive complete bytes=%s frequency_hz=%s",
                        len(raw_payload),
                        payload["frequency_hz"],
                    )
                    response = _response(
                        request_id=request_id,
                        kind=KIND_OK,
                        status=_worker_status(generation=generation, radio=radio, last_error=None),
                        timing_ms=(time.monotonic() - started) * 1000,
                        payload={"raw_payload_hex": raw_payload.hex(), "rx_settings": rx_settings},
                    )
                last_error = None
            elif command == COMMAND_STOP:
                _worker_log(logging.INFO, "STOP received")
                if radio is not None:
                    try:
                        _cleanup_radio_for_exit(radio)
                    except Exception as exc:
                        _worker_log(
                            logging.ERROR,
                            "cleanup failure during STOP exception_type=%s error=%s\n%s",
                            type(exc).__name__,
                            exc,
                            traceback.format_exc(),
                        )
                response = _response(
                    request_id=request_id,
                    kind=KIND_OK,
                    status=_worker_status(generation=generation, radio=radio, last_error=last_error),
                    timing_ms=(time.monotonic() - started) * 1000,
                )
                conn.send(response)
                _worker_log(logging.INFO, "STOP complete")
                break
            else:
                raise RuntimeError(f"Unsupported worker command: {command}")
        except Exception as exc:
            normalized = normalize_yardstick_backend_error(exc)
            last_error = f"{type(normalized).__name__}: {normalized}"
            _worker_log(
                logging.ERROR,
                "exception command=%s exception_type=%s error=%s\n%s",
                command,
                type(exc).__name__,
                exc,
                traceback.format_exc(),
            )
            response = _response(
                request_id=request_id,
                kind=KIND_ERROR,
                status=_worker_status(generation=generation, radio=radio, last_error=last_error),
                timing_ms=(time.monotonic() - started) * 1000,
                error_class=type(normalized).__name__,
                error=str(normalized),
                tb=traceback.format_exc(),
            )

        conn.send(response)

    conn.close()
    _worker_log(logging.INFO, "exiting")


class YardStickWorkerSupervisor:
    """Manage one process-isolated Yard Stick worker."""

    def __init__(
        self,
        *,
        device_index: int = 0,
        cooldown_seconds: float = DEFAULT_WORKER_COOLDOWN_SECONDS,
        stop_timeout_seconds: float = DEFAULT_STOP_TIMEOUT_SECONDS,
        process_target: Callable[[Connection, dict[str, Any]], None] = yardstick_worker_main,
        mp_context: Any | None = None,
        mock_mode: bool = False,
    ) -> None:
        self._device_index = device_index
        self._cooldown_seconds = cooldown_seconds
        self._stop_timeout_seconds = stop_timeout_seconds
        self._process_target = process_target
        self._ctx = mp_context or mp.get_context("spawn")
        self._mock_mode = mock_mode
        self._process: mp.Process | None = None
        self._conn: Connection | None = None
        self._request_id = 0
        self._next_allowed_start_monotonic = 0.0
        self._packet_logger = get_packet_debug_logger()
        self._diagnostics = WorkerDiagnostics()
        self._lock = threading.RLock()
        self._shutdown_requested = False
        self._shutdown_reason: str | None = None

    @property
    def diagnostics(self) -> WorkerDiagnostics:
        return self._diagnostics

    def request(
        self,
        command: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float,
    ) -> dict[str, Any]:
        with self._lock:
            if self._shutdown_requested and command != COMMAND_STOP:
                raise YardStickBackendUnavailableError(
                    f"Yard Stick unavailable; shutdown in progress ({self._shutdown_reason or 'unknown'}).",
                    reason=REASON_UNKNOWN,
                )
            self._ensure_worker_started()
            assert self._process is not None
            assert self._conn is not None
            self._request_id += 1
            request_id = self._request_id
            self._diagnostics.last_operation = command
            self._diagnostics.outstanding_request_id = request_id
            self._diagnostics.outstanding_command = command
            self._log_debug("supervisor: request sent id=%s command=%s timeout=%.2fs", request_id, command, timeout)
            try:
                self._conn.send({"request_id": request_id, "command": command, "payload": payload or {}})
            except (BrokenPipeError, EOFError, OSError) as exc:
                self._diagnostics.last_error = f"Yard Stick worker exited unexpectedly during {command}: {exc}"
                self._diagnostics.consecutive_failures += 1
                self._diagnostics.backend_available = False
                self._diagnostics.last_restart_reason = f"worker_exit:{command}"
                self._diagnostics.outstanding_request_id = None
                self._diagnostics.outstanding_command = None
                self._terminate_worker(reason=f"worker_exit:{command}")
                raise YardStickBackendUnavailableError(
                    f"Yard Stick worker exited unexpectedly during {command}.",
                    reason=REASON_UNKNOWN,
                ) from exc
        try:
            if not self._conn.poll(timeout):
                self._diagnostics.last_error = f"Yard Stick worker timed out during {command}."
                self._diagnostics.consecutive_failures += 1
                self._diagnostics.backend_available = False
                self._diagnostics.worker_alive = self._process.is_alive()
                self._diagnostics.last_restart_reason = f"timeout:{command}"
                _LOGGER.error(
                    "Yard Stick worker timed out command=%s timeout=%.2fs pid=%s generation=%s",
                    command,
                    timeout,
                    self._process.pid,
                    self._diagnostics.worker_generation,
                )
                self._log_debug(
                    "supervisor: timeout command=%s timeout=%.2fs pid=%s generation=%s",
                    command,
                    timeout,
                    self._process.pid,
                    self._diagnostics.worker_generation,
                )
                self._terminate_worker(reason=f"timeout:{command}")
                raise YardStickBackendUnavailableError(
                    f"Yard Stick worker timed out during {command}.",
                    reason=REASON_UNKNOWN,
                )
            try:
                response = self._conn.recv()
            except (EOFError, OSError) as exc:
                self._diagnostics.last_error = f"Yard Stick worker exited unexpectedly during {command}: {exc}"
                self._diagnostics.consecutive_failures += 1
                self._diagnostics.backend_available = False
                self._diagnostics.last_restart_reason = f"worker_exit:{command}"
                self._terminate_worker(reason=f"worker_exit:{command}")
                raise YardStickBackendUnavailableError(
                    f"Yard Stick worker exited unexpectedly during {command}.",
                    reason=REASON_UNKNOWN,
                ) from exc
            self._update_diagnostics_from_response(command, response)
            self._log_debug(
                "supervisor: response received id=%s command=%s kind=%s success=%s timing_ms=%s pid=%s generation=%s",
                response["request_id"],
                command,
                response["kind"],
                response["success"],
                response["timing_ms"],
                response["worker_pid"],
                response["worker_generation"],
            )
            if not response["success"]:
                error = response.get("error") or "unknown worker failure"
                error_class = response.get("error_class")
                _LOGGER.error(
                    "Yard Stick worker request failed command=%s error_class=%s error=%s",
                    command,
                    error_class,
                    error,
                )
                raise YardStickBackendUnavailableError(error, reason=REASON_UNKNOWN)
            return response
        finally:
            self._diagnostics.outstanding_request_id = None
            self._diagnostics.outstanding_command = None

    def stop(self, *, reason: str = "stop") -> None:
        with self._lock:
            self._shutdown_requested = True
            self._shutdown_reason = reason
            self._diagnostics.shutdown_in_progress = True
            self._diagnostics.last_shutdown_reason = reason
            process = self._process
            conn = self._conn
            outstanding_request_id = self._diagnostics.outstanding_request_id
            outstanding_command = self._diagnostics.outstanding_command
        _LOGGER.info(
            "Yard Stick worker shutdown requested reason=%s pid=%s generation=%s outstanding_request_id=%s outstanding_command=%s",
            reason,
            None if process is None else process.pid,
            self._diagnostics.worker_generation,
            outstanding_request_id,
            outstanding_command,
        )
        self._log_debug(
            "supervisor: shutdown requested reason=%s pid=%s generation=%s outstanding_request_id=%s outstanding_command=%s",
            reason,
            None if process is None else process.pid,
            self._diagnostics.worker_generation,
            outstanding_request_id,
            outstanding_command,
        )
        if process is None:
            self._diagnostics.shutdown_in_progress = False
            return

        stop_timed_out = False
        if conn is not None and process.is_alive() and outstanding_request_id is None:
            try:
                self._request_id += 1
                stop_request_id = self._request_id
                self._diagnostics.outstanding_request_id = stop_request_id
                self._diagnostics.outstanding_command = COMMAND_STOP
                _LOGGER.info(
                    "Yard Stick worker graceful STOP sent reason=%s pid=%s generation=%s request_id=%s",
                    reason,
                    process.pid,
                    self._diagnostics.worker_generation,
                    stop_request_id,
                )
                conn.send({"request_id": stop_request_id, "command": COMMAND_STOP, "payload": {}})
                if conn.poll(self._stop_timeout_seconds):
                    stop_response = conn.recv()
                    _LOGGER.info(
                        "Yard Stick worker STOP response received pid=%s generation=%s request_id=%s kind=%s",
                        process.pid,
                        self._diagnostics.worker_generation,
                        stop_request_id,
                        stop_response.get("kind"),
                    )
                else:
                    stop_timed_out = True
                    _LOGGER.warning(
                        "Yard Stick worker STOP timed out pid=%s generation=%s timeout=%.2fs",
                        process.pid,
                        self._diagnostics.worker_generation,
                        self._stop_timeout_seconds,
                    )
            except Exception as exc:
                _LOGGER.warning("Yard Stick worker STOP failed before terminate: %s", exc)
            finally:
                self._diagnostics.outstanding_request_id = None
                self._diagnostics.outstanding_command = None
        elif outstanding_request_id is not None:
            _LOGGER.warning(
                "Yard Stick worker shutdown skipping graceful STOP because request is in flight pid=%s generation=%s request_id=%s command=%s",
                process.pid,
                self._diagnostics.worker_generation,
                outstanding_request_id,
                outstanding_command,
            )

        self._terminate_worker(reason=reason if not stop_timed_out else f"{reason}:stop_timeout")
        self._diagnostics.shutdown_in_progress = False

    def _ensure_worker_started(self) -> None:
        now = time.monotonic()
        if self._process is not None and self._process.is_alive():
            return
        if self._process is not None and not self._process.is_alive():
            exitcode = self._process.exitcode
            self._diagnostics.last_error = f"Yard Stick worker exited unexpectedly with exitcode {exitcode}."
            self._diagnostics.last_restart_reason = f"worker_exit:{exitcode}"
            _LOGGER.warning(
                "Yard Stick worker exited unexpectedly pid=%s exitcode=%s generation=%s",
                self._process.pid,
                exitcode,
                self._diagnostics.worker_generation,
            )
            self._cleanup_process_handles()
        if now < self._next_allowed_start_monotonic:
            cooldown_remaining = self._next_allowed_start_monotonic - now
            raise YardStickBackendUnavailableError(
                f"Yard Stick unavailable; retry after cooldown ({cooldown_remaining:.1f}s remaining).",
                reason=REASON_UNKNOWN,
            )

        parent_conn, child_conn = self._ctx.Pipe()
        self._diagnostics.worker_generation += 1
        self._process = self._ctx.Process(
            target=self._process_target,
            args=(
                child_conn,
                {
                    "generation": self._diagnostics.worker_generation,
                    "device_index": self._device_index,
                    "mock_mode": self._mock_mode,
                },
            ),
            name=f"yardstick-worker-{self._diagnostics.worker_generation}",
        )
        self._process.start()
        child_conn.close()
        self._conn = parent_conn
        self._diagnostics.worker_pid = self._process.pid
        self._diagnostics.worker_alive = True
        self._diagnostics.shutdown_in_progress = False
        _LOGGER.info(
            "Yard Stick worker started pid=%s generation=%s device_index=%s",
            self._process.pid,
            self._diagnostics.worker_generation,
            self._device_index,
        )
        self._log_debug(
            "supervisor: worker start pid=%s generation=%s device_index=%s",
            self._process.pid,
            self._diagnostics.worker_generation,
            self._device_index,
        )

    def _update_diagnostics_from_response(self, command: str, response: dict[str, Any]) -> None:
        status = response.get("status", {})
        self._diagnostics.worker_pid = response.get("worker_pid")
        self._diagnostics.worker_generation = int(response.get("worker_generation", self._diagnostics.worker_generation))
        self._diagnostics.worker_alive = self._process.is_alive() if self._process is not None else False
        self._diagnostics.backend_available = bool(status.get("backend_available", False))
        self._diagnostics.last_operation_duration_ms = float(response.get("timing_ms", 0.0))
        if response.get("success"):
            self._diagnostics.last_success_time = _now_iso()
            self._diagnostics.last_error = None
            self._diagnostics.consecutive_failures = 0
            if command == COMMAND_OPEN:
                _LOGGER.info(
                    "Yard Stick worker ready/open pid=%s generation=%s",
                    self._diagnostics.worker_pid,
                    self._diagnostics.worker_generation,
                )
        else:
            error = response.get("error") or "unknown worker failure"
            error_class = response.get("error_class")
            self._diagnostics.last_error = (
                f"{error_class}: {error}" if error_class else error
            )
            self._diagnostics.consecutive_failures += 1

    def _terminate_worker(self, *, reason: str) -> None:
        if self._process is None:
            return
        _LOGGER.warning(
            "Yard Stick worker terminate requested pid=%s generation=%s reason=%s",
            self._process.pid,
            self._diagnostics.worker_generation,
            reason,
        )
        self._log_debug(
            "supervisor: worker terminate requested pid=%s generation=%s reason=%s",
            self._process.pid,
            self._diagnostics.worker_generation,
            reason,
        )
        if self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=self._stop_timeout_seconds)
            _LOGGER.warning(
                "Yard Stick worker terminate result pid=%s generation=%s alive=%s",
                self._process.pid,
                self._diagnostics.worker_generation,
                self._process.is_alive(),
            )
            if self._process.is_alive():
                _LOGGER.warning(
                    "Yard Stick worker kill requested pid=%s generation=%s",
                    self._process.pid,
                    self._diagnostics.worker_generation,
                )
                try:
                    os.kill(self._process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self._process.join(timeout=self._stop_timeout_seconds)
                _LOGGER.warning(
                    "Yard Stick worker kill result pid=%s generation=%s alive=%s",
                    self._process.pid,
                    self._diagnostics.worker_generation,
                    self._process.is_alive(),
                )
        exitcode = self._process.exitcode
        _LOGGER.warning(
            "Yard Stick worker terminated pid=%s generation=%s exitcode=%s reason=%s",
            self._process.pid,
            self._diagnostics.worker_generation,
            exitcode,
            reason,
        )
        self._log_debug(
            "supervisor: worker terminated pid=%s generation=%s exitcode=%s reason=%s",
            self._process.pid,
            self._diagnostics.worker_generation,
            exitcode,
            reason,
        )
        self._diagnostics.worker_alive = False
        self._diagnostics.backend_available = False
        self._diagnostics.last_restart_reason = reason
        self._diagnostics.final_exit_code = exitcode
        self._next_allowed_start_monotonic = time.monotonic() + self._cooldown_seconds
        self._cleanup_process_handles()

    def _cleanup_process_handles(self) -> None:
        if self._conn is not None:
            self._conn.close()
        self._conn = None
        self._process = None

    def _log_debug(self, message: str, *args: Any) -> None:
        if not self._packet_logger.isEnabledFor(logging.INFO) or not self._packet_logger.handlers:
            return
        self._packet_logger.info("yardstick_worker: " + message, *args)

    def serialize_diagnostics(self) -> dict[str, Any]:
        return asdict(self._diagnostics)
