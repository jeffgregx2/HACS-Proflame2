# LilyGO RMT Repeat Selection Fix for v0.7.0

Status: shared legacy/extended implementation, software validation and agreed
hardware reception acceptance passed. This supersedes the earlier
legacy-only proposal. The existing filename is retained for document links.

## Purpose and release boundary

Recover an independently valid repeat when a stretched LOW produces an invalid
segment one bit longer than normal and that segment wins the current longest-row
selection. Preserve the original selected bytes whenever they could already
decode successfully.

Use one selection policy, one bounded word reader, and separate small legacy and
extended handlers. HA continues to receive one unchanged `pulse_capture` event.
There is no packet-event schema, HA decoder, learning, timing-threshold, radio,
buffer-capacity, or transmit change. Add one requested ESPHome diagnostic counter
for malformed repeat candidates skipped during selection.

The v0.7.0 release gate is preservation of existing behavior and improvement of
this specific failure scenario. Valid extended streams can be bench-tested with
YardStick. An extended native remote and naturally damaged extended RF captures
are not prerequisites. Synthetic damaged extended repeats test the selection
logic without claiming evidence that a particular remote generates them.

## Evidence and rejected approaches

The existing `RmtOokReceiver::convert_symbols_()` selects the longest segment;
the first segment wins ties. Normal segments commonly contain 181 bits for
legacy or 259 bits for extended because the final LOW half of the last end guard
is omitted at the segment boundary.

Two measured timing collections contain 50 exported windows. Three windows have
an invalid 182-bit winner and valid 181-bit alternatives. LOW intervals of 665,
667, and 716 microseconds become two units under the unchanged 417-microsecond
quantizer. Comparison with good repeats shows an extra LOW bit in those damaged
segments. The common acquisition and length ranking make the corresponding
259-to-260-bit extended failure plausible, although it has not been observed in
an extended native-remote capture.

Blind nominal-length preference is unsafe. A shorter seven-word fragment can
displace a complete extended frame; an invalid nominal-length row can displace a
valid longer row containing leading noise. Neither length nor repeat count proves
validity. This design first preserves the original winner, then permits a
strictly validated fallback only when that winner is structurally impossible to
accept under the existing framing rules.

Raw support captures, identifiers, and host-specific replay artifacts stay out
of public implementation fixtures. Use synthetic identities and generated packet
words for committed tests.

## Shared selection contract

Compute the original longest-segment winner exactly as today, preserving its
bytes, exact bit length, first-occurrence tie-break, and metadata.

Consider replacement only when all of the following hold:

1. Normal RMT active listening is requested, outside waveform diagnostics and
   guided-learning display mode.
2. A handler is available under the existing controller context described below.
3. The original winner has exactly the handler's one-extra-bit length: 182 for
   legacy or 260 for extended.
4. The common preservation probe finds no structurally plausible frame that the
   existing HA decoder could accept anywhere in the original winner.
5. A separate segment from the same capture has the handler's normal truncated
   length: 181 for legacy or 259 for extended. It passes complete word validation,
   applicable integrity checks, and observed-state range checks.
6. All strict valid alternatives agree on the complete seven- or ten-word
   payload. Invalid or foreign legacy candidates contribute nothing to agreement.

Select the first agreeing strict alternative. One valid copy is sufficient;
valid copies need not be adjacent. If none qualifies or valid alternatives
conflict, forward the original raw winner as before and let HA decide its state
acceptance. Invalid repeats must not become acceptable through majority voting.

Never delete an extra bit, re-encode a received packet, trim a damaged row, join
fragments, change pulse timings, or combine damaged copies. A replacement is an
observed, unmodified segment and retains its original format.

### Preservation probe protects both formats

Preservation is independent of the active fallback handler and learned identity.
A 260-bit original can contain a decodable legacy frame with leading noise; the
extended handler must not displace it.

Scan the bounded original winner for seven- and ten-word framing at every
possible bit offset. Mirror HA's word requirements and boundary rules:

- Complete start/end guards, valid Manchester data symbols, per-word parity,
  and the serial/command padding bits.
