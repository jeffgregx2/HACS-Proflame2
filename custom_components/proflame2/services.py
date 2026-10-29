"""Service registration and execution for Proflame2."""

from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass
from typing import Any

import voluptuous as vol
from homeassistant.const import ATTR_AREA_ID, ATTR_DEVICE_ID, ATTR_ENTITY_ID, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    BACKEND_ESPHOME,
    CONF_ACTION_LABEL,
    CONF_AUX,
    CONF_CONFIG_ENTRY_ID,
    CONF_CPI,
    CONF_FAN,
    CONF_FLAME,
    CONF_FRONT,
    CONF_LIGHT,
    CONF_NAME,
    CONF_PILOT,
    CONF_POWER,
    CONF_PROFILE_ID,
    CONF_THERMOSTAT,
    DATA_CONFIRMATION_RECEIVE_TIMEOUT_SECONDS,
    DATA_CONFIRMATION_WINDOW_SECONDS,
    DATA_CONTROL_DEBOUNCE_SECONDS,
    DATA_SERVICES_REGISTERED,
    DEFAULT_CONFIRMATION_RECEIVE_TIMEOUT_SECONDS,
    DEFAULT_CONFIRMATION_WINDOW_SECONDS,
    DEFAULT_CONTROL_DEBOUNCE_SECONDS,
    DOMAIN,
    OPERATIONAL_STATUS_CONFIRMING,
    OPERATIONAL_STATUS_FAILED,
    OPERATIONAL_STATUS_PENDING,
    OPERATIONAL_STATUS_READY,
    OPERATIONAL_STATUS_SENDING,
    OPERATIONAL_STATUS_UNAVAILABLE,
    SERVICE_APPLY_PROFILE,
    SERVICE_DISPLAY_STATE_UPDATE,
    SERVICE_SET_STATE,
    STATE_CONFIDENCE_OBSERVED,
    STATE_CONFIDENCE_REQUESTED,
)
from .control import StateValidationError, build_requested_state, build_staged_state
from .control_intent import ControlIntent, ControllerArbiter, controller_arbiter
from .packet_debug import get_packet_debug_logger
from .protocol.encoder import encode_packet
from .rf.esphome.contract import ESPHomeDisplayState
from .rf.waveform import build_transmission_plan
from .runtime import (
    Proflame2RuntimeEntry,
    async_get_runtime_entries,
    async_notify_runtime_entry_updated,
    async_persist_runtime_entry_state,
    async_set_runtime_current_state,
    clear_active_profile_if_state_differs,
    runtime_current_state,
)

_LOGGER = logging.getLogger(__name__)
BACKEND_SEND_TIMEOUT_SECONDS = 30.0
_RETAINED_OFF_CONTROL_KEYS = frozenset({CONF_FAN, CONF_LIGHT, CONF_FRONT, CONF_AUX, CONF_CPI})


@dataclass(frozen=True)
class _PreparedSendRequest:
    """Packet and context prepared for one already-validated control request."""

    requested_state: Any
    request_summary: str
    packet: Any
    source: str
    intent: ControlIntent | None = None


SERVICE_SET_STATE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_POWER): bool,
        vol.Optional(CONF_CONFIG_ENTRY_ID): str,
        vol.Optional(ATTR_DEVICE_ID): vol.Any(str, [str]),
        vol.Optional(ATTR_ENTITY_ID): vol.Any(str, [str]),
        vol.Optional(ATTR_AREA_ID): vol.Any(str, [str]),
        vol.Optional(CONF_FLAME): vol.Coerce(int),
        vol.Optional(CONF_FAN): vol.Coerce(int),
        vol.Optional(CONF_LIGHT): vol.Coerce(int),
        vol.Optional(CONF_FRONT): bool,
        vol.Optional(CONF_AUX): bool,
        vol.Optional(CONF_CPI): bool,
    }
)

SERVICE_APPLY_PROFILE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PROFILE_ID): str,
        vol.Optional(CONF_CONFIG_ENTRY_ID): str,
        vol.Optional(ATTR_DEVICE_ID): vol.Any(str, [str]),
        vol.Optional(ATTR_ENTITY_ID): vol.Any(str, [str]),
        vol.Optional(ATTR_AREA_ID): vol.Any(str, [str]),
    }
)

SERVICE_DISPLAY_STATE_UPDATE_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_CONFIG_ENTRY_ID): str,
        vol.Optional(ATTR_DEVICE_ID): vol.Any(str, [str]),
        vol.Optional(ATTR_ENTITY_ID): vol.Any(str, [str]),
        vol.Optional(ATTR_AREA_ID): vol.Any(str, [str]),
        vol.Optional(CONF_POWER): bool,
        vol.Optional(CONF_FLAME): vol.Coerce(int),
        vol.Optional(CONF_FAN): vol.Coerce(int),
        vol.Optional(CONF_LIGHT): vol.Coerce(int),
        vol.Optional(CONF_PILOT): vol.Coerce(int),
        vol.Optional(CONF_THERMOSTAT): bool,
        vol.Optional(CONF_FRONT): bool,
        vol.Optional(CONF_AUX): bool,
        vol.Optional(CONF_ACTION_LABEL): str,
    }
)


def _cancel_task(task: asyncio.Task[None] | None) -> None:
    """Cancel one runtime task if it is still running."""

    if task is not None and not task.done():
        task.cancel()


def _task_label(task: asyncio.Task[None] | None) -> str:
    """Return a compact label for a task used in cancellation diagnostics."""

    if task is None:
        return "none"
    name_getter = getattr(task, "get_name", None)
    name = name_getter() if callable(name_getter) else "unnamed"
    return f"id={id(task)} name={name} done={task.done()}"


def _cancel_runtime_task(
    runtime_entry: Proflame2RuntimeEntry,
    task: asyncio.Task[None] | None,
    *,
    reason: str,
    kind: str,
) -> None:
    """Cancel one runtime task with diagnostics about who requested it."""

    if task is None or task.done():
        return
    current_task = asyncio.current_task()
    _log_control_event(
        runtime_entry,
        "task cancel requested reason=%s kind=%s current_task=%s target_task=%s debounce_task=%s active_send_task=%s",
        reason,
        kind,
        _task_label(current_task),
        _task_label(task),
        _task_label(runtime_entry.debounce_task),
        _task_label(runtime_entry.active_send_task),
        level=logging.WARNING,
    )
    if kind == "debounce":
        runtime_entry.debounce_cancel_reason = reason
    task.cancel()


def _control_debounce_seconds(hass: HomeAssistant) -> float:
    return float(hass.data.setdefault(DOMAIN, {}).get(DATA_CONTROL_DEBOUNCE_SECONDS, DEFAULT_CONTROL_DEBOUNCE_SECONDS))


def _confirmation_window_seconds(hass: HomeAssistant) -> float:
    return float(
        hass.data.setdefault(DOMAIN, {}).get(DATA_CONFIRMATION_WINDOW_SECONDS, DEFAULT_CONFIRMATION_WINDOW_SECONDS)
    )


def _confirmation_receive_timeout_seconds(hass: HomeAssistant) -> float:
    return float(
        hass.data.setdefault(DOMAIN, {}).get(
            DATA_CONFIRMATION_RECEIVE_TIMEOUT_SECONDS,
            DEFAULT_CONFIRMATION_RECEIVE_TIMEOUT_SECONDS,
        )
    )


