# Issue #34: bounded offline logging and display synchronization recovery

Status: implemented locally; software validation recorded below. Hardware
acceptance and release pending.
Target: v0.7.0, before GA. Created 2026-11-06.

Tracking issue: [#34](https://github.com/jeffgregx2/HACS-Proflame2/issues/34).
This document covers its offline-controller logging and reconnection portion.
Device-registry deprecations remain separate v0.8.0 work. The public-API transport
migration in [#22](https://github.com/jeffgregx2/HACS-Proflame2/issues/22) is also
separate.

## Problem and inspected behavior

The reporter describes repeated tracebacks while a battery-powered LilyGO is
offline. The supplied patch targets v0.6.0. Inspection before implementation
confirmed a remaining failure path regardless of improvements since then:

- The previous `services._async_display_sync_loop()` ran every 15 seconds. It logged
  `RuntimeError` at DEBUG but logged every other exception with a full traceback
  on each attempt. A fault-injection check of that function produced
  20 ERROR records and 20 tracebacks for 20 consecutive timeouts.
- Synchronization applies the RX policy before updating the display. Not all
  exceptions at that boundary are normalized into `RuntimeError`.
- Backend/transport `connected` records setup state; it does not establish that
  the linked ESPHome device is reachable or that cached runtime/action objects
  are still current.
- The receive path already checks current runtime identity and availability.
  The display path must recover independently when active listening is disabled.
- Display synchronization can return without updating the display during
  learning, pending control changes, missing known state, or signature
  deduplication. Such a return is not proof of successful resynchronization.

The reporter's patch is useful evidence, not an implementation to apply directly.
Its cached-runtime availability check can remain attached to an obsolete object
after HA reloads the linked ESPHome entry.

## Approved behavior and boundaries

1. Resolve the linked ESPHome entry and its current runtime using in-memory
   lookups at the existing 15-second interval. Do not add availability callback
   subscriptions or new network probes.
2. Suspend display/API synchronization attempts while the controller is known
   offline. Continue checking without an attempt limit, so it can recover later.
3. Reconnect and resume synchronization as soon as an eligible check finds the
   device available. Recovery must work with active listening enabled or disabled.
4. Log failure episodes rather than each attempt. Rate-limit repeated DEBUG
   summaries as well as bounding normal-level logs.
5. End an episode only after an actual successful display synchronization and a
   60-second healthy observation window. A failure during this window keeps the
   same episode and its logging suppression.
6. Force a display update after reconnection even when HA's intended state is
   unchanged. Preserve normal learning/control safety guards and leave that
   update pending until it really succeeds.

No new UI options, persisted configuration, firmware changes, RF transmissions,
protocol changes, device-registry migration, or redesign of TX confirmation.
Retain existing action names, payloads, legacy/extended behavior and supported
firmware compatibility. Keep the active-listener task independently owned and
retryable; do not make its recovery depend on display synchronization.

## Responsibility and code organization

| Layer | Responsibility | Expected code location |
| --- | --- | --- |
| ESPHome transport | Read live entry/runtime availability; identify stale bindings; retain action discovery and invocation mechanics. | `rf/esphome/transport.py` |
| ESPHome backend | Expose readiness without leaking runtime objects to services; reuse connection/configuration/policy setup against the current runtime. | `rf/esphome_api.py` |
| Display synchronization | Decide whether an update is safe and needed; distinguish applied, unchanged and deferred outcomes. | Existing helpers in `services.py` |
| Failure-episode policy | Own monotonic timing, suppression state, recovery candidacy and bounded summary decisions. | `display_sync.py` |
| Periodic loop | Check readiness, invoke existing synchronization, report policy decisions, and retain task lifecycle. | `_async_display_sync_loop()` |

Use a small explicit state holder rather than interleaving unrelated flags and
logging branches throughout the loop. Do not introduce a general connection
manager or duplicate transport discovery/configuration logic. Episode state is
local to the owning display task and resets when that task is unloaded; it is
not stored on disk. Named timing constants make deterministic tests practical.

## Live readiness and rebinding

The transport's availability accessor must resolve
`hass.config_entries.async_get_entry(linked_entry_id)` on every invocation, then
inspect that entry's current `runtime_data` and `available` flag. A missing entry,
missing runtime or explicitly unavailable runtime is offline. Never use the
transport's cached `_runtime_data` to decide whether to keep skipping work.

This is a dictionary lookup and a few attribute reads. It does not fetch data
from the device or filesystem. Network work occurs only in the existing
connection/synchronization operations after availability permits an attempt.

Expose a backend-level availability accessor so `services.py` does not inspect
ESPHome runtime internals. For test transports lacking that signal, represent
availability as unknown and retain ordinary bounded attempts; unknown must not
be treated as proof of either offline or recovered.

Before an eligible synchronization attempt, the backend must ensure its binding
still belongs to the current available runtime. Invalidate stale connection
state and use the existing `connect()` path to rediscover actions, configure the
transport and apply the existing RX policy. Do not merely set `connected=True`.
This preparation must not depend on calling `receive()` and must work when HA
has no known fireplace state yet, even if the subsequent display update defers.

Keep binding identity/generation inside the backend/transport boundary. A runtime
replacement or an observed disconnect invalidates recovery candidacy and makes
display resynchronization pending. Even if the runtime object survives a normal
disconnect, the observed outage requires resynchronization on return.

Availability is advisory: the controller can disconnect after the check. Handle
the actual API failure through the same episode/retry path. Classify known API
connection failures and timeouts explicitly, preserving chained causes where
wrappers already exist. Do not classify every `RuntimeError` as an expected
disconnection: missing actions or programming errors need diagnostic evidence.

## Synchronization outcomes and pending work

Have the display helpers return an explicit internal outcome, with existing
callers free to ignore it:

| Outcome | Meaning | Recovery effect |
| --- | --- | --- |
| Applied | The display action completed successfully; update existing signature/time caches. | Can establish successful resynchronization. |
| Unchanged | No display action was needed because the signature matched. | Does not establish successful resynchronization by itself. |
| Deferred | Learning, staged/sending controls, or absent known state prevents an update. | Keep pending resynchronization and the episode; do not claim recovery. |
| Exception | Preparation, policy application or display synchronization failed. | Enter/continue the episode and invalidate recovery candidacy. |

An unavailable controller is handled before API invocation. When user control or
learning owns the controller, defer new background reconnect/resync operations
until safe; do not interrupt a transmission or change staged intent. Reading
availability remains safe during that deferral. Preserve the existing rule that
display synchronization must not replace pending desired state or cancel a
command's delayed retry through a changed display state.

After an outage, bypass signature deduplication for the next safe display update.
Clear the pending-update flag only after the actual display action completes.
If that update raises, the flag stays set. A successful connection or RX-policy
action alone is insufficient. No refresh/TX activity timestamp is advanced by
display recovery, and it must not replay a fireplace command.

## Failure episode and flapping policy

Use monotonic time. The display task has three conceptual states:

| State | Event | Result |
| --- | --- | --- |
| Healthy | Offline or synchronization failure | Open one episode; make resync pending; report the initial failure. |
| Interrupted | Still offline or another failure | Retain the episode; suppress duplicate normal-level logs; schedule bounded checks. |
| Interrupted | Available and a real display update succeeds | Restore operation immediately; begin a recovery candidate window. |
| Recovering | Further healthy checks on the same live binding | Continue observing; do not announce recovery yet. |
| Recovering | Offline, failure, or runtime replacement | Return to Interrupted within the same episode; clear healthy timing, keep log suppression. |
| Recovering | At least 60 healthy observed seconds and successful confirmation | Log recovery once; close episode and reset suppression. |
| Any | Intentional unload/shutdown/cancellation | Exit cleanly; no failure/recovery announcement for cancellation. |

Start the healthy window only after a real display update succeeds against the
current live binding. Keep checking every 15 seconds. Before closing the episode,
require an eligible successful synchronization on that same binding; force a
confirmation display update if signature deduplication would otherwise skip it.
That allows unchanged-state checks between the initial resync and confirmation
without treating a no-op as proof of recovery. Deferred work cannot close the
episode. A failure resets the healthy window, not the episode's reporting state.

The 60-second requirement delays only the recovery announcement and suppression
reset. It does not delay the first safe attempt to resume operation. Repeated
flaps within that window do not produce alternating warning/recovered messages.
The normal five-minute forced reconciliation remains in place after recovery.

### Logging contract

- Known offline/connection interruption: one WARNING when the episode starts,
  with config-entry context and a concise reason; no routine traceback.
- Unexpected failure: at most one ERROR with a traceback per episode. If an
  episode began with a known outage and then exposes an unexpected error, retain
  one traceback for that unexpected failure rather than hiding it entirely.
- Further failures, including changing exception messages: no repeated WARNING
  or ERROR records. Keep bounded counters and the most recent failure summary;
  do not retain an unbounded collection of exception objects/messages.
- Repeated DEBUG summaries: at most once per 60 seconds per display task,
  including during flapping. Include episode duration, attempt/failure counts
  and latest reason. Do not attach repeated tracebacks. State changes must not
  bypass this rate limit or reset its timestamp within an episode.
- Confirmed stable recovery: one INFO with useful episode duration/counts.

These bounds cover logs owned by this display-sync path. Preserve independent
active-listener and ESPHome integration logging; do not change their existing
retry ownership or suppress another integration's logs. Test their coexistence
so display recovery neither terminates the listener nor creates another one.

### Polling limits

An observed return can normally trigger an attempt on the next 15-second check,
subject to user work and existing API timeouts. This is not instantaneous event
notification. A disconnect/reconnect entirely between checks may go unobserved;
the 60-second window means healthy observations, not proof of uninterrupted
physical connectivity. Detected API failures still reset the window, and normal
periodic reconciliation remains the fallback for an unobserved brief restart.

## Implementation steps and completion gates

| Task | Work | Completion evidence |
| --- | --- | --- |
| T1 | Add regression cases against the current display loop before changing it. | Reproduce repeated timeout/unexpected-error tracebacks and stale-runtime recovery with active listening disabled. Use synthetic controller data and simulated time. |
| T2 | Add fresh in-memory availability/binding checks at the transport boundary and backend preparation through existing connection logic. | Missing/offline/replaced runtime cases work; returning live runtime can rebind even when the old cached object stays unavailable. Unknown test-transport availability remains retryable. No new subscriptions or probes. |
| T3 | Return explicit display outcomes and preserve pending forced resynchronization through guarded skips and failures. | Applied/unchanged/deferred are distinguishable; only a real successful display update changes display caches and satisfies resync. Existing callers and control/learning behavior remain compatible. |
| T4 | Implement the small episode policy and integrate it into the periodic loop. | One failure episode survives flapping; normal/DEBUG log counts are bounded; 60-second recovery confirmation uses monotonic time. No duplicate task, busy retry or premature recovery. |
| T5 | Exercise the full display/backend/transport path and its interaction with existing active listening and lifecycle handling. | Recovery with listening ON/OFF, genuine action calls against replacement runtime, correct RX policy, intentional cancellation, and no RF send calls. Test outage races after an initially successful availability check. |
| T6 | Run required checks and update documentation/tracker with actual results. | Focused regressions, full integration suite, Ruff, Black and whitespace checks pass. Record deployed version only after a release actually contains the change. |
| T7 | Perform maintainer hardware acceptance after installing the integration change. | Powered-off/returned LilyGO recovers without a HA restart or user command, unchanged display is restored, logging stays bounded, and repeated connection interruptions do not create repeated recovery announcements. Validate listening enabled and disabled. |

Keep tests with the existing HA/ESPHome runtime and service tests where practical;
put pure episode timing tests alongside the focused helper. Use a fake monotonic
clock and controllable waits rather than sleeping for actual outage durations.
Do not add firmware builds to the mandatory gate for this HA-only change unless
implementation unexpectedly touches firmware; that scope change needs review.

## Required test matrix

- Sustained offline state over many poll intervals: no action calls while known
  offline, one initial warning, bounded summaries, task remains alive.
- Repeated timeouts and unexpected exceptions while availability still says true:
  bounded ERROR/traceback count; no accidental retry termination.
- Known outage followed by a different unexpected failure: one unexpected
  traceback remains available, but subsequent failures do not flood the log.
- Repeated offline/online flaps, with successes less than 60 seconds apart:
  operation resumes when possible; one episode persists; no recovery INFO yet.
- After the final flap, healthy observations for at least 60 seconds plus a real
  confirmation update: exactly one recovery INFO. A later genuine outage opens a
  new episode. Test the exact 60-second boundary and monotonic clock behavior.
- Unchanged display state after reboot: forced action runs; cached signature does
  not suppress restoration. Failure of that action retains pending work.
- Learning, staged controls, active sending and unknown fireplace state: no-op
  outcomes do not end the episode or claim recovery; a later safe update works.
- Entry missing, runtime absent, offline cached object, changed runtime, and
  same-object reconnect: current binding and actions are used, with listening
  disabled as well as enabled. Old cached availability cannot block recovery.
- API failure after a live readiness check: normal failure handling applies.
- Shutdown, removal and cancellation during outage or recovery: stop promptly,
  release task ownership, and leave no orphan task or subscription.
- Existing startup retries, observed-state display updates, refresh scheduling,
  profiles, legacy/extended control, and TX confirmation remain intact. Assert
  zero RF sends from periodic recovery and no duplicate active listeners.

## Implementation status

T1–T6 are implemented locally. T7 is partially complete: the maintainer completed
H0/H1. The 2026-11-09 risk review reduced remaining hardware acceptance to R1
(startup offline, then automatic display and listening recovery) and R2 (actual
ESPHome runtime replacement with listening ON) in the
[standalone hardware protocol](issue_34_hardware_test_protocol.md). It contains
setup, power-control instructions, R1/R2, pass criteria and results reporting;
the superseded matrix and reported results are preserved privately. Existing
H1 listening-OFF evidence and automated OFF/ON, flapping, logging and episode-reset
checks remove the need for repeated long outages and manual timing-policy tests.
The maintainer's 2026-11-09 R1/R2 report confirms startup retry exhaustion followed
by automatic display recovery, automatic listener reconnection after runtime
replacement, and two accepted extended RF states with matching HA/display updates.
T7 remains partial: R1's RF-state checks are treated as not performed following
tester clarification, and R2 included a
direct LilyGO HA Power ON before its receive checks. A short command-free R2
reload/receive check remains; another long outage is not required. Supplied
excerpts show one display warning/recovery per episode, minute-spaced outage
summaries and no ERROR tracebacks. Exact installed source revision is unrecorded.
There is no deployed release for this fix yet; no firmware change is required.

Implemented boundaries:

- `DisplaySyncRecovery` owns the per-task failure episode, bounded counters,
  60-second healthy candidate window and 60-second DEBUG summary rate limit.
  `DisplaySyncOutcome` distinguishes APPLIED, UNCHANGED and DEFERRED results.
- The transport's live `device_available` and `connection_is_current` accessors
  resolve the current linked entry each time. The backend's `prepare_display_sync`
  uses those checks and the existing connection path; its connection generation
  also lets the display loop notice rebinding performed by another recovery path.
- Periodic synchronization uses the existing controller arbiter and yields to
  learning, staged/sending intent and an actual uncertain TX request. Ordinary
  disconnect readiness fencing without an uncertain request does not block
  display recovery. This protects user
  work while background preparation is added. The independent receive task is
  neither replaced nor made dependent on display recovery.
- Display-update caches change only after the action completes. A failed forced
  update stays pending. Both initial resynchronization and stable recovery
  confirmation bypass unchanged-signature deduplication when required.
- Known disconnects/timeouts, including explicitly chained backend wrappers and
  native API exception subclasses, get bounded warning-level reporting.
  An unrelated programming error with an implicit earlier network context is
  still unexpected and retains its one traceback.
- The existing bounded startup retry path recognizes the new typed unavailable
  exception as well as the older not-ready message. Its retry timing is unchanged.

### Software validation — 2026-11-06

The original periodic loop failed the new timeout regression by logging twenty
ERROR tracebacks. The implemented loop bounds timeout warnings and unexpected
error tracebacks across repeated attempts. Dedicated regressions cover all the
failure/recovery, flapping, real transport/backend action, cache, user-intent and
task-lifecycle cases listed above, including the existing ordinary-disconnect
send fence and both old/typed startup retry errors.

- Dedicated display recovery regressions: **39 passed**.
- Full integration suite: **2,143 passed, 34 skipped, one deselected**. Skips
  comprise 27 real ESPHome configuration checks without an installed test CLI,
  four installed-helper interoperability checks and three upstream rtl_433
  detector checks requiring their optional external environments. No HA-only
  recovery regression was skipped.
- Repository-wide Ruff and Black checks, document-link checks and
  `git diff --check`: passed.

These are simulated HA/API tests, not physical controller acceptance. Firmware
builds and RF transmission were not part of this HA-only implementation.

After the firmware shutdown feature was separated into its v0.8.0 checkout,
the v0.7.0 `dev` tree was revalidated with the real ESPHome configuration CLI:
**2,170 passed, seven skipped, one deselected**. The seven remaining skips are
the four installed-helper interoperability and three external upstream detector
checks listed above. The real ESPHome configuration checks passed. `git diff`
confirms there are no firmware changes on `dev`.

### Maintainer hardware acceptance procedure

Use the [hardware test protocol](issue_34_hardware_test_protocol.md) for timed
fault injection, per-case pass/fail criteria and the result worksheet. It covers
power loss, flapping, startup while offline and linked ESPHome entry reloads,
with active listening enabled and disabled. No firmware update is required.
The following is a short overview.

The controller shutdown gesture and its firmware/HA notification work are now
explicitly deferred to v0.8.0; see the [separate design](controller_shutdown_design.md).
They are not prerequisites or implementation changes for this HA-only fix.
Use beta6 firmware for v0.7.0 acceptance. The protocol includes a repeatable
linked-ESPHome-entry disable/enable test when battery power prevents a physical
power interruption; it records that fault separately from cold-boot coverage.

1. Update the HA integration and restart/reload it. Existing beta6 LilyGO firmware
   is sufficient. Start with a known HA state and a synchronized controller
   display; record the selected settings.
2. With active listening disabled, fully switch the LilyGO off. Disconnecting
   USB alone may leave it running on battery. Wait through several 15-second
   checks and verify that the display-sync path logs one interruption warning,
   without repeated normal-level errors. If DEBUG is enabled, its repeated
   display-sync summaries should be at most once per minute.
3. Restore controller power without sending a fireplace command or restarting HA.
   Verify that the current HA display state returns after the API reconnects and
   an eligible periodic check runs. No RF fireplace command should be generated
   by this display recovery.
4. After at least 60 seconds of healthy observed operation following successful
   resynchronization, verify one display-sync recovery INFO record.
5. Repeat with active listening enabled. Verify native-remote updates still work
   after recovery. Normal ESPHome and active-listener messages are independent;
   assess the display-sync message bounds separately.
6. Repeat observable offline/online transitions with online periods shorter than
   60 seconds. Verify useful operation resumes when available, while the same
   display failure episode remains open. Once the connection stays healthy for
   the required window and confirmation succeeds, expect one recovery message.
   Brief outages entirely between polling checks need not be observed.

## Follow-up: Ready after successful transmission

The 2026-11-10 follow-up identified a separate status defect: after a successful
send with active listening enabled, the integration entered `confirming` without
a finite confirmation task. A quiet remote could leave that status indefinitely.

Successful transmission here means the backend returned success. For LilyGO,
that requires HA to receive request-matched controller telemetry confirming that
the local RF transmission routine completed successfully. It does not establish
that the fireplace received or acted on the command. Queue admission, timeout,
or missing controller confirmation is not successful completion.

The narrow v0.7.0 correction sets `ready` after successful completion when active
listening is enabled and no newer request is pending, then continues listening.
It does not promote state confidence from `requested` to `observed`; a valid
received remote packet still supplies observation evidence. Pending requests,
failed/unconfirmed sends, retries, the listening-disabled confirmation policy,
and firmware/RF timing remain unchanged.

Regression coverage exercises both entity and service commands: with no remote
packet, successful completion leaves Ready/Requested with a live listener and
no confirmation task. A later valid remote packet updates the state to Observed
without another transmission. Both regressions reproduced the original defect
and pass after the fix. The full default suite passed: **2,220 passed, 34 skipped,
one deselected**; Python lint, formatting and source preflight passed. The fix is
local on dev, uncommitted and unreleased. No additional hardware test is required.

## References

- [Existing active-listener recovery design](startup_api_recovery_design.md)
- [Public transport migration design](issue_17_ha_esphome_public_transport_design.md)
- [Code quality guidelines](code_quality_guidelines.md)
- [v0.8.0 maintenance plan](v0_8_0_maintenance_plan.md)
