# Issue 15 Extended Protocol Reverse-Engineering Plan

## Purpose

This is the durable engineering plan for the SIT TMFSLA / `T99058402300`
remote reported in GitHub issue #15. It receives structurally valid
259-bit, ten-word Proflame2 frames, but it does not use the legacy seven-word
`Cmd1`, `Cmd2`, `Err1`, `Err2` layout used by the current learner and
transmitter.

The goal is to establish an evidence-backed extended-frame model that can be
decoded, learned, actively listened to, and transmitted safely. This plan is
intentionally resumable: record each completed task and save every generated
artifact in the locations defined below. Do not infer transmit behavior from a
small lookup table or enable partial transmit support.

## Scope And Safety Boundaries

In scope:

- Decode and validate the ten-word format from raw RMT PCM captures.
- Determine state-field semantics and the integrity relationship for the
  extended layout.
- Add an explicit extended protocol model, learning path, active listener,
  encoder, and regression coverage if the evidence supports them.

Out of scope until the model is validated:

- Sending arbitrary ten-word frames to a fireplace.
- Treating a 260-bit RMT row as a valid frame merely because adjacent 259-bit
  rows decode.
- Replacing the legacy seven-word protocol or its C/D algorithm.

The existing RMT path and its one-bit terminal end-guard handling are retained.
The prior FIFO path remains a diagnostic fallback. All extended-frame work must
fail closed: an unrecognized or ambiguous frame may be logged, but must not
create a learned profile or result in a transmission.

## Current Evidence

The issue reporter supplied a labelled continuous native-remote capture on
2026-09-05. Preserve the original issue comment and its raw `pcm_hex` lines as
the source evidence. The working facts are:

| Observation | Confidence | Consequence |
| --- | --- | --- |
| 91 captured 259-bit rows decode to one structurally valid ten-word frame with serial `08E905`. | Confirmed | RMT acquisition and the 259-bit terminal truncation support are working. |
| Three 260-bit rows do not decode under the bounded frame rules. | Confirmed | Keep them as rejected diagnostic artifacts. Do not broaden framing acceptance. |
| `W4` changes with power, light, thermostat mode, and pilot-related state. | Strong | It is a state/control field, not a legacy error byte. |
| `W5` changes with flame and AUX state. | Strong | It is a state/control field. |
| `W6` is `0x15` through `0x18` for requested thermostat setpoints 21 through 24 C. | Strong | It appears to carry the requested setpoint, not a measured fireplace temperature. |
| `W7` changed from `F5` to `FA` while the visible state was unchanged. | Incomplete | Its semantic role and persistence rules require dedicated experiments. |
| `W9` and `W10` vary with state-word changes. | Confirmed | The accepted alternating-word integrity construction validates all 91 raw-backed rows. |
| Mapping `W4/W5/W6/W7` to legacy `Cmd1/Cmd2/Err1/Err2` cannot derive stable C/D values. | Confirmed | The present extended-frame parser is not a semantic decoder for this remote. |

The current code represents `W4/W5/W6/W7` as the legacy frame fields and
retains `W8/W9/W10` only as extension bytes. That is an acquisition-compatible
interim representation, not a validated model of this remote.

## Artifact Inventory

Create and maintain the following repository artifacts. Do not edit the
original issue comment or substitute manually retyped data for raw capture
evidence.

| Artifact | Required contents | Owner task |
| --- | --- | --- |
| `docs/issue_15_extended_protocol_reverse_engineering_plan.md` | This plan, decision log, and task status. | All tasks |
| `tests/fixtures/issue_15_tmfsla_extended_capture.json` | Immutable transcription of raw RMT rows, labels, observed state, decoded words, bit length, and source issue-comment URL. | Task 1 |
| `tools/proflame_extended/analyze_issue_15.py` | Deterministic offline analysis; no Home Assistant, radio, or network dependencies. | Tasks 2-4 |
| `tools/proflame_extended/README.md` | Exact command lines, inputs, outputs, and interpretation limits. | Task 2 |
| `artifacts/issue_15_extended/analysis-*.json` | Versioned machine-readable analysis output and hypothesis scores. Do not commit transient exploratory output unless it is cited by a decision. | Tasks 3-5 |
| `docs/issue_15_extended_protocol_evidence.md` | Human-readable evidence table, accepted/rejected hypotheses, and capture provenance. | Tasks 4-5 |
| `docs/issue_15_extended_targeted_capture_protocol.md` | Narrow follow-up procedure derived from unresolved hypotheses. | Task 4 |
| `tests/test_issue_15_extended_protocol.py` | Decoder, integrity, and encoder regression tests using the fixture. | Tasks 1, 6, 7 |
| `docs/issue_15_extended_protocol_design.md` | Final protocol specification and implementation choices, only after validation. | Task 8 |