- Exactly the existing single missing terminal guard LOW is allowed, and only
  at the exact row boundary.
- Legacy full frames need the existing observed 18-zero trailer; extended full
  frames need the existing observed minimum ten-zero repeat gap.
- Conservatively also preserve a complete, structurally valid body ending
  exactly at the row boundary. This may retain a row HA would reject for a
  missing trailer, but cannot displace a successful prior decode.
- A seven-word prefix followed by extension data is not a complete legacy
  frame. It must not block a legitimate extended rescue or become a legacy
  rescue alternative in the extended handler.

The probe intentionally does not require profile match, semantic state validity,
or extended integrity. If the original is structurally plausible, retain it
regardless of those checks. This makes preservation more permissive than final
acceptance rather than introducing a stricter rejection rule.

### Preservation argument

All winner lengths except 182/260 keep their original selection. At those two
lengths, replacement occurs only if a necessary framing condition for every
supported HA decode fails at every possible offset. Therefore an original row
that HA can decode is retained byte-for-byte.

Equivalence of that necessary-condition probe with HA's actual bounded parser is
an implementation gate. Do not assume it solely from host examples. Include
leading noise, alternate formats inside a winner, terminal boundaries, and all
previously accepted fixtures in differential checks.

## Handler responsibilities

| Property | Legacy handler | Extended handler |
| --- | --- | --- |
| Words | Seven | Ten |
| Alternative length | 181 bits | 259 bits |
| Eligible damaged-winner length | 182 bits | 260 bits |
| Common validation | All word guards, symbols, parity, padding, terminal boundary, and observed state ranges | Same reader and checks across all ten words |
| Integrity | Both command check bytes against current learned C/D constants | Both serial-inclusive interleaved integrity lanes, W9 and W10 |
| Identity | Must match the current learned serial | Firmware has no extended learned identity in the current RX contract; HA retains identity ownership |
| Agreement | All seven words equal | All ten words equal, including temperature/template words |

Legacy integrity reuses `proflame2_build_err_byte()` and the learned C/D values.
Extended integrity folds the existing same transform with C=D=0 over W1/W3/W5/W7
and W2/W4/W6/W8, comparing the results against W9/W10. Do not apply legacy C/D
inference to extended words. Validate the actual received W6/W7/W8 values; do
not substitute the integration's manual-TX temperature defaults or normalize
Fahrenheit/Celsius data.

Use shared observed-state range checks equivalent to current receive behavior:
flame and fan 0 through 6, light 0 through 7, with native thermostat and manual
flame-zero observations allowed. HA's transmit permission is not an RX gate.

### Context and dispatch without changing HA actions

The component constructs the handler context once per poll using current
controller state:

- Requested active listening with a configured current legacy filter profile:
  enable the legacy handler.
- Requested raw active listening without a legacy filter profile, outside
  guided learning: enable the extended handler. HA already uses this raw mode
  for extended remotes because it withholds their profile from legacy firmware
  filtering. The extended handler acts only on a 260-bit winner and requires a
  separate fully validated 259-bit alternative.
- Listening disabled, guided learning, waveform diagnostics, or unsupported
  context: no rescue handler; retain current behavior.

The extended context does not prove ownership by a particular configured
remote. A valid extended alternative is still published as a raw pulse capture,
not as a firmware-accepted semantic state. HA continues matching it to its
configured fireplace. No new identity, variant, or C/D fields are added to HA's
RX action. Conflicting valid identities in the alternatives are an ambiguity
and keep the original winner. If the only valid alternative belongs to another
remote, HA ignores it; the original malformed winner was not decodable anyway.

Unknown legacy profiles cannot receive a strict legacy rescue. Do not reuse the
last-enabled profile after disable, reconnect, relearning, or profile changes.
Each capture uses its own context and clears its alternative/conflict state.

## Code organization

### Generic transport hook

Add an optional synchronous segment-observer hook to RMT conversion, null by
default. Invoke it when each segment completes, before clearing the local segment
buffer. Keep the original longest-segment calculation unchanged.

