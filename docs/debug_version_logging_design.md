# Version information in HA debug captures

Status: implemented locally on `dev`; automated validation passed. This is independent of Issue #33's
flame-zero feature and the device-registry maintenance backlog.

## Behavior

When HA debug logging becomes enabled for Proflame2, log the active integration
version and the configured controller type for each loaded fireplace entry.
For a LilyGO entry only, include its Proflame2 firmware release version. For
YardStick, omit the firmware field entirely.

Use normal DEBUG records; do not force logger levels or emit version information
as warnings. HA's normal effective level depends on installation settings.
The packet-debug configuration option and PCM switch remain independent.

Subscribe to HA's `EVENT_LOGGING_CHANGED`, then check the effective DEBUG level
of the version logger. The event is global: unrelated logger changes must not
repeat the record. Also check on entry setup, covering debug enabled before
startup or reload. Disabling and re-enabling debug starts a new capture session
and emits the header again.

Example:

```text
Proflame2 version information: integration=0.7.0-beta5 controller=lilygo_cc1101 proflame2_firmware=v0.7.0-beta4
Proflame2 version information: integration=0.7.0-beta5 controller=yardstick
```

## Version sources and availability

- Integration: the existing `version.integration_version()` build metadata.
- LilyGO: the linked ESPHome endpoint's existing
  `proflame2_firmware_version` text-sensor telemetry.
- Never substitute `device_info.esphome_version` or the firmware protocol
  integer for the Proflame2 release version. Do not infer a version from action
  availability, HA package version, or configured YAML source ref.
- Read current linked runtime telemetry without opening a connection, calling
  device actions, changing RX policy, or transmitting. This must work even if
  the transmit action is absent. Reuse the existing ESPHome telemetry parser,
  including old integer keys and current tuple keys.
- If the linked endpoint is unavailable or has no usable release telemetry,
  emit `proflame2_firmware=unavailable`. Do not treat a disconnected endpoint's
  cached value as confirmed current firmware.
- While debug remains enabled, observe state updates from that linked ESPHome
  entry. Emit another version record only if firmware availability/version
  changes. This covers late startup, reconnect, and a changed installed build.
  No polling, active probing, or firmware changes are needed.

## Organization and lifecycle

`version_logging.py` owns event observation, session deduplication, and message
formatting. Register one observer per config entry after runtime creation and
before startup display/receive work. Register cleanup with the entry's standard
unload callbacks; do not leave listeners behind on reload or removal.

The production ESPHome transport exposes a read-only Proflame2 firmware getter
using its existing snapshot machinery. Fetch the current linked runtime rather
than requiring an earlier successful Proflame2 transport connection or using a
stale cached runtime after reconnect.

Limit state-event handling to the linked entry's registered entities, using
registry ownership rather than guessed entity IDs or user-visible names. An
update from another controller must not be reported as this controller's firmware.
Use the config-entry ID to distinguish headers when several fireplaces exist;
do not add addresses, remote IDs, user names, or raw RF data to these records.

## Verification

- WARNING/INFO effective logging emits no version records; DEBUG emits one.
- HA logger-setting events produce the header, including its `logger.set_level`
  service path. Unrelated events and repeated DEBUG settings do not duplicate it.
- Entry setup with DEBUG already active emits the header; toggling off/on emits
  it again. Unload removes observers; reload does not accumulate them.
- YardStick emits integration/controller only and never reads LilyGO telemetry.
- LilyGO emits its reported Proflame2 version, independently of a different
  ESPHome compiler version. Test known, missing, blank, unknown, and unavailable
  values; integer/tuple telemetry keys; disconnected endpoint and fresh runtime
  replacement; and missing transmit action metadata.
- Late linked telemetry changes unavailable to known exactly once. Version
  changes are reported; unchanged telemetry and other entries' updates are not.
- Logging must not send, connect, configure, restart listening, or change user
  logging settings. Existing RF, config-flow, and logging regression tests pass.
- Keep public usage guidance brief: enabling HA debug logging now includes
  integration and available LilyGO firmware versions automatically.

## Completion record

Implemented on 2026-11-04. The 15 focused version-logging tests passed, including
HA's logger service/event path, late firmware availability, live runtime
replacement, both telemetry key layouts, YardStick omission, and entry cleanup.
The full repository suite passed 1,983 tests, with 34 skipped and one deselected.
Repository-wide Ruff and Black checks passed, as did documentation link and
whitespace checks. No controller connection or RF operation was performed for
validation. Firmware source and protocol behavior are unchanged.

Actual browser download acceptance remains a separate user check; automated
logger/event tests are not a physical LilyGO test. Implementation is uncommitted
at the time of this record; no deployment or publication was performed.
