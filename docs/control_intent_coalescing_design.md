# Editable Controls During Transmission

Status: design only. Runtime implementation has not started.

## Goal and Agreed Policy

Allow Home Assistant controls to accept new settings while a previous command
is transmitting. Keep one latest full desired state instead of a history of
slider movements. Apply this consistently to ESPHome, YardStick and fake/test
backends, without changing RF encoding, integrity calculations or timings.

- One immutable command may be in flight; one replaceable full-state intent may
  be pending for a fireplace. No FIFO of obsolete commands is retained.
- Further edits replace or merge into that pending full state and remain visible
  in the existing HA controls.
- A pending UI intent is eligible when both its normal debounce has expired and
  the current controller operation has completed. These waits overlap.
- Local intent takes precedence over RX observations while local work remains.
- Successful local TX establishes requested/optimistic state, not proof that the
  fireplace received the command. Never wait for a fireplace acknowledgement.
- A reported TX failure, timeout or controller disconnect discards pending work,
  restores the last accepted state, publishes HA updates and records an error.
- Reconnection does not replay discarded work. A new explicit request starts a
  new sequence.

Pending state is semantic state, not an already encoded packet. Build the RF
packet only when its intent is selected for transmission, using existing code.

## Current Code and Gaps

| Area | Current behavior / required change |
| --- | --- |
| `services.py: async_stage_control_change` | Stages full state and starts a debounce task, but lacks a single owner that waits for an existing send. Its comparison with current state is unsafe when a different state is already in flight. |
| `services.py: _async_debounce_send_task` | Launches a send after debounce; it must instead wake one dispatcher. |
| `services.py: _async_begin_send_execution` | Cancels the current debounce task. An older send must not cancel newer intent. |
| `services.py: _async_record_successful_send` and failure handlers | Clear shared `desired_state` unconditionally. Results must commit only the matching in-flight snapshot. |
| `services.py: async_apply_observed_packet` | Marks the entry ready and applies RX/display state independently of local work. It needs intent-aware precedence. |
| `runtime.py` | Stores accepted state in `last_packet`, plus one ambiguous `desired_state`. Needs distinct pending/in-flight ownership and cancellation fencing. |
| `number.py`, `switch.py` | Already display desired state ahead of accepted state. Preserve that behavior using a common effective-state accessor. |
| ESPHome transport | Matches TX completion telemetry to a request. Preserve this correlation; enqueue/API dispatch alone is not completion. |
| LilyGO retry scheduler | A new TX cancels an obsolete scheduled retry; a running retry can defer one normal TX. New HA pending intent needs to invalidate an obsolete scheduled retry before debounce finishes. |

The existing no-flicker control test blocks a send but does not submit another
edit after that send has started. New tests must reproduce that overlap.

## State Ownership

Introduce a focused `control_intent.py` module for intent state and controller
arbitration. Keep HA service adapters and existing packet construction in
`services.py`; do not redesign the protocol or backend hierarchy.

Each runtime entry owns:

| State | Meaning |
| --- | --- |
| Accepted baseline | Existing `last_packet`, confidence and accepted profile metadata: the best integration-known state. Used for persistence and rollback. |
| Last observed packet | Latest valid native-remote observation, retained separately for diagnostics and provenance. |
| Pending intent | Immutable full `FireplaceState`, revision, lifecycle epoch, source, monotonic eligibility deadline and optional saved-profile metadata. Replaced atomically by newer intent. |
| In-flight intent | Immutable selected snapshot, revision, lifecycle epoch and backend request correlation. Never modified by subsequent edits. |
| Dispatcher task / wake event | One owner per entry, woken by edits, completion, cancellation or deadline. No task per slider change. |
| Failure / lifecycle epoch | Fences callbacks and results from failed sequences, unloaded entries and previous connections. |

Use monotonic deadlines. Increment the intent revision for each new accepted
intent, even if the values later return to an earlier state. Publish state
transitions before awaiting persistence, controller acquisition or display I/O.
Do not hold a state-transition mutex over any backend or HA await.

`runtime_current_state()` continues to mean accepted baseline. Add one accessor
for effective control state with this precedence:

```text
pending full state > in-flight full state > accepted baseline
```