Use a function pointer with caller-owned context or an equally bounded,
non-allocating interface. It runs in the normal polling task, never in the ISR.
The callback receives a const segment valid only during the call. The policy
copies at most one qualifying alternative and stores counts/conflict state; it
does not retain a vector of segments or pointers into DMA storage.

### One shared policy with format handlers

Use a small `rmt_repeat_selection.h/.cpp` helper for the baseline-preservation,
alternative-agreement, and skip-count policy. The handler interface supplies
word count, lengths, and format-specific integrity/profile validation. A static
handler descriptor or small function-pointer interface is sufficient; no dynamic
class registry or new configurable mode is needed.

Keep exact-bit word reading and preservation scanning in one small shared RMT
framing helper, either a clearly separated section of that module or
`rmt_frame_validation.h/.cpp` if extraction improves clarity. Both handlers use
it. There must not be separate legacy and extended selection algorithms or
separate copies of word/parity parsing for each format.

The earlier legacy-only scratch-trailer adapter is superseded. A single bounded
RMT word reader is needed for ten-word validation and cross-format preservation.
It reads only actual bits, with the existing one-bit terminal exception, and
never supplies a fictitious repeat gap. Leave the existing FIFO decoder's parser
and acceptance behavior untouched. Reuse its public checksum transform. Verify
the new reader against HA's parser and legacy integrity/state results against the
existing firmware decoder. This isolates the addition from shipped FIFO logic.

`Proflame2TEmbedComponent::poll_rmt_pulse_capture_()` chooses context, supplies
the observer, finalizes selection after successful polling/rearming, applies the
counter delta once, and invokes the existing single-event publisher.

A replacement retains its own bytes, bit length, transition count, and first-run
metadata. Preserve `symbol_count` as the whole RMT-window count. Do not publish
validation scratch buffers or reconstructed words as received PCM.

### Resource and lifecycle limits

Use fixed storage: one alternative capture, at most ten decoded words, and
bounded per-capture counters/state. Preserve the 256-symbol production buffer,
1024-bit PCM limit, quantization thresholds, receive queue, rearm/error behavior,
TX priority, and one-event-per-capture behavior. No per-segment allocation,
additional RF activity, or longer acquisition is allowed.

A DEBUG message may report the handler, old/new lengths, agreeing alternatives,
and skipped malformed count when rescue occurs. Existing raw PCM logging remains
available and uses the selected actual segment.

## Diagnostic counter

Add **Proflame2 RMT Malformed Repeats Skipped**, an ESPHome diagnostic sensor on
the existing LilyGO device. Suggested component key:
`rx_rmt_malformed_repeats_skipped`. It follows the existing sensor setter,
configuration-schema, code-generation, and publish-if-changed patterns. Do not
reuse `RX Dropped Packets`, whose existing accounting describes FIFO failures.
No new fireplace configuration option or HA integration entity platform is needed.

Define its meaning precisely:

- Count each separate frame-sized segment that the active handler examines,
  rejects as malformed, and does not forward as the selected raw capture.
- Examined lengths are the active handler's normal and one-extra-bit lengths.
  Framing, parity, integrity, and invalid observed-state results can establish
  rejection. Other lengths are outside this limited selector's assessment.
- Do not count short noise, valid duplicate repeats, valid foreign legacy
  frames, wrong-profile filtering alone, skipped unexamined formats, or diagnostic
  acquisition windows.
- If a malformed original winner is replaced, include it once. Two malformed
  copies skipped in favor of one good copy increment the counter by two, while
  HA receives one state observation.
- If compatibility fallback forwards the original raw winner, exclude that row
  from the skipped count, even if the helper's strict validation rejects it.
  Other examined malformed rows that are not forwarded still count. A lone bad
  row forwarded to HA therefore does not increment this counter.
- Apply the aggregate delta once after each successfully consumed normal capture;
  reading telemetry or retrying publication cannot increment it again.
- Reset at controller boot, not on Start/Stop, reconnect, profile changes, or
  listening toggles. Use a saturating uint32 counter and total-increasing sensor
  semantics so wraparound cannot look like new skips.

