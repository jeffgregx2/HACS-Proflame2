# Alpha1/Beta1 Radio Register Comparison

## Purpose

Compare CC1101 hardware readbacks from two isolated diagnostic branches:

- `issue-26-alpha1-radio-trace`, based on `v0.7.0-alpha1`.
- `issue-26-beta1-radio-trace`, based on `v0.7.0-beta1`.

The initial branches used identical instrumentation. Beta1 now uses schema 4
with deferred worker snapshots, runtime experiment controls, bounded radio
readiness checks, and an optional delayed retransmission. The alpha1 diagnostic
branch remains on schema 1. Beta1 preserves the RF register values, packet
encoder, individual-symbol timings, and normal repeat-gap targets. It now checks
SO before SPI headers, confirms IDLE after SIDLE, and waits longer for confirmed
TX. Timeout/recovery paths can extend handoff and repeat-gap times; these builds
are no longer an instrumentation-only comparison.
The diagnostic branch base package enables `PROFLAME2_RADIO_TRACE=1`
automatically. These are investigation builds, not replacement release tags.

## Build

After the branches have been committed and pushed, set
`proflame2_package_ref` in the device substitutions to the appropriate branch
name or its separately created diagnostic tag. Keep the base and display
packages and external component source on that same reference. Do not build
with a local external-component override unless it points at that branch's
worktree. Existing release tags are not moved or replaced.

Use the same ESPHome version (2026.8.2), device YAML, logger level, receiver
path, frequency, and learned profile for both builds. Build and install each
version on the SAME physical controller, in the SAME location, before treating
results as a firmware A/B comparison. Different controllers introduce a hardware
variable. Avoid additional debug packages or settings that differ between builds.

INFO logging for `proflame2_cc1101` is sufficient; no manual capture mode is
required. The ordinary startup version still identifies the original release,
but `RFTRACE diagnostic build base_version=... schema=4 worker_logs=deferred readiness_timeout_us=10000 spi_ready_check=YES`
identifies the current beta1 instrumentation. Each snapshot also ends with
`RFTRACE seq=N end=1`.
New traces require rebuilding and flashing, not just changing the HA integration.

## Runtime Experiment Controls

The controls are in the ESPHome device's Configuration section in HA. They
require this diagnostic firmware, not the optional debug YAML package.

- **Radio Register Trace Logging** defaults to OFF. OFF skips both snapshot
  register reads and RFTRACE output, including previously captured records
  awaiting output. Normal radio-operation SPI traffic, TX results, and existing
  receive logging are not disabled. A single confirmation logs each setting
  change. Re-enabling resumes tracing without rebooting.
- **Pre-TX Settling Delay** defaults to 20 ms, with a range of
  0-100 ms in 0.1 ms steps. The setting is copied into each queued TX request,
  so a later edit does not alter an in-flight command. TX result logs include
  the applied `pre_tx_settle_us` value even when tracing is OFF.

Neither setting persists over a reboot: each boot restores trace OFF / delay 20 ms.
Change controls only while the radio is idle so a capture is not intentionally
split between tracing ON and OFF. The trace switch is atomic across tasks.

- **Delayed Retransmission** defaults to OFF and resets OFF at reboot. When
  enabled, a successful normal command can send one extra full burst two seconds
  after the worker finishes the original burst, subject to main-loop scheduling.
  This uses the exact cached payload, frame count, timing profile, and settling
  delay from the original command. It does not alter the five repetitions
  already inside that burst, and the extra burst cannot schedule another retry.
- RX remains active during the two-second wait. Only the actual retry pauses RX,
  using the same handoff as a normal command and restoring it at completion.
- Any successfully queued newer normal command replaces the pending retry and
  starts a fresh window when that new command finishes. Rejected/unqueued
  commands do not replace it. A queued test pattern cancels it without arming
  another retry. Cancellation invalidates an in-flight original completion too.
- Accepted FIFO packets (including deduplicated repeats), changed HA display
  state, learning, listener disable/profile changes, and HA disconnect cancel it.
  Restoring the **same** listener after HA's normal stop-RX / TX / resume-RX
  cycle does not count as a profile change. Firmware retains the last enabled
  profile only for retry invalidation, while still clearing the active RX
  acceptance profile on disable. Changing the remote ID, C/D constants, or
  strict/learning profile does invalidate the retry.
  RMT captures are validated by HA, so **any usable RMT capture conservatively
  cancels it before HA validation**. This favors avoiding stale commands over
  retrying through RF activity; a noise capture can suppress the optional retry.
  This policy does not make raw captures semantic evidence.