def _state_summary(state) -> str:
    """Return a compact human-readable summary for control-path logs."""

    if state is None:
        return "None"

    if not state.power:
        return "Off"

    parts = [f"On · Flame {state.flame}"]
    if state.fan > 0:
        parts.append(f"Fan {state.fan}")
    if state.light > 0:
        parts.append(f"Light {state.light}")
    if state.front:
        parts.append("Front On")
    if state.aux:
        parts.append("Aux On")
    if state.cpi:
        parts.append("CPI On")
    return " · ".join(parts)


def _log_control_event(
    runtime_entry: Proflame2RuntimeEntry,
    message: str,
    *args: object,
    level: int = logging.INFO,
) -> None:
    """Log one control/service/runtime event to normal logs and packet debug when enabled."""

    prefixed_message = (
        f"config_entry_id={runtime_entry.config_entry_id} backend={runtime_entry.backend_type} " + message
    )
    _LOGGER.log(level, "Proflame2 control: " + prefixed_message, *args)
    if runtime_entry.debug_logging_enabled:
        get_packet_debug_logger().log(level, "control: " + prefixed_message, *args)


def _is_retained_off_control_edit(
    current_state: Any,
    desired_state: Any,
    changes: dict[str, Any],
) -> bool:
    """Return whether an OFF-state feature edit should be retained without TX."""

    return (
        current_state is not None
        and not current_state.power
        and not desired_state.power
        and set(changes).issubset(_RETAINED_OFF_CONTROL_KEYS)
    )


def _transmit_failure_message(
    reason: str,
    *,
    rollback: bool = True,
) -> str:
    """Return a user-facing transmit failure message with the underlying cause."""

    normalized_reason = str(reason).strip().rstrip(".")
    suffix = "; controls reverted to last known state." if rollback else "."
    if not normalized_reason:
        return f"Transmit failed{suffix}"
    return f"Transmit failed because {normalized_reason}{suffix}"


def _linked_backend_entry_id(runtime_entry: Proflame2RuntimeEntry) -> str | None:
    """Return the linked backend entry id when the backend exposes one."""

    backend = runtime_entry.backend
    transport = getattr(backend, "transport", None) if backend is not None else None
    linked_entry_id = getattr(transport, "linked_entry_id", None)
    return str(linked_entry_id) if linked_entry_id else None


async def _should_start_confirmation(hass: HomeAssistant, runtime_entry: Proflame2RuntimeEntry) -> bool:
    """Return whether a post-TX confirmation listen should start now."""

    if _confirmation_window_seconds(hass) <= 0 or runtime_entry.backend is None:
        return False
    backend = runtime_entry.backend
    try:
        capabilities = await backend.capabilities()
    except Exception:
        _LOGGER.exception(
            "Proflame2 confirmation capability check failed config_entry_id=%s backend=%s",
            runtime_entry.config_entry_id,
            runtime_entry.backend_type,
        )
        return False
    _log_control_event(
        runtime_entry,
        "confirmation capability check can_receive=%s endpoint_status=%s",
        capabilities.can_receive,
        getattr(getattr(backend, "last_endpoint_status", None), "status", None),
    )
    if not capabilities.can_receive:
        _log_control_event(
            runtime_entry,
            "confirmation skipped can_receive=%s backend=%s",
            capabilities.can_receive,
            runtime_entry.backend_type,
        )
        return False
    if getattr(backend, "name", "") == "fake" and not getattr(backend, "receive_queue", []):
        return False
    return True


async def async_stage_control_change(
    hass: HomeAssistant,
    runtime_entry: Proflame2RuntimeEntry,
    changes: dict[str, Any],
) -> None:
    """Stage a debounced user-facing control edit without sending immediately."""

    _ensure_runtime_entry_accepts_actions(runtime_entry)

    _log_control_event(
        runtime_entry,
        "user action received changes=%s current_state=%s desired_state=%s",
        changes,
        _state_summary(runtime_current_state(runtime_entry)),
        _state_summary(runtime_entry.desired_state),
    )
    current_state = runtime_current_state(runtime_entry)
    base_state = runtime_entry.desired_state or current_state
    if base_state is None:
        runtime_entry.desired_state = None
        runtime_entry.operational_status = OPERATIONAL_STATUS_FAILED
        runtime_entry.last_error = "Cannot stage fireplace control because current state is unknown."
        _cancel_task(runtime_entry.debounce_task)
        runtime_entry.debounce_task = None
        _log_control_event(
            runtime_entry,
            "control staging failed because current state is unknown changes=%s",
            changes,
            level=logging.ERROR,
        )
        async_notify_runtime_entry_updated(hass, runtime_entry.config_entry_id)
        return

    desired_state = build_staged_state(runtime_entry.features, base_state, changes)

    intents = runtime_entry.control_intents
    expected_state = intents.in_flight.state if intents.in_flight is not None else current_state
    if expected_state is not None and desired_state == expected_state:
        if intents.pending is not None:
            _finish_intent(intents.pending, HomeAssistantError("Request superseded by a newer control change."))
            intents.pending = None
            intents.wake.set()
        if intents.in_flight is not None:
            runtime_entry.desired_state = intents.desired_state
            async_notify_runtime_entry_updated(hass, runtime_entry.config_entry_id)
            return
        runtime_entry.desired_state = None
        runtime_entry.operational_status = OPERATIONAL_STATUS_READY
        runtime_entry.debounce_task = None
        _log_control_event(
            runtime_entry,
            "staged state matches current state; clearing pending summary=%s",
            _state_summary(current_state),
        )
        async_notify_runtime_entry_updated(hass, runtime_entry.config_entry_id)
        return

    if intents.in_flight is None and _is_retained_off_control_edit(current_state, desired_state, changes):
        if intents.pending is not None:
            _finish_intent(intents.pending, HomeAssistantError("Request superseded by a retained control change."))
            intents.pending = None
            intents.wake.set()
        runtime_entry.desired_state = None
        runtime_entry.operational_status = OPERATIONAL_STATUS_READY
        runtime_entry.debounce_task = None
        clear_active_profile_if_state_differs(runtime_entry, desired_state)
        _log_control_event(
            runtime_entry,
            "off-state control edit retained without transmit state=%s",
            _state_summary(desired_state),
        )
        await async_set_runtime_current_state(
            hass,
            runtime_entry,
            desired_state,
            source="retained_control",
            confidence=STATE_CONFIDENCE_REQUESTED,
            packet=encode_packet(
                desired_state,
                runtime_entry.remote_profile,
                source="retained_control",
                allow_power_off_flame=True,
            ),
        )
        return

    _submit_control_intent(
        hass, runtime_entry, desired_state, source="debounced_control", delay=_control_debounce_seconds(hass)
    )


def _finish_intent(intent: ControlIntent, error: Exception | None = None) -> None:
    future = intent.completion
    if future is None or future.done():
        return
    if error is None:
        future.set_result(None)
    else:
        future.set_exception(error)


def _control_arbiter(hass: HomeAssistant, entry: Proflame2RuntimeEntry) -> ControllerArbiter:
    key = getattr(entry.backend, "physical_controller_id", None) or entry.controller_id
    return controller_arbiter(hass.data.setdefault(DOMAIN, {}), key)


def async_controller_connection_lost(hass: HomeAssistant, entry: Proflame2RuntimeEntry) -> None:
    """Fence controller work without assuming a frontend disconnect is device loss."""
    arbiter = _control_arbiter(hass, entry)
    arbiter.readiness_unknown = True
    for candidate in async_get_runtime_entries(hass).values():
        if _control_arbiter(hass, candidate) is not arbiter:
            continue
        if candidate.control_intents.in_flight is not None:
            arbiter.uncertain_request_id = getattr(candidate.backend, "latest_request_id", None)
        if hasattr(candidate.backend, "connected"):
            candidate.backend.connected = False
        if candidate.control_intents.desired_state is not None:
            _mark_send_failed(candidate, "Controller disconnected; controls reverted to last known state.")
            async_notify_runtime_entry_updated(hass, candidate.config_entry_id)


