# LilyGO CC1101 Proflame2 RF Reference

This document describes the validated Proflame2 transmit path and the validated
CC1101 FIFO receive path implemented by the LilyGO T-Embed CC1101 endpoint in
HACS-Proflame2.

The intent is to let a future controller implementation on different hardware
reproduce a compliant waveform without rediscovering the protocol details.

## Scope

This is the reference for the working transmitter path:

- `tx_mode: proflame_native_groups`
- `native_group_timing_profile: native_remote`
- `native_group_repeat_boundary_mode: continuous_tx`
- `repeat_count: 5`
- `payload_bit_length_override: 182`
- `inter_frame_gap_us: 0`
- `post_frame_idle_gap_us: 0`

The protocol authority remains in Home Assistant. The ESPHome endpoint is only
responsible for:

- accepting the authoritative prepared air payload
- deriving the native-group transmit schedule
- driving the RF hardware so the over-the-air waveform is compliant

The endpoint must not own:

- ECC derivation
- command generation
- profile semantics
- thermostat policy
- payload regeneration
- Proflame2 decode authority

## Implementation Boundary

The firmware receives a prepared payload from Home Assistant:

- `air_payload_hex`
- `payload_bit_length`
- `repeat_count`

For the validated native-group path, Home Assistant provides 25 payload bytes
and the firmware transmits the first 182 bits of that payload.

Example validated ON fixture:

- `serial_id=3b3f02`
- `cmd1=0x01`
- `err1=0x76`
- `cmd2=0x06`
- `err2=0xDE`
- `air_payload_hex=e5a9a9b96aa96e55596b95559ae55695b9a9a5aea6a9580000`
- `payload_bit_length=182`

Example validated OFF fixture:

- `serial_id=3b3f02`
- `cmd1=0x00`
- `err1=0x57`
- `cmd2=0x06`
- `err2=0xDE`
- `air_payload_hex=e5a9a9b96aa96e55596b955556e55695b999a9aea6a9580000`
- `payload_bit_length=182`

The transmitter must send the provided payload as-is. It must not attempt to
recompute ECC bytes or mutate the payload.

## Native-Group Symbol Decoding

The native-group TX path starts by decoding the payload bitstream into
two-bit symbols.

Symbol mapping:

- `11` -> `S` (`SYNC`)
- `01` -> `0`
- `10` -> `1`
- `00` -> `Z` (`TRAILER`)

For the validated Proflame2 native-group packet:

- 7 groups are expected
- each group consumes 13 symbols
- total word symbols per repeat = `7 * 13 = 91`
- any remaining symbols must be trailer symbols (`00`)

Each 13-symbol group is interpreted as:

1. sync symbol
2. start bit
3. 9 data bits
4. parity bit
5. end bit

So the logical group shape is:

- `S start data9 parity end`

The firmware requires:

- symbol 0 in every group is `S`
- `start`, `parity`, and `end` are each either `0` or `1`
- all 9 data symbols are each either `0` or `1`
- all symbols after the 7 groups are `Z`

## Native-Group Emit-Bit Derivation

The analyzer-visible native-group bits are not sent directly by Home
Assistant. They are derived from the 13-symbol group.

Derivation algorithm:

1. Convert the 13-symbol group back into its 26 air bits using:
   - `S -> 11`
   - `0 -> 01`
   - `1 -> 10`
2. Run-length encode the 26 air bits.
3. The first run must be `111`. That is the sync high run.
4. Ignore zero runs.
5. For every subsequent high run:
   - run length `1` emits analyzer bit `0`
   - run length `2` emits analyzer bit `1`
   - any other high-run length is invalid

This yields the group-specific emitted bits used for the pulse schedule.

Example validated ON packet emitted analyzer groups:

- `{8}b6`
- `{9}bf0`
- `{9}fa8`
- `{9}fc8`
- `{9}f70`
- `{8}6d`
- `{9}df0`

Those groups repeat 5 times in a compliant packet.

## Pulse Schedule Per Repeat

Each repeat is built from:

- one sync pulse before every group
- one pulse for every emitted analyzer bit in that group

For the validated ON and OFF fixtures:

- pulses per repeat = 68
- groups per repeat = 7
- repeats per transmission = 5
- total pulses over the full burst = 340

The schedule is level-driven:

1. drive the async data line high
2. wait the symbol high duration
3. drive the async data line low
4. wait the symbol low duration
5. continue with the next symbol

The final low after the last symbol remains low until the next repeat starts or
the radio is idled at the end of the burst.

## Native-Remote RF-Visible Timing Contract

