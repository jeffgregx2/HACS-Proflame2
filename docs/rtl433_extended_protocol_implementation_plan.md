# rtl_433 Extended Protocol Implementation Plan

Status: offline implementation and software validation complete. Live radio
validation, publication, and upstream pull-request submission remain pending.

Add ten-word Proflame2 reception to rtl_433 while preserving seven-word legacy
support. Build a separate Learning ID helper in HACS-Proflame2 that consumes
rtl_433 JSON and reuses Proflame2's protocol and profile logic. The rtl_433
contribution must be useful independently of Home Assistant and Learning IDs.

## Ownership and Dependencies

| Responsibility | Owner | Dependency boundary |
| --- | --- | --- |
| Pulse detection and PCM slicing | Existing rtl_433 infrastructure | Retain existing timing and modulation settings unless replay evidence requires a change. |
| Bounded word parsing, frame classification, integrity and semantic output | rtl_433 `src/devices/proflame2.c` | Use the existing device registration and decoder APIs. No HA or Learning ID code. |
| Protocol models, extended integrity, profile derivation and Learning ID codec | HACS-Proflame2 pure Python protocol code | One implementation shared by the integration and standalone helper. |
| JSON input, feature selection and Learning ID command line | HACS-Proflame2 `tools/learning_id/` | Read files or stdin. No rtl_433 source tree, build, shared library, or private debug output dependency. |
| HA import forms and existing rtl_433-assisted learning | HACS-Proflame2 integration | Consume normalized frames/profiles through the shared Python boundary. |

The decoder remains a small ordered workflow: parse words, classify the complete
frame, validate the selected variant, then format output. The helper similarly
reads records, normalizes frames, derives one profile, and encodes one Learning
ID. Input parsing must not send RF, change HA state, or silently create a profile.

## Baseline and Evidence

The reviewed decoder has an unbounded word loop writing into a seven-byte
caller buffer. AddressSanitizer reproduced a stack-buffer overflow using a
captured extended frame. This defect must be fixed before enabling extended
output.

The available extended corpus contains 120 accepted capture rows across two
remote identifiers, plus malformed captures that must remain rejected. All 120
accepted rows contain 259 PCM bits. With adequate output capacity and the final
Manchester low bit present, the existing C word decoder reproduces all ten
recorded words. This establishes reuse of the word encoding; it does not yet
establish a safe complete-frame decoder or live SDR reception.

The existing waveform adapter runs rtl_433's pulse detector and subsequently
uses Python frame decoding. Its extended tests are not acceptance tests for
rtl_433's C Proflame2 device decoder.

## Phase 0 Preserve Work and Freeze the Contract

- [x] **B1 Record both baselines.** Record repository commits and the existing
  changes before creating external development worktrees. Preserve the local
  rtl_433 diagnostic modifications and the unrelated HACS-Proflame2 edits.
  Build and test scratch data belongs in task-owned temporary directories.
- [x] **B2 Establish decoder tests.** Add a reproducible test that invokes the
  actual C decoder on legacy rows and reproduces the extended overflow under
  AddressSanitizer. Include complete rows, terminal-bit cases, and malformed
  extended frames that could otherwise be mistaken for legacy frames.
- [x] **B3 Freeze JSON examples.** Establish golden legacy and extended output
  examples, numeric types, variant names, wire-word ordering, and integrity
  labels before the independent helper consumes them. Use synthetic examples
  for public documentation and retain provenance for capture-derived tests.

Exit gate: both projects have an explicit baseline, the memory defect is
reproducible, and the output contract can be reviewed without helper code.

## Phase 1 Implement the rtl_433 Decoder

### Function Boundaries

Keep related static functions in `proflame2.c`; this change does not justify a
generic protocol framework or multiple new utility modules. Use a small result
structure containing decoded words, word count, consumed bit position, frame
variant, and the framing/rejection result.

Separate the following responsibilities:

1. Word parsing validates synchronization, Manchester pairs, first-word padding,
   per-word parity, and end guards within explicit input and output bounds.
2. Frame classification proves a seven-word or ten-word packet boundary.
3. Extended integrity validation computes both alternating lanes.
4. Output formatting maps the selected variant to rtl_433 fields.

