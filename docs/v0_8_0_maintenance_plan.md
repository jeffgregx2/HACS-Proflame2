# v0.8.0: technical debt and post-v0.7.0 maintenance

Status: planned maintenance release, with the explicitly approved controller
shutdown exception below. This document inventories deferred work; it does not authorize all
items to be implemented at once.

## Release purpose and boundaries

Reduce technical debt, address compatibility warnings, and fix issues found
after v0.7.0. Preserve existing fireplace behavior, learned identities, entity
IDs, supported bands/protocols, and installed firmware compatibility.

Do not hold a necessary stability/security fix until v0.8.0 merely to match this
schedule. Release a v0.7.x fix when its severity requires that. Conversely, do
not add an unrelated feature because its implementation is convenient during
cleanup. Reassess priorities after v0.7.0 acceptance and incoming reports.

## Integration architecture and compatibility

| Item | Required work and completion conditions |
| --- | --- |
| [#22: public HA/ESPHome transport](https://github.com/jeffgregx2/HACS-Proflame2/issues/22) | Replace private ESPHome runtime access with supported HA action, entity/state, and event APIs. Follow the [existing migration design](issue_17_ha_esphome_public_transport_design.md), including explicit capability selection, authoritative TX confirmation, old-firmware compatibility, and real LilyGO acceptance. Keep RX events on the public bus. Include the recently added firmware-version lookup in the migration audit. |
| [#17: historical telemetry-key compatibility](https://github.com/jeffgregx2/HACS-Proflame2/issues/17) | The immediate integer/tuple-key fix already exists. Preserve it while any supported path needs it. Remove obsolete shims only after #22 eliminates that dependency and regression tests prove compatibility. Update issue status to distinguish the released fix from remaining architecture work. |
| Device lookup deprecations | Replace deprecated `device_registry.async_get_device` usage with the appropriate supported identifier/connection APIs. Handle ambiguous matches; preserve device reconciliation and ownership. |
| Parent-device deprecations | Replace deprecated `via_device` relationships with the supported device-ID API across all affected entities. Preserve LilyGO's ESPHome parent link and YardStick's fireplace-only presentation. |

The last two items are detailed in the
[integration maintenance backlog](integration_maintenance_backlog.md).
The reported removal deadline is **HA 2027.8.0**. Validate any replacement APIs
against the minimum supported HA version before implementation; changing that
minimum requires an explicit compatibility decision and upgrade documentation.

## Existing diagnostics and user interface cleanup

[#25](https://github.com/jeffgregx2/HACS-Proflame2/issues/25) tracks diagnostic
entity classification and the long Last Payload Hex presentation. Some
diagnostic classification is already implemented; audit the shipped state
before planning additional changes rather than repeating completed work.

- Complete any outstanding diagnostic/configuration classification while
  preserving names, unique IDs, values, visibility defaults, and TX confirmation.
- Improve the presentation of long payloads using a compact HA-side state and
  complete payload attribute, as described in the issue. Preserve the full
  evidence in diagnostics; do not truncate firmware data or mutate ESPHome's
  entities through private APIs.
- Update the issue's older v0.7.1 target when scheduling this remaining work.
- Retain normal user controls and internal-only entities in their intended
  categories. Check existing automations and mixed firmware/integration upgrades.

## Firmware maintainability and validation

### Approved deferral: manual LilyGO shutdown

The owner moved the [controller shutdown feature](controller_shutdown_design.md)
from v0.7.0 to v0.8.0 on 2026-11-06. Preserve the four-second prompt/ten-second
hold, battery-only PMIC cutoff, OTA-confirmation correction and HA notification
with its half-second interval as one feature. The local branch and outstanding
build/hardware checks are recorded in that design. This is the specific approved
exception to the original no-new-features boundary, not authorization for other
new capabilities. The HA-only #34 recovery fix and already released #33 fix
remain with v0.7.0.

### Existing cleanup work

The [firmware cleanup backlog](firmware_cleanup_backlog.md) contains the
technical detail. Its items are included in v0.8.0 planning:

- Establish the C++ formatting baseline in a separate mechanical change; enable
  the maintained formatter check in CI after the baseline is clean.
- Decompose the oversized component into coherent setup, receive lifecycle,
  capture/diagnostics, display/API, and telemetry responsibilities where useful.
  Preserve the timing-sensitive RF implementation and API contracts. Prove
  behavior with existing host tests before moving code.
- Review log levels, repeated idle/capture messages, and duplicated diagnostic
  formatting. Preserve support evidence, firmware/band identity, and full PCM
  payload logging when explicitly enabled.
- Review ESPHome's flash-chip driver suggestion using the value reported by
  the particular board. Preserve older supported ESPHome schema compatibility;
  this warning does not itself establish an RF or memory fault.
- Maintain the host C++ harness, both 315/433.92 MHz configuration/compile
  fixtures, and dependency-version coverage. Address new ESPHome deprecations
  before their removal deadlines.

Separate formatting/refactoring commits from functional fixes so reviewers can
assess whether radio behavior changed. Do not rework protocol decoding, CC1101
settings, command repeat policy, or scheduling solely for aesthetic consistency.

## Stability reports and post-v0.7.0 intake

[#28](https://github.com/jeffgregx2/HACS-Proflame2/issues/28) tracks an intermittent
LilyGO cache-error panic originally observed in a diagnostic build. Review
current-version evidence before assigning a fix. A reproducible crash in the
supported v0.7.0 configuration is a v0.7.x concern, not automatically deferred
cleanup. Retain diagnostic artifacts matched to their exact build and separate
panics from ordinary Wi-Fi/API reconnects and RF command loss.

For new reports, record:

1. Integration, Proflame2 firmware, HA, and ESPHome versions where applicable.
2. Controller, protocol variant, band, relevant options, and reproducible steps.
3. Observed versus expected behavior, with correctly collected support logs.
4. Whether the issue is a regression, compatibility defect, existing limitation,
   or new feature request; severity and the intended patch/maintenance release.
5. A failing regression case where possible, followed by focused checks and
   applicable full-suite/firmware/hardware acceptance.

Keep private logs, addresses, remote identities, Learning IDs, and installation
details out of published fixtures and plans. Follow repository privacy review
before committing compact capture-backed vectors or publishing support details.

## Items that remain with v0.7.0

| Item | Boundary |
| --- | --- |
| [#33: flame OFF while power ON](https://github.com/jeffgregx2/HACS-Proflame2/issues/33) | Keep the fix and its existing user-facing behavior in v0.7.0; it first shipped in beta6. The deferred controller shutdown work must not change it. |
| [#34: offline logging and display recovery](issue_34_offline_display_sync_design.md) | Keep the bounded HA-only recovery fix in v0.7.0. It requires no new LilyGO firmware; optional firmware shutdown/test support is deferred above. |
| [#20: ESPHome image/fallback AP warnings](https://github.com/jeffgregx2/HACS-Proflame2/issues/20) | Previously required before v0.7.0 GA. Do not silently defer to this release. The image removal deadline is ESPHome 2027.1.0. |
| [Startup/API recovery](startup_api_recovery_design.md) | Before GA, ensure ordinary delayed ESPHome readiness, missing action metadata during startup, and temporary disconnections cannot permanently terminate active listening. A broad #22 transport rewrite is not required to fix a bounded lifecycle defect. |
| Version information in debug captures | [Implemented locally and tested](debug_version_logging_design.md); include in v0.7.0 rather than treating it as future firmware cleanup. |
| Node.js 24 artifact-upload action | Already committed and pushed on `dev`, positioned for the next release. No additional v0.8.0 implementation is currently needed. |
| [#31 refresh](https://github.com/jeffgregx2/HACS-Proflame2/issues/31), [#32 learning recovery](https://github.com/jeffgregx2/HACS-Proflame2/issues/32), and addressed portions of [#26](https://github.com/jeffgregx2/HACS-Proflame2/issues/26) | Review acceptance and close/update tracking for implemented changes. Carry forward actual remaining defects, not already delivered features. |

## Features excluded from this maintenance plan

- [#33 flame OFF while power stays ON](https://github.com/jeffgregx2/HACS-Proflame2/issues/33)
  and its [configuration-page redesign](issue_33_flame_off_design.md) were
  separately authorized and implemented locally for the v0.7.0 work, with
  physical acceptance still pending. They are not new v0.8.0 feature scope;
  regressions found after release qualify for normal bug-fix triage.
- [#23 Proflame GTMF compatibility](https://github.com/jeffgregx2/HACS-Proflame2/issues/23)
  is a compatibility expansion requiring separate evidence and scope approval.
- Thermostat control, controller sharing, additional radio/controller support,
  and other new operating capabilities are not included.

## Release completion

Before starting implementation, convert selected items into focused tasks after
inspecting their then-current scope. This inventory is not a duration estimate
or a promise that all uncertain refactors fit a fixed release date.

For selected items, record resolving commits, regression evidence, compatibility
decisions, and hardware acceptance separately. Run maintained test/validation
targets appropriate to the changes, and preserve existing release automation.
Release notes should describe user-visible fixes and any upgrade requirements;
implementation and testing detail belongs in technical documentation.