The validated native-remote target is the RF-visible waveform, not just nominal
scheduled delays.

Target pulse families:

- sync high: about `1224 us`
- short high: about `408 us`
- long high: about `820 us`
- short low: about `424 us`
- long low: about `832 us`
- repeat gap: about `5240 us`

Validated external results for the LilyGO path are close to:

- `Total count=340`
- `width≈396 ms`
- `sync≈1228 us`
- `short≈408-412 us`
- `long≈820-824 us`
- `short_gap≈420-424 us`
- `long_gap≈832-836 us`
- `reset_gap≈5232-5244 us`

## Important: Analyzer Codes Are Not Sufficient

This was the main discovery during bring-up.

It is possible to generate analyzer-clean PWM groups while still failing
`rtl_433` decoder 207. The Proflame2 decoder in `rtl_433` uses an
`OOK_PULSE_PCM` slicer path, so the RF envelope must also expand into the
correct PCM row.

A controller implementation is not compliant just because it produces:

- correct pulse count
- correct short/long/sync families
- correct analyzer groups

It must also produce the same decoder 207 PCM row shape as the native remote.

For the validated ON fixture, the expected decoder 207 row is:

- `{183}e5a9a9b96aa96e55596b95559ae55695b9a9a5aea6a958`

For the validated OFF fixture, the expected decoder 207 row is:

- `{183}e5a9a9b96aa96e55596b955556e55695b999a9aea6a958`

## Position-Aware Low Selection for Native Remote

The final important TX rule is that native-remote low duration is not a fixed
function of symbol type alone.

The working implementation derives each native-remote symbol low duration by
walking the expected decoder 207 PCM row in parallel with the emitted symbol
stream.

Fixed high-bit expansions:

- `S` contributes `111`
- `0` contributes `1`
- `1` contributes `11`

Selectable low-bit expansions:

- short low contributes `0`
- long low contributes `00`

Candidate contributions per symbol:

- `S`: `11100` first, then `1110`
- `0`: `10` first, then `100`
- `1`: `110` first, then `1100`

Selection rule:

1. Compute the expected PCM row bit count as `payload_bit_length + 1`, capped
   by the available payload bits.
2. For each symbol in the scheduled stream:
   - verify that the next high-run bits in the expected PCM row match the
     symbol's fixed high contribution
   - try the short-low and long-low candidates allowed for that symbol
   - choose the first candidate that:
     - matches the expected zero run at the current PCM cursor
     - leaves the next bit as `1` if another symbol follows
     - ends exactly at the expected row length if this is the final symbol
3. If neither candidate matches, log a diagnostic and fall back to the default
   scheduled timing for that symbol

This rule is scoped only to:

- `tx_mode: proflame_native_groups`
- `native_group_timing_profile: native_remote`

It is not used for YardStick-compatible timing or other TX modes.

## Current Native-Remote Symbol Timing Inputs

The current native-remote profile uses these desired timing families:

- `SYNC`
  - high `1224 us`
  - base long low `832 us`
- `ZERO`
  - high `408 us`
  - base short low `424 us`
- `ONE`
  - high `820 us`
  - base short low `424 us`

Current scheduled high-side compensation:

- short high compensation `12 us`
- long high compensation `12 us`
- sync high compensation `8 us`

Current scheduled low-side biases:

- short low bias `20 us`
- long low bias `20 us`

These values exist only to make the RF-visible result land on the desired
timing families for the ESP32/CC1101 implementation. A new hardware platform
may need different scheduled microseconds while preserving the same RF-visible
contract and PCM row.

## Repeat-Boundary Rules

The validated repeat-boundary mode is:

- `native_group_repeat_boundary_mode: continuous_tx`

That means:

- enter TX once before the burst
- keep the CC1101 in TX across all 5 repeats
- hold the async data pin low during the repeat gap
- return the radio to idle once after the full burst

The repeat-gap contract is:

- measure from the previous repeat's final RF-visible falling edge
- to the next repeat's first RF-visible rising edge

For compliant native-remote TX:

- desired repeat gap `5240 us`
- scheduled repeat gap `5240 us`
- re-entry strobes between repeats are not used

This was necessary to avoid per-repeat setup overhead pushing the RF-visible
gap away from the native target.

## CC1101 Configuration Required for Working TX

The currently validated CC1101 async OOK configuration is:

- frequency `314973000 Hz`
- data rate `2400 bps`
- async serial OOK TX
- MCU drives the async data input pin
- `PATABLE[0] = 0x00`
- `PATABLE[1..7] = 0xC6`