The public decoder callback orchestrates those functions, chooses a valid row,
and uses rtl_433's normal output and decode-result conventions. Do not duplicate
the parsing loop inside legacy and extended branches.

### Decoder Tasks

- [x] **D1 Bound parsing.** Pass output capacity explicitly and check it before
  every write. Bound bit reads before arithmetic or Manchester decoding. Return
  a distinct malformed/oversized result rather than a successful shorter frame
  when capacity is exhausted.
- [x] **D2 Classify whole frames.** Accept seven or ten words only with a valid
  boundary. Account for low bits retained by the PCM slicer at the row end;
  do not classify solely from total row length or the count decoded before an
  error. Eight-, nine-, and eleven-word packets are invalid. An invalid eighth
  word must not turn a damaged extended frame into a legacy success.
- [x] **D3 Handle the terminal bit narrowly.** Match Proflame2's known missing
  final low half of the final end-guard symbol only at the physical row end.
  All data, padding, parity, and other guard checks must still pass. Do not
  repair internal Manchester errors, fill missing payload bits, mutate the
  shared input buffer, or broaden acceptance of malformed 260-bit captures.
- [x] **D4 Validate extended integrity.** Port the existing serial-inclusive
  calculation, with byte-width arithmetic and no remote-specific constants:

  ```text
  T(x) = legacy Err transform with C = D = 0
  lane: start at zero; accumulator = T(accumulator XOR next_word)
  W9  = lane(W1, W3, W5, W7)
  W10 = lane(W2, W4, W6, W8)
  ```

  Require both lanes to match. Do not use the historical reduced-lane constants
  `0x23/0x65`, and do not apply learned legacy C/D derivation to extended words.
- [x] **D5 Map semantics by variant.** Reuse established power, light,
  thermostat, flame, fan, auxiliary, and burner mappings. Preserve power and
  flame zero independently. Interpret the pilot field only where established
  for that variant; extended W4 bit 7 must not be presented as legacy pilot.
  Retain W6-W8 as raw fields; additional temperature/setpoint output requires
  its own capture-backed mapping and tests.
- [x] **D6 Emit the agreed output.** Update `output_fields`, source protocol
  documentation, and JSON/CSV tests together. Return the appropriate rtl_433
  rejection category for length, framing, or integrity failures. Preserve the
  existing decoder registration so existing `-R` use remains valid.
- [x] **D7 Keep diagnostics separate.** Preserve the investigation checkout's
  existing debug work. The production contribution must not include the local
  build suffix, debug-on-by-default changes, or project-specific PCM logging
  in the shared slicer. Use normal verbosity-controlled decoder diagnostics.

### Proposed Output Contract

Keep the existing `model` and `id` representation. Add
`protocol_variant=legacy_7_word` or `extended_10_word`. Fields with established
shared meanings retain their current names and types.

| Field group | Legacy | Extended |
| --- | --- | --- |
| `cmd1`, `cmd2` | Existing command bytes | W4 and W5 command bytes |
| `err1`, `err2` | Existing legacy integrity bytes | Omit; W6/W7 are not legacy integrity bytes. |
| `w6`, `w7`, `w8`, `w9`, `w10` | Omit | Preserve each received byte. |
| `raw_words` | Complete seven-byte wire sequence | Complete ten-byte wire sequence |
| `pilot` | Existing established mapping | Omit until its meaning is established. |
| `mic` | Report the parity check actually performed. | Use rtl_433's accepted label for the validated custom integrity calculation. |

Use a fixed-width hexadecimal string for `raw_words` in on-air order. Use
integer values for individual byte fields in JSON, with hexadecimal formatting
for human-readable output. The helper treats JSON numbers as numbers, not as
hexadecimal text. Where raw and expanded fields coexist, reject disagreement.

Legacy currently emits `mic=CHECKSUM` without validating learned C/D bytes.
Correct that label explicitly to `PARITY`, with a documented compatibility
note. Select the upstream-appropriate extended `mic` label during B3; retain
the exact custom algorithm in the decoder documentation. Do not invent a CRC
or claim legacy command-integrity validation that was not performed.

Exit gate: the C decoder passes the captured corpus, malformed-input cases,
sanitizers, and rtl_433's applicable tests without shared slicer changes.

