# Issue 15 Targeted Extended-Frame Capture Protocol

## Purpose

This protocol is the next evidence request for the Issue #15 SIT TMFSLA remote.
It is intentionally narrower than the prior A-I sweep. The existing data shows
that, within the observed rows, `W9` depends on `W5/W7` and `W10` depends on
`W4/W6`. It does not establish the integrity algorithm because `W7` has only
two observed values and several state-bit combinations remain untested.

The capture is diagnostic only. Do not use any unproven decoded value to
transmit a command.

## Preflight: Resolve Existing Exceptions First

Before running the full procedure, provide the original `pcm_hex` lines for
these earlier manually transcribed frames if they are still available:

```text
08 E9 05 D1 06 15 F0 00 B6 E4
08 E9 05 E1 06 15 F0 00 B6 BF
```

The current candidate integrity lanes predict `W10=E6` and `W10=99`
respectively. The raw rows will determine whether the earlier values were
capture/decoding artifacts, transcription errors, or valid counterexamples.
If those logs are unavailable, continue with the full procedure below.

## Setup

1. Use firmware containing the Issue #15 RMT receive path.
2. Enable ESPHome `DEBUG` logging and open the LilyGO log.
3. Set **Active Listener RX Path** to `rmt_pulse`.
4. Set **Enable Capture** to `rmt_pulse`.
5. Do not send Home Assistant commands during the entire session.
6. Keep the native remote near the LilyGO and wait about two seconds after each
   press. Use one continuous capture session for all phases.

Before starting, write down the visible fireplace state: power, flame, light,
fan, AUX, thermostat mode, requested setpoint, and pilot mode where visible.

## Required Evidence

For every press, record this table separately from the raw log:

| Step | Native-remote button | State before | State after | Notes |
| --- | --- | --- | --- | --- |

Preserve the complete, unedited ESPHome log from enabling through disabling
capture. Each accepted frame must retain its full line containing:

```text
RX RMT pulse capture schema=2 capture_id=... pcm_bits=... pcm_hex=...
```

Also retain discarded lines. Do not remove duplicate frames, failed rows, or
unexpected results.

## Phase A: Repeat State Sweep Around A Possible W7 Change

Start in manual mode with power on, light off, AUX off, and flame high if those
states are available.

1. Capture a flame sequence: high to 5, 5 to 4, 4 to 3, 3 to 2, 2 to 1, then
   1 to off. Record whether the final flame-off command also powers the
   fireplace off.
2. If the fireplace powers off, use Power On to restore the baseline. If it
   remains on with flame off, press Flame Up once per level until flame high is
   restored, recording every intermediate capture. Record the actual recovery
   operation and resulting state.
3. Change thermostat mode only: manual to normal at 21 C, normal to smart at
   21 C, then smart to manual. Use the closest supported setpoint if 21 C is
   unavailable.
4. Repeat the exact flame sequence from step 1.
5. Power off, wait 30 seconds, power on, return to the same baseline, and
   repeat the short flame sequence high to 5, 5 to 4, 4 to high.

Why: this compares multiple `W5` values before and after thermostat and
power/idle transitions. It can determine whether the unexplained `W7` value is
a persistent mode/configuration field and whether its effect on `W9` is
independent of flame state.

## Phase B: Thermostat And Light Cross-Checks

Perform each available transition once. If the remote prevents a combination,
record `unsupported by remote` and continue.

For each available requested setpoint in **normal** thermostat mode, then
repeat for each available requested setpoint in **smart** thermostat mode:

1. Set the requested temperature while light is off.
2. Press Light Up once: light 0 to 1.
3. Press Light Up once: light 1 to 2.
4. Press Light Down once: light 2 to 1.
5. Press Light Down once: light 1 to 0.

Why: this adds independent `W4/W6` combinations required to distinguish an
integrity function from a table fitted to the existing thermostat rows.

## Phase C: Unobserved Command Bits

With power, light, thermostat, and pilot state held constant as far as the
remote permits:

1. Capture every fan level from off through high and back to off.
2. Capture each front/rear flame or split-flame setting, if available.
3. Capture AUX off/on/off at two different flame levels.
4. Capture CPI/IPI changes, then repeat one power transition and one flame
   transition in each pilot mode.

Why: the existing capture set does not exercise the fan/front bits carried by
the normal `W5` command field. These rows test whether the observed `W9`
relationship remains valid for every control bit.

## Completion

After the final press, wait about two seconds and set **Enable Capture** to
`off`. Attach the unedited log and the completed step table to Issue #15.

Stop the test if a command produces an unexpected fireplace behavior. The
native remote remains the recovery mechanism; no Home Assistant or LilyGO
transmission is needed for this procedure.
