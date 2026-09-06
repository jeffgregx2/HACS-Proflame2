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
layout with bit 7 retained from the learned manual frame, and `W5` uses the
existing flame/fan/front/aux bit layout. `W6` and `W8` are learned fixed fields.

```text
T(value) = build_err_byte(value, 0, 0)
W9  = T(T(W5)) ^ T(W7) ^ 0x23
W10 = T(T(T(W4))) ^ T(T(W6)) ^ T(W8) ^ 0x65
```

Both integrity lanes must validate before a received extended frame is
accepted.

## Profile And Learning

Extended profiles persist `protocol_variant=extended_10_word` and a template:
`w4_base`, `w6`, `w7`, and `w8`. Legacy C/D fields are intentionally unused by
this variant. Existing seven-word profiles continue to load and transmit
unchanged.

Guided learning accepts validated ten-word RMT frames, selects a manual-mode
sample to obtain `w4_base`, `w6`, and `w8`, and stores `W7=0xC8` (20.0 C) for
manual Home Assistant frames. The original learned raw frame, including its
extension words, remains available for initial-state restoration and diagnostics.

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
