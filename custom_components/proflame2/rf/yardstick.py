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
from datetime import datetime, timezone
import logging
import time
from pprint import pformat
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from .base import BackendCapabilities, CaptureResult, RFBackend, ReceiveStatus, SendResult
from .capture import (
    CaptureSample,
    REASON_BAD_START_END_GUARD,
    REASON_UNKNOWN_DECODE_FAILURE,
    REASON_WORD_COUNT_MISMATCH,
    diagnose_air_payload,
    find_proflame_candidates,
)
from ..packet_debug import (
    get_packet_debug_logger,
    get_packet_decode_failure_logger,
)
from .waveform import SMARTFIRE_DEFAULT_TOTAL_TRANSMISSIONS

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

PROFLAME2_FREQUENCY_HZ = 314_973_000
PROFLAME2_DATA_RATE = 2_400
PROFLAME2_PACKET_BYTES = 25
DIAGNOSTIC_PACKET_BYTES = 255
YARDSTICK_RX_LEARNING_FREQUENCY_HZ = 315_000_000
YARDSTICK_RX_LEARNING_PACKET_BYTES = DIAGNOSTIC_PACKET_BYTES
YARDSTICK_RX_LEARNING_SWEEP_ENABLED = False
YARDSTICK_OPERATION_LOCK_TIMEOUT_SECONDS = 5.0
YARDSTICK_CONNECT_TIMEOUT_SECONDS = 10.0
YARDSTICK_TRANSMIT_TIMEOUT_SECONDS = 15.0
YARDSTICK_TX_DEFAULT_TRANSMISSIONS = SMARTFIRE_DEFAULT_TOTAL_TRANSMISSIONS
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

    from rflib import MOD_ASK_OOK, RfCat
    from rflib import ChipconUsbTimeoutException

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
    likely_noise = (
        dominant_ratio >= 0.8
        or unique_values in ([0xFF], [0x00], [0x00, 0xFF])
    )
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
        operation_lock_timeout_seconds: float = YARDSTICK_OPERATION_LOCK_TIMEOUT_SECONDS,
        connect_timeout_seconds: float = YARDSTICK_CONNECT_TIMEOUT_SECONDS,
        transmit_timeout_seconds: float = YARDSTICK_TRANSMIT_TIMEOUT_SECONDS,
    ) -> None:
        self.name = "yardstick"
        self._executor_job = executor_job or (
            hass.async_add_executor_job if hass is not None else None
        )
        self._device_index = device_index
        self._frequency_hz = frequency_hz
        self._tx_frequency_hz = tx_frequency_hz
        self._data_rate = data_rate
        self._radio = radio
        self._packet_length_bytes = packet_length_bytes
        self._probe_mode = probe_mode
        self._frequency_scan_hz = self._build_frequency_scan(frequency_hz, frequency_scan_hz)
        self._sweep_enabled = (
            len(self._frequency_scan_hz) > 1 if sweep_enabled is None else sweep_enabled
        )
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
        self._operation_lock_timeout_seconds = operation_lock_timeout_seconds
        self._connect_timeout_seconds = connect_timeout_seconds
        self._transmit_timeout_seconds = transmit_timeout_seconds
        self._operation_lock = asyncio.Lock()
        _LOGGER.info(
            "Proflame2 Yard Stick backend constructed device_index=%s rx_frequency_hz=%s tx_frequency_hz=%s packet_length_bytes=%s sweep_enabled=%s probe_mode=%s tx_mode=%s tx_transmissions=%s tx_inter_frame_gap_ms=%s",
            self._device_index,
            self._frequency_hz,
            self._tx_frequency_hz,
            self._packet_length_bytes,
            self._sweep_enabled,
            self._probe_mode,
            "software_repeat",
            self._tx_transmissions or YARDSTICK_TX_DEFAULT_TRANSMISSIONS,
            self._tx_inter_frame_gap_ms,
        )

    async def connect(self) -> None:
        """Open the Yard Stick and configure it for Proflame2 receive."""

        lock_acquired = False
        self._debug(
            "connect: backend connected=%s radio_exists=%s lock_acquire_start timeout=%.2fs",
            self._radio is not None,
            self._radio is not None,
            self._operation_lock_timeout_seconds,
        )
        await self._acquire_operation_lock("connect")
        lock_acquired = True
        try:
            self._debug("connect: lock acquired")
            await self._async_connect_locked()
            self._debug("connect: rx configure start")
            radio_settings = await self._await_with_timeout(
                self._async_in_executor(self._configure_radio, self._modulation),
                timeout=self._connect_timeout_seconds,
                label="receive_radio_configuration",
            )
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

    async def close(self) -> None:
        """Close the backend connection."""

        if self._radio is None:
            _LOGGER.info("Proflame2 Yard Stick close requested but no radio is open.")
            return None
        _LOGGER.info("Proflame2 Yard Stick close requested device_index=%s", self._device_index)
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
            _LOGGER.info("Proflame2 Yard Stick close completed device_index=%s", self._device_index)

    async def send(self, packet) -> SendResult:
        """Transmit one prepared Proflame2 packet via Yard Stick One."""

        self._debug(
            "Entered YardStickBackend.send remote_id=%06x tx_frequency_hz=%s packet_has_plan=%s",
            packet.remote_id,
            self._tx_frequency_hz,
            packet.transmission_plan is not None,
        )
        lock_acquired = False
        if packet.transmission_plan is None:
            raise RuntimeError("Yard Stick transmit requires packet.transmission_plan to be present.")
        try:
            self._debug(
                "send: lock acquire start timeout=%.2fs radio_exists=%s",
                self._operation_lock_timeout_seconds,
                self._radio is not None,
            )
            await self._acquire_operation_lock("send")
            lock_acquired = True
            self._debug("send: lock acquired")
            self._debug("send: connect required=%s", self._radio is None)
            await self._async_connect_locked()
            assert self._radio is not None

            modulation = self._modulation
            if modulation is None:
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

            self._debug("send: TX configure start")
            tx_settings = await self._await_with_timeout(
                self._async_in_executor(self._configure_transmit_radio, modulation),
                timeout=self._connect_timeout_seconds,
                label="transmit_radio_configuration",
            )
            self._debug("send: TX configure complete")
            plan = packet.transmission_plan
            effective_transmissions = self._tx_transmissions or plan.repeat_count
            transmission_mode = "software_repeat"
            _LOGGER.info(
                "Proflame2 Yard Stick TX remote_id=%06x tx_frequency_hz=%s modulation=%s data_rate=%s payload_length_bytes=%s transmission_mode=%s software_transmissions=%s inter_frame_gap_ms=%s",
                packet.remote_id,
                tx_settings["frequency_hz"],
                tx_settings["modulation_mode"],
                tx_settings["data_rate"],
                len(plan.air_payload),
                transmission_mode,
                effective_transmissions,
                self._tx_inter_frame_gap_ms,
            )
            self._debug(
                "TX start remote_id=%06x tx_frequency_hz=%s tx_reference_freq_hz=%s modulation=%s data_rate=%s payload_length_bytes=%s transmission_mode=%s software_transmissions=%s inter_frame_gap_ms=%s "
                "cmd1=0x%02X err1=0x%02X cmd2=0x%02X err2=0x%02X air_payload=%s",
                packet.remote_id,
                tx_settings["frequency_hz"],
                self._tx_frequency_hz,
                tx_settings["modulation_mode"],
                tx_settings["data_rate"],
                len(plan.air_payload),
                transmission_mode,
                effective_transmissions,
                self._tx_inter_frame_gap_ms,
                packet.frame.cmd1,
                packet.frame.err1,
                packet.frame.cmd2,
                packet.frame.err2,
                plan.air_payload.hex(),
            )
            self._debug("send: blocking TX executor submit")
            await self._await_with_timeout(
                self._async_in_executor(
                    self._transmit_air_payload,
                    plan.air_payload,
                    effective_transmissions,
                    self._tx_inter_frame_gap_ms,
                ),
                timeout=self._transmit_timeout_seconds,
                label="blocking_transmit_executor",
            )
        except Exception as exc:
            _LOGGER.exception(
                "Proflame2 Yard Stick TX failed remote_id=%06x payload_length_bytes=%s software_transmissions=%s",
                packet.remote_id,
                len(packet.transmission_plan.air_payload),
                self._tx_transmissions or packet.transmission_plan.repeat_count,
            )
            self._debug(
                "TX failure remote_id=%06x payload_length_bytes=%s software_transmissions=%s exception_type=%s error=%s",
                packet.remote_id,
                len(packet.transmission_plan.air_payload),
                self._tx_transmissions or packet.transmission_plan.repeat_count,
                type(exc).__name__,
                exc,
            )
            raise RuntimeError(str(exc)) from exc
        finally:
            if lock_acquired:
                self._release_operation_lock("send")

        self._debug(
            "TX success remote_id=%06x payload_length_bytes=%s software_transmissions=%s",
            packet.remote_id,
            len(packet.transmission_plan.air_payload),
            self._tx_transmissions or packet.transmission_plan.repeat_count,
        )
        self._debug("send: returning success")
        return SendResult(
            packet=packet,
            backend_name=self.name,
            warnings=packet.warnings,
        )

    async def receive_raw_payload(self, timeout: float | None = None) -> bytes | None:
        """Receive one raw RF payload without attempting Proflame2 decode."""

        if self._radio is None:
            raise RuntimeError("YardStickBackend.connect() must be called before receive().")
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

        if self._radio is None:
            raise RuntimeError("YardStickBackend.connect() must be called before receive().")

        start_wall = datetime.now(timezone.utc)
        start_monotonic = time.monotonic()
        self.last_receive_status = None
        self._debug_decode_failure(
            "RFrecv start timeout=%s active_freq_hz=%s payload_length_bytes=%s sweep_enabled=%s scan_index=%s/%s start=%s",
            timeout,
            self._active_frequency_hz,
            self._packet_length_bytes,
            self._sweep_enabled,
            self._frequency_index + 1,
            len(self._frequency_scan_hz),
            start_wall.isoformat(),
        )
        try:
            raw_payload = await self._async_in_executor(self._recv_once, timeout)
        except Exception as exc:
            end_wall = datetime.now(timezone.utc)
            elapsed_ms = (time.monotonic() - start_monotonic) * 1000
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
                timeout,
            )
            self._debug_decode_failure_exception(
                "RFrecv exception timeout=%s active_freq_hz=%s payload_length_bytes=%s sweep_enabled=%s start=%s end=%s elapsed_ms=%.2f exception_type=%s error=%s",
                timeout,
                self._active_frequency_hz,
                self._packet_length_bytes,
                self._sweep_enabled,
                start_wall.isoformat(),
                end_wall.isoformat(),
                elapsed_ms,
                type(exc).__name__,
                exc,
            )
            self._debug_decode_failure(
                "RFrecv end timeout=%s active_freq_hz=%s payload_length_bytes=%s sweep_enabled=%s start=%s end=%s elapsed_ms=%.2f received_payload=%s exception_type=%s error=%s",
                timeout,
                self._active_frequency_hz,
                self._packet_length_bytes,
                self._sweep_enabled,
                start_wall.isoformat(),
                end_wall.isoformat(),
                elapsed_ms,
                False,
                type(exc).__name__,
                exc,
            )
            self._debug(
                "RF receive failed start=%s end=%s elapsed_ms=%.2f exception_type=%s error=%s",
                start_wall.isoformat(),
                end_wall.isoformat(),
                elapsed_ms,
                type(exc).__name__,
                exc,
            )
            raise
        try:
            end_wall = datetime.now(timezone.utc)
            elapsed_ms = (time.monotonic() - start_monotonic) * 1000
            self._debug_decode_failure(
                "RFrecv end timeout=%s active_freq_hz=%s payload_length_bytes=%s sweep_enabled=%s start=%s end=%s elapsed_ms=%.2f received_payload=%s",
                timeout,
                self._active_frequency_hz,
                self._packet_length_bytes,
                self._sweep_enabled,
                start_wall.isoformat(),
                end_wall.isoformat(),
                elapsed_ms,
                raw_payload is not None,
            )
            if raw_payload is None:
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
                        timeout,
                        self._active_frequency_hz,
                        self._packet_length_bytes,
                        self._sweep_enabled,
                        self._frequency_index + 1,
                        len(self._frequency_scan_hz),
                        start_wall.isoformat(),
                        end_wall.isoformat(),
                        elapsed_ms,
                    )
                await self._advance_frequency("timeout_without_payload", log_to_decode_failure=False)
                return None

            self._consecutive_timeouts = 0
            diagnostics = diagnose_air_payload(raw_payload)
            candidates = list(diagnostics.candidates)
            if not candidates:
                self.last_receive_status = ReceiveStatus(
                    outcome="payload_no_candidates",
                    reason=(
                        diagnostics.best_failure.reason
                        if diagnostics.best_failure is not None
                        else "no_valid_candidate"
                    ),
                    active_frequency_hz=self._active_frequency_hz,
                    payload_length_bytes=len(raw_payload),
                    sweep_enabled=self._sweep_enabled,
                    candidate_count=0,
                )
                self._debug_decode_failure(
                    "RF receive complete start=%s end=%s elapsed_ms=%.2f timeout=%s received_payload=%s active_freq_hz=%s scan_index=%s/%s",
                    start_wall.isoformat(),
                    end_wall.isoformat(),
                    elapsed_ms,
                    False,
                    True,
                    self._active_frequency_hz,
                    self._frequency_index + 1,
                    len(self._frequency_scan_hz),
                )
                self._log_decode_diagnostics(raw_payload, diagnostics)
                await self._advance_frequency("payload_received_but_decode_failed", log_to_decode_failure=True)
                return None

            self._debug(
                "RF receive complete start=%s end=%s elapsed_ms=%.2f timeout=%s received_payload=%s active_freq_hz=%s payload_length_bytes=%s sweep_enabled=%s",
                start_wall.isoformat(),
                end_wall.isoformat(),
                elapsed_ms,
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
            return sample
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

    async def learn(self, timeout: float | None = None) -> CaptureResult:
        """Collect decodable samples until the timeout expires."""

        deadline = None if timeout is None else (time.monotonic() + timeout)
        samples: list[CaptureSample] = []
        raw_payloads = 0
        decode_failures = 0

        while deadline is None or time.monotonic() < deadline:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            raw_payload = await self._async_in_executor(self._recv_once, remaining)
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
            can_send=False,
            can_receive=True,
            can_learn=True,
            notes=(
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
            raise RuntimeError(
                f"Timed out while waiting for Yard Stick backend lock during {reason}."
            ) from exc

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
            ordered = tuple(
                base_frequency_hz + offset_hz for offset_hz in DEFAULT_RX_SCAN_OFFSETS_HZ
            )

        unique_ordered: list[int] = []
        for frequency_hz in ordered:
            if frequency_hz not in unique_ordered:
                unique_ordered.append(frequency_hz)
        return tuple(unique_ordered)

    async def _advance_frequency(self, reason: str, *, log_to_decode_failure: bool) -> None:
        """Retune to the next frequency in the receive scan window."""

        if self._radio is None or len(self._frequency_scan_hz) <= 1:
            return
        if not self._sweep_enabled:
            return

        previous_frequency_hz = self._active_frequency_hz
        self._frequency_index = (self._frequency_index + 1) % len(self._frequency_scan_hz)
        next_frequency_hz = self._active_frequency_hz
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
        software_transmissions: int,
        inter_frame_gap_ms: float,
    ) -> None:
        """Perform one blocking RFxmit call using a prepared air payload."""

        assert self._radio is not None
        self._debug(
            "Entered blocking TX executor payload_length_bytes=%s software_transmissions=%s inter_frame_gap_ms=%s",
            len(air_payload),
            software_transmissions,
            inter_frame_gap_ms,
        )
        try:
            self._debug(
                "Software burst start payload_length_bytes=%s software_transmissions=%s inter_frame_gap_ms=%s note=stock_remote_style_five_frame_burst",
                len(air_payload),
                software_transmissions,
                inter_frame_gap_ms,
            )
            for frame_index in range(software_transmissions):
                self._debug(
                    "RFxmit frame start mode=software_repeat frame_index=%s/%s payload_length_bytes=%s",
                    frame_index + 1,
                    software_transmissions,
                    len(air_payload),
                )
                self._radio.RFxmit(air_payload)
                self._debug(
                    "RFxmit frame complete mode=software_repeat frame_index=%s/%s payload_length_bytes=%s",
                    frame_index + 1,
                    software_transmissions,
                    len(air_payload),
                )
                if inter_frame_gap_ms > 0 and frame_index + 1 < software_transmissions:
                    self._debug(
                        "Inter-frame gap sleep mode=software_repeat frame_index=%s/%s inter_frame_gap_ms=%s",
                        frame_index + 1,
                        software_transmissions,
                        inter_frame_gap_ms,
                    )
                    time.sleep(inter_frame_gap_ms / 1000.0)
            self._debug(
                "Software burst complete payload_length_bytes=%s software_transmissions=%s inter_frame_gap_ms=%s",
                len(air_payload),
                software_transmissions,
                inter_frame_gap_ms,
            )
        except Exception as exc:
            self._debug(
                "RFxmit raised payload_length_bytes=%s transmission_mode=%s software_transmissions=%s exception_type=%s error=%s",
                len(air_payload),
                "software_repeat",
                software_transmissions,
                type(exc).__name__,
                exc,
            )
            raise
        finally:
            if hasattr(self._radio, "setModeIDLE"):
                self._debug(
                    "Post-TX idle requested payload_length_bytes=%s transmission_mode=%s software_transmissions=%s",
                    len(air_payload),
                    "software_repeat",
                    software_transmissions,
                )
                try:
                    self._radio.setModeIDLE()
                    self._debug(
                        "Post-TX idle succeeded payload_length_bytes=%s transmission_mode=%s software_transmissions=%s",
                        len(air_payload),
                        "software_repeat",
                        software_transmissions,
                    )
                except Exception as exc:
                    _LOGGER.warning(
                        "Proflame2 Yard Stick post-TX setModeIDLE failed payload_length_bytes=%s transmission_mode=%s software_transmissions=%s exception_type=%s error=%s",
                        len(air_payload),
                        "software_repeat",
                        software_transmissions,
                        type(exc).__name__,
                        exc,
                    )
                    self._debug(
                        "Post-TX idle failed payload_length_bytes=%s transmission_mode=%s software_transmissions=%s exception_type=%s error=%s",
                        len(air_payload),
                        "software_repeat",
                        software_transmissions,
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

        if (
            not self._decode_failure_logger.isEnabledFor(logging.INFO)
            or not self._decode_failure_logger.handlers
        ):
            return
        self._decode_failure_logger.info("yardstick: " + message, *args)

    def _debug_decode_failure_exception(self, message: str, *args: Any) -> None:
        """Write one decode-failure exception line with traceback if enabled."""

        if (
            not self._decode_failure_logger.isEnabledFor(logging.ERROR)
            or not self._decode_failure_logger.handlers
        ):
            return
        self._decode_failure_logger.exception("yardstick: " + message, *args)

    def _log_decode_diagnostics(self, raw_payload: bytes, diagnostics) -> None:
        """Log detailed decode-failure diagnostics for one raw payload."""

        reason_counts = ", ".join(
            f"{reason}={count}" for reason, count in sorted(diagnostics.reason_counts.items())
        ) or "none"
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