## Phase 2 Expose Shared Python Profile Logic

- [x] **P1 Establish an ordinary standalone package boundary.** The current
  protocol files are largely pure Python, but importing them through the HA
  integration package executes its HA-dependent initializer. Provide normal
  packaging for a standalone `proflame2_protocol` import using the existing
  protocol source directory as the package source. Keep the integration's
  existing import paths working. Do not copy the codec, edit `sys.path` at
  runtime, load source files manually, or fabricate `sys.modules` packages.
- [x] **P2 Remove the protocol dependency on integration constants.** Move the
  protocol-variant constants into the pure protocol package and re-export them
  from the existing integration constants module. Keep runtime HA/RF imports
  outside the standalone protocol dependency graph. Verify wheel installation
  as well as source-checkout development.
- [x] **P3 Extract profile derivation policy.** Add a focused pure module under
  the protocol package for deriving a `RemoteProfile` from validated frames and
  explicit `FireplaceFeatures`. Reuse it from existing HA learning and the new
  helper. Keep HA session handling, persistence, controller ownership, and UI
  messages in the integration adapters.
- [x] **P4 Preserve learning requirements.** Use the existing legacy C/D
  algorithm and observed variation requirements. For extended profiles,
  validate both integrity lanes, select manual-mode observations, require
  consistent `W4 & 0x8C`, W6, and W8 template fields, and retain the existing
  manual W7 value `0xC8`. Reject mixed identities, mixed variants, contradictory
  templates, and insufficient evidence. Repeated copies alone must not count
  as distinct state observations. Lock these rules in tests before extraction.
  Keep the helper's additional confirmation gates separate from HA session
  completion policy; extracting shared logic must not silently raise existing
  HA learning thresholds.

Exit gate: the existing integration retains its behavior, and an installed
standalone protocol package imports and runs without Home Assistant or rtl_433.

## Phase 3 Build the Independent Learning ID Helper

- [x] **H1 Add a small command-line adapter.** Implement
  `proflame2-learning-id` in HACS-Proflame2 under `tools/learning_id/`, packaged
  with the shared protocol library. Keep JSON normalization separate from the
  CLI. Use a typed record for validated frames and explicit derivation results.
- [x] **H2 Read JSON records.** Accept newline-delimited rtl_433 JSON from a
  named file or stdin. Ignore clearly unrelated device records; report malformed
  Proflame2 records without echoing their contents. Bound line length, input
  volume, and live collection duration. Diagnostics go to stderr; successful
  default stdout contains exactly one Learning ID.
- [x] **H3 Enforce variant semantics.** Validate raw words, fields, byte ranges,
  identity, frame length, parity provenance where supplied, and extended
  integrity before profile derivation. Accept existing legacy JSON without a
  variant marker only through an explicit legacy compatibility path. Never
  route an explicitly extended record into legacy C/D inference.
- [x] **H4 Obtain supported features explicitly.** Capabilities cannot be
  inferred from a control being OFF. Accept a declared feature list for
  unattended use, or ask the user in interactive mode. Reuse integration
  capability names and avoid prompts when stdin carries piped JSON. Do not
  embed installation settings such as refresh period, backend, or RF band in
  the Learning ID.
- [x] **H5 Generate using the shared codec.** Pass the derived profile to the
  existing `encode_learning_id` implementation. Encode and decode the resulting
  ID and verify that the complete profile survives unchanged. Apply the
  evidence gates and mode-specific completion rules below. Return a nonzero
  exit status when no ID can be generated because input is invalid,
  contradictory, ambiguous, or below Gate 1, or when live confirmation cannot
  complete. Do not transmit, launch rtl_433 implicitly, change HA configuration,
  or save raw inputs by default.
- [x] **H6 Track deterministic confirmation gates.** Count distinct commands
  and matching repeats separately, freeze the candidate at Gate 1, and validate
  additional commands against that candidate. Do not silently replace a
  contradicted candidate or discard conflicting Proflame2 records.
- [x] **H7 Explain progress in plain language.** Add `--verbose` / `-v` for
  evidence progress and, in live mode, remote-use guidance. Always explain the
  final file-mode confirmation level on stderr, even without verbosity.

### Evidence and Confirmation Gates

