# Proflame2 T-Embed Decomposition Status

The LilyGO T-Embed ESPHome component has been decomposed into small
responsibility-focused helpers while preserving the working FIFO RX, active
listening, TX, display, telemetry, and HA/YAML contracts.

`Proflame2TEmbedComponent` remains the ESPHome integration shell. It owns public
service/entity entrypoints, CC1101 runtime coordination, TX-over-RX priority,
display/telemetry mutation, and HA event publication. Domain mechanics are kept
out of the shell where they can be isolated without changing public contracts.

## Goals

- Keep TX behavior and timing stable.
- Keep FIFO semantic RX and active listening stable.
- Preserve ESPHome service/entity names unless a separate migration is planned.
- Make public APIs readable without requiring maintainers to inspect the full
  component implementation.
- Keep `Proflame2TEmbedComponent` as the ESPHome integration shell.

## Current Boundaries

### `display_controller.*`

Owns display view state, wake/dim policy, screen text selection, and button
navigation. It should not know RF packet structure beyond display-ready labels.

### `tx_controller.*`

Owns TX request validation, hex payload decoding, repeat-count policy, and
payload bit-length policy. The component shell still owns radio runtime queueing,
CC1101 TX execution, timing profile selection, TX result telemetry, and RX
pause/restore coordination.

### `fifo_rx_controller.*`

Owns bounded rolling FIFO buffers, selected trailing-window bytes, and low-level
FIFO timing/overflow metadata. The component shell still owns CC1101 register
configuration and FIFO polling.

### `active_listener.*`

Owns learned-profile filtering, Proflame2 FIFO decode decision policy, profile
mismatch classification, and duplicate suppression. The component shell still
owns HA event publication and telemetry counter mutation.

### `telemetry_publisher.*`

Owns publish-if-changed helpers for sensor/text-sensor values. The component
shell still owns source state and decides publication cadence.

### `battery_monitor.*`

Owns I2C battery/PMIC reads, battery fault handling, and battery sensor values.
It should be isolated from RF timing-sensitive paths.

## Completed Split Order

1. Extract passive value structs and public API documentation in headers.
2. Extract `battery_monitor.*`; it has the lowest RF behavior risk.
3. Extract display text/policy helpers into `display_controller.*`.
4. Extract telemetry publication caching into `telemetry_publisher.*`.
5. Extract FIFO RX buffer/configuration mechanics into `fifo_rx_controller.*`.
6. Extract active-listener decode/filter/publish policy into `active_listener.*`.
7. Extract TX request validation into `tx_controller.*` after RX state ownership
   is clear.
8. Update component shell comments/tests/docs to describe the final
   cross-domain coordination boundary.

## Required Invariants

- TX pauses RX and restores the previous RX state after TX completes.
- Active listening only publishes packets matching the learned profile.
- Diagnostic/debug buffers must not become semantic packet evidence.
- FIFO semantic artifacts remain packet-owned only after scanner/decoder
  acceptance.
- No dynamic allocation is introduced into timing-sensitive ISR paths.
- Logging stays out of ISR/tight polling paths.

## Validation

Each extraction ran or should run when repeated:

- `./.venv/bin/python -m pytest tests/test_esphome_firmware_scaffold.py -q`
- `make format-cpp-check`
- `git diff --check`
- ESPHome compile validation before deployment when firmware structure changes.

After RX/TX-affecting extractions, validate with:

- LilyGO TX command observed by `rtl_433`.
- Native remote or YardStick TX observed by LilyGO active listening.

## Remaining Notes

The shell intentionally still coordinates domains that cross ESPHome, CC1101,
display, telemetry, and HA publication boundaries. Further splitting should only
be done when a new behavior change justifies it; do not move RF timing execution
or HA/YAML contracts opportunistically.

The removed LilyGO GDO edge-interval ownership path remains out of scope. Active
RX is FIFO semantic capture only.