Keep `desired_state` as a derived compatibility view while existing consumers
are migrated. It must no longer be independently assigned or cleared by send
completion, RX or exception handlers. Pending/in-flight work is never persisted
or restored after HA restart.

### Controller Ownership

Share a TX arbiter by physical controller identity, not backend object identity.
Multiple fireplaces linked to one ESPHome entry use the same arbiter; separate
controllers can transmit concurrently. Existing YardStick IDs are scoped to a
fireplace entry, so hardware identity/exclusive ownership must be audited rather
than assuming the current `controller_id` always identifies a physical radio.

There is one pending slot per fireplace, not a historical command queue. A shared
controller can therefore have multiple fireplaces waiting, each represented once.
An edit for one fireplace must not silently erase another fireplace's intent.
Use fair arbitration between eligible entries and re-read the latest intent
after acquiring ownership; never capture an obsolete packet while waiting.
Existing backend restrictions on shared hardware and RX routing still apply;
this feature does not introduce multi-profile RX support or bypass exclusive
device ownership.

Ownership covers RX suspension, TX and the required backend RX restoration.
Passive post-TX observation and the two-second optional repeat timer do not hold
the arbiter. Learning and other exclusive hardware operations must participate
in controller ownership or reject conflicts explicitly.

## Dispatch Rules

### Accept an Edit

1. Validate without altering current work if validation fails.
2. Build the complete desired state from pending, else in-flight, else accepted
   state, using the existing feature and off-state rules.
3. Replace the pending intent atomically. Preserve every attribute, not just the
   latest edited field. Reset its UI deadline to the latest edit plus the existing
   debounce interval (currently 1.5 seconds).
4. Publish the new effective control values and pending status. Wake the dispatcher
   and invalidate any obsolete scheduled LilyGO repeat where supported.

Never cancel an in-flight TX to accommodate a new edit. Deduplicate against the
state expected after the in-flight command, not blindly against accepted state.
Example: accepted OFF, ON in flight, user requests OFF. OFF must remain pending
because the ON command can still complete; it is not a no-op.

### Select and Send

Wait until the intent deadline and controller readiness both permit dispatch:

```text
send_start >= max(latest_edit_time + debounce, controller_ready_time)
```

Recheck epoch, connectivity, deadline and revision after every await. Move the
latest eligible pending snapshot to in-flight synchronously before any backend
await. Only the dispatcher calls the actual send path. Existing request IDs and
local-completion checks remain authoritative.

When a newer edit arrives while waiting for the controller, re-evaluate its
deadline and full state. Repeated identical pending values may share the same
outcome rather than generating another RF command. An idle explicit resend must
retain its existing meaning; do not turn every explicit request into a no-op.

### Complete Successfully

Check the entry epoch and exact in-flight identity before applying any result.
Commit only that snapshot to the accepted baseline with requested confidence,
persist it and clear only that in-flight slot. Preserve any newer pending state
and its deadline. Publish effective values without a temporary jump back to the
older completed values. Dispatch the next eligible intent or return to idle.

RX restoration is attempted whenever supported/configured. A passive confirmation
window must not delay the next eligible intent. A post-TX RX/display housekeeping
failure after known local TX success is reported separately; it must not erase
that successful baseline or mislabel the RF transmission as failed.

## Incoming RX and Confirmation

Validate and route RX normally, including remote identity and integrity checks.
Do not weaken packet acceptance for this feature.

- Idle: apply accepted native observations and update controls normally.
- Pending/in-flight: record valid observations and update the accepted baseline
  underneath the local-intent overlay, but do not alter pending/in-flight state,
  clear send errors indiscriminately, set the entry ready, or push RX values over
  the effective UI/display state.
- A subsequent successful local TX commits its full snapshot as the new baseline
  and therefore wins over earlier RX observations.
- After draining local work, normal active listening resumes. Do not replay
  buffered older observations as if newly received. A genuinely later native
  transmission can change state normally; no indefinite local-priority window
  is introduced.

Thus, if local TX fails after a valid native observation, rollback can use that
newer accepted observation rather than an older estimate. With no intervening
observation, A-success/B-failure rolls back to A, not the pre-A state.

