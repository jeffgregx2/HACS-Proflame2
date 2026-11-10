# LilyGO manual shutdown — deferred to v0.8.0

Status: implemented on a separate local branch; remaining hardware and final
both-band firmware-build acceptance pending. Updated 2026-11-06.

The owner explicitly deferred this work from v0.7.0 to v0.8.0 because the firmware
changes grew beyond the intended release risk. This is an approved exception to
the maintenance release's original no-new-features boundary. It is not required
for the HA-only issue #34 recovery fix. Issue #33 remains in v0.7.0, first shipped
in beta6.

## Preserved work and release boundaries

- Branch: `feature/controller-shutdown-v0.8.0`.
- Baseline: `9cbf7d7`, the current committed `dev` baseline when the work was split.
  Merge/rebase the released v0.7.0 work before resuming v0.8.0 integration; the
  HA-only #34 changes are not part of that shutdown branch.
- Preserve the center-button gesture, PMIC power cutoff, guarded radio cleanup,
  OTA-confirmation correction, HA shutdown-request event/logging, and their tests
  together. The HA event consumer and configuration-entry correlation are also
  deferred; they must not remain in the v0.7.0 implementation.
- No new user configuration option or HA controller device is introduced.

## Agreed behavior

1. Pressing the center button wakes a dimmed screen. A short press/release keeps
   the existing page-selection behavior; a cancelled long hold does not cycle it.
2. After more than four seconds, check the power source. On battery, the
   right-hand panel shows **Shutdown?** in red and initially displays six seconds
   remaining. Release before ten seconds to cancel. With USB connected, show
   **Shutdown** in red with **Cannot shut down / Disconnect USB**, without a
   countdown. Unknown power readings similarly show **Cannot shut down / Power
   unknown**. A refused hold requires release and a fresh hold, even if USB is
   removed before ten seconds.
3. At ten seconds, refuse shutdown if TX/learning is active, power readings are
   unavailable, or USB power is present. A refused attempt requires release and
   a fresh hold; removing USB while continuing that hold must not trigger it.
4. After the initial power checks, stop receive activity and pending delayed
   retransmission without transmitting a fireplace command. Send a best-effort
   `esphome.proflame2_controller_shutdown` event to HA if connected, with
   `event_kind=manual_shutdown_requested` and `reason=center_button_hold`.
5. Pause **0.5 seconds**, then recheck USB power. HA records an INFO message with
   the fireplace and linked ESPHome entry IDs. It records intent, not proof that
   power was cut; delivery is not guaranteed when disconnected or unsubscribed.
6. Invoke the configured ESPHome SafeMode component's public `on_safe_shutdown()`
   policy and synchronize preferences before the PMIC write. Respect explicit
   `boot_is_good_on_shutdown` configuration and the framework's check against
   confirming an OTA image that has not booted. Do not disable rollback.
7. Set BQ25896 `BATFET_DIS`, clearing `BATFET_DLY` for an immediate battery cutoff
   while preserving unrelated register bits. Restore the receive path if a power
   check or write fails; leave the API running for diagnosis.
8. Release the center button and reconnect USB to restart. Do not depend on
   access to the power-management button under the enclosure/battery. This
   feature turns off the controller, without changing fireplace power/state.

## OTA rollback finding

ESPHome normally confirms a new OTA boot after its configured healthy interval
(default 60 seconds). The first custom cutoff bypassed orderly-shutdown
confirmation. Powering off during the first minute could therefore revert to
older firmware on the next boot. A short center-button press would still work
on that older firmware even though the shutdown hold would be absent.

The correction binds the existing SafeMode instance during code generation and
calls its shutdown policy after the final power checks. It avoids full application
shutdown hooks because those close the API and would leave a failed PMIC cutoff
without a working diagnostic connection. Framework policy remains authoritative.
See [ESPHome safe-mode documentation](https://esphome.io/components/safe_mode/).

## Evidence and outstanding validation

- The maintainer confirmed one physical power cutoff and restart by reconnecting
  USB. The first countdown value was six seconds remaining, consistent with the
  intended four-second prompt. The approximate initial timing concern does not
  establish a timer defect.
- A repeat hold then appeared unresponsive, while the screen updated, HA TX
  appeared, and a short press changed pages. OTA rollback is supported by the
  inspected implementation but has not been confirmed in that device's boot log.
- The original gesture built successfully for both bands. The subsequent combined
  HA/firmware software suite passed 2,180 tests, with seven external helper/decoder
  checks skipped and one deselected. This was before the final optional-binding
  preprocessor guard correction, and before the release split.
- A real firmware compile exposed an incorrect assumption that ESPHome defines
  `USE_SAFE_MODE`. Code generation now defines `USE_PROFLAME2_SAFE_MODE` only when
  a configured SafeMode instance exists. Updated host/scaffold/real configuration
  checks passed **60 tests** after that correction. A final complete firmware
  build with the corrected guard remains pending; the superseded build was
  stopped when the work was deferred. Do not treat earlier builds as acceptance
  of the corrected notification/confirmation path.
- After the release split, the isolated feature checkout's shutdown-focused
  selection passed **12 tests**, including production C++ handler ordering, real
  ESPHome code-generation bindings and HA event handling. This is focused
  verification, not a replacement for the pending full firmware builds.
- The maintainer subsequently installed the corrected firmware and confirmed
  the four-second countdown prompt, shutdown-request log at zero, USB restart
  and another long-hold prompt after restart. USB refusal at prompt time was
  requested afterward; that display refinement still needs hardware acceptance.

Before release, complete the isolated-branch regression/build checks and verify:

- Four-second prompt, countdown, cancellation and ordinary short presses.
- USB-connected, unknown-power and busy/learning refusals.
- HA notification before cutoff, the half-second interval, and no RF command.
- USB insertion during the notification interval refuses power-off and restores
  receive operation; PMIC I/O failure leaves the API usable.
- Shutdown during the first minute of a newly installed OTA image, USB wake with
  the same firmware build identity, and repeated shutdown/wake cycles.
- Listening and normal controls after recovery for both supported radio bands.

Retain rollback protection, compact synthetic fixtures and private capture/log
handling throughout. No firmware deployment, commit, push or publication is
authorized by this planning document.