@callback
def async_observe_controller_connection(hass: HomeAssistant, entry: Proflame2RuntimeEntry) -> None:
    """Use public entity availability, not ESPHome's private connection internals."""
    if entry.backend_type != BACKEND_ESPHOME:
        return
    linked_entry = entry.controller_id.partition(":")[2]
    entities = [
        item.entity_id
        for item in er.async_entries_for_config_entry(er.async_get(hass), linked_entry)
        if item.unique_id.endswith("proflame2_endpoint_status")
    ]
    if not entities:
        return

    @callback
    def state_changed(event) -> None:
        old = event.data.get("old_state")
        new = event.data.get("new_state")
        if old is not None and old.state != STATE_UNAVAILABLE and (new is None or new.state == STATE_UNAVAILABLE):
            async_controller_connection_lost(hass, entry)

    entry.control_disconnect_unsub = async_track_state_change_event(hass, entities, state_changed)


def _submit_control_intent(
    hass: HomeAssistant,
    entry: Proflame2RuntimeEntry,
    state: Any,
    *,
    source: str,
    delay: float = 0,
    warnings: tuple[str, ...] = (),
    profile_id: str | None = None,
    profile_name: str | None = None,
    clear_active_profile: bool = True,
    completion: asyncio.Future[None] | None = None,
) -> ControlIntent:
    intents = entry.control_intents
    if intents.pending is not None:
        _finish_intent(intents.pending, HomeAssistantError("Request superseded by a newer control change."))
        _log_control_event(
            entry, "debounce_timer_cancelled reason=restart_debounce_after_new_user_edit", level=logging.DEBUG
        )
    intents.revision += 1
    intent = ControlIntent(
        intents.revision,
        intents.epoch,
        state,
        asyncio.get_running_loop().time() + delay,
        source,
        warnings,
        profile_id,
        profile_name,
        clear_active_profile,
        completion,
    )
    intents.pending = intent
    entry.desired_state = intents.desired_state
    entry.operational_status = OPERATIONAL_STATUS_SENDING if entry.sending_in_progress else OPERATIONAL_STATUS_PENDING
    intents.wake.set()
    _log_control_event(entry, "desired state staged pending_state=%s", _state_summary(state))
    if entry.control_task is None or entry.control_task.done():
        # Install ownership before the dispatcher can reach its finally block.
        entry.control_task = hass.async_create_task(_async_control_dispatcher(hass, entry), eager_start=False)
    if intents.in_flight is None:
        entry.debounce_task = entry.control_task
    async_notify_runtime_entry_updated(hass, entry.config_entry_id)
    return intent


async def _async_control_dispatcher(hass: HomeAssistant, entry: Proflame2RuntimeEntry) -> None:
    """Drain latest intent, never overlapping physical sends or retaining obsolete packets."""
    intents = entry.control_intents
    arbiter = _control_arbiter(hass, entry)
    try:
        while not entry.shutting_down and intents.pending is not None:
            intents.wake.clear()
            remaining = intents.pending.eligible_at - asyncio.get_running_loop().time()
            if remaining > 0:
                try:
                    await asyncio.wait_for(intents.wake.wait(), remaining)
                except TimeoutError:
                    pass
                continue
            async with arbiter.lock:
                intent = intents.pending
                if intent is None:
                    continue
                if intent.eligible_at > asyncio.get_running_loop().time():
                    continue
                if entry.shutting_down:
                    break
                intents.pending = None
                intents.in_flight = intent
                entry.debounce_task = None
                entry.active_send_task = asyncio.current_task()
                _log_control_event(entry, "debounce task fired pending_state=%s", _state_summary(intent.state))
                try:
                    if arbiter.learning_owner is not None:
                        raise HomeAssistantError("Controller is learning a remote; try again after learning finishes.")
                    if arbiter.readiness_unknown:
                        recover = getattr(entry.backend, "recover_send_readiness", None)
                        if recover is None or not await recover(arbiter.uncertain_request_id):
                            raise HomeAssistantError(
                                "Controller readiness is unknown; reconnect or reload before retrying."
                            )
                        arbiter.readiness_unknown = False
                        arbiter.uncertain_request_id = None
                    await _async_execute_requested_state(
                        hass,
                        entry,
                        intent.state,
                        source=intent.source,
                        warnings=intent.warnings,
                        applied_profile_id=intent.profile_id,
                        applied_profile_name=intent.profile_name,
                        clear_active_profile=intent.clear_active_profile,
                    )
                    _finish_intent(intent)
                    _log_control_event(
                        entry, "debounced send terminal result=succeeded state=%s", _state_summary(intent.state)
                    )
                except asyncio.CancelledError:
                    _finish_intent(intent, HomeAssistantError("Transmit cancelled."))
                    raise
                except Exception as exc:
                    _finish_intent(intent, exc)
                    if intents.owns(intent):
                        message = str(exc) if isinstance(exc, HomeAssistantError) else f"{type(exc).__name__}: {exc}"
                        _mark_send_failed(entry, message)
                    if not isinstance(exc, HomeAssistantError):
                        _LOGGER.exception("Proflame2 debounce task failed")
                    if "timed out" in str(exc).lower():
                        arbiter.readiness_unknown = True
                        arbiter.uncertain_request_id = getattr(entry.backend, "latest_request_id", None)
                    _log_control_event(
                        entry, "debounced send terminal result=exception error=%s", str(exc), level=logging.ERROR
                    )
                    async_notify_runtime_entry_updated(hass, entry.config_entry_id)
                    if intents.pending is None:
                        break
                finally:
                    if intents.in_flight is intent:
                        intents.in_flight = None
                    entry.sending_in_progress = False
                    entry.active_send_task = None
                    entry.desired_state = intents.desired_state
            if intents.pending is not None:
                entry.debounce_task = asyncio.current_task()
                entry.operational_status = OPERATIONAL_STATUS_PENDING
                async_notify_runtime_entry_updated(hass, entry.config_entry_id)
    finally:
        if entry.control_task is asyncio.current_task():
            if intents.in_flight is not None or intents.pending is not None:
                for abandoned in intents.invalidate():
                    _finish_intent(abandoned, HomeAssistantError("Control sequence stopped."))
                entry.desired_state = None
            entry.control_task = None
            entry.debounce_task = None
            entry.active_send_task = None


async def async_start_active_listener(hass: HomeAssistant, runtime_entry: Proflame2RuntimeEntry) -> None:
    """Start background active listening when the hidden test flag enables it."""

    if runtime_entry.shutting_down or not runtime_entry.active_listening_enabled or runtime_entry.backend is None:
        _log_control_event(
            runtime_entry,
            "active listener not started shutting_down=%s enabled=%s backend_available=%s",
            runtime_entry.shutting_down,
            runtime_entry.active_listening_enabled,
            runtime_entry.backend is not None,
        )
        return
    if runtime_entry.active_listener_task is not None and not runtime_entry.active_listener_task.done():
        return
    _log_control_event(
        runtime_entry,
        "active listener started backend=%s remote_id=%06x",
        runtime_entry.backend_type,
        runtime_entry.remote_profile.serial_id,
    )
    runtime_entry.active_listener_task = hass.async_create_background_task(
        _async_active_listener_loop(hass, runtime_entry),
        f"{DOMAIN}_{runtime_entry.config_entry_id}_active_listener",
    )