The existing post-TX confirmation task must be epoch/revision-aware, must not
mark a newer intent ready and must not stop RX owned by a subsequent operation.
An observed remote packet remains an observation, not a fireplace acknowledgement.

## Failure and Reconnection

For a matching in-flight failure, timeout, or detected controller disconnect:

1. Fence the sequence by advancing its epoch; invalidate timers and result handlers.
2. Discard its pending intent and clear in-flight UI intent. Resolve waiting
   service calls with the appropriate failure.
3. Restore effective controls to the latest accepted baseline and its confidence
   and profile metadata. Publish normal dispatcher/entity updates and failure
   status. No compensating RF packet is sent.
4. Persist only accepted state; do not persist/replay discarded commands.
5. Quarantine an operation whose physical completion is unknown until the adapter
   establishes readiness or reconnects. Do not immediately send another command
   into a timed-out operation that may still be running.

A controller-wide disconnect clears waiting intents for all affected entries.
A definitive per-request validation/TX failure clears that entry's sequence,
not unrelated commands for other fireplaces unless radio availability is unknown.
Late telemetry from the old epoch cannot restore discarded UI intent or alter
a newer successful state. A new explicit request may start a new sequence once
the controller is ready; connection recovery alone does not start one.

Add a narrow backend connection-state notification hook where supported. Current
ESPHome `connected` bookkeeping is not sufficient evidence of a live API socket.
Do not treat the phone's frontend disconnect as a radio disconnect. Do not expand
private HA-runtime dependencies while adding lifecycle observation; keep that
work behind the existing transport adapter and the public-API migration plan.
For adapters lacking disconnect notification, send errors/timeouts remain the
failure detector; immediate disconnect detection cannot be promised.

HA controls remain optimistic while pending, as they are today. Rollback publishes
real old/new entity changes when values differ and updates status/error attributes.
It cannot undo automations already triggered by an optimistic value, and unchanged
numeric values need not generate a numeric-state transition. Automations needing
send outcome must also use operational status/error information. Do not synthesize
fake value changes to force events.

## Service, Display and Compatibility Boundaries

- Keep entity IDs, service schemas, standard HA controls and normal debounce.
- Route UI, `set_state`, saved profiles and automation-originated commands through
  the same dispatcher. Preserve existing service validation and full-state semantics.
- UI entity actions acknowledge staging promptly. Explicit integration services
  retain their completion/error semantics; do not report staging as TX success.
  A pending explicit request replaced by a different intent returns a clear
  superseded result/error, not false success. Do not retain its obsolete packet.
- Use one completion future per intent; identical callers may await it. Superseded
  futures terminate promptly rather than forming an unbounded waiter history.
- Evaluate retained off-state edits only when no in-flight transition changes
  whether the fireplace is off. Preserve existing no-TX retention and flame rules.
- Profile metadata follows the snapshot actually committed. Edits superseding a
  profile must not cause an older result to label the newer state as that profile.
- Make display synchronization intent-aware. Older RX, periodic sync and TX
  completion must not overwrite newer desired values or cancel newer retry state.
  Reuse current display/status fields; no new display screen is needed.
- Backend success, failure, retry and RX restoration remain controller-specific;
  semantic intent ownership and rollback policy are backend-independent.
- Unload, HA shutdown and profile/backend replacement invalidate epochs, remove
  arbiter waiters and cancel only owned tasks. No pending work survives reload.

### LilyGO Delayed Retransmission

A new pending local intent should invalidate an obsolete scheduled repeat even
when its debounce has not expired. The current display-state update cancels a
repeat on state changes, but display equality is not a sufficient request token.

Design an optional, capability-detected native action to invalidate the named
original request's scheduled repeat. Fence it by request identity; an old cancel
must not cancel a newer request's repeat. It must also suppress scheduling a repeat
when the named original TX is still running and completes afterward. It must not
abort an RF burst already underway. A running repeat completes, then a new normal
TX proceeds using the existing deferred-TX mechanism.

Older firmware retains the existing new-TX cancellation behavior; do not call an
absent action, require simultaneous HA/firmware upgrades, or pretend that immediate
pending-intent cancellation is guaranteed there. Document that compatibility limit.
The primary command's success is not reversed by failure of its best-effort repeat.
No RF timing or repeat interval change is part of this design.

