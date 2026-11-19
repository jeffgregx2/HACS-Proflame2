# v0.7.2 pre-hardware review — 2026-11-18

## Candidate assessment — 2026-11-19

The U0–U5 owner hardware matrix is complete: configured HA 2025.8 baseline,
staged upgrades through 2026.1/2026.8/2026.9 to 2026.11.2, and an independent
restored-baseline 2025.8 → 2026.11.2 direct jump. The agreed per-stage checks
cover retained entity/controller identities and settings plus live TX/RX.
Earlier comments below marking post-fix owner TX/RX acceptance pending are
superseded for these tested paths. The U4 disk-space interruption was resolved.
No further broad manual testing is recommended without a new failure/change.

Retained automated evidence: HA 2025.8 full suite 2,456 passed/42 skipped/one
deselected; HA 2026.8 full suite 2,463 passed/35 skipped/one deselected, with
native-version/model and external-test skip limitations recorded in the design.
Those are recorded runs, not reruns performed for this assessment. Source
preflight currently passes 480 files; whitespace check passes. The transport
continues to use public HA services and entity state interfaces.

The final released-firmware-source compatibility check is complete by owner
report on 2026-11-19: current v0.7.2 candidate on HA 2026.11.2, LilyGO built
from the exact GitHub v0.7.1 tag with ESPHome Builder 2026.6.0, and the existing
configuration retained. The agreed check was one HA transmission and one
native-remote update. This closes the agreed source-based compatibility gate;
it does not claim byte-identical reproduction of every previously installed
firmware binary. No further hardware testing is required absent new changes
or failures.

Functional validation is complete for the agreed release scope. Final
metadata/docs stamping, source checks/CI on the final artifact, publication and
HACS re-review remain. Current integration version is still 0.7.2-debug; the
candidate is uncommitted/unpublished and this assessment authorizes no release.

## Post-upgrade correction

Owner testing on HA 2026.8.0 subsequently exposed an ESPHome active entity
unique-ID migration that this review's original tests did not exercise. The
original no-blocker conclusion below is historical and does not qualify the
updated candidate. The registry-only upgrade test did not load ESPHome, so it
could not establish compatibility with that integration's startup migration.

The binding assumed legacy object-ID suffixes; migrated entities use the native
API's version-3 device/type/unmangled-name identity. This caused all required
telemetry to appear missing. That aggregate error exceeded HA's 255-character
sensor-state limit, producing Unknown instead of the explanation. Both defects
were reproduced with failing behavior tests before correction. Updated binding
accepts native API v1/v2/v3 identities, retains entry/device/ambiguity checks and
continues to ignore editable HA names/IDs. All sensor string states are bounded;
full Last Issue remains in primary attributes and logs. HA 2026.8 is added to
CI; regression transport fixtures now exercise legacy and modern identities.
Final automated results and owner retesting are recorded in the design/protocol.
No live HA restart or RF transmission was performed by the agent.

## Conclusion

No blocking implementation defect found in the pending v0.7.2 changes.
The candidate is ready for the owner's focused hardware acceptance. This is
not release approval or a guarantee of HACS acceptance. Changes remain local
on `issue_22-v0.7.2`; no commit, push, release or review request was performed.

## Scope and review method

