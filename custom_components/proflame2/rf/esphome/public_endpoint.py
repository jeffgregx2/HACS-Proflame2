"""Public HA bindings for one explicitly selected ESPHome controller."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable, Iterable

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import slugify

from ...device_registry_compat import device_registry_compatibility


class ESPHomeControllerUnavailableError(RuntimeError):
    """The selected controller's public actions or telemetry are not ready."""


class ESPHomeControllerOfflineError(ESPHomeControllerUnavailableError):
    """Required public telemetry is unavailable, with no known physical cause."""


CRITICAL_TELEMETRY = frozenset(
    {
        "proflame2_endpoint_status",
        "proflame2_last_request_id",
        "proflame2_last_tx_result",
        "proflame2_last_payload_length",
        "proflame2_tx_success_count",
        "proflame2_tx_failure_count",
    }
)


def _matches_telemetry(unique_id: str, object_id: str) -> bool:
    """Support ESPHome's object-ID and unmangled-name registry identities.

    HA/ESPHome migrates v1 mac-type-object IDs to v3 mac/device/type/name IDs.
    v2 uses mac-type-name. These are firmware names encoded in unique IDs,
    not editable HA entity IDs or user-assigned display names.
    """
    if unique_id.endswith(f"-{object_id}"):
        return True
    parts = unique_id.split("/", 3)
    if len(parts) == 4 and parts[2] in {"sensor", "text_sensor"}:
        return slugify(parts[3]) == object_id
    for kind in ("-text_sensor-", "-sensor-"):
        if kind in unique_id:
            return slugify(unique_id.split(kind, 1)[1]) == object_id
    return False


