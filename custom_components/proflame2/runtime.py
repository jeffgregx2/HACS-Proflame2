"""Runtime state and backend helpers for the Proflame2 integration."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers import device_registry as dr

from proflame2_protocol.models import ECCProfile, FireplaceFeatures, RemoteProfile
from proflame2_protocol.packet import ProflamePacket
from proflame2_rf.base import RFBackend, SendResult
from proflame2_rf.fake import FakeRFBackend
from proflame2_rf.waveform import ProflameTransmissionPlan

from .const import (
    BACKEND_FAKE,
    BACKEND_YARDSTICK,
    CONF_AUX,
    CONF_BACKEND_TYPE,
    CONF_C1,
    CONF_C2,
    CONF_CPI,
    CONF_D1,
    CONF_D2,
    CONF_FAN,
    CONF_FRONT,
    CONF_LIGHT,
    CONF_PROFILES,
    CONF_REMOTE_ID,
    DATA_RUNTIME_ENTRIES,
    DOMAIN,
    MANUFACTURER,
)
from .profile import normalize_profiles, remote_id_as_hex


@dataclass
class Proflame2RuntimeEntry:
    """Live runtime state for one configured fireplace."""

    config_entry_id: str
    title: str
    backend_type: str
    remote_profile: RemoteProfile
    features: FireplaceFeatures
    backend: RFBackend | None
    device_id: str
    learning_in_progress: bool = False
    last_packet: ProflamePacket | None = None
    last_send_result: SendResult | None = None
    last_error: str | None = None
    last_applied_profile_id: str | None = None
    last_applied_profile_name: str | None = None
    saved_profiles: dict[str, dict[str, Any]] | None = None


def async_get_runtime_entries(hass: HomeAssistant) -> dict[str, Proflame2RuntimeEntry]:
    """Return the integration runtime map."""

    return hass.data.setdefault(DOMAIN, {}).setdefault(DATA_RUNTIME_ENTRIES, {})


@callback
def async_runtime_signal(config_entry_id: str) -> str:
    """Return the dispatcher signal used for runtime updates."""

    return f"{DOMAIN}_runtime_updated_{config_entry_id}"


@callback
def async_notify_runtime_entry_updated(hass: HomeAssistant, config_entry_id: str) -> None:
    """Notify listeners that runtime data changed for one fireplace."""

    async_dispatcher_send(hass, async_runtime_signal(config_entry_id))


async def async_setup_runtime_entry(
    hass: HomeAssistant, entry: ConfigEntry
) -> Proflame2RuntimeEntry:
    """Create and store runtime state for one config entry."""

    features = FireplaceFeatures(
        fan=bool(entry.options.get(CONF_FAN, True)),
        light=bool(entry.options.get(CONF_LIGHT, True)),
        front=bool(entry.options.get(CONF_FRONT, False)),
        aux=bool(entry.options.get(CONF_AUX, False)),
        cpi=bool(entry.options.get(CONF_CPI, False)),
    )
    remote_profile = RemoteProfile(
        serial_id=int(entry.data[CONF_REMOTE_ID]),
        ecc=ECCProfile(
            c1=int(entry.data[CONF_C1]),
            d1=int(entry.data[CONF_D1]),
            c2=int(entry.data[CONF_C2]),
            d2=int(entry.data[CONF_D2]),
        ),
        features=features,
    )

    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, remote_id_as_hex(remote_profile.serial_id))},
        manufacturer=MANUFACTURER,
        name=entry.title,
        model=f"Backend: {entry.data[CONF_BACKEND_TYPE]}",
    )

    backend_type = str(entry.data[CONF_BACKEND_TYPE])
    backend: RFBackend | None
    if backend_type == BACKEND_FAKE:
        backend = FakeRFBackend()
        await backend.connect()
    elif backend_type == BACKEND_YARDSTICK:
        backend = None
    else:
        raise ValueError(f"Unsupported backend type: {backend_type}")

    runtime_entry = Proflame2RuntimeEntry(
        config_entry_id=entry.entry_id,
        title=entry.title,
        backend_type=backend_type,
        remote_profile=remote_profile,
        features=features,
        backend=backend,
        device_id=device.id,
        saved_profiles=normalize_profiles(
            entry.options.get(CONF_PROFILES, {}),
            features=features,
        ),
    )
    async_get_runtime_entries(hass)[entry.entry_id] = runtime_entry
    return runtime_entry


async def async_unload_runtime_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Unload and discard runtime state for one config entry."""

    runtime_entry = async_get_runtime_entries(hass).pop(entry.entry_id, None)
    if runtime_entry and runtime_entry.backend is not None:
        await runtime_entry.backend.close()