`artifacts/` is for durable, reviewable evidence only. Temporary notebooks,
ad-hoc scripts, decoded tables, and downloaded third-party source belong
outside the repository or must be promoted into one of the documented
artifacts with provenance.

## Execution Plan

### Phase 0: Establish A Reproducible Baseline

- [x] **0.1 Record repository state.** Save the commit SHA, issue URL, beta
  version, Python version, and exact analysis command in the evidence document.
  Artifact: `docs/issue_15_extended_protocol_evidence.md`.
- [x] **0.2 Verify raw decoding.** Run every supplied 259-bit `pcm_hex` value
  through `find_proflame_pcm_candidates()` and prove it produces exactly one
  candidate with serial `08E905`. Record each 260-bit value and its rejection
  reason.
  Artifact: fixture plus test.
- [x] **0.3 Freeze the legacy behavior.** Add a test showing that the current
  legacy C/D derivation fails for the captured ten-word state layout. This is a
  guard against accidentally claiming support before the extended algorithm is
  known.
  Artifact: `tests/test_issue_15_extended_protocol.py`.

Exit criterion: all raw evidence is reproducible from committed data, with no
network or hardware dependency.

### Phase 1: Normalize The Evidence

- [x] **1.1 Build the capture fixture.** Include capture identifier, raw PCM
  bit count and hex, frame format, the ten decoded words, test-step label,
  native-remote operation, state before/after, and a source reference.
- [x] **1.2 Define state vocabulary.** Use explicit fields for power, flame,
  light, AUX, fan, thermostat mode, requested setpoint, pilot mode, and any
  unknown fields. Unknown is valid data; do not coerce it to `false` or zero.
- [x] **1.3 Verify the reporter's decoded words independently.** Decode from
  the raw rows using project code and compare every word. Report mismatches;
  do not use a manually decoded row as the test oracle without this check.
- [x] **1.4 Classify anomalies.** Keep 260-bit captures in the fixture with
  their raw values and a `rejected` classification. Determine whether they are
  capture-boundary artifacts or a distinct protocol format only after separate
  evidence exists.

Exit criterion: the fixture is complete, reviewable, and can regenerate the
normalized state/word table deterministically.

### Phase 2: Establish Field Semantics

- [ ] **2.1 Produce a word-to-state delta matrix.** For every pair of adjacent
  controlled captures, identify the changed native state and XOR/byte deltas
  in `W4` through `W10`.
- [ ] **2.2 Confirm the directly observed mappings.** Test that the following
  candidate mappings explain every applicable capture: power/light/thermostat
  mode in `W4`; flame/AUX in `W5`; requested Celsius setpoint in `W6`.
- [ ] **2.3 Characterize `W7`.** Identify all observed values, transitions,
  persistence across power cycles, and correlation with pilot, thermostat,
  elapsed time, or remote configuration. Mark its semantics unknown if no
  controlled evidence distinguishes those hypotheses.
- [ ] **2.4 Characterize `W8`.** Confirm whether it is always zero across all
  valid frames. Do not call it reserved until captures from another state or
  another compatible remote support that conclusion.
- [ ] **2.5 Publish the normalized table.** Include only supported claims and
  link each claim to fixture capture IDs.
  Artifact: evidence document and analyzer JSON output.

Exit criterion: every state-field claim has capture references, and every
unresolved field is explicitly marked unknown.

### Phase 3: Reverse Engineer Integrity

- [x] **3.1 Test the legacy model conclusively.** Evaluate all plausible
  assignments of ten-word fields to the existing C/D function. Record the
  command/error pairs, candidate intersections, and failures. Do not stop at
  the present `W4/W5/W6/W7` assignment.
- [ ] **3.2 Test simple integrity families.** Evaluate documented checksum,
  CRC, parity, affine/XOR, nibble-permutation, and paired-field hypotheses over
  `W4` through `W8` for `W9` and `W10`. The analyzer must emit the full
  parameters and the list of matching/non-matching fixture IDs.
- [ ] **3.3 Measure model rank and ambiguity.** For any fitted affine model,
  report the independent input-bit rank. A model that matches only observed
  combinations but lacks enough independent inputs is diagnostic evidence, not
  an encoder algorithm.
- [x] **3.4 Search independent implementations.** Locate and inspect public
  source or protocol documentation for the precise remote family. Record URL,
  revision/license, relevant behavior, and whether it is independently
  corroborated. Do not copy code with incompatible licensing.
