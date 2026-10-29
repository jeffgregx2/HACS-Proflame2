# Waveform Diagnostics

Development capability, not a production timing correction. Hardware validation
is pending. The investigation compares native remote and LilyGO transmissions
without modifying the transmitter under test.

## HA Payload Logging

Each configured fireplace has a diagnostic **PCM Payload Logging** switch.
It defaults OFF on first setup and restores an explicitly selected state on
restart. Turning it on logs complete TX/RX payloads through
`custom_components.proflame2.pcm` at INFO without enabling general DEBUG output.
No fireplace command is sent when toggling it.

Each line identifies direction, backend, bit length when available, and request
or event context. TX records describe requested payloads, not acknowledgement
from the fireplace. ESPHome RX logs linked-device events before queue consumption
and semantic acceptance; YardStick logs raw received payloads. No RF reception
is started merely by enabling the switch. Enable normal listening or learning
to receive data. Data discarded inside controller firmware cannot be logged by HA.

`pcm_hex` is a conventional diagnostic field name. FIFO/RFrecv bytes can contain
noise and padding; they are not necessarily an aligned frame. Refer to the event
kind and bit length. Hex includes every supplied byte, including padding.
Firmware-local delayed retransmissions are not additional HA requests and are
not logged as new TXs by this HA switch. Use controller logs to observe them.

These payloads contain remote identifiers. Treat logs as private and scrub before
publication. Disabling the switch stops payload logging for that entry without
changing the integration's general debug configuration.

The paths below are examples, not machine defaults. Configure actual storage,
receiver details, and recorder locations in the external configuration described
in [the diagnostic setup guide](README.md).

## Receiver-Only Diagnostic Firmware

Include the opt-in waveform package alongside the existing base/display packages
in a dedicated receiver configuration, all from the same source revision:

```yaml
packages:
  waveform: !include ./packages/diagnostics/proflame2_tembed_waveform_diagnostics.yaml
```

Include this package after the base/display packages. It sets the diagnostic
firmware identity to `v0.7.0-debug`, independently of the normal release identity.
This is an opt-in receiver package; the ordinary base and debug packages do not
create the waveform buttons. Setting only `waveform_diagnostics: true` compiles
the receiver instrumentation but does not add those YAML-defined controls.

For shared-filesystem development, both the packages and the local external
component must point to the diagnostic worktree, not a different checkout.
The package above lives at
`esphome/packages/diagnostics/proflame2_tembed_waveform_diagnostics.yaml` within that tree;
use its full mounted path if the device YAML lives elsewhere. Paths must be
visible inside the ESPHome Builder container, not only on its host. Rebuild and
install the firmware after changing includes; reloading HA alone cannot add the
controls to an already installed firmware.

After installation, open the controller's **ESPHome device** in Home Assistant
(not its Proflame2 fireplace device). Its normal controls include **Waveform
Capture Start** and **Waveform Capture Stop**, with a shared name prefix to keep
them together. They are deliberately not categorized as diagnostic entities. The package leaves both
entities enabled by default. If missing, check the device's full entity list for
disabled/hidden entities and verify that the build generated both button names
and logged `Proflame2 firmware version: v0.7.0-debug` on connection.

The package enables `waveform_diagnostics: true`, defining
`USE_PROFLAME2_WAVEFORM_DIAGNOSTICS`. Without that option, raw snapshot
storage, export code and diagnostic hooks are compiled out. The production RMT
buffer and acquisition settings remain unchanged. Do not use this diagnostic
firmware as the transmitting controller: normal TX and test-pattern entrypoints
are deliberately rejected. Configure the intended RF band. The package adds
normal **Waveform Capture Start** and **Waveform Capture Stop** buttons and
corresponding `proflame2_waveform_start`/`proflame2_waveform_abort` API actions.
Normal profile/listener/FIFO/capture controls (including HA's ordinary stop-RX
action) are ignored by diagnostic builds, preventing
HA synchronization from changing the acquisition configuration. Do not use this
receiver as a normal HA-controlled fireplace endpoint during measurement.
Configuration validation requires ESP32-S3 with the ESP-IDF framework for this
diagnostic option; production builds retain their existing framework behavior.

### Optional GPIO Edge Observer

To investigate whether RMT loses the beginning of a burst, include this additional
overlay **after** the waveform package, then rebuild and install the passive
receiver. The transmitter under test must remain unchanged:

```yaml
packages:
  waveform: !include ./packages/diagnostics/proflame2_tembed_waveform_diagnostics.yaml
  gpio_edges: !include ./packages/diagnostics/proflame2_tembed_waveform_gpio_edges.yaml

proflame2_tembed:
  waveform_session_ms: 90000
```

Adjust include paths to the same mounted development tree used for the component.
The overlay sets `waveform_gpio_edge_observer: true` and identifies the build as
`v0.7.0-debug-gpio`. It requires waveform diagnostics. Both options are false by
default; the existing waveform package alone does **not** enable GPIO observation.
`USE_PROFLAME2_GPIO_EDGE_DIAGNOSTICS` gates the entire observer, including its
interrupt handler, integration hooks and approximately 32 KiB of internal-heap
storage. Removing the overlay and rebuilding eliminates that instrumentation.

