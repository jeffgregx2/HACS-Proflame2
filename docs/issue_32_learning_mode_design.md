# Thermostat mode and temperature handling during learning

Status: original learning fix released in v0.7.0-beta5 and confirmed by the
reporter on beta6. Explicit detected-mode guidance refined locally on `dev`
for v0.7.0; automated validation passed and the maintainer confirmed the revised
action numbering in hardware testing. Reporter confirmation of the refinement
is pending. Tracking: [Issue #32](https://github.com/jeffgregx2/HACS-Proflame2/issues/32).

## Pre-GA guidance follow-up — 2026-11-07

The [reporter's beta6 acceptance and feedback](https://github.com/jeffgregx2/HACS-Proflame2/issues/32#issuecomment-6699066815)
confirms successful learning after leaving thermostat/SMART mode, but reports a
confusing return from "Press the Power button again" to the mode instructions.
The pause already identifies thermostat packets and retains the current learning
step; its text did not explicitly state that the remote's mode was detected.

The owner requires that explanation before v0.7.0 GA. Keep this change limited to
the shared HA/LilyGO mode instruction: begin with **Thermostat or SMART mode
detected.** on its own line (using a Markdown hard line break in HA), then retain
the existing instructions for turning both modes off and automatic continuation.
The first prompt only asks the user to press Power; it does not ask every user
to turn thermostat/SMART off before any packet has identified the active mode.
The owner additionally approved a small v0.7.0 presentation change: show a bold
header followed by a blank line and the instructions, keeping HA's existing
centered progress layout for all text. Request numbers 1–4 use **Step 1
of 4** through **Step 4 of 4**. Later requests use **Additional step 5**,
**Additional step 6**, and so on, even when their action text repeats. A mode
pause uses **Thermostat or SMART mode detected.** as the header, without a step
number; the instructions explain how to leave that mode. On returning to manual
mode, the same internal learning stage resumes under a new action number.
Diagnostic prompts use their
existing separate sequence, labeled **Diagnostic step X of 4**. Header totals
come from the existing prompt lists. A separate request counter increments for
each new action, including power corrections, mode instructions and requests
for more derivation evidence, independently of the internal learning stage.
The thermostat warning keeps its unnumbered header, but its requested action
counts toward subsequent numbering. Extra actions never restart their count
at one. Refreshing a pending prompt, duplicate packets and ignored telemetry
do not increment it. Reset the counter when starting a new learning session.
LilyGO's Learn N title follows this request counter and retains plain
instructions. These presentation changes
do not alter packet acceptance, sequencing or timeout rules. Broader packet/retry progress feedback is
separate v0.8.0 work. Mobile appearance still needs visual acceptance.

Implementation and verification:

- [x] Add the explicit detected-mode explanation to the shared instruction.
- [x] Separate HA headers and instructions, including standard and additional
  steps and an unnumbered mode pause. Retain the step number during mode recovery.
- [x] Exercise thermostat/SMART detection both at the first prompt and after
  an initial OFF packet advances to "Press the Power button again".
- [x] Verify both HA and LilyGO show the explanation at the retained learning
  stage, mode exit and repeats do not complete that stage, and learning succeeds.
- [x] Run learning and configuration-flow regressions: **101 passed**, including separate header/instruction rendering, additional-step numbering and mode-pause coverage. The two
  affected end-to-end cases failed for the missing explanation before the fix.
- [ ] Obtain reporter confirmation of the revised guidance and include it in
  the next v0.7.0 build. No firmware update is required for this text change.

### Executable prompts and power prerequisites — 2026-11-07

The maintainer found a blocking assumption in the original fixed sequence:
starting ON and following OFF → ON → OFF leaves the fireplace OFF when step 4
asks for Flame Down. The flow must inspect the last matching received state
before asking for a flame adjustment; it cannot assume three Power presses end
in ON. This correction is required for the v0.7.0 guided-learning flow.

- Retain the latest observation after existing frame, identity and format
  validation, including duplicate and mode-exit packets. Profile evidence remains
  separate; a duplicate is not promoted into new derivation evidence.
- Before standard or additional flame-adjustment prompts, if the observed
  fireplace is OFF, ask: **The fireplace is OFF. Press the Power button to turn
  it ON before adjusting the flame.** Show this on HA and LilyGO. Keep the
  current internal learning stage, preserve accumulated evidence, and transmit
  nothing. The corrective request and subsequent flame request have distinct,
  increasing action numbers.
- Wait for an ON observation from the matching remote. OFF repeats do not
  satisfy the prerequisite or restart the per-prompt deadline. A previously
  seen legacy ON packet can restore the power prerequisite, just as it can
  satisfy the existing restore-power prompt. Extended learning retains its
  command/telemetry duplicate rules.
- A corrective packet does not advance the outstanding flame step. If it
  independently supplies enough valid derivation evidence, learning may complete
  normally; otherwise reissue the applicable flame instruction at the same
  internal stage with the next action number.
- If an OFF packet arrives while waiting for a flame adjustment, return to the
  power prerequisite immediately, even if that OFF packet is a duplicate. Use
  the existing prerequisite-transition return path without treating it as a
  completed flame action. Thermostat/SMART handling retains priority.
- Once ON, request Flame Up for flame 0 or 1, and Flame Down above 1. This
  avoids asking a remote to lower flame below its usable minimum or raise it
  beyond its maximum. These directions apply to additional flame prompts too.
- Keep RF decoding, integrity checks, identity generation, diagnostic-capture
  sequencing and the standalone rtl_433 helper outside this HA prompt correction.

Regression coverage includes both legacy and extended learning, initially ON
and OFF, unexpected power-off while a flame action is outstanding, repeated OFF
packets during correction, restoration with a duplicate ON packet, minimum and
maximum flame settings, additional prompts, and retained mode-recovery behavior.
The original **101-test** wording/layout check preceded this correction;
updated verification passed **121 learning/configuration-flow tests** and the
full default regression selection passed **2,166 tests**, with **34 skipped**
and one deselected. The skips cover 27 real ESPHome configuration checks (CLI
not enabled), four installed-helper interoperability checks and three external
rtl_433 detector checks. Black, Ruff and whitespace checks passed.
The maintainer subsequently reported that the revised flow and action numbering
worked well. Broader remote and mobile acceptance remains pending.

After separating action numbering from capture stages, **122 learning and
configuration-flow tests passed**. Coverage includes corrective ON requests,
unplanned OFF during a flame request, automatic mode recovery, additional
evidence requests through Additional step 6, and repeated progress callbacks
that must not increment the count. The earlier full-suite result above predates
this presentation-counter refinement.

Extended remote learning must pause when thermostat or SMART mode is detected,
resume automatically when the remote returns to manual mode, and tolerate room
temperature, setpoint, and unit changes throughout learning. The Home Assistant
flow and the standalone rtl_433 Learning ID helper must use the same protocol
policy. No additional temperature captures are required to implement this behavior.

## Original evidence and failures before the beta5 fix

The [reported transition sequence](https://github.com/jeffgregx2/HACS-Proflame2/issues/32#issuecomment-6566081499)
shows thermostat ON, SMART, and manual frames with W4 low bits `10`, `11`, and
`01`, respectively. Leaving thermostat mode broadcasts the restored manual
state. The remote reportedly repeats each command once after approximately
15 seconds, unless another press supersedes it. These observations support
automatic resumption without requiring a confirmation click.

Before the fix, guided learning accepted thermostat packets as completed actions but
profile derivation requires manual evidence. The flow therefore continues asking
for commands without explaining the missing requirement. HA and LilyGO can show
different descriptions of that apparent progress.

Profile derivation already validated extended checksums against the received
bytes. It replaced W7 with `0xC8`, but required W8 to remain constant and copied
it into the output template. A changing temperature can therefore produce a
false template conflict. The standalone Learning ID helper also compares profiles
derived from individual packets, so the fix must cover that comparison too.

## Packet validation and profile generation

Keep received frames immutable. Validate each original frame, including W7, W8,
W9, and W10, before using its identity, mode, or command fields. Never replace
temperature bytes before checking integrity or modify received checksums to
make a frame valid.

For newly learned extended profiles:

- Continue requiring one serial ID and one protocol variant.
- Derive retained flags from manual ON frames using W4 mask `0x88` (bits 3
  and 7). Preserve and check those flags consistently. Bit 3 remains unknown;
  this fix does not change extended CPI handling for bit 7.
- Exclude W4 bit 2 and W6/W7/W8 from identity consistency checks. They describe
  units, setpoint, and room temperature, not fixed remote identity. Allow them
  to change without discarding prior evidence or restarting learning.
- Produce one canonical manual TX template: clear W4 bit 2 for Celsius, set
  W6 to decimal 20 (`0x14`), W7 to `0xC8`, and W8 to `0x00`. The generated
  setpoint and room temperature both represent 20°C. Retain W4 bits 3 and 7
  from the validated evidence. This replaces the earlier proposal to preserve
  observed units and select separate Fahrenheit/Celsius temperature pairs.
- Generate W9/W10 through the existing encoder from the actual outgoing fields.
  Original received integrity proves the observations; newly calculated integrity
  protects the generated command. Those are separate operations.

The integration operates the fireplace in manual mode, where temperature is
unused, but generated temperature fields should still agree with their units
flag. [Controlled unit-switch captures](https://github.com/jeffgregx2/HACS-Proflame2/issues/32#issuecomment-6566490137)
confirm that both W6 and W8:W7 change scale when W4 bit 2 changes. New profiles
and Learning IDs must be independent of received units, setpoint, room
temperature, and input ordering when the remaining evidence is equivalent.
Do not require approximate Fahrenheit/Celsius conversion matches between
successive observations: the remote may round setpoints and room temperature
may change between packets. Original integrity remains mandatory.

Use the same fixed manual template for learning, HA profile loading, and
Learning ID import/export. Original imported IDs must pass their checksum and
canonical encoding checks before their temperature fields are replaced. HA
always transmits with a 20°C setpoint and 20.0°C room-temperature template,
independently of the imported or previously stored temperature values. The ID's
binary layout remains unchanged. An older valid ID may export a different ID
once when normalized; equivalent profiles then produce the same ID thereafter.

`protocol/manual_profile.py` owns this policy, including the fixed Celsius
values and identity flag mask `0x88`. The generic encoder retains its literal
field behavior and mask `0x8C` for protocol fixtures and diagnostic use; HA
normalizes its runtime profile before calling it. Never rewrite received frames
to apply this policy before their original integrity has been validated.

## Guided learning states

Use a small explicit state enum for normal extended guided learning. The flow
owns presentation and transitions; the learning session owns packet reception
and accepted evidence. Keep one receiver task and the existing controller lease
through every transition.

| State | Instruction and behavior | Transition |
| --- | --- | --- |
| Collecting a command | Show the current learning instruction. Accept a qualifying manual command change. | A thermostat or SMART frame enters waiting for manual mode without advancing the prompt. |
| Waiting for manual mode | “Turn OFF thermostat and SMART mode on your remote. Press the thermostat button until both modes are off. Learning will continue automatically.” | A valid matching frame with W4 bit 1 clear resumes the pending prompt. |
| Resuming the command | Show the pending instruction again with a fresh full prompt timeout. | A qualifying subsequent command completes that prompt. |

The normal first instruction should also ask users to turn thermostat and SMART
mode off before pressing Power. This prevents avoidable waits but does not
replace automatic detection.

### Mode transitions

For supported extended frames, W4 bit 1 set means thermostat or SMART mode is
active. Bit 0 alone cannot identify manual mode because SMART frames have both
low bits set. Check mode after integrity and remote/variant validation, before
duplicate filtering or prompt acceptance.

Thermostat packets remain available to diagnostics but do not enter the accepted
manual evidence set, advance prompts, or satisfy frame/command requirements.
Receiving thermostat ON followed by SMART must leave the same waiting prompt
visible. Previously accepted valid manual evidence is retained.

A matching frame with bit 1 clear ends the wait, including a manual OFF frame
with low bits `00`. Manual OFF is sufficient to establish that thermostat mode
is disabled, but is not an ON template sample under the existing derivation rule.
The transition frame establishes the current command baseline; it does not
complete the pending action or count as a newly prompted observation. Request
the pending action again, without inferring that the user already performed it.

If a remote does not transmit when its mode changes, the instruction can add:
“If learning does not continue, press Power once after turning both modes off.”
The first valid manual packet then supplies the same transition baseline.

Use the existing configured per-prompt timeout for each manual-mode wait and
for the resumed action. Repeated thermostat packets do not extend the wait.
When mode waiting expires, report that thermostat/SMART mode was not observed
turning off, rather than a generic missing flame command. Cancellation and
exceptions close the session and release the controller through the existing
cleanup path.

### Repeats and temperature-only changes

Keep raw frame identity for diagnostics. Separately compare extended command
state using `(W4 & 0xFB, W5)` for guided action acceptance, clearing only the
units bit. Setpoint, temperature, and units-only changes must not count as a
new command or advance a prompt. Legacy command keys remain unchanged.

Keep a last observed manual command baseline across prompt and mode transitions.
A byte-identical repeat, or a frame with unchanged normalized command state,
does not complete a guided action. This includes restore prompts: returning to
an earlier session command is allowed only when it changes the immediately
preceding command state. Do not use a global “seen once means unusable” rule
for legitimate restoration.

The approximately 15-second repeat needs no special delay or timing heuristic.
A repeated command has no command-state change, so it contributes no extra
guided action. Do not claim RF can distinguish an arbitrary delayed old command
from a real press producing that same state; the reported remote cancels pending
repeats when another button is pressed.

## Implementation boundaries

1. **Shared protocol policy:** Define the canonical manual template in
   `protocol/manual_profile.py`; use it from derivation, ID import/export, and HA
   profile loading. Use the identity flag mask `0x88` and exclude received
   units/setpoint/temperature from profile consistency. Count command diversity
   using a shared extended command key `(W4 & 0xFB, W5)`. Keep original-frame
   validation first. Provide narrowly named mode and command classification
   functions in the protocol package for HA and the independent helper.
2. **Learning session:** In `learning.py`, add typed capture outcomes for a
   completed action, manual mode required, manual mode restored, and terminal
   result. Keep transport receive mechanics separate from acceptance policy.
   Retain explicit session state and command baseline; do not overload packet
   counts, prompt labels, or warning text to encode transitions.
3. **HA flow:** In `config_flow.py`, handle mode outcomes before incrementing
   the prompt index or deriving a profile. Complete the current progress task
   on a mode transition, then start the next task with the new instruction.
   This uses the existing HA progress lifecycle and avoids relying on an
   in-place text update to a running task. HA and LilyGO receive the instruction
   for the same state. Add matching strings/translations and specific timeout
   text through the repository's established translation process.
4. **Standalone helper:** Update `tools/learning_id/evidence.py` and
   `tools/learning_id/guided.py`, plus file/live CLI guidance and tests, as
   specified below. Both adapters must consume the shared protocol policy;
   the helper must not import HA or depend on the rtl_433 source tree.
5. **Compatibility and diagnostics:** Preserve legacy learning, unsupported-frame
   diagnostic collection, existing firmware APIs, imported IDs, and saved
   profiles. Record mode transitions and acceptance reasons through existing
   diagnostic facilities. Counters must distinguish observed packets from
   accepted evidence. No firmware or rtl_433 decoder source change is required.

## Standalone rtl_433 helper tasks

The helper is the separate `proflame2-learning-id` program in this repository,
not code to add to upstream rtl_433. Updating shared derivation alone is
insufficient because the helper stores command keys and compares candidate
profiles before and after confirmation gates.

- **Candidate validation:** `Evidence.add()` must validate every original frame
  before deduplication, even for a repeated or already-confirmed command.
  Canonical profiles derived from equivalent manual evidence must compare
  equal despite differences in W4 bit 2 or W6/W7/W8. Contradictions in retained
  flags, serial ID, protocol variant, or original integrity still fail.
- **Command accounting:** Apply the shared normalized command key consistently
  to representative frames, repeat counts, the frozen derivation basis, and
  additional confirmation commands. Units switching must not create new keys
  or confirmation gates. A second validated observation with the same command
  and different temperature settings may corroborate that command; it cannot
  supply command diversity. Preserve original representative frames unchanged.
- **Evidence gates:** Retain the existing three-command derivation requirement,
  two observations per derivation command, and two additional distinct command
  checks for full confirmation. For extended frames, thermostat/SMART traffic
  must not satisfy those gates; manual evidence is still required. File mode
  must continue validating all input through EOF, including after confirmation.
- **Guided mode recovery:** Replace the current thermostat-mode abort/restart
  with a bounded wait asking the user to turn off thermostat and SMART mode.
  Resume the outstanding target automatically on a valid matching manual frame.
  Use the exit packet as a baseline, not confirmation of that target. Keep
  terminal interaction and target sequencing in the helper, separate from the
  HA state machine. Update its normalized last-command comparisons and keep
  units/setpoint-only changes from advancing targets or extending deadlines.
- **File and unguided live operation:** Validate thermostat observations and
  identity consistency, but exclude them from manual learning evidence. Explain
  missing manual commands when necessary. Retain valid accumulated evidence
  across temperature and units changes. Mode traffic cannot mask bad integrity
  or contradictory remote identity.
- **Output and delivery:** Keep the Learning ID alone on stdout, guidance and
  diagnostics on stderr, and existing cancellation/cleanup behavior. Update the
  installed helper packaging/decoder-contract tests and user guidance together
  so the distributed helper uses the same policy as HA.

## Tests and completion criteria

Use compact synthetic fixtures for temperature and timing variations. Preserve
capture-derived expectations independently of the implementation under test,
and apply the repository's privacy rules before adding any recorded vectors.

| Area | Required coverage |
| --- | --- |
| Original integrity | Valid changes to W4 units and W6/W7/W8 pass; changing any covered byte without the matching original checksum fails before normalization. |
| Deterministic profile | Fahrenheit-only, Celsius-only, mixed units, varying setpoints, W8 rollover, and reordered inputs produce the same new profile and Learning ID for equivalent command evidence. |
| Real conflicts | Mixed serials/formats and inconsistent retained manual W4 bits 3/7 still fail, including after helper candidate freeze. |
| Mode detection | Thermostat `10` and SMART `11` pause; manual `01` and manual OFF `00` release the wait. Invalid or wrong-remote frames cannot release it. |
| Prompt accounting | Mode packets and the exit packet do not advance the prompt; accepted manual evidence survives the pause; only a subsequent qualifying command completes the pending action. |
| Repeats | An identical repeat after 15 seconds, repeated mode packets, and temperature-only changes cannot advance ordinary or restore prompts. A real return to an earlier command after an intervening state change succeeds. |
| UI and timeout | HA and LilyGO show the same manual-mode instruction, resume automatically, and receive a fresh action timeout. Repeats do not postpone mode timeout; cancellation and errors release the controller. |
| Helper | Units/setpoint/temperature changes before and after candidate freeze do not conflict or add command diversity. Repeat corroboration, normalized basis membership, full confirmation, and EOF validation retain their requirements. Guided thermostat recovery resumes without losing evidence. |
| Compatibility | Legacy corpus, existing saved profiles, Learning ID round trips, and unsupported-frame diagnostics remain valid. Stored/imported extended profiles use the same fixed manual template after validation. |
| Generated command | Newly derived profiles clear the units flag, use W6 `0x14`, W7 `0xC8`, W8 `0x00`, retain bits 3/7, use manual control bits, and generate correct W9/W10 for several control states. |
| Temperature units | Units or setpoint changes neither restart learning nor discard evidence. Units-only changes cannot advance HA prompts, helper targets, or command diversity. Imported Fahrenheit profiles normalize to the same Celsius manual template. |
| Packaged helper | The installed helper and HA derive the same profile from equivalent synthetic input. File/live/guided paths preserve stdout/stderr contracts, cancellation, and cleanup. |

Run focused protocol, learning, config-flow, and helper tests, followed by the
existing full regression suite and required style checks. Add tests proving the
two original failures before applying the implementation. Manual acceptance
should confirm automatic mode recovery on LilyGO and successful fireplace
control using the newly generated profile; protocol tests alone cannot prove
hardware acceptance.

Local validation completed with 1,856 repository tests passed, 34 skipped, and
one deselected. The isolated installed helper passed 115 tests and six HA
interoperability checks; its source archive passed all 33 checks through the
existing Make targets. Changed Python files passed Ruff and Black checks.
No deployment, RF transmission, or publication was performed.

Follow-up validation for fixed manual temperatures across profile loading and
Learning ID import/export passed 1,968 repository tests, with 34 skipped and one
deselected. The installed helper again passed 115 tests and six HA
interoperability checks; its source archive passed all 33 checks. Regression
coverage includes a 21°C imported setpoint, Fahrenheit inputs, stable IDs across
temperature variants, retained identity flags, original ID validation before
normalization, and HA control using the fixed 20°C template. Original received
frames remain unchanged. No RF transmission or deployment was performed.
