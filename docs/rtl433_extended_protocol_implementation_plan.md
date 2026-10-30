# rtl_433 Extended Protocol Implementation Plan

Status: offline implementation and software validation complete. Live radio
validation, publication, and upstream pull-request submission remain pending.

Add ten-word Proflame2 reception to rtl_433 while preserving seven-word legacy
support. Build a separate Learning ID helper in HACS-Proflame2 that consumes
rtl_433 JSON and reuses Proflame2's protocol and profile logic. The rtl_433
contribution must be useful independently of Home Assistant and Learning IDs.

## Ownership and Dependencies

### Remaining Integration Task List

The HACS-Proflame2 `rtl433-enhancements` branch delivers the standalone helper,
shared Python protocol boundary, HA Learning ID import, interoperability tests,
and documentation. It is intended to merge into `dev`, not become a permanent
parallel codeline. It does not own rtl_433 decoder development or firmware/RF
timing changes. The authoritative rtl_433 repository is `/srv/Tools/rtl_433`;
its session owns decoder edits, builds, decoder tests, and output handoff.

Existing checked phase tasks below record earlier implementation. They do not
mean that the remaining integration and live acceptance tasks here have passed.

1. [x] **T1 Confirm the cross-session contract.** Record the decoder commit,
   actual build used to generate fixture output, JSON field names/types,
   variant markers, integrity labels, and unknown-pilot semantics. Keep Learning
   IDs remote-only format v1. Coordinate any helper CLI or shared protocol change
   before editing; request decoder/JSON changes from the rtl_433 owner.
2. [x] **T2 Obtain decoder-generated JSON fixtures.** The rtl_433 owner runs
   the real decoder on available representative legacy and extended captures
   and supplies its emitted JSON. Include at least one case per known captured
   remote and enough distinct commands/repeats per learnable remote to exercise
   the helper's confirmation gates. Record generating commit, command, input
   provenance, expected identity/protocol values, and whether replay used real
   recordings or derived signals. Identify remotes with insufficient learning
   evidence rather than inventing extra observations.
3. [x] **T3 Review fixture privacy and storage.** Store compact, reviewed JSON
   and a provenance manifest in a dedicated helper test-fixture directory. Do
   not check in IQ recordings, credentials, personal paths, host addresses, or
   unrelated metadata. Preserve protocol bytes necessary for real-remote
   regressions; keep source captures in their approved external/private storage.
4. [x] **T4 Test the installed helper against those fixtures.** Assert expected
   legacy/extended Learning IDs and decoded remote values, confirmation-gate
   behavior, file and pipe handling, and stdout/stderr/exit-status contracts.
   Retain existing negative tests for corruption, conflicting fields, mixed
   remotes/variants, insufficient evidence, and late contradictions. Normal
   helper tests consume committed JSON and require neither an SDR nor an
   rtl_433 executable or source checkout.
5. [x] **T5 Confirm helper completion policy and installation.** Review the
   existing file-mode preliminary results at Gates 1/2 versus live-mode Gate 3
   completion, progress guidance, and timeout defaults. Document the agreed
   behavior; coordinate any change with the helper owner. Verify clean wheel
   installation without HA or rtl_433 and provide reproducible source/wheel
   instructions without implying an unpublished PyPI package is available.
6. [x] **T6 Finish the HA import transition.** Complete the HA-only adapter and
   replace the old rtl_433 JSON-paste menu flow with Enter a Learning ID. Keep
   guided learning and Enter legacy values. Ask for enabled features and
   controller assignment in HA; do not encode those choices or RF band into the
   ID. Reject invalid IDs and duplicate controller assignments without sending
   RF. Review the currently uncommitted partial edits before extending them.
7. [x] **T7 Exercise the real HA configuration flow.** Feed helper-generated
   legacy and extended IDs through the actual forms, choose features/controller,
   create and reload entries, and verify reconstructed profiles and regenerated
   diagnostic IDs. Compare supported manual TX encodings using simulated
   backends, not RF transmission. Test invalid input, feature independence,
   unknown extended pilot, ownership conflicts, and unchanged guided/legacy
   setup behavior.
8. [x] **T8 Reconcile end-user documentation.** Update setup menus, translations,
   links, and the dedicated rtl_433 guide. Explain the helper/decoder separation,
   tested decoder build, installation, JSON collection, confirmation messages,
   and saving a reusable Learning ID. Keep the main setup guide short and link
   to detailed instructions; preserve useful old documentation links without
   continuing to recommend the removed paste UI.