A distinct command state is a unique `(cmd1, cmd2)` pair for legacy frames or
`(W4, W5)` pair for extended frames. Telemetry-only changes, including extended
W7 temperature changes, do not count as additional commands. All contributing
frames must belong to the same remote identity and protocol variant.

Each command counted toward a gate must have at least two validated matching
observations. For legacy frames, command/error bytes must agree. For extended
frames, commands and learned fixed template fields must agree; telemetry may
change, but both integrity lanes must validate on every observation. Automatic
remote retransmissions may provide the matching observation. The helper cannot
infer a second physical button press from JSON and must not claim to do so.
Frames for the same command that contradict the candidate are failures, not
additional commands or matching repeats.

| Gate | Minimum evidence | Meaning |
| --- | --- | --- |
| 1: Profile derived | Three distinct commands, each observed at least twice, plus the variant requirements below. | A consistent profile can be generated; freeze it for subsequent checks. |
| 2: Profile corroborated | Two additional distinct commands agree with the frozen profile, each observed at least twice: five total. | Additional independent commands support the profile; full confirmation is incomplete. |
| 3: Profile confirmed | Two further distinct commands agree with the frozen profile, each observed at least twice: seven total. | All required capture-evidence checks passed. |

At Gate 1, legacy evidence must contain at least two distinct `cmd1` values and
two distinct `cmd2` values. Every observed command/error pair must yield the
same corresponding C/D values. In the current legacy algorithm, a single pair
uniquely determines its C/D value; additional commands cross-check consistency
rather than resolving mathematical ambiguity.

Extended evidence must include at least two manual-mode commands with different
W5 values. Validate both integrity lanes for every frame and require consistent
manual-mode `W4 & 0x8C`, W6, and W8 template fields. Keep manual transmit W7 at
`0xC8`. The extended integrity algorithm is known; learning derives and confirms
the profile template rather than discovering a new integrity algorithm.

Validate all relevant observations, including commands that have not yet
received a matching repeat. A contradiction prevents output; it must not merely
delay a gate. Matching repeats strengthen evidence but cannot increase the
distinct-command count. Gates 2 and 3 must use additional command pairs beyond
those counted at the preceding gate.

These thresholds are deterministic acceptance policy, not numerical confidence
probabilities. "Confirmed" means the documented capture-evidence policy passed;
it does not claim that operation with a physical fireplace has been tested.

### Input Modes and Completion

Provide `--input-mode file|live`. A named file defaults to file mode. Standard
input identified as a regular file also defaults to file mode. Pipes default
to live mode, with an explicit override for saved data supplied through a pipe.
Do not try to infer whether a pipe originates from `cat` or a running rtl_433.

- **File mode:** Process the entire input through EOF before printing an ID.
  Below Gate 1, print no ID and explain the missing evidence. At Gates 1, 2,
  or 3, print exactly one ID on stdout and explain its confirmation level on
  stderr. Successful generation at Gates 1 or 2 is permitted in file mode;
  incomplete confirmation alone is not an error. Any contradictory or invalid
  relevant input prevents ID output, even if an earlier prefix reached Gate 3.
  Do not offer button-press guidance while processing a file.
- **Live mode:** Continue collecting until Gate 3, then print exactly one ID
  and finish successfully. Verbose messages describe progress and guide remote
  use. EOF, timeout, or input limits before Gate 3 produce no ID and an
  explanation with a nonzero exit status. A contradiction fails the collection
  rather than resetting the candidate and continuing silently.

There is no `--allow-unconfirmed` option. File mode provides the partial-evidence
result after full-input validation; live mode requires Gate 3.

### User Messages

Use familiar words such as "commands", "changes", and "received again" in
user-facing messages. Avoid protocol terms such as "states" and "receptions".
All progress, guidance, warnings, and confirmation explanations go to stderr;
stdout is reserved for the single resulting Learning ID. Do not echo an ID or
full captured records in diagnostics.

With `--verbose` / `-v`, report actual counts and specific missing requirements,
including commands still needing a repeat. Live-mode guidance should read:

```text
Use your fireplace remote to change several settings, such as Light Up or Down
and Fan Up or Down. Pause after each change to give the remote time to send it
again. If a change still needs confirmation, make that same change again.
```

