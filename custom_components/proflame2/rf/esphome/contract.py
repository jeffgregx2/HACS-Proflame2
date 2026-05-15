"""Transport contract types for ESPHome/T-Embed CC1101 endpoints.

These models describe the boundary between the Home Assistant integration and
an ESPHome-based T-Embed CC1101 radio/display endpoint.  Home Assistant remains
the Proflame2 profile/state authority: it owns learning promotion, stored ECC
profiles, command encoding, transmission plan construction, and state policy.
The endpoint may use a HA-provided learned profile to perform bounded active
listening packet validation/filtering so it emits only matching Proflame2
packets instead of raw RF noise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ...protocol.packet import ProflamePacket
from ...protocol.profiles import DEFAULT_PROTOCOL_PROFILE, ProtocolModulation

DEFAULT_ESPHOME_FIRMWARE_PROTOCOL_VERSION = 1
DEFAULT_ESPHOME_CONFIG_REVISION = 1
DEFAULT_ESPHOME_TX_FREQUENCY_HZ = DEFAULT_PROTOCOL_PROFILE.frequency_hz
DEFAULT_ESPHOME_RX_FREQUENCY_HZ = (
    DEFAULT_PROTOCOL_PROFILE.default_rx_frequency_hz or DEFAULT_PROTOCOL_PROFILE.frequency_hz
)
DEFAULT_ESPHOME_DATA_RATE_BPS = DEFAULT_PROTOCOL_PROFILE.data_rate_bps
DEFAULT_ESPHOME_INTER_FRAME_GAP_MS = DEFAULT_PROTOCOL_PROFILE.inter_frame_gap_ms


class ESPHomeEndpointStatus(StrEnum):
    """Lifecycle/status values exposed by a T-Embed endpoint."""

    BOOTING = "booting"
    NOT_CONFIGURED = "not_configured"
    CONFIGURING = "configuring"
    READY = "ready"
    TX_ACTIVE = "tx_active"
    RX_ACTIVE = "rx_active"
    FAULT = "fault"
    SHUTTING_DOWN = "shutting_down"


ESPHomeModulation = ProtocolModulation


@dataclass(frozen=True)
class ESPHomeRadioConfig:
    """Persistent RF configuration applied to the endpoint.

    Internal radio details such as post-TX idle/RX transitions belong to the
    firmware implementation and are intentionally not part of this contract.
    """

    config_revision: int = DEFAULT_ESPHOME_CONFIG_REVISION
    firmware_protocol_version: int = DEFAULT_ESPHOME_FIRMWARE_PROTOCOL_VERSION
    tx_frequency_hz: int = DEFAULT_ESPHOME_TX_FREQUENCY_HZ
    rx_frequency_hz: int = DEFAULT_ESPHOME_RX_FREQUENCY_HZ
    modulation: ESPHomeModulation = DEFAULT_PROTOCOL_PROFILE.modulation
    data_rate_bps: int = DEFAULT_ESPHOME_DATA_RATE_BPS
    tx_repeat_count: int = DEFAULT_PROTOCOL_PROFILE.tx_repeat_count
    rx_enabled: bool = False
    inter_frame_gap_ms: float | None = DEFAULT_ESPHOME_INTER_FRAME_GAP_MS
    rx_bandwidth_hz: int | None = None
    sync_mode: str | None = None
    packet_mode: str | None = None
    debug_enabled: bool = False


@dataclass(frozen=True)
class ESPHomeDisplayState:
    """User-facing state that the endpoint may render on its display.

    These fields are display metadata only.  The endpoint must not infer
    authoritative fireplace state or generate RF payloads from them.
    """

    fireplace_name: str | None = None
    power: bool | None = None
    flame: int | None = None
    fan: int | None = None
    light: int | None = None
    pilot: int | None = None
    thermostat: bool | None = None
    front: bool | None = None
    aux: bool | None = None
    cpi: bool | None = None
    action_label: str | None = None
    status_text: str | None = None
    fault_text: str | None = None
    debug_enabled: bool = False


@dataclass(frozen=True)
class ESPHomeTXRequest:
    """Prepared transmit request for a T-Embed endpoint."""

    request_id: str
    air_payload: bytes
    air_payload_bit_length: int
    repeat_count: int | None = None
    remote_id: int | None = None
    cmd1: int | None = None
    err1: int | None = None
    cmd2: int | None = None
    err2: int | None = None
    display_state: ESPHomeDisplayState | None = None

    @property
    def air_payload_hex(self) -> str:
        """Return a stable hex representation for transports lacking bytes."""

        return self.air_payload.hex()

    @classmethod
    def from_packet(
        cls,
        packet: ProflamePacket,
        *,
        request_id: str,
        display_state: ESPHomeDisplayState | None = None,
        include_frame_metadata: bool = True,
    ) -> ESPHomeTXRequest:
        """Build a TX request from an HA-generated transmission plan."""

        if packet.transmission_plan is None:
            raise ValueError("ESPHome TX requests require packet.transmission_plan.")

        frame = packet.frame
        return cls(
            request_id=request_id,
            air_payload=bytes(packet.transmission_plan.air_payload),
            air_payload_bit_length=packet.transmission_plan.air_payload_bit_length,
            repeat_count=packet.transmission_plan.repeat_count,
            remote_id=frame.serial_id if include_frame_metadata else None,
            cmd1=frame.cmd1 if include_frame_metadata else None,
            err1=frame.err1 if include_frame_metadata else None,
            cmd2=frame.cmd2 if include_frame_metadata else None,
            err2=frame.err2 if include_frame_metadata else None,
            display_state=display_state,
        )


@dataclass(frozen=True)
class ESPHomeTXResponse:
    """Result of one prepared payload transmit operation."""

    request_id: str
    ok: bool
    payload_length: int
    frames_sent: int
    elapsed_ms: int | float | None = None
    error_code: str | None = None
    error_message: str | None = None
    radio_status: str | None = None


@dataclass(frozen=True)
class ESPHomeRXEvent:
    """Receive/FIFO event reported by the endpoint.

    Learning events may carry raw FIFO windows for HA-side candidate scanning.
    Active-listening events may carry firmware-filtered decoded packet metadata
    using the learned profile supplied by HA.
    """

    event_id: str
    raw_payload: bytes
    timestamp_ms: int | None = None
    device_tick_ms: int | None = None
    rssi: float | None = None
    lqi: int | None = None
    frequency_hz: int | None = None
    capture_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def raw_payload_hex(self) -> str:
        """Return a stable hex representation for transports lacking bytes."""

        return self.raw_payload.hex()


@dataclass(frozen=True)
class ESPHomeEndpointStatusReport:
    """Status snapshot exposed by a T-Embed endpoint."""

    status: ESPHomeEndpointStatus
    configured: bool
    config_revision: int | None = None
    firmware_protocol_version: int | None = None
    last_error: str | None = None
    last_tx_result: ESPHomeTXResponse | None = None
    last_rx_result: ESPHomeRXEvent | None = None
    tx_success_count: int = 0
    tx_failure_count: int = 0
    rx_packet_count: int = 0
    uptime_ms: int | None = None
    wifi_rssi: float | None = None
    ip_address: str | None = None
    firmware_version: str | None = None