9. [ ] **T9 Run offline acceptance.** Run the native invocation-owned Python
   test environment, formatting/lint checks, installed-package tests, and HA
   interoperability tests. The rtl_433 owner separately runs its decoder
   regression suite and reports results. Record commands, revisions, passes,
   skips, and gaps. Confirm test scratch files are cleaned on exit/interruption.
10. [ ] **T10 Perform live end-to-end acceptance.** On hardware-equipped test
    systems, receive native remote commands with the agreed rtl_433 build,
    generate a Learning ID, import it into HA, and verify the expected profile.
    Cover legacy and extended remotes where available; record unavailable
    hardware/remote coverage explicitly. Any fireplace-control test is a
    separate authorized hardware action, not an automated helper test.
11. [ ] **T11 Review for integration into dev.** Check scope, privacy, concurrent
    edits, and documentation consistency. Present validation evidence and
    remaining live limitations. Commit, push, create a PR, merge, or publish
    only when separately authorized; await required CI before merging.

Execution order: T1 precedes the decoder-fixture handoff T2/T3 and fixture tests
T4. T5 and HA work T6/T7 can proceed independently once the contract is frozen.
Documentation T8 and offline acceptance T9 precede live acceptance T10 and the
final integration review T11. Live hardware is not required for T1-T9.

### HA Integration Execution Evidence

- Decoder fixture generation used the clean linked `proflame2-extended` checkout
  of the authoritative rtl_433 repository at commit
  `13cae578111d92434a6c09212aae0afa0141a191`. The canonical checkout itself is
  still on its older investigation baseline; do not assume its executable has
  extended support. Neither decoder checkout was modified by this integration.
- `tools/rtl433_decoder_tests/export_helper_fixtures.py` compiled and invoked
  the decoder-owned C harness with ASan/UBSan in automatically cleaned scratch
  storage. Its JSON export contains four legacy observations from two remotes
  and 120 extended observations from two remotes. Provenance, input hashes,
  generating revision, and replay classification accompany the fixture in
  `tests/fixtures/rtl433_helper_decoder_output.json` and its companion Markdown.
  This was read-only fixture generation, not a decoder implementation change or
  a replacement for the decoder owner's full regression/pipeline suite.
- Installed-package tests validate every exported record and preserve its wire
  words. Real extended sequences use consistent observed manual-mode templates
  and existing repeats, without synthesizing new evidence. The available legacy
  captures lack sufficient commands/repeats and correctly produce no ID;
  synthetic legacy sequences cover successful collection and all three gates.
- The helper CLI and shared protocol implementation were not changed. File-mode
  Gates 1/2 remain usable preliminary results with explicit warnings; live mode
  requires Gate 3. Confirmation level is not encoded in an ID and HA cannot
  infer it. Documentation explains that distinction and retains verbose guidance
  with a 600-second collection example rather than changing the default policy.
- HA now imports through the dedicated adapter, with feature/controller choices
  remaining local. Main and failure menus no longer offer the JSON-paste flow.
  Guided learning, legacy-value entry, and internal legacy parsing regressions
  remain covered. Existing documentation links lead to the current workflow.
- Offline validation used a fresh native Python environment with cleanup on
  exit, rather than the shared architecture-dependent virtual environment:
  **1,811 repository tests passed, 34 skipped, one deselected; 46 installed-helper
  tests passed; six HA interoperability tests passed.** The latter include
  helper-generated IDs from both real extended remotes through the actual HA
  setup forms, runtime construction, manual encoding comparisons, and reload.
  Installed-helper cases skipped by the ordinary suite ran separately against
  the clean wheel. Ruff passed and Black accepted all 189 Python files.
- T9's HACS-side checks are complete. A fresh full rtl_433 regression/pipeline
  report remains owned by the decoder session; its earlier reported results
  below are not presented as a new run here. T10 live end-to-end collection is
  still pending. No RF was transmitted, firmware deployed, commits created,
  branches merged, or changes pushed during this implementation.
- Follow-up live-test setup uses `tools/learning_id/run.sh` instead of a forgotten
  persistent virtual environment. Each invocation stages packaging sources and
  installs in an owned `/tmp` directory; normal completion, errors, SIGINT, and
  SIGTERM clean it up. Capture files and IDs remain caller-owned persistent
  artifacts. Six focused runner tests passed, as did real temporary installation
  with `--help` and captured extended JSON input. No runner scratch directories
  remained after those checks. SIGKILL/power loss cannot execute cleanup.