Important register values:

- `IOCFG2 = 0x2E`
- `IOCFG1 = 0x2E`
- `IOCFG0 = 0x2E`
- `PKTCTRL1 = 0x04`
- `PKTCTRL0 = 0x32`
- `MDMCFG2 = 0x30`
- `DEVIATN = 0x00`
- `FREND0 = 0x11`

Why the PA table matters:

- logic `0` in OOK must map to carrier off
- logic `1` in OOK must map to carrier on

With the validated configuration:

- PATABLE index 0 is off
- PATABLE index 1 is on
- `FREND0.PA_POWER = 1`

If all PATABLE entries are nonzero, the result is continuous carrier rather
than gated OOK.

## Async Data Path Requirements

The working firmware drives:

- ESP32 GPIO connected to CC1101 GDO0

The firmware keeps the CC1101 GDO pin configuration in high-impedance mode and
externally drives the pin from the MCU. The validated path is:

- async data input assumed on `GDO0`
- MCU drives that pin directly
- no packet-engine framing
- no FIFO payload framing

For the LilyGO T-Embed CC1101 board, the reference wiring is:

- `CC1101 GDO0 -> GPIO3`
- `CC1101 GDO2 -> GPIO38`
- `CC1101 CS -> GPIO12`
- `RF switch SW1 -> GPIO47` set high for 315 MHz path
- `RF switch SW0 -> GPIO48` set low for 315 MHz path

A different hardware implementation does not need these exact GPIO numbers, but
it does need an async OOK data path with equivalent externally visible
behavior.

## Compliance Checks

A new controller implementation should be validated in this order.

1. Payload and emitted groups

- the transmitted payload bytes must match the HA-generated bytes
- the native-group emitted analyzer bits must match the derived groups

2. Analyzer-visible burst

- `rtl_433 -A -R 207 -f 315M -g 40 -Y autolevel`
- one packet should contain all 5 repeats
- `Total count` should be `340`
- analyzer groups should repeat 5 times

3. PCM slicer / decoder 207 compatibility

- the decoder 207 row must match the native row shape
- ON row should start with `e5a9a9`
- row length should be `183`
- the packet should decode as `Proflame2-Remote`

4. Physical acceptance

- the fireplace must accept ON and OFF commands reliably

Analyzer-only correctness is not enough. Decoder 207 and the physical receiver
must both agree.

## Minimal YAML Configuration for the Validated TX Path

These are the settings that matter for the compliant path:

```yaml
proflame2_tembed:
  tx_frequency_hz: 314973000
  data_rate_bps: 2400
  tx_repeat_count: 5
  tx_mode: proflame_native_groups
  native_group_timing_profile: native_remote
  native_group_repeat_boundary_mode: continuous_tx
  payload_bit_length_override: 182
  inter_frame_gap_us: 0
  post_frame_idle_gap_us: 0
  pre_burst_low_us: 0
  pre_frame_low_us: 0
  async_tx_data_pin: gdo0
```

## Guidance for a New Hardware Implementation

If a future controller uses different hardware, preserve these invariants:

1. Home Assistant remains the payload authority.
2. Use the native-group serializer, not a simplified pulse approximation.
3. Preserve the 7-group repeat structure and 5-repeat burst.
4. Preserve the RF-visible repeat gap contract.
5. Preserve the decoder 207 PCM row shape, not just analyzer codes.
6. Treat scheduled microseconds as implementation-specific and RF-visible
   timing as authoritative.

If the new hardware already has a better way to synthesize the decoder 207 PCM
row directly, that is acceptable as long as the on-air result is equivalent.

## RX

The validated LilyGO RX path is a CC1101 RX FIFO byte-window capture followed
by host-side Proflame2 candidate scanning. It intentionally mirrors the
YardStick/rfcat receive abstraction: use the CC1101 as an OOK slicer that
produces bytes, then scan those bytes in software for a packet-owned Proflame2
candidate. Do not use GDO edge-interval ownership as the semantic RX path.

The LilyGO edge packet-ownership path was deprecated after Stage 5AL. Edge
diagnostics may remain in firmware or historical tools for low-level RF
troubleshooting, but they are not part of the active RX architecture and should
not be used as a transform prerequisite.

### RX Architecture

The firmware performs bounded Proflame2 candidate decode only for FIFO learning
and active-listening filtering. Home Assistant still owns learning persistence,
command generation, profile policy, and fireplace state authority.

The firmware RX job is:

1. Configure the CC1101 in an rfcat-like ASK/OOK RX FIFO mode.
2. Continuously drain CC1101 `RXFIFO` bytes into a bounded rolling buffer.
3. On learning, active-listening scan, or diagnostic completion, select the
   trailing FIFO byte window.
4. Include radio settings, status, timestamps, and overflow metadata.
5. Decode enough Proflame2 candidate structure to suppress noise, stale
   candidates, and packets for other serial/profile values.
6. Publish only accepted learned-profile matches to Home Assistant during active
   listening.
7. Preserve raw FIFO diagnostics for troubleshooting without treating them as
   semantic packet evidence.

The packet-owned artifact is `semantic_fifo_artifact.json` with:

- `artifact_class=semantic_fifo_candidate`
- `semantic_comparable=true`
- `decode_success=true`
- `packet_normalized=true`
- decoded `remote_id`, `cmd1`, `cmd2`, `err1`, `err2`
- selected candidate offsets, confidence, repeat count, symbols, and raw slice

The raw FIFO export remains diagnostic. The semantic artifact is the host-side
selected candidate inside that FIFO export.

### CC1101 RX Configuration

The default validated profile is:

```text
rx_frequency_hz: 314973000
data_rate_bps: 2400
RX FIFO Profile: rfcat_fixed_none_rfcat_wide
Enable Capture: fifo_trailing_window
Export window: 6000 ms
Active listener scan interval: 1500 ms
```

The profile configures the CC1101 as an ASK/OOK byte-window source, not as a
Proflame2 packet decoder. For the default 314.973 MHz / 2400 bps profile, the
important register values are:

```text
IOCFG2   = 0x2E
IOCFG1   = 0x2E
IOCFG0   = 0x2E
FIFOTHR  = 0x47
SYNC1    = 0x00
SYNC0    = 0x00
PKTLEN   = 0xFF
PKTCTRL1 = 0x00
PKTCTRL0 = 0x00
FSCTRL1  = 0x06
FSCTRL0  = 0x00
FREQ2    = 0x0C
FREQ1    = 0x1D
FREQ0    = 0x45
MDMCFG4  = 0x56
MDMCFG3  = 0x83
MDMCFG2  = 0x30
MDMCFG1  = 0x02
MDMCFG0  = 0xF8
DEVIATN  = 0x00
MCSM1    = 0x3F
MCSM0    = 0x18
FOCCFG   = 0x17
BSCFG    = 0x6C
AGCCTRL2 = 0x03
AGCCTRL1 = 0x40
AGCCTRL0 = 0x91
FREND1   = 0xB6
FREND0   = 0x10
FSCAL3   = 0xE9
FSCAL2   = 0x2A
FSCAL1   = 0x00
FSCAL0   = 0x1F
TEST2    = 0x88
TEST1    = 0x31
TEST0    = 0x09
```

The frequency word is computed as:

```text
FREQ = floor(frequency_hz * 2^16 / 26_000_000)
```

For 314,973,000 Hz, that is `0x0C1D45`.

The data-rate registers are computed by minimizing CC1101 data-rate error over
`DRATE_E` and `DRATE_M`:

```text
data_rate = (256 + DRATE_M) * 2^DRATE_E * 26_000_000 / 2^28
```

For 2400 bps, the best base value is `DRATE_E=6`, `DRATE_M=0x83`, producing
about 2398 bps. The wide receive profile keeps that exponent/mantissa but uses
`MDMCFG4=0x56` to widen channel bandwidth.

Operationally:

- Sync is disabled.
- Hardware Manchester decode is disabled.
- Whitening and CRC handling are disabled.
- The CC1101 packet engine is used only to surface sliced FIFO bytes.
- Software scans all relevant bit offsets; byte 0 is not assumed to be packet
  aligned.

### Rolling FIFO Capture Flow

The user-facing control is the `Enable Capture` select. Set it to
`fifo_trailing_window` for Proflame2 RX.

When `fifo_trailing_window` is selected:

1. Firmware initializes the radio if needed.
2. Firmware applies the selected FIFO profile.
3. Firmware strobes `SIDLE`, `SFRX`, `SFTX`, writes the RX registers, calibrates
   with `SCAL`, flushes RX FIFO, then enters `SRX`.
4. The main loop polls `RXBYTES`.
5. If bytes are available, firmware drains them from `RXFIFO` into a 4096-byte
   rolling buffer with a millisecond tick per drained byte.
6. If the hardware RX FIFO overflow bit is set, firmware records
   `rx_fifo_overflow`, flushes RX FIFO, and re-enters RX.

The sample boundary is explicit. For each native remote press:

