"""Yard Stick One receive/learn backend.

This module intentionally separates two radio assumptions:

1. SmartFire transmit reference baseline:

- frequency: ``314_973_000`` Hz
- modulation: ``MOD_ASK_OOK``
- data rate: ``2400``

2. Proven Yard Stick receive / learning acquisition profile:

- frequency: ``315_000_000`` Hz
- payload length: ``255`` bytes
- sweep disabled
- embedded candidate scanner enabled

Reference:
- https://github.com/JoelB/smartfire
- https://github.com/JoelB/smartfire/blob/main/smartfire_controller/fireplace.py

SmartFire is primarily our known-good transmit reference. For receive, we still
layer additional radio settings on top of that baseline so the CC1111 can be
driven through ``RFrecv()`` and hand raw bytes back to the decoder. Those
receive-specific settings are logged separately so bench debugging can clearly
distinguish SmartFire-aligned baseline from our extra receive assumptions.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from functools import partial
from pprint import pformat
from typing import TYPE_CHECKING, Any

from ..packet_debug import (
    get_packet_debug_logger,
    get_packet_decode_failure_logger,
)
from ..protocol.profiles import DEFAULT_PROTOCOL_PROFILE
from .base import BackendCapabilities, CaptureResult, ReceiveStatus, RFBackend, SendResult
from .capture import (
    REASON_BAD_START_END_GUARD,
    REASON_UNKNOWN_DECODE_FAILURE,
    REASON_WORD_COUNT_MISMATCH,
    TOTAL_SYMBOLS,
    CaptureSample,
    DecodeDiagnostics,
    diagnose_air_payload,
    find_proflame_candidates,
)
from .waveform import NATIVE_REPEAT_SEPARATOR_BITS, ProflameTransmissionPlan, build_repeated_air_payload
from .yardstick_worker import (
    COMMAND_OPEN,
    COMMAND_RECEIVE,
    COMMAND_SEND,
    YardStickWorkerSupervisor,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

YARDSTICK_TX_STRATEGY_EMBEDDED_REPEAT_PAYLOAD = "embedded_repeat_payload"
YARDSTICK_TX_STRATEGY_SOFTWARE_REPEAT = "software_repeat"
YARDSTICK_TX_REPEAT_STRATEGIES = (
    YARDSTICK_TX_STRATEGY_EMBEDDED_REPEAT_PAYLOAD,
    YARDSTICK_TX_STRATEGY_SOFTWARE_REPEAT,
)
YARDSTICK_TX_DEFAULT_REPEAT_STRATEGY = YARDSTICK_TX_STRATEGY_EMBEDDED_REPEAT_PAYLOAD

_YARDSTICK_RX_REGISTER_ADDRESSES: dict[str, int] = {
    "IOCFG2": 0x00,
    "IOCFG1": 0x01,
    "IOCFG0": 0x02,
    "FIFOTHR": 0x03,
    "SYNC1": 0x04,
    "SYNC0": 0x05,
    "PKTLEN": 0x06,
    "PKTCTRL1": 0x07,
    "PKTCTRL0": 0x08,
    "ADDR": 0x09,
    "CHANNR": 0x0A,
    "FSCTRL1": 0x0B,
    "FSCTRL0": 0x0C,
    "FREQ2": 0x0D,
    "FREQ1": 0x0E,
    "FREQ0": 0x0F,
    "MDMCFG4": 0x10,
    "MDMCFG3": 0x11,
    "MDMCFG2": 0x12,
    "MDMCFG1": 0x13,
    "MDMCFG0": 0x14,
    "DEVIATN": 0x15,
    "MCSM2": 0x16,
    "MCSM1": 0x17,
    "MCSM0": 0x18,
    "FOCCFG": 0x19,
    "BSCFG": 0x1A,
    "AGCCTRL2": 0x1B,
    "AGCCTRL1": 0x1C,
    "AGCCTRL0": 0x1D,
    "FREND1": 0x21,
    "FREND0": 0x22,
    "FSCAL3": 0x23,
    "FSCAL2": 0x24,
    "FSCAL1": 0x25,
    "FSCAL0": 0x26,
    "TEST2": 0x2C,
    "TEST1": 0x2D,
    "TEST0": 0x2E,
    "PARTNUM": 0x30,
    "VERSION": 0x31,
    "FREQEST": 0x32,
    "LQI": 0x33,
    "RSSI": 0x34,
    "MARCSTATE": 0x35,
    "PKTSTATUS": 0x38,
    "RXBYTES": 0x3B,
}


def _read_yardstick_register(radio: Any, address: int) -> str:
    for method_name in ("peek", "getRFRegister", "readRFRegister"):
        method = getattr(radio, method_name, None)
        if method is None:
            continue
        try:
            return f"0x{int(method(address)) & 0xFF:02X}"
        except Exception as exc:  # pragma: no cover - depends on rflib backend quirks
            return f"unavailable:{method_name}:{type(exc).__name__}"
    return "unavailable:no_register_reader"


def _yardstick_register_snapshot(radio: Any) -> dict[str, str]:
    return {
        name: _read_yardstick_register(radio, address) for name, address in _YARDSTICK_RX_REGISTER_ADDRESSES.items()
    }


PROFLAME2_FREQUENCY_HZ = DEFAULT_PROTOCOL_PROFILE.frequency_hz
PROFLAME2_DATA_RATE = DEFAULT_PROTOCOL_PROFILE.data_rate_bps
PROFLAME2_PACKET_BYTES = 25
DIAGNOSTIC_PACKET_BYTES = 255
YARDSTICK_RX_LEARNING_FREQUENCY_HZ = 315_000_000
YARDSTICK_RX_LEARNING_PACKET_BYTES = DIAGNOSTIC_PACKET_BYTES
YARDSTICK_RX_LEARNING_SWEEP_ENABLED = False
YARDSTICK_OPERATION_LOCK_TIMEOUT_SECONDS = 5.0
YARDSTICK_CONNECT_TIMEOUT_SECONDS = 10.0
YARDSTICK_TRANSMIT_TIMEOUT_SECONDS = 15.0
YARDSTICK_TX_DEFAULT_TRANSMISSIONS = DEFAULT_PROTOCOL_PROFILE.tx_repeat_count
YARDSTICK_TX_DEFAULT_INTER_FRAME_GAP_MS = 0.0
PROBE_RX_BANDWIDTH = 325_000
SMARTFIRE_REFERENCE_URL = "https://github.com/JoelB/smartfire/blob/main/smartfire_controller/fireplace.py"
DEFAULT_RX_SCAN_OFFSETS_HZ = (0, 27_000, -27_000, 54_000, -54_000)
REASON_RFLIB_MISSING = "rflib_missing"
REASON_LIBUSB_UNAVAILABLE = "libusb_unavailable"
REASON_DEVICE_NOT_FOUND = "device_not_found"
REASON_PERMISSION_DENIED = "permission_denied"
REASON_UNKNOWN = "unknown"

ExecutorJobCallable = Callable[..., Awaitable[Any]]
WorkerSupervisorFactory = Callable[..., YardStickWorkerSupervisor]


@dataclass(frozen=True)
class YardStickReceiveDiagnostics:
    """Normalized receive diagnostics for one YardStick receive window."""

    capture_complete: bool
    sample: CaptureSample | None
    raw_payload: bytes | None
    raw_payload_hex: str | None
    payload_length_bytes: int | None
    bit_stream: str | None
    symbol_stream: str | None
    decode_diagnostics: DecodeDiagnostics | None
    decoded_fields: dict[str, Any] | None
    decode_success: bool
    decode_failure_reason: str | None
    best_failure_reason: str | None
    reason_counts: dict[str, int]
    selected_bit_offset: int | None
    selected_symbol_offset: int | None
    repeat_count: int | None
    occurrence_offsets: tuple[tuple[int, int], ...]
    candidate_count: int
    artifact_layer: str
    symbol_stream_layer: str
    bit_stream_layer: str
    packet_normalized: bool | None
    contains_multiple_repeats: bool | None
    contains_partial_window: bool | None
    candidate_search_performed: bool
    candidate_windows_retained: bool
    selected_window_available: bool
    diagnostic_limitations: tuple[str, ...]
    candidate_windows: tuple[dict[str, Any], ...]
    failed_candidate_windows: tuple[dict[str, Any], ...]
    best_candidate_window: dict[str, Any] | None
    selected_candidate_window: dict[str, Any] | None
    diagnostic_candidate_windows: tuple[dict[str, Any], ...]
    diagnostic_candidate_offsets: tuple[int, ...]
    diagnostic_candidate_reason: str | None
    diagnostic_candidate_confidence: str | None
    active_frequency_hz: int | None
    rx_settings: dict[str, Any]
    host_start_ns: int
    host_complete_ns: int
    started_at_utc: str
    completed_at_utc: str
    receive_status: ReceiveStatus | None
    semantic_artifact: dict[str, Any] | None = None
    semantic_comparable: bool = False
    artifact_class: str | None = None
    learning_attempt_count: int | None = None
    failed_attempt_count_before_success: int | None = None
    failed_attempts: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class _ReceiveAttemptContext:
    """Timing and radio context for one YardStick RFrecv attempt."""

    timeout: float | None
    start_wall: datetime
    start_monotonic: float
    start_ns: int


@dataclass(frozen=True)
class _CompletedReceiveAttempt:
    """Completion timestamps for one YardStick RFrecv attempt."""

    end_wall: datetime
    elapsed_ms: float
    complete_ns: int


@dataclass(frozen=True)
class YardStickTransmitRequest:
    """YardStick TX execution contract for one HA-prepared packet."""

    packet: Any
    plan: ProflameTransmissionPlan
    single_frame_air_payload: bytes
    air_payload: bytes
    air_payload_bit_length: int
    logical_repeat_count: int
    repeat_separator_bits: int
    rf_xmit_call_count: int
    software_transmissions: int
    inter_frame_gap_ms: float
    transmission_mode: str = "embedded_repeat_payload"


class YardStickBackendUnavailableError(RuntimeError):
    """Raised when the Yard Stick backend cannot be used on this host."""

    def __init__(self, message: str, *, reason: str = REASON_UNKNOWN) -> None:
        super().__init__(message)
        self.reason = reason


class YardStickDependencyError(YardStickBackendUnavailableError):
    """Backward-compatible dependency error for missing Yard Stick runtime pieces."""


def normalize_yardstick_backend_error(exc: BaseException) -> YardStickBackendUnavailableError:
    """Return a clean Yard Stick backend error for UI and diagnostics use.

    ``rflib`` and its USB stack raise a mix of import, pyusb, and transport
    exceptions depending on the host environment. We normalize those into a
    small stable set of user-facing errors so Home Assistant flows can fail
    cleanly instead of surfacing low-level USB details.
    """

    if isinstance(exc, YardStickBackendUnavailableError):
        return exc
    if isinstance(exc, ImportError):
        return YardStickDependencyError(
            "YARD Stick One support is unavailable because the rflib Python package is not installed.",
            reason=REASON_RFLIB_MISSING,
        )
    if isinstance(exc, PermissionError):
        return YardStickBackendUnavailableError(
            "The YARD Stick One could not be opened because access was denied.",
            reason=REASON_PERMISSION_DENIED,
        )

    message = str(exc).strip()
    lowered = message.lower()
    if "yard stick worker" in lowered or "retry after cooldown" in lowered:
        return YardStickBackendUnavailableError(message, reason=REASON_UNKNOWN)
    if "no backend available" in lowered or "libusb" in lowered:
        return YardStickBackendUnavailableError(
            "YARD Stick One support is unavailable because the libusb backend could not be loaded.",
            reason=REASON_LIBUSB_UNAVAILABLE,
        )
    if (
        "no such device" in lowered
        or "device not found" in lowered
        or "not found" in lowered
        or "could not find device" in lowered
    ):
        return YardStickBackendUnavailableError(
            "No YARD Stick One device was found.",
            reason=REASON_DEVICE_NOT_FOUND,
        )
    if (
        "permission denied" in lowered
        or "access denied" in lowered
        or "operation not permitted" in lowered
        or "insufficient permissions" in lowered
    ):
        return YardStickBackendUnavailableError(
            "The YARD Stick One could not be opened because access was denied.",
            reason=REASON_PERMISSION_DENIED,
        )

    return YardStickBackendUnavailableError(
        "YARD Stick One support is unavailable.",
        reason=REASON_UNKNOWN,
    )


def _open_radio(device_index: int) -> tuple[Any, type[Exception], int]:
    """Open ``rflib.RfCat`` in a blocking context and return radio metadata."""

    from rflib import MOD_ASK_OOK, ChipconUsbTimeoutException, RfCat

    return RfCat(idx=device_index), ChipconUsbTimeoutException, MOD_ASK_OOK


def _load_mod_ask_ook() -> int:
    """Load the ASK/OOK modulation constant in a blocking context."""

    from rflib import MOD_ASK_OOK

    return MOD_ASK_OOK


def _radio_setting(radio: Any, label: str, getter_name: str, *, fallback: Any = "unavailable") -> Any:
    """Read one radio setting if a suitable getter exists."""

    getter = getattr(radio, getter_name, None)
    if callable(getter):
        try:
            return getter()
        except Exception as exc:
            return f"unavailable ({label} getter failed: {type(exc).__name__})"
    return fallback


def _payload_quality_metrics(raw_payload: bytes) -> dict[str, object]:
    """Return compact metrics to distinguish likely noise from signal."""

    unique_values = sorted(set(raw_payload))
    ff_count = raw_payload.count(0xFF)
    zero_count = raw_payload.count(0x00)
    dominant_ratio = max(raw_payload.count(value) for value in unique_values) / len(raw_payload)
    likely_noise = dominant_ratio >= 0.8 or unique_values in ([0xFF], [0x00], [0x00, 0xFF])
    return {
        "unique_values": unique_values,
        "ff_count": ff_count,
        "zero_count": zero_count,
        "dominant_ratio": dominant_ratio,
        "likely_noise": likely_noise,
    }


def _summarize_payload_quality(raw_payload: bytes) -> str:
    """Return a compact quality summary to distinguish likely noise from signal."""

    metrics = _payload_quality_metrics(raw_payload)
    unique_values = metrics["unique_values"]
    return (
        f"unique_bytes={len(unique_values)} values={[f'0x{value:02X}' for value in unique_values[:8]]} "
        f"ff_count={metrics['ff_count']} zero_count={metrics['zero_count']} "
        f"dominant_ratio={metrics['dominant_ratio']:.2f} likely_noise={metrics['likely_noise']}"
    )


def _should_suppress_verbose_failure(raw_payload: bytes, diagnostics) -> bool:
    """Return whether a failed payload looks like low-value RF noise."""

    if diagnostics.candidates:
        return False

    metrics = _payload_quality_metrics(raw_payload)
    if bool(metrics["likely_noise"]):
        return True

    failure = diagnostics.best_failure
    if failure is None:
        return False
    if failure.reason not in {
        REASON_BAD_START_END_GUARD,
        REASON_WORD_COUNT_MISMATCH,
        REASON_UNKNOWN_DECODE_FAILURE,
    }:
        return False

    symbols = diagnostics.symbols or ""
    bit_stream = diagnostics.bit_stream or ""
    if symbols:
        non_idle_symbols = sum(symbol not in {"S", "Z", "?"} for symbol in symbols)
        idle_symbol_ratio = sum(symbol in {"S", "Z", "?"} for symbol in symbols) / len(symbols)
        if idle_symbol_ratio >= 0.97 and non_idle_symbols <= max(12, len(symbols) // 80):
            return True

    if bit_stream:
        ones_count = bit_stream.count("1")
        zeros_count = len(bit_stream) - ones_count
        minority_bits = min(ones_count, zeros_count)
        if minority_bits <= max(12, len(bit_stream) // 100):
            return True

    return False


class YardStickBackend(RFBackend):
    """Backend used for development-time RX and learning."""

    def __init__(
        self,
        *,
        hass: HomeAssistant | None = None,
        executor_job: ExecutorJobCallable | None = None,
        device_index: int = 0,
        frequency_hz: int = PROFLAME2_FREQUENCY_HZ,
        tx_frequency_hz: int = PROFLAME2_FREQUENCY_HZ,
        data_rate: int = PROFLAME2_DATA_RATE,
        radio: Any | None = None,
        packet_length_bytes: int = PROFLAME2_PACKET_BYTES,
        probe_mode: bool = False,
        frequency_scan_hz: tuple[int, ...] | None = None,
        sweep_enabled: bool | None = None,
        tx_transmissions: int | None = None,
        tx_inter_frame_gap_ms: float = YARDSTICK_TX_DEFAULT_INTER_FRAME_GAP_MS,
        tx_repeat_strategy: str = YARDSTICK_TX_DEFAULT_REPEAT_STRATEGY,
        operation_lock_timeout_seconds: float = YARDSTICK_OPERATION_LOCK_TIMEOUT_SECONDS,
        connect_timeout_seconds: float = YARDSTICK_CONNECT_TIMEOUT_SECONDS,
        transmit_timeout_seconds: float = YARDSTICK_TRANSMIT_TIMEOUT_SECONDS,
        worker_supervisor: YardStickWorkerSupervisor | None = None,
        worker_supervisor_factory: WorkerSupervisorFactory | None = None,
        worker_mode: bool | None = None,
    ) -> None:
        self.name = "yardstick"
        self._executor_job = executor_job or (hass.async_add_executor_job if hass is not None else None)
        self._device_index = device_index
        self._frequency_hz = frequency_hz
        self._tx_frequency_hz = tx_frequency_hz
        self._data_rate = data_rate
        self._radio = radio
        self._packet_length_bytes = packet_length_bytes
        self._probe_mode = probe_mode
        self._frequency_scan_hz = self._build_frequency_scan(frequency_hz, frequency_scan_hz)
        self._sweep_enabled = len(self._frequency_scan_hz) > 1 if sweep_enabled is None else sweep_enabled
        self._frequency_index = 0
        self._timeout_exception: type[Exception] | None = None
        self._owns_radio = radio is None
        self._packet_logger = get_packet_debug_logger()
        self._decode_failure_logger = get_packet_decode_failure_logger()
        self._consecutive_timeouts = 0
        self.last_receive_status: ReceiveStatus | None = None
        self._modulation: int | None = None
        self._tx_transmissions = tx_transmissions
        self._tx_inter_frame_gap_ms = tx_inter_frame_gap_ms
        if tx_repeat_strategy not in YARDSTICK_TX_REPEAT_STRATEGIES:
            raise ValueError(
                f"Unsupported Yard Stick TX repeat strategy {tx_repeat_strategy!r}; "
                f"expected one of {YARDSTICK_TX_REPEAT_STRATEGIES!r}."
            )
        self._tx_repeat_strategy = tx_repeat_strategy
        self._operation_lock_timeout_seconds = operation_lock_timeout_seconds
        self._connect_timeout_seconds = connect_timeout_seconds
        self._transmit_timeout_seconds = transmit_timeout_seconds
        self._operation_lock = asyncio.Lock()
        self._worker_supervisor = worker_supervisor
        self._worker_supervisor_factory = worker_supervisor_factory or YardStickWorkerSupervisor
        self._worker_mode = (radio is None) if worker_mode is None else worker_mode
        self._shutdown_requested = False
        self._last_radio_settings: dict[str, Any] | None = None
        self._last_worker_rx_settings: dict[str, Any] | None = None
        self.last_receive_diagnostics: YardStickReceiveDiagnostics | None = None
        if self._worker_mode and self._worker_supervisor is None:
            self._worker_supervisor = self._worker_supervisor_factory(device_index=device_index)
        _LOGGER.info(
            "Proflame2 Yard Stick backend constructed device_index=%s rx_frequency_hz=%s tx_frequency_hz=%s packet_length_bytes=%s sweep_enabled=%s probe_mode=%s tx_mode=%s tx_transmissions=%s tx_inter_frame_gap_ms=%s worker_mode=%s",
            self._device_index,
            self._frequency_hz,
            self._tx_frequency_hz,
            self._packet_length_bytes,
            self._sweep_enabled,
            self._probe_mode,
            self._tx_repeat_strategy,
            self._tx_transmissions or YARDSTICK_TX_DEFAULT_TRANSMISSIONS,
            self._tx_inter_frame_gap_ms,
            self._worker_mode,
        )

    async def connect(self) -> None:
        """Open the Yard Stick and configure it for Proflame2 receive."""

        if self._shutdown_requested:
            raise RuntimeError("Yard Stick unavailable; shutdown in progress.")
        lock_acquired = False
        self._debug(
            "connect: backend connected=%s radio_exists=%s lock_acquire_start timeout=%.2fs",
            self._radio is not None or self._worker_mode,
            self._radio is not None,
            self._operation_lock_timeout_seconds,
        )
        await self._acquire_operation_lock("connect")
        lock_acquired = True
        try:
            self._debug("connect: lock acquired")
            await self._async_connect_locked()
            if self._worker_mode:
                radio_settings = {
                    "smartfire_reference_url": SMARTFIRE_REFERENCE_URL,
                    "smartfire_reference_baseline": {
                        "frequency_hz": PROFLAME2_FREQUENCY_HZ,
                        "modulation_mode": "MOD_ASK_OOK",
                        "data_rate": PROFLAME2_DATA_RATE,
                    },
                    "frequency_hz": self._active_frequency_hz,
                    "configured_frequency_hz": self._frequency_hz,
                    "frequency_scan_hz": self._frequency_scan_hz,
                    "sweep_enabled": self._sweep_enabled,
                    "packet_length_bytes": self._packet_length_bytes,
                    "probe_mode": self._probe_mode,
                    "worker_mode": True,
                }
            else:
                self._debug("connect: rx configure start")
                radio_settings = await self._await_with_timeout(
                    self._async_in_executor(self._configure_radio, self._modulation),
                    timeout=self._connect_timeout_seconds,
                    label="receive_radio_configuration",
                )
            self._last_radio_settings = radio_settings
            _LOGGER.info(
                "Proflame2 Yard Stick open succeeded device_index=%s rx_frequency_hz=%s tx_frequency_hz=%s",
                self._device_index,
                self._active_frequency_hz,
                self._tx_frequency_hz,
            )
            self._debug(
                "Connected Yard Stick One idx=%s rx_freq_hz=%s tx_reference_freq_hz=%s data_rate=%s probe_mode=%s payload_length_bytes=%s sweep_enabled=%s",
                self._device_index,
                self._active_frequency_hz,
                self._tx_frequency_hz,
                self._data_rate,
                self._probe_mode,
                self._packet_length_bytes,
                self._sweep_enabled,
            )
            self._debug("Radio settings:\n%s", pformat(radio_settings, sort_dicts=True))
        except Exception as exc:
            self._debug(
                "Yard Stick connection failed idx=%s error=%s",
                self._device_index,
                exc,
            )
            raise normalize_yardstick_backend_error(exc) from exc
        finally:
            if lock_acquired:
                self._release_operation_lock("connect")

    async def _async_connect_locked(self) -> None:
        """Ensure a radio is open and modulation is loaded while the lock is held."""

        _LOGGER.info(
            "Proflame2 Yard Stick open attempted device_index=%s rx_frequency_hz=%s tx_frequency_hz=%s",
            self._device_index,
            self._frequency_hz,
            self._tx_frequency_hz,
        )
        if self._worker_mode:
            self._debug("connect: open required worker=yes")
            response = await self._await_with_timeout(
                self._async_worker_request(COMMAND_OPEN, {}, timeout=self._connect_timeout_seconds),
                timeout=self._connect_timeout_seconds,
                label="yardstick_worker_open",
            )
            self._debug(
                "connect: worker open complete pid=%s generation=%s",
                response["worker_pid"],
                response["worker_generation"],
            )
            return
        if self._radio is None:
            self._debug("connect: open required yes")
            self._debug("connect: RfCat open start")
            self._radio, self._timeout_exception, modulation = await self._await_with_timeout(
                self._async_in_executor(_open_radio, self._device_index),
                timeout=self._connect_timeout_seconds,
                label="rflib_open_radio",
            )
            self._debug("connect: RfCat open complete")
        else:
            self._debug("connect: open required no")
            modulation = getattr(self._radio, "MOD_ASK_OOK", None)
            if modulation is None:
                self._debug("connect: modulation load start")
                try:
                    modulation = await self._await_with_timeout(
                        self._async_in_executor(_load_mod_ask_ook),
                        timeout=self._connect_timeout_seconds,
                        label="load_modulation_constant",
                    )
                except Exception:
                    modulation = 0x30
                self._debug("connect: modulation load complete value=%s", modulation)
        self._modulation = modulation

    async def close(self, *, reason: str | None = None) -> None:
        """Close the backend connection."""

        self._shutdown_requested = True
        if self._radio is None:
            if not self._worker_mode:
                _LOGGER.info("Proflame2 Yard Stick close requested but no radio is open.")
                return None
        if self._worker_mode:
            _LOGGER.info(
                "Proflame2 Yard Stick close requested device_index=%s reason=%s", self._device_index, reason or "close"
            )
            lock_acquired = False
            await self._acquire_operation_lock("close")
            lock_acquired = True
            try:
                if self._worker_supervisor is not None:
                    await self._async_in_executor(partial(self._worker_supervisor.stop, reason=reason or "close"))
                    self._debug("Close skipped/no-op idx=%s reason=worker_stop", self._device_index)
            finally:
                if lock_acquired:
                    self._release_operation_lock("close")
                _LOGGER.info(
                    "Proflame2 Yard Stick close completed device_index=%s reason=%s",
                    self._device_index,
                    reason or "close",
                )
            return None
        _LOGGER.info(
            "Proflame2 Yard Stick close requested device_index=%s reason=%s", self._device_index, reason or "close"
        )
        self._debug("Closing Yard Stick One idx=%s", self._device_index)
        lock_acquired = False
        await self._acquire_operation_lock("close")
        lock_acquired = True
        try:
            if hasattr(self._radio, "setModeIDLE"):
                try:
                    await self._async_in_executor(self._radio.setModeIDLE)
                except Exception as exc:
                    _LOGGER.warning(
                        "Proflame2 Yard Stick close failed during setModeIDLE device_index=%s exception_type=%s error=%s",
                        self._device_index,
                        type(exc).__name__,
                        exc,
                    )
                    self._debug("setModeIDLE failed during close exception_type=%s error=%s", type(exc).__name__, exc)
            if hasattr(self._radio, "close"):
                _LOGGER.info(
                    "Proflame2 Yard Stick close skipped device_index=%s reason=explicit_rflib_close_avoided",
                    self._device_index,
                )
                self._debug(
                    "Close skipped/no-op idx=%s reason=explicit_rflib_close_avoided",
                    self._device_index,
                )
            else:
                _LOGGER.info(
                    "Proflame2 Yard Stick close skipped device_index=%s reason=no_explicit_close_available",
                    self._device_index,
                )
                self._debug(
                    "Close skipped/no-op idx=%s reason=no_explicit_close_available",
                    self._device_index,
                )
        finally:
            if self._owns_radio:
                self._radio = None
            if lock_acquired:
                self._release_operation_lock("close")
            _LOGGER.info(
                "Proflame2 Yard Stick close completed device_index=%s reason=%s", self._device_index, reason or "close"
            )

    async def send(self, packet) -> SendResult:
        """Transmit one prepared Proflame2 packet via Yard Stick One."""

        if self._shutdown_requested:
            raise RuntimeError("Yard Stick unavailable; shutdown in progress.")
        self._debug(
            "Entered YardStickBackend.send remote_id=%06x tx_frequency_hz=%s packet_has_plan=%s",
            packet.remote_id,
            self._tx_frequency_hz,
            packet.transmission_plan is not None,
        )
        lock_acquired = False
        if packet.transmission_plan is None:
            raise RuntimeError("Yard Stick transmit requires packet.transmission_plan to be present.")
        transmit_request = self._prepare_yardstick_transmit_request(packet)
        try:
            self._debug(
                "send: lock acquire start timeout=%.2fs radio_exists=%s",
                self._operation_lock_timeout_seconds,
                self._radio is not None or self._worker_mode,
            )
            await self._acquire_operation_lock("send")
            lock_acquired = True
            self._debug("send: lock acquired")
            self._debug("send: connect required=%s", self._radio is None or self._worker_mode)
            await self._async_connect_locked()
            if not self._worker_mode:
                assert self._radio is not None

            tx_settings = await self._execute_yardstick_transmit_request(transmit_request)
            _LOGGER.info(
                "Proflame2 Yard Stick TX remote_id=%06x tx_frequency_hz=%s modulation=%s data_rate=%s payload_length_bytes=%s payload_bit_length=%s transmission_mode=%s logical_repeat_count=%s repeat_separator_bits=%s rf_xmit_call_count=%s",
                packet.remote_id,
                tx_settings["frequency_hz"],
                tx_settings["modulation_mode"],
                tx_settings["data_rate"],
                len(transmit_request.air_payload),
                transmit_request.air_payload_bit_length,
                transmit_request.transmission_mode,
                transmit_request.logical_repeat_count,
                transmit_request.repeat_separator_bits,
                transmit_request.rf_xmit_call_count,
            )
            self._debug(
                "TX start remote_id=%06x tx_frequency_hz=%s tx_reference_freq_hz=%s modulation=%s data_rate=%s payload_length_bytes=%s payload_bit_length=%s single_frame_payload_length_bytes=%s transmission_mode=%s logical_repeat_count=%s repeat_separator_bits=%s rf_xmit_call_count=%s "
                "cmd1=0x%02X err1=0x%02X cmd2=0x%02X err2=0x%02X air_payload=%s",
                packet.remote_id,
                tx_settings["frequency_hz"],
                self._tx_frequency_hz,
                tx_settings["modulation_mode"],
                tx_settings["data_rate"],
                len(transmit_request.air_payload),
                transmit_request.air_payload_bit_length,
                len(transmit_request.single_frame_air_payload),
                transmit_request.transmission_mode,
                transmit_request.logical_repeat_count,
                transmit_request.repeat_separator_bits,
                transmit_request.rf_xmit_call_count,
                packet.frame.cmd1,
                packet.frame.err1,
                packet.frame.cmd2,
                packet.frame.err2,
                transmit_request.air_payload.hex(),
            )
        except Exception as exc:
            _LOGGER.exception(
                "Proflame2 Yard Stick TX failed remote_id=%06x payload_length_bytes=%s transmission_mode=%s logical_repeat_count=%s rf_xmit_call_count=%s",
                packet.remote_id,
                len(transmit_request.air_payload),
                transmit_request.transmission_mode,
                transmit_request.logical_repeat_count,
                transmit_request.rf_xmit_call_count,
            )
            self._debug(
                "TX failure remote_id=%06x payload_length_bytes=%s transmission_mode=%s logical_repeat_count=%s rf_xmit_call_count=%s exception_type=%s error=%s",
                packet.remote_id,
                len(transmit_request.air_payload),
                transmit_request.transmission_mode,
                transmit_request.logical_repeat_count,
                transmit_request.rf_xmit_call_count,
                type(exc).__name__,
                exc,
            )
            raise RuntimeError(str(exc)) from exc
        finally:
            if lock_acquired:
                self._release_operation_lock("send")

        self._debug(
            "TX success remote_id=%06x payload_length_bytes=%s transmission_mode=%s logical_repeat_count=%s rf_xmit_call_count=%s",
            packet.remote_id,
            len(transmit_request.air_payload),
            transmit_request.transmission_mode,
            transmit_request.logical_repeat_count,
            transmit_request.rf_xmit_call_count,
        )
        self._debug("send: returning success")
        return self._build_yardstick_send_result(transmit_request)

    def _prepare_yardstick_transmit_request(self, packet: Any) -> YardStickTransmitRequest:
        """Return the explicit YardStick transmit request for one packet."""

        plan = packet.transmission_plan
        if plan is None:
            raise RuntimeError("Yard Stick transmit requires packet.transmission_plan to be present.")
        logical_repeat_count = self._tx_transmissions or plan.repeat_count
        separator_bits = (
            self._yardstick_repeat_separator_bits()
            if self._tx_repeat_strategy == YARDSTICK_TX_STRATEGY_EMBEDDED_REPEAT_PAYLOAD
            else 0
        )
        air_payload, bit_length, rf_xmit_call_count, software_transmissions = self._build_yardstick_air_payload(
            plan,
            logical_repeat_count=logical_repeat_count,
            separator_bits=separator_bits,
        )
        return YardStickTransmitRequest(
            packet=packet,
            plan=plan,
            single_frame_air_payload=plan.air_payload,
            air_payload=air_payload,
            air_payload_bit_length=bit_length,
            logical_repeat_count=logical_repeat_count,
            repeat_separator_bits=separator_bits,
            rf_xmit_call_count=rf_xmit_call_count,
            software_transmissions=software_transmissions,
            inter_frame_gap_ms=self._tx_inter_frame_gap_ms,
            transmission_mode=self._tx_repeat_strategy,
        )

    def _build_yardstick_air_payload(
        self,
        plan: ProflameTransmissionPlan,
        *,
        logical_repeat_count: int,
        separator_bits: int,
    ) -> tuple[bytes, int, int, int]:
        """Map logical Proflame2 repeats to a Yard Stick/rfcat TX strategy."""

        if self._tx_repeat_strategy == YARDSTICK_TX_STRATEGY_SOFTWARE_REPEAT:
            return (
                plan.air_payload,
                plan.air_payload_bit_length,
                logical_repeat_count,
                logical_repeat_count,
            )

        repeated_payload, repeated_bit_length = build_repeated_air_payload(
            plan,
            repeat_count=logical_repeat_count,
            separator_bits=separator_bits,
        )
        return repeated_payload, repeated_bit_length, 1, 1

    def _yardstick_repeat_separator_bits(self) -> int:
        """Return low separator bits inserted between YardStick logical repeats."""

        if self._tx_inter_frame_gap_ms <= 0:
            return NATIVE_REPEAT_SEPARATOR_BITS
        total_low_bits = round((self._tx_inter_frame_gap_ms / 1000.0) * PROFLAME2_DATA_RATE)
        return max(0, total_low_bits - 1)

    async def _execute_yardstick_transmit_request(self, request: YardStickTransmitRequest) -> dict[str, Any]:
        """Execute one prepared YardStick TX request through worker or rfcat."""

        if self._worker_mode:
            self._debug("send: blocking TX executor submit")
            response = await self._await_with_timeout(
                self._async_worker_request(
                    COMMAND_SEND,
                    {
                        "air_payload_hex": request.air_payload.hex(),
                        "air_payload_bit_length": request.air_payload_bit_length,
                        "tx_frequency_hz": self._tx_frequency_hz,
                        "software_transmissions": request.software_transmissions,
                        "inter_frame_gap_ms": request.inter_frame_gap_ms,
                        "logical_repeat_count": request.logical_repeat_count,
                        "repeat_separator_bits": request.repeat_separator_bits,
                        "rf_xmit_call_count": request.rf_xmit_call_count,
                        "transmission_mode": request.transmission_mode,
                    },
                    timeout=self._transmit_timeout_seconds,
                ),
                timeout=self._transmit_timeout_seconds,
                label="yardstick_worker_send",
            )
            return response.get("payload", {}).get(
                "tx_settings",
                {
                    "frequency_hz": self._tx_frequency_hz,
                    "modulation_mode": 0x30,
                    "data_rate": PROFLAME2_DATA_RATE,
                },
            )

        modulation = await self._yardstick_tx_modulation()
        self._debug("send: TX configure start")
        tx_settings = await self._await_with_timeout(
            self._async_in_executor(self._configure_transmit_radio, modulation),
            timeout=self._connect_timeout_seconds,
            label="transmit_radio_configuration",
        )
        self._debug("send: TX configure complete")
        self._debug("send: blocking TX executor submit")
        await self._await_with_timeout(
            self._async_in_executor(
                self._transmit_air_payload,
                request.air_payload,
                request.logical_repeat_count,
                request.repeat_separator_bits,
                request.rf_xmit_call_count,
                request.inter_frame_gap_ms,
                request.transmission_mode,
            ),
            timeout=self._transmit_timeout_seconds,
            label="blocking_transmit_executor",
        )
        return tx_settings

    async def _yardstick_tx_modulation(self) -> int:
        """Return the ASK/OOK modulation constant for rfcat direct TX."""

        modulation = self._modulation
        if modulation is not None:
            return modulation
        modulation = getattr(self._radio, "MOD_ASK_OOK", None)
        if modulation is None:
            try:
                modulation = await self._await_with_timeout(
                    self._async_in_executor(_load_mod_ask_ook),
                    timeout=self._connect_timeout_seconds,
                    label="load_modulation_constant_for_send",
                )
            except Exception:
                modulation = 0x30
        self._modulation = modulation
        return modulation

    def _build_yardstick_send_result(self, request: YardStickTransmitRequest) -> SendResult:
        """Build the public send result for a completed YardStick TX request."""

        return SendResult(
            packet=request.packet,
            backend_name=self.name,
            warnings=request.packet.warnings,
        )

    async def receive_raw_payload(self, timeout: float | None = None) -> bytes | None:
        """Receive one raw RF payload without attempting Proflame2 decode."""

        if self._shutdown_requested:
            raise RuntimeError("Yard Stick unavailable; shutdown in progress.")
        if not self._worker_mode and self._radio is None:
            raise RuntimeError("YardStickBackend.connect() must be called before receive().")
        if self._worker_mode:
            lock_acquired = False
            try:
                await self._acquire_operation_lock("receive")
                lock_acquired = True
                await self._async_connect_locked()
                request_timeout = self._connect_timeout_seconds + (timeout or 0.0) + 1.0
                response = await self._await_with_timeout(
                    self._async_worker_request(
                        COMMAND_RECEIVE,
                        {
                            "timeout": timeout,
                            "frequency_hz": self._active_frequency_hz,
                            "data_rate": self._data_rate,
                            "packet_length_bytes": self._packet_length_bytes,
                            "probe_mode": self._probe_mode,
                        },
                        timeout=request_timeout,
                    ),
                    timeout=request_timeout,
                    label="yardstick_worker_receive",
                )
                payload = response.get("payload", {})
                rx_settings = payload.get("rx_settings")
                if isinstance(rx_settings, dict):
                    self._last_worker_rx_settings = rx_settings
                if response["kind"] == "NO_PACKET":
                    return None
                raw_payload_hex = payload.get("raw_payload_hex")
                return None if raw_payload_hex is None else bytes.fromhex(raw_payload_hex)
            finally:
                if lock_acquired:
                    self._release_operation_lock("receive")
        return await self._async_in_executor(self._recv_once, timeout)

    async def receive(self, timeout: float | None = None):
        """Receive and decode one Proflame2 frame when possible."""

        sample = await self.receive_sample(timeout=timeout)
        return (
            None
            if sample is None
            else sample.as_packet(
                source="yardstick",
                received_at=datetime.now(timezone.utc),
            )
        )

    async def receive_sample(self, timeout: float | None = None) -> CaptureSample | None:
        """Receive and decode one Proflame2 sample when possible."""

        diagnostics = await self.receive_diagnostics(timeout=timeout)
        return diagnostics.sample

    async def receive_learning_diagnostics(
        self,
        timeout: float | None = None,
        *,
        attempt_timeout: float = 1.0,
    ) -> YardStickReceiveDiagnostics:
        """Retry learning-equivalent receive attempts until a semantic packet is decoded.

        This diagnostic path mirrors guided learning's prompt-level semantics:
        individual RFrecv/decode failures are retained only as debug attempts,
        while the first successful selected ``DecodeCandidate`` is promoted to
        the canonical semantic artifact.
        """

        deadline = None if timeout is None else time.monotonic() + timeout
        capture_started_at = datetime.now(timezone.utc)
        failed_attempts: list[dict[str, Any]] = []
        attempt_count = 0
        last_result: YardStickReceiveDiagnostics | None = None

        while deadline is None or time.monotonic() < deadline:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            if remaining is not None and remaining <= 0:
                break
            receive_timeout = attempt_timeout if remaining is None else min(attempt_timeout, remaining)
            attempt_count += 1
            result = await self.receive_diagnostics(timeout=receive_timeout)
            last_result = result
            if result.decode_success and result.sample is not None and result.selected_candidate_window is not None:
                semantic_artifact = self._build_learning_semantic_artifact(
                    result,
                    rfrecv_attempt_index=attempt_count,
                    learning_attempt_count=attempt_count,
                    failed_attempt_count_before_success=len(failed_attempts),
                    capture_started_at_utc=capture_started_at.isoformat(),
                    capture_completed_at_utc=result.completed_at_utc,
                )
                return replace(
                    result,
                    semantic_artifact=semantic_artifact,
                    semantic_comparable=True,
                    artifact_class="semantic",
                    learning_attempt_count=attempt_count,
                    failed_attempt_count_before_success=len(failed_attempts),
                    failed_attempts=tuple(failed_attempts),
                )
            failed_attempts.append(self._summarize_learning_failed_attempt(result, attempt_index=attempt_count))

        if last_result is None:
            last_result = await self.receive_diagnostics(timeout=0.0)
            attempt_count = 1
            failed_attempts.append(self._summarize_learning_failed_attempt(last_result, attempt_index=attempt_count))

        return replace(
            last_result,
            semantic_artifact=None,
            semantic_comparable=False,
            artifact_class="debug_failure",
            learning_attempt_count=attempt_count,
            failed_attempt_count_before_success=len(failed_attempts),
            failed_attempts=tuple(failed_attempts),
            candidate_windows=(),
            failed_candidate_windows=(),
            best_candidate_window=None,
            selected_candidate_window=None,
            diagnostic_candidate_windows=(),
            diagnostic_candidate_offsets=(),
            diagnostic_candidate_reason=None,
            diagnostic_candidate_confidence=None,
            candidate_windows_retained=False,
            selected_window_available=False,
        )

    async def receive_diagnostics(self, timeout: float | None = None) -> YardStickReceiveDiagnostics:
        """Receive one RF payload and return normalized diagnostics."""

        if not self._worker_mode and self._radio is None:
            raise RuntimeError("YardStickBackend.connect() must be called before receive().")

        attempt = self._start_receive_attempt(timeout)
        try:
            raw_payload = await self.receive_raw_payload(timeout)
        except Exception as exc:
            self._handle_rfrecv_exception(attempt, exc)
            raise
        try:
            completed = self._complete_receive_attempt(attempt, raw_payload=raw_payload)
            if raw_payload is None:
                return await self._handle_no_payload_receive_attempt(attempt, completed)

            self._consecutive_timeouts = 0
            diagnostics = diagnose_air_payload(raw_payload)
            candidates = list(diagnostics.candidates)
            if not candidates:
                return await self._handle_payload_without_candidates(attempt, completed, raw_payload, diagnostics)

            return self._handle_decoded_payload(attempt, completed, raw_payload, diagnostics, candidates)
        except Exception as exc:
            self.last_receive_status = ReceiveStatus(
                outcome="exception",
                reason=type(exc).__name__,
                active_frequency_hz=self._active_frequency_hz,
                payload_length_bytes=None if raw_payload is None else len(raw_payload),
                sweep_enabled=self._sweep_enabled,
                exception_type=type(exc).__name__,
                exception_message=str(exc),
            )
            _LOGGER.exception(
                "Proflame2 Yard Stick post-receive processing failed active_freq_hz=%s payload_length_bytes=%s sweep_enabled=%s timeout=%s",
                self._active_frequency_hz,
                None if raw_payload is None else len(raw_payload),
                self._sweep_enabled,
                timeout,
            )
            self._debug_decode_failure_exception(
                "Post-receive processing exception active_freq_hz=%s payload_length_bytes=%s sweep_enabled=%s timeout=%s exception_type=%s error=%s",
                self._active_frequency_hz,
                None if raw_payload is None else len(raw_payload),
                self._sweep_enabled,
                timeout,
                type(exc).__name__,
                exc,
            )
            raise

    def _start_receive_attempt(self, timeout: float | None) -> _ReceiveAttemptContext:
        """Start one RFrecv diagnostics attempt and reset observable state."""

        attempt = _ReceiveAttemptContext(
            timeout=timeout,
            start_wall=datetime.now(timezone.utc),
            start_monotonic=time.monotonic(),
            start_ns=time.monotonic_ns(),
        )
        self.last_receive_status = None
        self.last_receive_diagnostics = None
        self._debug_decode_failure(
            "RFrecv start timeout=%s active_freq_hz=%s payload_length_bytes=%s sweep_enabled=%s scan_index=%s/%s start=%s",
            timeout,
            self._active_frequency_hz,
            self._packet_length_bytes,
            self._sweep_enabled,
            self._frequency_index + 1,
            len(self._frequency_scan_hz),
            attempt.start_wall.isoformat(),
        )
        return attempt

    def _complete_receive_attempt(
        self,
        attempt: _ReceiveAttemptContext,
        *,
        raw_payload: bytes | None,
    ) -> _CompletedReceiveAttempt:
        """Finish one RFrecv attempt and emit the common receive-end breadcrumb."""

        completed = _CompletedReceiveAttempt(
            end_wall=datetime.now(timezone.utc),
            elapsed_ms=(time.monotonic() - attempt.start_monotonic) * 1000,
            complete_ns=time.monotonic_ns(),
        )
        self._debug_decode_failure(
            "RFrecv end timeout=%s active_freq_hz=%s payload_length_bytes=%s sweep_enabled=%s start=%s end=%s elapsed_ms=%.2f received_payload=%s",
            attempt.timeout,
            self._active_frequency_hz,
            self._packet_length_bytes,
            self._sweep_enabled,
            attempt.start_wall.isoformat(),
            completed.end_wall.isoformat(),
            completed.elapsed_ms,
            raw_payload is not None,
        )
        return completed

    def _handle_rfrecv_exception(self, attempt: _ReceiveAttemptContext, exc: Exception) -> None:
        """Record and log an exception raised by the raw RFrecv operation."""

        completed = _CompletedReceiveAttempt(
            end_wall=datetime.now(timezone.utc),
            elapsed_ms=(time.monotonic() - attempt.start_monotonic) * 1000,
            complete_ns=time.monotonic_ns(),
        )
        self.last_receive_status = ReceiveStatus(
            outcome="exception",
            reason=type(exc).__name__,
            active_frequency_hz=self._active_frequency_hz,
            payload_length_bytes=self._packet_length_bytes,
            sweep_enabled=self._sweep_enabled,
            exception_type=type(exc).__name__,
            exception_message=str(exc),
        )
        _LOGGER.exception(
            "Proflame2 Yard Stick RFrecv failed active_freq_hz=%s payload_length_bytes=%s sweep_enabled=%s timeout=%s",
            self._active_frequency_hz,
            self._packet_length_bytes,
            self._sweep_enabled,
            attempt.timeout,
        )
        self._debug_decode_failure_exception(
            "RFrecv exception timeout=%s active_freq_hz=%s payload_length_bytes=%s sweep_enabled=%s start=%s end=%s elapsed_ms=%.2f exception_type=%s error=%s",
            attempt.timeout,
            self._active_frequency_hz,
            self._packet_length_bytes,
            self._sweep_enabled,
            attempt.start_wall.isoformat(),
            completed.end_wall.isoformat(),
            completed.elapsed_ms,
            type(exc).__name__,
            exc,
        )
        self._debug_decode_failure(
            "RFrecv end timeout=%s active_freq_hz=%s payload_length_bytes=%s sweep_enabled=%s start=%s end=%s elapsed_ms=%.2f received_payload=%s exception_type=%s error=%s",
            attempt.timeout,
            self._active_frequency_hz,
            self._packet_length_bytes,
            self._sweep_enabled,
            attempt.start_wall.isoformat(),
            completed.end_wall.isoformat(),
            completed.elapsed_ms,
            False,
            type(exc).__name__,
            exc,
        )
        self._debug(
            "RF receive failed start=%s end=%s elapsed_ms=%.2f exception_type=%s error=%s",
            attempt.start_wall.isoformat(),
            completed.end_wall.isoformat(),
            completed.elapsed_ms,
            type(exc).__name__,
            exc,
        )

    async def _handle_no_payload_receive_attempt(
        self,
        attempt: _ReceiveAttemptContext,
        completed: _CompletedReceiveAttempt,
    ) -> YardStickReceiveDiagnostics:
        """Build no-payload diagnostics and advance the scan window."""

        self.last_receive_status = ReceiveStatus(
            outcome="no_payload",
            reason="timeout_or_no_rf_payload",
            active_frequency_hz=self._active_frequency_hz,
            payload_length_bytes=self._packet_length_bytes,
            sweep_enabled=self._sweep_enabled,
            candidate_count=0,
        )
        self._consecutive_timeouts += 1
        if self._consecutive_timeouts == 1 or self._consecutive_timeouts % 20 == 0:
            self._debug_decode_failure(
                "No RF payload received consecutive_timeouts=%s timeout=%s active_freq_hz=%s payload_length_bytes=%s sweep_enabled=%s scan_index=%s/%s "
                "start=%s end=%s elapsed_ms=%.2f",
                self._consecutive_timeouts,
                attempt.timeout,
                self._active_frequency_hz,
                self._packet_length_bytes,
                self._sweep_enabled,
                self._frequency_index + 1,
                len(self._frequency_scan_hz),
                attempt.start_wall.isoformat(),
                completed.end_wall.isoformat(),
                completed.elapsed_ms,
            )
        await self._advance_frequency("timeout_without_payload", log_to_decode_failure=False)
        diagnostics_result = self._build_no_payload_receive_diagnostics(attempt, completed)
        self.last_receive_diagnostics = diagnostics_result
        return diagnostics_result

    def _build_no_payload_receive_diagnostics(
        self,
        attempt: _ReceiveAttemptContext,
        completed: _CompletedReceiveAttempt,
    ) -> YardStickReceiveDiagnostics:
        """Build the explicit debug artifact for an RFrecv timeout/no-payload result."""

        return YardStickReceiveDiagnostics(
            capture_complete=False,
            sample=None,
            raw_payload=None,
            raw_payload_hex=None,
            payload_length_bytes=None,
            bit_stream=None,
            symbol_stream=None,
            decode_diagnostics=None,
            decoded_fields=None,
            decode_success=False,
            decode_failure_reason="timeout_or_no_rf_payload",
            best_failure_reason=None,
            reason_counts={},
            selected_bit_offset=None,
            selected_symbol_offset=None,
            repeat_count=None,
            occurrence_offsets=(),
            candidate_count=0,
            artifact_layer="no_rf_payload",
            symbol_stream_layer="not_available",
            bit_stream_layer="not_available",
            packet_normalized=None,
            contains_multiple_repeats=None,
            contains_partial_window=None,
            candidate_search_performed=False,
            candidate_windows_retained=False,
            selected_window_available=False,
            diagnostic_limitations=("No RF payload was received; no bit or symbol artifacts exist.",),
            candidate_windows=(),
            failed_candidate_windows=(),
            best_candidate_window=None,
            selected_candidate_window=None,
            diagnostic_candidate_windows=(),
            diagnostic_candidate_offsets=(),
            diagnostic_candidate_reason=None,
            diagnostic_candidate_confidence=None,
            active_frequency_hz=self._active_frequency_hz,
            rx_settings=self._build_receive_settings_snapshot(),
            host_start_ns=attempt.start_ns,
            host_complete_ns=completed.complete_ns,
            started_at_utc=attempt.start_wall.isoformat(),
            completed_at_utc=completed.end_wall.isoformat(),
            receive_status=self.last_receive_status,
        )

    async def _handle_payload_without_candidates(
        self,
        attempt: _ReceiveAttemptContext,
        completed: _CompletedReceiveAttempt,
        raw_payload: bytes,
        diagnostics: DecodeDiagnostics,
    ) -> YardStickReceiveDiagnostics:
        """Record a decodable RF payload that produced no valid Proflame candidate."""

        self.last_receive_status = ReceiveStatus(
            outcome="payload_no_candidates",
            reason=diagnostics.best_failure.reason if diagnostics.best_failure is not None else "no_valid_candidate",
            active_frequency_hz=self._active_frequency_hz,
            payload_length_bytes=len(raw_payload),
            sweep_enabled=self._sweep_enabled,
            candidate_count=0,
        )
        self._debug_decode_failure(
            "RF receive complete start=%s end=%s elapsed_ms=%.2f timeout=%s received_payload=%s active_freq_hz=%s scan_index=%s/%s",
            attempt.start_wall.isoformat(),
            completed.end_wall.isoformat(),
            completed.elapsed_ms,
            False,
            True,
            self._active_frequency_hz,
            self._frequency_index + 1,
            len(self._frequency_scan_hz),
        )
        self._log_decode_diagnostics(raw_payload, diagnostics)
        await self._advance_frequency("payload_received_but_decode_failed", log_to_decode_failure=True)
        diagnostics_result = self._build_receive_diagnostics_result(
            raw_payload=raw_payload,
            diagnostics=diagnostics,
            sample=None,
            selected_candidate=None,
            decode_success=False,
            start_ns=attempt.start_ns,
            complete_ns=completed.complete_ns,
            start_wall=attempt.start_wall,
            end_wall=completed.end_wall,
        )
        self.last_receive_diagnostics = diagnostics_result
        return diagnostics_result

    def _handle_decoded_payload(
        self,
        attempt: _ReceiveAttemptContext,
        completed: _CompletedReceiveAttempt,
        raw_payload: bytes,
        diagnostics: DecodeDiagnostics,
        candidates: list[Any],
    ) -> YardStickReceiveDiagnostics:
        """Promote the best decoded candidate to the receive diagnostics result."""

        self._debug(
            "RF receive complete start=%s end=%s elapsed_ms=%.2f timeout=%s received_payload=%s active_freq_hz=%s payload_length_bytes=%s sweep_enabled=%s",
            attempt.start_wall.isoformat(),
            completed.end_wall.isoformat(),
            completed.elapsed_ms,
            False,
            True,
            self._active_frequency_hz,
            self._packet_length_bytes,
            self._sweep_enabled,
        )
        self._debug(
            "Received raw RF payload bytes=%s hex=%s",
            len(raw_payload),
            raw_payload.hex(),
        )
        self._debug("RF payload quality %s", _summarize_payload_quality(raw_payload))
        best_candidate = candidates[0]
        self.last_receive_status = ReceiveStatus(
            outcome="decoded_packet",
            reason="candidate_selected",
            active_frequency_hz=self._active_frequency_hz,
            payload_length_bytes=len(raw_payload),
            sweep_enabled=self._sweep_enabled,
            candidate_count=len(candidates),
            selected_bit_offset=best_candidate.bit_offset,
            selected_symbol_offset=best_candidate.symbol_offset,
            repeat_count=best_candidate.repeat_count,
        )
        self._debug(
            "Decoded candidate_count=%s candidate_offsets=%s selected_bit_offset=%s selected_symbol_offset=%s repeat_count=%s confidence=%s trailing_guard_valid=%s trailing_guard_observed=%s trailing_guard_warning=%s validation_notes=%s",
            len(candidates),
            [
                {
                    "bit_offset": candidate.bit_offset,
                    "symbol_offset": candidate.symbol_offset,
                    "repeat_count": candidate.repeat_count,
                }
                for candidate in candidates[:5]
            ],
            best_candidate.bit_offset,
            best_candidate.symbol_offset,
            best_candidate.repeat_count,
            best_candidate.confidence,
            best_candidate.trailing_guard_valid,
            best_candidate.trailing_guard_observed,
            best_candidate.trailing_guard_warning,
            list(best_candidate.validation_notes),
        )
        sample = best_candidate.sample
        self._debug(
            "Decoded packet remote_id=%06x cmd1=0x%02X err1=0x%02X cmd2=0x%02X err2=0x%02X",
            sample.remote_id,
            sample.cmd1,
            sample.err1,
            sample.cmd2,
            sample.err2,
        )
        diagnostics_result = self._build_receive_diagnostics_result(
            raw_payload=raw_payload,
            diagnostics=diagnostics,
            sample=sample,
            selected_candidate=best_candidate,
            decode_success=True,
            start_ns=attempt.start_ns,
            complete_ns=completed.complete_ns,
            start_wall=attempt.start_wall,
            end_wall=completed.end_wall,
        )
        self.last_receive_diagnostics = diagnostics_result
        return diagnostics_result

    async def learn(self, timeout: float | None = None) -> CaptureResult:
        """Collect decodable samples until the timeout expires."""

        if self._shutdown_requested:
            raise RuntimeError("Yard Stick unavailable; shutdown in progress.")
        deadline = None if timeout is None else (time.monotonic() + timeout)
        samples: list[CaptureSample] = []
        raw_payloads = 0
        decode_failures = 0

        while deadline is None or time.monotonic() < deadline:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            raw_payload = await self.receive_raw_payload(remaining)
            if raw_payload is None:
                break
            raw_payloads += 1
            candidates = find_proflame_candidates(raw_payload)
            if not candidates:
                decode_failures += 1
                continue
            samples.extend(candidate.sample for candidate in candidates)

        serial_id = samples[-1].remote_id if samples else 0
        return CaptureResult(
            serial_id=serial_id,
            packets=tuple(
                sample.as_packet(
                    source="yardstick",
                    received_at=datetime.now(timezone.utc),
                )
                for sample in samples
            ),
            samples=tuple(samples),
            metadata={
                "frequency_hz": self._active_frequency_hz,
                "configured_frequency_hz": self._frequency_hz,
                "data_rate": self._data_rate,
                "frequency_scan_hz": self._frequency_scan_hz,
                "raw_payloads_seen": raw_payloads,
                "decode_failures": decode_failures,
            },
        )

    async def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            can_send=True,
            can_receive=True,
            can_learn=True,
            notes=(
                "Transmit and receive operations are isolated behind a dedicated Yard Stick worker process.",
                "Receive path tunes the Yard Stick One for ASK/OOK near 315 MHz.",
                "Successful decodes are normalized into Proflame remote_id/cmd/err samples.",
                "Undecodable payloads are counted in learn-mode metadata for follow-up decoder work.",
            ),
        )

    def _configure_radio(self, modulation: int) -> None:
        """Apply the radio settings used for Proflame2 receive.

        The first three radio settings intentionally mirror SmartFire's known
        Proflame2 transmit baseline exactly:

        1. ``setFreq(314973000)``
        2. ``setMdmModulation(MOD_ASK_OOK)``
        3. ``setMdmDRate(2400)``

        After that baseline, we apply additional receive-oriented settings that
        SmartFire does not use on transmit. Those settings remain explicit so
        that future live-capture testing can validate or revise them without
        confusing the baseline radio assumptions with the receive-only layer.
        """

        assert self._radio is not None
        if hasattr(self._radio, "setModeIDLE"):
            self._radio.setModeIDLE()

        # SmartFire-aligned baseline radio configuration.
        self._radio.setFreq(self._active_frequency_hz)
        self._radio.setMdmModulation(modulation)
        self._radio.setMdmDRate(self._data_rate)

        # Receive-oriented assumptions layered on top of the SmartFire baseline.
        packet_length_mode = "fixed_length_packet"
        if hasattr(self._radio, "makePktFLEN"):
            self._radio.makePktFLEN(self._packet_length_bytes)
            if self._probe_mode:
                packet_length_mode = "fixed_length_probe_capture"
        elif self._probe_mode:
            packet_length_mode = "raw_or_variable_probe"
        if self._probe_mode and hasattr(self._radio, "setMdmChanBW"):
            try:
                self._radio.setMdmChanBW(PROBE_RX_BANDWIDTH)
            except Exception:
                pass
        if hasattr(self._radio, "setPktPQT"):
            self._radio.setPktPQT(0)
        if hasattr(self._radio, "setMdmSyncMode"):
            self._radio.setMdmSyncMode(0)
        if self._probe_mode and hasattr(self._radio, "setEnablePktCRC"):
            try:
                self._radio.setEnablePktCRC(False)
            except Exception:
                pass
        if self._probe_mode and hasattr(self._radio, "setPktAddr"):
            try:
                self._radio.setPktAddr(0)
            except Exception:
                pass
        if hasattr(self._radio, "setEnableMdmManchester"):
            self._radio.setEnableMdmManchester(False)
        entered_rx_mode = False
        if hasattr(self._radio, "setModeRX"):
            try:
                self._radio.setModeRX()
                entered_rx_mode = True
            except Exception:
                entered_rx_mode = False
        return {
            "smartfire_reference_url": SMARTFIRE_REFERENCE_URL,
            "smartfire_reference_baseline": {
                "frequency_hz": PROFLAME2_FREQUENCY_HZ,
                "modulation_mode": "MOD_ASK_OOK",
                "data_rate": PROFLAME2_DATA_RATE,
            },
            "smartfire_reference_match": {
                "frequency_hz": self._frequency_hz == PROFLAME2_FREQUENCY_HZ,
                "data_rate": self._data_rate == PROFLAME2_DATA_RATE,
                "modulation_mode": True,
            },
            "frequency_hz": self._active_frequency_hz,
            "configured_frequency_hz": self._frequency_hz,
            "frequency_scan_hz": self._frequency_scan_hz,
            "sweep_enabled": self._sweep_enabled,
            "modulation_mode": _radio_setting(self._radio, "modulation", "getMdmModulation", fallback=modulation),
            "data_rate": _radio_setting(self._radio, "data_rate", "getMdmDRate", fallback=self._data_rate),
            "rx_channel_bandwidth": _radio_setting(self._radio, "rx_channel_bandwidth", "getMdmChanBW"),
            "deviation": _radio_setting(self._radio, "deviation", "getMdmDeviatn"),
            "mode_selection": "probe_raw_preferred" if self._probe_mode else "packet_receive",
            "sync_mode": _radio_setting(self._radio, "sync_mode", "getMdmSyncMode", fallback=0),
            "preamble_quality_threshold": _radio_setting(
                self._radio,
                "preamble_quality_threshold",
                "getPktPQT",
                fallback=0,
            ),
            "packet_length_mode": packet_length_mode,
            "packet_length_bytes": None if self._probe_mode else self._packet_length_bytes,
            "address_filtering": _radio_setting(self._radio, "address_filtering", "getPktAddr"),
            "agc_gain": _radio_setting(self._radio, "agc_gain", "getAGCState"),
            "raw_registers": _yardstick_register_snapshot(self._radio),
            "rx_mode_explicitly_set": entered_rx_mode,
            "manchester_enabled": _radio_setting(
                self._radio,
                "manchester_enabled",
                "getEnableMdmManchester",
                fallback=False,
            ),
            "receive_only_settings": {
                "probe_mode": self._probe_mode,
                "packet_length_bytes": self._packet_length_bytes,
                "packet_length_mode": packet_length_mode,
                "sync_mode_for_receive": 0,
                "pqt": 0,
                "manchester_disabled_in_hardware": True,
                "explicit_rx_mode_requested": True,
                "probe_rx_bandwidth": PROBE_RX_BANDWIDTH if self._probe_mode else None,
            },
        }

    def _configure_transmit_radio(self, modulation: int) -> dict[str, Any]:
        """Apply the SmartFire-aligned transmit baseline before RFxmit."""

        assert self._radio is not None
        if hasattr(self._radio, "setModeIDLE"):
            self._radio.setModeIDLE()
        self._radio.setFreq(self._tx_frequency_hz)
        self._radio.setMdmModulation(modulation)
        self._radio.setMdmDRate(PROFLAME2_DATA_RATE)
        if hasattr(self._radio, "setMdmSyncMode"):
            self._radio.setMdmSyncMode(0)
        if hasattr(self._radio, "setPktPQT"):
            self._radio.setPktPQT(0)
        if hasattr(self._radio, "setEnableMdmManchester"):
            self._radio.setEnableMdmManchester(False)
        if hasattr(self._radio, "setMaxPower"):
            self._radio.setMaxPower()
        return {
            "frequency_hz": self._tx_frequency_hz,
            "modulation_mode": modulation,
            "data_rate": PROFLAME2_DATA_RATE,
            "sync_mode": 0,
            "packet_quality_threshold": 0,
            "manchester_enabled": False,
            "max_power_requested": hasattr(self._radio, "setMaxPower"),
        }

    async def _async_in_executor(self, func: Callable[..., Any], *args: Any) -> Any:
        """Run one potentially blocking rflib operation off the event loop."""

        if self._executor_job is not None:
            return await self._executor_job(func, *args)
        return await asyncio.to_thread(func, *args)

    async def _await_with_timeout(
        self,
        awaitable: Awaitable[Any],
        *,
        timeout: float,
        label: str,
    ) -> Any:
        """Await one backend operation with a visible timeout."""

        try:
            return await asyncio.wait_for(awaitable, timeout=timeout)
        except asyncio.TimeoutError as exc:
            self._debug("Operation timed out label=%s timeout=%.2fs", label, timeout)
            raise RuntimeError(f"Timed out while {label}.") from exc

    async def _acquire_operation_lock(self, reason: str) -> None:
        """Acquire the backend operation lock with timeout and breadcrumbs."""

        try:
            await asyncio.wait_for(
                self._operation_lock.acquire(),
                timeout=self._operation_lock_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            self._debug(
                "operation lock timeout reason=%s timeout=%.2fs",
                reason,
                self._operation_lock_timeout_seconds,
            )
            raise RuntimeError(f"Timed out while waiting for Yard Stick backend lock during {reason}.") from exc

    def _release_operation_lock(self, reason: str) -> None:
        """Release the backend operation lock if it is currently held."""

        self._operation_lock.release()
        self._debug("operation lock released reason=%s", reason)

    @property
    def _active_frequency_hz(self) -> int:
        """Return the frequency currently selected from the scan window."""

        return self._frequency_scan_hz[self._frequency_index]

    @staticmethod
    def _build_frequency_scan(
        base_frequency_hz: int,
        explicit_scan_hz: tuple[int, ...] | None,
    ) -> tuple[int, ...]:
        """Build the deterministic receive scan window.

        The scan intentionally includes both:
        - SmartFire's exact 314.973 MHz reference
        - the 315.000 MHz center used successfully with rtl_433

        plus a small symmetric set of nearby offsets so we can cheaply probe a
        narrow acquisition window before concluding that the Yard Stick is not
        hearing useful payloads at all.
        """

        if explicit_scan_hz:
            ordered = tuple(int(freq) for freq in explicit_scan_hz)
        else:
            ordered = tuple(base_frequency_hz + offset_hz for offset_hz in DEFAULT_RX_SCAN_OFFSETS_HZ)

        unique_ordered: list[int] = []
        for frequency_hz in ordered:
            if frequency_hz not in unique_ordered:
                unique_ordered.append(frequency_hz)
        return tuple(unique_ordered)

    async def _advance_frequency(self, reason: str, *, log_to_decode_failure: bool) -> None:
        """Retune to the next frequency in the receive scan window."""

        if len(self._frequency_scan_hz) <= 1:
            return
        if not self._sweep_enabled:
            return

        previous_frequency_hz = self._active_frequency_hz
        self._frequency_index = (self._frequency_index + 1) % len(self._frequency_scan_hz)
        next_frequency_hz = self._active_frequency_hz
        if not self._worker_mode:
            assert self._radio is not None
            await self._async_in_executor(self._retune_radio_frequency, next_frequency_hz)
        if log_to_decode_failure:
            self._debug_decode_failure(
                "Advanced receive frequency reason=%s previous_freq_hz=%s next_freq_hz=%s scan_index=%s/%s",
                reason,
                previous_frequency_hz,
                next_frequency_hz,
                self._frequency_index + 1,
                len(self._frequency_scan_hz),
            )

    def _retune_radio_frequency(self, frequency_hz: int) -> None:
        """Retune the radio to one frequency from the receive scan window."""

        assert self._radio is not None
        if hasattr(self._radio, "setModeIDLE"):
            self._radio.setModeIDLE()
        self._radio.setFreq(frequency_hz)
        if hasattr(self._radio, "setModeRX"):
            self._radio.setModeRX()

    def _transmit_air_payload(
        self,
        air_payload: bytes,
        logical_repeat_count: int,
        repeat_separator_bits: int,
        rf_xmit_call_count: int,
        inter_frame_gap_ms: float,
        transmission_mode: str,
    ) -> None:
        """Perform blocking RFxmit calls using a prepared Yard Stick strategy."""

        assert self._radio is not None
        self._debug(
            "Entered blocking TX executor payload_length_bytes=%s transmission_mode=%s logical_repeat_count=%s repeat_separator_bits=%s rf_xmit_call_count=%s inter_frame_gap_ms=%s",
            len(air_payload),
            transmission_mode,
            logical_repeat_count,
            repeat_separator_bits,
            rf_xmit_call_count,
            inter_frame_gap_ms,
        )
        try:
            for frame_index in range(rf_xmit_call_count):
                self._debug(
                    "RFxmit start payload_length_bytes=%s transmission_mode=%s frame_index=%s/%s logical_repeat_count=%s repeat_separator_bits=%s",
                    len(air_payload),
                    transmission_mode,
                    frame_index + 1,
                    rf_xmit_call_count,
                    logical_repeat_count,
                    repeat_separator_bits,
                )
                self._radio.RFxmit(air_payload)
                self._debug(
                    "RFxmit complete payload_length_bytes=%s transmission_mode=%s frame_index=%s/%s logical_repeat_count=%s repeat_separator_bits=%s",
                    len(air_payload),
                    transmission_mode,
                    frame_index + 1,
                    rf_xmit_call_count,
                    logical_repeat_count,
                    repeat_separator_bits,
                )
                if inter_frame_gap_ms > 0 and frame_index + 1 < rf_xmit_call_count:
                    time.sleep(inter_frame_gap_ms / 1000.0)
        except Exception as exc:
            self._debug(
                "RFxmit raised payload_length_bytes=%s transmission_mode=%s logical_repeat_count=%s rf_xmit_call_count=%s exception_type=%s error=%s",
                len(air_payload),
                transmission_mode,
                logical_repeat_count,
                rf_xmit_call_count,
                type(exc).__name__,
                exc,
            )
            raise
        finally:
            if hasattr(self._radio, "setModeIDLE"):
                self._debug(
                    "Post-TX idle requested payload_length_bytes=%s transmission_mode=%s logical_repeat_count=%s",
                    len(air_payload),
                    transmission_mode,
                    logical_repeat_count,
                )
                try:
                    self._radio.setModeIDLE()
                    self._debug(
                        "Post-TX idle succeeded payload_length_bytes=%s transmission_mode=%s logical_repeat_count=%s",
                        len(air_payload),
                        transmission_mode,
                        logical_repeat_count,
                    )
                except Exception as exc:
                    _LOGGER.warning(
                        "Proflame2 Yard Stick post-TX setModeIDLE failed payload_length_bytes=%s transmission_mode=%s logical_repeat_count=%s exception_type=%s error=%s",
                        len(air_payload),
                        transmission_mode,
                        logical_repeat_count,
                        type(exc).__name__,
                        exc,
                    )
                    self._debug(
                        "Post-TX idle failed payload_length_bytes=%s transmission_mode=%s logical_repeat_count=%s exception_type=%s error=%s",
                        len(air_payload),
                        transmission_mode,
                        logical_repeat_count,
                        type(exc).__name__,
                        exc,
                    )

    def _debug(self, message: str, *args: Any) -> None:
        """Write one packet-debug line if packet logging is enabled."""

        if not self._packet_logger.isEnabledFor(logging.INFO) or not self._packet_logger.handlers:
            return
        self._packet_logger.info("yardstick: " + message, *args)

    def _debug_decode_failure(self, message: str, *args: Any) -> None:
        """Write one decode-failure line if decode-failure logging is enabled."""

        if not self._decode_failure_logger.isEnabledFor(logging.INFO) or not self._decode_failure_logger.handlers:
            return
        self._decode_failure_logger.info("yardstick: " + message, *args)

    def _debug_decode_failure_exception(self, message: str, *args: Any) -> None:
        """Write one decode-failure exception line with traceback if enabled."""

        if not self._decode_failure_logger.isEnabledFor(logging.ERROR) or not self._decode_failure_logger.handlers:
            return
        self._decode_failure_logger.exception("yardstick: " + message, *args)

    def _log_decode_diagnostics(self, raw_payload: bytes, diagnostics) -> None:
        """Log detailed decode-failure diagnostics for one raw payload."""

        reason_counts = (
            ", ".join(f"{reason}={count}" for reason, count in sorted(diagnostics.reason_counts.items())) or "none"
        )
        suppression_reason = diagnostics.best_failure.reason if diagnostics.best_failure is not None else "none"
        if _should_suppress_verbose_failure(raw_payload, diagnostics):
            self._debug_decode_failure(
                "Suppressed verbose decode diagnostics for noise-like RF payload bytes=%s quality=%s candidate_count=%s reason_counts=%s best_reason=%s",
                len(raw_payload),
                _summarize_payload_quality(raw_payload),
                len(diagnostics.candidates),
                reason_counts,
                suppression_reason,
            )
            return

        self._debug_decode_failure(
            "Received undecodable RF payload bytes=%s hex=%s",
            len(raw_payload),
            raw_payload.hex(),
        )
        self._debug_decode_failure(
            "RF payload quality %s",
            _summarize_payload_quality(raw_payload),
        )
        self._debug_decode_failure(
            "Decode failed samples_found=%s reason_counts=%s",
            diagnostics.samples_found,
            reason_counts,
        )
        self._debug_decode_failure(
            "Candidate count=%s candidate_offsets=%s",
            len(diagnostics.candidates),
            [
                {
                    "bit_offset": candidate.bit_offset,
                    "symbol_offset": candidate.symbol_offset,
                    "repeat_count": candidate.repeat_count,
                    "confidence": candidate.confidence,
                }
                for candidate in diagnostics.candidates[:5]
            ],
        )
        if diagnostics.best_failure is None:
            return

        failure = diagnostics.best_failure
        self._debug_decode_failure(
            "Best decode failure reason=%s detail=%s bit_offset=%s symbol_offset=%s",
            failure.reason,
            failure.detail,
            failure.bit_offset,
            failure.symbol_offset,
        )
        if diagnostics.symbols is not None:
            self._debug_decode_failure("Symbols=%s", diagnostics.symbols)
        self._debug_decode_failure("Bit stream=%s", diagnostics.bit_stream)
        if failure.symbol_window:
            self._debug_decode_failure("Candidate symbol window=%s", failure.symbol_window)
        if failure.extracted_words:
            self._debug_decode_failure("Extracted 9-bit words=%s", list(failure.extracted_words))
        if failure.candidate_remote_id is not None:
            self._debug_decode_failure(
                "Candidate frame remote_id=%06x cmd1=0x%02X err1=0x%02X cmd2=0x%02X err2=0x%02X",
                failure.candidate_remote_id,
                failure.candidate_cmd1,
                failure.candidate_err1,
                failure.candidate_cmd2,
                failure.candidate_err2,
            )

    def _recv_once(self, timeout: float | None) -> bytes | None:
        """Perform one blocking RF receive call."""

        assert self._radio is not None
        rf_timeout = 0 if timeout is None else max(1, int(timeout * 1000))
        try:
            payload, _ = self._radio.RFrecv(timeout=rf_timeout)
        except Exception as exc:
            if self._timeout_exception is not None and isinstance(exc, self._timeout_exception):
                return None
            raise

        if isinstance(payload, str):
            return payload.encode("latin1")
        return bytes(payload)

    def _build_receive_settings_snapshot(self) -> dict[str, Any]:
        snapshot: dict[str, Any] = {
            "active_frequency_hz": self._active_frequency_hz,
            "configured_frequency_hz": self._frequency_hz,
            "data_rate": self._data_rate,
            "packet_length_bytes": self._packet_length_bytes,
            "probe_mode": self._probe_mode,
            "sweep_enabled": self._sweep_enabled,
            "frequency_scan_hz": self._frequency_scan_hz,
            "worker_mode": self._worker_mode,
        }
        if self._last_radio_settings is not None:
            snapshot["radio_settings"] = self._last_radio_settings
        if self._last_worker_rx_settings is not None:
            snapshot["worker_receive_settings"] = self._last_worker_rx_settings
        if not self._worker_mode and self._radio is not None:
            snapshot["live_raw_registers"] = _yardstick_register_snapshot(self._radio)
        return snapshot

    def _build_learning_semantic_artifact(
        self,
        result: YardStickReceiveDiagnostics,
        *,
        rfrecv_attempt_index: int,
        learning_attempt_count: int,
        failed_attempt_count_before_success: int,
        capture_started_at_utc: str,
        capture_completed_at_utc: str,
    ) -> dict[str, Any]:
        selected = dict(result.selected_candidate_window or {})
        decoded = dict(result.decoded_fields or {})
        sample = result.sample
        state_fields: dict[str, Any] = {}
        if sample is not None:
            packet = sample.as_packet(source="yardstick")
            state_fields = {
                "power": int(packet.state.power),
                "flame": packet.state.flame,
                "fan": packet.state.fan,
                "pilot": int(packet.state.cpi),
                "light": packet.state.light,
                "thermostat": int(packet.state.thermostat),
                "front": int(packet.state.front),
                "aux": int(packet.state.aux),
            }
        remote_id = decoded.get("remote_id")
        if remote_id is None and sample is not None:
            remote_id = sample.remote_id
        return {
            "artifact_type": "yardstick_learning_semantic_candidate",
            "artifact_class": "semantic",
            "semantic_comparable": True,
            "decode_success": True,
            "learning_accepted": True,
            "acceptance_policy": "learning_equivalent_success",
            "remote_id": remote_id,
            "id": decoded.get("id") or (f"{sample.remote_id:06x}" if sample is not None else None),
            "cmd1": decoded.get("cmd1") or (f"{sample.cmd1:02x}" if sample is not None else None),
            "err1": decoded.get("err1") or (f"{sample.err1:02x}" if sample is not None else None),
            "cmd2": decoded.get("cmd2") or (f"{sample.cmd2:02x}" if sample is not None else None),
            "err2": decoded.get("err2") or (f"{sample.err2:02x}" if sample is not None else None),
            **state_fields,
            "raw_payload_hex": result.raw_payload_hex,
            "payload_length_bytes": result.payload_length_bytes,
            "candidate_bit_offset": selected.get("bit_offset"),
            "candidate_symbol_offset": selected.get("symbol_offset"),
            "candidate_absolute_bit_offset": selected.get("absolute_bit_offset"),
            "candidate_symbol_length": selected.get("symbol_length"),
            "candidate_bit_length": selected.get("bit_length"),
            "candidate_symbol_stream": selected.get("symbol_stream"),
            "candidate_bit_stream": selected.get("bit_stream"),
            "repeat_count": result.repeat_count,
            "occurrence_offsets": [list(offset) for offset in result.occurrence_offsets],
            "candidate_confidence": selected.get("score"),
            "candidate_validation_notes": selected.get("validation_notes", []),
            "candidate_guard_check": selected.get("guard_check"),
            "candidate_manchester_check": selected.get("manchester_check"),
            "rx_settings": result.rx_settings,
            "rfrecv_attempt_index": rfrecv_attempt_index,
            "learning_attempt_count": learning_attempt_count,
            "failed_attempt_count_before_success": failed_attempt_count_before_success,
            "capture_started_at_utc": capture_started_at_utc,
            "capture_completed_at_utc": capture_completed_at_utc,
            "provenance": {
                "source": "YardStickBackend.receive_learning_diagnostics",
                "scanner": "find_proflame_candidates",
                "source_object": "DecodeCandidate.sample/CaptureSample",
                "candidate_local_streams": True,
                "parent_rfrecv_payload": True,
                "learning_equivalent_acceptance_path": True,
                "failed_attempts_separate": True,
                "ownership_guarantee": (
                    "Packet ownership begins at successful DecodeCandidate selection; "
                    "full RFrecv streams and failed windows are debug-only."
                ),
            },
        }

    def _summarize_learning_failed_attempt(
        self,
        result: YardStickReceiveDiagnostics,
        *,
        attempt_index: int,
    ) -> dict[str, Any]:
        best_failed = None
        if result.failed_candidate_windows:
            window = dict(result.failed_candidate_windows[0])
            best_failed = {
                "artifact_class": "debug_failure",
                "semantic_comparable": False,
                "decode_success": False,
                "learning_accepted": False,
                "failure_reason": window.get("failure_reason"),
                "score": window.get("score"),
                "bit_offset": window.get("bit_offset"),
                "symbol_offset": window.get("symbol_offset"),
                "symbol_length": window.get("symbol_length"),
                "symbol_prefix": str(window.get("symbol_stream") or "")[:32],
                "detail": window.get("detail"),
            }
        raw_payload_hex = result.raw_payload_hex or ""
        return {
            "attempt_index": attempt_index,
            "artifact_class": "debug_failure",
            "semantic_comparable": False,
            "decode_success": False,
            "learning_accepted": False,
            "prohibited_uses": [
                "semantic_replicate_analysis",
                "transform_inference",
                "canonical_yardstick_reference",
            ],
            "capture_complete": result.capture_complete,
            "decode_failure_reason": result.decode_failure_reason,
            "best_failure_reason": result.best_failure_reason,
            "reason_counts": dict(result.reason_counts),
            "payload_length_bytes": result.payload_length_bytes,
            "raw_payload_sha256": (
                hashlib.sha256(bytes.fromhex(raw_payload_hex)).hexdigest() if raw_payload_hex else None
            ),
            "active_frequency_hz": result.active_frequency_hz,
            "rx_settings": result.rx_settings,
            "receive_status": None if result.receive_status is None else result.receive_status.__dict__,
            "best_failure_window": best_failed,
        }

    def _build_receive_diagnostics_result(
        self,
        *,
        raw_payload: bytes,
        diagnostics: DecodeDiagnostics,
        sample: CaptureSample | None,
        selected_candidate: Any,
        decode_success: bool,
        start_ns: int,
        complete_ns: int,
        start_wall: datetime,
        end_wall: datetime,
    ) -> YardStickReceiveDiagnostics:
        decoded_fields = None
        occurrence_offsets: tuple[tuple[int, int], ...] = ()
        selected_bit_offset = None
        selected_symbol_offset = None
        repeat_count = None
        if selected_candidate is not None:
            selected_bit_offset = selected_candidate.bit_offset
            selected_symbol_offset = selected_candidate.symbol_offset
            repeat_count = selected_candidate.repeat_count
            occurrence_offsets = tuple(selected_candidate.occurrence_offsets)
        if sample is not None:
            decoded_fields = {
                "remote_id": sample.remote_id,
                "id": f"{sample.remote_id:06x}",
                "cmd1": f"{sample.cmd1:02x}",
                "cmd2": f"{sample.cmd2:02x}",
                "err1": f"{sample.err1:02x}",
                "err2": f"{sample.err2:02x}",
            }
        best_failure_reason = diagnostics.best_failure.reason if diagnostics.best_failure is not None else None
        candidate_windows = tuple(
            self._serialize_candidate_window(candidate, diagnostics, candidate_index=index)
            for index, candidate in enumerate(diagnostics.candidates)
        )
        failed_candidate_windows = (
            (self._serialize_failed_window(diagnostics.best_failure, diagnostics, candidate_index=0),)
            if diagnostics.best_failure is not None
            else ()
        )
        diagnostic_candidate_windows = self._diagnostic_candidate_windows(diagnostics)
        selected_candidate_window = (
            self._serialize_candidate_window(selected_candidate, diagnostics, candidate_index=0)
            if selected_candidate is not None
            else None
        )
        best_candidate_window = candidate_windows[0] if candidate_windows else None
        limitations = [
            "bit_stream is the full RFrecv payload bit expansion, not a packet-normalized stream.",
            "symbol_stream is the full RFrecv payload decoded at bit_offset=0, not a selected packet window.",
        ]
        if not diagnostics.candidates:
            limitations.append(
                "No structurally valid Proflame candidate was found; selected offsets and occurrence offsets are unavailable."
            )
        if diagnostic_candidate_windows:
            limitations.append(
                "diagnostic_candidate_windows are heuristic/offline diagnostics and are not semantic decode truth."
            )
        return YardStickReceiveDiagnostics(
            capture_complete=True,
            sample=sample,
            raw_payload=raw_payload,
            raw_payload_hex=raw_payload.hex(),
            payload_length_bytes=len(raw_payload),
            bit_stream=diagnostics.bit_stream,
            symbol_stream=diagnostics.symbols,
            decode_diagnostics=diagnostics,
            decoded_fields=decoded_fields,
            decode_success=decode_success,
            decode_failure_reason=None if decode_success else best_failure_reason,
            best_failure_reason=best_failure_reason,
            reason_counts=dict(diagnostics.reason_counts),
            selected_bit_offset=selected_bit_offset,
            selected_symbol_offset=selected_symbol_offset,
            repeat_count=repeat_count,
            occurrence_offsets=occurrence_offsets,
            candidate_count=len(diagnostics.candidates),
            artifact_layer="rfrecv_fixed_length_payload",
            symbol_stream_layer="full_payload_tolerant_symbols_bit_offset_0",
            bit_stream_layer="full_payload_raw_bits",
            packet_normalized=False,
            contains_multiple_repeats=True if repeat_count and repeat_count > 1 else None,
            contains_partial_window=None,
            candidate_search_performed=True,
            candidate_windows_retained=bool(
                candidate_windows or failed_candidate_windows or diagnostic_candidate_windows
            ),
            selected_window_available=selected_candidate_window is not None,
            diagnostic_limitations=tuple(limitations),
            candidate_windows=candidate_windows,
            failed_candidate_windows=failed_candidate_windows,
            best_candidate_window=best_candidate_window,
            selected_candidate_window=selected_candidate_window,
            diagnostic_candidate_windows=diagnostic_candidate_windows,
            diagnostic_candidate_offsets=tuple(window["symbol_offset"] for window in diagnostic_candidate_windows),
            diagnostic_candidate_reason=(
                "guard_pattern_heuristic_windows" if diagnostic_candidate_windows else "no_diagnostic_candidate_window"
            ),
            diagnostic_candidate_confidence=("low" if diagnostic_candidate_windows else None),
            active_frequency_hz=self._active_frequency_hz,
            rx_settings=self._build_receive_settings_snapshot(),
            host_start_ns=start_ns,
            host_complete_ns=complete_ns,
            started_at_utc=start_wall.isoformat(),
            completed_at_utc=end_wall.isoformat(),
            receive_status=self.last_receive_status,
        )

    def _serialize_candidate_window(
        self, candidate: Any, diagnostics: DecodeDiagnostics, *, candidate_index: int
    ) -> dict[str, Any]:
        bit_start = candidate.absolute_bit_offset
        bit_length = len(candidate.sample.symbols) * 2
        return {
            "candidate_index": candidate_index,
            "artifact_class": "candidate",
            "semantic_comparable": False,
            "learning_accepted": False,
            "symbol_offset": candidate.symbol_offset,
            "bit_offset": candidate.bit_offset,
            "absolute_bit_offset": candidate.absolute_bit_offset,
            "symbol_length": len(candidate.sample.symbols),
            "bit_length": bit_length,
            "symbol_stream": candidate.sample.symbols,
            "bit_stream": diagnostics.bit_stream[bit_start : bit_start + bit_length],
            "decode_attempted": True,
            "decode_success": True,
            "failure_reason": None,
            "score": candidate.confidence,
            "repeat_count": candidate.repeat_count,
            "occurrence_offsets": [list(offset) for offset in candidate.occurrence_offsets],
            "guard_check": {
                "trailing_guard_valid": candidate.trailing_guard_valid,
                "trailing_guard_observed": candidate.trailing_guard_observed,
                "trailing_guard_warning": candidate.trailing_guard_warning,
            },
            "manchester_check": "passed",
            "validation_notes": list(candidate.validation_notes),
        }

    def _serialize_failed_window(
        self, failure: Any, diagnostics: DecodeDiagnostics, *, candidate_index: int
    ) -> dict[str, Any]:
        absolute_bit_offset = failure.bit_offset + (failure.symbol_offset * 2)
        bit_length = len(failure.symbol_window) * 2
        return {
            "candidate_index": candidate_index,
            "artifact_class": "debug_failure",
            "semantic_comparable": False,
            "learning_accepted": False,
            "prohibited_uses": [
                "semantic_replicate_analysis",
                "transform_inference",
                "canonical_yardstick_reference",
            ],
            "symbol_offset": failure.symbol_offset,
            "bit_offset": failure.bit_offset,
            "absolute_bit_offset": absolute_bit_offset,
            "symbol_length": len(failure.symbol_window),
            "bit_length": bit_length,
            "symbol_stream": failure.symbol_window,
            "bit_stream": diagnostics.bit_stream[absolute_bit_offset : absolute_bit_offset + bit_length],
            "decode_attempted": True,
            "decode_success": False,
            "failure_reason": failure.reason,
            "score": failure.stage_score,
            "guard_check": "failed" if failure.reason in {REASON_BAD_START_END_GUARD} else "unknown",
            "manchester_check": "failed" if failure.reason == "invalid_manchester_symbols" else "unknown",
            "detail": failure.detail,
        }

    def _diagnostic_candidate_windows(self, diagnostics: DecodeDiagnostics) -> tuple[dict[str, Any], ...]:
        symbols = diagnostics.symbols or ""
        if len(symbols) < TOTAL_SYMBOLS:
            return ()
        scored: list[tuple[int, int, int, str]] = []
        for symbol_offset in range(0, len(symbols) - TOTAL_SYMBOLS + 1):
            window = symbols[symbol_offset : symbol_offset + TOTAL_SYMBOLS]
            guard_score = 0
            for word_index in range(7):
                offset = word_index * 13
                chunk = window[offset : offset + 13]
                if len(chunk) != 13:
                    continue
                guard_score += int(chunk[0] == "S")
                guard_score += int(chunk[1] == "1")
                guard_score += int(chunk[-1] == "1")
            binary_score = sum(symbol in {"0", "1"} for symbol in window)
            if guard_score < 10 and binary_score < 20:
                continue
            scored.append((guard_score, binary_score, symbol_offset, window))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        windows: list[dict[str, Any]] = []
        for index, (guard_score, binary_score, symbol_offset, window) in enumerate(scored[:5]):
            absolute_bit_offset = symbol_offset * 2
            windows.append(
                {
                    "candidate_index": index,
                    "artifact_class": "heuristic_debug",
                    "semantic_comparable": False,
                    "learning_accepted": False,
                    "prohibited_uses": [
                        "semantic_replicate_analysis",
                        "transform_inference",
                        "canonical_yardstick_reference",
                    ],
                    "symbol_offset": symbol_offset,
                    "bit_offset": 0,
                    "absolute_bit_offset": absolute_bit_offset,
                    "symbol_length": len(window),
                    "bit_length": len(window) * 2,
                    "symbol_stream": window,
                    "bit_stream": diagnostics.bit_stream[absolute_bit_offset : absolute_bit_offset + (len(window) * 2)],
                    "decode_attempted": False,
                    "decode_success": False,
                    "failure_reason": "diagnostic_only_not_decoded",
                    "score": guard_score + binary_score,
                    "guard_score": guard_score,
                    "binary_symbol_count": binary_score,
                    "diagnostic_only": True,
                }
            )
        return tuple(windows)

    async def _async_worker_request(
        self,
        command: str,
        payload: dict[str, Any],
        *,
        timeout: float,
    ) -> dict[str, Any]:
        if self._worker_supervisor is None:
            raise RuntimeError("Yard Stick worker supervisor is not available.")
        return await self._async_in_executor(
            partial(self._worker_supervisor.request, command, payload, timeout=timeout),
        )

    def serialize_worker_diagnostics(self) -> dict[str, Any] | None:
        if self._worker_supervisor is None:
            return None
        return self._worker_supervisor.serialize_diagnostics()
