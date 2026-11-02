# Issue #15 Extended Protocol Beta Design

## Scope

This document specifies the beta implementation for the ten-word Proflame2
frames captured from Alex's SIT TMFSLA / T99058402300 remote. It is an internal
implementation record, not an end-user configuration guide.

The beta supports learning, active listening, and manual Home Assistant
control through the LilyGO RMT pulse path. Hardware acceptance remains pending
reporter validation.

## Frame And Integrity

The ten on-air words are `W1 W2 W3 W4 W5 W6 W7 W8 W9 W10`. `W1-W3` are the
remote identifier. In manual mode, `W4` uses the existing power/light bit
layout with bits 2, 3 and 7 retained from the learned manual frame, and `W5` uses the
existing flame/fan/front/aux bit layout. Bit 2 selects the temperature scale.
W6 is the requested setpoint; W7/W8 carry room temperature, rather than fixed
remote identity data.

### Temperature fields and units

[Issue #32's controlled Fahrenheit/Celsius captures](https://github.com/jeffgregx2/HACS-Proflame2/issues/32#issuecomment-6566490137)
establish the following mapping on the reporting extended remote. Bit numbers
are zero-based, with bit 0 the least significant bit.

| Field | Meaning |
| --- | --- |
| W4 bit 2 (`0x04`) | Temperature units: 1 = Fahrenheit, 0 = Celsius. |
| W4 bit 3 (`0x08`) | Meaning remains unknown. |
| W6 | Requested setpoint in whole degrees of the selected scale. |
| W7 | Low byte of room temperature in tenths of the selected scale. |
| W8 | High byte of room temperature in tenths of the selected scale. |

Decode room temperature as `((W8 << 8) | W7) / 10`. Both temperature fields
change scale when the units flag changes; conversion is not limited to the
remote's display. The controlled switch produced W4 `0x84 → 0x80 → 0x84`,
with W5 unchanged. Representative observed temperature fields were:

| Units | W6 | W7 | W8 | Setpoint | Room temperature |
| --- | --- | --- | --- | --- | --- |
| Fahrenheit | `0x4F` | `0x02` | `0x03` | 79°F | 77.0°F |
| Celsius | `0x1A` | `0xF5` | `0x00` | 26°C | 24.5°C |
| Fahrenheit | `0x4E` | `0xF8` | `0x02` | 78°F | 76.0°F |

These are successive observations, not exact temperature conversion pairs.
Each units switch broadcasts a frame; the reporter also observed the usual
single repeat approximately 15 seconds later. W4 bit 7 separately tracks CPI
in this reporter's controlled captures; the current implementation still
preserves it as a learned flag rather than exposing extended CPI control.

### Integrity words

```text
T(value) = build_err_byte(value, 0, 0)
W9  = T(T(T(T(W1) ^ W3) ^ W5) ^ W7)
W10 = T(T(T(T(W2) ^ W4) ^ W6) ^ W8)
```

Both integrity lanes must validate before a received extended frame is
accepted.

Issue #26 established that the earlier reduced-lane constants `0x23/0x65`
represented Alex's serial `08E905`, not universal protocol constants. The
serial-inclusive lanes above reproduce his captures unchanged and also validate
Willy's serial `338705`. See [Issue #26 regression evidence](issue_26_extended_integrity.md).

Some native extended frames have flame level zero while the power bit remains
set. Receive preserves both fields without guessing whether this represents
full shutdown or flame-only shutdown. Thermostat mode can also use flame zero.
HA transmit validation remains unchanged; accepting a native observation does
not enable new thermostat or flame-zero commands.

## Profile And Learning

Extended profiles persist `protocol_variant=extended_10_word` and a template:
`w4_base`, `w6`, `w7`, and `w8`. Legacy C/D fields are intentionally unused by
this variant. Existing seven-word profiles continue to load and transmit
unchanged.

Guided learning accepts validated ten-word RMT frames, selects a manual-mode
sample to obtain `w4_base`, `w6`, and `w8`, and stores `W7=0xC8` for
manual Home Assistant frames. The original learned raw frame, including its
extension words, remains available for initial-state restoration and diagnostics.

That describes the current implementation, not the meaning of the fields:
W8 is temperature data and may change during learning. W7 alone does not
specify a temperature. `W7=0xC8` means 20.0°C only when W8 is zero and W4 bit 2
is clear. The [Issue #32 learning fix design](issue_32_learning_mode_design.md)
removes units, setpoint, and room temperature from identity consistency and
proposes a canonical Celsius manual TX template for newly learned profiles:
W4 bit 2 clear, W6 `0x14`, W7 `0xC8`, W8 `0x00`. Existing profiles retain their
stored units and temperature fields unchanged. Received
checksums must always be validated using the original, unmodified bytes.

## Transmit Rules

The HA encoder emits ten words and a 260-bit Manchester payload. LilyGO TX
already accepts a payload with any bit length that fits its supplied byte
array; no ESPHome firmware change is required for this beta.

Only direct manual control is supported. Native thermostat modes and dynamic
temperature telemetry are not exposed by Home Assistant. `W7=0xC8` is a fixed
placeholder, not a measurement. Reporter hardware testing must establish that
the fireplace ignores temperature telemetry in manual mode.

For extended profiles, HA supplies no strict legacy C/D profile to the LilyGO
listener. Firmware therefore uses raw RMT capture and HA validates the
ten-word integrity lanes.

## Verification Boundary

Automated tests cover decoding, integrity rejection, guided-learning
persistence, HA control encoding, RMT listener selection, and ESPHome request
shape. They cannot verify radio timing or fireplace acceptance. Beta hardware
validation must first perform guided learning, then one low-risk manual command
at a time with the native remote available for recovery.
