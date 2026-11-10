# Diagnostic Acquisition Design

## Audit Baseline

This audit targets ESP32-S3 and ESP-IDF 5.5.5, matching the reported investigation
SDK. Recheck other SDK versions rather than assuming identical driver internals.
The buffered revision is also compiled against ESPHome 2026.10.1 / ESP-IDF 5.5.6.

The [RMT RX driver](https://github.com/espressif/esp-idf/blob/v5.5.5/components/esp_driver_rmt/src/rmt_rx.c)
allocates DMA descriptors from `mem_block_symbols` and checks the application
buffer against that capacity. The prototype's 1024-symbol configuration cannot
support its 4096-symbol application buffer. Use matching capacities and aligned
internal DMA memory. PSRAM is not supported for this receive buffer in that SDK.

The descriptor chain is circular. Without partial notifications, counting only
the final callback does not establish that earlier buffer contents survived.
Enable partial reception and copy each reported block into bounded, independently
owned storage before its DMA descriptor can be reused. Record capacity exhaustion
and stop rather than concatenate overwritten data. This copy costs ISR time;
its effect needs bench measurement.

The [public RX API](https://github.com/espressif/esp-idf/blob/v5.5.5/components/esp_driver_rmt/include/driver/rmt_rx.h)
places callbacks in ISR context and distinguishes partial delivery from final
reception. Cache-safe builds require ISR code and context in internal memory.
Reception stops on the configured idle threshold; a final callback is not itself
a timestamp of the last RF edge. Timing anchors must include callback latency
and terminal-idle uncertainty.

## Bounded Lifecycle

The waveform package now opts into buffered acquisition. One internal DMA input
and two internal raw slots have explicit `FREE`, `FILLING`, `SEALED`, `READY`, and
`PROCESSING` ownership. ISR delivery copies ordered raw words and metadata into
the filling slot, seals on final delivery/capacity, and notifies the acquisition
task. No decoding, allocation, logging or rearming occurs in the ISR.

The priority-6 acquisition task stops the completed RMT transaction, timestamps
and seals it, rearms a free slot, then queues the completed index. Main-loop
analysis cannot reuse a filling/ready slot; the producer cannot reuse a processing
slot. Analysis saves selected snapshots/history to a bounded PSRAM pool and
releases the internal slot. No free slot, a failed queue send or exhausted
retention is explicitly counted and stops acquisition rather than overwriting
measurements. Session duration/export/rejected limits remain bounded.

Stop/deadline drains already completed work and retains any delivered partial
prefix with its final/timeout flags. After task acknowledgement and confirmed
hardware shutdown, selected records export one line per loop pass. A final
session footer follows complete export and resource release. Another Start cannot
discard an active/draining session. A stop is not evidence of a complete burst.
The `waveform_buffered_capture: false` control retains the earlier synchronous
stop/classify/export/rearm implementation for comparison.

The receiver never transmits. Diagnostic acquisition is not a way to change the
transmitter's pulse schedule. Production builds omit the context, storage,
callbacks, controls and export code.

## 75 ms Pre-Trigger History

Main-loop analysis saves a rolling tail of completed acquisitions,
including suppressed noise/rejected windows. Each selected window exports this
history separately before its original measurements. The target is 75,000 us of
measured durations; whole packed words are preserved, so the retained duration
may slightly exceed the target. No durations are rounded, clipped or synthesized.
The selected window itself is unchanged. Session Start clears the history.

History is bounded to 4096 words and 32 source segments. Its summary reports
the requested/retained duration, `short` when fewer than 75 ms are available,
and `limited` when storage pressure prevented retaining the target. Startup can
also produce short history. Per-segment source offsets, arm/callback/poll/seal
times, health flags and sequence IDs preserve provenance and acquisition gaps.
Each selected record owns a frozen history copy. Live history is updated after
selection; export does not borrow live acquisition data.

This is 75 ms of retained samples, not a guarantee of continuous wall-clock
coverage immediately before a frame. Silence, rearm/export blind intervals and
unacquired edges cannot be reconstructed. Do not concatenate the segments into
one edge stream or treat their timestamps as calibrated RF-edge anchors. GPIO
snapshots separately use an observed rising-edge-after-long-LOW burst hint, or an
explicit arm-time fallback, to request 75 ms of history. This is not RF onset.
Earlier
captured prefixes can now be inspected instead of being discarded; determining
whether they contain the missing leading burst remains a bench test.

## Selection and Evidence

Firmware performs a conservative plausibility screen on a derived PCM copy.
The diagnostic-only screen looks for seven consecutive words with symbol guards,
parity and layout markers. It deliberately accepts the common seven-word prefix
of ten-word frames without depending on their integrity formula. It scans all
measured runs, not only the longest production-converted segment, and preserves
the complete window including preceding noise and partial subsequent repeats.
The filter is tested against archived legacy and extended remote data; malformed
rows remain available through the bounded rejected-candidate option.
Host classification uses the existing seven/ten-word decoder and reports raw
edge indexes, preserving the measured window and repetition context. Structural
recognition is not proof of learned legacy integrity. A bounded rejected-output
option retains malformed candidates for inspection.

Record arm, callback, poll and seal times separately, plus the prior blind gap,
partial/final callbacks, queue losses, saturation and timeout. Callback latency,
RMT idle behavior and DMA reuse deadlines need hardware validation. Even zero
reported queue loss does not prove that no RF edges were missed.

## Resource and Validation Budget

Buffered mode allocates one 4096-symbol DMA input and two 4096-word internal
slots, approximately 48 KiB plus metadata. The acquisition task has a 6144-byte
stack and no explicit core pinning. Optional GPIO observation adds approximately
32 KiB of internal memory. Live history and the fixed retained-record pool use
PSRAM; no ISR or DMA accesses PSRAM. Records include raw words/history and, when
enabled, a GPIO snapshot. Capacity follows `waveform_max_exports`, not elapsed
seconds. These allocations are checked up front; failure is explicit and no
internal-memory fallback is attempted for PSRAM storage. Large buffers are not
task-local. Classification/export are outside the acquisition task. The
structural screen uses a 26-byte fixed streak array and scalar state, with no
PCM-sized allocation. Export uses a 129-byte chunk buffer and emits at most one
raw line per loop pass. These are source-level bounds, not measured MCU stack
high-water marks. Runtime stack/ISR latency and heap headroom still need bench
measurement. `PFMEM1`/`PFSTORE1` report memory at lifecycle boundaries and requested
storage sizes; `PFACQ1` reports the worker stack low-water mark and data-loss/error
counts. `PFBOUND1` brackets the actual `rmt_receive` call. They describe software
boundaries, not exact RF edge times. Both diagnostic modes compile out of ordinary
firmware, including the worker and PSRAM pool.

Completed-record counts are emitted only after the raw terminator. Session
closure allows the importer to detect an entire missing record. In buffered mode,
Stop preserves retained records for complete deferred export. In the unbuffered
control, aborting during export intentionally leaves an incomplete record. Failed
shutdown retains task/hardware-owned buffers until safe cleanup is confirmed.
The task performs no context access after acknowledging completion; main owns
task deletion and resource release. Tests inject allocation/setup/shutdown faults
and exercise producer/consumer ownership with a threaded host RTOS model under
address/undefined-behavior sanitizers. Strict import rejects reported losses even
if a damaged log otherwise claims successful closure.

Required checks include partial blocks, final blocks, exact capacity, overflow,
late callbacks, timeout, failed rearm, queue loss, count/time budgets, allocation
failure and compile-time exclusion. Mock-driver tests establish software behavior;
board builds and a simultaneous SDR pilot establish measurement suitability.
