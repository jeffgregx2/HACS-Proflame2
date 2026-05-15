"""Transport boundary for ESPHome native API integration.

This module defines the async boundary used by the Proflame2 HA backend to talk
to a T-Embed endpoint.  It includes:

- a deterministic in-memory mock transport for unit tests
- a Home Assistant-linked transport that reuses the runtime client/service
  catalog of an existing ESPHome config entry and calls the device's
  ``proflame2_tx_stateful`` action over the real native API path
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from homeassistant.const import ATTR_DEVICE_ID, CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr

from ...packet_debug import get_packet_debug_logger
from .contract import (
    ESPHomeDisplayState,
    ESPHomeEndpointStatus,
    ESPHomeEndpointStatusReport,
    ESPHomeRadioConfig,
    ESPHomeRXEvent,
    ESPHomeTXRequest,
    ESPHomeTXResponse,
)

_LOGGER = logging.getLogger(__name__)


class ESPHomeTransport(Protocol):
    """Async transport operations provided by a T-Embed endpoint client."""

    async def connect(self) -> None:
        """Open the transport connection."""

    async def configure_radio(self, config: ESPHomeRadioConfig) -> None:
        """Apply persistent radio configuration to the endpoint."""

    async def send_tx(self, request: ESPHomeTXRequest) -> ESPHomeTXResponse:
        """Send one prepared TX request."""

    async def update_display_state(self, display_state: ESPHomeDisplayState) -> None:
        """Push display-only state to the endpoint without transmitting RF."""

    async def get_status(self) -> ESPHomeEndpointStatusReport:
        """Return the latest endpoint status."""

    async def receive_rx_event(self, timeout: float | None = None) -> ESPHomeRXEvent | None:
        """Return one raw receive event from the endpoint."""

    async def set_active_listening(self, enabled: bool, profile: Any | None = None) -> None:
        """Apply the runtime RX policy to the endpoint."""

    async def stop_rx(self) -> None:
        """Stop RX listening on the endpoint."""

    async def end_confirmation_rx(self) -> None:
        """End confirmation-mode RX without changing active-listening policy."""

    async def update_learning_mode(
        self,
        *,
        active: bool,
        step_title: str,
        instruction: str,
        status: str,
    ) -> None:
        """Update endpoint learning-mode UI/status."""

    async def close(self) -> None:
        """Close the transport connection."""

    def serialize_diagnostics(self) -> dict[str, Any]:
        """Return diagnostics for debugging the transport boundary."""


ESPHOME_TX_ACTION = "proflame2_tx_stateful"
ESPHOME_DISPLAY_STATE_UPDATE_ACTION = "proflame2_display_state_update"
ESPHOME_RX_SET_ACTIVE_LISTENING_ACTION = "proflame2_rx_set_active_listening"
ESPHOME_RX_STOP_ACTION = "proflame2_rx_stop"
ESPHOME_RX_END_CONFIRMATION_ACTION = "proflame2_rx_end_confirmation"
ESPHOME_LEARN_MODE_UPDATE_ACTION = "proflame2_learn_mode_update"
ESPHOME_STATUS_ENTITY_OBJECT_ID = "proflame2_endpoint_status"
ESPHOME_LAST_ERROR_ENTITY_OBJECT_ID = "proflame2_last_error"
ESPHOME_LAST_TX_RESULT_ENTITY_OBJECT_ID = "proflame2_last_tx_result"
ESPHOME_LAST_REQUEST_ID_ENTITY_OBJECT_ID = "proflame2_last_request_id"
ESPHOME_LAST_TX_PATH_ENTITY_OBJECT_ID = "proflame2_last_tx_path"
ESPHOME_TX_SUCCESS_COUNT_ENTITY_OBJECT_ID = "proflame2_tx_success_count"
ESPHOME_TX_FAILURE_COUNT_ENTITY_OBJECT_ID = "proflame2_tx_failure_count"
ESPHOME_LAST_PAYLOAD_LENGTH_ENTITY_OBJECT_ID = "proflame2_last_payload_length"
ESPHOME_TX_REPEAT_COUNT_ENTITY_OBJECT_ID = "proflame2_tx_repeat_count"
ESPHOME_LAST_REQUEST_REPEAT_COUNT_ENTITY_OBJECT_ID = "proflame2_last_request_repeat_count"
ESPHOME_LAST_TX_ELAPSED_MS_ENTITY_OBJECT_ID = "proflame2_last_tx_elapsed_ms"
ESPHOME_FIRMWARE_PROTOCOL_VERSION_ENTITY_OBJECT_ID = "proflame2_firmware_protocol_version"
ESPHOME_CONFIG_REVISION_ENTITY_OBJECT_ID = "proflame2_config_revision"
ESPHOME_LAST_PAYLOAD_HEX_ENTITY_OBJECT_ID = "proflame2_last_payload_hex"
ESPHOME_LAST_MARCSTATE_BEFORE_TX_ENTITY_OBJECT_ID = "proflame2_last_marcstate_before_tx"
ESPHOME_LAST_MARCSTATE_AFTER_TX_ENTITY_OBJECT_ID = "proflame2_last_marcstate_after_tx"
ESPHOME_CC1101_PARTNUM_ENTITY_OBJECT_ID = "proflame2_cc1101_partnum"
ESPHOME_CC1101_VERSION_ENTITY_OBJECT_ID = "proflame2_cc1101_version"
ESPHOME_RX_PACKET_COUNT_ENTITY_OBJECT_ID = "proflame2_rx_packet_count"
ESPHOME_RX_DROPPED_PACKET_COUNT_ENTITY_OBJECT_ID = "proflame2_rx_dropped_packets"
ESPHOME_RX_NO_RF_CAPTURE_COUNT_ENTITY_OBJECT_ID = "proflame2_rx_no_rf_captures"
ESPHOME_RX_INCOMPLETE_FIFO_COUNT_ENTITY_OBJECT_ID = "proflame2_rx_incomplete_fifo_captures"
ESPHOME_RX_DECODE_FAILED_COUNT_ENTITY_OBJECT_ID = "proflame2_rx_decode_failures"
ESPHOME_RX_PROFILE_MISMATCH_COUNT_ENTITY_OBJECT_ID = "proflame2_rx_profile_mismatches"
ESPHOME_RX_ACCEPTED_PACKET_COUNT_ENTITY_OBJECT_ID = "proflame2_rx_accepted_packets"
ESPHOME_RX_TX_SUPPRESSED_COUNT_ENTITY_OBJECT_ID = "proflame2_rx_tx_suppressed"
ESPHOME_RX_TRANSPORT_UNAVAILABLE_COUNT_ENTITY_OBJECT_ID = "proflame2_rx_transport_unavailable"
ESPHOME_RX_LAST_REJECTION_SNAPSHOT_ENTITY_OBJECT_ID = "proflame2_rx_last_rejection_snapshot"
ESPHOME_RX_EVENT_TYPE = "esphome.proflame2_rx_packet"
_SUPPORTED_RX_SCHEMA_VERSIONS = {"1", "2"}
_SUPPORTED_RX_EVENT_KINDS = {"rx_packet", "rx_debug_sample", "fifo_capture"}
_RX_CAPTURE_METADATA_STRING_KEYS = (
    "schema_version",
    "protocol",
    "event_kind",
    "qualifier",
    "capture_meta",
    "raw_timing_summary",
    "artifact_class",
    "source",
    "capture_mode",
    "profile",
    "stop_reason",
)
_RX_CAPTURE_METADATA_VALUE_KEYS = (
    "accepted",
    "reject_reason",
    "byte_count",
    "trailing_window_complete",
    "insufficient_trailing_window",
    "rx_fifo_overflow",
    "rolling_history_overflow",
    "dropped_required_window_byte",
    "post_last_byte_quiet_ms",
    "remote_id",
    "cmd1",
    "cmd2",
    "err1",
    "err2",
    "power",
    "flame",
    "fan",
    "light",
    "front",
    "aux",
    "thermostat",
    "cpi",
    "repeat_count",
    "confidence",
    "bit_offset",
    "symbol_offset",
    "absolute_bit_offset",
    "ones_count",
    "zeros_count",
    "transition_count",
    "longest_run_0",
    "longest_run_1",
    "first_32_bits",
    "last_32_bits",
)


@dataclass(frozen=True, slots=True)
class _ParsedESPHomeRXEvent:
    """One accepted ESPHome bus event converted to the backend RX queue form."""

    event_kind: str
    packet_count: int | None
    payload_hex: str
    event: ESPHomeRXEvent


@dataclass(slots=True)
class _TelemetrySnapshot:
    status_text: str | None = None
    last_error: str | None = None
    last_tx_result_text: str | None = None
    last_request_id: str | None = None
    last_tx_path: str | None = None
    last_payload_length: int | None = None
    last_request_repeat_count: int | None = None
    last_tx_elapsed_ms: int | None = None
    last_payload_hex: str | None = None
    last_marcstate_before_tx: str | None = None
    last_marcstate_after_tx: str | None = None
    cc1101_partnum: str | None = None
    cc1101_version: str | None = None
    tx_success_count: int = 0
    tx_failure_count: int = 0
    firmware_protocol_version: int | None = None
    config_revision: int | None = None
    rx_packet_count: int = 0
    rx_dropped_packet_count: int = 0
    rx_no_rf_capture_count: int = 0
    rx_incomplete_fifo_count: int = 0
    rx_decode_failed_count: int = 0
    rx_profile_mismatch_count: int = 0
    rx_accepted_packet_count: int = 0
    rx_tx_suppressed_count: int = 0
    rx_transport_unavailable_count: int = 0
    rx_last_rejection_snapshot: str | None = None


class HomeAssistantESPHomeTransport:
    """Transport that reuses a linked Home Assistant ESPHome config entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        linked_entry_id: str,
        controller_id: str,
        debug_logging_enabled: bool,
        action_name: str = ESPHOME_TX_ACTION,
        observation_timeout_seconds: float = 5.0,
        poll_interval_seconds: float = 0.25,
    ) -> None:
        self.hass = hass
        self.linked_entry_id = linked_entry_id
        self.controller_id = controller_id
        self.debug_logging_enabled = debug_logging_enabled
        self.action_name = action_name
        self.observation_timeout_seconds = observation_timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.connected = False
        self.configured = False
        self.connect_count = 0
        self.close_count = 0
        self.configurations: list[ESPHomeRadioConfig] = []
        self.tx_requests: list[ESPHomeTXRequest] = []
        self.tx_responses: list[ESPHomeTXResponse] = []
        self.display_state_updates: list[ESPHomeDisplayState] = []
        self.last_error: str | None = None
        self.last_snapshot: _TelemetrySnapshot | None = None
        self._linked_entry = None
        self._runtime_data = None
        self._service = None
        self._display_state_service = None
        self._rx_policy_service = None
        self._rx_stop_service = None
        self._rx_end_confirmation_service = None
        self._learn_mode_update_service = None
        self._active_listening_enabled = False
        self._rx_queue: asyncio.Queue[ESPHomeRXEvent] = asyncio.Queue()
        self._rx_unsub = None
        self._linked_controller_device_id: str | None = None
        self._rx_event_transport_ready = False
        self.can_receive = False

    async def connect(self) -> None:
        self._log_debug(
            "connect start controller_id=%s linked_entry_id=%s action_name=%s",
            self.controller_id,
            self.linked_entry_id,
            self.action_name,
        )
        self._linked_entry = self.hass.config_entries.async_get_entry(self.linked_entry_id)
        if self._linked_entry is None:
            raise RuntimeError(f"Linked ESPHome entry not found: {self.linked_entry_id}")
        if self._linked_entry.domain != "esphome":
            raise RuntimeError(f"Linked entry is not an ESPHome config entry: {self.linked_entry_id}")
        runtime_data = getattr(self._linked_entry, "runtime_data", None)
        if runtime_data is None:
            raise RuntimeError(f"Linked ESPHome entry is not loaded or has no runtime_data: {self.linked_entry_id}")

        service = _find_user_service(getattr(runtime_data, "services", None), self.action_name)
        if service is None:
            raise RuntimeError(f"Linked ESPHome entry does not expose action {self.action_name!r}")

        self._runtime_data = runtime_data
        self._service = service
        self._display_state_service = _find_user_service(
            getattr(runtime_data, "services", None), ESPHOME_DISPLAY_STATE_UPDATE_ACTION
        )
        self._rx_policy_service = _find_user_service(
            getattr(runtime_data, "services", None), ESPHOME_RX_SET_ACTIVE_LISTENING_ACTION
        )
        self._rx_stop_service = _find_user_service(getattr(runtime_data, "services", None), ESPHOME_RX_STOP_ACTION)
        self._rx_end_confirmation_service = _find_user_service(
            getattr(runtime_data, "services", None), ESPHOME_RX_END_CONFIRMATION_ACTION
        )
        self._learn_mode_update_service = _find_user_service(
            getattr(runtime_data, "services", None), ESPHOME_LEARN_MODE_UPDATE_ACTION
        )
        self._linked_controller_device_id = self._resolve_linked_controller_device_id()
        self._rx_event_transport_ready = (
            self._linked_controller_device_id is not None
            and self._rx_policy_service is not None
            and self._rx_stop_service is not None
        )
        if self._rx_event_transport_ready:
            self._subscribe_rx_events()
        self.last_snapshot = self._collect_snapshot()
        self.can_receive = self._rx_event_transport_ready
        self.connected = True
        self.connect_count += 1
        self._log_debug(
            "connect complete controller_id=%s linked_entry_id=%s snapshot=%s",
            self.controller_id,
            self.linked_entry_id,
            self.serialize_diagnostics(),
        )

    async def configure_radio(self, config: ESPHomeRadioConfig) -> None:
        if not self.connected:
            raise RuntimeError("ESPHome transport must be connected before configure_radio().")
        self.configurations.append(config)
        self.configured = True
        snapshot = self._collect_snapshot()
        self.last_snapshot = snapshot

        if snapshot.config_revision is not None and snapshot.config_revision != config.config_revision:
            raise RuntimeError("Linked ESPHome endpoint config revision does not match Proflame2 expectations.")
        if (
            snapshot.firmware_protocol_version is not None
            and snapshot.firmware_protocol_version != config.firmware_protocol_version
        ):
            raise RuntimeError(
                "Linked ESPHome endpoint firmware protocol version does not match Proflame2 expectations."
            )
        if (
            snapshot.last_request_repeat_count is not None
            and snapshot.last_request_repeat_count != 0
            and snapshot.last_request_repeat_count != config.tx_repeat_count
        ):
            self._log_debug(
                "configure_radio tolerated repeat-count telemetry mismatch controller_id=%s linked_entry_id=%s configured_repeat_count=%s observed_repeat_count=%s",
                self.controller_id,
                self.linked_entry_id,
                config.tx_repeat_count,
                snapshot.last_request_repeat_count,
            )

    async def send_tx(self, request: ESPHomeTXRequest) -> ESPHomeTXResponse:
        if not self.connected or not self.configured:
            raise RuntimeError("ESPHome backend is unavailable; transport is not configured.")
        assert self._runtime_data is not None
        assert self._service is not None

        repeat_count = request.repeat_count
        if repeat_count is None and self.configurations:
            repeat_count = self.configurations[-1].tx_repeat_count
        if repeat_count is None:
            repeat_count = 5

        display_state = request.display_state
        payload = {
            "request_id": request.request_id,
            "air_payload_hex": request.air_payload_hex,
            "payload_bit_length": request.air_payload_bit_length,
            "repeat_count": repeat_count,
            "status_text": display_state.status_text if display_state and display_state.status_text else "Sending...",
            "intended_power": (
                1
                if display_state and display_state.power is True
                else 0 if display_state and display_state.power is False else -1
            ),
            "intended_flame": display_state.flame if display_state and display_state.flame is not None else -1,
            "intended_fan": display_state.fan if display_state and display_state.fan is not None else -1,
            "intended_light": display_state.light if display_state and display_state.light is not None else -1,
            "intended_pilot": display_state.pilot if display_state and display_state.pilot is not None else -1,
            "intended_thermostat": (
                1
                if display_state and display_state.thermostat is True
                else 0 if display_state and display_state.thermostat is False else -1
            ),
            "intended_front": (
                1
                if display_state and display_state.front is True
                else 0 if display_state and display_state.front is False else -1
            ),
            "intended_aux": (
                1
                if display_state and display_state.aux is True
                else 0 if display_state and display_state.aux is False else -1
            ),
            "intended_action_label": display_state.action_label if display_state and display_state.action_label else "",
            "fireplace_name": display_state.fireplace_name if display_state and display_state.fireplace_name else "",
        }
        pre_snapshot = self._collect_snapshot()
        self.tx_requests.append(request)
        self._drain_rx_queue()
        self._log_debug(
            "send_tx invoke controller_id=%s linked_entry_id=%s action_name=%s request_id=%s payload_length=%s payload_bit_length=%s repeat_count=%s air_payload_hex=%s",
            self.controller_id,
            self.linked_entry_id,
            self.action_name,
            request.request_id,
            len(request.air_payload),
            request.air_payload_bit_length,
            repeat_count,
            request.air_payload_hex,
        )
        await self._runtime_data.client.execute_service(self._service, payload)

        observed = await self._wait_for_request_confirmation(
            request.request_id,
            len(request.air_payload),
            repeat_count,
            pre_snapshot=pre_snapshot,
        )
        self.last_snapshot = observed

        ok = observed.last_tx_result_text == "ok"
        error_message = observed.last_error if not ok else None
        response = ESPHomeTXResponse(
            request_id=request.request_id,
            ok=ok,
            payload_length=observed.last_payload_length or len(request.air_payload),
            frames_sent=repeat_count,
            elapsed_ms=observed.last_tx_elapsed_ms,
            error_code=None if ok else observed.last_tx_result_text,
            error_message=error_message,
            radio_status=observed.status_text,
        )
        self.tx_responses.append(response)
        self.last_error = error_message
        self._log_debug(
            "send_tx confirmed controller_id=%s request_id=%s last_tx_result=%s payload_length=%s repeat_count=%s last_tx_path=%s marcstate_before=%s marcstate_after=%s partnum=%s version=%s last_payload_hex=%s",
            self.controller_id,
            request.request_id,
            observed.last_tx_result_text,
            observed.last_payload_length,
            observed.last_request_repeat_count,
            observed.last_tx_path,
            observed.last_marcstate_before_tx,
            observed.last_marcstate_after_tx,
            observed.cc1101_partnum,
            observed.cc1101_version,
            observed.last_payload_hex,
        )
        return response

    async def update_display_state(self, display_state: ESPHomeDisplayState) -> None:
        if not self.connected or not self.configured:
            raise RuntimeError("ESPHome backend is unavailable; transport is not configured.")
        assert self._runtime_data is not None
        if self._display_state_service is None:
            raise RuntimeError(f"Linked ESPHome entry does not expose action {ESPHOME_DISPLAY_STATE_UPDATE_ACTION!r}")
        payload = {
            "intended_power": 1 if display_state.power is True else 0 if display_state.power is False else -1,
            "intended_flame": display_state.flame if display_state.flame is not None else -1,
            "intended_fan": display_state.fan if display_state.fan is not None else -1,
            "intended_light": display_state.light if display_state.light is not None else -1,
            "intended_pilot": display_state.pilot if display_state.pilot is not None else -1,
            "intended_thermostat": (
                1 if display_state.thermostat is True else 0 if display_state.thermostat is False else -1
            ),
            "intended_front": 1 if display_state.front is True else 0 if display_state.front is False else -1,
            "intended_aux": 1 if display_state.aux is True else 0 if display_state.aux is False else -1,
            "intended_action_label": display_state.action_label or "",
            "fireplace_name": display_state.fireplace_name or "",
        }
        self._log_debug(
            "display_state_update invoke controller_id=%s linked_entry_id=%s action_name=%s payload=%s",
            self.controller_id,
            self.linked_entry_id,
            ESPHOME_DISPLAY_STATE_UPDATE_ACTION,
            payload,
        )
        await self._runtime_data.client.execute_service(self._display_state_service, payload)
        self.last_snapshot = self._collect_snapshot()

    async def set_active_listening(self, enabled: bool, profile: Any | None = None) -> None:
        if not self.connected or not self.configured:
            raise RuntimeError("ESPHome backend is unavailable; transport is not configured.")
        assert self._runtime_data is not None
        if self._rx_policy_service is None:
            raise RuntimeError(
                f"Linked ESPHome entry does not expose action {ESPHOME_RX_SET_ACTIVE_LISTENING_ACTION!r}"
            )
        self._active_listening_enabled = enabled
        if enabled:
            self._drain_rx_queue()
        self._log_debug(
            "rx policy invoke controller_id=%s linked_entry_id=%s action_name=%s enabled=%s",
            self.controller_id,
            self.linked_entry_id,
            ESPHOME_RX_SET_ACTIVE_LISTENING_ACTION,
            enabled,
        )
        ecc = getattr(profile, "ecc", None)
        payload = {
            "enabled": 1 if enabled else 0,
            "serial_id": int(getattr(profile, "serial_id", 0) or 0),
            "c1": int(getattr(ecc, "c1", 0) or 0),
            "d1": int(getattr(ecc, "d1", 0) or 0),
            "c2": int(getattr(ecc, "c2", 0) or 0),
            "d2": int(getattr(ecc, "d2", 0) or 0),
        }
        await self._runtime_data.client.execute_service(self._rx_policy_service, payload)
        self.last_snapshot = self._collect_snapshot()
        self.can_receive = self._rx_event_transport_ready

    async def stop_rx(self) -> None:
        if not self.connected or not self.configured:
            return
        assert self._runtime_data is not None
        if self._rx_stop_service is None:
            return
        self._log_debug(
            "rx stop invoke controller_id=%s linked_entry_id=%s action_name=%s",
            self.controller_id,
            self.linked_entry_id,
            ESPHOME_RX_STOP_ACTION,
        )
        await self._runtime_data.client.execute_service(self._rx_stop_service, {})
        self._active_listening_enabled = False
        self._drain_rx_queue()
        self.last_snapshot = self._collect_snapshot()

    async def end_confirmation_rx(self) -> None:
        if not self.connected or not self.configured:
            return
        assert self._runtime_data is not None
        if self._rx_end_confirmation_service is None:
            return
        self._log_debug(
            "rx end confirmation invoke controller_id=%s linked_entry_id=%s action_name=%s",
            self.controller_id,
            self.linked_entry_id,
            ESPHOME_RX_END_CONFIRMATION_ACTION,
        )
        await self._runtime_data.client.execute_service(self._rx_end_confirmation_service, {})
        self.last_snapshot = self._collect_snapshot()

    async def update_learning_mode(
        self,
        *,
        active: bool,
        step_title: str,
        instruction: str,
        status: str,
    ) -> None:
        if not self.connected or not self.configured:
            raise RuntimeError("ESPHome backend is unavailable; transport is not configured.")
        assert self._runtime_data is not None
        if self._learn_mode_update_service is None:
            raise RuntimeError(f"Linked ESPHome entry does not expose action {ESPHOME_LEARN_MODE_UPDATE_ACTION!r}")
        payload = {
            "active": 1 if active else 0,
            "step_title": step_title,
            "instruction": instruction,
            "status": status,
        }
        self._log_debug(
            "learn mode update invoke controller_id=%s linked_entry_id=%s action_name=%s payload=%s",
            self.controller_id,
            self.linked_entry_id,
            ESPHOME_LEARN_MODE_UPDATE_ACTION,
            payload,
        )
        await self._runtime_data.client.execute_service(self._learn_mode_update_service, payload)

    async def get_status(self) -> ESPHomeEndpointStatusReport:
        if self._runtime_data is None:
            raise RuntimeError("ESPHome transport is not connected.")
        snapshot = self._collect_snapshot()
        self.last_snapshot = snapshot
        status = _status_from_snapshot(snapshot, getattr(self._runtime_data, "available", False))
        self.can_receive = self._rx_event_transport_ready
        firmware_version = None
        device_info = getattr(self._runtime_data, "device_info", None)
        if device_info is not None:
            firmware_version = getattr(device_info, "esphome_version", None)

        return ESPHomeEndpointStatusReport(
            status=status,
            configured=self.configured,
            config_revision=snapshot.config_revision,
            firmware_protocol_version=snapshot.firmware_protocol_version,
            last_error=snapshot.last_error,
            last_tx_result=self.tx_responses[-1] if self.tx_responses else None,
            tx_success_count=snapshot.tx_success_count,
            tx_failure_count=snapshot.tx_failure_count,
            rx_packet_count=snapshot.rx_packet_count,
            ip_address=self._linked_entry.data.get(CONF_HOST) if self._linked_entry else None,
            firmware_version=firmware_version,
        )

    async def receive_rx_event(self, timeout: float | None = None) -> ESPHomeRXEvent | None:
        if not self.connected or not self.configured or not self._rx_event_transport_ready:
            return None
        try:
            if timeout is None:
                return await self._rx_queue.get()
            return await asyncio.wait_for(self._rx_queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    async def close(self) -> None:
        if self._rx_unsub is not None:
            self._rx_unsub()
            self._rx_unsub = None
        self.connected = False
        self.configured = False
        self._runtime_data = None
        self._linked_entry = None
        self._service = None
        self._display_state_service = None
        self._rx_policy_service = None
        self._rx_stop_service = None
        self._rx_end_confirmation_service = None
        self._learn_mode_update_service = None
        self._linked_controller_device_id = None
        self._active_listening_enabled = False
        self._rx_event_transport_ready = False
        self.can_receive = False
        self._drain_rx_queue()
        self.close_count += 1

    def serialize_diagnostics(self) -> dict[str, Any]:
        snapshot = self.last_snapshot or _TelemetrySnapshot()
        linked_entry = self._linked_entry
        return {
            "class": self.__class__.__name__,
            "controller_id": self.controller_id,
            "linked_entry_id": self.linked_entry_id,
            "linked_entry_loaded": linked_entry is not None,
            "linked_entry_available": (
                getattr(self._runtime_data, "available", None) if self._runtime_data is not None else None
            ),
            "action_name": self.action_name,
            "connected": self.connected,
            "configured": self.configured,
            "connect_count": self.connect_count,
            "close_count": self.close_count,
            "tx_request_count": len(self.tx_requests),
            "tx_response_count": len(self.tx_responses),
            "last_error": self.last_error,
            "last_request_id": snapshot.last_request_id,
            "last_tx_path": snapshot.last_tx_path,
            "last_payload_length": snapshot.last_payload_length,
            "last_request_repeat_count": snapshot.last_request_repeat_count,
            "last_tx_elapsed_ms": snapshot.last_tx_elapsed_ms,
            "last_payload_hex": snapshot.last_payload_hex,
            "last_marcstate_before_tx": snapshot.last_marcstate_before_tx,
            "last_marcstate_after_tx": snapshot.last_marcstate_after_tx,
            "cc1101_partnum": snapshot.cc1101_partnum,
            "cc1101_version": snapshot.cc1101_version,
            "last_tx_result_text": snapshot.last_tx_result_text,
            "status_text": snapshot.status_text,
            "tx_success_count": snapshot.tx_success_count,
            "tx_failure_count": snapshot.tx_failure_count,
            "config_revision": snapshot.config_revision,
            "firmware_protocol_version": snapshot.firmware_protocol_version,
            "rx_event_transport_ready": self._rx_event_transport_ready,
            "can_receive": self.can_receive,
            "linked_controller_device_id": self._linked_controller_device_id,
            "active_listening_enabled": self._active_listening_enabled,
            "rx_queue_depth": self._rx_queue.qsize(),
            "rx_dropped_packet_count": snapshot.rx_dropped_packet_count,
            "rx_no_rf_capture_count": snapshot.rx_no_rf_capture_count,
            "rx_incomplete_fifo_count": snapshot.rx_incomplete_fifo_count,
            "rx_decode_failed_count": snapshot.rx_decode_failed_count,
            "rx_profile_mismatch_count": snapshot.rx_profile_mismatch_count,
            "rx_accepted_packet_count": snapshot.rx_accepted_packet_count,
            "rx_tx_suppressed_count": snapshot.rx_tx_suppressed_count,
            "rx_transport_unavailable_count": snapshot.rx_transport_unavailable_count,
            "rx_last_rejection_snapshot": snapshot.rx_last_rejection_snapshot,
            "host": linked_entry.data.get(CONF_HOST) if linked_entry is not None else None,
            "port": linked_entry.data.get(CONF_PORT) if linked_entry is not None else None,
        }

    async def _wait_for_request_confirmation(
        self,
        request_id: str,
        expected_payload_length: int,
        expected_repeat_count: int,
        *,
        pre_snapshot: _TelemetrySnapshot,
    ) -> _TelemetrySnapshot:
        deadline = asyncio.get_running_loop().time() + self.observation_timeout_seconds
        while True:
            snapshot = self._collect_snapshot()
            if (
                snapshot.last_request_id == request_id
                and snapshot.last_payload_length == expected_payload_length
                and (
                    snapshot.tx_success_count > pre_snapshot.tx_success_count
                    or snapshot.tx_failure_count > pre_snapshot.tx_failure_count
                    or snapshot.last_tx_result_text == "ok"
                    or (snapshot.last_tx_result_text is not None and snapshot.last_tx_result_text.startswith("error:"))
                )
            ):
                if (
                    snapshot.last_request_repeat_count is not None
                    and snapshot.last_request_repeat_count != 0
                    and snapshot.last_request_repeat_count != expected_repeat_count
                ):
                    self._log_debug(
                        "send_tx confirmation tolerated repeat-count telemetry mismatch controller_id=%s request_id=%s expected_repeat_count=%s observed_repeat_count=%s",
                        self.controller_id,
                        request_id,
                        expected_repeat_count,
                        snapshot.last_request_repeat_count,
                    )
                return snapshot
            if asyncio.get_running_loop().time() >= deadline:
                raise RuntimeError("ESPHome TX action executed but endpoint telemetry did not confirm the request.")
            await asyncio.sleep(self.poll_interval_seconds)

    def _collect_snapshot(self) -> _TelemetrySnapshot:
        runtime_data = self._runtime_data
        if runtime_data is None:
            return _TelemetrySnapshot()

        info_by_key: dict[int, Any] = {}
        for info_map in getattr(runtime_data, "info", {}).values():
            if isinstance(info_map, Mapping):
                for key, info in info_map.items():
                    entity_key = key[1] if isinstance(key, tuple) else getattr(info, "key", key)
                    info_by_key[entity_key] = info

        def text_value(object_id: str) -> str | None:
            state = _find_state_for_object_id(runtime_data, info_by_key, object_id)
            if state is None:
                return None
            value = getattr(state, "state", None)
            if value is None:
                return None
            text = str(value)
            return text or None

        def int_value(object_id: str) -> int | None:
            value = text_value(object_id)
            if value is None:
                return None
            try:
                return int(float(value))
            except ValueError:
                return None

        return _TelemetrySnapshot(
            status_text=text_value(ESPHOME_STATUS_ENTITY_OBJECT_ID),
            last_error=text_value(ESPHOME_LAST_ERROR_ENTITY_OBJECT_ID),
            last_tx_result_text=text_value(ESPHOME_LAST_TX_RESULT_ENTITY_OBJECT_ID),
            last_request_id=text_value(ESPHOME_LAST_REQUEST_ID_ENTITY_OBJECT_ID),
            last_tx_path=text_value(ESPHOME_LAST_TX_PATH_ENTITY_OBJECT_ID),
            last_payload_length=int_value(ESPHOME_LAST_PAYLOAD_LENGTH_ENTITY_OBJECT_ID),
            last_request_repeat_count=int_value(ESPHOME_LAST_REQUEST_REPEAT_COUNT_ENTITY_OBJECT_ID),
            last_tx_elapsed_ms=int_value(ESPHOME_LAST_TX_ELAPSED_MS_ENTITY_OBJECT_ID),
            last_payload_hex=text_value(ESPHOME_LAST_PAYLOAD_HEX_ENTITY_OBJECT_ID),
            last_marcstate_before_tx=text_value(ESPHOME_LAST_MARCSTATE_BEFORE_TX_ENTITY_OBJECT_ID),
            last_marcstate_after_tx=text_value(ESPHOME_LAST_MARCSTATE_AFTER_TX_ENTITY_OBJECT_ID),
            cc1101_partnum=text_value(ESPHOME_CC1101_PARTNUM_ENTITY_OBJECT_ID),
            cc1101_version=text_value(ESPHOME_CC1101_VERSION_ENTITY_OBJECT_ID),
            tx_success_count=int_value(ESPHOME_TX_SUCCESS_COUNT_ENTITY_OBJECT_ID) or 0,
            tx_failure_count=int_value(ESPHOME_TX_FAILURE_COUNT_ENTITY_OBJECT_ID) or 0,
            rx_packet_count=int_value(ESPHOME_RX_PACKET_COUNT_ENTITY_OBJECT_ID) or 0,
            rx_dropped_packet_count=int_value(ESPHOME_RX_DROPPED_PACKET_COUNT_ENTITY_OBJECT_ID) or 0,
            rx_no_rf_capture_count=int_value(ESPHOME_RX_NO_RF_CAPTURE_COUNT_ENTITY_OBJECT_ID) or 0,
            rx_incomplete_fifo_count=int_value(ESPHOME_RX_INCOMPLETE_FIFO_COUNT_ENTITY_OBJECT_ID) or 0,
            rx_decode_failed_count=int_value(ESPHOME_RX_DECODE_FAILED_COUNT_ENTITY_OBJECT_ID) or 0,
            rx_profile_mismatch_count=int_value(ESPHOME_RX_PROFILE_MISMATCH_COUNT_ENTITY_OBJECT_ID) or 0,
            rx_accepted_packet_count=int_value(ESPHOME_RX_ACCEPTED_PACKET_COUNT_ENTITY_OBJECT_ID) or 0,
            rx_tx_suppressed_count=int_value(ESPHOME_RX_TX_SUPPRESSED_COUNT_ENTITY_OBJECT_ID) or 0,
            rx_transport_unavailable_count=int_value(ESPHOME_RX_TRANSPORT_UNAVAILABLE_COUNT_ENTITY_OBJECT_ID) or 0,
            rx_last_rejection_snapshot=text_value(ESPHOME_RX_LAST_REJECTION_SNAPSHOT_ENTITY_OBJECT_ID),
            firmware_protocol_version=int_value(ESPHOME_FIRMWARE_PROTOCOL_VERSION_ENTITY_OBJECT_ID),
            config_revision=int_value(ESPHOME_CONFIG_REVISION_ENTITY_OBJECT_ID),
        )

    def _resolve_linked_controller_device_id(self) -> str | None:
        registry = dr.async_get(self.hass)
        devices = dr.async_entries_for_config_entry(registry, self.linked_entry_id)
        if not devices:
            return None
        ranked = sorted(
            devices,
            key=lambda device: (
                0 if device.via_device_id is None else 1,
                0 if device.connections else 1,
                0 if device.identifiers else 1,
                device.name_by_user or device.name or "",
            ),
        )
        return ranked[0].id

    def _subscribe_rx_events(self) -> None:
        if self._rx_unsub is not None:
            return

        @callback
        def _handle_state_changed(event) -> None:
            if event.event_type != ESPHOME_RX_EVENT_TYPE:
                return
            parsed = self._build_rx_event_from_bus_data(event.data or {})
            if parsed is None:
                return
            self._rx_queue.put_nowait(parsed.event)
            self._log_debug(
                "rx event accepted controller_id=%s event_kind=%s packet_count=%s payload_length_bytes=%s payload_hex_preview=%s",
                self.controller_id,
                parsed.event_kind,
                parsed.packet_count,
                len(parsed.event.raw_payload),
                _abbreviate_hex(parsed.payload_hex),
            )

        self._rx_unsub = self.hass.bus.async_listen(ESPHOME_RX_EVENT_TYPE, _handle_state_changed)

    def _build_rx_event_from_bus_data(self, event_data: Mapping[str, Any]) -> _ParsedESPHomeRXEvent | None:
        """Validate and convert one Home Assistant bus payload into an RX event."""

        if not self._event_matches_linked_device(event_data):
            return None
        event_kind = self._supported_event_kind(event_data)
        if event_kind is None:
            return None
        raw_payload = self._raw_payload_from_event_data(event_data)
        if raw_payload is None:
            return None
        packet_count = _int_from_event_value(event_data.get("packet_count"))
        payload_hex = event_data["payload_hex"]
        return _ParsedESPHomeRXEvent(
            event_kind=event_kind,
            packet_count=packet_count,
            payload_hex=payload_hex,
            event=ESPHomeRXEvent(
                event_id=self._rx_event_id(packet_count),
                raw_payload=raw_payload,
                device_tick_ms=_int_from_event_value(event_data.get("device_tick_ms")),
                rssi=_float_from_event_value(event_data.get("rssi")),
                lqi=_int_from_event_value(event_data.get("lqi")),
                frequency_hz=_int_from_event_value(event_data.get("freq_hz")),
                capture_metadata=self._capture_metadata_from_event_data(event_data),
            ),
        )

    def _event_matches_linked_device(self, event_data: Mapping[str, Any]) -> bool:
        """Return whether the bus event belongs to the linked ESPHome device."""

        linked_device_id = self._linked_controller_device_id
        event_device_id = event_data.get(ATTR_DEVICE_ID)
        if not isinstance(event_device_id, str):
            self._log_debug(
                "rx event ignored controller_id=%s reason=missing_device_id payload=%s",
                self.controller_id,
                event_data,
            )
            return False
        if linked_device_id is None or event_device_id != linked_device_id:
            self._log_debug(
                "rx event ignored controller_id=%s reason=wrong_device_id expected=%s got=%s",
                self.controller_id,
                linked_device_id,
                event_device_id,
            )
            return False
        return True

    def _supported_event_kind(self, event_data: Mapping[str, Any]) -> str | None:
        """Return the supported Proflame2 RX event kind or log why it was rejected."""

        event_kind = event_data.get("event_kind")
        if (
            event_data.get("schema_version") not in _SUPPORTED_RX_SCHEMA_VERSIONS
            or event_data.get("protocol") != "proflame2"
            or event_kind not in _SUPPORTED_RX_EVENT_KINDS
        ):
            self._log_debug(
                "rx event ignored controller_id=%s reason=unexpected_schema payload=%s",
                self.controller_id,
                event_data,
            )
            return None
        return str(event_kind)

    def _raw_payload_from_event_data(self, event_data: Mapping[str, Any]) -> bytes | None:
        """Return decoded FIFO/RX bytes from one event or log the rejection reason."""

        payload_hex = event_data.get("payload_hex")
        if not isinstance(payload_hex, str) or not payload_hex:
            self._log_debug(
                "rx event ignored controller_id=%s reason=missing_payload payload=%s",
                self.controller_id,
                event_data,
            )
            return None
        try:
            return bytes.fromhex(payload_hex)
        except ValueError:
            self._log_debug(
                "rx event ignored controller_id=%s reason=invalid_hex payload=%s",
                self.controller_id,
                event_data,
            )
            return None

    def _rx_event_id(self, packet_count: int | None) -> str:
        """Return a stable queue event id from firmware packet count or local time."""

        if packet_count is not None:
            return f"{self.controller_id}:rx:{packet_count}"
        return f"{self.controller_id}:rx:{asyncio.get_running_loop().time()}"

    def _capture_metadata_from_event_data(self, event_data: Mapping[str, Any]) -> dict[str, Any]:
        """Copy whitelisted firmware metadata into the queued RX event."""

        capture_metadata = {
            key: value for key in _RX_CAPTURE_METADATA_STRING_KEYS if (value := event_data.get(key)) is not None
        }
        for key in _RX_CAPTURE_METADATA_VALUE_KEYS:
            value = event_data.get(key)
            if value is not None:
                capture_metadata[key] = value
        return capture_metadata

    def _drain_rx_queue(self) -> None:
        while not self._rx_queue.empty():
            try:
                self._rx_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    def _log_debug(self, message: str, *args: object) -> None:
        """Emit transport debug logs only when explicitly enabled."""

        if not self.debug_logging_enabled:
            return
        _LOGGER.warning("Proflame2 ESPHome transport: " + message, *args)
        get_packet_debug_logger().warning("esphome_transport: " + message, *args)


class MockESPHomeTransport:
    """Deterministic in-memory ESPHome transport for backend tests."""

    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.connected = False
        self.configured = False
        self.can_receive = True
        self.connect_count = 0
        self.close_count = 0
        self.configurations: list[ESPHomeRadioConfig] = []
        self.tx_requests: list[ESPHomeTXRequest] = []
        self.tx_responses: list[ESPHomeTXResponse] = []
        self.display_state_updates: list[ESPHomeDisplayState] = []
        self.active_listening_updates: list[bool] = []
        self.rx_stop_count = 0
        self.rx_end_confirmation_count = 0
        self.learning_mode_updates: list[dict[str, object]] = []
        self.last_error: str | None = None
        self.active_listening_enabled = False
        self._rx_queue: asyncio.Queue[ESPHomeRXEvent] = asyncio.Queue()

    async def connect(self) -> None:
        if not self.available:
            self.last_error = "mock_transport_unavailable"
            raise RuntimeError("ESPHome backend is unavailable.")
        self.connected = True
        self.connect_count += 1

    async def configure_radio(self, config: ESPHomeRadioConfig) -> None:
        if not self.connected:
            raise RuntimeError("ESPHome transport must be connected before configure_radio().")
        self.configured = True
        self.configurations.append(config)

    async def send_tx(self, request: ESPHomeTXRequest) -> ESPHomeTXResponse:
        if not self.connected or not self.configured:
            raise RuntimeError("ESPHome backend is unavailable; transport is not configured.")
        self.tx_requests.append(request)
        config = self.configurations[-1]
        response = ESPHomeTXResponse(
            request_id=request.request_id,
            ok=True,
            payload_length=len(request.air_payload),
            frames_sent=request.repeat_count or config.tx_repeat_count,
            elapsed_ms=0,
            radio_status=ESPHomeEndpointStatus.READY.value,
        )
        self.tx_responses.append(response)
        return response

    async def update_display_state(self, display_state: ESPHomeDisplayState) -> None:
        if not self.connected or not self.configured:
            raise RuntimeError("ESPHome backend is unavailable; transport is not configured.")
        self.last_error = None
        self.display_state_updates.append(display_state)

    async def receive_rx_event(self, timeout: float | None = None) -> ESPHomeRXEvent | None:
        if not self.connected or not self.configured:
            return None
        try:
            if timeout is None:
                return await self._rx_queue.get()
            return await asyncio.wait_for(self._rx_queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    async def set_active_listening(self, enabled: bool, profile: Any | None = None) -> None:
        if not self.connected or not self.configured or not self.can_receive:
            raise RuntimeError("ESPHome backend is unavailable; transport is not configured.")
        self.active_listening_enabled = enabled
        self.can_receive = True
        if enabled:
            while not self._rx_queue.empty():
                try:
                    self._rx_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
        self.active_listening_updates.append(enabled)

    async def stop_rx(self) -> None:
        self.rx_stop_count += 1
        self.active_listening_enabled = False
        while not self._rx_queue.empty():
            try:
                self._rx_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def end_confirmation_rx(self) -> None:
        self.rx_end_confirmation_count += 1

    async def update_learning_mode(
        self,
        *,
        active: bool,
        step_title: str,
        instruction: str,
        status: str,
    ) -> None:
        if not self.connected or not self.configured:
            raise RuntimeError("ESPHome backend is unavailable; transport is not configured.")
        self.learning_mode_updates.append(
            {
                "active": active,
                "step_title": step_title,
                "instruction": instruction,
                "status": status,
            }
        )

    async def get_status(self) -> ESPHomeEndpointStatusReport:
        if not self.available:
            return ESPHomeEndpointStatusReport(
                status=ESPHomeEndpointStatus.FAULT,
                configured=False,
                last_error=self.last_error or "mock_transport_unavailable",
            )
        if not self.connected:
            return ESPHomeEndpointStatusReport(
                status=ESPHomeEndpointStatus.NOT_CONFIGURED,
                configured=False,
            )
        return ESPHomeEndpointStatusReport(
            status=ESPHomeEndpointStatus.READY if self.configured else ESPHomeEndpointStatus.NOT_CONFIGURED,
            configured=self.configured,
            config_revision=(self.configurations[-1].config_revision if self.configurations else None),
            firmware_protocol_version=(
                self.configurations[-1].firmware_protocol_version if self.configurations else None
            ),
            last_error=self.last_error,
            last_tx_result=self.tx_responses[-1] if self.tx_responses else None,
            tx_success_count=len(self.tx_responses),
            tx_failure_count=0,
            rx_packet_count=self._rx_queue.qsize(),
            firmware_version="mock",
        )

    async def close(self) -> None:
        self.connected = False
        while not self._rx_queue.empty():
            try:
                self._rx_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self.close_count += 1

    def serialize_diagnostics(self) -> dict[str, Any]:
        return {
            "class": self.__class__.__name__,
            "connected": self.connected,
            "configured": self.configured,
            "connect_count": self.connect_count,
            "close_count": self.close_count,
            "tx_request_count": len(self.tx_requests),
            "tx_response_count": len(self.tx_responses),
            "available": self.available,
            "last_error": self.last_error,
        }

    def push_rx_event(self, event: ESPHomeRXEvent) -> None:
        self._rx_queue.put_nowait(event)


def _int_state_value(state: Any) -> int | None:
    if state is None:
        return None
    value = getattr(state, "state", None)
    if value in (None, "", "unknown", "unavailable"):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _int_from_event_value(value: Any) -> int | None:
    if value in (None, "", "unknown", "unavailable"):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _float_from_event_value(value: Any) -> float | None:
    if value in (None, "", "unknown", "unavailable"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _find_user_service(services: Any, action_name: str) -> Any | None:
    if isinstance(services, Mapping):
        service_iterable = services.values()
    else:
        service_iterable = services or ()
    for service in service_iterable:
        if getattr(service, "name", None) == action_name:
            return service
    return None


def _find_state_for_object_id(runtime_data: Any, info_by_key: dict[int, Any], object_id: str) -> Any | None:
    for state_map in getattr(runtime_data, "state", {}).values():
        if not isinstance(state_map, Mapping):
            continue
        for key, state in state_map.items():
            info = info_by_key.get(key)
            if info is None:
                continue
            if getattr(info, "object_id", None) == object_id:
                return state
    return None


def _status_from_snapshot(snapshot: _TelemetrySnapshot, available: bool) -> ESPHomeEndpointStatus:
    raw = snapshot.status_text
    if raw:
        normalized = raw.strip().lower().replace("/", "_")
        if normalized in {
            "ready_fake_tx_only",
            "ready_tx_only",
            "ready_rx_supported",
            "ready_fifo_rx",
        }:
            return ESPHomeEndpointStatus.READY
        if normalized in {"ready_rx_confirmation", "ready_rx_listening"}:
            return ESPHomeEndpointStatus.RX_ACTIVE
        if normalized == "tx":
            return ESPHomeEndpointStatus.TX_ACTIVE
        for candidate in ESPHomeEndpointStatus:
            if normalized == candidate.value:
                return candidate
    if available:
        return ESPHomeEndpointStatus.READY
    return ESPHomeEndpointStatus.FAULT


def _abbreviate_hex(value: str, *, edge_chars: int = 32) -> str:
    """Return a log-safe preview of a potentially large hex payload."""

    if len(value) <= edge_chars * 2:
        return value
    return f"{value[:edge_chars]}...{value[-edge_chars:]}"