- Retry scheduling, cancellation, queue failure, and result have separate INFO
  or warning messages. The extra burst never overwrites original-request HA TX
  confirmation telemetry, reapplies an old display intent, or increments normal
  request success/failure counters. A failed retry is logged, not retried again.

## Radio Readiness And SPI Audit

ESPHome 2026.8.2 `SPIDelegateHw::begin_transaction()` acquires the bus and asserts
CS through `SPIDelegate::begin_transaction()`. It does not implement the CC1101
SO/CHIP_RDYn handshake. The component now borrows the existing SPI bus MISO pin
from code generation (no second GPIO setup or changed wiring) and checks it
after chip selection, before every register/status/burst/strobe header. The bus
remains acquired throughout. A timeout logs a warning and proceeds best-effort;
a continuously stuck SO reports once until a successful handshake rearms the
warning. Required readiness reads remain active when diagnostic tracing is OFF.

All SIDLE strobes now wait for two consecutive `MARCSTATE=IDLE` reads before
callers flush FIFOs or update configuration. A timeout logs and reissues SIDLE
once, then performs another bounded wait. If IDLE is still unconfirmed, it logs
the last state and proceeds as explicitly requested for this experiment. No
automatic reset or speculative register changes are performed. Applying the TX
configuration also explicitly enters IDLE first.

After STX, the TX path requires two consecutive TX-state reads. On timeout it
logs the last state and proceeds best-effort, preserving the fallback policy.
This is **not a guarantee that an unready radio transmits correctly**; software
`status=ok` still means the emission routine completed, not fireplace acceptance.

The YAML substitution `proflame2_radio_ready_timeout_us: "10000"` configures
`radio_ready_timeout_us` for each SPI, IDLE, and TX readiness wait. Supported
values are 100-100000 microseconds. The default is 10 ms per attempt; IDLE can
use two attempts, and a stalled SPI handshake can extend a state-poll attempt.
This timeout is separate from the runtime **Pre-TX Settling Delay** setting.
TI Table 34 gives typical calibrated IDLE-to-TX times around 799 us, not a
universal worst-case deadline. Ten milliseconds is a conservative diagnostic
timeout, not a datasheet-mandated constant.

References: TI CC1101 datasheet Sections 10, 19, and Table 42:
https://www.ti.com/lit/ds/symlink/cc1101.pdf

## Settling Delay Experiment

The delay adds the configured time to the first RF-edge deadline after the
existing TX-ready check and pre-burst/pre-frame guard calculations.
It does not delay the STX command, modify symbol durations, or insert a delay
between repetitions. It is applied once per burst to the production
`proflame_native_groups` path, for both seven- and ten-word frames and both
continuous-TX and reenter-TX repeat modes. Other experimental TX modes reject
a nonzero setting instead of silently ignoring it. Diagnostic test patterns
are outside this command-path experiment.

The diagnostic default of **20 ms** is a deliberately generous test setting,
not a production recommendation or guarantee of readiness. TI's CC1101 datasheet Table 34 gives typical
IDLE-to-TX times of about 75 us without calibration and 799 us with calibration
(26 MHz crystal, no PA ramping). The extra 20 ms exceeds those typical times
substantially but cannot repair a rejected strobe or invalid state.
Source: https://www.ti.com/lit/ds/symlink/cc1101.pdf

### Controlled Test

1. Keep the same firmware, controller placement, SDR placement/gain, HA
   integration, and commanded light/fan sequence throughout. Leave Delayed
   Retransmission OFF while comparing readiness/settling behavior.
2. Reproduce rejection with trace ON / delay 0. Save complete logs and raw I/Q.
3. Change only the delay to 20 ms. Repeat the same sequence and record acceptance
   counts and request IDs, not just whether one command worked.
4. Return to 0, then 20 ms again to check that reliability follows the setting.
   Do not change trace state during this comparison.
5. If the effect is repeatable, sweep downward (10, 5, 2, 1, 0.5, 0.1, 0 ms),
   retaining each setting's result counts and captured failed/successful bursts.