async def async_start_display_sync_listener(hass: HomeAssistant, runtime_entry: Proflame2RuntimeEntry) -> None:
    """Periodically resync ESPHome display state for reconnect/option convergence."""

    if runtime_entry.shutting_down or runtime_entry.backend_type != BACKEND_ESPHOME or runtime_entry.backend is None:
        return
    if runtime_entry.display_sync_task is not None and not runtime_entry.display_sync_task.done():
        return
    runtime_entry.display_sync_task = hass.async_create_background_task(
        _async_display_sync_loop(hass, runtime_entry),
        f"{DOMAIN}_{runtime_entry.config_entry_id}_display_sync",
    )


async def _async_display_sync_loop(hass: HomeAssistant, runtime_entry: Proflame2RuntimeEntry) -> None:
    """Keep the LilyGO display state converged after reconnects and HA option changes."""

    reconcile_interval_seconds = 300.0
    try:
        while (
            not runtime_entry.shutting_down
            and runtime_entry.backend_type == BACKEND_ESPHOME
            and runtime_entry.backend is not None
        ):
            await asyncio.sleep(15.0)
            if runtime_entry.shutting_down:
                break
            try:
                loop_time = asyncio.get_running_loop().time()
                last_sync = runtime_entry.last_display_sync_monotonic
                force = last_sync is None or (loop_time - last_sync) >= reconcile_interval_seconds
                await async_sync_runtime_display_state(hass, runtime_entry, force=force)
            except RuntimeError as exc:
                _LOGGER.debug(
                    "Proflame2 periodic display sync skipped config_entry_id=%s: %s",
                    runtime_entry.config_entry_id,
                    exc,
                )
            except Exception:
                _LOGGER.exception(
                    "Proflame2 periodic display sync failed config_entry_id=%s",
                    runtime_entry.config_entry_id,
                )
    except asyncio.CancelledError:
        raise
    finally:
        runtime_entry.display_sync_task = None


async def _async_active_listener_loop(hass: HomeAssistant, runtime_entry: Proflame2RuntimeEntry) -> None:
    """Continuously receive observed packets when active listening is enabled."""

    retry_delay_seconds = 1.0
    try:
        while (
            not runtime_entry.shutting_down
            and runtime_entry.active_listening_enabled
            and runtime_entry.backend is not None
        ):
            try:
                if runtime_entry.learning_in_progress:
                    await asyncio.sleep(0.1)
                    continue
                packet = await runtime_entry.backend.receive(timeout=_confirmation_receive_timeout_seconds(hass))
            except RuntimeError as exc:
                message = str(exc)
                if "Linked ESPHome entry is not loaded or has no runtime_data" not in message:
                    raise
                _log_control_event(
                    runtime_entry,
                    "active listener waiting for linked ESPHome runtime data: %s",
                    message,
                    level=logging.DEBUG,
                )
                await asyncio.sleep(retry_delay_seconds)
                continue
            if (
                runtime_entry.learning_in_progress
                or packet is None
                or packet.remote_id != runtime_entry.remote_profile.serial_id
            ):
                if packet is None:
                    await asyncio.sleep(0.01)
                continue
            await async_apply_observed_packet(hass, runtime_entry, packet)
    except asyncio.CancelledError:
        raise
    except Exception:
        _LOGGER.exception(
            "Proflame2 active listening loop failed config_entry_id=%s",
            runtime_entry.config_entry_id,
        )
    finally:
        runtime_entry.active_listener_task = None


async def async_apply_observed_packet(
    hass: HomeAssistant,
    runtime_entry: Proflame2RuntimeEntry,
    packet,
) -> None:
    """Apply a valid observed packet as the current integration-known state."""

    runtime_entry.last_observed_packet = packet
    clear_active_profile_if_state_differs(runtime_entry, packet.state)
    local_work = runtime_entry.desired_state is not None
    if not local_work:
        runtime_entry.last_error = None
        runtime_entry.operational_status = OPERATIONAL_STATUS_READY
    _log_control_event(
        runtime_entry,
        "observed packet accepted remote_id=%06x state=%s source=%s",
        packet.remote_id,
        _state_summary(packet.state),
        packet.source or "observed_packet",
    )
    current_state = runtime_current_state(runtime_entry)
    if current_state == packet.state and runtime_entry.state_confidence == STATE_CONFIDENCE_OBSERVED:
        runtime_entry.last_packet = packet
        if not local_work:
            runtime_entry.last_error = None
            runtime_entry.operational_status = OPERATIONAL_STATUS_READY
        return
    await async_set_runtime_current_state(
        hass,
        runtime_entry,
        packet.state,
        source=packet.source or "observed_packet",
        confidence=STATE_CONFIDENCE_OBSERVED,
        packet=packet,
    )
    # Remote activity must update the controller display immediately. The
    # periodic sync below remains responsible for reconnect convergence.
    await _async_push_runtime_display_state(runtime_entry)


async def _async_stop_rx(runtime_entry: Proflame2RuntimeEntry) -> None:
    if runtime_entry.backend is None:
        return
    stop_rx = getattr(runtime_entry.backend, "stop_rx", None)
    if callable(stop_rx):
        await stop_rx()


async def _async_end_confirmation_rx(runtime_entry: Proflame2RuntimeEntry) -> None:
    if runtime_entry.backend is None:
        return
    end_confirmation_rx = getattr(runtime_entry.backend, "end_confirmation_rx", None)
    if callable(end_confirmation_rx):
        await end_confirmation_rx()


async def _async_confirmation_task(
    hass: HomeAssistant,
    runtime_entry: Proflame2RuntimeEntry,
    requested_packet,
) -> None:
    """Listen briefly for a post-TX confirmation/echo packet."""

    revision = runtime_entry.control_intents.revision
    epoch = runtime_entry.control_intents.epoch
    try:
        deadline = asyncio.get_running_loop().time() + _confirmation_window_seconds(hass)
        receive_timeout = _confirmation_receive_timeout_seconds(hass)
        _log_control_event(
            runtime_entry,
            "post-TX confirmation started window=%.3fs receive_timeout=%.3fs",
            _confirmation_window_seconds(hass),
            receive_timeout,
        )
        while asyncio.get_running_loop().time() < deadline:
            if runtime_entry.shutting_down:
                break
            if runtime_entry.backend is None:
                break
            packet = await runtime_entry.backend.receive(timeout=receive_timeout)
            if (revision, epoch) != (runtime_entry.control_intents.revision, runtime_entry.control_intents.epoch):
                return
            if packet is None:
                await asyncio.sleep(min(0.05, max(0.0, deadline - asyncio.get_running_loop().time())))
                continue
            if packet.remote_id != runtime_entry.remote_profile.serial_id:
                _log_control_event(
                    runtime_entry,
                    "post-TX confirmation ignored wrong remote_id=%06x expected=%06x",
                    packet.remote_id,
                    runtime_entry.remote_profile.serial_id,
                )
                continue
            _log_control_event(
                runtime_entry,
                "post-TX confirmation observed state=%s match=%s",
                _state_summary(packet.state),
                "yes" if packet.state == requested_packet.state else "no",
            )
            if packet.state != requested_packet.state:
                _log_control_event(
                    runtime_entry,
                    "post-TX confirmation ignored mismatched state observed=%s expected=%s",
                    _state_summary(packet.state),
                    _state_summary(requested_packet.state),
                )
                continue
            await async_apply_observed_packet(hass, runtime_entry, packet)
            return

        if (revision, epoch) != (runtime_entry.control_intents.revision, runtime_entry.control_intents.epoch):
            return
        runtime_entry.operational_status = OPERATIONAL_STATUS_READY
        runtime_entry.state_confidence = STATE_CONFIDENCE_REQUESTED
        await async_persist_runtime_entry_state(hass, runtime_entry)
        _log_control_event(
            runtime_entry,
            "post-TX confirmation window expired without observed packet; keeping requested state=%s",
            _state_summary(runtime_current_state(runtime_entry)),
        )
        async_notify_runtime_entry_updated(hass, runtime_entry.config_entry_id)
    except asyncio.CancelledError:
        raise
    except Exception:
        _LOGGER.exception(
            "Proflame2 confirmation listen failed config_entry_id=%s",
            runtime_entry.config_entry_id,
        )
        if runtime_entry.debug_logging_enabled:
            get_packet_debug_logger().exception(
                "control: config_entry_id=%s backend=%s confirmation listen failed",
                runtime_entry.config_entry_id,
                runtime_entry.backend_type,
            )
        raise
    finally:
        if (revision, epoch) == (
            runtime_entry.control_intents.revision,
            runtime_entry.control_intents.epoch,
        ) and runtime_entry.desired_state is None:
            await _async_end_confirmation_rx(runtime_entry)
            if not runtime_entry.active_listening_enabled:
                await _async_stop_rx(runtime_entry)
        if runtime_entry.confirmation_task is asyncio.current_task():
            runtime_entry.confirmation_task = None


