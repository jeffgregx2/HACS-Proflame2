"""Runtime state and backend helpers for the Proflame2 integration."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.storage import Store

from .protocol.encoder import encode_packet
from .protocol.models import FireplaceState
from .protocol.models import ECCProfile, FireplaceFeatures, RemoteProfile
from .protocol.packet import ProflameFrame, ProflamePacket
from .rf.base import RFBackend, SendResult
from .rf.fake import FakeRFBackend
from .rf.waveform import ProflameTransmissionPlan
from .rf.yardstick import (
    YARDSTICK_RX_LEARNING_FREQUENCY_HZ,
    YARDSTICK_RX_LEARNING_PACKET_BYTES,
    YARDSTICK_RX_LEARNING_SWEEP_ENABLED,
    YardStickBackend,
)

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
    CONF_DEBUG_LOGGING,
    CONF_FAN,
    CONF_FRONT,
    CONF_INITIAL_FRAME,
    CONF_INITIAL_PACKET_SOURCE,
    CONF_LIGHT,
    CONF_PROFILES,
    CONF_REMOTE_ID,
    DATA_ACTIVE_LISTENING,
    DATA_RUNTIME_ENTRIES,
    DOMAIN,
    MANUFACTURER,
    OPERATIONAL_STATUS_READY,
    STATE_CONFIDENCE_OBSERVED,
    STATE_CONFIDENCE_REQUESTED,
    STATE_CONFIDENCE_RESTORED,
    STATE_CONFIDENCE_UNKNOWN,
)
from .profile import normalize_profiles, remote_id_as_hex
from .packet_debug import (
    async_disable_packet_debug_logging,
    async_enable_packet_debug_logging,
)

STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.runtime_state"

_LOGGER = logging.getLogger(__name__)


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
    sending_in_progress: bool = False
    last_packet: ProflamePacket | None = None
    last_send_result: SendResult | None = None
    last_error: str | None = None
    last_applied_profile_id: str | None = None
    last_applied_profile_name: str | None = None
    saved_profiles: dict[str, dict[str, Any]] | None = None
    desired_state: FireplaceState | None = None
    operational_status: str = OPERATIONAL_STATUS_READY
    state_confidence: str = STATE_CONFIDENCE_UNKNOWN
    active_profile_state: FireplaceState | None = None
    active_listening_enabled: bool = False
    debug_logging_enabled: bool = False
    debounce_task: asyncio.Task[None] | None = None
    debounce_cancel_reason: str | None = None
    active_send_task: asyncio.Task[None] | None = None
    confirmation_task: asyncio.Task[None] | None = None
    active_listener_task: asyncio.Task[None] | None = None
    shutting_down: bool = False
    shutdown_reason: str | None = None


def _runtime_store(hass: HomeAssistant) -> Store[dict[str, Any]]:
    """Return the persistent store used for restored runtime state."""

    return Store(hass, STORAGE_VERSION, STORAGE_KEY)


async def _async_load_persisted_runtime_state(hass: HomeAssistant) -> dict[str, Any]:
    """Load the persisted runtime-state map once per Home Assistant instance."""

    domain_data = hass.data.setdefault(DOMAIN, {})
    cache = domain_data.get("runtime_state_cache")
    if cache is not None:
        return cache
    loaded = await _runtime_store(hass).async_load()
    cache = loaded if isinstance(loaded, dict) else {}
    domain_data["runtime_state_cache"] = cache
    return cache


async def _async_save_persisted_runtime_state(hass: HomeAssistant) -> None:
    """Persist the current runtime-state cache to Home Assistant storage."""

    domain_data = hass.data.setdefault(DOMAIN, {})
    cache = domain_data.setdefault("runtime_state_cache", {})
    await _runtime_store(hass).async_save(cache)


def runtime_current_state(runtime_entry: Proflame2RuntimeEntry) -> FireplaceState | None:
    """Return the current integration-known fireplace state."""

    return None if runtime_entry.last_packet is None else runtime_entry.last_packet.state


async def async_persist_runtime_entry_state(
    hass: HomeAssistant, runtime_entry: Proflame2RuntimeEntry
) -> None:
    """Persist the current fireplace state used for restart restoration."""

    cache = await _async_load_persisted_runtime_state(hass)
    current_state = runtime_current_state(runtime_entry)
    if current_state is None:
        cache.pop(runtime_entry.config_entry_id, None)
    else:
        frame = runtime_entry.last_packet.frame if runtime_entry.last_packet is not None else None
        cache[runtime_entry.config_entry_id] = {
            "state": asdict(current_state),
            "state_confidence": runtime_entry.state_confidence,
            "last_applied_profile_id": runtime_entry.last_applied_profile_id,
            "last_applied_profile_name": runtime_entry.last_applied_profile_name,
            "frame": asdict(frame) if frame is not None else None,
            "source": None if runtime_entry.last_packet is None else runtime_entry.last_packet.source,
        }
    await _async_save_persisted_runtime_state(hass)


async def async_set_runtime_current_state(
    hass: HomeAssistant,
    runtime_entry: Proflame2RuntimeEntry,
    state: FireplaceState,
    *,
    source: str,
    confidence: str,
    packet: ProflamePacket | None = None,
    notify: bool = True,
) -> None:
    """Update the current known fireplace state and persist it."""

    runtime_entry.last_packet = packet or encode_packet(
        state,
        runtime_entry.remote_profile,
        source=source,
    )
    if runtime_entry.last_packet.source is None:
        runtime_entry.last_packet.source = source
    runtime_entry.state_confidence = confidence
    await async_persist_runtime_entry_state(hass, runtime_entry)
    if notify:
        async_notify_runtime_entry_updated(hass, runtime_entry.config_entry_id)


def clear_active_profile_if_state_differs(
    runtime_entry: Proflame2RuntimeEntry, state: FireplaceState
) -> None:
    """Clear the active profile marker when the current state no longer matches it."""

    if runtime_entry.active_profile_state is None:
        return
    if runtime_entry.active_profile_state != state:
        runtime_entry.last_applied_profile_id = None
        runtime_entry.last_applied_profile_name = None
        runtime_entry.active_profile_state = None


async def async_restore_runtime_state(
    hass: HomeAssistant, runtime_entry: Proflame2RuntimeEntry
) -> None:
    """Restore the last known state from persistent storage if one exists."""

    cache = await _async_load_persisted_runtime_state(hass)
    restored = cache.get(runtime_entry.config_entry_id)
    if not isinstance(restored, dict):
        return

    raw_state = restored.get("state")
    if not isinstance(raw_state, dict):
        return

    restored_state = FireplaceState(
        power=bool(raw_state.get("power", False)),
        flame=int(raw_state.get("flame", 0)),
        fan=int(raw_state.get("fan", 0)),
        light=int(raw_state.get("light", 0)),
        front=bool(raw_state.get("front", False)),
        aux=bool(raw_state.get("aux", False)),
        thermostat=bool(raw_state.get("thermostat", False)),
        cpi=bool(raw_state.get("cpi", False)),
    )
    raw_frame = restored.get("frame")
    if isinstance(raw_frame, dict):
        runtime_entry.last_packet = ProflamePacket.from_frame(
            ProflameFrame(
                serial_id=int(raw_frame["serial_id"]),
                cmd1=int(raw_frame["cmd1"]),
                err1=int(raw_frame["err1"]),
                cmd2=int(raw_frame["cmd2"]),
                err2=int(raw_frame["err2"]),
            ),
            source=str(restored.get("source", "restored_state")),
        )
    else:
        runtime_entry.last_packet = encode_packet(
            restored_state,
            runtime_entry.remote_profile,
            source="restored_state",
        )
    runtime_entry.state_confidence = str(
        restored.get("state_confidence", STATE_CONFIDENCE_RESTORED)
    )
    runtime_entry.last_applied_profile_id = restored.get("last_applied_profile_id")
    runtime_entry.last_applied_profile_name = restored.get("last_applied_profile_name")
    runtime_entry.operational_status = OPERATIONAL_STATUS_READY
    if runtime_entry.last_applied_profile_id and runtime_entry.last_applied_profile_name:
        runtime_entry.active_profile_state = restored_state


async def async_bootstrap_runtime_state_from_entry_data(
    hass: HomeAssistant, runtime_entry: Proflame2RuntimeEntry, entry: ConfigEntry
) -> None:
    """Seed current state from the learned packet stored in entry data, if present."""

    if runtime_entry.last_packet is not None:
        return

    raw_frame = entry.data.get(CONF_INITIAL_FRAME)
    if not isinstance(raw_frame, dict):
        return

    runtime_entry.last_packet = ProflamePacket.from_frame(
        ProflameFrame(
            serial_id=int(raw_frame["serial_id"]),
            cmd1=int(raw_frame["cmd1"]),
            err1=int(raw_frame["err1"]),
            cmd2=int(raw_frame["cmd2"]),
            err2=int(raw_frame["err2"]),
        ),
        source=str(entry.data.get(CONF_INITIAL_PACKET_SOURCE, "observed_packet")),
    )
    runtime_entry.state_confidence = STATE_CONFIDENCE_OBSERVED
    runtime_entry.operational_status = OPERATIONAL_STATUS_READY
    await async_persist_runtime_entry_state(hass, runtime_entry)
    _LOGGER.warning(
        "Proflame2 bootstrapped runtime state from learned packet config_entry_id=%s source=%s state=%s",
        runtime_entry.config_entry_id,
        runtime_entry.last_packet.source,
        runtime_entry.last_packet.state,
    )


async def async_initialize_safe_default_runtime_state(
    hass: HomeAssistant, runtime_entry: Proflame2RuntimeEntry
) -> None:
    """Seed a safe OFF/default state when no observed or restored state exists."""

    if runtime_entry.last_packet is not None:
        return

    safe_state = FireplaceState(
        power=False,
        flame=0,
        fan=0,
        light=0,
        front=False,
        aux=False,
        cpi=False,
    )
    runtime_entry.last_packet = encode_packet(
        safe_state,
        runtime_entry.remote_profile,
        source="restored_state",
    )
    runtime_entry.state_confidence = STATE_CONFIDENCE_RESTORED
    runtime_entry.operational_status = OPERATIONAL_STATUS_READY
    runtime_entry.last_error = (
        "State initialized from safe defaults; no observed fireplace state was available."
    )
    await async_persist_runtime_entry_state(hass, runtime_entry)
    _LOGGER.warning(
        "Proflame2 initialized safe default runtime state config_entry_id=%s state=%s",
        runtime_entry.config_entry_id,
        runtime_entry.last_packet.state,
    )


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
    active_listening_enabled = bool(hass.data.setdefault(DOMAIN, {}).get(DATA_ACTIVE_LISTENING, False))
    debug_logging_enabled = bool(entry.options.get(CONF_DEBUG_LOGGING, False))

    if backend_type == BACKEND_FAKE:
        backend = FakeRFBackend()
        await backend.connect()
    elif backend_type == BACKEND_YARDSTICK:
        backend = YardStickBackend(
            hass=hass,
            frequency_hz=YARDSTICK_RX_LEARNING_FREQUENCY_HZ,
            packet_length_bytes=YARDSTICK_RX_LEARNING_PACKET_BYTES,
            sweep_enabled=YARDSTICK_RX_LEARNING_SWEEP_ENABLED,
        )
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
        active_listening_enabled=active_listening_enabled,
        debug_logging_enabled=debug_logging_enabled,
    )
    if debug_logging_enabled:
        log_paths = await async_enable_packet_debug_logging(hass)
        _LOGGER.warning(
            "Proflame2 packet debug logging is ENABLED for config_entry_id=%s primary_log=%s decode_failures_log=%s",
            entry.entry_id,
            log_paths.primary_log_path,
            log_paths.decode_failure_log_path,
        )
    if backend_type == BACKEND_FAKE:
        runtime_entry.last_packet = encode_packet(
            FireplaceState(
                power=True,
                flame=1,
                fan=0,
                light=0,
                front=False,
                aux=False,
                cpi=False,
            ),
            remote_profile,
            source="fake_default",
        )
        runtime_entry.state_confidence = STATE_CONFIDENCE_REQUESTED
    await async_restore_runtime_state(hass, runtime_entry)
    if runtime_entry.last_packet is None:
        await async_bootstrap_runtime_state_from_entry_data(hass, runtime_entry, entry)
    if runtime_entry.last_packet is None:
        await async_initialize_safe_default_runtime_state(hass, runtime_entry)
    async_get_runtime_entries(hass)[entry.entry_id] = runtime_entry
    return runtime_entry


async def async_unload_runtime_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Unload and discard runtime state for one config entry."""

    runtime_entry = async_get_runtime_entries(hass).pop(entry.entry_id, None)
    if runtime_entry:
        runtime_entry.shutting_down = True
        runtime_entry.shutdown_reason = "config_entry_unload"
        runtime_entry.active_listening_enabled = False
        for task in (
            runtime_entry.debounce_task,
            runtime_entry.active_send_task,
            runtime_entry.confirmation_task,
            runtime_entry.active_listener_task,
        ):
            if task is not None:
                task.cancel()
        pending_tasks = [
            task
            for task in (
                runtime_entry.debounce_task,
                runtime_entry.active_send_task,
                runtime_entry.confirmation_task,
                runtime_entry.active_listener_task,
            )
            if task is not None
        ]
        if pending_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*pending_tasks, return_exceptions=True),
                    timeout=2.0,
                )
            except asyncio.TimeoutError:
                _LOGGER.warning(
                    "Proflame2 runtime task shutdown timed out config_entry_id=%s",
                    runtime_entry.config_entry_id,
                )
        if runtime_entry.backend is not None:
            await runtime_entry.backend.close(reason="config_entry_unload")
        if runtime_entry.debug_logging_enabled:
            await async_disable_packet_debug_logging(hass)