6. Separately repeat the 0/20 ms comparison with trace OFF to measure the effect
   of snapshot reads and logging. Standard TX logs still identify the delay.

No extra commands are generated unless Delayed Retransmission is enabled.
Rebuilding adds code and can change
binary layout even with default settings; reproduce the baseline on this build
before treating the experiment as causal.

Separately enable Delayed Retransmission, verify an original burst plus one
burst about two seconds after completion, then issue a new command during that
window and verify only the newer command gets its extra burst. Repeat with a
native-remote change and verify cancellation instead of replaying old state.
Keep SDR capture and INFO logs running to correlate the separate retry messages.

### Root-Cause Follow-Up

- Correlate accepted/rejected identical payloads with actual SDR waveform
  timing, repeat boundaries, carrier onset, and signal conditions.
- Characterize STX-to-TX-ready and TX-ready-to-first-edge timing under different
  workloads; distinguish MARCSTATE polling success from its timeout fallback.
- Review SPI/chip readiness, strobe preconditions, calibration/PLL state, and
  CPU/task scheduling before replacing a validated readiness policy with sleeps.
- Verify legacy and extended hardware behavior before proposing a beta2
  workaround. A delay that improves one setup is not proof of protocol support.
- Keep the delay experimental until its effect is reproducible. The desired
  final solution is an understood readiness/state transition, not an arbitrary
  permanent pause.

## Validation Scope

Host tests execute the real SPI/register driver and real legacy/extended TX
scheduler against a deterministic fake radio. They cover slow IDLE, SIDLE
recovery, permanent IDLE/SO/TX failures with bounded logged fallbacks, SPI
handshake ordering on all access forms, TX startup beyond the previous 1.5 ms
limit, and unchanged normal symbol scheduling. The main-task retry policy is
compiled and executed for both frame lengths, queue-generation supersession,
in-flight cancellation, toggle changes, failure, one-shot consumption, owned
payload copies, and millisecond-clock rollover. Source-contract tests guard
main-loop RX ordering and original HA telemetry isolation.
A lifecycle regression harness compiles the actual production
`configure_active_listener()` method with stubbed radio side effects, then
executes disable / queue / complete / resume for both FIFO/RMT and both frame
lengths. It also checks unchanged periodic refreshes, resume before completion,
explicit stops, and genuine profile changes before/after completion. This
reproduced the erroneous `listener_profile_changed` cancellation before the fix.
`make firmware-unit-test` includes these checks in the existing ESPHome CI job.
It enforces 100% host-side line coverage of `delayed_tx_retry.h`; this does not
claim full component, FreeRTOS scheduling, or hardware-driver coverage.

These are software checks, not proof of analog radio readiness or fireplace
acceptance. ESPHome source generation checks configuration/code generation;
the Builder compile and controlled hardware comparison are still required.

## Snapshots

Each snapshot has a sequence number, stage, request ID, uptime, and read duration.
All continuation lines carry the sequence number. Configuration addresses and
values are hexadecimal, in ascending order. The final config block ends at 0x2E.

The worker collects pre/post-TX values without logging and copies both records
through the existing completion queue. After restoring RX, the main task emits
the saved records on both successful and failed transmissions. This avoids
overflowing ESPHome's cross-task logging buffer, which previously dropped the
post-TX records and the tail of pre-TX records from API logs.

Sequence numbers and uptime identify capture order, not log delivery order.
For example, the restored-RX snapshot can appear before the earlier pre/post-TX
records. Deferred logging does not perform any additional register reads.

- `radio_initialized`: radio setup completed, before normal RX begins.
- `rmt_rx_running`: RX configuration applied and SRX issued.
- `rmt_rx_stopping`: before RX is stopped and its registers are replaced.
- `rmt_rx_stopped_tx_config`: after restoring the async TX configuration.
- `tx_prepared`: worker immediately before calling the existing TX function.
- `tx_complete`: worker after that function returns, on success or failure.

Snapshots contain all 47 configuration registers, all eight PA table entries,
and selected status registers:

| Address | Status |
| --- | --- |
| 30 | PARTNUM |
| 31 | VERSION |
| 32 | FREQEST |
| 33 | LQI |
| 34 | RSSI |
| 35 | MARCSTATE |
| 38 | PKTSTATUS |
| 39 | VCO_VC_DAC |
| 3A | TXBYTES |
| 3B | RXBYTES |