async def async_register_services(hass: HomeAssistant) -> None:
    """Register domain services once."""

    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(DATA_SERVICES_REGISTERED):
        return

    async def handle_set_state(call: ServiceCall) -> None:
        runtime_entry = _resolve_runtime_entry(hass, call)
        await async_execute_set_state(
            hass,
            runtime_entry,
            call.data,
            source="homeassistant_service",
        )

    async def handle_apply_profile(call: ServiceCall) -> None:
        runtime_entry = _resolve_runtime_entry(hass, call)
        await async_execute_apply_profile(
            hass,
            runtime_entry,
            str(call.data[CONF_PROFILE_ID]).strip().lower(),
            source="saved_profile",
        )

    async def handle_display_state_update(call: ServiceCall) -> None:
        runtime_entry = _resolve_runtime_entry(hass, call)
        await async_execute_display_state_update(hass, runtime_entry, call.data)

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_STATE,
        handle_set_state,
        schema=SERVICE_SET_STATE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_APPLY_PROFILE,
        handle_apply_profile,
        schema=SERVICE_APPLY_PROFILE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DISPLAY_STATE_UPDATE,
        handle_display_state_update,
        schema=SERVICE_DISPLAY_STATE_UPDATE_SCHEMA,
    )
    domain_data[DATA_SERVICES_REGISTERED] = True


async def async_unregister_services(hass: HomeAssistant) -> None:
    """Unregister domain services when no entries remain."""

    domain_data = hass.data.setdefault(DOMAIN, {})
    if not domain_data.get(DATA_SERVICES_REGISTERED):
        return
    hass.services.async_remove(DOMAIN, SERVICE_SET_STATE)
    hass.services.async_remove(DOMAIN, SERVICE_APPLY_PROFILE)
    hass.services.async_remove(DOMAIN, SERVICE_DISPLAY_STATE_UPDATE)
    domain_data[DATA_SERVICES_REGISTERED] = False


async def async_execute_display_state_update(
    hass: HomeAssistant,
    runtime_entry: Proflame2RuntimeEntry,
    data: dict[str, Any],
) -> None:
    """Push display-only state to an ESPHome endpoint without transmitting RF."""

    del hass
    backend = runtime_entry.backend
    if backend is None:
        raise HomeAssistantError("No RF backend is available for this fireplace.")
    update_display_state = getattr(backend, "update_display_state", None)
    if not callable(update_display_state):
        raise HomeAssistantError("Display state update is only supported for ESPHome backends.")

    display_state = ESPHomeDisplayState(
        power=data.get(CONF_POWER),
        flame=data.get(CONF_FLAME),
        fan=data.get(CONF_FAN),
        light=data.get(CONF_LIGHT),
        pilot=data.get(CONF_PILOT),
        thermostat=data.get(CONF_THERMOSTAT),
        front=data.get(CONF_FRONT),
        aux=data.get(CONF_AUX),
        action_label=data.get(CONF_ACTION_LABEL),
        fireplace_name=runtime_entry.display_short_name,
    )
    await update_display_state(display_state)


async def async_sync_runtime_display_state(
    hass: HomeAssistant,
    runtime_entry: Proflame2RuntimeEntry,
    *,
    action_label: str | None = None,
    force: bool = False,
) -> None:
    """Push the current known HA/runtime state to the ESPHome display endpoint."""

    backend = runtime_entry.backend
    if backend is None or runtime_entry.learning_in_progress:
        return
    await async_sync_runtime_rx_policy(runtime_entry)
    if runtime_entry.active_listening_enabled:
        await async_start_active_listener(hass, runtime_entry)
    await _async_push_runtime_display_state(
        runtime_entry,
        action_label=action_label,
        force=force,
    )


async def _async_push_runtime_display_state(
    runtime_entry: Proflame2RuntimeEntry,
    *,
    action_label: str | None = None,
    force: bool = False,
) -> None:
    """Push current runtime state without changing receive policy."""

    # Existing firmware treats changed display settings as grounds to cancel a
    # repeat. HA-only intent must not suppress the last accepted command's retry.
    if runtime_entry.desired_state is not None:
        return
    backend = runtime_entry.backend
    if backend is None:
        return
    update_display_state = getattr(backend, "update_display_state", None)
    if not callable(update_display_state):
        return

    state = runtime_current_state(runtime_entry)
    if state is None:
        return

    signature = (
        state.power,
        state.flame,
        state.fan,
        state.light,
        state.thermostat,
        state.front,
        state.aux,
        runtime_entry.display_short_name,
    )
    if not force and action_label is None and runtime_entry.last_display_sync_signature == signature:
        return

    display_state = ESPHomeDisplayState(
        power=state.power,
        flame=state.flame,
        fan=state.fan,
        light=state.light,
        pilot=None,
        thermostat=state.thermostat,
        front=state.front,
        aux=state.aux,
        action_label=action_label,
        fireplace_name=runtime_entry.display_short_name,
    )
    await update_display_state(display_state)
    runtime_entry.last_display_sync_signature = signature
    runtime_entry.last_display_sync_monotonic = asyncio.get_running_loop().time()


async def async_sync_runtime_rx_policy(runtime_entry: Proflame2RuntimeEntry) -> None:
    """Converge ESPHome RX policy with HA options/profile state.

    ESPHome firmware restarts reset RX capture state. HA may still have a live
    integration runtime, so RX policy must be reapplied through the same
    periodic controller sync path used for display/config convergence.
    """

    backend = runtime_entry.backend
    if backend is None or runtime_entry.learning_in_progress:
        return
    set_active_listening_enabled = getattr(backend, "set_active_listening_enabled", None)
    if not callable(set_active_listening_enabled):
        return
    maybe_awaitable = set_active_listening_enabled(
        runtime_entry.active_listening_enabled,
        runtime_entry.remote_profile,
    )
    if inspect.isawaitable(maybe_awaitable):
        await maybe_awaitable