def serialize_runtime_entry(runtime_entry: Proflame2RuntimeEntry) -> dict[str, Any]:
    """Convert runtime state into diagnostics-safe data."""

    last_packet = runtime_entry.last_packet

    return {
        "config_entry_id": runtime_entry.config_entry_id,
        "title": runtime_entry.title,
        "backend_type": runtime_entry.backend_type,
        "device_id": runtime_entry.device_id,
        "learning_in_progress": runtime_entry.learning_in_progress,
        "remote_profile": {
            "serial_id": runtime_entry.remote_profile.serial_id,
            "ecc": asdict(runtime_entry.remote_profile.ecc),
            "features": asdict(runtime_entry.remote_profile.features),
        },
        "last_packet": serialize_packet(last_packet) if last_packet is not None else None,
        "last_requested_state": asdict(last_packet.state) if last_packet is not None else None,
        "last_encoded_frame": asdict(last_packet.frame) if last_packet is not None else None,
        "last_transmission_plan": (
            serialize_transmission_plan(last_packet.transmission_plan)
            if last_packet is not None and last_packet.transmission_plan is not None
            else None
        ),
        "last_send_result": (
            {
                "packet": serialize_packet(runtime_entry.last_send_result.packet),
                "requested_state": asdict(runtime_entry.last_send_result.requested_state),
                "encoded_frame": asdict(runtime_entry.last_send_result.encoded_frame),
                "backend_name": runtime_entry.last_send_result.backend_name,
                "echo_seen": runtime_entry.last_send_result.echo_seen,
                "echo_delay_ms": runtime_entry.last_send_result.echo_delay_ms,
                "warnings": runtime_entry.last_send_result.warnings,
                "errors": runtime_entry.last_send_result.errors,
            }
            if runtime_entry.last_send_result is not None
            else None
        ),
        "last_error": runtime_entry.last_error,
        "last_applied_profile_id": runtime_entry.last_applied_profile_id,
        "last_applied_profile_name": runtime_entry.last_applied_profile_name,
        "saved_profiles": runtime_entry.saved_profiles,
    }


def serialize_transmission_plan(
    transmission_plan: ProflameTransmissionPlan,
) -> dict[str, Any]:
    """Serialize waveform-plan metadata for diagnostics."""

    return {
        "frame": asdict(transmission_plan.frame),
        "symbol_string": transmission_plan.symbol_string,
        "air_payload": transmission_plan.air_payload.hex(),
        "repeat_count": transmission_plan.repeat_count,
        "backend_repeat_argument": transmission_plan.backend_repeat_argument,
        "sync_strategy": transmission_plan.sync_strategy,
        "repeat_spacing_ms": transmission_plan.repeat_spacing_ms,
        "timing_profile": transmission_plan.timing_profile,
        "source_urls": transmission_plan.source_urls,
        "notes": transmission_plan.notes,
    }


def serialize_packet(packet: ProflamePacket) -> dict[str, Any]:
    """Serialize the unified operational packet model for diagnostics."""

    raw = packet.raw
    if isinstance(raw, bytes):
        raw_value: str | None = raw.hex()
    else:
        raw_value = raw

    return {
        "remote_id": packet.remote_id,
        "state": asdict(packet.state),
        "frame": asdict(packet.frame),
        "raw": raw_value,
        "source": packet.source,
        "received_at": packet.received_at.isoformat() if packet.received_at else None,
        "rssi": packet.rssi,
        "transmission_plan": (
            serialize_transmission_plan(packet.transmission_plan)
            if packet.transmission_plan is not None
            else None
        ),
        "is_echo_candidate": packet.is_echo_candidate,
        "echo_delay_ms": packet.echo_delay_ms,
        "warnings": packet.warnings,
    }
