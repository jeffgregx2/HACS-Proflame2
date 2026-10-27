# Editable Controls During Transmission

Status: implemented on `issue-control-intent-coalescing`; local automated
validation passed. Hardware acceptance and release gates remain open.

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

## Original Code and Gaps

The following table records the pre-change behavior that motivated this work.

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
| LilyGO retry scheduler | Preserve existing cancellation when a replacement TX is accepted; a running retry can defer one normal TX. HA-only pending intent must not cancel an accepted command's repeat. |

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

Keep `desired_state` as a compatibility mirror of the owned intent while existing
consumers are migrated. Staging, dispatch and fenced terminal transitions publish
that mirror; RX must not clear it. After local TX is committed, its in-flight
ownership may remain briefly for persistence/RX housekeeping, but that completed
snapshot no longer overlays accepted state. Pending/in-flight work is never
persisted or restored after HA restart.

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
4. Publish the new effective control values and pending status. Wake the dispatcher.
   Do not cancel controller retries for changes that exist only inside HA.

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

Preserve the controller's existing cancellation-on-acceptance behavior. A pending
HA intent is not evidence that its replacement has reached the controller. Keep
the prior accepted command's scheduled repeat until a replacement TX is accepted,
subject to existing cancellation policies such as native-remote activity or
disconnects. A rejected replacement must not cancel that repeat.

There is no separate early-cancellation action or HA-side cancellation task.
Display-only synchronization is deferred while HA has pending/in-flight intent:
existing firmware can cancel repeats when display values change, so publishing
HA-only settings would otherwise bypass this policy.

A running repeat completes before the new normal TX proceeds using the existing
deferred-TX mechanism. The primary command's success is not reversed by failure
of its best-effort repeat. No firmware update, RF timing change or repeat-interval
change is required for this control-intent feature.

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
| Pending replacement / replacement rejected / running repeat | HA staging preserves the accepted repeat; rejected firmware requests preserve it too; an active burst is not interrupted. |
| Existing firmware / TX-only backend | No new firmware action or waiting for unsupported RX/acknowledgement. |

Extend `test_controls.py`, `test_services.py`, runtime/ESPHome transport tests and
the firmware retry host harness. Add focused pure state-machine/arbiter tests for
the new module. Keep all legacy/extended capture and encoding regressions passing.
Measure branch coverage of the intent transitions, failure fencing and deadline
logic; every transition above needs a named deterministic test, not just a total
coverage percentage. Reuse the repository's automatic temporary-workspace cleanup.

## Implementation Tasks and Release Gates

- [x] Add failing regressions for editing during an active send and for the
      return-to-baseline/power-reversal case.
- [x] Implement immutable intent snapshots, revisions/epochs, effective-state
      access and a single event-driven dispatcher.
- [x] Add physical-controller arbitration and audit YardStick identity/ownership.
- [x] Route runtime sending entrypoints through it; preserve service-call semantics.
- [x] Fence commit, rollback, RX, confirmation, persistence and display updates.
- [x] Add disconnect handling, unknown-completion quarantine and lifecycle cleanup.
- [x] Preserve controller-managed retry cancellation on replacement acceptance;
      do not cancel repeats for HA-only intent or publish pending display values.
- [x] Run deterministic concurrency/rollback regressions, the full suite,
      lint/format and branch-coverage collection.
- [x] Validate ESPHome configurations and host-side C++ retry behavior, including
      existing-firmware compatibility without additional actions.
- [ ] Hardware-test rapid edits slower and faster than TX completion, pending
      power OFF, native-remote overlap, deliberate disconnect and reconnection.
      Test legacy locally and request extended-remote confirmation separately.
- [ ] Save scrubbed logs/test evidence and document the outcome semantics in the
      user-facing control guidance and release notes before release.

Log intent accepted/replaced/dispatched/completed/failed/rolled-back transitions
with non-identifying correlation tokens. Use debug for routine transitions and
clear failure reporting for actual errors; do not restore per-slider INFO spam
or include identifying paths, network details or raw remote IDs in public evidence.

## Implementation Notes

- `tests/test_control_intents.py` exercises rollback, local-intent/RX precedence,
  disconnect fencing, explicit-service supersession, shared/independent radios,
  debounce overlap, unload, persistence failure and edits during persistence.
- Guided learning reserves the same physical-controller identity and rejects
  starting while sends are pending or active. While learning owns that radio,
  controls cannot start TX. Its lease suspends normal runtime RX policy changes
  until learning closes. Existing standalone low-level learning
  helpers are not a second concurrent runtime control interface.
- ESPHome endpoint availability is observed through HA's public entity registry
  and state-change events. This does not replace the existing ESPHome transport
  adapter or resolve the separately tracked private-runtime-API technical debt.
- Timeout recovery requires matching terminal telemetry for the uncertain request
  on the physical radio. Another fireplace entry cannot bypass that quarantine.
  YardStick requires reload/reconnection when readiness cannot be established.
- This feature uses the existing firmware contract without a new action or
  firmware changes. Repeat cancellation remains a controller decision when the
  replacement TX is accepted, not when HA stages its intent.
- Routine state comparisons are against HA's accepted/optimistic baseline, not
  an acknowledgement or measurement from the fireplace.

## Local Validation

- Full suite: 1,137 passed, with the optional real ESPHome configuration checks
  enabled against ESPHome 2026.8.2.
- Ruff, Black, changed C++ test formatting and whitespace checks passed.
- Line and branch coverage were collected in repository-local ignored reports.
- Firmware source and YAML are unchanged from the branch baseline. The host
  harness verifies that invalid and queue-rejected replacements retain the prior
  accepted command's retry for both legacy and extended payloads.
- No new full-board firmware build or physical fireplace test was performed for
  this HA-side change. Hardware acceptance remains a release requirement.