def _resolve_runtime_entry(hass: HomeAssistant, call: ServiceCall) -> Proflame2RuntimeEntry:
    """Resolve the target fireplace runtime for a service call."""

    runtime_entries = async_get_runtime_entries(hass)
    if not runtime_entries:
        raise HomeAssistantError("No Proflame2 fireplaces are configured.")

    device_ids = _coerce_target_ids(call.data.get(ATTR_DEVICE_ID))
    entity_ids = _coerce_target_ids(call.data.get(ATTR_ENTITY_ID))
    area_ids = _coerce_target_ids(call.data.get(ATTR_AREA_ID))
    if entity_ids or area_ids:
        raise HomeAssistantError("Proflame2 services currently support device targets or config_entry_id only.")

    data_config_entry_id = call.data.get(CONF_CONFIG_ENTRY_ID)

    candidates: list[Proflame2RuntimeEntry] = []
    if data_config_entry_id is not None:
        runtime_entry = runtime_entries.get(data_config_entry_id)
        if runtime_entry is None:
            raise HomeAssistantError(f"Unknown Proflame2 config_entry_id: {data_config_entry_id}")
        candidates.append(runtime_entry)

    if device_ids:
        if len(device_ids) != 1:
            raise HomeAssistantError("Target exactly one Proflame2 device per service call.")
        targeted = [entry for entry in runtime_entries.values() if entry.device_id == device_ids[0]]
        if not targeted:
            raise HomeAssistantError("No Proflame2 fireplace matches the targeted device.")
        if len(targeted) > 1:
            raise HomeAssistantError(
                "Ambiguous Proflame2 device target: multiple fireplaces share this device. "
                "Target a specific config_entry_id or entity/backend instead."
            )
        if candidates and targeted[0].config_entry_id != candidates[0].config_entry_id:
            raise HomeAssistantError("config_entry_id and device target refer to different fireplaces.")
        candidates = targeted

    if not candidates:
        if len(runtime_entries) == 1:
            return next(iter(runtime_entries.values()))
        raise HomeAssistantError(
            "Multiple Proflame2 fireplaces are configured; specify config_entry_id or target a device."
        )

    return candidates[0]


def _coerce_target_ids(raw_value: Any) -> list[str]:
    """Normalize a Home Assistant service target field into a list of ids."""

    if raw_value is None:
        return []
    if isinstance(raw_value, str):
        return [raw_value]
    if isinstance(raw_value, list):
        return [str(item) for item in raw_value]
    return [str(raw_value)]


def _ensure_runtime_entry_accepts_actions(runtime_entry: Proflame2RuntimeEntry) -> None:
    """Fail fast when an entry is shutting down or unavailable."""

    if runtime_entry.shutting_down:
        reason = runtime_entry.shutdown_reason or "shutdown"
        raise HomeAssistantError(
            f"This Proflame2 fireplace is shutting down ({reason}); new actions are temporarily unavailable."
        )
    if runtime_entry.learning_in_progress:
        raise HomeAssistantError("Controller is learning a remote; try again after learning finishes.")


async def async_execute_set_state(
    hass: HomeAssistant,
    runtime_entry: Proflame2RuntimeEntry,
    data: dict[str, Any],
    *,
    source: str,
    applied_profile_id: str | None = None,
    applied_profile_name: str | None = None,
) -> None:
    """Execute the shared atomic state-application path for all services."""

    _ensure_runtime_entry_accepts_actions(runtime_entry)
    _log_control_event(
        runtime_entry,
        "service action received source=%s data=%s",
        source,
        {key: value for key, value in data.items() if key != ATTR_ENTITY_ID},
    )
    try:
        requested_state, ignored_warnings = build_requested_state(runtime_entry.features, data)
    except StateValidationError as exc:
        runtime_entry.last_error = str(exc)
        _log_control_event(
            runtime_entry,
            "service validation failed source=%s error=%s",
            source,
            runtime_entry.last_error,
            level=logging.ERROR,
        )
        async_notify_runtime_entry_updated(hass, runtime_entry.config_entry_id)
        raise HomeAssistantError(str(exc)) from exc

    await _async_execute_requested_state(
        hass,
        runtime_entry,
        requested_state,
        source=source,
        warnings=ignored_warnings,
        applied_profile_id=applied_profile_id,
        applied_profile_name=applied_profile_name,
        clear_active_profile=(source != "saved_profile"),
    )


async def async_execute_apply_profile(
    hass: HomeAssistant,
    runtime_entry: Proflame2RuntimeEntry,
    profile_id: str,
    *,
    source: str,
) -> None:
    """Apply one saved profile through the shared atomic send path."""

    _ensure_runtime_entry_accepts_actions(runtime_entry)
    normalized_profile_id = str(profile_id).strip().lower()
    profile = (runtime_entry.saved_profiles or {}).get(normalized_profile_id)
    if profile is None:
        runtime_entry.last_error = f"Unknown saved profile '{normalized_profile_id}' for this fireplace."
        _log_control_event(
            runtime_entry,
            "apply profile failed source=%s profile_id=%s error=%s",
            source,
            normalized_profile_id,
            runtime_entry.last_error,
            level=logging.ERROR,
        )
        async_notify_runtime_entry_updated(hass, runtime_entry.config_entry_id)
        raise HomeAssistantError(runtime_entry.last_error)

    _log_control_event(
        runtime_entry,
        "apply profile requested source=%s profile_id=%s profile_name=%s",
        source,
        normalized_profile_id,
        profile[CONF_NAME],
    )
    await async_execute_set_state(
        hass,
        runtime_entry,
        profile,
        source=source,
        applied_profile_id=profile[CONF_PROFILE_ID],
        applied_profile_name=profile[CONF_NAME],
    )


async def _async_execute_requested_state(
    hass: HomeAssistant,
    runtime_entry: Proflame2RuntimeEntry,
    requested_state,
    *,
    source: str,
    warnings: tuple[str, ...] = (),
    applied_profile_id: str | None = None,
    applied_profile_name: str | None = None,
    clear_active_profile: bool = False,
) -> None:
    """Submit explicit services and execute only on the single owning dispatcher."""

    if runtime_entry.active_send_task is not asyncio.current_task():
        completion = asyncio.get_running_loop().create_future()
        # A caller may disconnect while physical TX continues; always retrieve
        # its terminal exception without changing what active waiters observe.
        completion.add_done_callback(lambda future: None if future.cancelled() else future.exception())
        _submit_control_intent(
            hass,
            runtime_entry,
            requested_state,
            source=source,
            warnings=warnings,
            profile_id=applied_profile_id,
            profile_name=applied_profile_name,
            clear_active_profile=clear_active_profile,
            completion=completion,
        )
        await asyncio.shield(completion)
        return

    await _async_transmit_requested_state(
        hass,
        runtime_entry,
        requested_state,
        source=source,
        warnings=warnings,
        applied_profile_id=applied_profile_id,
        applied_profile_name=applied_profile_name,
        clear_active_profile=clear_active_profile,
    )