## Test and Validation Plan

Use fake monotonic time and explicit start/finish barriers, not arbitrary long
sleeps. Assert actual packets, maximum concurrent sends, complete HA state events,
accepted baseline, pending state, metadata and task cleanup.

| Required case | Expected result |
| --- | --- |
| Edits during debounce | One full-state packet containing the final combined values. |
| New edits after A starts | A stays immutable; only one latest B remains; A completion never clears B or flickers controls. |
| Deadline before/after A completes | B starts at the later eligibility condition, without another full debounce after completion. |
| Many edits / identical edits | B is replaced, not accumulated; latest attributes survive together. |
| Return to accepted value while A differs | Corrective B is sent after A; it is not dropped as a no-op. |
| RX before debounce, during A, between A and B | Local controls and full pending snapshot stay unchanged; observations remain available as rollback baseline. |
| RX after the local sequence drains | Normal active listening updates state. Old buffered observations do not replay. |
| A fails while B is pending | Discard A/B intent, revert to accepted baseline, publish failure and HA rollback events; no automatic B send. |
| A succeeds, B fails | Revert to A unless a newer accepted native observation exists. |
| Disconnect while pending, in flight or acquiring controller | Clear affected intents; no reconnect replay or concurrent send after uncertain timeout. |
| Late success/failure/confirmation from an old epoch | Cannot mutate a newer sequence, restore discarded work or stop its RX. |
| New edit during persistence/display await | Does not get cleared by an older completion. |
| Shared controller / separate controllers | One physical TX at a time per controller, fair latest-intent dispatch; independent radios proceed concurrently. |
| UI versus service/profile calls | Preserve staging versus completion semantics; superseded callers are not falsely successful. |
| Off-state retention, power reversal and feature combinations | Preserve established power/flame/light/fan/front/AUX/CPI behavior. |
| Saved profile followed by edits/failure | Correct committed profile metadata or rollback; no stale profile marker. |
| Unload, shutdown and backend replacement | No orphan dispatcher, timer, pending future or arbiter ownership. |
| Retry cancellation before original completion / during repeat | No obsolete scheduled retry or interrupted burst; old cancel cannot affect a newer retry. |
| Older firmware / TX-only backend | No absent-action calls or waiting for unsupported RX/acknowledgement. |

Extend `test_controls.py`, `test_services.py`, runtime/ESPHome transport tests and
the firmware retry host harness. Add focused pure state-machine/arbiter tests for
the new module. Keep all legacy/extended capture and encoding regressions passing.
Measure branch coverage of the intent transitions, failure fencing and deadline
logic; every transition above needs a named deterministic test, not just a total
coverage percentage. Reuse the repository's automatic temporary-workspace cleanup.

## Implementation Tasks and Release Gates

- [ ] Add failing regressions for editing during an active send and for the
      return-to-baseline/power-reversal case.
- [ ] Implement immutable intent snapshots, revisions/epochs, effective-state
      access and a single event-driven dispatcher.
- [ ] Add physical-controller arbitration and audit YardStick identity/ownership.
- [ ] Route all sending entrypoints through it; preserve service-call semantics.
- [ ] Fence commit, rollback, RX, confirmation, persistence and display updates.
- [ ] Add disconnect handling, unknown-completion quarantine and lifecycle cleanup.
- [ ] Implement/test optional request-scoped retry invalidation and older-firmware
      fallback without changing RF waveforms or timing.
- [ ] Complete the deterministic matrix, full suite, lint/format and coverage review.
- [ ] Validate ESPHome configurations and both tracing-enabled/disabled firmware
      compile paths; include old-firmware interoperability tests.
- [ ] Hardware-test rapid edits slower and faster than TX completion, pending
      power OFF, native-remote overlap, deliberate disconnect and reconnection.
      Test legacy locally and request extended-remote confirmation separately.
- [ ] Save scrubbed logs/test evidence and document the outcome semantics in the
      user-facing control guidance and release notes before release.

Log intent accepted/replaced/dispatched/completed/failed/rolled-back transitions
with non-identifying correlation tokens. Use debug for routine transitions and
clear failure reporting for actual errors; do not restore per-slider INFO spam
or include identifying paths, network details or raw remote IDs in public evidence.