1. Leave `Enable Capture` set to `fifo_trailing_window`.
2. Press the native remote button.
3. After the RF burst ends, press `RX FIFO Capture Complete`.
4. Firmware exports all FIFO bytes whose drain timestamp falls inside the last
   6000 ms ending at the complete button press.
5. Firmware resets the rolling FIFO state, reapplies the RX profile, and resumes
   rolling capture without requiring `Enable Capture` to be toggled.

Old rolling history before the requested trailing window may be dropped. That
is not a semantic failure. A capture is invalid only if the exported trailing
window is incomplete, corrupted, or affected by RX FIFO overflow.

### Syslog Export Format

Firmware exports short grouped syslog lines. Long single-line metadata is not
used because syslog truncation was observed during validation.

The final rolling FIFO export uses schema 2 lines:

```text
RX fifo probe begin schema=2 ... capture_mode=rolling_fifo_trailing_window profile=...
RX fifo probe chunk schema=2 ... offset=... count=... hex=...
RX fifo probe meta window schema=2 ... export_window_ms=... wall_clock_window_coverage_ms=...
RX fifo probe meta status schema=2 ... byte_count=... rx_fifo_overflow=... trailing_window_complete=...
RX fifo probe meta timing schema=2 ... post_last_byte_quiet_ms=...
RX fifo probe meta radio_status schema=2 ... marcstate_after_rx=... rssi_raw=... lqi_raw=...
RX fifo probe meta radio_regs1 schema=2 ...
RX fifo probe meta radio_regs2 schema=2 ...
RX fifo probe end schema=2 ... byte_count=...
```

The Python syslog collector reconstructs these lines into:

```text
lilygo/fifo_probe.json
lilygo/fifo_probe_payload.hex
lilygo/fifo_probe_bit_stream.txt
lilygo/semantic_fifo_artifact.json
```

`fifo_probe_payload.hex` is the raw CC1101 FIFO byte window. The semantic
artifact exists only when host-side scanning finds a valid Proflame2 candidate.

### Host-Side Candidate Selection

The host scanner treats the LilyGO FIFO bytes exactly like a YardStick receive
payload:

1. Expand FIFO bytes to bits.
2. Scan candidate bit offsets and Proflame-sized windows.
3. Validate Proflame2 start/end structure, Manchester-compatible symbols,
   repeat structure, ECC/check fields, and decoded command fields.
4. Select the best candidate by the same candidate model used for YardStick
   learning diagnostics.
5. Persist a semantic FIFO artifact only for a successful candidate.

During live validation, `rtl_433` remains the canonical external semantic
witness. A validation sample is rejected if LilyGO decoded
`remote_id/cmd1/cmd2/err1/err2` does not match the `rtl_433` decode for that
same native remote press. This guard prevents stale or previous-packet FIFO
candidates from becoming valid samples.

### Validated Stability

The clean FIFO semantic replicate session validated the final RX abstraction:

```text
analysis/live/stage5aj_fifo_semantic_replicates_clean2/20260514T111059Z
```

It produced 6 valid samples in 2 exact semantic groups. Formal replicate
analysis showed:

- LilyGO FIFO semantic symbol similarity >= 0.97 in both groups.
- YardStick semantic symbol similarity >= 0.97 in both groups.
- YardStick semantic bit similarity >= 0.98 in both groups.
- decoded-field mismatches: 0.
- artifact warnings: 0.

This is the first LilyGO RX artifact that should be treated as packet-owned
semantic data.

### Reimplementation Checklist

A CC1101 implementation that wants to reproduce this RX path should:

1. Use a CC1101-class radio as an ASK/OOK slicer and FIFO byte source.
2. Configure approximately 315 MHz receive at 2400 bps with sync, whitening,
   CRC, and hardware Manchester disabled.
3. Use the FIFO profile above as the starting register set.
4. Drain `RXFIFO` continuously and quickly enough to avoid hardware FIFO
   overflow.
5. Keep a bounded rolling buffer of recent FIFO bytes and byte timestamps.
6. Export a trailing byte window after an explicit operator or orchestration
   completion point.
7. Preserve raw bytes and radio diagnostics even if decode fails.
8. Run Proflame2 candidate scanning in host software over all relevant bit
   offsets; do not assume byte alignment.
9. Promote only successful candidates to semantic packet events/artifacts.
10. In active listening, suppress repeats from the same transmission and publish
    only packets matching the learned serial/C-D profile.
11. Cross-check decoded fields against an independent semantic witness such as
    `rtl_433` during validation.
