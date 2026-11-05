# Active-listening recovery after startup/API failures

Status: implemented and automatically validated locally for v0.7.0 GA. This bounded
lifecycle fix is separate from the v0.8.0 public-transport rewrite in
[Issue #22](https://github.com/jeffgregx2/HACS-Proflame2/issues/22).

## Problem and existing coverage

The previous listener retried only a RuntimeError containing the exact
"not loaded or has no runtime_data" message. Other exceptions ended the task.
The existing startup test verified that the task remained alive shortly after
one failure; it did not verify repeated attempts, restored service discovery,
or actual reception afterward.

Periodic display synchronization can restart active listening when it succeeds,
but receive recovery must not depend on that unrelated task, a user command,
an integration reload, or a HA restart.

A second failure mode occurs after connection: the transport can retain an old
runtime object while ESPHome is disconnected or replaces its runtime. Returning
no event from that stale path leaves the task alive without restoring operation.

## Required lifecycle

- Own one active-listening task per fireplace while enabled and configured.
- Retry receive and observed-state-application errors within that task. Preserve
  cancellation, option disablement, shutdown, removal, and learning pauses.
- Use a one-second initial retry delay, exponential backoff capped at thirty
  seconds, and no maximum attempt count. Recovery is possible whenever a
  previously unavailable action/API becomes usable again.
- Log one warning at the start of an interruption, subsequent failures at DEBUG,
  and an INFO record when receiving calls resume. Include unexpected exception
  tracebacks without flooding normal logs on every retry.
- On connection/runtime errors, invalidate the backend's connected flag so the
  next attempt rediscovers actions, configures the current runtime, and reapplies
  the existing listening policy. Do not reconnect merely because a received
  packet fails semantic validation.
- Before consuming events, check that the linked runtime is still the one used
  by the transport and is available. A missing, offline, or replaced runtime
  signals the owning loop to reconnect rather than silently polling forever.
- Repeated start requests must not create duplicate listeners. An exiting task
  clears its handle only if it still owns it.

Persistently incompatible firmware remains unable to receive until corrected.
The task stays retryable and the warning remains diagnostic evidence; retrying
does not fabricate missing capabilities or claim hardware recovery.

## Scope exclusions

No packet mapping, integrity acceptance, command encoding, RF settings, repeat
counts, or firmware changes. This does not implement Issue #33 flame-zero TX,
relax its receive validation, or replay a fireplace command during reconnect.
No redesign of action discovery or migration to public transport APIs is included.

## Regression evidence

New tests reproduced missing-action, disconnected/timed-out API, receive-error,
and stale-runtime recovery failures before the implementation change.

The focused suite now covers:

- Real transport/backend recovery from delayed runtime creation, missing action
  metadata, API disconnection, timeout, an endpoint going offline after connection,
  and replacement of the native runtime object.
- Several failed attempts followed by an actual event-bus PCM packet updating
  observed HA state on the same listener task. The tests invoke neither periodic
  display sync nor user controls and assert no TX action is called.
- Receive errors after successful operation, including malformed-frame errors,
  followed by later valid observations. Invalid packets remain rejected.
- Deduplication of start requests, bounded warning output, and intentional stop
  during retry when disabled, shutting down, losing the backend, or unloading.

These are automated tests with simulated native API/RF events. They do not prove
the cause of a reporter's RF capture quality or replace physical reconnect testing.

## Validation record

The focused active-listener suite passed 14 cases, including 12 new regression
cases. The full Python suite passed 1,995 tests, with 34 skipped and one
deselected. Repository-wide Black and Ruff checks passed, as did `git diff
--check`. Local document references resolve, and both this document and the
v0.8.0 maintenance plan render through the Markdown service.

Physical disconnect/reconnect acceptance remains separate. No changes have
been deployed or published.