Use the existing Start/Stop controls and collector. No extra wiring is needed:
the observer reads the same GDO0 input, never changes its direction or drives it.
It timestamps GPIO interrupts continuously during an armed session, including
RMT rearming and log-export gaps. It stops when the session expires or is stopped.
Buffered captures inspect the bounded ring for a GDO0 rising edge after at least
30 ms LOW, near or after the RMT arm time. When found, this observed burst-start
hint anchors the requested 75 ms prehistory. It is **not** calibrated RF onset.
`PFBOUND1` records the anchor and whether it was observed; otherwise arm time
is the explicit fallback. Snapshots extend through their snapshot time and are
limited to the newest 2048 edges. The unbuffered control build uses arm time.
This is not a guarantee of a continuous 75 ms history.

`PFGPIO1` announces instrumentation. `PFEDGE1` exports a separately checksummed
timestamp/level stream; it is never prepended to RMT durations. Each packed
64-bit entry is `(timestamp_us << 1) | level_after_edge`. The importer preserves
these records under `gpio_observer`, separately from the original RMT data.
Missing chunks, bad checksums and missing companion records are rejected.

`clipped` identifies insufficient retained history; `raced` identifies a ring
overwrite during snapshot copying; `storm` identifies automatic interrupt
shutdown after more than 32 interrupts within a 1 ms budget window. An overloaded
observer remains stopped until a new session. `repeats` counts repeated sampled
levels, a possible indication of coalesced/missed transitions. These cumulative
health fields do not establish an exact number of lost physical edges.

ISR work is bounded: sample, timestamp, store and check the overload budget.
No logging, allocation or protocol decoding runs in the ISR. Internal storage
is allocated once per session, not on the task stack. Snapshot export is frozen
while the live ring continues collecting; each loop prints at most one line.

GPIO timestamps are **ISR service times**, not independently calibrated physical
edge times. ESPHome owns the shared GPIO interrupt service; the observer does
not uninstall or reconfigure it. Cache-off delivery is not guaranteed. GPIO
interrupts can coalesce, and the observer itself affects timing. A matching
leading waveform visible through GPIO but absent from RMT is useful positive
evidence against the RMT acquisition path. Absence from both paths is inconclusive
and still calls for SDR/oscilloscope comparison. Keep an observer-disabled build
as a control; do not change production TX timings based on this diagnostic alone.

### Capture Memory Snapshots

Waveform builds log INFO-level `PFMEM1` lines under `proflame2.memory` only at
capture lifecycle boundaries, never periodically or in the ISR:

- `before_allocate`: before allocating receiver buffers and drivers.
- `allocated`: after receiver/driver/optional GPIO observer allocation, before arming.
- `capture_end`: at Stop or automatic session completion, before cleanup.
- `released`: after retained data has been exported and resources released.
  The unbuffered control build may retain RMT resources after automatic pause
  until Stop or the next Start cleans them up.

All values are bytes. Each line reports free bytes, largest free allocation block
and minimum free bytes for internal 8-bit memory, internal DMA-capable memory and
PSRAM. Capability pools overlap: do not add internal and DMA free counts. Minimum
values are the allocator's boot-lifetime low-water marks, not a reset per-session
minimum. Largest-block values matter even when total free memory looks adequate.
PSRAM values are zero if no matching heap is registered; PSRAM does not replace
the internal/DMA requirements of these capture buffers.

The hooks compile out with waveform diagnostics. To measure, rebuild the passive
receiver using the existing waveform/GPIO packages, retain INFO logs, run a capture
and press Stop. No additional YAML option or periodic logging is needed. Preserve
all four phase lines; failed startup/cleanup may legitimately omit phases.

### Buffered Acquisition And Deferred Export

The waveform package enables `waveform_buffered_capture: true`, which defines
`USE_PROFLAME2_BUFFERED_WAVEFORM_DIAGNOSTICS`. No additional YAML include is needed
when rebuilding an existing waveform/GPIO configuration. The option defaults OFF
outside that package and requires waveform diagnostics. To reproduce the earlier
stop/export/rearm implementation as a diagnostic control, explicitly override
`waveform_buffered_capture: false` in the device component configuration.

Buffered mode requires enabled, available PSRAM. It fails explicitly if internal,
DMA or PSRAM allocations fail; it never silently substitutes the unbuffered path.
A 4096-symbol DMA input and two internal 4096-word slots separate acquisition
from the lower-priority main-loop consumer. The acquisition task has priority 6,
a 6144-byte stack and no explicit core affinity. It stops the completed receive
transaction, timestamps/seals its slot, and rearms a free slot **before** publishing
the completed slot to main-loop analysis. The ISR copies only reported RMT words
and metadata and signals the task; it never decodes or exports.

Main-loop analysis screens immutable completed slots, saves selected records and
their separate history/GPIO snapshots to a preallocated bounded PSRAM pool, then
releases the slot. The pool is sized by `waveform_max_exports`, not by the duration
of the session. Suppressed noise is processed and discarded after contributing
its bounded history. Large waveforms never live on task stacks. `PFSTORE1` logs
the requested buffer/pool and task-stack sizes; the optional GPIO observer's
internal buffers and RTOS/driver overhead are additional.

