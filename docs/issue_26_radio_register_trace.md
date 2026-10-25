# Alpha1/Beta1 Radio Register Comparison

## Purpose

Compare CC1101 hardware readbacks from two isolated diagnostic branches:

- `issue-26-alpha1-radio-trace`, based on `v0.7.0-alpha1`.
- `issue-26-beta1-radio-trace`, based on `v0.7.0-beta1`.

Both use identical instrumentation. Neither modifies the RF register values,
packet encoder, waveform loops, calibration policy, or TX/RX state commands.
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
but `RFTRACE diagnostic build base_version=... schema=1` confirms instrumentation.
New traces require rebuilding and flashing, not just changing the HA integration.

## Snapshots

Each snapshot has a sequence number, stage, request ID, uptime, and read duration.
All continuation lines carry the sequence number. Configuration addresses and
values are hexadecimal, in ascending order. The final config block ends at 0x2E.

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
There is no snapshot during RF emission or between individual repeats.
Post-TX calibration registers need not equal their pre-TX values.

Host tests compile and run the actual radio readback/logger implementation
with a mock SPI bus. They verify address/read modes, PA entries, bounded lines,
sequence numbers, changed readbacks, no state-changing commands, and a disabled
trace with no bus access. They do not validate SPI electrical behavior or RF
acceptance. ESPHome source generation and host tests are not a firmware build.

## Local Validation

- Beta1 diagnostic branch: 1,082 tests passed; one optional external-source
  check was deselected by the existing suite configuration.
- Alpha1 diagnostic branch: 551 tests passed with the rtl_433 source path
  configured. Two release-metadata tests were excluded after reproducing their
  failures on the untouched alpha1 tag (alpha prerelease flavor and stamped
  documentation links versus an expectation of development links).
- The three new diagnostic checks pass on both branches, including host C++
  compilation with tracing enabled and disabled.
- ESPHome 2026.8.2 configuration validation and C++ generation succeed on both.
  Full ESP32 firmware compilation and hardware validation remain outstanding.