No FIFO reads, register writes, resets, calibration commands, or state-change
strobes are added. All values are collected before the snapshot is logged.
Status reads are sequential and may change while the radio is active; they are
not atomic. Configuration differences between RX and TX are expected. Compare
the same lifecycle stage, not RX from one run against TX from another.
RX-stage request IDs identify the most recently recorded request and do not
necessarily identify why RX was enabled or disabled.

## Collection

1. Start continuous ESPHome API logging before rebooting.
2. Reboot and retain the initialization and RX snapshots.
3. Send spaced light and fan changes through HA, recording the time, requested
   setting, and actual response when observation is available.
4. Preserve rejected commands as well as accepted ones. If the same command
   later works, record that explicitly.
5. Continue logging across a reboot and retry of the same failed command.
6. Save complete logs separately for alpha1 and beta1, along with the source
   reference and ESPHome build version. Pair with SDR capture when practical.

Do not infer fireplace acceptance from TX success telemetry, an SDR decoder,
or another LilyGO's mirrored state.

## Limits

Reads and logging add latency outside the waveform loop and change the binary
layout. They may mask a timing-sensitive defect. If diagnostic builds stop
reproducing the failure, that is an inconclusive result, not a fix.
Schema 2 also removes worker logging overhead before RF emission. Do not assume
its timing effects are identical to the original schema 1 diagnostic build.
Schema 3 preserves trace read placement when ON, and removes trace reads and
output when OFF. Its delay-0 branch does not add a new wait or poll, but the
new binary is not instruction-for-instruction identical to schema 2.
There is no snapshot during RF emission or between individual repeats.
Post-TX calibration registers need not equal their pre-TX values.

Host tests compile and run the actual radio readback/logger implementation
with a mock SPI bus. They verify address/read modes, PA entries, bounded lines,
sequence numbers, changed readbacks, no state-changing commands, and a disabled
trace with no bus access. They do not validate SPI electrical behavior or RF
acceptance. ESPHome source generation and host tests are not a firmware build.

## Local Validation

- Beta1 diagnostic branch before schema 2: 1,082 tests passed; one optional external-source
  check was deselected by the existing suite configuration.
- Alpha1 diagnostic branch: 551 tests passed with the rtl_433 source path
  configured. Two release-metadata tests were excluded after reproducing their
  failures on the untouched alpha1 tag (alpha prerelease flavor and stamped
  documentation links versus an expectation of development links).
- The initial three diagnostic checks passed on both branches, including host C++
  compilation with tracing enabled and disabled.
- ESPHome 2026.8.2 configuration validation and C++ generation succeed on both.
  Full ESP32 firmware compilation and hardware validation remain outstanding.

Schema 2 adds a fourth diagnostic check. Host tests verify that capture emits
no logs, queue-copy-compatible records retain pre/post-TX values after live
registers change, deferred logging performs no SPI accesses, and both records
have completion markers. Lifecycle assertions check capture surrounds TX and
output occurs after RX restoration on success and failure.

Schema 2 validation: 1,083 tests passed with one existing optional-source test
deselected; Ruff, Black, whitespace checks and ESPHome 2026.8.2 source generation
passed. Full ESP32 compilation and hardware/API-log delivery remain to be
verified on the Builder/device.

Schema 3 host tests also execute the actual `radio_cc1101_tx.cpp` implementation
with a deterministic simulated clock, GPIO, and SPI radio. They cover seven-
and ten-word payloads, both native repeat modes, delays of 0/0.1/1/20/100 ms,
returning to 0, and rejection of unsupported TX modes. They verify first-edge
settling and identical relative edge schedules/repeat gaps. These checks do
not simulate analog settling or establish RF/fireplace acceptance.

Schema 3 validation: 1,085 tests passed with one existing optional-source test
deselected. Six focused diagnostic checks include trace-disabled SPI suppression
and real-scheduler tests. Ruff, Black, whitespace checks and ESPHome 2026.8.2
source generation pass. The native TX host compiler reports pre-existing unused
diagnostic-variable warnings; these are outside this experimental change.
Full ESP32 compilation and hardware acceptance still require Builder/device tests.
