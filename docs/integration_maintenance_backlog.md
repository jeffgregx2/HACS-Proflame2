# Home Assistant integration maintenance backlog

These are tracked compatibility tasks for the HA integration. Firmware-only
cleanup is tracked separately in the [LilyGO firmware backlog](firmware_cleanup_backlog.md).
Adding an item here does not authorize implementation or a release.

## Device registry deprecations: removal in HA 2027.8.0

Status: backlog; not implemented. Address before HA 2027.8.0. These warnings
were identified in the logs supplied for
[Issue #33](https://github.com/jeffgregx2/HACS-Proflame2/issues/33).
They are separate from the flame-zero feature and ESPHome connection failures.

### Device lookup

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

### Parent device relationships

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