Examples of verbose live progress and gate messages:

```text
3 different commands received.
4 more needed before your Learning ID is ready.
2 commands still need to be received again.

Make another change using your remote, then pause.

Step 1 of 3: Your remote has been identified. Keep making different changes.
Step 2 of 3: The additional commands match. A few more checks are needed.
Step 3 of 3: All checks passed. Your Learning ID is ready.
```

File-mode final messages must explain the labels, even without `-v`. Use actual
counts where applicable; the following are examples at the gate boundaries.

Gate 1:

```text
Preliminary: A Learning ID was generated from 3 different commands.
The commands agree, and each was received at least twice, but this file
does not contain enough different commands to complete confirmation.
```

Gate 2:

```text
Confirmation incomplete: At least 5 different commands agree with this
Learning ID, and each was received at least twice.
Additional checks passed, but full confirmation requires 7 different
commands, each received at least twice.
```

Gate 3:

```text
Confirmed: At least 7 different commands agree with this Learning ID,
and each was received at least twice. The entire file was checked,
and no conflicting commands were found.
All required checks passed.
```

Below Gate 1:

```text
No Learning ID was generated.
This file does not contain enough different commands with matching repeats.
```

Contradictory evidence:

```text
No Learning ID was generated.
Some commands in this file disagree about your remote's settings.
The file could contain commands from different remotes or damaged recordings.
```

When insufficient command variation or another requirement prevents Gate 1,
explain that specific missing requirement instead of implying that repeats
alone would resolve it. Malformed input should receive its own clear error;
do not describe every validation failure as a disagreement between commands.

The intended unattended workflow is a normal process pipeline:

```text
rtl_433 -R 207 -f 315M -F json | proflame2-learning-id --features fan,light
cat capture.json | proflame2-learning-id --input-mode file --features fan,light
```

The frequency remains an rtl_433/controller choice. The helper must also work
against a saved JSON file with no SDR, rtl_433 installation, or HA instance.
Handle a closed input stream and normal upstream pipe termination cleanly.

Exit gate: a clean environment can install the helper, read fixtures, and
produce importable legacy and extended Learning IDs without either codebase
being installed as a running service.

## Phase 4 Integrate Existing Consumers

- [x] **I1 Update rtl_433-assisted HA learning.** Extend the current legacy-only
  sample adapter to preserve variant and complete frames, then call the shared
  profile derivation. Retain compatibility with existing legacy pasted rows.
  Keep guided prompts and config-entry storage outside the protocol library.
- [x] **I2 Audit capture consumers.** Check collectors, comparison tools, and
  display parsers for assumptions about seven bytes or `err1/err2`. Support
  explicit extended records or reject them clearly. The helper must not depend
  on those tools' private logging or timing envelopes.
- [x] **I3 Document installation and use.** Describe separate installation,
  feature selection, sufficient capture sequences, invalid-input behavior, and
  the existing HA Learning ID import path. Clearly distinguish rtl_433 releases
  that support extended output from the current legacy-only decoder.

## Test Matrix

