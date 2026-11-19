# Home Assistant integration maintenance backlog

These are tracked compatibility tasks for the HA integration. Firmware-only
cleanup is tracked separately in the [LilyGO firmware backlog](firmware_cleanup_backlog.md).
Adding an item here does not authorize implementation or a release.

## Device registry deprecations: removal in HA 2027.8.0

Status: V08-02 lookup/ownership and V08-03 parent relationships are implemented
locally for v0.7.2. Publication and final manual acceptance remain pending.
Validation results are recorded below and in the v0.7.2 design.
Address the remaining deprecations before HA 2027.8.0. These warnings
were identified in the logs supplied for
[Issue #33](https://github.com/jeffgregx2/HACS-Proflame2/issues/33).
They are separate from the flame-zero feature and ESPHome connection failures.

### Device lookup

The local implementation is in `device_registry_compat.py`, behind the common
`DeviceRegistryCompatibility` interface:

- HA 2025.8 through 2026.7 selects `LegacyDeviceRegistryCompatibility`, retaining
  `async_get_device()` and the old set-valued ownership field.
- HA 2026.8 onward selects `ModernDeviceRegistryCompatibility`, using
  `async_get_device_by_identifier(identifier, config_entry_id)` and single-entry
  ownership. A missing modern API does not silently fall back to legacy calls.
- Identity reconciliation and controller-device event handling use this boundary.
  The existing legacy ownership/collision guards remain in place. No device or
  entity ID changes are introduced beyond the existing legacy migration.

The minimum remains HA 2025.8.0. These differences follow HA's
[2026.8 registry ownership change](https://contributors.home-assistant.io/blog/2026/07/21/device-registry-single-config-entry/).
The original requirements below remain the acceptance criteria; no firmware or
parent-relationship change is part of V08-02.

- Replace the deprecated `device_registry.async_get_device` lookups in
  `custom_components/proflame2/__init__.py` (reported at lines 129 and 131).
- Select the supported identifier/connection lookup API appropriate to each
  operation. Do not assume a device identifier resolves uniquely across config
  entries or pick an arbitrary match when more than one device is returned.
- Preserve existing device/entity identities, ownership, and reconciliation
  behavior. Inspect compatibility with the integration's supported minimum HA
  version before choosing a replacement API or raising that minimum.
- Test existing-device reconciliation, missing and ambiguous matches, duplicate
  assignments, and reload behavior without altering unrelated devices.

V08-02 automated validation (2026-11-18): full suites on HA 2025.8.0 and
2026.9.0 each passed 2,383 tests, with 35 skips and one deselection. One skip
per stack is the opposite native registry model; the other 34 are existing
external/optional checks. Tests verify preserved device/entity IDs, shared
legacy ownership rejection, wrong-owner isolation and modern duplicate
identifiers across entries. Lint, formatting and source checks passed. No
firmware changes or live HA registry mutations were performed by the agent.

### Parent device relationships

V08-03 removes the deprecated identifier-based `via_device` hint from sensor,
switch, number and profile-button `DeviceInfo`. The existing runtime is the
single owner of the parent relationship, using public
`async_update_device(via_device_id=...)` on both supported HA versions. No
modern-only `DeviceInfo` key is introduced on HA 2025.8.

A regression test reproduced entity registration overriding the correct parent
when separate modern ESPHome entries have matching controller identifiers.
Removing the redundant hints preserves the entry-scoped controller ID. Tests
also cover MAC-only controllers, delayed discovery, full fireplace reload,
controller removal/replacement, stable entity/device identities and YardStick's
single parentless fireplace device. No firmware or RF behavior changes.


V08-03 automated validation: full HA 2026.9.0 suite passed 2,391 tests
(35 skipped, one deselected); HA 2025.8.0 passed 2,390 tests (36 skipped,
one deselected). The extra minimum-version skip is the modern-only duplicate-
identifier parent regression. Other skips are existing external/optional checks
and the opposite native registry model. Ruff, Black, whitespace, relative
Markdown links and source preflight passed. Firmware remains identical to
v0.7.1. These results do not replace manual inspection of the actual registry or
remaining public-transport hardware acceptance. All changes remain local and
uncommitted; no GitHub issue or release was changed.

The original acceptance requirements remain below:

- Replace the deprecated `via_device` parameter with `via_device_id` where
  required by HA's device registry/entity APIs. Warnings were reported for
  `sensor.py`, `switch.py`, `number.py`, and `button.py`.
- Review shared device-info construction and any other platforms using the same
  relationship, rather than fixing only the four warning sites.
- Preserve LilyGO's existing ESPHome parent relationship and YardStick's
  fireplace-only presentation. Handle unavailable or not-yet-created parent
  devices without introducing duplicates or changing unique IDs.
- Validate behavior on supported HA versions, device/entity setup, options
  reload, reconnect, and removal. Verify the warnings no longer appear.

### Documentation and completion

- Document the implemented HA compatibility range and any required upgrade
  action in release notes and installation requirements. Keep user-facing
  instructions concise; put API details in technical documentation.
- Update this backlog with the resolving commit/release and focused test
  results. Retain the original removal deadline for traceability.
- Run the appropriate device-registry regression tests, full HA integration
  suite, and HACS/Hassfest validation before declaring the work complete.
- Keep this maintenance separate from RF/protocol changes. These deprecations
  currently emit warnings; the logs do not identify them as the cause of the
  missing `proflame2_tx_stateful` action.

## Consistent service state merging — v0.8.0 (V8-15)

Approved for v0.8.0; implementation has not started. The service currently uses
separate merging/default rules depending on whether flame is supplied. Unify
partial requests and pilot retention without changing full saved-profile
application. See the [service state design](service_state_merge_design.md) for
the agreed transition rules, implementation tasks and acceptance cases. Create
the tracking issue after v0.7.2; this is outside the v0.7.2 code change.