- [ ] **3.5 Write a hypothesis decision record.** For each rejected or retained
  hypothesis, state the evidence, counterexamples, and what next capture would
  distinguish remaining possibilities.

Exit criterion: either a fully specified integrity algorithm validates all
fixture rows, or the remaining ambiguity is converted into a targeted capture
request. No guessed encoder is allowed.

### Phase 4: Collect Only Missing Evidence

Do not request another broad capture matrix until Phase 3 identifies what is
missing. If needed, issue a short capture protocol with explicit starting state,
one press per row, two-second spacing, and raw RMT logging enabled. Preserve
the unedited enable-to-disable log block.

- [ ] **4.1 Exercise unobserved controls.** Capture all fan levels and any
  front/rear blower, split-flow, or other available remote controls.
- [ ] **4.2 Isolate pilot mode.** Capture CPI/IPI changes with identical
  power, flame, light, and thermostat state, then repeat one changed state in
  each pilot mode.
- [ ] **4.3 Isolate `W7`.** Capture the exact same visible state before and
  after each suspected triggering operation: power cycle, pilot toggle,
  thermostat mode change, long idle interval, and battery/restart only if
  practical. Repeat the test on a second day if time-dependent behavior is
  suspected.
- [ ] **4.4 Add cross-product rows.** For every candidate integrity input,
  capture enough pairwise combinations to distinguish an affine/checksum model
  from a state lookup. Prioritize changes that toggle previously unobserved
  bits, not repeated copies of already represented states.
- [ ] **4.5 Validate temperature encoding.** Capture the complete supported
  setpoint range and both unit systems, if the remote exposes them. Record
  displayed requested setpoint separately from ambient temperature.

Exit criterion: each requested row eliminates a named ambiguity from the Phase
3 decision record.

### Phase 5: Design The Extended Protocol Model

- [x] **5.1 Extend the immutable frame model.** It preserves all ten words and
  exposes their on-air order without changing legacy seven-word serialization.
- [x] **5.2 Add an extended state decoder.** It may publish only semantics
  proven by the evidence. Unsupported features must remain unavailable, not
  guessed.
- [x] **5.3 Add integrity validation.** Validate `W9/W10` using the specified
  algorithm before a frame is eligible for learning or active listening.
- [x] **5.4 Define profile persistence.** Persist the protocol variant and any
  extended constants separately from legacy C/D values. Existing profiles must
  remain compatible.
- [ ] **5.5 Define rejection telemetry.** Expose concise reason codes for bad
  extended integrity, unsupported state fields, and structural framing errors;
  preserve raw diagnostic metadata only in debug paths.

Exit criterion: a written design explains every changed public/internal data
contract and backward-compatibility behavior.

### Phase 6: Implement Decode, Learning, And Active Listening

- [x] **6.1 Implement structural parsing without changing legacy parsing.**
- [x] **6.2 Implement extended integrity validation.** Use pure functions with
  fixture-driven unit tests.
- [x] **6.3 Update guided learning.** It must recognize a supported extended
  remote, persist the correct variant, and continue to fail safely for unknown
  variants.
- [ ] **6.4 Update active listening.** Confirm repeated native-remote frames
  produce one correct Home Assistant state update without reverting current
  state from stale repeats.
- [x] **6.5 Retain diagnostic fallback.** Unsupported extended frames must
  continue to save labeled raw captures to `/config/proflame2_debug.log`.

Exit criterion: all fixture rows decode to the expected supported state or
explicit rejection; legacy and FIFO regression tests remain green.

### Phase 7: Implement And Prove Transmission

This phase starts only after Phases 3 and 6 have a validated integrity model.

- [x] **7.1 Implement a pure extended encoder.** Given a persisted extended
  profile and desired supported state, produce all ten words and an RF waveform.
- [ ] **7.2 Golden-vector tests.** Reproduce every fixture frame for its known
  state exactly, including both integrity bytes and word framing.
- [x] **7.3 Round-trip tests.** Encode, decode, and validate representative
  state transitions, boundary values, and unsupported-state rejection.
- [ ] **7.4 Hardware bench test.** Send one low-risk command at a time to the
  reporter's fireplace with the native remote available for recovery. Compare
  LilyGO TX output, fireplace result, Home Assistant state, and subsequent
  native-remote active listening.
- [ ] **7.5 Repetition and coexistence test.** Verify native remote immediately
  after HA control, HA control immediately after native remote, retries, and
  TX/RX arbitration.

Exit criterion: each supported control works on hardware, state remains
consistent, and no unsupported field can be transmitted.

### Phase 8: Release, Documentation, And Preservation

