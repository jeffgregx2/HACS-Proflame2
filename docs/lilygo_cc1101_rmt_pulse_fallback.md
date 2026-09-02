# LilyGO CC1101 RMT Pulse Receive Path

## Purpose

This document specifies the normal LilyGO receive path for native Proflame2
remotes. It follows the acquisition architecture used by Bruce firmware: configure the
CC1101 for ASK/OOK asynchronous serial output, route GDO0 to an ESP32 RMT RX
channel, quantize bounded high/low runs, and reconstruct the OOK PCM row in
software.

It is the default for guided learning and active listening. The previous FIFO
byte-window path is used only as a fallback when RMT has compatibility problems.
Neither path changes Home Assistant ownership of learned profiles or fireplace
state.

## Implementation Status

The implemented receive path is:

- `custom_components/proflame2/rf/pulse.py` validates a 417 us PCM row and
  recognizes the standard seven-word and explicit ten-word layouts.
- The base package exposes persistent `Active Listener RX Path` selection.
  It defaults to `rmt_pulse`; selecting `fifo` restores FIFO acquisition for
  subsequent guided-learning and active-listening sessions. The debug package
  retains `Enable Capture: rmt_pulse` for direct capture diagnostics. Firmware
  exports each RMT window as a `pulse_capture` event with packed PCM and
  `pcm_bit_length`.
- The initial persistent selection is controlled by the YAML substitution
  `proflame2_active_listener_rx_path_default`; set it to `fifo` in an overlay
  to make FIFO the first-boot rollback default.
- Home Assistant decodes those pulse events using the same learning candidate
  path as FIFO captures. Extension bytes are retained in diagnostic notes only.

The selected path is persistent. Changing the YAML substitution does not alter
an already-saved selection; use `Active Listener RX Path` in Home Assistant or
erase device preferences. There is no automatic FIFO-to-RMT fallback.

## Evidence And Scope

GitHub issue #15 supplied two captures of the same SIT TMFSLA Power press:

- The LilyGO FIFO export contains a repeated 34-byte block.
- The Bruce RAW capture has base pulse families near 413, 823, and 1218 us,
  plus inter-repeat low gaps near 5.4 ms.
- One Bruce repeat takes about 113 ms including its repeat boundary. A 34-byte
  block at 2400 bps takes about 113.3 ms.
- Quantizing the Bruce capture at 417 us reconstructs seven standard
  Proflame2 words with serial `08E905`, `Cmd1=81`, `Cmd2=06`, `Err1=15`, and
  `Err2=EB`. They are followed by three additional valid words: `00 EC 77`.

This demonstrates that the remote is received and protocol-decodable, but the
current FIFO scanner is not given a suitable PCM representation and also
requires a zero trailer immediately after seven words.

## Bruce Reference

The reference was inspected from a local shallow clone of:

```text
https://github.com/BruceDevices/firmware.git
commit ba519c936c87b89c9c667c15b932d7faca5360d5
```

Relevant source files at that commit:

| Bruce source | Role to reproduce independently |
| --- | --- |
| `src/modules/rf/rf_utils.cpp` | Configures CC1101 ASK/OOK with asynchronous serial format, sets GDO0 input, and creates a 1 MHz RMT RX channel on GDO0. |
| `src/modules/rf/protocols/rf_decoder.cpp` | Arms RMT with a 3 us minimum duration and 30 ms idle completion threshold; converts RMT symbols to signed microsecond durations. |
| `src/modules/rf/rf_scan.cpp` | Treats RAW capture as signed edge durations and writes them to `RAW_Data`; it does not provide a Proflame2 decoder. |

Bruce's board sources contain more than one T-Embed pin mapping. This project
must use its validated T-Embed wiring, where CC1101 GDO0 is GPIO3, rather than
copying a Bruce board mapping. The issue hardware confirms Bruce can receive
the target remote; it does not make every Bruce board default authoritative.

Bruce is AGPL-3.0-or-later. Do not copy Bruce code into this project. Implement
the behavior below from ESP-IDF, ESPHome, CC1101, and Proflame2 protocol
documentation; keep this document's source references as provenance.

## Why FIFO Is Insufficient Here

The production FIFO receiver records bytes at a fixed data rate, then scans
their bit offsets as if they were an already-aligned Manchester symbol stream.
For the issue #15 remote, the useful signal is a run-length OOK PCM sequence:

```text
CC1101 asynchronous GDO0 -> high/low durations -> 417 us PCM bits -> words
```

The FIFO export still proves RF reception, because its repeated block has the
same timing budget as the Bruce repeat. It does not preserve explicit edge
durations or packet boundaries. A FIFO decoder therefore cannot reliably
distinguish the 1x, 2x, and 3x pulse runs required by this remote.

## Required Architecture

```text
guided learning / active listening
             |
             v
configure CC1101 async OOK receive on GDO0
             |
             v
ESP32-S3 RMT RX captures signed edge durations
             |
             v
bounded pulse package and 417 us PCM reconstruction
             |
             v
validate 7-word or 10-word Proflame2 frame
             |
             v
existing learned-profile / dedup / HA event policy
```

Only one radio receive mode may be active at a time. Before enabling RMT RX,
the component must stop FIFO capture, flush the CC1101 RX FIFO, put the radio
in IDLE, configure asynchronous receive, then enter RX. Before TX, it must
stop and delete the RMT channel, restore the normal TX configuration, and
restore the requested receive mode after transmission.

### CC1101 And RMT Requirements

The implementation must configure a dedicated asynchronous receive profile:

- ASK/OOK modulation at `314973000` Hz.
- CC1101 asynchronous serial mode with demodulated data output on GDO0.
- GPIO3 as an input to a 1 MHz ESP32-S3 RMT RX channel.
- No input inversion initially; make polarity a measured diagnostic, not a
  hidden heuristic.
- A 3 us RMT minimum duration, matching the Bruce capture behavior. Reject
  noise after capture using Proflame-specific thresholds instead of increasing
  the RMT hardware minimum.
- An idle completion threshold of at least 30 ms so the approximately 5.4 ms
  repeat gaps stay in one package.
- A fixed, bounded RMT buffer and a maximum capture duration. Overflow, too
  few transitions, or timing out without a complete package are diagnostic
  failures, never accepted packets.

Use `esp-idf` RMT APIs already available to ESPHome builds. The implementation
must own channel create/enable/receive/disable/delete lifecycle explicitly and
must not allocate, log, publish, or invoke Home Assistant APIs from an RMT
callback.

## Pulse And PCM Decoder

### Capture Artifact

Firmware emits a packed PCM row rather than raw durations. The event includes
frequency, capture mode, decimal PCM bit length, RMT symbol count, and
transition count. It is acquisition input, not semantic fireplace state.

### Validation And Quantization

For this profile, establish a nominal unit of 417 us from the configured 2400
bps rate. A valid package must:

1. Contain enough alternating edges for one full Proflame frame.
2. Have durations classifiable near one, two, or three unit widths, subject to
   explicit tolerances derived from the Bruce evidence and native captures.
3. End at either the normal seven-word zero trailer or the supported one-bit
   truncation of the final Manchester end guard. The latter is a known RMT
   capture boundary artifact and is accepted only for the final word.
4. For the ten-word extended layout, contain a repeat low gap of at least ten
   PCM bits after the extension words.

Do not infer a frame only from total duration. Scan every bounded bit phase
until a validated word sequence is found.

### Frame Formats

Use the existing 13-symbol / 26-encoded-bit word validation for every word:

- sync/start/end guards,
- binary data symbols only,
- per-word parity,
- serial trailing bits `1/0/0`, and
- command/error trailing zero bits.

Recognize two explicit layouts:

| Format | Layout | Acceptance |
| --- | --- | --- |
| Standard | seven validated words followed by the zero trailer, or the final one-bit RMT end-guard truncation | Publish the seven standard fields. |
| Extended | seven validated words, then exactly three additional validated words and a repeat low gap | Publish the standard seven-word fields and retain the extension bytes only in diagnostics. |

Do not make the trailer generally optional. The only accepted truncation is the
first bit of the final `1` Manchester end guard in the seventh word. A frame is
extended only when all ten words, their parity, and the repeat gap validate.

The extension's protocol meaning is not established. The implementation must
not map `00 EC 77` or any other extension values into fireplace state or ECC
until independently validated across buttons and remotes.

## Integration Boundaries

The implemented separation is:

| Helper | Responsibility |
| --- | --- |
| `rmt_ook_receiver.*` | CC1101 async-RX transition lifecycle and bounded RMT capture. |
| `rf/pulse.py` | Bounded PCM validation and seven/ten-word frame scanning. |
| `Proflame2TEmbedComponent` | Receive-path selection, radio arbitration, telemetry, and event publication. |

`Proflame2TEmbedComponent` remains responsible for radio-state arbitration,
TX priority, active-listening policy, telemetry, and Home Assistant events.
Home Assistant continues to own learning persistence and fireplace state.

Pulse rows and rejected candidates remain acquisition artifacts. The event
schema is deliberately distinct from `fifo_capture`:

```text
event_kind=pulse_capture
artifact_class=raw_ook_pulse_window
capture_mode=cc1101_gdo0_rmt_pcm
pcm_bit_length=<decimal>
symbol_count=<decimal>
transition_count=<decimal>
```

## Activation Policy

`rmt_pulse` is the production default and applies to both guided learning and
active listening. FIFO remains available only as an explicit persistent
rollback via `Active Listener RX Path: fifo` or the first-boot YAML
substitution. The debug-only `Enable Capture` control is for raw diagnostics;
it does not select the normal learning/listening path.

## Validation Plan

### Offline Fixtures

Create a checked-in, consented fixture from issue #15's Power capture with:

- packed 181-bit RMT PCM Power On and Power Off rows,
- expected first seven fields, and
- malformed/noise/incorrect-polarity cases.

Unit tests must prove:

- the captured 181-bit standard rows decode despite the terminal end-guard
  truncation;
- the standard and extended layouts validate their required guards;
- extension bytes never alter semantic state;
- wrong polarity, missing extended repeat gap, parity errors, and invalid RMT
  segments reject;
- decoder work and storage remain bounded.

### Hardware Validation

On the issue #15 T-Embed and at least one existing working remote:

1. Record pulse diagnostics for Power, Flame, Fan, and Light.
2. Verify stable serial and extension behavior across at least five presses of
   each button.
3. Confirm guided learning derives the same profile from repeated captures.
4. Confirm active listening ignores unrelated OOK traffic and deduplicates one
   remote burst to one event.
5. Confirm TX, TX-over-RX priority, and receive restoration still work.
6. Compare RMT timings against Bruce: base unit, pulse families, repeat gap,
   repeat count, and decoded first seven fields.

These checks are required whenever changing RMT timing, CC1101 async-RX
registers, or TX/RX arbitration.

## Implementation Order

1. Preserve the captured-frame tests and the 181-bit terminal end-guard rule.
2. Validate firmware builds in ESPHome Builder and exercise learning, active
   listening, display updates, TX, and post-TX RMT restoration on hardware.
3. Use FIFO only as a controlled rollback comparison when diagnosing a remote.
