# Issue #36: publish backend connection transitions

Status: implemented; automated and maintainer hardware validation passed.
Target release v0.7.1 is not yet published.
Created 2026-11-12. Owner-assigned target: **v0.7.1**, on `dev` from the
v0.7.0 GA baseline (`ad047a5`). v0.8.0 shutdown work is excluded.

## Problem and evidence

[Issue #36](https://github.com/jeffgregx2/HACS-Proflame2/issues/36) describes a
primary sensor whose `operational_status` attribute remains `unavailable` after
LilyGO and active listening recover. Runtime diagnostics show `ready` and a
connected backend. Manually refreshing that entity corrects the attribute without
RF activity. This concerns an attribute, not physical-fireplace confirmation.

The sensor is nonpolling and uses the existing runtime dispatcher to publish.
Its status derives from both runtime state and `backend.connected`. ESPHome
connection/recovery can change that flag without dispatching an entity update.
Receiving a packet or performing another operation may subsequently hide the
problem by triggering an unrelated update. The relevant sensor and service code
matches the released v0.7.0 code.

The fix must notify HA when the connection state changes. It must not force
runtime status to Ready, change fireplace state/confidence, or transmit RF.

## Reproduction test

`tests/test_issue36_readiness_publication.py` exercises real HA entity platforms
and the production ESPHome backend/transport against a synthetic native API:

1. Start the integration with a linked ESPHome runtime that is unavailable.
2. Verify the published primary sensor attribute is `unavailable`.
3. Restore native API availability without sending or receiving an RF packet.
4. Exercise either active-listener recovery or the existing display-sync recovery
   entry point with active listening disabled. The latter avoids waiting the
   production scheduler interval; it is not a new product polling mechanism.
5. Wait briefly for any connection-triggered publication, then capture HA's
   published attribute before manually refreshing it.
6. Exercise the reported `homeassistant.update_entity` workaround and verify it
   shows Ready with unchanged RF counters, state, and confidence.
7. Assert the captured pre-refresh status was already Ready. This must fail on
   affected code; the workaround cannot make the regression pass.

The regression intentionally has no received frame: a frame would notify HA and
mask the missing publication. It checks HA's stored State object rather than
only reading live runtime properties. Test-side bounded waits are synchronization
for asynchronous operations, not proposed product polling.

Before-fix verification: **both new cases failed exactly at the pre-refresh status
assertion (`unavailable` versus `ready`)**; all earlier assertions, including the
manual refresh and zero-RF checks, pass. Six existing real-transport recovery
tests pass. The final focused run completed in **1.02 seconds** after removing
unrelated startup retry waits. This reproduces the defect on the current pinned
HA test stack without requiring the reporter's physical controller or firmware.
Python lint/formatting, whitespace and source preflight pass. These tests were left as normal regressions, without expected-failure markers;
the implementation must make them pass before the fix can be accepted.

## Agreed design

Use **connection-change callbacks plus a 100 ms one-shot notification timer**.
Backend events are the trigger; the timer coalesces publication and separates
entity writes from the connection setter. It never checks connection state on a
recurring schedule.

### Why a timer rather than publishing directly on every event

Connection events are still essential: they initiate the work. The choice is
**event-triggered deferred publication instead of immediate publication for every
event**, not a timer that discovers changes instead of events.

A direct publish from each connection event can expose short intermediate states
while asynchronous connection setup or recovery is progressing. It also puts
entity-publication work on the connection-change execution path. A short one-shot
lets that path complete, groups nearby changes, and reads the latest HA-held
state when publication actually occurs. Keeping the RF callback notification-only
also prevents HA entity work from becoming a backend responsibility.

The tradeoff is a deliberate 100 ms publication delay and a timer handle that
must be cleaned up correctly. It does not delay reconnection, listening or
commands. No timer runs when there has been no connection transition, and a
firing does not reschedule itself.

A next-event-loop publication would also avoid synchronous entity writes. The
short one-shot is selected because it can combine related transitions that span
several event-loop turns, including rapid disconnect/reconnect sequences.

### Exactly one pending timer per fireplace

Maintain one pending timer handle on each runtime entry. The first transition
sets its deadline. Further transitions while it is pending leave that deadline
unchanged; they do not create another timer, cancel it or restart its countdown.
This is bounded coalescing, not reset-on-every-transition debounce.

Resetting the deadline on every flap could postpone publication indefinitely.
The agreed fixed deadline instead provides a bounded opportunity to publish the
current state even while flapping continues.

| Time | Transition/action | Timer/publication |
| --- | --- | --- |
| 0 ms | Connection becomes unavailable | Schedule one publication at 100 ms. |
| 40 ms | Connection becomes connected | Keep the same 100 ms deadline. |
| 80 ms | Connection becomes unavailable | Keep the same 100 ms deadline. |
| 100 ms | Timer fires | Clear its handle and publish current derived state: unavailable. |
| 130 ms | Connection becomes connected | Schedule the next publication at 230 ms. |
| 230 ms | Timer fires | Publish current derived state; Ready only if the other runtime conditions also permit it. |

Each actual connection transition emits a callback. Coalescing applies to the
entity publications, so it does not promise a separate HA state-history record
for every brief intermediate state.

### RF/backend boundary

- Add an optional `add_connection_listener(callback)` subscription contract to
  `RFBackend`, returning an idempotent unsubscribe callable. The default is a
  no-op for backends that do not publish connection transitions.
- In `ESPHomeAPIBackend`, replace the public boolean storage with a `connected`
  property backed by `_connected`. Initialize the flag and listener collection
  before connection setup. Keep the meaning of the flag unchanged.
- A setter stores the new value and emits an internal callback only for an actual
  boolean transition. Reassigning False while already False emits nothing.
- All existing assignments, including those from service recovery, pass through
  the property. Do not sprinkle separate notification calls through each connect,
  listener, display-sync, close, and exception path.
- Callbacks are internal Python observers, not a new public HA event or service.
  The RF backend imports no Home Assistant code. Subscriber failure must not
  change a successful connection or transmission into a failure.
- Keep YardStick availability and FakeRFBackend's dataclass behavior unchanged.
  YardStick currently has no `connected` attribute; adding a shared property with
  a new default would change its current sensor semantics. The optional contract
  supports later adoption without requiring that unrelated refactor for #36.

This guarantees notifications for changes to the affected ESPHome backend flag.
It does not redefine physical connectivity or promise to detect a Wi-Fi outage
before existing availability/recovery paths observe it.

### HA runtime bridge

Register one listener per runtime entry before starting background connection
work. On a transition:

1. Ignore shutdown/unloaded entries.
2. If no notification is already scheduled, arm a one-shot for 100 ms later.
3. Leave an existing deadline unchanged when another transition arrives. Do not
   reset the timer repeatedly: persistent flapping must not postpone publication
   indefinitely.
4. At expiry, clear the handle, verify this is still the current runtime object,
   then call `async_notify_runtime_entry_updated`. Entities read current values.

Do not capture a status or fireplace snapshot when scheduling. A newer send,
learning operation, error or connection transition may occur before expiry;
publishing current derived state prevents an old callback restoring stale state.
The maximum added publication delay is the one-shot interval plus normal HA
scheduling. Connection setup, retries, listening and user commands do not wait
for that timer.

Use the existing dispatcher so all subscribed entities update consistently.
Do not call the primary sensor directly, switch it to polling, or invoke
`homeassistant.update_entity` as the product fix.

On unload or HA shutdown, remove the subscription and cancel the pending timer
before closing the backend. Register idempotent config-entry cleanup so a later
platform setup failure also detaches the observer and cancels its timer. The callback also checks shutdown/current-runtime identity, covering
late callbacks and reload replacement. Current ESPHome transition writers run
on HA's event loop; scheduling must remain on that loop.

### Current state and race handling

"Current state" means the latest connection and operational state held by the
integration in HA: `backend.connected`, learning/sending/pending/error state,
and the existing derived sensor attributes. It is not a new network query to
LilyGO and is not a measurement of the physical fireplace.

Connection updates and timer callbacks execute on HA's event loop. The timer
callback is synchronous and must not await or perform I/O. Another event-loop
callback cannot interrupt it halfway through clearing the handle and dispatching
the update. No new lock is required for these existing event-loop writers.

The event ordering has explicit outcomes:

- If a connection update runs before the timer callback, publication reads the
  newer value.
- If the timer callback runs first, it clears its handle. A subsequent connection
  update can therefore schedule a new timer, even if an entity update from the
  first dispatch is still queued. Those entities also read current values.
- If a send, learning operation or error changes operational state before an
  entity publishes, its derived attributes reflect that current state. No old
  status or fireplace snapshot is carried by the timer.
- If unload/reload happens first, unsubscribe/cancellation and the shutdown/
  runtime-identity guards prevent the old callback publishing for a replacement
  entry with the same configuration ID.

A publication can naturally become stale immediately after it is written. The
following transition schedules the next bounded update; it must not be lost.
Tests must exercise both orderings around expiry rather than assuming there is
never an event-ordering boundary to handle. This guarantee concerns existing
HA-event-loop writers; adding a future worker-thread writer would require an
explicit handoff to that loop.

### State and event semantics

- A connected backend may still have runtime status Pending, Sending, Failed or
  Learning. Republish the derived status; never replace it with Ready merely
  because the connection recovered.
- State confidence remains unchanged. Connectivity is not an RF observation.
- The setter emits each transition. Several transitions within 100 ms may yield
  one entity publication of the final state; intermediate flaps are not promised
  as separate HA state-history entries.
- No per-receive-loop notifications, no repeated updates for an unchanged flag,
  and no recurring timer are introduced.

## Alternatives and rationale

| Approach | Assessment |
| --- | --- |
| Poll the sensor or add a readiness-check loop | Adds ongoing work, delays publication until the next check, and masks missing state-change ownership. Reject. |
| Notify explicitly after listener recovery | Small patch, but misses first-attempt startup success, display-only recovery, close and other writers. Reject as the complete solution. |
| Publish synchronously inside the backend setter | Couples entity writes to connection execution and can expose partially updated state or subscriber failures to the backend. Avoid. |
| Queue one publication on the next event-loop turn | Valid alternative with less latency, but may publish multiple intermediate states across asynchronous setup steps. The owner agreed to a short one-shot for additional coalescing. |
| Transition event plus a fixed 100 ms one-shot | Recommended. Bounded latency, coalesced writes, explicit lifecycle cleanup, and no polling or delay to RF/control operations. |

## Risk assessment

**Overall implementation risk: low after automated validation.** Before validation,
the assessment was low to moderate because of callback/timer lifecycle risks. Operational
and RF impact should be low: no protocol, firmware, timing, retry, service,
configuration, fireplace-state or confidence changes are proposed. The remaining
risk is callback/timer lifecycle behavior, not the act of republishing a sensor.

| Risk | Mitigation and required evidence |
| --- | --- |
| Observer failure interferes with a connection/send | Keep callbacks notification-only, isolate callback errors from transport success, and test a failing observer. |
| Repeated failures create write storms | Emit only on value transitions; coalesce into one fixed-deadline one-shot; test repeated assignments and rapid flapping. |
| Old callback writes after unload/reload | Unsubscribe and cancel before close; guard shutdown and runtime identity; test both unload and replacement. |
| Recovery incorrectly reports Ready during another operation | Read derived state at expiry; test Pending/Failed/Learning and unchanged confidence. |
| Broader backend changes break YardStick or test mocks | Limit the observable property to ESPHome and use an optional base subscription hook; retain other backends' availability behavior. |
| Coalescing hides a brief intermediate disconnect | Explicitly permit final-state publication within the short window; do not delay the deadline under continuing flaps. |
| v0.8.0 functionality leaks into a maintenance release | If released as v0.7.1, apply test/fix from the GA maintenance baseline and carry forward; exclude shutdown firmware/guide changes. |

The automated lifecycle, event-ordering and real HA entity regressions below
passed. No additional RF hardware test is required for this HA-only publication
fix: it neither transmits nor changes radio configuration. Reporter acceptance
on the affected installation remains useful and is not claimed by these tests.

## Implementation and verification tasks

- [x] T1 Both reproduction cases fail specifically on pre-refresh published
  readiness; the manual workaround, no-RF and unchanged-state/confidence checks
  pass. Six existing transport-recovery tests also pass.
- [x] T2 Add the optional subscription contract and transition-aware ESPHome
  property; audit every writer and preserve existing flag semantics.
- [x] T3 Register the runtime bridge and fixed-deadline one-shot. Keep sensor
  code on the existing dispatcher and derived-property path.
- [x] T4 Add unsubscribe/timer cleanup and guards for failed setup, unload,
  shutdown and runtime replacement.
- [x] T5 Make both reproduction cases pass without relying on manual refresh.
  Add quiet initial-success, disconnect, close/failure, listening-disabled and
  repeated-recovery coverage; assert no RF/state/confidence changes.
- [x] T6 Test unchanged assignments, exactly one pending timer per entry,
  unchanged deadlines under flapping, both transition/expiry orderings, current-
  state publication, failing subscribers, multiple-entry isolation and teardown
  cleanup. Verify that expiry neither queries the device nor schedules itself.
- [x] T7 Run related integration tests, full default suite, lint/formatting and
  source preflight. Record exact results and the selected maintenance baseline.

No application fix, release assignment, commit, push, firmware flash, RF
transmission or GitHub reply is authorized by this design document itself.


## Implementation and validation — 2026-11-12

Implemented on `dev`, based on v0.7.0 GA (`ad047a5`), for the owner-assigned
v0.7.1 target. No v0.8.0 firmware or shutdown-guide changes are included.

- `rf/base.py` provides an optional no-op connection subscription contract.
- `rf/esphome_api.py` observes actual `connected` value transitions and isolates
  subscriber exceptions from transport operations. Existing flag writers retain
  their behavior and now notify consistently.
- `connection_updates.py` owns one fixed-deadline timer and publishes through the
  runtime's existing dispatcher. It holds no status snapshot and performs no I/O.
- `runtime.py` installs the publisher and guards runtime/backend identity. Failed
  setup, unload and HA shutdown detach it and cancel pending work before close.
- Three existing display tests now explicitly unload the runtimes they create;
  their functional assertions remain unchanged. This is necessary cleanup for
  tests that can now schedule an observable connection notification.

Results on the repository's pinned Home Assistant 2026.9.0 test stack:

| Check | Result |
| --- | --- |
| Related connection, HA publication, transport and display tests | 134 passed in 13.59 seconds |
| Full default suite | 2,240 passed; 34 skipped; 1 deselected in 65.52 seconds |
| Python lint / formatting | Ruff passed; Black checked all 220 Python files |
| Whitespace / source-private-data preflight | Passed |

The 20 new cases cover both original quiet-recovery failures, initial successful
setup, disconnect/reconnect and derived statuses, unchanged state/confidence and
zero RF, fixed deadlines under continuous flapping, expiry ordering, failing
observers, entry isolation, failed setup, shutdown and unload/reload. The 34
external-tool/opt-in skips are unchanged from the maintenance baseline; this run
is not a new physical-radio or reporter-installation certification. The normal
release workflow will stamp version metadata. Commit and push are authorized
separately by the owner; no GitHub support reply is part of this implementation.


## Maintainer hardware acceptance — 2026-11-12

The maintainer confirmed HA was restarted after restoring `dev`, then verified
command-free recovery using Test LilyGO. Existing v0.8.0-debug controller firmware
provided the manual shutdown mechanism; the #36 fix itself remains HA-only.

| Scenario | Reported result |
| --- | --- |
| Power off the controller and wait without changing settings | Published operational_status changed to unavailable after 1 minute 50 seconds, measured with a stopwatch. |
| Restore controller power after an outage, leaving fireplace controls untouched | Published operational_status returned to ready automatically in approximately one minute. |
| Restart HA while the controller is powered off, then restore controller power without operating fireplace controls | Published operational_status returned to ready automatically in roughly 10 seconds. Stopwatch timing was approximate because the transition occurred before the tester expected it. |

Both quiet-recovery acceptance checks passed. These elapsed times include device
boot, network/API reconnection and backend recovery; they do not independently
measure the 100 ms entity publication delay. The tester restarted ESPHome Builder
alongside HA; Builder is not in the runtime HA-to-controller connection path.

During the initial outage, both ESPHome availability and fireplace readiness
remained online for several minutes. A rejected ESPHome setting edit preceded
both becoming unavailable. It is not established whether that edit accelerated
disconnect detection or merely coincided with it. A quoted startup-sync warning
alone does not establish the disconnect chronology. This observation remains
unexplained and is not claimed fixed by #36. A subsequent timed power-off
repeat reached unavailable automatically after 1 minute 50 seconds without
changing any setting. This establishes that a setting edit is not required for
the offline transition. It does not isolate the delay between native API loss
detection, backend invalidation and HA publication. No extra detection or
polling change was made. Acceptance from the original issue reporter remains outstanding.


### Native API heartbeat timing

Upstream source inspection provides a plausible explanation for the measured
110-second offline transition: aioesphomeapi v46.25.0 defaults to a 20-second
ping interval and a response timeout of 4.5 times that interval (90 seconds).
Its timeout starts when the first unanswered ping is sent, allowing approximately
90–110 seconds after a silent connection loss. HA 2026.11.1 pins that library
version and creates its shared client without overriding keepalive. This matches
the reported timing; matching deployed native API logs are still needed to prove
that exact sequence on the test installation.

Sources: [client defaults](https://github.com/esphome/aioesphomeapi/blob/v46.25.0/aioesphomeapi/client_base.py),
[timeout scheduling](https://github.com/esphome/aioesphomeapi/blob/v46.25.0/aioesphomeapi/connection.py),
and [HA client creation](https://github.com/home-assistant/core/blob/2026.11.1/homeassistant/components/esphome/manager.py).

The native API firmware configuration exposes no documented keepalive interval;
reboot_timeout restarts a running device after loss of clients, rather than
controlling HA's dead-connection detection. No heartbeat, shared-client timeout
or firmware change is included in #36.