There are **no raw exports during acquisition**. Stop or the duration/retention
limit ends acquisition, drains queued slots and stops GPIO observation. `PFSTOP1`
acknowledges that acquisition has ended; selected data then exports incrementally,
at most one raw line per main-loop pass. The final `PFSESSION` line appears only
after retained records and resources are released. Do not press Start again until
that line or the collector's Stop confirmation. Stop does not discard pending
evidence. A new Start while capture/export is active does not replace the session.

The collector stops its SDR process on `PFSTOP1`, keeps the receiver log connection
open for export, and reports progress every 30 seconds while draining, with a
bounded ten-minute export deadline. Use the updated collector from the same tree.
The older firmware's direct final-footer flow remains supported.

`PFBOUND1` brackets each `rmt_receive()` call with before/after timestamps;
`PFACQ1` reports slot-pool, ready-queue, malformed-callback and retention losses, driver errors, the
acquisition-task stack low-water mark in ESP-IDF bytes, and acquisition end time.
Any reported loss/error makes the session failed and the strict importer rejects
it. Acquisition gaps are still possible and measurable; task scheduling is not
a promise of zero blind time or physical timing calibration. Failed shutdown
retains hardware/task-owned memory for cleanup retry, rather than freeing it
while callbacks could still use it.

The implementation uses RMT DMA with a 4096-symbol application buffer
instead of the production non-DMA 256-symbol buffer. It requires a supported
ESP32-S3/ESP-IDF build. Failure to allocate/enable the diagnostic channel is an
error, not a silent fallback to the smaller production capture. Verify actual
driver behavior on hardware before relying on completeness. DMA descriptor
capacity matches the receive buffer. Partial notifications are copied into an
internal 4096-word slot before descriptor reuse; reaching capacity
seals and flags the window rather than pretending that overwritten data survived.

After seven consecutive structurally plausible Proflame words are found, the recorder exports the
whole original RMT window, not reconstructed or rounded durations. This firmware
screen checks symbol guards, parity and word marker bits, without requiring a
learned remote identity or integrity formula. This retains legacy frames and the
seven-word prefix of extended frames, including terminal-half-bit truncation.
It is not a full integrity validator; the host importer performs frame
selection on a derived copy. Buffered exports use retained PSRAM records after
reception has stopped, so acquisition cannot overwrite exported measurements.
The feature changes receiver timing and belongs only in diagnostic builds.

The unbuffered control build stops before classification/export and does not
rearm until export finishes. Its export blind intervals are a deliberate control,
not the buffered mode's behavior. Neither mode pins tasks to a CPU core.

Diagnostic builds also retain **75 ms of preceding measured samples** from
completed acquisitions, including windows normally suppressed as noise. No YAML
change is needed; rebuild/install the updated receiver firmware. Whole raw words
are retained without rounding. The history is bounded to 4096 words/32 segments
and starts empty on each Start. This uses approximately 19 KiB per history copy,
in PSRAM in buffered mode and internal memory in the unbuffered control.
History is exported as separate source segments, not spliced into the selected
waveform. Arm/callback/seal timestamps, source offsets and gap/health metadata
remain attached. A `short=1` summary means less than 75 ms was available;
`limited=1` identifies a shortage caused by history storage limits. Acquisition
gaps and idle periods are not invented as samples. This does not guarantee 75 ms
of continuous coverage before the first RF edge or recover edges missed while
the receiver was unarmed. Buffered mode does not export until acquisition ends.

Sessions default to 30 seconds and at most 32 exports. Both limits can be changed
using `waveform_session_ms` (1000-120000) and `waveform_max_exports` (1-64) component
options. `waveform_capture_rejected: true` retains at most four additional
non-plausible windows within those budgets. Noise suppression is counted. Start
begins a new bounded session after the previous one finishes; Stop ends acquisition
and preserves retained data for export. The end state is logged
as paused, aborted, or failed. Collection is not automatically restarted by HA.
The duration bounds acquisition/rearming; an already sealed window is allowed to
finish its bounded export after that deadline rather than being silently cut off.
Use INFO logging or more verbose output; raw records use the `proflame2.raw` tag.
Keep the logger's line buffer at least 512 bytes. Preserve the log from before
pressing Start until after the session's terminal lifecycle line appears.

### Export Format

`proflame2.raw` INFO lines contain `PFRAW2` records and `PFSESSION` lifecycle lines.
The importer also reads the earlier prototype's `PFRAW1` records:

- Header: session/sequence IDs, symbol count, saturation indication, poll/arm times,
  tick resolution, glitch filter, idle termination threshold, and checksum.
- Health record: arm/callback/poll/seal timing, post-seal blind gap, callback and
  queue-loss counts, ignored callbacks/symbols, final-notification and timeout flags.
- Chunks: ID, zero-based symbol offset and up to 16 packed symbol words.
- Terminator: matching ID and `end=1`.
- Session closure: session ID, paused/aborted state, completed export count,
  suppressed-window count and monotonic end time. Failed sessions are not valid
  complete acquisitions. Restarting begins a separately identified session.

New diagnostic exports precede each selected `PFRAW2` window with `PFHIST1`
history records and a `PFPRE1` summary. Each history segment has a parent capture
ID, original source-word offset and its own header/health/chunks/checksum/end.
The summary binds the session/capture to its segment count and retained duration,
including empty/short history. The importer validates these records and retains
them under `pretrigger`, separately from the main words/edges used for matching.
Missing chunks, inconsistent summaries, orphan history or reordered segments
invalidate the import. Session export counts count selected windows, not history
segments. Older logs without pre-trigger records remain readable. Use the updated
host tools when collecting with the updated receiver.

