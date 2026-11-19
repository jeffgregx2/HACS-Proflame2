# Service state merging and pilot retention

Status: approved design for v0.8.0, V8-15. Implementation has not started.
Create the public tracking issue after v0.7.2. This change is HA-only.

## Problem and intended behavior

`proflame2.set_state` currently treats requests with and without an explicit
flame value differently. Supplying flame can reset omitted accessories rather
than retain their current settings. Pilot retention also differs by frame type.
The service should consistently change only the attributes supplied by the user.

Each request builds a complete state from the latest effective current state,
including pending desired changes, then overlays explicitly supplied attributes.
An omitted attribute retains its current value regardless of whether flame was
supplied. Power remains required by the existing service schema.

### Power and flame

- Power OFF preserves the current flame level, including 0 through 6, and other
  attributes unless the request explicitly changes them.
- On an OFF-to-ON transition, use the resulting flame level. If it is 0, raise
  it to 1 so the burner lights.
- Exception: an explicitly supplied `flame: 0` remains 0 on OFF-to-ON when
  **Allow flame OFF while power is ON** is enabled.
- An omitted flame of 0 on OFF-to-ON becomes 1 even when that option is enabled.
- A request while already ON does not perform ignition normalization. Supplied
  values apply; omitted values remain current. Normal validation still rejects
  ON/flame 0 when the option is disabled.
- Unrelated light or fan adjustments must not relight an already-ON burner.

### Pilot mode and current state

Preserve active CPI/IPI mode for both legacy and extended frames unless the user
explicitly changes it through an enabled UI control or service attribute.
Learned/template pilot defaults initialize state only; do not reapply them on
later commands. Disabling the pilot control must not reset the transmitted mode.
Valid native-remote observations continue updating the current state, including
pilot mode, which subsequent unrelated commands retain.

Use the existing persisted state, learned-packet bootstrap and safe initialization
path. Normal HA restart restores last-known settings; it does not require a new
persistence mechanism. Last-known state is not an acknowledgement from the
fireplace. If no effective state exists unexpectedly, fail clearly rather than
inventing pilot values in the service merge path.

### Saved profiles

Saved profiles are complete states. Apply their stored values directly, retaining
normal feature and range validation. Do not merge omitted service attributes or
apply the partial-request ignition correction to profile application. Preserve
the existing profile identity and activation behavior.

## Implementation tasks

1. Add failing behavioral regressions for partial service requests before changing
   production code, covering both protocols and the transition cases below.
2. In `services.py`, replace flame-dependent defaulting with a single merge from
   the effective current/pending desired state. Capture the prior power state and
   explicitly supplied fields before merging so transition handling can distinguish
   an explicit flame 0 from a retained one.
3. Apply the OFF-to-ON correction at that boundary only. Adjust `control.py`
   validation to accept retained OFF flame levels 0–6 while preserving ON/flame-0
   permission checks and existing range/feature validation.
4. Generalize pilot preservation across legacy and extended UI/service paths.
   Preserve pending-intent serialization and native-observation arbitration; do
   not merge against a stale last packet when newer desired changes exist.
5. Keep saved-profile application outside partial-service normalization even
   though it shares execution helpers. Verify full stored-state transmission.
6. Align service descriptions and end-user examples with the consistent retained-
   value contract. Record the behavior change for automations that previously
   depended on omitted accessories resetting to defaults.

No firmware, RF encoding, transport, retry, configuration-option or persistence
redesign is included. The shared execution path and pending changes are the main
regression risks; keep the changes local and covered by behavioral tests.

## Acceptance coverage

| Case | Expected result |
| --- | --- |
| Explicit flame, omitted fan/light/front/aux/CPI | Retain every omitted current attribute on both protocols |
| Power-only OFF, flame initially 0, 1, 3 or 6 | Preserve flame and all other current settings |
| OFF-to-ON, retained flame 0, option OFF or ON | Transmit flame 1 and display the same state |
| OFF-to-ON, explicit flame 0, option ON | Transmit flame 0 |
| OFF-to-ON, explicit flame 0, option OFF | Reject without transmission or unintended state change |
| OFF-to-ON, retained or explicit nonzero flame | Retain/use the requested level |
| Already ON, power true with current flame 0, option ON | Preserve flame 0 unless explicitly changed |
| Already ON, light/fan change with flame 0 | Preserve flame 0; no unintended ignition |
| Pending accessory edit followed by a service request | Merge pending values, preserving existing ordering and supersession |
| Native CPI/IPI observation, then unrelated UI/service change | Preserve observed pilot on both protocols, including when pilot control is disabled |
| Explicit enabled pilot change, power OFF or ON | Send the updated pilot with the other effective settings |
| HA restart after pilot selection | Preserve restored pilot; do not reseed from the learned default |
| Apply a complete saved profile | Transmit stored state, without partial-request merge or ignition correction |
| Invalid or unsupported settings | Preserve existing validation and feature handling |

Assert generated/transmitted state and published HA state, plus no transmission
on rejected requests. Extend service/control/profile tests and run the relevant
integration suite. For manual acceptance, use YardStick/rtl_433 for extended
state and pilot checks and LilyGO/native legacy observations for retained state.
Check OFF-to-ON flame 0 and nonzero behavior, pending accessory retention, pilot
changes while OFF, and one complete saved profile. A focused smoke test is
sufficient; this HA-only change does not require repeating the HA upgrade matrix.