- [x] **8.1 Publish the beta protocol design.** Describe frame fields,
  integrity algorithm, known limitations, capture provenance, and licensing
  references.
- [x] **8.2 Update internal RMT documentation.** Replace the obsolete claim
  that the ten-word extension only carries diagnostics. Keep end-user
  documentation concise; expose only the RX path choice and supported behavior.
- [ ] **8.3 Add migration coverage.** Verify legacy learned profiles, existing
  LilyGO firmware configuration, and extended profiles across upgrades.
- [ ] **8.4 Run complete validation.** Run `make test`, Python lint/format
  checks, and ESPHome validation. Record exact results in the evidence document.
- [ ] **8.5 Preserve final evidence.** Commit fixture, analyzer, decision
  record, tests, and design together with the implementation. Link the issue
  comment and release in the final design document.

Exit criterion: the implementation is reproducible from committed artifacts,
with no unrecorded reverse-engineering assumptions.

## Decision Gates

| Gate | Required proof | Allowed outcome |
| --- | --- | --- |
| A: Raw capture | All accepted 259-bit rows decode independently; anomalous rows are preserved and rejected. | Begin semantic analysis. |
| B: Field model | State mappings are backed by controlled captures. | Add decode-only semantics. |
| C: Integrity model | One specified algorithm validates every relevant fixture row and has no unexplained fitting ambiguity. | Design encoder. |
| D: Encoder | Golden vectors and round trips pass. | Begin controlled hardware TX. |
| E: Hardware | Each supported control works and coexists with native-remote listening. | Release support. |

Failure at any gate returns to the prior evidence phase. It never falls back to
guessing constants, weakening structural validation, or transmitting a frame
that only appears plausible.

## Decision Log

Append concise entries as work proceeds. Each entry must include date, commit
SHA or artifact hash, task ID, conclusion, and next action.

| Date | Task | Evidence | Conclusion | Next action |
| --- | --- | --- | --- | --- |
| 2026-09-05 | Baseline | Issue #15 continuous A-I capture | The remote uses a valid 259-bit ten-word frame whose semantic/integrity layout differs from legacy C/D. | Execute Phase 0. |
| 2026-09-05 | 0.1-1.4 | Committed fixture, baseline evidence, and focused regression tests | All 91 normal rows independently decode to the reported words; the three 260-bit rows remain rejected; legacy C/D derivation fails as expected. | Begin field-delta and integrity analysis. |
| 2026-09-05 | 2.1, 3.3 (initial) | `analysis-baseline.json` regenerated from the fixture | `W4-W7` supply only 14 independent input-bit changes. Both tail bytes admit affine fits to the observed rows, but those fits are underdetermined and cannot drive an encoder. | Test named integrity families and isolate missing input bits. |
| 2026-09-05 | 2.1-2.2, 3.1-3.2 (initial) | Analyzer dependency search and direct C/D, checksum, and CRC-8 results | In this fixture `W9` is conflict-free only with `W5/W7`; `W10` is conflict-free only with `W4/W6`. Direct legacy C/D, XOR, sum, and common CRC-8 variants do not explain every row. | Characterize `W7`, test remaining multi-word integrity hypotheses, and define targeted captures. |
| 2026-09-05 | 3.1 | Direct and packed-nibble C/D analysis | No direct `W4-W8 -> W9/W10` assignment works. No legacy C/D calculation works when its input byte is built from any ordered pair of `W4-W8` nibbles. | Do not reuse legacy C/D for this variant; continue multi-word analysis. |
| 2026-09-05 | 3.2, 5.3, 6.2 | Alex's alternating-word transform, independently evaluated from raw PCM and implemented for receive | `T(value)=build_err_byte(value,0,0)` with final XORs `0x23` and `0x65` reproduces all 91 raw-backed fixture rows. The scanner rejects structurally valid ten-word rows unless both lanes validate. The two older manually transcribed `W7=F0` exceptions are treated as transcription errors. | Characterize `W7` before implementing an encoder. |
| 2026-09-05 | 3.4 | rtl_433, SmartFire, and j2deen/proflame2-esp source review | The available implementations corroborate only the legacy seven-word C/D protocol. None documents the captured ten-word integrity algorithm. | Continue evidence-led analysis and collect targeted extended-frame samples. |
| 2026-09-06 | 5.1-8.3 beta | Controlled temperature captures, extended profile implementation, and regression suite | `W7` is ambient-temperature telemetry; manual beta TX uses `W7=0xC8`, generated integrity lanes, and a 260-bit ten-word waveform. Legacy profiles remain unchanged. | Perform reporter hardware validation before release. |
