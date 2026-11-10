# v0.8.0 stability and compatibility implementation plan

Status: proposed; inventory reviewed 2026-11-10 against the repository, deferred
branch and all 12 open GitHub issues. Implementation has not started under this
plan. v0.7.0-beta7 is published; the final v0.7.0 baseline is not yet selected.

## Goal and scope

The primary goal is **stability and compatibility**. Preserve working fireplace
control, saved profiles, entity identities and automations across HA, ESPHome
and firmware upgrades. Avoid new features and broad rewrites whose principal
benefit is cosmetic.

The one approved feature exception is **LilyGO power OFF and restart by
reconnecting USB**, already implemented on a deferred branch. It is the storage
shutdown gesture described in the [shutdown design](controller_shutdown_design.md),
not a new HA restart control or a fireplace power command.

The inventory below distinguishes committed release intent, work already
implemented, conditional cleanup, and queued proposals that need approval.
An inventory entry does not authorize starting every item. Necessary severe
regression fixes should ship as v0.7.x patches rather than wait for v0.8.0.

## Inventory of planned work

Priority A protects compatibility or addresses a concrete stability risk.
Priority B finishes the approved exception. Priority C is lower-priority cleanup
or assessment: it must not delay stability fixes or introduce disproportionate risk.

| ID | Work item and reference | Current state | Proposed v0.8.0 scope and completion evidence | Priority |
| --- | --- | --- | --- | --- |
| V8-01 | Supported-version contract and validation | HACS declares HA 2025.1.0 minimum; HA tests pin 2026.9.0; ESPHome validation pins 2026.8.2. These are different facts, not a proven support matrix. | Establish the supported minimum/current HA and ESPHome combinations and both firmware contracts. Exercise representative supported versions; obtain approval before raising a minimum. Maintain the matrix and upgrade guidance with V8-12. | A |
| V8-02 | Device lookup deprecations, [#34](https://github.com/jeffgregx2/HACS-Proflame2/issues/34), also reported in [#35](https://github.com/jeffgregx2/HACS-Proflame2/issues/35) | Two deprecated lookups remain in integration identity migration. | Use supported identifier/connection lookup APIs, handle missing/ambiguous matches, preserve ownership and identity migration. Test duplicate assignments, existing configurations and unrelated-device isolation. | A |
| V8-03 | Parent-device deprecations, #34/#35 | Sensor, switch, number and button device-info construction still uses `via_device`; runtime reconciliation already uses device IDs in places. | Migrate the complete shared relationship path to supported APIs. Preserve LilyGO's ESPHome parent, YardStick's fireplace-only presentation and one controller per fireplace. Verify delayed parent discovery, reload and removal. | A |
| V8-04 | Public HA/ESPHome transport, [#22](https://github.com/jeffgregx2/HACS-Proflame2/issues/22) | Action discovery/execution and telemetry still depend on ESPHome `runtime_data`. | Bind actions, devices and telemetry through public HA registries, services, states and events. Preserve RX event schema, #34 reconnect/logging behavior, version reporting, arbitration and unload cleanup. Remove private-runtime dependencies only after both supported firmware paths work. | A |
| V8-05 | Request-correlated completion and firmware compatibility, #22; preserve [#17](https://github.com/jeffgregx2/HACS-Proflame2/issues/17) | Existing firmware exposes request/result telemetry; no Proflame2 structured TX-response contract is implemented. | Support installed firmware using public request-correlated telemetry and new firmware using an explicit capability marker plus a structured action response. Respond after local TX completes, never at enqueue. Never retransmit because a response is late. Keep #17 compatibility only while a live path needs it. | A |
| V8-06 | Compact payload diagnostics, [#25](https://github.com/jeffgregx2/HACS-Proflame2/issues/25) | Diagnostic categories already shipped in beta2; schema defaults and package declarations have coverage. Compact presentation remains outstanding. | Design a compact HA-owned presentation with full payload evidence retained. Do not truncate firmware data or mutate ESPHome-owned entities. Review the entity/automation impact before implementation; do not repeat delivered classification work. | C |
| V8-07 | Learning progress follow-up, [#32](https://github.com/jeffgregx2/HACS-Proflame2/issues/32) | Detection guidance, power prerequisites and numbered action headers shipped in beta7. | Assess whether meaningful packet/retry feedback is still needed after beta7. Only add feedback that explains a real wait using existing observations; preserve acceptance, deadlines and action numbering. Do not make cosmetic progress changes a release gate or promise fixed packet totals. | C |
| V8-08 | LilyGO shutdown/USB restart, [design](controller_shutdown_design.md) | Functional commit `216785c` is on `feature/controller-shutdown-v0.8.0`, based on pre-beta7 code. Storage-guide changes are uncommitted on that branch. Some hardware behavior is confirmed; final checks remain. | Integrate the feature without losing v0.7.0 fixes or the pending guide. Complete both-band builds, USB/busy refusal, cancellation, notification-before-cutoff, early-OTA shutdown and repeated USB wake tests. Firmware update is required to obtain this optional feature. | B |
| V8-09 | C++ formatting and CI, [firmware backlog](firmware_cleanup_backlog.md) | Formatter target exists; a clean mechanical baseline and CI enforcement remain planned. | After functional branch integration, format separately with the pinned formatter and enable the maintained check. No semantic/RF change in this commit. Keep it separate from code moves and fixes. | C |
| V8-10 | Component responsibility boundaries, firmware backlog | Main component is about 3,300 C++ lines plus a 940-line header; specialist radio/decoder modules already exist. | Prefer small, separately reviewed extractions where ownership or lifecycle risk justifies them. Cover behavior before moving it. Do not rewrite timing-sensitive RX/TX, change scheduling or move every subsystem merely to reduce line count. | C |
| V8-11 | Firmware diagnostics/logging cleanup, firmware backlog | Startup identity, full PCM diagnostics and malformed-repeat counters already exist. | Audit remaining repeated idle/capture output and duplicate formatting. Preserve opt-in complete evidence, band/version identity and useful failures. Change severity or rate limits only where supported by observed noise and tests. | C |
| V8-12 | Firmware toolchain and harness maintenance, firmware backlog | Host C++ tests and 315/433.92 MHz configuration/compile targets exist. | Maintain the agreed version matrix, production/debug configuration coverage, invalid-band rejection and both-band builds. Update dependency/deprecation handling without silently dropping the current supported version. Reuse these gates across work items. | A |
| V8-13 | Flash-chip driver suggestion, firmware backlog | Earlier board suggested `generic`; pinned ESPHome 2026.8.2 does not accept the newer setting. | Assess on supported versions and the actual board. Apply only a compatible, evidenced change; otherwise document the limitation and defer. Do not impose a board-wide driver assumption or raise the minimum solely to silence a suggestion. | C |
| V8-14 | Post-v0.7.0 stability intake | No new regression is established by an issue merely remaining open. #35 now confirms successful beta7 operation after removing local patches. | Triage reproducible regressions with exact versions and a failing behavioral test. Fix urgent defects in v0.7.x; add appropriate nonurgent fixes explicitly to this plan. Do not invent additional work or treat observed RF range as a diagnosed code defect. | A, as needed |

V8-02/V8-03 are detailed in the
[integration maintenance backlog](integration_maintenance_backlog.md). Reported
registry removal dates include HA 2027.8; verify each affected API and its
replacement against the supported versions rather than treating that as one
universal deadline.

## Queued work requiring a scope decision

These items were found during the inventory but have no established v0.8.0
assignment. They are not silently included in the implementation commitment.

| Candidate | Recommendation and boundary |
| --- | --- |
| Risk-based coverage audit | Recommended supporting work for V8-01/V8-12: distinguish narrow firmware-job Python coverage from full HA coverage, then fill genuine failure-path gaps. Do not chase a percentage, repeat tests merely for counts or treat mocked RF as hardware proof. Its detailed queued audit is not yet assigned a release. |
| Main-loop responsiveness during software-timed TX | Keep as a separate investigation requiring approval. Existing notes identify possible same-core preemption and shared-state risks. Changing task/core affinity, worker priority, RMT TX or framework code is not routine decomposition and requires its own design and waveform/hardware evidence. |
| Delayed retransmission interval | Keep unassigned. No new setting/default change without receiver evidence and approval. Native burst observations do not establish a universal interval. |
| Older YardStick modernization checklist | Reconcile rather than implement wholesale: both bands, both protocols, learning and ownership already shipped. Only a demonstrated remaining recovery defect belongs in stability intake. Continuous listening, new retries or other parity features remain excluded. |
| Repository/tool/configuration reorganization | Low-value for release reliability unless a concrete problem requires it. No bulk directory moves, public-path changes or pytest/config consolidation as automatic v0.8.0 scope. |
| Broader per-repeat receive/capture redesign | Not required by the accepted beta6 malformed-repeat selection fix. Leave unassigned unless new evidence identifies a gap. |
| ESPHome action-permission observation in #35 | No proven RX permission prerequisite or HA 2026.11 regression. Do not add a mandatory setup permission based only on an observation. Investigate only if it reproduces on supported clean builds. |

Private captures and operational data must be kept outside source trees and
excluded from test fixtures, build inputs and release artifacts. Resolve any
source-input violation before implementation packaging or permission repair;
this is a workspace prerequisite, not an added product feature.

## Proposed implementation sequence

### Phase 0 — Establish a recoverable baseline

- [ ] P0.1 Select the final v0.7.0 baseline when available. Until then, use beta7
  only for investigation; do not call it the final release baseline.
- [ ] P0.2 Preserve the canonical checkout's pending #34 documentation and the
  deferred shutdown branch's pending storage guide. Plan integration from the
  actual branch diff, not an assumption that the branch is clean/current.
- [ ] P0.3 Inventory source-input violations and consumers before migration;
  preserve evidence and obtain any needed storage/ownership decisions. Do not
  delete captures, broaden access or rewrite history to make a check pass.
- [ ] P0.4 Record one automated baseline and the known hardware limits. Do not
  restart the full v0.7.0 manual test matrix.

**Exit:** a known source baseline and safe selected inputs; existing work is
preserved. No implementation branch, deployment or history changes are made by
this planning task.

### Phase 1 — Prove the public compatibility contract (V8-01, V8-04, V8-05)

- [ ] P1.1 Reconcile the [transport design](issue_17_ha_esphome_public_transport_design.md)
  with #22's explicit staggered-upgrade requirement. Keep compatibility with
  installed firmware and prohibit hidden fallback to private ESPHome objects.
- [ ] P1.2 Prove public service discovery/binding and structured response handling
  on the agreed HA/ESPHome versions. Distinguish ESPHome calling HA with a
  response from HA calling an ESPHome action that uses `api.respond`.
- [ ] P1.3 Specify an explicit firmware protocol/capability marker, response
  schema, request identity, local-completion boundary, errors and timeout behavior.
  Define response completion after asynchronous worker TX, including failures,
  without blocking the main loop or returning a previous request's result.
- [ ] P1.4 Prove legacy-firmware confirmation from public entities/states using a
  fresh matching request/result; stale telemetry or unavailable entities cannot
  count as success or trigger a duplicate send.
- [ ] P1.5 Document tested minimums, the compatibility window and both upgrade
  directions. If the existing declared minimum cannot support the design, stop
  for a compatibility decision before changing it or selecting another architecture.

**Exit:** a small executable contract proof and updated design. The documented
public APIs support the approach, but that alone is not hardware/version-matrix
acceptance. Do not build a second native client or connection owner without review.

### Phase 2 — Migrate registry relationships (V8-02, V8-03)

- [ ] P2.1 Centralize the applicable supported device lookup/relationship logic;
  migrate existing identifier reconciliation and all entity platforms.
- [ ] P2.2 Test missing/ambiguous parents, delayed discovery, existing IDs,
  duplicate ownership, reload, rename and removal. Verify no unrelated device
  changes and no duplicate fireplace/controller entities.
- [ ] P2.3 Validate the actual minimum/current HA combinations and warning
  removal. Keep these commits separate from transport/protocol behavior changes.

**Exit:** stable identities and supported registry access. The device-ID resolver
can be reused by public endpoint binding instead of creating a second resolver.

### Phase 3 — Implement the public transport (V8-04, V8-05)

- [ ] P3.1 Bind public actions and endpoint entities to the selected ESPHome
  entry/device. Refresh bindings after rename, recreation, reload and late discovery;
  reject ambiguous/wrong endpoints and release all subscriptions on unload.
- [ ] P3.2 Add versioned firmware response support alongside the installed action
  contract. Select the confirmation mode from the explicit marker. Preserve
  existing action names/arguments and RX schema unless the reviewed versioned
  contract explicitly adds compatible fields.
- [ ] P3.3 Use blocking public action calls with request-correlated responses on
  new firmware; use bounded public telemetry confirmation on existing firmware.
  Preserve no-replay behavior on timeout, late responses and uncertain TX.
- [ ] P3.4 Move availability, telemetry, diagnostics and firmware-version reporting
  to public interfaces. Retain one lifecycle owner, controller serialization,
  active listening, display resync, learning and refresh behavior from v0.7.0.
- [ ] P3.5 Test actual migration and reload paths, not just isolated adapters.
  Remove private-runtime reads and #17 shims only once no supported production
  path depends on them. Search the complete integration, including debug paths.

**Exit:** old and new firmware both work with the new integration, without hidden
private-state access, duplicate TX or stale acknowledgements. New firmware must
also retain the existing callable contract for staged upgrades; verify it with
the v0.7.0 integration rather than assuming interoperability.

### Phase 4 — Finish the approved shutdown feature (V8-08)

- [ ] P4.1 Integrate the existing feature commit onto the maintained baseline,
  preserving its pending guide and adapting only the transport/event boundary
  needed by Phase 3. Review the merged result rather than replaying old patches blindly.
- [ ] P4.2 Preserve battery-only cutoff; four-second red prompt; ten-second hold;
  USB/busy/unknown-power refusal; release-to-cancel; USB restart; and ordinary
  short-press behavior. Keep the HA notification and 0.5-second interval together.
- [ ] P4.3 Retain OTA rollback safeguards and failure recovery. Complete host
  regression and final firmware builds at both bands.
- [ ] P4.4 Perform the focused hardware checks below, then finalize the storage
  guide only on the v0.8.0 branch. Keep normal operation powered on; no additional
  controller entity or user setting is introduced.

**Exit:** the existing feature has current-source validation and user instructions;
an old compile or one successful cutoff does not substitute for the remaining checks.

### Phase 5 — Select only justified cleanup (V8-06, V8-07, V8-09–V8-13)

- [ ] P5.1 Review a compact-payload proposal against #25. Decide which HA-owned
  surface presents it and document entity-ID/automation impact. If a new projection
  is needed, obtain agreement before adding UI clutter or replacing another
  integration's entity. Full payload evidence remains available.
- [ ] P5.2 Reassess learning feedback after beta7 acceptance. If work is still
  justified, display existing accepted/waiting information without changing
  evidence rules, timeout resets or the meaning of action numbers.
- [ ] P5.3 Apply a separately reviewed mechanical C++ baseline, then enable the
  maintained formatter check. Do this after functional branch integration to
  avoid repeatedly resolving formatting conflicts.
- [ ] P5.4 Select small lifecycle/ownership extractions with measurable benefit;
  establish focused host tests before each move. Keep setup, RX lifecycle,
  diagnostics, display/API and telemetry ownership explicit. Do not combine
  extraction with a scheduling, RF or protocol redesign.
- [ ] P5.5 Audit logging and flash-chip suggestions against supported versions.
  If they cannot be addressed without a wider compatibility or behavior change,
  record the limitation and defer instead of expanding the release.
- [ ] P5.6 Maintain harness/version checks and address concrete test gaps exposed
  by these changes. The broader queued coverage audit needs a scope decision.

**Exit:** selected cleanup is independently reviewable and regression-free.
Unselected cosmetic work does not block a stable compatibility release; record
any proposed deferral for owner agreement instead of silently changing targets.

### Phase 6 — Combined qualification and release

- [ ] P6.1 Run the full HA suite and relevant helper/archive tests against final
  source. Run HACS/Hassfest, documentation checks, Python checks and the C++
  formatting/host checks selected by the changes.
- [ ] P6.2 Validate production/debug configuration on both bands and perform
  final 315/433.92 MHz firmware builds once for the final integrated firmware.
  Reuse results unless a later change invalidates them.
- [ ] P6.3 Complete the focused hardware acceptance below, record exact source
  versions, and publish a beta with clear staggered-upgrade guidance.
- [ ] P6.4 Review reporter acceptance and post-beta regressions. Record skipped
  checks explicitly, update issue/release mappings and validate published source,
  stamped versions, assets/checksums and release workflows before GA.

## Verification strategy

Use maintained commands such as `make test`, `make firmware-unit-test`,
`make esphome-validate`, `make learning-id-package-test`, `make lint-python`,
`make format-python-check` and `make format-cpp-check` where applicable.
Run focused checks while changing their owner; run the combined suite once per
integration milestone. Use native temporary environments and synthetic fixtures.
A passing host suite does not prove RF output or real HA lifecycle behavior.

| Boundary | Automated evidence | Focused hardware acceptance |
| --- | --- | --- |
| Registry and endpoint identity | Minimum/current HA; entity/device rename/recreation; late discovery; duplicate ownership; unload cleanup. | One upgrade of an existing test fireplace: IDs, automations and parent link remain intact. No separate manual permutation matrix. |
| Old/new firmware transport | Capability selection, public calls, fresh request correlation, late/lost/error response, unavailable telemetry, no duplicate send; old integration/new firmware compatibility. | Existing firmware with new HA integration, then response-capable firmware with new integration; one normal TX/control cycle and receive check for each. Verify the reverse integration/firmware upgrade order on Test. |
| Lifecycle and RF preservation | Offline startup, late actions, runtime replacement, listening OFF/ON, fences, stale events, legacy/extended codec and exact band/payload/repeat settings. | One real ESPHome reload/reconnect with no HA command before two matching external RF states. Use native legacy reception and generated extended YardStick frames; do not require damaged extended native packets or unavailable hardware. Keep Prod unchanged. |
| Shutdown and OTA | Gesture times, cancellation, refusal, PMIC errors, cleanup ordering, notification ordering, no RF on shutdown, reconnect guards. | Battery cutoff/USB wake, release-to-cancel, USB-connected refusal and release/retry, busy refusal, shutdown soon after OTA followed by same-build restart, and repeated shutdown/wake. Verify notification if HA is connected; delivery is best effort. |
| Presentation | Full payload remains available; categories/IDs stable; truthful progress; unchanged learning acceptance. | One desktop/mobile visual check only for the surfaces actually changed. |

Combine compatible observations within each hardware session. Do not require
long outage waits or repeated logging-clock tests that deterministic automated
tests already cover. If RF verification is ambiguous, retain independent SDR
or PCM evidence privately and reproduce just the failing boundary.

## Decisions before dependent work

1. **Compatibility:** preserve declared HA 2025.1 and current firmware support,
   or explicitly approve an updated minimum if the Phase 1 proof requires it.
   Do not infer minimum support from the version used by the test suite.
2. **Payload UI:** agree how compact presentation can be delivered without
   silently changing/hiding ESPHome entities or breaking automation references.
3. **Cleanup selection:** confirm any component extractions and broader coverage
   work after their concrete risk/benefit review. A large refactor is not required
   merely because it was listed in a cleanup backlog.
4. **Release date/effort:** no defensible detailed duration forecast yet. The main
   uncertainty is the versioned TX-completion bridge and staggered upgrades, not
   formatting. After Phase 1's contract proof, estimate the selected executor-led
   work independently, account for shared setup/tests once, separate firmware
   build/CI waits from user installation and acceptance, and recalibrate after
   the first migrated endpoint. Do not estimate the entire backlog as though all
   optional investigations were approved implementation.

## Work already delivered or kept outside v0.8.0

| Item | Treatment |
| --- | --- |
| #17 telemetry-key fix | Released in v0.5.5; protect compatibility until the public migration removes its dependency. Not a new repair. |
| #25 diagnostic classification | Released in v0.7.0-beta2; audit/preserve, do not implement again. |
| #26/#32/#33 core protocol, learning and flame-OFF support | Delivered across v0.7.0 betas. Beta7 includes the additional learning guidance/power prerequisites. Only demonstrated regressions re-enter intake. |
| #34 offline logs/display recovery | Released in beta7. Preserve behavior during #22, do not reimplement it as v0.8.0 functionality. Narrow command-free hardware follow-up remains useful before v0.7.0 GA. |
| #35 reported extended-remote defects | Reporter confirmed clean beta7 integration/firmware, retained profile/automation operation and Celsius control. Do not reopen delivered fixes because the issue remains open. |
| [#20 image/fallback-AP warnings](https://github.com/jeffgregx2/HACS-Proflame2/issues/20) | Remains assigned before v0.7.0 GA. Do not silently move it into v0.8.0. ESPHome image removal was reported for 2027.1. |
| #28 cache-error panic | Mitigation delivered and successful operation reported. Reopen investigation only on current reproducible evidence; urgent supported-version recurrence belongs in v0.7.x. |
| Version logging, repeat selection and Node.js artifact action | Already shipped; no additional implementation merely to include them in this inventory. |
| [#23 GTMF compatibility](https://github.com/jeffgregx2/HACS-Proflame2/issues/23), thermostat control, shared controllers, new RF hardware or repeater support | Separate capabilities, requiring explicit scope and evidence; not part of this plan. |

## References

- [Public HA/ESPHome transport design](issue_17_ha_esphome_public_transport_design.md)
- [Integration maintenance backlog](integration_maintenance_backlog.md)
- [Firmware cleanup backlog](firmware_cleanup_backlog.md)
- [Shutdown design and acceptance](controller_shutdown_design.md)
- [Learning design](issue_32_learning_mode_design.md)
- [ESPHome user-defined actions and responses](https://esphome.io/components/api/#user-defined-actions)
- [HA integration action responses](https://developers.home-assistant.io/docs/dev_101_services/#response-data)

Local scope decisions and private evidence are maintained separately. This public
plan contains no raw captures or installation-specific identities. Its creation
does not authorize implementation, firmware flashing, RF transmissions, commits,
pushes, service restarts or changes to published releases.
