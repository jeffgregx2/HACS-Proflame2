# Issue #17: Public Home Assistant / ESPHome Transport Design

## Status

Issue #17 has an immediate compatibility fix for Home Assistant 2026.9. That
fix must be applied now. This document records the follow-up design work that
is intentionally deferred until after travel.

The follow-up replaces Proflame2's dependency on ESPHome integration runtime
objects with documented Home Assistant and ESPHome interfaces.

## Incident

Home Assistant 2026.9 changed the ESPHome integration's in-memory state map
from entity-keyed entries to `(device_id, entity_key)` entries. Proflame2 read
that map through the linked ESPHome config entry's `runtime_data` object. The
result was that LilyGO transmission completed physically, but Proflame2 could
not find TX telemetry to confirm it and treated the request as failed.

The immediate Issue #17 fix supports both state key formats. It is a required
compatibility repair, not the long-term architecture.

## Problem With The Current Boundary

`HomeAssistantESPHomeTransport` currently reaches into the ESPHome
integration's private runtime representation to:

- enumerate API actions from `runtime_data.services`;
- execute actions through `runtime_data.client.execute_service(...)`;
- inspect availability and firmware data;
- match entity metadata in `runtime_data.info` to values in
  `runtime_data.state`.

These objects are implementation details of another integration. Home
Assistant does not promise their type, key shape, lifetime, or naming. The
2026.9 key change demonstrates the resulting compatibility risk.

The custom Proflame2 event bus payload is different: it is an explicit firmware
to Home Assistant contract and is already consumed through HA's public event
bus. It should remain the RX delivery mechanism.

## Target Boundary

The target design uses only these public boundaries:

| Need | Current implementation | Target implementation |
| --- | --- | --- |
| Send TX, display, RX-policy, and learning commands | ESPHome `runtime_data.client.execute_service` | HA `esphome.<node>_<action>` action via `hass.services.async_call` |
| Confirm TX | Poll ESPHome `runtime_data.info/state` | Response from `proflame2_tx_stateful`; telemetry entities only as diagnostic fallback |
| Read endpoint diagnostics | ESPHome `runtime_data.info/state` | Public entity registry plus `hass.states` |
| Receive native-remote packets | HA event bus | HA event bus (unchanged) |
| Track telemetry changes | Poll ESPHome state map | `async_track_state_change_event` for resolved entity IDs |

Home Assistant's state machine, entity registry, event bus, service registry,
and state-change listener helpers are supported integration interfaces.
ESPHome documents `api.actions` as Home Assistant-callable actions, supports
responses through `api.respond`, and documents Home Assistant event emission.

## Firmware Contract Changes

The package currently publishes named telemetry entities and exposes the
following actions. Preserve their existing names and argument schema unless a
versioned firmware-contract change is explicitly made:

- `proflame2_tx_stateful`
- `proflame2_display_state_update`
- `proflame2_rx_set_active_listening`
- `proflame2_rx_stop`
- `proflame2_learn_mode_update`

### TX Response

Extend `proflame2_tx_stateful` to use the ESPHome `api.respond` contract after
the radio transmit operation completes. Its response must include at least:

- `request_id`
- `ok`
- `payload_length`
- `frames_sent`
- `elapsed_ms`
- `error_code`, when applicable
- `error_message`, when applicable
- `radio_status`

The response must correspond to this action invocation, not a cached result
from an earlier TX. The HA transport must use the response as the authoritative
TX completion result. Existing text sensors and counters remain diagnostics;
they must not be required to make a control request succeed.

Before relying on this path, verify response handling against the supported
ESPHome and HA versions with the real LilyGO firmware. The YAML action must not
respond until the C++ transmit action has completed and recorded its result.

### RX Event

Keep `esphome.proflame2_rx_packet` as the RX event type and preserve its schema
versioning. The firmware must continue to include the linked HA `device_id` so
Proflame2 can reject events from a different LilyGO. This is already a public
HA event-bus interaction and does not require ESPHome runtime access.

### Telemetry Entities

Retain the Proflame2 endpoint telemetry entities for diagnostics and endpoint
health. Their declared package names are a firmware contract:

- `Proflame2 Endpoint Status`
- `Proflame2 Last Error`
- `Proflame2 Last TX Result`
- `Proflame2 Last Request ID`
- TX counters and payload/repeat/elapsed diagnostics
- firmware protocol and configuration revision