async def _async_transmit_requested_state(
    hass: HomeAssistant,
    runtime_entry: Proflame2RuntimeEntry,
    requested_state,
    *,
    source: str,
    warnings: tuple[str, ...] = (),
    applied_profile_id: str | None = None,
    applied_profile_name: str | None = None,
    clear_active_profile: bool = False,
) -> None:
    """Send one already-validated full-state request and update runtime state."""

    _ensure_runtime_entry_accepts_actions(runtime_entry)
    send_epoch = runtime_entry.control_intents.epoch
    request_summary = _state_summary(requested_state)
    prepared_request: _PreparedSendRequest | None = None
    try:
        await _async_begin_send_execution(hass, runtime_entry, requested_state, source=source, warnings=warnings)
        if send_epoch != runtime_entry.control_intents.epoch:
            raise HomeAssistantError("Control sequence ended before transmission started.")
        _ensure_send_backend_available(hass, runtime_entry, source=source)
        prepared_request = _prepare_send_request(
            runtime_entry,
            requested_state,
            source=source,
            warnings=warnings,
            request_summary=request_summary,
        )
        send_result = await _async_send_prepared_request(runtime_entry, prepared_request)
    except asyncio.TimeoutError as exc:
        _mark_send_failed(
            runtime_entry,
            (
                f"Transmit timed out after {BACKEND_SEND_TIMEOUT_SECONDS:.0f} seconds; "
                "controls reverted to last known state."
            ),
            expected_epoch=send_epoch,
        )
        _log_control_event(
            runtime_entry,
            "send execution failed source=%s state=%s exception=%s error=%s",
            source,
            request_summary,
            "TimeoutError: backend send timed out",
            runtime_entry.last_error,
            level=logging.ERROR,
        )
        async_notify_runtime_entry_updated(hass, runtime_entry.config_entry_id)
        raise HomeAssistantError(runtime_entry.last_error) from exc
    except asyncio.CancelledError:
        _mark_send_failed(
            runtime_entry, "Transmit cancelled; controls reverted to last known state.", expected_epoch=send_epoch
        )
        _log_control_event(
            runtime_entry,
            "send execution cancelled source=%s state=%s",
            source,
            request_summary,
            level=logging.ERROR,
        )
        async_notify_runtime_entry_updated(hass, runtime_entry.config_entry_id)
        raise
    except (NotImplementedError, RuntimeError) as exc:
        _mark_send_failed(runtime_entry, _transmit_failure_message(str(exc)), expected_epoch=send_epoch)
        _log_control_event(
            runtime_entry,
            "send execution failed source=%s exception=%s error=%s",
            source,
            f"{type(exc).__name__}: {exc}",
            runtime_entry.last_error,
            level=logging.ERROR,
        )
        async_notify_runtime_entry_updated(hass, runtime_entry.config_entry_id)
        raise HomeAssistantError(runtime_entry.last_error) from exc
    except Exception as exc:
        _mark_send_failed(
            runtime_entry, _transmit_failure_message(f"{type(exc).__name__}: {exc}"), expected_epoch=send_epoch
        )
        _LOGGER.exception(
            "Proflame2 send execution exception config_entry_id=%s backend=%s source=%s state=%s",
            runtime_entry.config_entry_id,
            runtime_entry.backend_type,
            source,
            request_summary,
        )
        if runtime_entry.debug_logging_enabled:
            get_packet_debug_logger().exception(
                "control: config_entry_id=%s backend=%s send execution exception source=%s state=%s exception_type=%s error=%s",
                runtime_entry.config_entry_id,
                runtime_entry.backend_type,
                source,
                request_summary,
                type(exc).__name__,
                exc,
            )
        _log_control_event(
            runtime_entry,
            "send execution exception source=%s state=%s exception=%s",
            source,
            request_summary,
            f"{type(exc).__name__}: {exc}",
            level=logging.ERROR,
        )
        async_notify_runtime_entry_updated(hass, runtime_entry.config_entry_id)
        raise HomeAssistantError(runtime_entry.last_error) from exc

    if not runtime_entry.control_intents.owns(prepared_request.intent):
        return
    try:
        await _async_record_successful_send(
            hass,
            runtime_entry,
            prepared_request,
            send_result,
            applied_profile_id=applied_profile_id,
            applied_profile_name=applied_profile_name,
            clear_active_profile=clear_active_profile,
        )
        await _async_finish_send_confirmation_policy(hass, runtime_entry, prepared_request)
    except Exception as exc:
        # Local RF completion is already known. Persistence/display/RX errors
        # must not turn it into a failed TX or discard a newer pending intent.
        if not runtime_entry.control_intents.owns(prepared_request.intent):
            return
        runtime_entry.last_error = f"Transmit completed; post-TX update failed: {exc}"
        runtime_entry.operational_status = (
            OPERATIONAL_STATUS_PENDING if runtime_entry.control_intents.pending else OPERATIONAL_STATUS_READY
        )
        runtime_entry.desired_state = (
            runtime_entry.control_intents.pending.state if runtime_entry.control_intents.pending else None
        )
        _LOGGER.warning("Proflame2 post-TX update failed", exc_info=True)
        async_notify_runtime_entry_updated(hass, runtime_entry.config_entry_id)


async def _async_begin_send_execution(
    hass: HomeAssistant,
    runtime_entry: Proflame2RuntimeEntry,
    requested_state,
    *,
    source: str,
    warnings: tuple[str, ...],
) -> str:
    """Move runtime state into the sending phase and stop pending RX/tasks."""

    request_summary = _state_summary(requested_state)
    if runtime_entry.debounce_task is not asyncio.current_task():
        _cancel_runtime_task(
            runtime_entry,
            runtime_entry.debounce_task,
            reason=f"start_send_execution:{source}",
            kind="debounce",
        )
    runtime_entry.debounce_task = None
    _cancel_runtime_task(
        runtime_entry,
        runtime_entry.confirmation_task,
        reason=f"start_send_execution:{source}",
        kind="confirmation",
    )
    runtime_entry.confirmation_task = None
    intent = runtime_entry.control_intents.in_flight
    await _async_stop_rx(runtime_entry)
    if not runtime_entry.control_intents.owns(intent):
        raise HomeAssistantError("Control sequence ended before transmission started.")

    runtime_entry.last_send_result = None
    runtime_entry.last_error = None
    runtime_entry.sending_in_progress = True
    runtime_entry.operational_status = OPERATIONAL_STATUS_SENDING
    _log_control_event(
        runtime_entry,
        "send execution started source=%s state=%s warnings=%s",
        source,
        request_summary,
        warnings,
    )
    async_notify_runtime_entry_updated(hass, runtime_entry.config_entry_id)
    return request_summary


def _ensure_send_backend_available(hass: HomeAssistant, runtime_entry: Proflame2RuntimeEntry, *, source: str) -> None:
    """Fail the send workflow before packet construction when no backend exists."""

    if runtime_entry.backend is None:
        runtime_entry.sending_in_progress = False
        runtime_entry.operational_status = OPERATIONAL_STATUS_UNAVAILABLE
        runtime_entry.last_error = "No RF backend is available for this fireplace."
        runtime_entry.desired_state = None
        _log_control_event(
            runtime_entry,
            "send execution failed source=%s error=%s",
            source,
            runtime_entry.last_error,
            level=logging.ERROR,
        )
        async_notify_runtime_entry_updated(hass, runtime_entry.config_entry_id)
        raise HomeAssistantError(runtime_entry.last_error)