This is a count of skipped malformed candidates, not an RF packet-loss rate,
number of button presses, or number of lost commands. A successful recovered
command can increase it. Zero does not prove all transmissions were received or
decoded, particularly because unchanged raw fallback still goes to HA. This
specific meaning should appear in the diagnostic documentation and DEBUG summary.

## Design prototype validation

A temporary C++ prototype used one exact-bit word reader, one preservation scan,
one selector, and two small format handlers. It linked the unmodified production
checksum implementation. Existing Python framing/integrity code independently
checked the selected output. These results preceded implementation; they are
retained separately from the production-code validation below.

All 12,241 cases passed:

| Cases | Coverage | Result |
| --- | --- | --- |
| 50 | Both measured legacy timing collections | All three demonstrated bad selections recovered; the other 47 winners unchanged. |
| 5 | Two contiguous measured subwindows plus three explicit synthetic good/bad repeat pairs | Production-sized cases recover. |
| 369 | All 123 existing extended fixture rows in disabled, legacy, and extended contexts | Identical selected bytes and decode outcomes: 120 accepted and three rejected in each context. |
| 4 | Canonical legacy ON/OFF fixtures from two remote profiles | Original selections unchanged. |
| 11,233 | Add one quantized LOW unit at every one-/two-unit LOW run in each of the 120 accepted extended fixture rows; pair with its undamaged original | Every synthetic 260-bit malformed winner replaced by the expected valid 259-bit repeat. |
| 28 | Counter, identity, integrity, ambiguity, disabled/learning context, full-guard and fallback cases | Expected counts and preservation/recovery decisions. |
| 440 | Every single-bit mutation of representative 181-bit legacy and 259-bit extended alternatives | No invalid mutated alternative promoted as a strict rescue. |
| 112 | Leading offsets and decodable legacy frames inside extended-sized winners | Original selected bytes preserved. |

The 11,233 extended cases model a timing-conversion error; they are not damaged
extended RF captures. Their synthetic two-repeat controls fit within normal
capacity, with a maximum of 196 RMT symbols against the 256-symbol limit. Future
committed regressions should generate a compact set of synthetic variants rather
than copy thousands of device-specific recordings.

Counter checks include two bad copies plus one good copy, good duplicates,
conflicting good alternatives, short noise, disabled selection, invalid extended
integrity, all-invalid captures, and exclusion of an unchanged forwarded winner.

An additional 11,592 differential checks compared the shared C++ word reader
directly with the existing Python `decode_pcm_words()` implementation. These
covered every relevant seven-/ten-word offset in the 123 extended fixture rows,
plus 1,500 deterministic random/boundary inputs. All returned the same rejection
or identical decoded words. The host prototypes compiled with C++17 and
`-Wall -Wextra -Werror`.

### Evidence limits relevant to acceptance

Diagnostic acquisition uses a 4096-symbol DMA buffer and different settings from
normal reception's 256-symbol buffer. Whole-window replay establishes selector
behavior, not identical callback boundaries or a field success rate. One measured
failure lacks a contiguous good/bad pair fitting normal capacity; its small-buffer
control is explicitly synthetic.

Earlier isolated failed PCM rows contain no alternatives. The design cannot
prove they would have been recoverable. It does not fix RF distortion, change
timing tolerances, repair damaged data, or guarantee a command when all repeats
are unusable. It leaves other malformed lengths for later investigation.

## Implementation tasks