Do not use mutable display names as the primary lookup at runtime. During
endpoint binding, resolve the entities registered for the linked ESPHome config
entry, validate the expected Proflame2 telemetry set, and store the resolved
HA entity IDs in Proflame2's own config-entry data. Refresh that binding when
the entity registry changes. A missing required diagnostic entity should make
the endpoint unhealthy with an actionable message, not silently fall back to
ESPHome private state.

## HA Transport Design

### Endpoint Binding

The user continues to select an existing ESPHome config entry during setup.
Proflame2 uses that public config-entry identifier only to:

1. find its HA device and telemetry entities through the device/entity
   registries;
2. validate that the expected ESPHome actions exist in the HA `esphome` domain;
3. associate RX bus events with the same HA device.

At bind time, resolve the exact HA action names and save them in Proflame2
configuration. ESPHome action names include the node prefix, so do not infer
them on every TX from undocumented ESPHome runtime metadata. Validate the
stored action names at setup and report a relink/reconfigure requirement if the
user renamed the ESPHome node or removed the Proflame2 package.

Action calls use `hass.services.async_call("esphome", action, data, ...)` and
request a response for `proflame2_tx_stateful`. Calls must be blocking only for
the action execution and response; no polling loop against ESPHome internals is
permitted.

### Telemetry

Use the entity registry to resolve endpoint entity IDs, `hass.states.get()` for
snapshots, and `async_track_state_change_event()` for changes. Treat telemetry
as status and diagnostics, never as the primary acknowledgement of a TX once
the action response exists.

Availability comes from the bound endpoint status entity/device state. Do not
read an ESPHome integration `available` attribute from `runtime_data`.

### RX

Continue listening with `hass.bus.async_listen()` for
`esphome.proflame2_rx_packet`. Validate event schema, device ID, payload, and
capture metadata exactly as today. No redesign of Proflame protocol decoding is
part of this issue.

## Direct Native Client Alternative

Opening a separate `aioesphomeapi` connection would also avoid HA's ESPHome
integration internals. It is not the preferred primary design because it would
require Proflame2 to manage the host, port, encryption key, connection
lifecycle, and a second connection to every LilyGO. It would also duplicate the
existing HA ESPHome integration. Reconsider it only if HA's public ESPHome
actions cannot deliver the required TX response behavior.

## Migration Plan

1. Merge the focused Issue #17 tuple-key compatibility patch. Keep it narrowly
   scoped and test scalar and tuple state maps.
2. After travel, create a dedicated follow-up branch and add the public action
   and telemetry binding layer alongside the current transport.
3. Update the ESPHome package to return a structured
   `proflame2_tx_stateful` response.
4. Add the HA action-service transport and use it for one LilyGO development
   endpoint.
5. Move TX confirmation to the action response. Retain entity telemetry as a
   diagnostic fallback during the transition.
6. Move all telemetry reads to the entity registry/state machine and remove all
   `runtime_data`, native client, service-catalog, info-map, and state-map
   accesses.
7. Validate upgrade from the current production firmware/package reference.
   If the new firmware contract is required, version it and present a clear
   firmware upgrade requirement rather than silently degrading control.
8. Remove the Issue #17 tuple-key compatibility shim only after no production
   transport path reads ESPHome `runtime_data`.

## Required Tests

- Unit test service-name binding, including renamed/missing actions.
- Unit test public HA action calls and structured success/error responses.
- Unit test entity binding scoped to the selected ESPHome config entry.
- Unit test entity-registry refresh after an entity ID is renamed or recreated.
- Unit test TX success when diagnostic sensors update late or do not update.
- Unit test malformed/missing response behavior and user-visible recovery.
- Retain RX event filtering tests for wrong/missing `device_id`.
- Integration test against the current HA release and the next HA beta before
  release.
- Device test: TX, guided learning, active listening, display update, restart,
  and endpoint reconnection using a real LilyGO.

## Completion Criteria

The work is complete only when `HomeAssistantESPHomeTransport` no longer reads
or writes any ESPHome integration `runtime_data` member, TX confirmation uses a
response bound to the invoked action, and the full LilyGO workflow passes on a
current HA release without access to ESPHome private runtime objects.
