"""Coordinate YardStick band changes with the existing exclusive RF owner."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from homeassistant.exceptions import HomeAssistantError

from .const import BACKEND_YARDSTICK, CONF_RF_BAND, OPERATIONAL_STATUS_READY, OPERATIONAL_STATUS_UNAVAILABLE
from .rf.band import yardstick_band
from .rf.yardstick import YardStickBackend

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .runtime import Proflame2RuntimeEntry


async def async_set_yardstick_band(
    hass: HomeAssistant, config_entry: ConfigEntry, runtime: Proflame2RuntimeEntry, selected: str
) -> None:
    """Save a band only after applying it, fencing old pending commands first.

    Reconfiguration waits for an in-flight send; it never cancels physical TX.
    Learning is rejected rather than interrupted. Failure keeps the saved band
    and blocks fireplace controls until a band configuration retry succeeds.
    """
    from .runtime import async_notify_runtime_entry_updated
    from .services import _async_stop_rx, _control_arbiter, _finish_intent, async_start_active_listener

    band = yardstick_band(selected)
    if runtime.backend_type != BACKEND_YARDSTICK or not isinstance(runtime.backend, YardStickBackend):
        raise HomeAssistantError("RF band selection is only available on the YardStick controller.")
    if runtime.shutting_down or runtime.radio_configuration_in_progress:
        raise HomeAssistantError("Controller is busy; try again after its current operation finishes.")
    arbiter = _control_arbiter(hass, runtime)
    if runtime.learning_in_progress or arbiter.learning_owner is not None:
        raise HomeAssistantError("Controller is learning; finish learning before changing its RF band.")
    if selected == runtime.rf_band and runtime.radio_configuration_error is None:
        return

    runtime.radio_configuration_in_progress = True
    runtime.refresh_schedule.suspended = True
    # Discard the pending snapshot without invalidating an already executing
    # send. It finishes on its original band under the same arbiter lock.
    pending = runtime.control_intents.pending
    runtime.control_intents.pending = None
    runtime.control_intents.wake.set()
    if pending is not None:
        _finish_intent(pending, HomeAssistantError("Control cancelled because the controller RF band is changing."))
    runtime.desired_state = runtime.control_intents.desired_state
    async_notify_runtime_entry_updated(hass, runtime.config_entry_id)
    try:
        async with arbiter.lock:
            if runtime.shutting_down:
                raise HomeAssistantError("Controller is shutting down.")
            # Receive tasks can outlive the TX lock. End old-band confirmation
            # and observation before retuning; they must not promote stale RX.
            rx_tasks = [task for task in (runtime.confirmation_task, runtime.active_listener_task) if task is not None]
            for task in rx_tasks:
                task.cancel()
            if rx_tasks:
                await asyncio.gather(*rx_tasks, return_exceptions=True)
            runtime.confirmation_task = None
            runtime.active_listener_task = None
            await _async_stop_rx(runtime)
            await runtime.backend.configure_frequencies(
                rx_frequency_hz=band.rx_frequency_hz, tx_frequency_hz=band.tx_frequency_hz
            )
            if runtime.shutting_down:
                raise HomeAssistantError("Controller stopped before RF configuration completed.")
            hass.config_entries.async_update_entry(config_entry, data={**config_entry.data, CONF_RF_BAND: selected})
            runtime.rf_band = selected
            runtime.radio_configuration_error = None
            arbiter.readiness_unknown = False
            arbiter.uncertain_request_id = None
            arbiter.recovery_deadline = 0.0
            runtime.last_error = None
            runtime.operational_status = OPERATIONAL_STATUS_READY
            runtime.refresh_schedule.restart()
    except BaseException as exc:
        # Cancellation also leaves the effective radio configuration uncertain.
        runtime.radio_configuration_error = "RF band configuration did not complete. Select a band again to retry."
        runtime.last_error = runtime.radio_configuration_error
        runtime.operational_status = OPERATIONAL_STATUS_UNAVAILABLE
        if isinstance(exc, Exception):
            raise HomeAssistantError(f"Unable to configure YardStick RF band: {exc}") from exc
        raise
    finally:
        runtime.radio_configuration_in_progress = False
        runtime.refresh_schedule.suspended = runtime.radio_configuration_error is not None
        async_notify_runtime_entry_updated(hass, runtime.config_entry_id)
    # Preserve an explicitly enabled existing listener; normal YardStick
    # installations remain TX-only, with no new receive loop.
    await async_start_active_listener(hass, runtime)