| Task | Work | Completion evidence |
| --- | --- | --- |
| T1 | Implement the shared exact-bit reader, cross-format preservation scan, handler interface, legacy profile/checksum handler, and extended integrity handler. | Compiled C++ checks against HA framing and integrity, existing legacy checksum/receive behavior, both original offsets, cross-format frames, and complete/truncated boundaries. No existing FIFO parser changes. |
| T2 | Add the optional non-allocating observer and shared selector. | Actual receiver code with driver stubs preserves baseline bytes/metadata/ties/errors/rearming without a handler; bounded valid alternatives and conflicting cases behave as designed. |
| T3 | Wire current controller context and existing single-event publication. | Legacy, raw extended, disabled, learning, diagnostics, profile changes, and reconnects dispatch correctly. HA identity ownership for raw extended events remains unchanged. |
| T4 | Add the one diagnostic counter through component storage/setter, ESPHome schema/codegen, base package, and existing telemetry publisher. | Per-repeat counts, forwarded-original exclusion, duplicate suppression, boot reset, saturation, and unchanged FIFO counters are tested. |
| T5 | Add compact synthetic regressions and replay private evidence locally. | Recover all demonstrated legacy selection failures and synthetic extended stretched-LOW cases; preserve all accepted/rejected fixtures and the prior counterexamples. Do not commit uploaded captures, identities, or host-specific artifacts. |
| T6 | Run required software/build checks and update technical documentation. | Relevant C++/RMT/pulse/learning tests, full integration suite, 315/433 MHz firmware builds, formatting/whitespace checks, and diagnostic sensor validation pass. No packet-event contract change. |
| T7 | Perform normal-firmware hardware acceptance with available equipment. | Native legacy remote reception and OFF/ON behavior match rtl_433; valid YardStick extended streams continue updating Test HA; existing learning and Prod-to-Test reception continue working. Inspect counter changes and rescue logs when malformed repeats occur. |

### Implementation and software verification

T1–T6 are complete. The user installed the development integration and firmware
and completed T7's agreed legacy/extended reception acceptance. Installation and
RF testing were performed by the user, separately from implementation.

- `rmt_ook_capture.h` holds the unchanged transport record. The receiver exposes
  a synchronous, non-owning observer for completed segments while preserving
  its existing longest-winner calculation and acquisition/rearming behavior.
- `rmt_repeat_selection.*` contains the shared bounded reader, preservation
  probe and selector with separate legacy and extended integrity handlers.
  Selection uses fixed storage (enforced at no more than 256 bytes in the host
  build), without ISR parsing, dynamic candidate storage or per-repeat events.
- The component creates fresh selection context for each normal capture, finishes
  selection only after successful rearming, updates the saturating counter once,
  and uses the unchanged single raw-event publisher. ESPHome schema/codegen,
  base YAML and telemetry include the new diagnostic sensor.
- Actual production C++ replay of all 50 private timing windows recovers the
  three demonstrated invalid 182-bit winners; the other 47 winners remain
  byte-identical. Each recovered row is an unmodified separate measured repeat,
  independently decodes in HA, and agrees with the rtl_433 payload evidence.
  All 50 resulting selections decode. Whole-window replay uses diagnostic-sized
  host conversion; the normal-capacity caveat above still applies. Actual
  normal-capacity polling also recovers two contiguous measured subwindows
  (134 and 199 symbols) and three explicitly synthetic good/bad pairs made from
  the measured repeat durations. The third contiguous measured span needs 270
  symbols and is not claimed as a production-capacity replay.
- Committed tests compile the actual receiver, selector and checksum code with
  driver stubs. They cover successful rearming and rearm failure, 665/667/716 µs
  stretched LOWs in both formats, ambiguity, counter semantics and saturation,
  disabled/learning contexts, state ranges, malformed guards/parity/integrity,
  and preservation at later offsets and across formats. They also exercise
  all 123 existing extended rows in three contexts, 11,233 synthetic extended
  stretched-LOW variants, 440 single-bit mutations, and differential word-reader
  checks against the existing Python parser.
- Full regression suite: **2,130 passed**, seven optional environment-dependent
  checks skipped, one intentionally deselected. The 29 real ESPHome entity/schema
  checks passed separately. Python lint/format, new C++ formatting, changed-line
  C++ formatting and whitespace checks passed.
- The maintained ESPHome validation target validates both supported bands,
  rejects the invalid-band fixture, and builds full display firmware for
  **315 MHz and 433 MHz**.

Tests must compile and exercise actual production helpers/receiver code rather
than maintain a separate Python implementation of selection as the gate. Include
raw-duration synthetic controls spanning quantization, segment completion, and
selection together. Measure fixed storage and bound observer work during builds;
do not add ISR parsing or logging.

## Mandatory negative and compatibility checks

