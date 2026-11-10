# Inactivity-Based State Refresh

Status: implemented locally on `issue-31-inactivity-refresh`; automated checks
passed. Hardware acceptance remains pending. No deployment or publication has
been performed.

The ON-only policy below supersedes the original proposal to refresh OFF states.
Tracking issue: [Issue #31](https://github.com/jeffgregx2/HACS-Proflame2/issues/31).

Related request: [Issue #26](https://github.com/jeffgregx2/HACS-Proflame2/issues/26#issuecomment-6507878495).

## Purpose

Optionally retransmit Home Assistant's current ON state after a
configured period without transmission or reception for that fireplace.
Ordinary TX and valid RX postpone the refresh, so active use does not generate
additional periodic commands.

Some fireplace versions require a state broadcast at least every sixty minutes
or they shut off. The reporter with a Mendota FV48 and SIT Proflame 0.584.090
remote states that the manufacturer specifies a ten-minute refresh interval.
This does not apply to every Mendota fireplace. Those timings
are reported behavior, not independently verified protocol requirements. This
feature sends an ordinary full-state command using the existing encoder and
transmission path. Hardware testing must establish whether that satisfies the
reported receiver timeout.

## Configuration

Add **Refresh period in minutes** alongside the existing fireplace options in
initial configuration and the options flow for existing entries.

- Default: `0`, meaning disabled.
- Accept non-negative whole minutes; reject negative and fractional values.
- Suggested help text: "Resend the current ON state after this many minutes
  without a transmission or valid received command. Set to 0 to disable.
  No refresh is sent while the fireplace is OFF."
- Store the value per fireplace in entry options, using a proposed internal key
  `refresh_period_minutes`.
- Missing values in existing entries normalize to `0`; no relearning is needed.
- Keep the setting independent of Active Listening. With listening disabled,
  only outgoing commands can postpone refresh.
- Use the same behavior for supported production backends and the fake backend.
  This is HA scheduling and requires no new firmware API or ESPHome option.

## Runtime Behavior

For period `P > 0`, maintain a monotonic inactivity deadline per fireplace.

1. On setup, enablement, or a period change, start a full interval of `P` minutes.
2. A completed successful TX for this fireplace restarts that interval.
3. A valid received command matching this fireplace's learned remote identity
   restarts the interval, including a repeated command with unchanged state.
4. At expiry, acquire the existing controller ownership and recheck eligibility.
   If qualifying activity occurred while waiting, postpone the refresh.
5. If there is no newer pending command, learning operation, or active send, read
   HA's current integration-known state. Transmit it through the existing packet
   construction, validation, and send path only if it is ON. Recheck after the
   asynchronous RX handoff; changed activity, OFF state, or a newer user command
   makes the refresh ineligible.
6. A successful refresh counts as TX and starts the next full interval.

The current state is the accepted runtime state returned by
`runtime_current_state()` when no local control intent is pending or in flight.
Do not cache a packet at timer creation. Pending user edits retain normal
dispatch priority; refresh must not replace them or send the older baseline
ahead of them. Encode the selected state immediately before dispatch.

Only ON states are eligible: the purpose is to keep an operating fireplace on.
If the native remote sends OFF, HA accepts OFF and restarts the timer. No further
refresh packet is sent while HA shows OFF, and a previously queued ON refresh
must be rechecked before dispatch. Ordinary user-requested OFF commands still
use the existing transmission path.

### What Counts as Activity

| Event | Restart inactivity interval? |
| --- | --- |
| Successful ordinary TX or refresh TX for this fireplace | Yes, at backend completion |
| Valid matching RX that changes state | Yes |
| Valid matching RX with unchanged state | Yes, before any unchanged-state early return |
| Raw RF noise, rejected packet, or another remote identity | No |
| UI edit awaiting debounce, enqueue, or API dispatch without TX completion | No; pending work separately prevents refresh |
| Failed or timed-out send | No; use the failure pacing below |
| Display synchronization, RX-policy synchronization, or local retained OFF edit | No |

Audit backend duplicate suppression: qualifying repeated RX commands must reach
the activity accounting even if normal state notifications are deduplicated.
One received burst may restart the deadline more than once without affecting
correctness. Do not treat raw capture events as accepted commands.

The existing firmware FIFO listener suppresses copies within five seconds of
the last accepted packet. It does not suppress unchanged commands indefinitely:
minute-scale refresh commands still reach HA. HA transport/backend reception
adds no state-based suppression. The timer counts accepted commands delivered
to HA; no new per-copy firmware activity telemetry is required.

Activity is scoped to the fireplace, while radio exclusion remains scoped to
the physical controller. Another fireplace's command must not indefinitely
postpone this fireplace's refresh.

### Lifecycle and Unavailable States

- `0` schedules no refresh work. Disabling cancels pending refresh work but does
  not cancel a physical transmission that has already started.
- Setup and restart begin a fresh interval; do not persist monotonic timestamps
  or replay missed intervals. Existing runtime state restoration remains in use.
  A restored state is eligible after that interval if ordinary validation allows
  it; there is no additional requirement for fresh manual interaction.
- No known state means no refresh packet. Unsupported states, including native
  thermostat observations that the current TX validator rejects, remain
  unsupported. Do not clear flags or manufacture a different state to send.
- Learning, connection loss, unknown controller readiness, and shutdown retain
  their existing transmission exclusions. Never create a backlog of refreshes.
- On reconnect, start a fresh interval. Do not send an overdue refresh immediately.
- After a failed refresh or a deadline with no eligible state, wait at least one
  configured interval before another automatic attempt. Track attempt pacing
  separately from successful RF activity; avoid an overdue-deadline busy loop.
- Unload, reload, and HA shutdown cancel the scheduler and fence stale callbacks.
  Repeated setup must not create multiple schedulers for one entry.

HA owns scheduling. When HA is stopped or disconnected, LilyGO does not continue
an autonomous periodic refresh. Ordinary successful software TX remains an
optimistic result, not acknowledgement from the fireplace.

## Implementation Tasks

- [x] **Configuration:** Add the constant and zero default in `const.py`;
  normalize and validate the option in `profile.py`; expose it in the existing
  configuration and options forms in `config_flow.py`. Cover every supported
  setup route and preserve unrelated options when saving.
- [x] **UI text:** Add the field label, help text, and validation messages using
  the repository's existing translation conventions.
- [x] **Scheduling policy:** Introduce a small focused module, implemented in
  `refresh.py`, for monotonic deadlines, activity accounting, and bounded
  scheduling. Keep scheduling policy separate from packet encoding and backend
  mechanics. Do not expand the existing five-minute display-sync loop into an
  RF scheduler.
- [x] **Runtime lifecycle:** Add only the needed period, deadline/attempt pacing,
  task ownership, and cancellation state to `Proflame2RuntimeEntry` in
  `runtime.py`. Start after setup; stop through existing unload/shutdown paths.
  Apply options updates through the established update/reload mechanism.
- [x] **TX accounting:** Hook the shared successful-send completion in
  `services.py`, covering entity changes, service calls, saved profiles, and
  refreshes consistently. Enqueue alone must not count as successful TX.
- [x] **RX accounting:** Record accepted matching activity before the
  unchanged-state early return in `async_apply_observed_packet`. Audit active
  listening, confirmation reception, and backend deduplication so unchanged
  valid commands reset the timer without changing existing state precedence.
- [x] **Dispatch integration:** Reuse the controller arbiter and ordinary send
  execution. Recheck the current deadline, lifecycle, state, and pending intent
  after waiting for controller ownership. Permit a deliberate unchanged-state
  resend without weakening ordinary no-op suppression or creating a second
  physical-send path. Do not reacquire a non-reentrant controller lock.
- [x] **Errors and logging:** Use a distinct source such as
  `inactivity_refresh` in existing TX diagnostics. Log failures through current
  error reporting, pace automatic attempts, and preserve saved-profile metadata
  and state confidence semantics for an unchanged-state refresh. Completion
  records TX without replacing the accepted state or starting a stale-state
  confirmation window; RX arriving during physical TX remains current.
- [x] **Documentation:** Describe configuration, zero default, native remote
  interaction, HA outage behavior, and the difference between successful TX and
  fireplace reception in user documentation.

Firmware-only optional delayed retransmissions are part of an existing command's
retry behavior. They must not introduce a new HA activity telemetry contract for
this feature. The HA interval is measured from its known backend TX completion;
the existing short delayed retry may occur afterward.

## Automated Tests

Use controlled monotonic time and fake backend completions; no real-minute
sleeps or hardware transmissions. Add focused scheduler tests, implemented in
`tests/test_refresh.py`, and extend the existing configuration, service, and
control-intent tests at their integration boundaries.

| Area | Required assertion |
| --- | --- |
| Defaults and compatibility | Existing entries and new forms default to zero; zero creates no refresh TX or scheduler. |
| Configuration routes | Supported setup and options routes save the value, preserve other options, and display the saved value. |
| Validation | Zero and positive integers pass; negative, fractional, boolean, and invalid text input fail. |
| Basic expiry | Nothing sends before the deadline; exactly one send occurs when due; successful completion schedules the next interval. |
| TX resets deadline | A successful ordinary send postpones refresh for a complete interval measured from completion. |
| RX resets deadline | Matching valid changed and unchanged packets both postpone refresh; exercise the existing early-return path. |
| RX filtering | Noise, invalid integrity, unrelated IDs, and raw captures do not postpone refresh. |
| Backend deduplication | A valid repeated native command still reaches activity accounting despite state-event deduplication. |
| Fresh state | Change state after timer creation; expiry sends the latest state and complete encoded attributes. |
| OFF handling | Native OFF postpones the timer and prevents refresh TX, including an OFF update while waiting for the radio or RX handoff. No resurrection of an earlier ON. |
| No-op bypass | Unchanged refresh state produces TX while ordinary redundant control changes retain current behavior. |
| Local intent overlap | Pending/debounced and in-flight commands take priority; refresh neither replaces intent nor sends a stale baseline. |
| Controller contention | Waiting for the shared arbiter cannot overlap sends; activity or newer intent during the wait invalidates the due refresh. |
| State validity | Missing state and unsupported thermostat/flame-zero TX states cause no packet mutation, transmission, or busy retry loop. |
| Failure and timeout | Failed/uncertain sends preserve existing errors and ownership fencing; automatic attempts remain spaced by the configured interval. |
| Options updates | Disable cancels queued refresh; enable or period change starts a fresh interval; stale callbacks cannot send. |
| Startup and reconnect | No immediate replay; restored valid state becomes eligible only after a full interval. Disconnection prevents sends. |
| Learning and shutdown | No refresh during learning or after unload/shutdown; repeated setup creates one scheduler. |
| Scope | Activity for fireplace A does not reset B; shared-controller exclusion and independent-controller concurrency remain intact. |
| Non-RF work | Display sync, RX-policy sync, and retained OFF edits do not reset RF activity timestamps. |
| Existing semantics | Saved-profile metadata, requested/observed confidence, packet integrity, and ordinary control behavior remain correct. |

Run the focused tests first, followed by the full applicable Python suite,
formatting/lint, and required repository checks through the established
validation workflows. Do not change encoding or RF timing to make these tests
pass. Any unavailable validation must be recorded before implementation is
considered complete.

## Local Validation

- Default Python suite: **1,558 passed, 30 skipped, 1 deselected**.
- Ruff, Black, and `git diff --check`: passed.
- The skipped tests require an explicitly configured ESPHome CLI (27 cases) or
  external instrumented rtl_433 source (3 cases). The source-only rtl_433 check
  was deselected by the standard test marker policy. No firmware/package source
  changed for this feature.
- Tests used Home Assistant 2026.9.0 and Python 3.14 in a temporary native
  environment. No RF transmissions or hardware acceptance tests were performed.

## Hardware Acceptance

- [ ] With the option disabled, confirm existing behavior and no periodic TX.
- [ ] With ten minutes configured and the native remote inactive, observe refresh
  TX after inactivity. Verify the fireplace stays in the selected state beyond
  the reported sixty-minute timeout, under direct observation.
- [ ] Change an HA control before expiry and verify a full interval passes after
  TX completion before the next refresh.
- [ ] Receive a native command, including an unchanged-state refresh, and verify
  that it postpones HA refresh. Correlate RX/TX timestamps in logs.
- [ ] Receive native OFF and verify HA remains OFF with no subsequent refresh TX.
- [ ] Disable the option, restart HA, and interrupt the controller connection;
  verify the documented cancellation and fresh-interval behavior.

Record software versions, configured interval, observed fireplace behavior, and
TX/RX times. A successful TX log alone does not pass hardware acceptance.

## Completion Criteria

Configuration and all automated cases pass; normal control and listening behavior
remain intact; disabled entries retain their previous behavior. Hardware results
must separately establish whether ordinary full-state refresh prevents the
reported receiver timeout. Until then, describe protocol compatibility as
unverified rather than claiming that the manufacturer's refresh is reproduced.