class PublicESPHomeEndpoint:
    """Own registry bindings and public lifecycle subscriptions, never a client."""

    def __init__(self, hass: HomeAssistant, entry_id: str, object_ids: Iterable[str]) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.object_ids = frozenset(object_ids)
        self.entities: dict[str, str] = {}
        self.entity_errors: dict[str, str] = {}
        self.device_id: str | None = None
        self.generation = 0
        self.changed = asyncio.Event()
        self._dirty = True
        self._unsubscribers: list[Callable[[], None]] = []
        self._state_unsub: Callable[[], None] | None = None
        self._was_available: bool | None = None
        self._action_names: set[str] = set()

    @property
    def entry(self):
        entry = self.hass.config_entries.async_get_entry(self.entry_id)
        return entry if entry is not None and entry.domain == "esphome" else None

    def action(self, name: str, *, required: bool = False) -> str | None:
        self._action_names.add(name)
        entry = self.entry
        node = entry.data.get("device_name") if entry is not None else None
        service = f"{node.replace('-', '_')}_{name}" if isinstance(node, str) and node else None
        if service is not None and self.hass.services.has_service("esphome", service):
            return service
        if required:
            raise ESPHomeControllerUnavailableError(f"Linked ESPHome entry does not expose action {name!r}")
        return None

    def resolve(self) -> None:
        # Standalone diagnostic readers have no subscriptions: resolve fresh.
        if not self._dirty and self._unsubscribers:
            return
        entries = er.async_entries_for_config_entry(er.async_get(self.hass), self.entry_id)
        self.entities = {}
        self.entity_errors = {}
        for object_id in self.object_ids:
            matches = [
                item for item in entries if item.platform == "esphome" and _matches_telemetry(item.unique_id, object_id)
            ]
            if len(matches) != 1:
                self.entity_errors[object_id] = "ambiguous" if matches else "missing"
            elif matches[0].disabled_by is not None:
                self.entity_errors[object_id] = f"disabled: {matches[0].entity_id}"
            else:
                self.entities[object_id] = matches[0].entity_id
        status_id = self.entities.get("proflame2_endpoint_status")
        status_entry = er.async_get(self.hass).async_get(status_id) if status_id else None
        devices = dr.async_entries_for_config_entry(dr.async_get(self.hass), self.entry_id)
        device_ids = {device.id for device in devices}
        self.device_id = status_entry.device_id if status_entry else None
        if self.device_id is not None and self.device_id not in device_ids:
            raise ESPHomeControllerUnavailableError("Endpoint telemetry belongs to an unrelated ESPHome device")
        if self.device_id is None:
            if len(devices) > 1:
                raise ESPHomeControllerUnavailableError("Linked ESPHome controller device is ambiguous")
            self.device_id = devices[0].id if devices else None
        for object_id, entity_id in list(self.entities.items()):
            item = er.async_get(self.hass).async_get(entity_id)
            if item and item.device_id and self.device_id and item.device_id != self.device_id:
                self.entities.pop(object_id)
                self.entity_errors[object_id] = "belongs to a different device"
        self._dirty = False
        if self._unsubscribers:
            self._subscribe_states()

    def text(self, object_id: str) -> str | None:
        self.resolve()
        entity_id = self.entities.get(object_id)
        state = self.hass.states.get(entity_id) if entity_id is not None else None
        if state is None or state.state in {STATE_UNKNOWN, STATE_UNAVAILABLE}:
            return None
        return state.state

    def integer(self, object_id: str) -> int | None:
        value = self.text(object_id)
        try:
            number = float(value)
            return int(number) if math.isfinite(number) else None
        except (TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def _state_available(object_id: str, state: State | None) -> bool:
        if state is None or state.state == STATE_UNAVAILABLE:
            return False
        # Released firmware does not publish its initially empty request ID.
        # HA therefore reports unknown until the first TX. This is not a link
        # failure; text() still returns None, so it cannot confirm any request.
        return state.state != STATE_UNKNOWN or object_id == "proflame2_last_request_id"

    def require_ready(self) -> None:
        entry = self.entry
        if entry is None or entry.state is not ConfigEntryState.LOADED:
            raise ESPHomeControllerUnavailableError("Linked ESPHome entry is not loaded")
        self.action("proflame2_tx_stateful", required=True)
        self.resolve()
        errors = [
            f"{key} ({self.entity_errors[key]})" for key in sorted(CRITICAL_TELEMETRY & self.entity_errors.keys())
        ]
        if errors:
            raise ESPHomeControllerUnavailableError(
                "Enable the required telemetry entities on the linked ESPHome device: " + ", ".join(errors)
            )
        for object_id in CRITICAL_TELEMETRY:
            state = self.hass.states.get(self.entities[object_id])
            if not self._state_available(object_id, state):
                raise ESPHomeControllerOfflineError(f"Linked ESPHome controller is offline; unavailable {object_id}")
        for object_id in ("proflame2_last_payload_length", "proflame2_tx_success_count", "proflame2_tx_failure_count"):
            if self.integer(object_id) is None:
                raise ESPHomeControllerUnavailableError(f"Invalid ESPHome telemetry: {object_id}")

    @property
    def available(self) -> bool:
        try:
            self.require_ready()
            return True
        except ESPHomeControllerUnavailableError:
            return False

    @callback
    def invalidate(self) -> None:
        self._dirty = True
        self.generation += 1
        self.changed.set()

    @callback
    def _state_changed(self, event) -> None:
        self.changed.set()
        new_state = event.data.get("new_state")
        critical_object_id = next(
            (
                object_id
                for object_id in CRITICAL_TELEMETRY
                if self.entities.get(object_id) == event.data.get("entity_id")
            ),
            None,
        )
        if critical_object_id is not None and not self._state_available(critical_object_id, new_state):
            # Preserve an observed disconnect even if a restoration event was
            # processed before this callback reads the current state machine.
            self.generation += 1
        available = self.available
        if self._was_available is not None and available != self._was_available:
            self.generation += 1
        self._was_available = available

    def _subscribe_states(self) -> None:
        if self._state_unsub is not None:
            self._state_unsub()
        self._state_unsub = async_track_state_change_event(self.hass, list(self.entities.values()), self._state_changed)

    def start(self) -> None:
        if self._unsubscribers:
            return
        entry = self.entry
        if entry is None:
            raise ESPHomeControllerUnavailableError("Linked ESPHome entry not found")
        device_registry = dr.async_get(self.hass)
        registry_compat = device_registry_compatibility(device_registry)

        @callback
        def registry_changed(event):
            # Entity removals may not carry config_entry_id; invalidate bindings
            # for cached IDs, or entities currently registered to this entry.
            entity_id = event.data.get("entity_id")
            item = er.async_get(self.hass).async_get(entity_id) if entity_id else None
            if entity_id in self.entities.values() or (item and item.config_entry_id == self.entry_id):
                self.invalidate()
                try:
                    self.resolve()
                except ESPHomeControllerUnavailableError:
                    # Keep the binding dirty; operations will return the
                    # actionable error while listeners stay alive for repair.
                    pass

        @callback
        def device_changed(event):
            device_id = event.data.get("device_id")
            device = device_registry.async_get(device_id) if device_id else None
            if device_id == self.device_id or (device and self.entry_id in registry_compat.config_entry_ids(device)):
                self.invalidate()

        @callback
        def service_changed(event):
            if event.data.get("domain") == "esphome":
                entry = self.entry
                node = entry.data.get("device_name") if entry else None
                if node and event.data.get("service") in {
                    f"{node.replace('-', '_')}_{action}" for action in self._action_names
                }:
                    self.invalidate()

        async def entry_changed(_hass, _entry):
            self.invalidate()

        self._unsubscribers = [
            self.hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, registry_changed),
            self.hass.bus.async_listen(dr.EVENT_DEVICE_REGISTRY_UPDATED, device_changed),
            self.hass.bus.async_listen("service_registered", service_changed),
            self.hass.bus.async_listen("service_removed", service_changed),
            entry.add_update_listener(entry_changed),
        ]
        # Available on newer HA; older supported versions still emit entity
        # availability/service removal events and are checked on each operation.
        if hasattr(entry, "async_on_state_change"):
            self._unsubscribers.append(entry.async_on_state_change(self.invalidate))
        self._dirty = True
        self.resolve()
        self._was_available = self.available

    def close(self) -> None:
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        if self._state_unsub is not None:
            self._state_unsub()
            self._state_unsub = None
        self.invalidate()