- Any original 182/260-bit row accepted by HA remains byte-identical, regardless
  of configured identity or integrity policy; preservation checks are structural.
- Preserve original lengths other than 182/260, including longer noisy rows and
  nominal 181/259-bit rows. Length alone never proves validity.
- Preserve legacy frames at later offsets inside 260-bit winners and complete
  extended frames preceded by seven-word fragments.
- Reject alternative bad symbols, guards, parity, padding, legacy checksums,
  either extended integrity lane, and incomplete extensions. Test corruption in
  extension words and both integrity words, not only the first seven words.
- Accept valid native manual flame-zero and thermostat observations under the
  existing receive range rules, independent of transmit configuration.
- Preserve actual temperature/template data during checks and publication. Treat
  differing full payloads as ambiguity rather than merging their fields.
- Do not activate rescue or count skips during guided learning or waveform
  diagnostics. No stale profile/candidate state may survive between captures.
- No qualifying alternative or conflicting strict alternatives keeps the original
  raw event. Repeated valid alternatives still produce only one event.
- Verify counter classification, unrelated/foreign frames, exactly-once deltas,
  reset/saturation, and the distinction between skipped repeats and HA commands.
- Exercise exact production capacity, empty/short/oversized input, chunk ends,
  copy lifetime, and metadata consistency without changing acquisition limits.

## Hardware acceptance and release decision

### Recorded acceptance — 2026-11-05

The user completed normal-firmware reception testing with the development
integration and firmware:

- Native legacy remote: the HA UI reflected the tested commands. A DEBUG capture
  confirmed four rescues, each replacing an invalid 182-bit winner with an
  unmodified 181-bit repeat backed by two agreeing valid repeats. The counter
  started at zero and reached four. All 19 published captures decoded, including
  the final Power OFF.
- YardStick extended transmitter to Test LilyGO receiver: the user confirmed
  correct operation, rtl_433 showed every expected packet, and the malformed
  repeat counter did not increase. This validates normal extended reception.
- Malformed extended-repeat recovery is covered by synthetic tests; no naturally
  damaged extended RF capture was required or claimed. The unchanged learning
  paths remain covered by software regressions; these reception runs do not
  constitute a new hardware relearning test.

The agreed software/build and hardware reception gates passed. The fix is
validated for inclusion in v0.7.0; this is project acceptance, not external
certification. Private logs and remote identifiers are not included here.

### Repeating the hardware checks

Use the normal development firmware, not waveform diagnostic firmware, for HA
state acceptance. Keep production on its known baseline while Test receives.

1. Native legacy remote: repeat flame/light cycles and OFF/ON commands with
   rtl_433 running. Validate correct received states, and check rescue DEBUG
   messages/counter increases if malformed repeats are encountered.
2. YardStick with the same extended identity and band as the Test fireplace:
   transmit valid power/flame/light/fan changes. Verify full extended decoding,
   correct HA state, and no regression relative to the existing receiver. Clean
   streams need not increment the malformed-repeat counter.
3. Confirm ordinary legacy and extended learning remains unchanged. Verify
   valid Prod LilyGO transmissions are still received on Test. Run the relevant
   band configurations supported by available equipment.

Do not require a naturally damaged extended transmission or an extended native
remote to pass v0.7.0. Synthetic tests establish the targeted failure handling;
YardStick establishes valid extended-stream compatibility with available hardware.
The absence of a natural extended failure does not justify changing RF timing or
adding a fault-injection transmitter.

Stop for review if implementation requires changed HA packet events/acceptance,
new HA RX-action identity/variant fields, timing thresholds, capture buffers,
learning behavior, the existing FIFO decoder's semantics, or per-repeat event
publication. If preservation checks or available-equipment acceptance fail,
defer broader changes to v0.8.0 rather than expand the release fix.

## Related technical documents

- [LilyGO RMT pulse receive path](lilygo_cc1101_rmt_pulse_fallback.md)
- [Waveform diagnostics](diagnostics/waveform_diagnostics.md)
- [Extended integrity evidence](issue_26_extended_integrity.md)
- [Code quality and publication rules](code_quality_guidelines.md)