Each eight-digit hex word explicitly packs duration0 in bits 0-14, level0 in
bit 15, duration1 in bits 16-30, and level1 in bit 31. Durations are microseconds.
Zero duration fields are preserved in the packed source. The checksum is FNV-1a
over the words' little-endian bytes, detecting transport corruption, not providing
cryptographic authentication. Preserve the original logs and separate each boot
and receiver into its own input file.

The diagnostic glitch filter is disabled (0 ns) to retain short runs; resolution
remains one microsecond. The idle limit remains 30 ms. PFRAW2's `rearm_us` header
field is a compatibility field containing that window's `arm_us`; it is not the
next window's future rearm time.

**Limitations:** callback time is an upper bound on receive completion, not an RF
edge timestamp. `gap_us` covers seal-to-next-arm processing/export time; compare
successive arm/callback records to examine additional possible blind time. Buffer
saturation, queue loss, ignored blocks and timeouts are flagged, but zero reported
loss is not proof of continuous RF reception. Hardware truncation, callback
latency and terminal-idle semantics still require independent validation. No
complete-burst or microsecond clock-alignment claim is made by software tests.

## Offline Import

From the repository root with the project Python environment:

```sh
python -m tools.waveform.waveform_diagnostics receiver.log --output selected.json --require-complete-sessions
```

The importer validates chunk ordering, counts and checksum, then outputs windows
containing recognizable legacy or extended frames. `--include-rejected` also
retains exported windows without a recognized frame. Enable the bounded firmware
rejected-candidate option as well when investigating malformed starts. The host
cannot recover windows the receiver did not export.

`--require-complete-sessions` also requires start/terminal lifecycle lines and
compares each session's exported count with the records actually present. This
detects a completely missing capture record as well as missing chunks. A session
aborted between complete records can be imported; aborting midway through export
leaves an incomplete record and is rejected. The output retains session totals
and explicitly reports whether session completeness was checked. It cannot
detect a whole session absent from the input or prove uninterrupted RF reception.
Omit this flag only to inspect older prototype logs or deliberately partial
diagnostic data; do not treat those as complete collection sessions.

Output preserves packed words and signed measured durations, with candidate raw
edge indexes, including repeated occurrences of the same frame rather than
deduplicating them as normal receive decoding does. The full measured window
also retains partial repeats that do not decode. Positive durations are high,
negative low. Terminal zero fields
remain in packed words but are omitted from the derived signed-duration list.
Legacy selection verifies structure/parity, not learned C/D integrity. Extended
selection also uses the existing extended integrity validator. Saturated windows
remain explicitly flagged and must not be used to assert a complete waveform.
The tool refuses to overwrite an existing output file.

`fit_sample_alignment` fits sample offset and clock slope from independently
matched RF events. It is unit-tested infrastructure, not automatic correlation.
Do not supply poll or log-arrival times as exact edge timestamps. The offline
matcher below adds coarse event matching and local edge fits. Independent
calibration of timestamp uncertainty and global receiver-clock anchors remains
pending.

## Offline RF Matching

`tools/waveform/waveform_rtl433` is a small offline adapter to the existing rtl_433
envelope/low-pass/pulse detector. It does not implement another RF demodulator
or require a Proflame-aware rtl_433 decoder. It exports integer sample indexes
and pulse/gap lengths before protocol decoding, so unrecognized RF events remain
available. The final low gap is a detector stop criterion, not an actual final
RF transition, and is kept separately. The adapter accepts continuous CU8 only;
other IQ formats supported by the slice utility are not accepted by this detector.

The existing rtl_433 VCD exporter is not the timing reference: it rounds times
and uses a rate-dependent scale. The adapter avoids that conversion. Its sample
indexes are still detector-derived edges, not calibrated physical RF edges.
Envelope filtering, thresholds, short-glitch suppression, overload and EOF
truncation need independent bench checks. A complete input file/footer does not
prove the SDR acquisition had no dropped samples. The detector caps each event
at 1200 pulses, flags capacity exhaustion, and fails after 4096 events rather
than producing an unbounded noise dump. Prefer short pilot recordings.

Prerequisites: a native C compiler, CMake, the reviewed rtl_433 source checkout,
and the project's development Python environment. No radio device is opened by
the analysis command. Existing foreign-architecture rtl_433 binaries are not
reused. Run from the repository root:

```sh
export PROFLAME2_RTL433_SOURCE=/path/to/rtl_433
CAPTURE=/persistent/captures/proflame2
tools/waveform/run_waveform_analysis.sh "$CAPTURE/pilot.cu8" "$CAPTURE/receiver.log" 1000000 "$CAPTURE/pilot-analysis"
```

Activate the Python environment first, or set `PYTHON` to its executable.
Stop both recorders before analysis; do not analyze files still being written.
`CMAKE` can similarly select a CMake executable outside PATH. The script builds
the detector in a unique `/tmp` directory and removes that build on completion,
failure or interruption. The explicitly requested output directory must not
already exist. Source IQ and receiver logs are never overwritten. A failed
report/slice export removes only its own newly created incomplete output tree.