Reviewed every changed production module and the new endpoint/registry adapter,
new and modified tests, the one-off persisted registry upgrade check, minimum
version metadata, documentation and compatibility workflow. Compared the
candidate with v0.7.1, current issue #22 and the actual
[HACS review](https://github.com/hacs/default/pull/14066#pullrequestreview-5931208715).
Packet/protocol generation and functional firmware behavior are unchanged from v0.7.1.
Later build-only changes add conditional formatting-helper includes and an explicit
ESPHome Builder 2026.6.0 minimum; existing installations need no firmware rebuild.
No test count or source-presence assertion substitutes for behavior review.

## Issue #22 implementation mapping

| Concern | Implementation and reviewed behavior |
| --- | --- |
| Private ESPHome runtime/client/action catalog | Public HA service dispatch through one invocation helper. All six action paths use it; no production native API client or private fallback remains. |
| Private telemetry/info/state maps | Selected-entry entity registry binding and HA states. Entity IDs may be renamed; ambiguous, missing, disabled or foreign-device critical telemetry fails safely. |
| Device registry lookup/ownership | Legacy public APIs before HA 2026.8; entry-scoped lookup and single-entry ownership afterward. Minimum stays 2025.8.0; guards preserve existing identities and reject unrelated/shared legacy ownership. |
| Deprecated parent hints | Four entity hints removed. Existing runtime uses controller registry ID for the parent; modern same-identifier controllers cannot override the correct relationship. |
| Completion correctness | Fresh success/failure counters and matching request ID; success also requires matching payload length. Unknown initial request history permits readiness but cannot confirm a send. Timeouts do not cause automatic replay. |
| Recovery and diagnostics | Public lifecycle/state/registry/service notifications invalidate bindings; existing reconnect, display and listening loops recover. Version reporting uses public telemetry/device metadata. Failed platform setup cleans up runtime observers/tasks. |
| Firmware compatibility | Existing action names, payload argument schemas, RX event schema and telemetry contract preserved. Optional end-confirmation action remains optional. No structured firmware-response prerequisite introduced. |

Production-wide AST/source inspection found no remaining private ESPHome access
or private Home Assistant imports. Legacy deprecated registry calls are confined
to the explicitly version-selected legacy compatibility class. Internal helpers
belonging to this integration are not cross-integration private API access.

## Tests validate behavior

The test suite includes structural guards and API-selection unit checks, but
those supplement real HA service/state/registry tests rather than replacing them:

- Command payloads pass through the installed HA ESPHome handler functions,
  extracted without modification, with released firmware YAML argument schemas
  and real aioesphomeapi service models. Only network/firmware I/O is synthetic.
  This checks the handler contract, not full ESPHome integration startup.
- A forbidden synthetic runtime raises on any attempted private read. Public
  dispatch and telemetry completion still work with it installed.
- Completion tests cover reordered telemetry, queued versus terminal state,
  cached success, a different request, error results, counter reset, completion
  during dispatch, cancellation, timeout, late results and no automatic replay.
- Failure/lifecycle tests check offline recognition and quiet recovery, rapid
  disconnect/restore, services appearing late, entry reload, rename/rebinding,
  wrong-controller isolation and cleanup after close/failed setup.
- Registry tests use real platform setup/reload and verify device/entity IDs,
  delayed parent discovery, controller replacement, unrelated/shared ownership
  and YardStick's single parentless fireplace device.
- The one-off test loads actual saved HA 2025.8.0 registry files under HA
  2026.10.4, checks six devices and 16 entities, runs integration reconciliation
  and device registration, then saves/reloads in another modern process.
  Ownership, IDs, parent links and entry configuration are preserved.

### Negative controls

The parent-association regression failed before removal of the deprecated
hints: separate modern ESPHome entries with matching identifiers selected the
wrong parent. The corrected code passes.

For this review, a temporary in-memory mutation removed the fresh success-
counter requirement. The existing cached-success regression failed with
`DID NOT RAISE`, correctly detecting the false completion. Production files
were not edited to run this negative control. This is a focused sensitivity
check, not comprehensive mutation testing.

### Validation evidence

| Stack/check | Result |
| --- | --- |
| HA 2025.8.0 full suite, latest production tree | 2,390 passed, 36 skipped, one deselected (retained preceding run; production unchanged since) |
| HA 2026.9.0 full suite, latest production tree | 2,391 passed, 35 skipped, one deselected (retained preceding run; production unchanged since) |
| HA 2026.10.4 full suite during review | 2,385 passed, 41 skipped, one deselected |
| HA 2026.10.4 six installed-handler checks | All six passed after installing its declared aioesphomeapi 46.6.0 dependency; these were the six additional skips in the initial full run |
| Persisted HA 2025.8.0 → 2026.10.4 upgrade | Seed, upgrade/reconciliation and second modern load all passed |
| Deliberately broken cached-success handling | Existing behavior regression failed as expected |
| Ruff, Black, whitespace, source/private-data audit | Passed |

The remaining skips concern real ESPHome CLI/external decoder/helper-wheel
checks and the inapplicable native registry model. No firmware change requires
rerunning its compile/configuration matrix for this HA-only patch. The new
one-off script does not execute during the default pytest suite. Existing
HA 2026.11.1 automated-harness limitations remain documented; do not describe
2026.10 coverage as a full 2026.11 runtime pass.

## HACS readiness and remaining gates

The actual reviewer requested public `esphome` service invocation and entity
state telemetry. Both requirements are addressed. No additional code-level
HACS blocker was identified. Repository structure, brand assets, manifest,
license, public visibility, description/topics and issue support were checked.
The existing HACS submission's admission checks are green, including HACS,
Hassfest, core-domain conflict, ownership and release checks. These runs concern
the published repository and do not validate this uncommitted candidate.

The remaining steps are explicit release gates:

1. Complete focused hardware acceptance with existing firmware: physical
   commands, native RX/TX handoff, guided learning/display and reload/recovery.
   See the [hardware protocol](issue_22_v0_7_2_hardware_validation.md).
2. Stamp final v0.7.2 integration/release metadata and documentation references;
   the current `0.7.2-debug` version is intentional for testing. Follow the
   existing release-source validator, including firmware version identification
   stamping if required; no functional firmware update is necessary for users.
3. With owner authorization, commit/push and pass HACS, Hassfest and compatibility
   CI on the actual final candidate, then publish a full v0.7.2 release.
4. Return the draft HACS submission to review as requested by the maintainer.
   Reviewer acceptance remains external and cannot be guaranteed by a local audit.

Requirements were checked against HACS's current
[integration requirements](https://www.hacs.xyz/docs/publish/integration/) and
[default-inclusion process](https://www.hacs.xyz/docs/publish/include/).
No public issue or PR was changed during this review.

Validation after correction: full HA 2026.8.0/API 45.6.1 suite passed
2,463 tests (35 skipped, one deselected); full HA 2025.8.0/API 37.2.2 passed
2,456 tests (42 skipped, one deselected). The minimum's six extra skips are
native v3-generator cases on its older API, plus its different applicable
registry model; synthetic legacy/modern behavior fixtures run on both stacks.
The combined focused transport/entity run passed 218 tests. Ruff, Black,
whitespace and source preflight passed. Initial attempts included an already-
migrated no-op test setup collision and low scratch-disk space while two test
environments coexisted; those were corrected without product changes before
the final full passes. No live HA restart or RF action was performed.
Owner post-fix HA command/native-RX acceptance remains pending. No commit/push.
