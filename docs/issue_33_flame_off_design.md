# Flame OFF with power ON and configuration page separation

Status: software implementation and automated validation complete locally.
Packet evidence is sufficient;
physical acceptance of HA-generated commands remains outstanding. Tracking:
[Issue #33](https://github.com/jeffgregx2/HACS-Proflame2/issues/33) and
[Discussion #14](https://github.com/jeffgregx2/HACS-Proflame2/discussions/14).
Evidence reviewed: 2026-11-04, after v0.7.0-beta5.

## Purpose

Allow an explicitly enabled fireplace to turn its main burner OFF by setting
flame to 0 while power remains ON, preserving the requested light, fan, auxiliary,
front-burner, and supported CPI settings. Separate hardware feature selection
from optional operating settings in HA configuration.

This is a fireplace-specific capability, not a new meaning for power OFF and
not universal Proflame2 behavior. Preserve existing behavior by default.

## What we know

The Issue #33 reporter describes a Nordik 34i with SIT Proflame 2 controlled by
a LilyGO T-Embed CC1101 Plus. With the original remote in manual mode, lowering
the flame below 1 reportedly extinguishes the main burner while leaving the
decorative lights and selected fan operation available. HA's Power switch
continues to show ON. The request also asks to retain CPI pilot operation;
the new captures confirm its legacy CPI bit remains set. Physical pilot behavior
still needs reporter acceptance.

Discussion #14 independently requested lights and blower operation with flame
OFF. The maintainer reports that their own fireplace instead turns OFF at
flame 0 and ignores subsequent light/fan changes. This difference supports an
explicit option, disabled by default; do not infer support from protocol variant,
radio band, remote identity, or fireplace brand.

The reported service call with power true and flame 0 fails validation. A call
with power false turns off the decorative lighting on the reported fireplace.
The new captures establish the command representation below.

### Resolved packet evidence

The latest [reporter reply](https://github.com/jeffgregx2/HACS-Proflame2/issues/33#issuecomment-6643963545)
contains overlapping logs, not two independent capture sets. Offline bounded
parsing found 15 valid legacy frames with consistent original integrity values,
four of which have manual power ON and flame 0. Two sequences reduce flame
from 1 to 0 at light 6, then change light to 2 without changing power/flame.
The sequences use fan 3 and fan 6 respectively; AUX and CPI remain set.

Non-identifying command/integrity pairs for regression coverage:

| State | Cmd1 / Err1 | Cmd2 / Err2 |
| --- | --- | --- |
| ON, flame 1, light 6, fan 3 | E1 / DD | 39 / 10 |
| ON, flame 0, light 6, fan 3 | E1 / DD | 38 / 31 |
| ON, flame 0, light 2, fan 3 | A1 / 19 | 38 / 31 |
| ON, flame 0, light 6, fan 6 | E1 / DD | 68 / C4 |
| ON, flame 0, light 2, fan 6 | A1 / 19 | 68 / C4 |

These command/integrity values were checked against the original captures; original
remote identity, full PCM and installation logs remain outside public history.
Synthetic fixtures may use an explicit synthetic identity, but must not be
represented as original full captured frames.

Intact ON/zero events reach HA and trigger its legacy semantic-validation
exception. The Python receive restriction is therefore directly reproduced;
the firmware FIFO decoder has the equivalent restriction. Raw pulse delivery
already reaches HA in this installation. No new mode bit or packet format is
needed. Front-burner behavior and physical pilot/fan persistence are not proven
by these captures; preserve requested bits and require hardware acceptance.

## Restrictions identified before implementation

All four restrictions below are addressed by this change; this list records the
original failure paths so their regression coverage remains explicit:

1. **Service and protocol validation reject power ON with flame 0.**
   `control.py::build_requested_state()` requires flame 1–6 when power is true.
   `protocol/models.py::FireplaceState.validate_transmit()` enforces the same
   manual-mode restriction. `protocol/encoder.py::encode_state()` calls that
   validator, so changing the service check alone is insufficient.
2. **The Flame entity deliberately displays 1 when its underlying value is 0.**
   `number.py` creates Flame with minimum 1 and explicitly returns 1 for an
   underlying zero. Its current display therefore cannot confirm the remote's
   actual flame-zero command.
3. **Staged control changes replace an existing flame 0 with 1.**
   `control.py::build_staged_state()` starts with flame 1 whenever the current
   flame is zero. If an ON/zero state becomes supported, a later light or fan
   adjustment must not unexpectedly relight the burner. This is a required
   invariant across every path that constructs or replays a requested state.
4. **Both legacy receive decoders reject manual power ON with flame 0.**
   Python `protocol/packet.py::state_from_frame()` currently permits observed
   flame OFF for extended frames, but not manual legacy frames.
   LilyGO `proflame2_decoder.cpp::observed_state_valid()` also requires manual
   ON flame 1–6. Accurate state tracking requires correcting both receive paths,
   independently of whether HA is allowed to transmit flame-zero commands.

The confirmed mapping keeps power in Cmd1 bit 0 and flame in Cmd2 bits 0–2.
No packet-format change is needed. Frame integrity must always be checked as received.

## Scope and boundaries

- Support HA service calls, staged entity controls, saved fireplace profiles,
  received state, restore, confirmation, and refresh consistently.
- Apply the fireplace option to both LilyGO and YardStick, on either supported
  band and for both protocol variants, subject to capture-backed verification.
- Preserve serial/C/D identity, extended template policy, Learning IDs, feature
  defaults, controller ownership, radio settings, and command repeat counts.
- Preserve thermostat/SMART learning recovery; do not add thermostat control.
- Do not add independent burner entities, model databases, inferred capability
  detection, new transport layers, or controller sharing.
- Fan speed, pilot operation, and front-burner behavior remain subject to the
  fireplace's own logic. Preserve requested bits without promising continuous
  fan operation or changing the meaning of an unknown field.

Implementation is authorized. Release stamping, physical RF transmission,
firmware installation, and publication remain separate actions.

## Fireplace option and storage

Add `allow_flame_off_when_on` to fireplace config-entry options, default false.
User-facing label: **Allow flame OFF while power is ON**.

Suggested description:

> Enable only if your original remote can turn the flame off while keeping the
> fireplace ON for lights or fan operation. Otherwise, leave this off.

This controls command permission, not remote identity. Do not add it to Learning
ID payloads or treat it as a radio/controller setting. Use an explicit local
command policy passed to validation/encoding; do not globally relax manual TX
validation or reuse thermostat/off-state bypass flags for this purpose.

Existing entries without the key behave as false. Normalize and preserve the
option through setup, edits, reload, restart, and saved-profile updates. No
relearning or identity-format migration is required.

## Command and reporting behavior

| Situation | Required behavior |
| --- | --- |
| Option OFF; manual power ON | Continue accepting flame 1–6 and rejecting requested flame 0. |
| Option ON; manual power ON | Accept flame 0–6. Zero means burner OFF with power still ON. |
| Change flame from 1 to 0 while ON | Preserve every other requested field; do not convert this to power OFF. |
| Change light, fan, auxiliary, CPI, or front control while ON/zero | Preserve flame 0 and power ON. Do not implicitly relight the burner. |
| Raise flame from 0 to 1–6 | Preserve accessory settings and transmit the requested flame level. |
| Power OFF | Preserve existing OFF/retained-settings behavior. Do not make OFF into accessories-only operation. |
| Power ON from an OFF state with no positive retained flame | Keep the existing default ignition level of 1; the new option alone must not change normal power-on behavior. |
| Redundant Power ON while already ON/zero | Preserve zero; do not treat it as an implicit ignition request. |
| Full-state service call or saved profile | Require an explicit valid state under the current command policy. Do not turn omitted fields into patch semantics; existing full-state defaults remain unchanged. |
| Receive a valid matching manual ON/zero frame | Report its actual power/flame/accessory values regardless of the TX option. Preserve identity/integrity checks. |
| Invalid flame value | Reject values outside 0–6, non-whole levels, and prohibited combinations without transmitting. |

For enabled fireplaces, the Flame number range becomes 0–6. Remove the blanket
zero-to-one reporting substitution. When power is ON, an accepted observed zero
must be visible even if TX permission is disabled. Do not enlarge the command
permission merely to accommodate received state. Verify HA's number presentation
for an observed zero with a configured minimum of 1; if that cannot faithfully
represent the state, resolve presentation before implementation is considered
complete. Do not silently fabricate a flame level.

Preserve the existing retained flame presentation when power is OFF; distinguish
that remembered power-on setting from an observed ON/zero burner state. An
unknown state must not be presented as a newly confirmed zero reading.

If an ON/zero observation arrives while the option is disabled, an accessory-only
edit must fail clearly rather than silently substituting flame 1. The user can
explicitly raise flame, turn power OFF, or enable the supported capability.

Turning the option OFF must not send an ignition command or silently rewrite
saved state. Reject that configuration change if the effective current/pending
state is ON/zero, or a saved profile still requires ON/zero. Explain that the
user must turn power OFF or select a positive flame, and edit/delete conflicting
profiles before retrying. Recheck at final save to account for intervening state
changes. Persisted ON/zero observations remain valid received data after restart.

## Configuration: two pages

Split the existing combined feature form for both initial setup and later
configuration. This separates installed hardware from optional behavior without
adding another device or configuration entry.

| Page | Fields | User-facing purpose |
| --- | --- | --- |
| **Fireplace features** | Fan, light, front burner, auxiliary, CPI pilot | Select features the fireplace actually supports. |
| **Optional settings** | Refresh period in minutes; Allow flame OFF while power is ON; existing active-listening and packet-debug settings; existing fireplace short name where currently editable | Configure integration behavior and retain access to existing settings. |

Refresh remains 0 (disabled) by default, and the flame-zero option defaults OFF.
Preserve each setup route's existing active-listening defaults and backend
restrictions. Keep short name on the initial identity form where it already
exists; retain editing on the second page rather than expanding this change into
a new identity-settings flow. Do not duplicate a field across setup pages.

The first page advances to the second; only the final submission creates or
updates the entry. HA's supported `last_step` form flag selects its standard
Next/Submit buttons (`False` on the feature page, `True` on the final page).
The local HA API documents this behavior. Titles also show “1 of 2” and “2 of 2”;
no custom frontend or custom button-label mechanism is introduced.

Keep pending inputs in flow-local state. Page-two validation errors preserve
page-one choices and all page-two inputs. Cancellation at either page leaves
persisted configuration, entities, and runtime policy unchanged. Do not reload
or apply page-one choices before final save. Preserve unrelated options and
saved profiles, and retain controller-assignment checks at final entry creation.

Cover guided learning, Learning ID import, retained legacy manual entry, and
existing-entry options. Remove refresh from earlier identity/manual schemas so
it is entered only on page two. Keep radio band before YardStick learning and
on its existing fireplace RF band selector afterward; do not move it into this
two-page flow. Retain the separate saved-profile management menu.

### Delayed retransmission placement

An earlier discussion listed delayed retransmission as an example optional
setting. In the current implementation, it is an ESPHome device control owned
by LilyGO firmware, not an option on the Proflame2 feature form. Keep that
placement and default unchanged. Do not add a duplicate Proflame2 setting or a
YardStick counterpart as part of this issue. The page split applies to existing
Proflame2 form settings and the new flame-zero option.

## Implementation organization

| Area | Changes and invariant |
| --- | --- |
| `const.py`, `profile.py`, runtime loading | Add the default-false option and explicit command policy; retain existing values and identity. Saved-profile normalization must use the same policy. |
| `control.py`, `protocol/models.py`, `protocol/encoder.py` | Carry explicit permission through requested-state and protocol validation. Separate observed-state acceptance from TX permission. Keep command bytes/integrity generation centralized. |
| `number.py`, staged controls | Set the permitted input range, report ON/zero accurately, and preserve zero during unrelated edits. Do not introduce parallel state-building logic. |
| `protocol/packet.py`, LilyGO `proflame2_decoder.cpp` | Accept integrity-valid, matching manual legacy ON/zero observations. Preserve other range checks, thermostat classification, and rejection of malformed captures. |
| `services.py`, profiles, refresh, runtime restore | Audit all state validation/encoding calls and replay paths. Do not lose permission or replace zero during coalescing, confirmation, retries, reload, or restore. |
| `config_flow.py`, translations | Introduce shared hardware/optional schemas and bounded pending-state transitions across setup routes and options. Persist once. |
| Independent Learning ID helper | Shared observed-state acceptance may affect helper imports and packaged protocol modules. Preserve identity derivation, evidence gates, original integrity checks, and stdout contracts; update tests/package coverage where affected. |
| User documentation | Explain supported behavior, when to enable it, and the two setup pages. Keep packet evidence, validation details, and implementation notes here or in technical documentation. |

No transport-specific command encoder is needed. LilyGO transmits HA-generated
payloads and already carries zero-valued flame fields for other states. A firmware
change is nevertheless needed for its legacy observed-state validation. Verify
raw pulse handling, firmware filtering, HA event delivery, and display updates
end to end. Document the firmware requirement and test older-firmware behavior;
do not promise full legacy RX support with firmware that still rejects the state.

Refresh remains eligible by power ON, including flame 0 when permitted. It must
replay that state without relighting the burner. Retain existing inactivity,
RX/TX activity, command-priority, and power-OFF suppression rules. Do not add a
new refresh purpose or scheduling policy. If command policy disallows a saved
zero state, suspend that transmission rather than coercing its flame to 1.

Learning may observe or restore an original flame-zero state before the new
option is selected. Guided learning restoration observes the remote's button
presses; it does not transmit a restoration command, so no TX permission
exception is needed. The final original frame bootstraps actual observed state
even with the option OFF. State-cache restoration likewise preserves observed
zero without transmitting; the older state-only cache fallback reconstructs
that state locally and never grants a command exception. Prompts and automatic
capability selection are unchanged.

## Capture procedure and remaining acceptance

The reporter has been asked to use the original remote, not HA controls:

1. Enable **Debug Logging** for the Proflame2 integration in HA.
2. Enable **PCM Payload Logging** on the fireplace device.
3. Turn OFF thermostat and SMART mode on the remote.
4. Perform these actions about 10 seconds apart:
   - Power ON, flame 1, light 6, and fan at a nonzero setting.
   - Lower flame to 0 without changing the other settings.
   - Change the light level while keeping flame at 0.
   - Raise flame back to 1.
5. Confirm whether the burner extinguishes, lights/fan retain their settings,
   and HA's Power switch stays ON. Released beta4/beta5 can display flame 1
   for a zero observation; the new implementation reports actual zero.
6. Disable PCM logging, then disable debug logging and download the complete
   log. Supply the action order and physical observations.

Collect complete RX log entries, including bit length, PCM hex, event/capture
identifiers, and timestamps. The reporter need not decode the data or obtain
additional RF equipment. Raw PCM is sufficient for offline interpretation.

| Evidence question | Result / remaining work |
| --- | --- |
| Actual flame-zero command | Confirmed legacy Cmd1 power ON, thermostat clear, Cmd2 flame 0. |
| Retained settings | Light/fan/AUX/CPI fields retained in the recorded sequences. Physical pilot/fan behavior and front burner still need acceptance. |
| Protocol variant | Validated seven-word legacy frames. Existing extended support remains covered separately. |
| Subsequent edits | Light 6 to 2 retains power ON/flame 0 with fan 3 and fan 6. |
| Integrity | All 15 structurally accepted rows have consistent original C/D integrity pairs. |
| Delivery/rejection | Complete zero frames reach HA via pulse events and fail the old semantic check. Both Python and the firmware FIFO semantic restriction are corrected. |

The received sequence confirms the expected power/mode representation. If
hardware acceptance shows additional command requirements, revise the design
before implementing those semantics. Do not
expand into new protocol behavior without discussing the evidence. Physical
acceptance of HA-generated commands still requires reporter testing after the
software change; a valid decode is not fireplace acceptance. Fan thermal behavior
and CPI persistence may need targeted follow-up if not demonstrated by this capture.

## Tasks and verification

1. **Software complete — evidence gate resolved.** Retain the confirmed mapping above and add
   privacy-reviewed command/integrity regression vectors. Retain raw logs outside public history. If a capture
   cannot be published safely, document the coverage limitation rather than
   quietly rewriting its fields or checksums.
2. **Software complete — explicit policy and validation.** Test the default restriction, enabled
   ON/zero commands, unchanged OFF rules, invalid values, and both protocol
   variants. Verify power/light/fan/CPI/front/aux fields and original integrity.
3. **Software complete — observed-state paths.** Add Python and firmware decoder regression
   cases that fail on the current legacy manual ON/zero checks; retain all
   existing malformed-frame and integrity rejection tests. Test extended parity.
4. **Software complete — controls and replay.** Exercise number/service/profile control,
   zero-preserving accessory edits, power transitions, coalescing, confirmation,
   refresh, restore/restart, and disabled-policy failures without any implicit
   flame-1 substitution. Preserve unrelated fields exactly in staged edits.
5. **Software complete — configuration split.** Test all setup routes and options with field placement,
   defaults, persistence only on final save, cancellation, error retry, saved
   profile preservation, option-disable conflicts, and controller ownership.
6. **Software complete — compatibility and packaging.** Run existing legacy/extended corpus,
   HA regression tests, firmware unit tests, applicable ESPHome configuration
   and compilation checks, helper/package interoperability, and style checks.
   Verify default-false behavior and unchanged Learning IDs for existing entries.
7. **Documentation complete; physical acceptance pending.** Explain the new
   option and firmware upgrade requirement without implementation detail. Ask the
   reporter to confirm zero-flame accessory operation, return to positive flame,
   ordinary power OFF/ON, and remote-driven HA updates with the updated firmware.

### Required behavior tests

| Test group | Acceptance criteria |
| --- | --- |
| Defaults | Missing option equals OFF; existing ON/positive and power-OFF behavior remains unchanged; no relearning. |
| Enabled controls | Services, Flame entity, and saved profiles permit ON/zero only when enabled; numeric input validation remains strict. |
| No accidental ignition | Every accessory edit, refresh, retry, restore, redundant ON, and command-coalescing case retains an existing valid ON/zero state. |
| Honest RX state | Valid legacy and extended ON/zero observations report zero even when TX permission is disabled; invalid integrity/mismatched identity remains rejected. |
| Firmware | Legacy manual ON/zero survives firmware validation and reaches HA; display and raw capture paths remain correct; old firmware limitations are documented. |
| Option changes | Enabling requires no identity change; disabling conflicting current/pending/saved-profile states fails atomically without TX or state coercion. |
| Configuration pages | Hardware features appear only on page one; optional settings appear on page two; refresh appears once; invalid input and cancellation never partially persist. |
| Backend boundaries | Both backends/bands retain their radio settings and repeat counts; YardStick gains no continuous listening; LilyGO delayed retransmission remains its existing device setting. |
| Helper and learning | Valid observed zero does not break identity derivation, temperature normalization, thermostat recovery, original-state restoration, or packaged helper interoperability. |

## Completion criteria

Complete only when the capture-backed command mapping is established, all
required software checks pass, default behavior remains compatible, no path
implicitly relights an ON/zero burner, and the reporter validates actual
fireplace operation. Report software completion separately if hardware acceptance
is still pending. Do not claim this behavior is supported by every Proflame2
receiver merely because both encoders can represent it.

## Implementation and verification record

- The option is an explicit runtime/validation/encoding keyword with a default
  of false; it is not part of the remote profile or Learning ID.
- Flame reports observed zero even when TX is disabled. The number
  entity temporarily exposes minimum 0 for an observed ON/zero state so HA can
  represent it faithfully; the setter still rejects prohibited zero commands.
  Otherwise its minimum remains 1 until the option is enabled.
- Hardware and behavior pages share schemas; guided and imported setup share
  one final-save path. Saved-profile management remains separate. Controller
  assignment is rechecked on final submission.
- Original command/integrity pairs are retained without publishing raw PCM or
  remote identity. Test frames using those pairs explicitly use synthetic serials.
- Tests cover both encoder variants, Python/RMT and the compiled firmware FIFO
  decoder, invalid integrity/ownership, HA services/entities/profiles, coalescing,
  confirmation, refresh, restoration, learning, and atomic option changes.
- Both 315 and 433 MHz ESPHome 2026.10.0 firmware fixtures compile; the invalid
  band fixture is rejected. Existing image/fallback-AP warnings are tracked by
  Issue #20 and are unchanged by this feature.
- The dependency-free Learning ID archive passes 34 tests, including manual
  zero-state input and its packaged observed-state decoder.
- The complete integration suite passes **2,039 tests**, with 34 skipped and one
  deselected. Installed-wheel validation separately passes 115 CLI/contract tests
  (35 archive/optional checks skipped there) and all 6 HA interoperability tests.
  The four installed-helper cases normally skipped in the integration suite are
  exercised by that interoperability run.
- Repository-wide Black/Ruff and `git diff --check` pass. Local documentation
  links resolve and the design/protocol pages render through the Markdown service.

No firmware has been installed, RF commands transmitted, or changes published.
Reporter acceptance must verify physical accessories/pilot behavior and updated
remote-driven HA state, as well as return to positive flame and normal OFF/ON.