def _prepare_send_request(
    runtime_entry: Proflame2RuntimeEntry,
    requested_state,
    *,
    source: str,
    warnings: tuple[str, ...],
    request_summary: str,
) -> _PreparedSendRequest:
    """Build the authoritative packet and display metadata for one send."""

    _log_control_event(
        runtime_entry,
        "packet build start source=%s state=%s",
        source,
        request_summary,
    )
    packet = encode_packet(
        requested_state,
        runtime_entry.remote_profile,
        source=source,
        warnings=warnings,
        allow_power_off_flame=(source == "debounced_control"),
    )
    packet.transmission_plan = build_transmission_plan(packet.frame)
    packet.display_state = ESPHomeDisplayState(
        power=requested_state.power,
        flame=requested_state.flame,
        fan=requested_state.fan,
        light=requested_state.light,
        pilot=None,
        thermostat=requested_state.thermostat,
        front=requested_state.front,
        aux=requested_state.aux,
        action_label=("Power OFF" if not requested_state.power else f"Flame {requested_state.flame}"),
        status_text="Sending...",
    )
    prepared_request = _PreparedSendRequest(
        requested_state=requested_state,
        request_summary=request_summary,
        packet=packet,
        source=source,
        intent=runtime_entry.control_intents.in_flight,
    )
    _log_pre_send_packet(runtime_entry, prepared_request)
    return prepared_request


def _log_pre_send_packet(runtime_entry: Proflame2RuntimeEntry, prepared_request: _PreparedSendRequest) -> None:
    """Log the stable packet-level pre-send diagnostic line."""

    requested_state = prepared_request.requested_state
    packet = prepared_request.packet
    _log_control_event(
        runtime_entry,
        "PROFLAME_TX_PRESEND source=%s controller_id=%s linked_entry_id=%s protocol_variant=%s state=power=%s flame=%s fan=%s light=%s front=%s aux=%s thermostat=%s cpi=%s serial_id=%06x c1=%s d1=%s c2=%s d2=%s cmd1=0x%02X err1=0x%02X cmd2=0x%02X err2=0x%02X wire_words=%s air_payload_hex=%s payload_bit_length=%s repeat_count=%s",
        prepared_request.source,
        runtime_entry.backend_type,
        _linked_backend_entry_id(runtime_entry),
        runtime_entry.remote_profile.protocol_variant,
        requested_state.power,
        requested_state.flame,
        requested_state.fan,
        requested_state.light,
        requested_state.front,
        requested_state.aux,
        requested_state.thermostat,
        requested_state.cpi,
        runtime_entry.remote_profile.serial_id,
        runtime_entry.remote_profile.ecc.c1,
        runtime_entry.remote_profile.ecc.d1,
        runtime_entry.remote_profile.ecc.c2,
        runtime_entry.remote_profile.ecc.d2,
        packet.frame.cmd1,
        packet.frame.err1,
        packet.frame.cmd2,
        packet.frame.err2,
        " ".join(f"{word:02X}" for word in packet.frame.wire_words),
        packet.transmission_plan.air_payload.hex(),
        packet.transmission_plan.air_payload_bit_length,
        packet.transmission_plan.repeat_count,
    )


async def _async_send_prepared_request(runtime_entry: Proflame2RuntimeEntry, prepared_request: _PreparedSendRequest):
    """Send the prepared packet through the configured backend."""

    _log_control_event(
        runtime_entry,
        "backend send start source=%s backend=%s state=%s",
        prepared_request.source,
        runtime_entry.backend_type,
        prepared_request.request_summary,
    )
    return await asyncio.wait_for(
        runtime_entry.backend.send(prepared_request.packet),
        timeout=BACKEND_SEND_TIMEOUT_SECONDS,
    )


def _mark_send_failed(
    runtime_entry: Proflame2RuntimeEntry, error_message: str, *, expected_epoch: int | None = None
) -> None:
    """Apply the common runtime state for failed or cancelled sends."""

    if expected_epoch is not None and expected_epoch != runtime_entry.control_intents.epoch:
        return
    runtime_entry.sending_in_progress = False
    runtime_entry.operational_status = OPERATIONAL_STATUS_FAILED
    runtime_entry.last_error = error_message
    runtime_entry.last_send_result = None
    runtime_entry.desired_state = None
    for intent in runtime_entry.control_intents.invalidate():
        _finish_intent(intent, HomeAssistantError(error_message))


async def _async_record_successful_send(
    hass: HomeAssistant,
    runtime_entry: Proflame2RuntimeEntry,
    prepared_request: _PreparedSendRequest,
    send_result,
    *,
    applied_profile_id: str | None,
    applied_profile_name: str | None,
    clear_active_profile: bool,
) -> None:
    """Persist the optimistic requested state after a backend send succeeds."""

    if not runtime_entry.control_intents.owns(prepared_request.intent):
        return
    runtime_entry.sending_in_progress = False
    runtime_entry.last_send_result = send_result
    runtime_entry.last_error = None
    _log_control_event(
        runtime_entry,
        "send execution succeeded source=%s backend=%s state=%s",
        prepared_request.source,
        send_result.backend_name,
        prepared_request.request_summary,
    )
    if clear_active_profile:
        runtime_entry.last_applied_profile_id = None
        runtime_entry.last_applied_profile_name = None
        runtime_entry.active_profile_state = None
    else:
        runtime_entry.last_applied_profile_id = applied_profile_id
        runtime_entry.last_applied_profile_name = applied_profile_name
        runtime_entry.active_profile_state = prepared_request.requested_state

    await async_set_runtime_current_state(
        hass,
        runtime_entry,
        prepared_request.requested_state,
        source=prepared_request.source,
        confidence=STATE_CONFIDENCE_REQUESTED,
        packet=prepared_request.packet,
        notify=False,
    )
    if not runtime_entry.control_intents.owns(prepared_request.intent):
        return
    runtime_entry.desired_state = (
        runtime_entry.control_intents.pending.state if runtime_entry.control_intents.pending is not None else None
    )


async def _async_finish_send_confirmation_policy(
    hass: HomeAssistant,
    runtime_entry: Proflame2RuntimeEntry,
    prepared_request: _PreparedSendRequest,
) -> None:
    """Resume active listening or start post-TX confirmation after send success."""

    if not runtime_entry.control_intents.owns(prepared_request.intent):
        return
    if runtime_entry.control_intents.pending is not None:
        runtime_entry.operational_status = OPERATIONAL_STATUS_PENDING
        if runtime_entry.active_listening_enabled:
            await async_start_active_listener(hass, runtime_entry)
        async_notify_runtime_entry_updated(hass, runtime_entry.config_entry_id)
        return

    if runtime_entry.active_listening_enabled:
        runtime_entry.operational_status = OPERATIONAL_STATUS_CONFIRMING
        await async_start_active_listener(hass, runtime_entry)
        _log_control_event(
            runtime_entry,
            "send execution entering active listening confirmation state=%s",
            _state_summary(prepared_request.requested_state),
        )
        async_notify_runtime_entry_updated(hass, runtime_entry.config_entry_id)
        return

    if await _should_start_confirmation(hass, runtime_entry):
        runtime_entry.operational_status = OPERATIONAL_STATUS_CONFIRMING
        _log_control_event(
            runtime_entry,
            "send execution succeeded; starting post-TX confirmation state=%s",
            _state_summary(prepared_request.requested_state),
        )
        runtime_entry.confirmation_task = hass.async_create_background_task(
            _async_confirmation_task(hass, runtime_entry, prepared_request.packet),
            f"{DOMAIN}_{runtime_entry.config_entry_id}_confirmation",
            eager_start=False,
        )
    else:
        runtime_entry.operational_status = OPERATIONAL_STATUS_READY
        _log_control_event(
            runtime_entry,
            "send execution complete without confirmation state=%s confidence=%s",
            _state_summary(prepared_request.requested_state),
            STATE_CONFIDENCE_REQUESTED,
        )
    async_notify_runtime_entry_updated(hass, runtime_entry.config_entry_id)