For a controlled threshold-sensitivity replay, provide a fifth argument, a fixed
high-level estimate in dB (negative, down to -100). For example:

```sh
tools/waveform/run_waveform_analysis.sh "$CAPTURE/pilot.cu8" "$CAPTURE/receiver.log" 1000000 "$CAPTURE/pilot-fixed-level" -12
```

The default uses rtl_433 adaptive levels with a -12 dB minimum high-level estimate
and 9 dB signal/noise ratio setting. These settings and the selected fixed value
are recorded. They are detector settings, not calibrated RF power measurements.

Outputs:

- `events.json`: detector event sample positions and original sample-count runs,
  terminal detector gap, capacity indication, and derived frame candidates.
- `matches.json`: matched/unmatched/ambiguous/unhealthy results, all retained
  candidate associations, local fits/residuals/outliers, IQ slice hashes, and
  input/binary/Python-source hashes with detector settings.
- `event-NNNN.cu8`: exact IQ bytes around uniquely matched RF events, normally
  with 20 ms of context on each side and explicit context-clipping indication.

Matching first uses common decoded words and pulse-pattern evidence. Without
decodable words, only a distinctive high-coverage edge signature is eligible.
Repeated payloads in different RF events, competing window claims, or repeated
edge signatures are not disambiguated by prompt order. Ambiguous associations
remain explicit; the tool does not guess which button press or repeat they were.
IQ extraction uses the matched SDR event's own sample bounds, not an inferred
MCU timestamp, so a coarse matched slice can still be useful without a precision
clock fit.

When edge correspondence is unique, the tool fits receiver-window-relative
elapsed microseconds to SDR sample positions. Robust initialization exposes
outliers, which remain in the residual report. Excessive drift or inconsistent
anchors produces an unsupported fit. No measured durations are normalized or
rewritten by this operation, and a residual bound is not a physical uncertainty
budget. In particular, constant receiver/filter bias can disappear into the fit
offset while remaining physically important. Global MCU-clock alignment remains
unsupported, and `ready_for_precision_comparison` remains false until a separate
calibration establishes measurement suitability.

This stage has been exercised with IQ synthesized from archived native legacy
and extended frames. That validates software plumbing, not real RF calibration.
A simultaneous receiver/SDR pilot remains necessary before drawing
waveform-correction conclusions.

## Guided Collection

For a prepared native pilot configuration, run `tools/waveform/run_waveform_pilot.sh`
by its absolute path from any directory. It defaults to
`/private/config/native-pilot.json` and one Light/Fan
cycle plus the quiet sample.
Use `--dry-run` to print the sequence without opening devices, `--config PATH`
for another configuration, or `--cycles N` for a longer run. It uses the capture
host's native Python rather than assuming a shared virtual environment is portable.

### TX-Only Carrier Experiment

The current stability candidate uses ten copies per burst, delayed
retransmission OFF at each boot, and zero frequency offset. This is intended
for functional testing; eight copies had two reported misses within three
cycles, while ten copies passed the earlier short test. Sustained reliability
remains to be established. Local device substitutions take precedence:
remove earlier repeat-count `"8"` / offset `"60000"` overrides or explicitly
set them to `"10"` / `"0"` when testing these defaults. The optional delayed
repeat can still be enabled manually, but will return OFF after a reboot.
`Proflame2 TX Repeat Count` now shows the effective count (10 with these
defaults), not the overridden base count of 5. `Last Request Repeat Count`
continues to show the count used for the last request. Neither sensor changes
the integration's request contract or establishes fireplace reception.

On this investigation branch, the base package identifies firmware as
`v0.7.0-debug` even without the waveform receiver packages. Production/release
version stamping remains separate; a debug version is not a release tag.

The transmitting controller has a YAML-only, signed frequency offset in Hz:

```yaml
substitutions:
  proflame2_tx_frequency_offset_hz: "60000"
```

The default is **0**, which preserves the unadjusted carrier. Values from
`-100000` through `100000` are accepted; invalid values are rejected. For the
first measured comparison, +60000 Hz is a hypothesis for this particular remote,
not a recommended universal setting. Rebuild/install after changing YAML.
Do not apply the offset to the passive diagnostic receiver. That receiver still
uses its waveform packages and capture duration; a normal transmitting controller
does not need `waveform_session_ms` or either waveform package.

This adjusts the TX frequency passed to the CC1101, not `FSCTRL0`, RX frequency,
band-switch selection, logical pulse timing or retry policy. Actual carrier
frequencies are quantized to CC1101 synthesizer steps and still depend on its
oscillator. Startup and the throttled API-connect version report include
`RF tuning: tx_hz=... rx_hz=... tx_offset_hz=...`; these are configured frequencies,
not RF measurements. For band 315 and +60000, expect configured TX 315033000 Hz
and RX 314973000 Hz. Verify the emitted shift with SDR before judging fireplace
acceptance. For nonzero-offset FIFO operation, leaving RX explicitly restores
the TX register profile; RX restoration reloads the unadjusted receive profile.
RMT already reloads its own separate TX/RX profiles. Zero-offset transitions are
unchanged. A rejected send or retry completion also restores the receive carrier.

Record the tested value with the capture, without changing SDR center tuning:

```bash
tools/waveform/run_waveform_pilot.sh --source lilygo --sdr-gain 0.9 --gain-check \
  --source-tx-offset-hz 60000
```

`--source-tx-offset-hz` is **operator-reported metadata only**. It does not send a
command or configure either controller. Confirm the value in the transmitter's
INFO log first. To isolate this experiment, establish a working zero-offset
baseline after any ESPHome platform upgrade, then change only the TX offset.

### Lower-Gain Capture Procedure

To screen for SDR overload without changing either LilyGO's firmware, run:

```bash
tools/waveform/run_waveform_pilot.sh --source lilygo --sdr-gain 0.9 --gain-check
```

Use the same receiver configuration and transmitter position as the previous run.
This short sequence asks for **Light 6, then Light 0**, using the HA controls on
the transmitting LilyGO. Follow the terminal's Start, target/Return and Stop
prompts for each trial; no separate log screen is needed. The usual 60-second
operator allowance and 15-second repeat-recording interval remain unchanged.
There is no Fan or quiet trial. The receiver still needs a 90-second raw session.

The override applies only to this attempt: the saved configuration, SDR center
frequency/sample rate and transmitter firmware/settings remain unchanged.
`rtl_433 -g 0` selects **automatic** gain, not minimum manual gain; `0.9` requests
low manual gain. The tuner may select a nearby supported gain. Recorder notice
logging is enabled to retain its reported value; missing confirmation fails the
check while preserving the evidence. `--source lilygo` labels the source correctly
and clears inherited native-remote inventory fields; firmware/profile details
still need operator verification. For a native-remote comparison, use
`--source native` instead, with the same gain and physical setup.
The gain override explicitly adds `-F log:/dev/stderr` as well as `-v`: the
baseline `-F null` otherwise suppresses tuner notices even at increased verbosity.
Packet output remains disabled, and the confirmation is retained in `sdr.stderr`.

After each Stop/export, the terminal reports sampled clipping statistics and the
reported gain. Each trial retains `gain-check.json` alongside the raw CU8/logs,
included in the hashed manifest and archive. Statistics sample every 32nd IQ pair
and report the total rail-hit fraction and worst approximately 100-ms block.
No observed clipping is **not** proof that RF was received: recordings still
require frame detection and offline review before timing/carrier comparisons.
If clipping persists, retain this run for review rather than changing several
parameters at once. Use `--dry-run` to check the two-target sequence without
opening hardware. Omit `--gain-check` to run the full normal pilot at the chosen
gain; existing invocations without overrides retain their original behavior.