- Live usability testing then exposed a moving confirmation target: late required
  command variation could consume more than three matches at Gate 1 and demand
  another four. Gates now use fixed totals of 3/5/7 qualified commands, while
  checking every observation against the candidate from the start. Regressions
  cover late variation and the reported repeated Light/Fan sequence.
- Live input is now guided by default using a controlling terminal separate from
  the JSON pipe. Preparation waits for the operator to turn the fireplace OFF;
  individual Up/Down targets are computed from received levels. Wrong controls
  and overshoots do not advance a step. Confirmation ends with a received Power
  OFF command before printing the ID. `--unguided` preserves automation;
  saved-file processing is unchanged. No protocol/ID schema was changed.
- Follow-up validation: **72 installed-helper tests passed**, including 20 real
  PTY/JSON-pipe guided tests; **six HA interoperability tests passed**; **1,817
  repository tests passed, 34 skipped, one deselected**. Ruff and Black passed.
  The operator reported that an unguided live extended ID matched the known
  profile ID. The operator subsequently confirmed that the guided live extended
  flow also produced the same ID. No real Learning ID is stored in this plan.
- Usability follow-up groups each control's changes into sweeps from 1 through
  4 and back to 1, yielding seven repeatable combinations without constant mode
  changes. Blank lines separate prompts and results. New managed reception via
  `run.sh --rf-band 315|433 [--rtl433 executable]` starts and stops its own
  rtl_433 child. External shell pipelines remain supported but their upstream
  receiver is not owned or killed by the helper. This distinction explains the
  apparent hang after an ID when the external receiver is quiet.
- Follow-up checks passed: **81 installed-helper tests**, including owned-radio
  success/failure/timeout/cancellation checks; **six HA interoperability tests**;
  **1,819 repository tests, 34 skipped, one deselected**. Runner dispatch covers
  both `--rf-band 315` and `--rf-band=433`. Ruff and Black passed. The grouped
  sequence and managed receiver still need an operator live-hardware run.
- Managed reception now checks receiver capability before opening the SDR:
  `-c 0 -R 207 -y <synthetic PCM> -F json` must produce compatible legacy,
  complete extended, and 259-bit extended JSON with the expected wire words.
  Version strings are informational, not the acceptance criterion. Incompatible
  or missing executables prompt for another path; noninteractive `--unguided`
  runs fail with a `--rtl433` instruction. These checks require no hardware and
  disable core dumps for obsolete decoders that might crash on test input.
- Receiver stderr is drained concurrently into a bounded 32-KiB in-memory tail,
  rather than interleaved with prompts. It is displayed on capture failure, not
  routine success/cancellation. The accepted receiver version appears once
  before preparation. No persistent log or machine-specific path is stored.
- Validation passed: **87 installed-helper tests**, **six HA interoperability
  tests**, and **1,819 repository tests (34 skipped, one deselected)**, plus Ruff
  and Black. Capability tests ran against a temporary build of the actual C
  decoder, with SDR drivers disabled, from an archived decoder checkout revision.
  Neither decoder checkout was modified. Tests include rejecting legacy-only
  and malformed decoder output before live startup, interactive replacement-path
  validation, and suppressing startup diagnostics without hiding error evidence.
- Operator testing confirmed the grouped Light/Fan prompts produce the expected
  Learning ID. The next revision simplifies control selection to a Light/Fan
  yes/no question, asking about Light alone only after No. It replaces the older
  seven-repeated-command policy with three repeated derivation combinations
  spanning required variation and two independent single-observation checks.
  Guided sweeps now use levels 1 through 3. Validation: **101 installed-helper
  tests passed, one optional decoder-binary test skipped; six HA interoperability
  tests passed**, plus Ruff, Black, and whitespace checks. Tests cover all three
  control paths, legacy/extended protocols, zero/high initial settings, automatic
  repeats, question validation/cancellation, and independent-check accounting.