async def async_handle_homeassistant_stop(hass: HomeAssistant) -> None:
    """Shut down all runtime entries on Home Assistant stop."""

    runtime_entries = list(async_get_runtime_entries(hass).values())
    for runtime_entry in runtime_entries:
        runtime_entry.shutting_down = True
        runtime_entry.shutdown_reason = "ha_shutdown"
        runtime_entry.active_listening_enabled = False
        for task in (
            runtime_entry.debounce_task,
            runtime_entry.active_send_task,
            runtime_entry.confirmation_task,
            runtime_entry.active_listener_task,
        ):
            if task is not None:
                task.cancel()
        pending_tasks = [
            task
            for task in (
                runtime_entry.debounce_task,
                runtime_entry.active_send_task,
                runtime_entry.confirmation_task,
                runtime_entry.active_listener_task,
            )
            if task is not None
        ]
        if pending_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*pending_tasks, return_exceptions=True),
                    timeout=2.0,
                )
            except asyncio.TimeoutError:
                _LOGGER.warning(
                    "Proflame2 HA-stop task shutdown timed out config_entry_id=%s",
                    runtime_entry.config_entry_id,
                )
        if runtime_entry.backend is not None:
            await runtime_entry.backend.close(reason="ha_shutdown")


@callback
def async_register_shutdown_listener(hass: HomeAssistant) -> None:
    """Register a one-time Home Assistant stop handler."""

    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get("shutdown_listener_registered"):
        return

    async def _handle_stop(event) -> None:
        del event
        await async_handle_homeassistant_stop(hass)

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _handle_stop)
    domain_data["shutdown_listener_registered"] = True


def serialize_runtime_entry(runtime_entry: Proflame2RuntimeEntry) -> dict[str, Any]:
    """Convert runtime state into diagnostics-safe data."""

    last_packet = runtime_entry.last_packet

    return {
        "config_entry_id": runtime_entry.config_entry_id,
        "title": runtime_entry.title,
        "backend_type": runtime_entry.backend_type,
        "device_id": runtime_entry.device_id,
        "learning_in_progress": runtime_entry.learning_in_progress,
        "sending_in_progress": runtime_entry.sending_in_progress,
        "operational_status": runtime_entry.operational_status,
        "state_confidence": runtime_entry.state_confidence,
        "shutting_down": runtime_entry.shutting_down,
        "shutdown_reason": runtime_entry.shutdown_reason,
        "desired_state": (
            asdict(runtime_entry.desired_state) if runtime_entry.desired_state is not None else None
        ),
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
        "backend_diagnostics": (
            runtime_entry.backend.serialize_worker_diagnostics()
            if isinstance(runtime_entry.backend, YardStickBackend)
            else None
        ),
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