| Boundary | Required tests |
| --- | --- |
| C memory bounds | Captured extended overflow regression; maximum capacity; oversized rows; address and undefined-behavior sanitizers. |
| C framing | Legacy and extended complete rows; permitted terminal low-bit omission; incorrect sync, Manchester, padding, parity, guards, and gap boundaries; eight/nine/eleven words; invalid extended tail cannot fall back to legacy. |
| C integrity | Both extended identifiers and all 120 accepted raw rows; malformed fixture rows remain rejected; single-bit mutations across all ten words; identifier changes affect integrity. |
| C semantics and output | Exact wire words and fields; independent power/flame-zero values; no invented extended pilot value; legacy compatibility; JSON numeric types and CSV fields. |
| Actual rtl_433 pipeline | Offline replay through pulse detection, PCM slicing, and the real Proflame2 C decoder; silence/noise negatives; legacy regressions; repeated frames and row boundaries. |
| Pure Python derivation | Existing legacy and extended learning cases unchanged; stable template requirements; sufficient distinct observations; mixed identities/variants and invalid integrity rejected. |
| Standalone packaging | Build and install the helper/library into a clean environment without HA or rtl_433; no HA imports, source loading tricks, or duplicate codec implementations. |
| Helper input | File and pipe input; legacy compatibility; complete extended records; unknown models; malformed JSON; conflicting raw/expanded fields; bounds, timeout, EOF, and exit statuses. |
| Helper evidence gates | Legacy and extended gates at 3/5/7 distinct commands with matching repeats; required command variation; automatic retransmissions; telemetry changes do not inflate counts; additional commands validate the frozen candidate; contradictions fail rather than reset it. |
| Helper input modes | Named files and regular-file stdin default to file mode; pipes default to live mode; explicit override; file mode validates through EOF including contradictions after Gate 3; file Gates 1/2 emit explained partial results; live EOF/timeout before Gate 3 emits no ID. |
| Helper user messages | `--verbose` and `-v` report accurate counts, missing repeats, and required variation; live remote guidance uses plain language; file mode never asks for button presses; final file confirmation labels always include explanations. |
| Helper output | Codec vectors; explicit feature choices; stdout contains only the resulting ID; invalid inputs and diagnostics do not echo IDs or full captured records. |
| HA interoperability | Import each helper-generated ID, load the complete profile, and compare generated supported manual TX frames with the shared encoder expectations; refresh remains disabled by default. |

RMT-derived PCM replay and synthesized IQ validate deterministic software
behavior. They must be labeled as derived signals. Live RTL-SDR recordings are
required separately to establish reception from real hardware.

## Review and Upstream Delivery

Use separate reviewable commits for the bounded-parser fix, extended decoding
and output, shared Python derivation/packaging, helper CLI, and HA consumer
updates. Every commit should have focused tests; broaden to the applicable
project suites after integration. Run rtl_433's CMake/CTest and style checks,
the sanitizer tests, and the relevant HACS-Proflame2 Python suites and Ruff/Black
checks. Record unavailable external or hardware checks rather than counting
them as passes.

Prepare the rtl_433 pull request from a clean baseline. Include only generic
decoder changes, protocol documentation, and regression tests/capture references
using upstream conventions. No Learning ID schema, helper package, HA code,
local version suffix, or investigation instrumentation belongs in that PR.
Review capture attachments for incidental private metadata before submission.

Phase 0 and the C decoder work can proceed independently of the helper. The
helper depends on the agreed JSON contract and pure Python boundary; existing
consumer updates depend on both. Publication, pull-request creation, and live
radio testing remain separate explicitly authorized actions.

## Offline Implementation Evidence

- The historical extended-frame stack-buffer overflow was reproduced under
  AddressSanitizer against the preserved investigation decoder.
- The bounded C decoder accepts all 120 accepted extended capture rows across
  both fixture remotes and rejects the malformed capture rows. The C regression
  runner also checks legacy captures, JSON goldens, terminal-bit boundaries,
  malformed framing, oversized inputs, and all single-bit payload/integrity
  mutations with regenerated word parity.
- rtl_433 Debug CMake/CTest passes all nine tests, including its style check.
  Derived CU8 replay passes through its actual detector, unchanged PCM slicer,
  and C device decoder, with JSON/CSV output and silence/noise controls.
- The helper wheel installs and imports in a clean environment without HA or
  rtl_433. Installed-wheel tests cover file/live behavior, all three evidence
  gates, matching repeats, telemetry-only changes, late contradictions, JSON
  compatibility, input limits, timeouts, and stdout/stderr separation.
- HA interoperability checks import helper-generated legacy and extended IDs,
  load their profiles through the runtime using a simulated backend, compare
  supported manual command encodings, and verify refresh remains disabled.
- Existing HA learning keeps its prior completion policy. The helper's repeat
  requirements and extra confirmation gates are not applied to existing HA
  sessions. The legacy capture collector explicitly rejects extended records;
  the comparison tool preserves and compares their variant and raw fields.

Run these checks using a development virtual environment, with CMake on PATH:

```sh
python tools/rtl433_decoder_tests/run_tests.py --source /path/to/rtl_433 --build-pipeline
python tools/learning_id/validate_package.py --ha-interoperability
```

See the C runner README for the baseline overflow command. Run the normal Python
suite separately; optional firmware and hardware checks are not counted as
passes when skipped. Live SDR reception remains unvalidated.