Every real attempt gets a new private `proflame2-pilot-*` directory under
`/persistent/captures/proflame2/runs`. Use `--output-root PATH` to select
another persistent destination; `/tmp` is rejected for retained recordings. The
launcher prints its path, retains original records on failure/abort, and packages
a verified `session.tar.gz` when possible. These are requested results, intentionally
retained for review; test/build scratch is separate and cleaned automatically.
The archive remains private, including for failed attempts. Existing attempts are
never replaced. Common USB-access failures produce a short explanatory hint rather
than requiring the operator to interpret a Python traceback.
Storage is checked before each trial and during capture, with reserved metadata
headroom and exclusive, atomic publication of JSON files. Errors distinguish
`EDQUOT` (the user's quota is exhausted despite filesystem free space) from
`ENOSPC` (insufficient filesystem space). A free-space check alone cannot prove
that user quota is available. Never automatically delete earlier attempts.
The shared capture root is private development data, outside Git, and is visible
on the development hosts through their shared `/srv` mount. Test/build scratch
alone belongs in `/tmp` and is cleaned on success, failure and interruption.
Legacy captures can be copied with `tools.waveform.waveform_relocate SOURCE DESTINATION`;
it verifies every file's size/SHA-256 and source stability before removing the
original. Stop recorders first. Existing destinations and links are refused.
After the last Stop confirmation, the launcher announces archive creation; no
more controls are needed. Large IQ recordings may take minutes to compress and
verify. Source checking, compression and verification each report their phase,
processed MiB and elapsed time, approximately every 30 seconds while reading
data. Compression/verification also show phase-specific totals and percentages;
compression reaching 100% is followed by the independent verification pass.
Future archives use fast, lossless gzip compression. Wait for the archive
path and `ALL DONE` before closing the terminal; original files remain intact.

On Linux, the capture account needs access to the SDR's USB device. If it is
owned by `root:plugdev` with group read/write access, use the existing group policy
rather than running the whole capture as root. Reconnect the login session after
an administrator changes group membership. Seeing a device in `lsusb` alone does
not prove it can be opened. The collector now waits for IQ data and, for its Docker
logger, the API handshake before requesting that the operator arm the receiver.

`python -m tools.waveform.waveform_collect CONFIG.json /persistent/captures/proflame2/runs/NEW-SESSION` runs five fixed
cycles of Light 6, Light 0, Fan 6, Fan 0, followed by a separate 60-second quiet
sample. `--cycles 1` selects a shorter pilot; `--dry-run` validates configuration
and prints prompts without opening recorders. Run separate sessions for each
source, then reverse source order in a subsequent block. The runner never sends
fireplace commands or automatically substitutes another requested action.

Before collection, establish manual mode, thermostat off, power on, flame 6,
light 0, fan 0. Leave pilot/aux/front unchanged and record their known settings
in the private inventory. No physical fireplace observation is required.

The trusted local JSON configuration contains:

- `inventory`: an object conforming to `waveform_schemas/inventory.json`.
- `session`: an object conforming to `waveform_schemas/session.json`, with source
  and receiver aliases identifying inventory entries. Include the baseline tool
  commit, installed SDR tool version, actual sample rate/frequency, fixed gain,
  and `sample_format: "cu8"`. Keep dropped samples null unless independently known.
- `receiver_command`: an argument array for a **live, unbuffered** ESPHome logger
  writing receiver messages to standard output. Do not replay cached logs.
- `sdr_command`: an argument array for continuous CU8 recording, containing
  exactly one `{iq}` output-path placeholder. Standard output and error are
  captured separately; the recorder must write IQ directly to that path.
- `device_lock`: a shared short alias for the physical SDR. Use the same alias
  for every collector using that device on this host.
- `recorder_options_verified: true`: an explicit acknowledgement that the installed
  recorder's help/options, selected device, frequency, sample format/rate, and fixed
  gain were checked. Commands are not inferred from a different machine's version.

Commands are executed directly, not through a shell. Only supply trusted local
commands. The runner does not inspect or guess credentials. The cooperative SDR
lease prevents concurrent instances using the same alias; it does not fence other
programs or other hosts. Stop other SDR users before starting. This software has
not yet been validated against the installed physical recorder on the bench.

For this protocol set `waveform_session_ms: 90000` in the optional diagnostic
package override so the receiver can cover the quiet sample. Recorders start
before the runner asks you to press **Waveform Capture Start**. It waits for an
armed session and nonempty IQ output, preserves at least two seconds of pretrigger,
then displays the requested action. This readiness check does not prove RF accuracy
or continuous samples. Arm-time, prompt-time and RF edges are different concepts.

Use the native remote's Up/Down buttons repeatedly until its display reaches the
requested target. Keep the fireplace ON in manual mode. You have up to 60 seconds
to reach each target. Only after reaching it, press Return ONCE in the terminal;
check the selected feature icon before pressing Up/Down because some remotes
automatically return to Flame after an idle interval. If the wrong feature changes,
record that fact; do not discard the original RF samples or relabel them as the
requested feature. Raw decoded state, not the trial filename, determines what
was transmitted.
When marking the target,
do not press Return after each remote button press. Keyboard input queued before
the next TARGET prompt is discarded, with a status message, so a Return from an
earlier step cannot automatically confirm the new target. A queued `q` still
aborts. No Return is required when clicking the receiver's Start/Stop controls.
Optional text such as
`actual light=5` before Return records an accidental selection without relabeling
it as 6. The script visibly acknowledges the operator's confirmation and then
records another 15 seconds while you leave the controls untouched. This marker
is not a measured RF-edge timestamp. The final 60-second quiet sample requires
no Return and no remote/HA actions.

At the Stop prompt, click **Waveform Capture Stop** and wait for **STOP CONFIRMED**.
Each subsequent trial requires a NEW click on **Waveform Capture Start**, only
when its own Start prompt appears; wait for **START CONFIRMED** before touching
the remote. Start, target confirmation and Stop are three separate steps. The
collector waits for completed exports and the matching footer before advancing.
Early receiver closure, recorder death, excessive output, or incomplete exports
fails the session rather than silently continuing. Enter `q` or use Ctrl-C to abort;
the receiver may still need its Stop control, but owned recorder processes stop.

The new output directory contains `inventory.json`, `collector.json` (fixed plan
and collector source hash), `session.json`, and one directory per trial containing
`signal.cu8`, receiver/SDR stdout/stderr, `trial.json`, and `acquisition.json`.
Recorder argument arrays are retained privately in `collector.json`; use credential
files or the environment rather than embedding tokens/passwords in those arguments.
The last file records recorder start/stop markers, every operator marker, and IQ
sample count. Failed acquisitions are retained, including silence and partial
recordings. No RF onset, decoded state, retry relationship, or physical outcome
is invented: those fields remain unknown/unobserved until offline analysis or
explicit annotation establishes them. Automatic RF association/final decoded-state
annotation remains follow-on work.

Verify `session.json` with the manifest tool before transferring the private
directory. Artifacts are hashed, but hashes do not establish RF/sample continuity.
Only a complete session is eligible for comparison; an aborted/failed one remains
useful evidence. Feed each completed trial's `signal.cu8` and `receiver.stdout`
to `run_waveform_analysis.sh`. Do not analyze files while recorders are running.
Raw sessions can contain network and remote identifiers: do not post them publicly
without review. Keep the originals private and scrub future regression fixtures.

### Private Handoff and Observations

Package a stopped session and verify the transferred archive without extracting it:

```sh
CAPTURE=/persistent/captures/proflame2/runs/NEW-SESSION
python -m tools.waveform.waveform_archive pack "$CAPTURE" "$CAPTURE.tar.gz"
python -m tools.waveform.waveform_archive verify "$CAPTURE.tar.gz"
```

Packaging refuses an existing destination, undeclared or missing files, unsafe
paths, symlinks, bad hashes and inconsistent inventory/trial references. A complete
session must have complete acquisition metadata, aligned CU8 byte/sample counts,
all recorder streams and valid receiver session closure. Silence with a valid
zero-export footer is permitted. Failed/aborted evidence can also be packaged;
verification reports its original status without upgrading it to success.

Archives use relative regular-file entries and omit filesystem owner names, IDs
and host timestamps. They still contain the original private log/metadata content:
this is **not** anonymization. Review before sharing, and never post raw archives
publicly by default. Archive verification checks structural completeness and hashes,
including compressed-file transfer integrity; it does not establish calibration,
correct RF associations or fireplace reception. Source files remain untouched.
Packaging scratch is private, bounded and removed on failure/interruption; only
the requested final archive remains on persistent capture storage. Archive staging
uses a private sibling directory for atomic publication on the same filesystem;
that ephemeral directory is always removed.

An explicit observation can be saved outside the original session:

```sh
python -m tools.waveform.waveform_observation "$CAPTURE" trial-001-light_6 \
  /persistent/captures/proflame2/light-selection-observation.json \
  --operator-state '{"light":5}' --outcome unobserved \
  --notes 'Actual slider selection was 5; physical response was not observed.'
```

This creates a separate, exclusive JSON record bound to the original session and
trial hashes. `operator_state` is the actual reported selection, not a decoded RF
state. Outcome may be `unobserved`, `ambiguous`, `no_response`, `before_retry`, or
`after_retry`; do not choose a physical-response outcome merely because local TX
reported success or another LilyGO mirrored the packet. RF association stays null.
Keep these observation files alongside the private archive when transferring them.
They do not alter its original manifests or hashes. Final RF associations still
require the offline matcher and review of its uncertainty/ambiguity flags.

### Docker Receiver Logging

Use `tools.waveform.waveform_docker_logs` rather than a bare `docker exec` as the receiver
recorder. It uses non-TTY interactive stdin only for a heartbeat; RF logs stay on
stdout and CLI diagnostics stay on stderr. The in-container supervisor terminates
its own logger on EOF, two seconds without a heartbeat, or its 240-second deadline.
It does not restart the container, reset the controller, or send control actions.
The host wrapper has a shorter bound and forwards shutdown by closing stdin.
No source/configuration is copied into the container and no temporary files are
created there. An unattended wrapper is therefore not an indefinite log subscriber.

Generate a private configuration from the capture host, in this checkout:

```sh
python3 -B -m tools.waveform.waveform_pilot_config /private/config/native-pilot.json \
  --container esphome --configuration /config/RECEIVER.yaml \
  --band 315 --gain 20 --tool-version VERIFIED_RTL433_VERSION \
  --recorder-options-verified
python3 -B -m tools.waveform.waveform_collect /private/config/native-pilot.json \
  /persistent/captures/proflame2/runs/native-pilot \
  --cycles 1 --dry-run
```

Replace the YAML/version placeholders and verify the selected SDR (index 0),
requested fixed gain and band before using the verification flag. The generated
SDR command disables implicit configuration and signal-triggered file saving,
records one continuous CU8 stream per trial, and performs no decoding during
acquisition. Actual tuner gain/continuity still require inspection of recorder
diagnostics during the pilot. The template does not invent device build/profile
details; complete those null inventory fields from the actual installed firmware
and settings. Use `--source lilygo` for a separate transmitter session.

The dry run opens neither recorder. After the receiver's optional diagnostic
firmware is configured for a 90-second session and the inventory/settings are
reviewed, omit `--dry-run` to begin the operator-assisted pilot. Recordings/configs
are private; the output paths must not already exist. This invokes the installed
Docker ESPHome version, not an older host virtual environment. Standalone decoding
loads the shared pure protocol modules in a private namespace without initializing
Home Assistant or changing the integration's real import path.

## Manifests and IQ Windows

Versioned schemas in `tools/waveform/waveform_schemas` describe software inventories,
sessions, and trials. Unknown values are explicit nulls. Trial outcomes distinguish
physical response before/after retry from no response, ambiguous, and unobserved.

```sh
python -m tools.waveform.waveform_manifest inventory inventory.json
python -m tools.waveform.waveform_manifest session manifest.json --verify-files
python -m tools.waveform.waveform_iq recording.cu8 selected.cu8 --format cu8 --start-sample 100000 --end-sample 200000
```

The sample indexes above are illustrative, not recommended capture boundaries.
Choose them from independently matched RF events. The slicing tool supports CU8,
CS16, and CF32, copies only selected byte ranges with bounded memory, hashes the
result, and refuses to overwrite files. `sample_window` expands aligned bounds
for context/uncertainty and reports pre-trigger clipping. It does not infer valid
anchors or prove that original raw IQ has no missing samples.

## Validation and Remaining Work

Host tests cover payload logging, HA switch behavior, raw packing/checksum,
chunk and whole-record loss, staged export/abort, driver/allocation/shutdown
failures, frame selection, clock fitting and compiled helper
exclusion. They do not establish RMT driver accuracy or fireplace compatibility.

Before physical comparison, validate DMA reception, full-burst capture, loss
indicators, and clock anchors using known input and SDR. Bench-check the offline
matcher, complete calibrated alignment and pilot the guided session collector.
Measure native timing distributions and corresponding LilyGO deviations before
proposing any transmit correction. Keep reusable code/tests/documentation in Git;
keep identifying raw sessions and the investigation checklist private.
