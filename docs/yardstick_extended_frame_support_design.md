# YardStick extended frames and controller configuration

Status: implemented locally on `dev`, preserving the committed Issue #32
learning changes. Automated validation passed; physical RF and fireplace
acceptance remain pending. No RF transmissions were performed.

The YardStick controller and RF band configuration described at the end of
this document is also implemented locally. LilyGO configuration and behavior
remain unchanged. Its validation is reported separately from the original
extended-frame results below.

YardStick must transmit, receive, diagnose, and learn both seven-word legacy
and ten-word extended Proflame2 frames. Its transmit path already preserves
extended waveforms. This implementation adds extended decoding in the FIFO
receive scanner, correct receive defaults, full-frame repeat grouping, and
extended-aware learning entry points.

Related designs: [extended protocol](issue_15_extended_protocol_design.md),
[Issue #32 learning policy](issue_32_learning_mode_design.md), and
[independent rtl_433 helper](rtl433_extended_protocol_implementation_plan.md).

## Scope and compatibility

Support both direct rfcat operation and the worker-backed production path,
including `receive()`, `receive_sample()`, `receive_diagnostics()`,
`receive_learning_diagnostics()`, and `learn()`. Cover the HA guided flow and
the older `async_learn_remote_profile()` convenience API.

Preserve legacy decoding, installed profiles, Learning ID formats, controller
ownership, worker lifecycle, cancellation, and existing RF settings. Do not
change frequency selection, modulation, baud rate, power, repeat cadence, or
firmware to solve a frame-length problem. The supported RF band remains a
transport configuration concern independent of seven- versus ten-word frames.

This work does not enable thermostat control, reinterpret unknown flags, add
YardStick input to rtl_433, or move the independent Learning ID helper into
rtl_433. The helper consumes rtl_433 JSON; YardStick learning uses the shared
Python protocol policy directly.

## Audit evidence

The offline audit used recorded protocol data and mocked radios; no RF was
transmitted and no real USB radio was opened.

| Path | Observed behavior |
| --- | --- |
| Waveform generation | Generated bits matched the recorded PCM portion of all 120 valid extended captures. An extended frame has 260 meaningful bits and a 35-byte serialized single-frame payload including its separator/padding. |
| Default repeated TX | Five extended frames with 12 separator bits produce 1,348 meaningful bits in 169 bytes. Both direct and worker transports preserved that payload. |
| FIFO scanner | Accepted none of the 120 reserialized extended observations; the existing pulse decoder accepted all 120. |
| YardStick receive and learning | A valid synthetic extended frame produced no receive packet, no backend learning samples, and a guided capture timeout. Legacy controls succeeded. |
| Convenience learner | Already-decoded valid extended commands were incorrectly passed to legacy C/D derivation and failed with a contradictory-profile result. |
| Existing regression tests | The focused YardStick, worker, collector, and pulse suite passed 67 tests despite these gaps. |

These results establish software behavior. They do not prove actual YardStick
RF timing, reception sensitivity, or fireplace acceptance.

## Architecture

Keep the data flow explicit:

```text
RFrecv bytes from direct radio or worker
  -> bounded word parsing and complete-frame classification
  -> original frame integrity validation
  -> candidate occurrences with complete frames and offsets
  -> repeat grouping and candidate selection
  -> CaptureSample / ProflamePacket
  -> shared state decoding or learning policy
```

Transport code owns device access and byte delivery. Framing code owns guards,
parity, frame boundaries, and offsets. The protocol package owns extended
integrity, state interpretation, and profile derivation. Diagnostics describe
the selected original frame and keep failed or heuristic windows separate.

### Shared bounded decoding

Refactor the reusable bounded word parsing already in `rf/pulse.py` for use by
`rf/capture.py`. Do not implement a second extended checksum algorithm or add
an unrelated extended-only receive loop in `YardStickBackend`.

The shared framing layer must expose individual occurrences before deduplication.
The existing pulse API can retain its deduplicated return behavior; YardStick
needs every occurrence to calculate repeat counts and offsets. Each occurrence
must carry its complete `ProflameFrame`, frame format, start offset, actual
frame bit length, and observed boundary information.

Keep transport boundary policies explicit. RMT rows can have the already
supported missing final low guard bit at their known row boundary. A raw FIFO
buffer must not acquire that exception automatically: do not append guessed
bits, borrow a bit from another capture, or stitch unrelated RFrecv buffers.

For FIFO reception:

1. Search arbitrary bit alignments within each received buffer. Ignore leading
   noise and clipped prefixes; accept complete later frames when present.
2. Require the per-word sync/start/end guards, padding pattern, and parity.
3. Classify exactly seven or ten words using frame boundaries. A valid seven-word
   prefix followed immediately by more word data is not a legacy frame.
4. Require both serial-inclusive integrity lanes for every extended frame.
   If an extended candidate fails integrity or is incomplete, do not retry its
   first seven words as a legacy packet when continuation evidence is present.
5. Preserve tested legacy separator tolerance. Extended inter-repeat separators
   must accept the existing 12-low-bit transmitter cadence. Keep separator
   rules named and tested rather than broadening guard acceptance generally.
6. Recognize a complete extended frame ending at the buffer boundary, including
   up to seven trailing low padding bits introduced by byte packing. All ten
   words and their final guard must already be present. Padding must never
   repair a missing word or guard. This covers the final frame of the default
   169-byte burst, which has four byte-padding bits after its frame body.
7. Preserve invalid and partial windows only as diagnostic evidence. Acceptance
   must not depend on temperature scale or the current thermostat state.

The existing pulse/RMT acceptance corpus must remain valid after extraction.
Do not simply call its current deduplicating scanner unchanged: YardStick needs
occurrences, and existing RMT and FIFO boundary rules differ.

### Samples and repeat grouping

`CaptureSample` already has `extension_words` and `frame_format`; populate them
from the actual decode. `as_frame()` and `as_packet()` must preserve all ten
words unchanged. Sample symbols and raw slices must use the actual frame length,
with any observed separator length recorded separately and consistently.

Group RF repeats by the complete `frame.wire_words`, not the current five-field
remote/cmd/err tuple. Two extended frames with identical W1–W7 but different
W8–W10 must remain separate observations. Preserve every occurrence offset;
derive counts from distinct physical offsets, without counting one offset
twice because two alignment searches found it.

Keep this separate from the learning command key. Exact frames determine RF
repeat agreement; the Issue #32 key `(W4 & 0xFB, W5)` determines extended control
diversity. Temperature and units changes may alter raw frames without providing
new control commands.

### Receive window defaults

Before this change, the default `YardStickBackend` receive window was 25 bytes.
That legacy packet size cannot contain a complete 260-bit extended frame.
HA runtime, guided learning, and the collector already explicitly use 255 bytes.

The constructor now uses a named default capture-window constant of 255 bytes,
consistent with those callers. Retain the 25-byte constant for legacy packet
size where needed; do not change waveform constants globally. The worker
continues to receive the requested capture length through its existing command.

Keep explicit short windows usable for diagnostic work, but report that a window
shorter than 33 bytes cannot contain a complete extended body. A short window
must not be advertised as full extended reception or silently padded. This
change requires tests of configuration and payload handling, not speculative
changes to CC1101 register settings or larger hardware buffers.

### Learning entry points

Once reception yields complete packets, the HA guided flow must use the existing
Issue #32 behavior: validate original bytes, pause for thermostat/SMART mode,
resume on a matching manual packet, ignore unchanged controls, and generate the
canonical Celsius manual profile. Keep received frames immutable.

Update the older `async_learn_remote_profile()` API to dispatch by the validated
protocol variant. Preserve its legacy behavior, timeout/error semantics, and
caller-supplied legacy diversity thresholds. For extended evidence, use shared
`derive_remote_profile()` and the established extended requirements rather than
deriving legacy C/D values from W6/W7. Validate mixed identities/formats and bad
original integrity before deduplication. Temperature, units, and setpoint changes
must not cause contradictions or create command diversity. Thermostat packets
cannot substitute for manual evidence; this noninteractive API continues
listening within its existing timeout and reports missing manual evidence when
necessary. Honor `min_valid_packets` for both variants; extended counts use
validated manual observations, and duplicates never replace required command
diversity. The two `min_unique_cmd*` arguments retain their documented legacy
role; extended diversity follows shared extended requirements. Its wrapper
`async_run_learning_with_backend()` must return the same
extended profile and close resources normally.

`YardStickBackend.learn()` remains a capture operation returning complete raw
samples and packets; it does not independently invent a learned profile. The
standalone rtl_433 helper retains its own stricter confirmation gates. Do not
copy its terminal workflow into the YardStick backend.

### Diagnostics and capture tools

Extend YardStick decoded fields, selected candidate windows, semantic artifacts,
and collector normalization with additive protocol information: variant, word
count, complete raw words, extension words, and extended integrity status. Keep
existing legacy cmd/err keys for compatibility, while identifying W6/W7 as
extended data rather than legacy validation bytes.

Replace seven-word assumptions in candidate lengths and heuristic windows with
explicit format-aware lengths. Heuristic windows remain debug-only regardless
of their score. Extended integrity failure must be visible as a distinct decode
failure, not reported solely as a legacy trailer or command-layout error.

Guided diagnostic capture must read YardStick's own receive diagnostics; do not
require the LilyGO-specific `last_fifo_semantic_artifact` attribute to preserve
YardStick evidence. Keep raw buffers, failed attempts, accepted frame data, and
profile-learning acceptance clearly distinguished.

### Transmission and worker transport

Keep the existing encoder, `build_transmission_plan()`, repeat builder, and
direct/worker byte transport. No demonstrated seven-word restriction exists in
the send paths. Verify both embedded repeats and software repeats with extended
frames, including exact bit counts and trailing padding.

Use the plan factory to obtain the correct frame length; do not substitute the
legacy 182-bit metadata default for an extended frame. Worker bit-length metadata
describes meaningful bits while RFxmit sends complete bytes, so final padding
must be low. Preserve frequency overrides, post-TX cleanup, timeout handling,
and controller serialization.

## Implementation tasks

1. Add failing regression cases for extended FIFO reception, zero learned
   samples, short default capture size, and the convenience learner failure.
   Keep existing Issue #32 work and unrelated changes intact.
2. Extract reusable bounded framing and occurrence information; add explicit
   FIFO boundary policy and serial-inclusive extended integrity acceptance.
3. Adapt the scanner and diagnostics to seven or ten words, complete-frame
   grouping, accurate offsets, and distinct rejection reasons.
4. Update the receive default and its documented diagnostic limitations across
   direct and worker operation. Verify existing HA and collector configuration.
5. Route extended convenience learning through shared profile derivation and
   verify the current guided flow end to end using actual YardStick decoding.
6. Extend semantic artifacts and collector normalization, preserving existing
   fields and provenance rules. Add user-facing compatibility notes.
7. Run focused and full regression suites, installed-helper interoperability,
   and the existing helper archive/package checks after shared framing changes.
   No helper gate or distribution redesign is part of this task.

## Required tests

| Area | Acceptance criteria |
| --- | --- |
| Legacy compatibility | Existing valid/corrupt legacy corpus, separator tolerance, error behavior, profiles, and learning thresholds remain valid. |
| Recorded extended evidence | Decode all valid recorded extended vectors with original words and integrity unchanged; compare generated TX bits with original PCM independently. Keep rejected captures rejected. |
| Frame boundaries | Exercise leading noise, bit offsets, mixed legacy/extended buffers, clipped prefixes/suffixes, 12-bit repeat separators, exact complete endings, and bounded byte padding. No guessed FIFO guard bit or malformed-extended fallback is accepted. |
| Integrity and structure | Corrupt W1–W10, parity, padding, guards, and word counts. Check that valid structure with bad extended integrity never becomes semantic evidence. |
| Repeat grouping | Identical extended frames yield accurate offsets/counts, including the final complete repeated frame; equal W1–W7 with differing W8–W10 do not merge. |
| Receive configuration | Default capture length is 255 in direct and worker paths. Explicit legacy-sized diagnostic windows retain their setting and expose their extended limitation. |
| Transport TX | Mock direct radio and actual worker dispatch with a fake radio. Verify full 260-bit frames, the 169-byte default burst, software repetition, frequency overrides, failures, and cleanup. No physical RF transmission in automated tests. |
| Receive APIs | Verify complete extension preservation through receive, sample, diagnostics, learning diagnostics, and backend capture learning in both transport modes. |
| Guided learning | Feed raw YardStick FIFO data through decoding into HA. Cover thermostat/SMART recovery, manual OFF exit, delayed repeats, temperature rollover, mixed units/setpoints, cancellation, and lease release. |
| Convenience learning | Already-decoded and raw-backend extended input produce the canonical shared profile. Mixed identities/formats fail; insufficient manual evidence times out clearly; wrapper cleanup and legacy thresholds remain intact. |
| Diagnostic provenance | Complete words and integrity status survive collector output; selected offsets/lengths identify the actual frame. Failed or heuristic windows never pass semantic eligibility gates. |
| Shared consumers | Pulse/RMT and FIFO consumers retain their documented boundary rules; installed helper and HA continue deriving equivalent IDs from equivalent valid evidence. |

Use compact synthetic fixtures for new permutations and existing approved
capture-backed vectors for independent expectations. Do not publish raw customer
captures, identifying profile values, or generated private Learning IDs. Keep
scratch environments, logs, and generated artifacts under task-owned `/tmp`
directories and clean them afterward.

## Validation without an extended fireplace

Software completion requires the above regression coverage, style checks, and
successful decoding of the recorded extended corpus. Report physical RF
validation separately; the maintainer does not have an extended remote/fireplace.

A YardStick can serve as a synthetic extended transmitter for a separate LilyGO
or RTL-SDR receiver. That can validate RF acquisition, HA learning, or rtl_433
plus the independent helper. Testing YardStick reception requires a separate
transmitter; one radio cannot establish simultaneous TX/RX loopback proof.

A transmitter scenario runner and actual over-the-air testing are optional
follow-up work requiring separate authorization. They must not be added silently
to this implementation. Synthetic sequences can model thermostat transitions,
unit changes, temperature variation, and the reported delayed repeat without
enabling thermostat TX in the integration's normal control API. Even successful
RF learning does not prove a real fireplace accepts generated manual commands;
that acceptance check must come from a reporter with the hardware.

## Completed software validation

The five initial regression cases failed before implementation, reproducing
extended FIFO rejection, missing repeat occurrences, incorrect integrity
diagnostics, the short receive default, and legacy derivation of extended data.

The full repository suite passed 1,924 tests, with 34 skipped and one deselected.
The new YardStick extended suite contains 68 passing cases, including both
transport modes, actual worker dispatch with a fake radio, raw FIFO input into
the HA flow, cancellation/lease release, all 120 recorded extended observations,
and original PCM comparisons. Installed-helper validation passed 115 tests and
six HA interoperability checks; the Make source-archive checks passed all 33
tests. Changed Python files passed Ruff and Black checks.

The shared parser extraction also required updating the offline waveform review
adapter to its public function name; its structural review behavior is unchanged.
No firmware, radio settings, normal thermostat TX behavior, or helper
confirmation gates were changed. The optional transmitter scenario runner was
not implemented.

## YardStick controller and RF band configuration

This section records the separately authorized controller/band implementation.
Band selection belongs to the physical controller, independently of protocol
frame length. Each fireplace uses its controller's band rather than storing an
independent band selection. Introduce an HA controller device and band UI for
YardStick only. Both YardStick and LilyGO remain limited to one fireplace per
controller. Sharing is deferred to a possible later release.

### Scope and compatibility

LilyGO remains configured through its existing ESPHome YAML `rf_band` setting.
Do not change its schema, package defaults, examples, firmware, native API,
startup behavior, antenna-switch handling, display synchronization, or HA band
configuration. YAML remains the sole band authority for LilyGO. Its existing
[RF band design](issue_21_433mhz_lilygo_support_design.md) remains applicable.
LilyGO keeps its existing ESPHome device link and one-fireplace assignment rule;
it does not gain a controller configuration entry or HA band selector.

Yard Stick One hardware supports 433.92 MHz as well as the current 315 MHz use
case; see the [manufacturer's supported frequency ranges](https://greatscottgadgets.com/yardstickone/).
The integration supports 315 and 433 MHz selection in software. Runtime
transmission, reception, learning, and reconnect use the same controller band.
Hardware acceptance remains pending.

Keep the current single default YardStick USB selection in scope; this design
does not add support for selecting multiple USB radios. Do not add shared
connections, packet fan-out, multi-profile receive filtering, reference-counted
backend ownership, or shared LilyGO display selection. Such work requires a
separate future design and authorization.

### HA devices, setup, and ownership

Use one existing Proflame2 configuration entry containing two devices: the
YardStick controller and its fireplace. There is no separate controller
configuration entry or new integration type. The controller appears under
Settings → Devices & services → Devices.
The fireplace remains a separate device linked to its controller through HA's
device relationships. The relationship is one controller to one fireplace.

| Owner | Configuration |
| --- | --- |
| YardStick controller | USB connection reference, selected band, and effective configuration status. |
| YardStick fireplace | Exclusive controller reference, remote profile/Learning ID, fireplace features, and refresh options. |
| LilyGO controller | Existing ESPHome YAML band configuration and existing ESPHome device. No new HA band setting or controller configuration entry. |
| LilyGO fireplace | Existing exclusive ESPHome controller link, remote profile/Learning ID, fireplace features, and refresh options. |

Do not require users to add a YardStick controller separately before adding a
fireplace. During Add fireplace, selecting YardStick collects the band,
defaulting to 315 MHz, and automatically creates both devices under the one
fireplace configuration entry as part of successful setup. An assigned
controller cannot be claimed by a second fireplace,
even with a different remote identity. Reject the attempt before opening the
radio or starting learning. Preserve the final entry-creation guard against
simultaneous setup attempts and the startup guard against saved duplicates.
LilyGO setup follows its existing flow and retains the same ownership checks.

Device creation is idempotent. Cancelled setup creates no devices or entries;
retries and simultaneous attempts must not leave duplicate registrations. The
YardStick identifier is `controller:yardstick:0`, matching the single supported
default USB selection. Existing fireplace device/entity identifiers are kept.
Existing entries without a stored band use 315 MHz without rewriting profiles
or adding configuration entries. The optional `rf_band` key is saved in the
original entry data, independently of feature options and Learning IDs.

Offer YardStick band changes through the RF band select entity on its
controller device, in the Configuration section. Link its assigned fireplace
through the device relationship and expose its name on the selector. Distinguish the saved selection
from a configuration successfully applied to the radio; never report a requested
value as effective after a failed configuration. Learning IDs remain unchanged
and do not gain a band field. Removing the integration configuration entry
removes both devices and their entities together and releases the controller
assignment; no separate controller-entry deletion/reassignment policy is needed.

### YardStick frequencies and reconfiguration

Preserve current 315 MHz behavior: 315,000,000 Hz receive and 314,973,000 Hz
transmit. Define 433 MHz operation at 433,920,000 Hz for both paths. Apply the
selected settings consistently to normal operation, guided learning, direct
rfcat operation, worker operation, and reconnect. Validate the existing optional
YardStick learning frequency override against the selected band explicitly;
a diagnostic override must not silently tune learning to another band.

Retain one runtime backend for the assigned fireplace and the existing
controller serialization and learning lease. The controller
device owns the band selector; it must not open a second radio connection.
Do not introduce continuous YardStick listening merely to add band selection.

For a YardStick band change, block new operations, finish active transmission
and stop reception, apply the complete radio settings, check for successful
configuration, and then restore the prior listening policy. Coordinate with
learning rather than retuning during a capture. Do not release queued commands
prepared under the old configuration without revalidation. A failed change must
suspend RF work until the effective configuration is known; report the failure
and retain the saved configuration needed for recovery. This uses YardStick's
existing radio/worker boundary and requires no ESPHome action or handshake.

### Migration and implementation tasks

1. Create a YardStick controller device under the existing fireplace entry
   and link the fireplace device to it. Preserve the one-fireplace-per-controller checks for both physical
   backends, including aliases, disabled assignments, and duplicate startup.
2. Add the YardStick band setup step and a controller-scoped select entity
   for band selection and configuration status. Keep LilyGO setup and options unchanged.
3. Add the controller device idempotently for existing YardStick entries without
   changing fireplace entity IDs, remote profiles, or USB assignments. Missing
   band data defaults to existing 315 MHz frequencies; no configuration-entry
   format migration or additional entry is needed. Leave LilyGO entries intact.
4. Apply the YardStick controller band consistently to runtime and learning,
   including direct and worker operation, reconnect, and diagnostics. Validate
   diagnostic learning overrides against the selected band. Retain the existing
   single-fireplace backend lifecycle and operation ownership.
5. Implement coordinated YardStick reconfiguration, failure reporting, and
   same-entry device cleanup without adding sharing mechanisms.
6. Update YardStick setup, controller, and migration documentation. Explain the
   one-fireplace restriction and distinguish YardStick's HA band setting from
   LilyGO's existing YAML band setting. No ESPHome changes are required.

### Required follow-up tests

| Area | Acceptance criteria |
| --- | --- |
| Automatic setup | A YardStick fireplace creates both devices under one configuration entry. Cancellation, retry, and concurrent setup do not create duplicates or orphan resources. No prerequisite manual controller setup is required. |
| Device presentation | YardStick has a controller device under the existing integration; its fireplace links to it. Band settings appear only at YardStick controller scope. LilyGO keeps its existing device relationships and setup flow. |
| Exclusive assignment | For both physical backends, a second fireplace is rejected during manual setup, Learning ID import, guided learning/retry, final creation, and duplicate startup. Different remote identities and controller aliases cannot bypass the restriction. Disabled saved fireplaces retain their setup reservation. Existing duplicate entries cannot open competing backends. |
| Band propagation | Both bands reach YardStick RX, TX, learning, direct/worker paths, reconnect, and diagnostics consistently. Existing 315 MHz frequencies and waveform behavior remain unchanged. Cross-band diagnostic learning overrides are rejected clearly. |
| Reconfiguration and failure | YardStick active TX, reception, queued commands, and learning coordinate through existing ownership. Failed configuration never reports the requested band as effective; RF work remains suspended and recoverable. |
| Migration and lifecycle | Existing YardStick profiles, entity IDs, assignments, and 315 MHz defaults survive repeated setup and restart. Removing the one configuration entry removes both devices and releases ownership. LilyGO entries and YAML settings remain untouched. |
| Protocol compatibility | YardStick legacy and extended learning/control work on either configured band with unchanged Learning IDs and evidence rules. Band selection does not weaken integrity checks or learning identity validation. |
| LilyGO regression | Existing 315/433 YAML configuration, firmware/API contracts, startup, learning, strict filtering, single-fireplace ownership, and display synchronization retain their current behavior. No new band selector appears for LilyGO. |

Use fake YardStick radios and worker simulations for automated coverage, along
with existing LilyGO regression tests. Physical 315/433 MHz validation remains
a separate hardware acceptance task; this design does not authorize RF
transmission, firmware flashing, or deployment.


### Controller/band implementation notes

`rf/band.py` owns the YardStick-only band/frequency policy. Setup collects the
band before opening the learning radio; successful setup stores the selection
in the existing fireplace entry. `select.py` attaches the RF band selector to
the YardStick device, while `yardstick_controller.py` coordinates changes with
the existing operation arbiter and learning lease. No controller configuration
flow, separate entry migration, or sharing machinery was added.

Band changes wait for an active send, discard pending controls, and end old-band
confirmation/receive tasks before applying the new settings. The worker's
`CONFIGURE` operation applies receive settings and returns to idle without
receiving a packet or transmitting. A failed or cancelled change keeps the saved
band and blocks controls/refresh until a configuration retry succeeds. Native
configuration calls retain ownership until completion even if their caller is
cancelled. Successful changes restart the refresh interval without immediately
sending a packet. A same-band selection is a no-op unless recovering a failure.

Existing YardStick entries acquire their controller device and selector during
normal setup; missing band data means 315 MHz. Feature/profile options do not
rewrite the stored band. No ESPHome firmware, package, YAML helper, or native API
contract changed.

The controller/band suite contains 30 automated cases covering both bands,
legacy/extended packets through direct and worker paths, existing-entry defaults,
HA device/entity placement and cleanup, the real select service, options reload,
learning overrides, ownership, pending commands, configuration failure/retry,
cancellation, and old-band receive cleanup. The final full repository suite
passed 1,955 tests, with 34 skipped and one deselected. Changed Python files
passed Ruff and Black checks. These are software results; no physical radio
was opened and no over-the-air transmission or firmware deployment was performed.