| Responsibility | Owner | Dependency boundary |
| --- | --- | --- |
| Pulse detection and PCM slicing | Existing rtl_433 infrastructure | Retain existing timing and modulation settings unless replay evidence requires a change. |
| Bounded word parsing, frame classification, integrity and semantic output | rtl_433 `src/devices/proflame2.c` | Use the existing device registration and decoder APIs. No HA or Learning ID code. |
| Protocol models, extended integrity, profile derivation and Learning ID codec | HACS-Proflame2 pure Python protocol code | One implementation shared by the integration and standalone helper. |
| JSON input and Learning ID command line | HACS-Proflame2 `tools/learning_id/` | Read files or stdin. No rtl_433 source tree, build, shared library, or private debug output dependency. |
| HA Learning ID import and local feature/controller selection | HACS-Proflame2 integration | Decode remote-only IDs; do not launch rtl_433 or duplicate helper confirmation policy. |

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
  Emit extended `pilot=-1` to explicitly mean unknown. Once a capture-backed
  extended pilot mapping is established, update the decoder and consumers to
  report validated `0/1` values. The sentinel is not a mapped pilot state.
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
| `pilot` | Existing established `0/1` mapping | Integer `-1` means unknown until its meaning is established. |
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

### Human-Readable Output Layout

The original control rows define the column starts: 0, 25, 51, and 77 characters
from the left margin. Keep these sections and column anchors for both variants:

- Header: `time` in column 1 and `Variant` in column 3; `model` in column 1 and
  `Id` in column 3 on the next line.
- Command/integrity bytes: legacy `Cmd1`, `Cmd2`, `Err1`, `Err2` occupy columns
  1-4. Extended `Cmd1`, `Cmd2`, `W6`, `W7` occupy columns 1-4, followed by
  `W8`, `W9`, `W10` in columns 1-3 on the next line.
- Controls: `Pilot`, `Light`, `Thermostat`, `Power` occupy columns 1-4;
  `Front`, `Fan`, `Aux`, `Flame` occupy columns 1-4 on the next line.
  Extended `Pilot` is `-1` (unknown), preserving this same layout.
- Final line: `Integrity` in column 1 and `Raw words` in column 3, aligned
  exactly beneath `Aux`.

This layout applies to the pretty key/value formatter for explicitly marked
Proflame2 records. Other devices retain their normal formatting. JSON/CSV field
names, types, and values remain unchanged except for the added extended
`pilot=-1` field. Consumers accept prior extended records that omit `pilot`,
and accept the integer `-1` sentinel when present. They must not interpret W4
bit 7 as a known pilot state or accept `0/1` until the mapping is validated.

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
- [x] **H4 Keep feature selection in HA.** Learning IDs contain remote identity
  and learned protocol values only. The helper has no profile-feature argument
  or feature-enablement prompt. Guided capture may ask which remote controls are
  available solely to choose its capture sequence. HA asks which features to
  enable during import. Do not embed
  feature choices or installation settings such as refresh period, backend, or
  RF band in the Learning ID. Unreleased schema revisions keep format v1.
- [x] **H5 Generate using the shared codec.** Pass the derived profile to the
  existing `encode_learning_id` implementation. Encode and decode the resulting
  ID and verify that the learned remote fields survive unchanged. Apply the
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

Each command used for initial derivation must have at least two validated matching
observations. The two independent checks need one observation each. For legacy frames, command/error bytes must agree. For extended
frames, commands and learned fixed template fields must agree; telemetry may
change, but both integrity lanes must validate on every observation. Automatic
remote retransmissions may provide the matching observation. The helper cannot
infer a second physical button press from JSON and must not claim to do so.
Frames for the same command that contradict the candidate are failures, not
additional commands or matching repeats.

| Gate | Minimum evidence | Meaning |
| --- | --- | --- |
| 1: Profile derived | Three distinct commands, each observed at least twice, plus the variant requirements below. | A consistent profile can be generated; freeze it for subsequent checks. |
| 2: Profile corroborated | One distinct command outside the frozen derivation set agrees with the profile. | An independent command supports the profile; full confirmation is incomplete. |
| 3: Profile confirmed | A second distinct command outside the derivation set agrees with the profile: normally five combinations total. | All required capture-evidence checks passed. |

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
distinct-command count. Freeze the actual repeated derivation set at Gate 1.
Gates 2 and 3 must use two other command pairs, not commands used in derivation.
The single-observation candidate is checked from the beginning, so previously
received commands outside the derivation set may serve as independent checks.
Multiple gates may therefore become satisfied on the same input. If required
variation arrives late, choose three repeated commands spanning that variation
for the derivation set; other combinations remain independent checks.

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
- **Live mode:** Guide the operator by default through terminal preparation,
  control choice, and individual button presses, confirming each requested
  value from RF JSON. Preparation has no action countdown; `--timeout` limits
  each requested action. After Gate 3, request and receive Power OFF before
  printing the ID. `--unguided` instead collects until Gate 3 with a total
  collection timeout, then prints exactly one ID. EOF, timeout, cancellation,
  or input limits before completion produce no ID. A contradiction fails the
  collection rather than resetting the candidate and continuing silently.

