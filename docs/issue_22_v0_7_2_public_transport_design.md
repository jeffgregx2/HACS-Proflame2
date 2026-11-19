# Issue #22: v0.7.2 public ESPHome transport design and implementation plan

## Status and release gate

Design prepared 2026-11-17 following the executable feasibility proof and
[HACS review](https://github.com/hacs/default/pull/14066#pullrequestreview-5931208715).
The local implementation and source/test audit are complete on the approved
task branch. The proof cases now exercise the production adapter; no separate
proof adapter is installed. Hardware acceptance has not been performed.

**Compatibility decision (2026-11-17):** the owner approved correcting the
advertised minimum to **HA 2025.8.0**, because the already-released options flow
requires `OptionsFlowWithReload`, introduced in that release. No legacy reload
fallback/backport is in scope. Execute minimum-version tests on 2025.8.0;
2025.1.0 remains historical evidence of the previous metadata mismatch.
The production draft has now passed the full default suite on HA 2025.8.0 and
2026.9.0: 2,285 passed, 34 optional/external skipped and one deselected on each.
The two matched runtime stacks are also specified in the new compatibility CI
workflow. That workflow has not yet run on GitHub. Hardware acceptance remains
pending; no firmware update is required by the implementation.
The separate HA 2026.11.1 probe could not start because the available test
harness targets 2026.10.4 and imports a removed HA logger helper. It does not
qualify 2026.11.1; this limitation is separate from the two matched passing runs.

**v0.7.2 must work with unchanged, released v0.7.1 LilyGO firmware.** It must not
require a firmware rebuild, new action, new telemetry field, response capability,
changed protocol/configuration revision, or re-learning an existing fireplace.
If this cannot be achieved using public HA interfaces, stop v0.7.2 implementation
and defer this migration to v0.8.0 for a separately reviewed firmware design.
Do not quietly relax this gate or retain a private-runtime fallback.

This document governs the narrow patch. The earlier
[public transport design](issue_17_ha_esphome_public_transport_design.md) remains
the longer-term reference for response-capable firmware. Structured responses
and the associated capability negotiation are outside v0.7.2.

## Problem and scope

The transport discovers actions, sends commands, reads telemetry and checks
connection liveness through another integration's `runtime_data`, API client,
service catalog, metadata and state maps. ESPHome changed those state-map keys
in HA 2026.9, already causing a compatibility failure in #17. The HACS reviewer
requires replacing these accesses before acceptance.

Preserve RF encoding, timing, repeat counts, learned profiles, Learning IDs,
radio-band configuration, control/coalescing policy, refresh, delayed
retransmission, confidence semantics and the public Proflame2 service API.
YardStick is outside the transport change; retain its existing tests and perform
one hardware smoke test if available. The owner extended this scope on
2026-11-18 to include V08-02 lookup/ownership compatibility using legacy and
modern classes, then requested V08-03 parent-relationship cleanup. Shutdown
firmware and new structured firmware responses remain outside this patch.

## Review concern to implementation mapping

| HACS concern | Required replacement | Acceptance evidence |
| --- | --- | --- |
| `connect()` reads `runtime_data.services` to find device actions | Discover registered `esphome` actions through HA's service registry; bind to the selected ESPHome node | Tests for node identity, late registration, optional capabilities, rename, multiple controllers and removal |
| TX/display/RX/learning commands call `runtime_data.client.execute_service()` | One HA action-invocation helper using `hass.services.async_call(..., blocking=True)` | All six existing call paths exercised with released YAML argument schemas; no directly owned/reused ESPHome client |
| `_collect_snapshot()` reads `runtime_data.info/state` | Selected-entry entity registry bindings plus `hass.states.get()` | Every existing snapshot field covered, renamed IDs, disabled/missing entities and unknown/unavailable states |
| `_current_runtime()`, availability and RX liveness inspect private runtime identity/state | Public config-entry lifecycle, required action registration and endpoint entity availability, with a local binding generation | Startup/offline/quiet recovery, ESPHome reload, action replacement, flapping, unload and waiting RX tests |
| Firmware/status/diagnostic paths expose private information | Project firmware from its public telemetry entity; ESPHome software version from public device registry; existing host from public config-entry data | Version logging, status and diagnostics retain their meanings; missing optional information is explicitly unavailable |
| Future ESPHome refactors can break control | No private fallback, private ESPHome type import or introspection of service-handler internals | Production source audit plus tests that make access to linked private runtime fail |

The public firmware-to-HA RX event remains the packet input. It must not be
replaced by telemetry polling or a second direct native API connection.

## Compatibility contract

Use the existing transport interface and request/response dataclasses. Retain
all existing action names, argument names and types:

| Action | Role in released firmware | Requirement |
| --- | --- | --- |
| `proflame2_tx_stateful` | Prepared command and intended display state | Required for control |
| `proflame2_display_state_update` | Display-only state synchronization | Required for currently supported display behavior |
| `proflame2_rx_set_active_listening` | Receiver policy/profile | Required for current learning/listening behavior |
| `proflame2_rx_stop` | Stop capture | Required for current learning/listening behavior |
| `proflame2_learn_mode_update` | Learning instructions on controller | Required for current learning display behavior |
| `proflame2_rx_end_confirmation` | Existing optional transport hook | Optional; absent from released base YAML; preserve its existing no-op behavior when missing |

Missing required capabilities must produce a specific readiness/setup error and
be retried through existing recovery when registration is late. Optional
capabilities must not become new firmware prerequisites. Keep capabilities
separate so the error names the affected function instead of incorrectly saying
that a missing optional action makes all hardware unavailable.

The proof executed on HA 2026.9.0. Source inspection found compatible service
naming, stored node identity and public entity publication in HA 2025.1.0,
2026.9.0 and 2026.11.1. This is feasibility evidence, not an executed runtime
matrix. The owner-approved declared HA minimum is 2025.8.0. Implementation must execute
the supported-version contract tests using compatible Python/dependency stacks;
if the minimum cannot be supported, stop for a release decision.

## Organization and ownership

Keep the existing `HomeAssistantESPHomeTransport` as the integration adapter.
Extract a small HA-specific binding helper only if needed to keep it readable.
That helper owns selected-entry action/entity lookup and invalidation; it must
not become a second transport or duplicate controller lifecycle/serialization.

1. **Binding:** resolve public action names, entity IDs and controller device ID.
2. **Invocation:** call the bound HA service with the existing payload.
3. **Snapshot/completion:** read public entity states and await fresh evidence.
4. **Lifecycle:** invalidate local binding generations and notify the existing
   backend/runtime machinery when public readiness changes.

Retain one owner for all new unsubscribe callbacks and outstanding waiters.
Do not add a parallel retry supervisor, a second status publisher, a global
poller or new hidden configuration storage. Cache bindings in memory, re-resolve
when invalidated, and leave existing config-entry data/profile formats intact.

## Public endpoint binding

- Resolve the explicitly selected ESPHome config entry through HA's config-entry
  API; verify its domain and public lifecycle state. Do not select a controller
  by display title or by a globally matching action suffix.
- Use its persisted physical `device_name` to form the registered action prefix
  (hyphens become underscores), then require the exact registered services.
  Read this through public config-entry data; do not import ESPHome runtime
  classes or inspect handler closures. This naming/data convention is a
  version-tested compatibility assumption, not a guarantee about every future
  ESPHome release. Missing discovery data means not ready; never guess another
  node or invent a new required user setting as a fallback.
- Resolve the controller's device and telemetry entries through public device
  and entity registries scoped to that selected entry. Match validated ESPHome
  unique-ID/object-ID suffixes, not mutable entity names. Validate device
  association when more than one device belongs to an entry. Reject ambiguous
  matches; never use the first arbitrary match.
- Store resolved entity IDs in the local binding. Entity ID changes must rebind
  without requiring re-learning. User device/title changes must not reroute RF.
- Invalidate on relevant entity/device registry updates, selected config-entry
  updates/removal/reload, and required service registration/removal. Filter
  listeners to the linked entry/controller. Re-establish policy/display state
  through existing reconnect behavior, not by assuming a stale service object
  is still live.
- After invalidation, operations recheck the current generation. Old events,
  delayed callbacks and old waiters cannot publish into a replacement runtime.

## Public telemetry and disabled entities

Replace the source of every field in `_TelemetrySnapshot`, not just fields used
by TX. Preserve existing number/text conversions and diagnostic names. Invalid,
`unknown`, `unavailable` or missing values must remain unavailable rather than
being interpreted as a usable zero or a successful operation.

Critical confirmation/readiness entities are endpoint status, last request ID,
last TX result, last payload length, TX success count and TX failure count.
An initially `unknown` last-request-ID state is allowed for readiness: released
firmware leaves its empty ID unpublished until the first TX. The entity must
still exist and be enabled; missing or `unavailable` states remain errors.
An unknown ID is never treated as a matching completion. Readiness and lifecycle
callbacks must apply the same distinction so a benign initial value does not
invalidate an otherwise live connection.
Configuration/protocol revision fields retain existing compatibility checks when
available; do not silently invent a new revision or firmware requirement.
Optional counters, payload diagnostics, elapsed time and version information may
be absent without blocking RF when the required confirmation contract is intact.
Keep existing repeat-count mismatch tolerance; do not require byte-identical
diagnostic formatting or every optional sensor for successful control.

All required entities are exposed by the default v0.7.1 package. However,
private-runtime reads could see telemetry that the user disabled as HA entities.
The public implementation must detect that case and name the exact entities to
enable under the linked ESPHome device. Do not silently enable user-disabled
entities or require a firmware update. Keep learned profiles and controller
association intact, and recover after enabling the entities without re-learning.
Document this exceptional upgrade action. Default-enabled v0.7.1 installations
must upgrade without additional configuration.

Read the Proflame2 firmware version from its version entity. Preserve the
separate ESPHome software-version diagnostic using public device information;
do not report one as the other. Optional missing version information is
`unavailable`, with no private fallback.

## Action dispatch and TX completion

All action payload construction remains in the existing transport paths. Route
the resulting dictionaries through the shared public invocation helper.
`blocking=True` means the HA action handler finished; it does **not** prove that
the controller's asynchronous radio worker completed or the fireplace responded.
Do not request structured response data from v0.7.1 firmware.

For one serialized TX:

1. Validate the current binding and required usable telemetry. Record the
   request ID, binding generation and success/failure counter baseline.
2. Subscribe to the bound state changes **before** dispatch. Keep the current
   backend/controller arbitration and request-ID generation unchanged.
3. Invoke the exact public TX action once with the existing arguments.
4. Evaluate current public states immediately after invocation and on relevant
   state changes. Do not rely on another event arriving after an already-complete
   request. Clear/check/wait sequencing must not lose a wakeup.
5. Accept successful local completion only for the matching request, fresh
   success-counter evidence, terminal `ok` result and matching payload length.
   A retained `ok`, matching ID alone, queued state, or action-handler return
   cannot confirm TX. Match failed completion with fresh failure-counter and
   matching terminal error evidence; a rejected payload need not have the
   successful payload length.
6. Preserve the existing bounded observation timeout and backend operation
   timeout behavior. Do not extend deadlines on repeated telemetry changes.
   Do not add an intentional polling/debounce delay to completion detection.
7. On disconnect, reload, counter reset, supersession or ambiguous/stale evidence,
   return failure/uncertain completion through existing recovery handling. Never
   replay a command merely because confirmation was late or missing. A later
   request takes a fresh baseline after readiness is restored.
8. Always remove temporary listeners and settle outstanding waiters on success,
   error, timeout, cancellation, failed setup and unload.

Test actual v0.7.1 publication sequences as well as reordered independent entity
updates, including old-request completion adjacent to a newly queued request.
No combination may turn an earlier result into a newer command's success.
Successful local completion preserves **Ready** and requested confidence;
confidence becomes observed only after a valid received remote packet.

The proof reproduced a mixed-snapshot acceptance in the existing predicate.
Fresh completion evidence is a necessary part of the migration, not an optional
new reliability feature. Do not copy the prototype's hard-coded payload length
or abbreviated request payload into production.

## Availability, RX and recovery

Use public endpoint state, config-entry lifecycle and required action presence
as readiness inputs. A registered service alone is not proof of an online API
connection. Pending TX/queued endpoint status is not equivalent to disconnect.
Normalize the existing firmware status values rather than comparing a single
display string to `ready`.

Maintain a local binding generation instead of comparing ESPHome runtime object
identity. RX liveness checks use that generation and public readiness; a
disconnect must wake or terminate a waiting receive operation so the owning
listener loop can reconnect. Preserve bounded receive behavior and cancellation.

Keep `esphome.proflame2_rx_packet`, all accepted event kinds and metadata,
device-ID filtering, packet parsing, deduplication and learning delivery intact.
Native remote observations must not cause a new HA transmission. Learning-mode
and active-listening transitions must continue to use their existing actions.

Reuse #34 recovery/backoff and failure-episode logging. Reapply RX policy and
display synchronization automatically after recovery. Preserve #36's existing
one-shot availability publication and runtime/unload guards. Status suppression
must not delay an eligible user operation or reconnection. No new polling or
heartbeat changes are included. Actual loss/recovery detection still depends on
HA/ESPHome's native API timing; compare publication after public readiness,
not only elapsed time since power removal.

## Implementation tasks

| ID | Concrete deliverable | Required check before proceeding |
| --- | --- | --- |
| P72-01 | Convert the proof into production contract tests and establish baseline fixtures for v0.7.1 actions/telemetry; enumerate all private accesses and snapshot fields | Tests fail against the private adapter when private runtime is forbidden; no firmware/schema changes |
| P72-02 | Implement scoped public binding and invalidation, required/optional capability handling and actionable missing/disabled-entity errors | Correct node/device, duplicate/ambiguous matches, rename, late registration and user-disabled telemetry covered |
| P72-03 | Replace all six action call paths with one public invocation helper, keeping argument construction unchanged | Released YAML schemas and exact arguments exercised; no direct ESPHome client calls |
| P72-04 | Replace snapshots and completion waiting with public states and event-driven fresh request-correlated evidence | Stale/queued/reordered/late/error/counter-reset sequences, timeout/cancellation, no replay or listener leaks |
| P72-05 | Replace private availability, RX-liveness, status, diagnostic and version paths; integrate existing recovery/publication owners | Startup/reload/disconnect/flapping/quiet recovery, active listening, learning, display restoration and #17/#34/#36 regressions |
| P72-06 | Execute default and version-matrix tests, review source for remaining private access, update technical/upgrade docs | All mandatory gates below; distinguish executed checks from source inspection and optional skips |
| P72-07 | Owner executes the self-contained hardware protocol using unchanged v0.7.1 firmware | Physical fireplace, active listening, learning, power-cycle/startup recovery and timing acceptance recorded |
| P72-08 | Review the final diff and prepare v0.7.2 release/HACS evidence | Separate authorization for commit/push/merge/release and re-requesting HACS review; do not claim acceptance before review |

Keep production boundary changes and their tests together in reviewable commits;
keep unrelated work out. Maintain existing public interfaces so a rollback to
v0.7.1 integration still works with the same firmware and learned configuration.
The test-only proof is replaced or folded into production tests, not retained
as a second installable implementation.

## Local implementation and audit record — 2026-11-17

| Task | Current result |
| --- | --- |
| P72-01 through P72-05 | Implemented locally; production contract and existing recovery/state tests pass |
| P72-06 | Source/test audit and local checks complete; GitHub HACS/Hassfest/compatibility workflows have not run on this uncommitted draft |
| P72-07 | Not run; owner hardware acceptance with released v0.7.1 firmware is the next gate |
| P72-08 | Local diff and review-concern mapping checked; commit, publication, release and HACS re-review are not authorized by these results |

The audit scanned all 57 production Python modules, reviewed their HA imports
and cross-integration access, and checked 88 test and 77 tool modules for the
same boundary. Manual review covered dispatch, each snapshot field, availability,
RX delivery, version/status/diagnostics, registry binding and cleanup. No remaining
production access to ESPHome's runtime, client, service catalog or state maps was
found. Proflame2's own private methods and documented Python exception attributes
are distinct from another integration's internals.

The source regression guard now covers the complete integration, including the
new endpoint helper and version path. It detects private HA imports/attributes,
direct or dynamic private runtime access and direct ESPHome data-store reads.
Negative examples check that the guard catches those patterns. This supplements
manual review; it is not a general Python data-flow proof.

Test-only native-client models, a forbidden-runtime sentinel, and extraction of
HA's own installed handler remain intentional compatibility-test boundaries.
Production does not import or introspect that handler. All five actions declared
by released firmware now run through their actual production methods, real HA
service/schema validation and that handler with a synthetic network endpoint.
The sixth optional action is routed separately and remains a no-op when absent
from released firmware. Cleanup tests also verify that later state/registry/action
changes do not call a closed endpoint. Obsolete private state-map version-test
parameters and duplicate assertions/publications were removed.

Executed local checks:

- HA 2026.9.0 full suite: **2,349 passed, 34 optional/external skipped, one
  deselected**. The final strengthened contract/version module checks then
  passed **124 tests** on this stack and **124** on HA 2025.8.0. Production code
  did not change during this audit; the earlier complete minimum-version run
  remains **2,285 passed, 34 skipped, one deselected** before test expansion.
- Both exact CI dependency stacks install with compatible dependencies. The
  DNS dependency constraint is selected per HA version: older aiodns needs
  pycares below 5, whereas the newer stack requires pycares 5.
- Ruff, Black, whitespace, workflow YAML/matrix checks and source/private-data
  preflight passed. The firmware tree is unchanged from v0.7.1.

The HA 2026.11.1 test-harness limitation above remains explicit. No current-core
runtime pass, hardware result or successful GitHub CI run is inferred from the
local audit. Existing public-device-registry deprecations remain v0.8.0 work;
this transport change does not expand into that separately assigned cleanup.

## Automated validation and compatibility gates

- Actual migrated transport through HA service registration, schema validation,
  entity/device registries, state machine and event helpers. Mocks simulate the
  radio/network boundary, not a fake HA service registry.
- A guard rejects any access to the linked ESPHome runtime. Audit production
  code for other-integration `runtime_data`, direct API execution, private state
  maps, handler introspection and stale-runtime fallback. Own Proflame2 runtime
  structures are not prohibited; remove obsolete error-message checks tied to
  the old ESPHome private-runtime path.
- Both legacy and extended payloads, both band settings, all action arguments,
  optional action absence, all snapshot fields and correct version semantics.
- Completion delivery before/after dispatch return, every relevant state order,
  unchanged terminal values, old/new requests, fresh errors, dropped updates,
  counter reset and wrong payload length. Zero automatic replay on uncertainty.
- Existing control semantics: flame 0, retained OFF-state settings, explicit
  services, CPI, coalescing, refresh, native RX arbitration and controller ownership.
- Public lifecycle: missing/delayed services/entities, disabled critical and
  optional entities, name/ID changes, multiple devices/controllers, registry
  recreation, unload/reload, failed setup, shutdown and flapping. No subscriptions
  or timer callbacks surviving their runtime.
- Preserve #17 completion behavior and #34/#36 recovery/status regressions.
  Exercise quiet recovery with active listening both enabled and disabled.
- Run supported contract tests on HA 2025.8.0, 2026.9.0 and current supported HA
  (2026.11.1 at design time), each with a compatible Python/plugin/dependency
  stack. Full default suite on the maintained test baseline and current HA where
  feasible; record dependency limitations without calling a source check a pass.
  A failed declared-minimum contract is a compatibility/release blocker.
- Ruff, Black, whitespace, source/private-data preflight, HACS, Hassfest and docs
  checks. Firmware source must be unchanged; firmware rebuilds are not required
  just to qualify an HA-only patch. No private captures in test fixtures.

## Hardware and latency acceptance

Follow the complete
[v0.7.2 hardware protocol](issue_22_v0_7_2_hardware_validation.md).
Do not substitute the earlier #34 protocol or assume previous runs qualify a
changed transport. The new protocol combines checks to limit manual repetition.

The proof measured only local dispatch (about 0.012 ms median). Hardware testing
must separately record actual fireplace behavior, command-to-radio delay,
command-to-completion delay, RX-to-HA behavior and post-recovery usability.
There must be no new fixed delay, recurring timeout, false completion or lost
subscription. Any reproducible visible slowdown or completion regression versus
v0.7.1 is a blocker until explained and corrected; do not widen deadlines to hide
it. Native API offline-detection delays already present in v0.7.1 are recorded
separately from the transport's response after HA detects readiness.

## Stop conditions and residual risk

Stop and consult the owner if unchanged v0.7.1 firmware cannot provide the public
contract; mandatory fields require a firmware change; identity cannot be bound
safely without new user configuration; the declared HA minimum must rise; or a
solution requires broader state/control/RF redesign. The first two conditions
exclude this work from v0.7.2 and move it to v0.8.0.

Risk is medium: fresh completion and lifecycle changes affect every LilyGO
operation. Mitigation is the narrow HA-only boundary, unchanged firmware/RF,
failure-first production-adapter tests, explicit supported-version checks and
hardware acceptance. The feasibility proof reduces architectural uncertainty;
it does not certify implementation or RF range/reliability.


## Cold-start hardware feedback and correction — 2026-11-17

An owner test with the local 0.7.2-debug integration and v0.8.0 firmware failed
before dispatch because last-request-ID telemetry was rejected as unavailable.
Both released v0.7.1 and the v0.8.0 branch initialize this ID and its publication
cache to empty strings; the publisher suppresses unchanged text. HA can therefore
show `unknown` until the first TX. The original synthetic fixture published an
empty string instead, hiding this initial-state distinction.

A new regression reproduced the exact reported readiness error before the fix.
The public endpoint now permits only the last-request-ID field to be unknown,
while requiring the entity to exist and be enabled. Unknown states on other
critical fields, and unavailable states on every critical field, still block
sending. Lifecycle invalidation follows the same rule. Confirmation still needs
a real matching ID plus fresh terminal/counter evidence, and cannot succeed with
an unknown ID. Firmware and RF behavior are unchanged.

The version-scaffold test also no longer assumes equal integration/firmware
versions; it retains independent firmware version-format/logging checks. This
supports the intended HA-only update and the owner's 0.7.2-debug local stamp.
The current default suite passed 2,366 tests (34 optional/external skips, one
deselected); all 282 targeted transport/runtime/recovery/scaffold tests also
passed on HA 2025.8.0. Owner hardware retesting remains necessary; the v0.8.0 smoke report
does not replace the unchanged-v0.7.1-firmware acceptance gate.


## Owner smoke feedback and confirmation-message refinement — 2026-11-17

The owner reports that smoke testing found no functional or timing difference.
After powering Test LilyGO off, an attempted command produced a missing-TX-
confirmation error. That is compatible with HA still considering the API link
online during its existing offline-detection interval; it does not establish
whether a command reached the controller. Formal unchanged-v0.7.1-firmware
hardware acceptance remains separate from this informal feedback.

Known-offline rejection and missing confirmation remain distinct. The transport
now raises a specific RuntimeError subclass for absent fresh TX confirmation,
preserved through the backend. Only presentation of that condition is refined:

> LilyGO did not confirm sending the command. Check that LilyGO is powered on
> and connected to Wi-Fi. The command may still have been sent; check the
> fireplace before trying again. Home Assistant restored the controls to their
> previous settings.

The original technical cause remains in normal HA logs. No timeout, readiness,
retry, recovery, RF or firmware behavior changes. The message fits HA's sensor
state length limit. All 263 related transport/backend/control/service tests
passed, including both confirmation-deadline paths, preservation of the specific
exception, one attempted send only, rollback and unchanged confidence/state.
Ruff, Black and whitespace checks passed. This targeted run follows the earlier
2,366-test full-suite result; it is not a new complete-suite or hardware pass.


## Owner control/recovery permutations — 2026-11-17

The owner subsequently confirmed successful HA restart with LilyGO online,
control rollback with the new message after controller shutdown, restoration of
control/RF transmission after power-up, known-offline feedback when HA starts
with LilyGO off, and restored control/RF transmission after LilyGO starts later.
rtl_433 received packets in each reported successful control case. Both error
paths and the exercised control recovery paths therefore passed these hardware
subchecks; no further behavior/timing difference was reported.

The [hardware protocol result record](issue_22_v0_7_2_hardware_validation.md)
now distinguishes these passes from partially covered H1/H4/H5 and unreported
checks. The most recently reported firmware was v0.8.0; these observations do
not replace released-v0.7.1 firmware acceptance or claim native RX/learning,
physical fireplace acknowledgement, precise recovery latency or all lifecycle
checks. No implementation change was needed from this report.


## V08-02 registry compatibility implementation — 2026-11-18

The owner confirmed a minimum of HA 2025.8.0 and requested two implementations
selected by HA version. `DeviceRegistryCompatibility` now owns device lookup and
ownership reads. `LegacyDeviceRegistryCompatibility` is selected before 2026.8,
retaining the old lookup and multi-entry ownership model. The modern class is
selected from 2026.8 and uses the new entry-scoped lookup and single owner field.
The factory uses public HA major/minor version constants. There is no dynamic
fallback to legacy calls on modern HA.

Both legacy identity reconciliation and public-controller registry event
handling use the interface. The existing single-owner/collision guards and
entity unique-ID migration stay in place. Modern lookups cannot select another
entry's matching identifier. Shared legacy devices are left untouched. Normal
device IDs and entity IDs are preserved; new tests verify that explicitly.

Version-boundary and API-isolation tests cover the last legacy/first modern
release, old shared ownership and wrong-owner devices, modern duplicate
identifiers across entries, and refusal to fall back on missing modern methods.
The full suites pass on HA 2025.8.0 and 2026.9.0: 2,383 passed, 35 skipped
and one deselected on each. The additional skip is the native legacy shared-
device case on modern HA, or the modern cross-entry duplicate-identifier case
on legacy HA. Unit fixtures exercise both adapter interfaces on both stacks.
The native model-specific case executes on its applicable stack. No firmware, RF, controller-sharing or parent-link change
is included in that V08-02 validation baseline. V08-03 is implemented in the
subsequent section. No GitHub issue or release has been changed.


Validation record for V08-02: full HA 2026.9.0 run completed in 68.79 seconds;
full HA 2025.8.0 run in 110.48 seconds. These are test-run durations, not measured
controller latency. Ruff, Black, whitespace, relative Markdown links and source
preflight passed. Firmware is unchanged. Source inspection confirms the two
legacy registry interfaces are used only inside the legacy class. At this
V08-02 baseline the entity parent hints were unchanged; the following V08-03
work removes them. Owner registry identity/parent inspection after restart/reload,
remaining communication acceptance and publication are separate from these
software passes. All changes remain local and uncommitted.


## V08-03 implementation — 2026-11-18

The runtime already establishes the fireplace's parent using the selected
ESPHome controller's registry device ID, and retries when the parent is not yet
available. This public `async_update_device(via_device_id=...)` path works on
HA 2025.8.0 and modern HA. Keep it as the sole owner of the relationship.
Remove the four redundant `DeviceInfo(via_device=...)` identifier hints from
sensor, switch, number and profile button. The RF-band select already supplies
only the fireplace identifier and needs no change. Do not add a modern-only
DeviceInfo field or a new relationship watcher.

The new modern-HA regression failed before this change: separate ESPHome
entries with the same controller identifier caused entity registration to
replace the second fireplace's correct parent with the first controller.
The corrected test passes using the existing device-ID relationship.

Validation covers full platform registration/reload, stable device and entity
identities, controllers with identifiers or MAC-only connections, delayed
parent discovery, HA detachment after controller removal, replacement linking
on fireplace reload, and YardStick's single parentless device including its
band select. An AST guard rejects deprecated identifier parent hints throughout
production Python. Legacy HA merges identical identifiers globally, so the
modern cross-entry ambiguity test runs only on its applicable registry model.

No firmware, RF, controller-sharing policy, minimum HA version or public service
behavior changes. Existing owner communication acceptance remains separate.

V08-03 automated validation: full HA 2026.9.0 suite passed 2,391 tests
(35 skipped, one deselected); HA 2025.8.0 passed 2,390 tests (36 skipped,
one deselected). The extra minimum-version skip is the modern-only duplicate-
identifier parent regression. Other skips are existing external/optional checks
and the opposite native registry model. Ruff, Black, whitespace, relative
Markdown links and source preflight passed. Firmware remains identical to
v0.7.1. These results do not replace owner inspection of the actual registry or
remaining public-transport hardware acceptance. All changes remain local and
uncommitted; no GitHub issue or release was changed.


## Issue ownership — updated 2026-11-18

Issue #22 now tracks this entire v0.7.2 patch, including V08-02/V08-03 registry
compatibility, V08-04 public transport, existing-firmware V08-05 confirmation,
minimum-version metadata/testing and clearer missing-confirmation errors.
The unimplemented V08-05 structured firmware response, capability detection
and matching HA adapter moved to existing issue #20 for v0.8.0. Existing
firmware remains supported through public request-correlated telemetry. Both
public issue bodies were updated and read back after owner approval. No new
issue is needed. The local implementation branch is `issue_22-v0.7.2`.


## Persisted registry upgrade acceptance — 2026-11-18

The owner identified a distinct upgrade risk not established by separate-version
unit suites. Added `scripts/validate_registry_upgrade.py`, an explicit one-off
check that writes real HA 2025.8.0 stores, loads them under HA 2026.10.4 and
checks the same fixture after a second modern process load. Six devices and
16 entities retain their config-entry ownership, device/entity IDs and parent
links through HA migrations and Proflame2 reconciliation/device registration.
Fixtures cover two separate LilyGO controllers (identifier and MAC-only),
YardStick's parentless fireplace/band select and an unrelated device. All phases
passed. The test uses synthetic data and isolated temporary storage, with no
network or RF. Test-only private store hooks flush actual files immediately;
production does not use these hooks. Full integration startup, native API
communication and physical hardware acceptance remain separate. H8 in the
hardware protocol records the procedure and its precise coverage.


## Known-offline user guidance — 2026-11-18

Following successful H4 testing, the owner requested that the known-offline
message suggest possible causes including power-off and network/Wi-Fi loss.
Required unavailable telemetry now raises an offline-specific subclass of the
existing controller-unavailable exception. The user-error formatter recognizes
that type through existing explicit backend exception causes and displays:

> LilyGO is unavailable. It may be powered off or disconnected from the network.
> Check its power and Wi-Fi/network connection.

Existing rollback guidance is appended. Logs retain the technical unavailable
field. Disabled/missing/ambiguous entity and action configuration errors are
not rewritten as power/network failures. No string matching, network probe,
retry, timeout, RF, firmware or recovery behavior changes. Missing completion
after dispatch retains its separate existing message. Targeted behavior tests
cover direct/wrapped offline errors, unchanged rollback/confidence, one send
attempt, technical logging and preservation of specific disabled-entity errors.

Validation: 381 related control/service/backend/public-transport/runtime/display tests passed on HA 2026.10.4. Ruff, Black and whitespace checks passed. No new hardware or firmware test is required for the message-only change; H6 binding and firmware-version confirmation remain pending.


## Active ESPHome identity migration regression — 2026-11-18

Read-only inspection after the owner's HA 2026.8.0 upgrade found all critical
telemetry enabled and still associated with the selected ESPHome entry. Their
unique IDs migrated during ESPHome startup from legacy mac/type/object-ID
strings to the API's version-3 mac/device/type/unmangled-name format. The
endpoint only recognized the legacy suffix, so every field appeared missing.
The long aggregate error exceeded the HA sensor-state limit and became Unknown.
This is an integration defect, not a Wi-Fi or controller-power diagnosis.

The earlier registry-only persisted upgrade test explicitly omitted ESPHome
startup. It passed but did not cover this migration; the original review
missed that gap. New tests generate identities using the actual installed
`aioesphomeapi.build_device_unique_id` helper and exercise live entity-registry
updates, reconnect and successful command completion. Both identity migration
and oversized sensor state were reproduced as failing regressions first.

Correction:

- Recognize the legacy object-ID suffix and native API v2/v3 unmangled-name
  identity components through public entity-registry fields. Normalize only the
  firmware name encoded in that unique ID, never the editable HA display name
  or entity ID. Still restrict matches to the selected ESPHome entry/platform,
  reject ambiguity/disabled entities and validate controller device ownership.
- Bound primary and diagnostic string sensor states to HA's 255-character
  limit using a trailing ellipsis. Non-string values are unchanged. The primary
  Last Issue attribute and technical logs retain the full error.
- Run the transport behavior matrix with both legacy and modern identity
  fixtures; test the native API's v1/v2/v3 outputs and long actual HA sensor
  publication. Add the exact HA 2026.8.0/API 45.6.1 stack to compatibility CI.

No firmware, retry, RF, registry-ownership policy or service argument change.
Owner must restart HA to load the fix and repeat one HA command/native update;
no full hardware retest is necessary for unchanged paths. The agent did not
modify/restart the live deployment or send any packet.

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