There is no `--allow-unconfirmed` option. File mode provides the partial-evidence
result after full-input validation; live mode requires Gate 3.

### User Messages

Use familiar words such as "commands", "changes", and "received again" in
user-facing messages. Avoid protocol terms such as "states" and "receptions".
All progress, guidance, warnings, and confirmation explanations go to stderr;
stdout is reserved for the single resulting Learning ID. Do not echo an ID or
full captured records in diagnostics.

Guided live prompts do not require `-v`. Startup explicitly waits for the
operator to turn the fireplace OFF and press Return, then asks whether the
remote adjusts both Light and Fan (Return for Yes, n for No). Only after No,
ask whether Light is available to select Light/Flame or Power/Flame. JSON input and keyboard input
remain separate. After preparation, do not require Return after each button:

```text
Press Power ON once. Waiting for its received command...
Received: Power ON.
Select Light on the remote. Press DOWN ONCE; the remote should show 2 bars. Waiting for receipt...
Received: Light 2.
```

Wrong control/level output should explain that the requested setting is still
pending and repeat the correct direction, including reversal after an overshoot.
The helper must not claim a fireplace acknowledgement. Keep the plausible-ID
announcement once, then report final confirmation and request Power OFF.

For explicit unguided collection with `-v`, print a single progress line only
when it changes; do not repeat received-command counters or gate chatter:

```text
2 more needed before your Learning ID is ready.
A plausible Learning ID has been created. Now we need to confirm it.
2 new command checks needed to confirm your Learning ID.
Your Learning ID is ready.
```

File-mode final messages must explain the labels, even without `-v`. Use actual
counts where applicable; the following are examples at the gate boundaries.

Gate 1:

```text
Preliminary: A Learning ID was generated from 3 different commands.
The commands agree, and each was received at least twice, but this file
Two additional different commands are needed to complete confirmation.
```

Gate 2:

```text
Confirmation incomplete: The repeated derivation commands agree with this
Learning ID, and one additional different command passed validation.
One more different command is needed to complete confirmation.
```

Gate 3:

```text
Confirmed: 3 different derivation commands were received at least twice,
and at least two additional different commands passed validation. The entire file was checked,
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
rtl_433 -R 207 -f 315M -F json | proflame2-learning-id
cat capture.json | proflame2-learning-id --input-mode file
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
  HA-side feature selection, sufficient capture sequences, invalid-input behavior, and
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
| Helper evidence gates | At least three repeated derivation combinations plus two independent single-observation checks for legacy and extended; required command variation; automatic retransmissions; telemetry changes do not inflate counts; derivation combinations cannot also count as independent checks; contradictions fail rather than reset it. |
| Helper input modes | Named files and regular-file stdin default to file mode; pipes default to live mode; explicit override; file mode validates through EOF including contradictions after Gate 3; file Gates 1/2 emit explained partial results; live EOF/timeout before Gate 3 emits no ID. |
| Helper user messages | `--verbose` and `-v` report accurate counts, missing repeats, and required variation; live remote guidance uses plain language; file mode never asks for button presses; final file confirmation labels always include explanations. |
| Helper output | Codec vectors; IDs independent of HA feature choices; stdout contains only the resulting ID; invalid inputs and diagnostics do not echo IDs or full captured records. |
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

Upstream PR #3740 was merged on October 27, 2026, as
`f35183aaf1d2d905d422fd70bcae4eb7349d6186`. It fixes the same write overflow
by stopping after seven words and ignoring the rest of the row. Our complete-row
classification remains necessary for ten-word support and rejection of damaged
extended tails. Do not present the bounds fix as a new upstream security fix.
The decoder branch is now rebased onto that commit, verified as upstream master
on October 30, 2026. No action on the merged PR itself is required.

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
- The decoder runner, frozen JSON examples, capture vectors, and output-layout
  checks belong to rtl_433 under `tests/proflame2/`. CTest registers the corpus
  and detector replay when Python 3.10+ is available; the core C test remains
  available without Python. HACS retains only a compatibility entry point.
- After rebasing, rtl_433 Debug CMake/CTest passes all 18 tests, including its style check.
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
passes when skipped. User-provided live ARM/RTL-SDR output demonstrates extended
power and light reception. Full live helper collection and broader hardware
validation remain pending.
