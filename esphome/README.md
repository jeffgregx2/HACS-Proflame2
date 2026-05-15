# Proflame2 ESPHome Source Tree

This directory contains the ESPHome source for the LilyGO T-Embed CC1101
backend. It is separate from the Home Assistant integration under
`custom_components/proflame2`.

The LilyGO endpoint is a radio/display endpoint. Home Assistant remains the
owner of learning workflow, profile storage, command generation, fireplace
state policy, scenes, profiles, and user-facing control behavior.

## Production Model

The current LilyGO firmware supports:

- Proflame2 transmit from Home Assistant-generated payloads.
- Guided FIFO learning through the Home Assistant learning flow.
- Active listening for packets matching the learned serial/profile.
- Display and diagnostic entities for normal operation.

The old LilyGO GDO edge-interval ownership path is not part of the production
RX model. RX uses CC1101 FIFO byte windows and Proflame2 candidate scanning.

## Source-Only Distribution

This project distributes ESPHome source and configuration only. It does not
distribute prebuilt firmware binaries.

Users should build and deploy locally with ESPHome Builder in Home Assistant or
the ESPHome CLI. For reproducible installs, pin the GitHub `ref` to a release
tag instead of tracking a moving branch. For `github://` package references,
use the package suffix after `@` for the same release pin.

CI may compile ESPHome examples to validate source compatibility, but compiled
firmware outputs must be discarded. Firmware binaries must not be uploaded as
CI artifacts or attached to GitHub releases.

## User Entry Point

Use the production example as an overlay on top of an ESPHome-generated device
YAML:

- `examples/lilygo_cc1101_example.yaml`

Do not start by copying the example over a blank YAML. Create the ESPHome
device first so ESPHome generates local Wi-Fi, API encryption, and OTA secrets.
Then apply the Proflame2-specific board/package edits from the example.

High-level user instructions live in:

- `../docs/lilygo_cc1101_controller.md`
- `../docs/esphome_firmware_build.md`

## Package Files

Normal firmware uses:

- `packages/proflame2_tembed_base.yaml`
- `packages/proflame2_tembed_display.yaml`

Debug firmware may additionally include:

- `packages/proflame2_tembed_debug.yaml`

The debug package exposes manual FIFO capture/profile controls and other
low-level RF diagnostics. It is intentionally omitted from the normal example.

## Release-Pinned Package Example

For normal users after releases are tagged:

```yaml
packages:
  proflame2_tembed_base: github://jeffgregx2/HACS-Proflame2/esphome/packages/proflame2_tembed_base.yaml@<release-or-branch>
  proflame2_tembed_display: github://jeffgregx2/HACS-Proflame2/esphome/packages/proflame2_tembed_display.yaml@<release-or-branch>
```

For local checkout development or manual sync into ESPHome:

```yaml
packages:
  proflame2_tembed_base: !include ../packages/proflame2_tembed_base.yaml
  proflame2_tembed_display: !include ../packages/proflame2_tembed_display.yaml
```

## Hardware Target

The production example targets the LilyGO T-Embed CC1101:

```yaml
esp32:
  board: esp32-s3-devkitc-1
  framework:
    type: arduino
```

Known board facts:

- `BOARD_PWR_EN`: GPIO15
- SPI SCK: GPIO11
- SPI MOSI: GPIO9
- SPI MISO: GPIO10
- CC1101 CS: GPIO12
- CC1101 GDO0: GPIO3
- CC1101 GDO2: GPIO38
- RF switch SW1: GPIO47 = HIGH for 315 MHz path
- RF switch SW0: GPIO48 = LOW for 315 MHz path
- I2C SDA: GPIO8
- I2C SCL: GPIO18

GPIO3 is a strapping pin on ESP32-S3, and ESPHome warns about it. That warning
is expected on this board because LilyGO wires CC1101 GDO0 there. The package
uses `ignore_strapping_warning: true` only on that pin and only because the
hardware is fixed.

## Runtime Boundary

Home Assistant sends prepared payloads to the firmware. The firmware must not
regenerate or mutate the Proflame2 payload bytes.

TX flow:

1. Home Assistant builds the authoritative Proflame2 transmission plan.
2. Home Assistant calls the ESPHome `proflame2_tx_stateful` action.
3. Firmware validates the payload shape and repeat request.
4. Firmware transmits through the CC1101 async OOK path.
5. Firmware reports result metadata and restores the previous RX state.

RX flow:

1. Firmware configures the CC1101 as an ASK/OOK FIFO byte source.
2. Firmware drains FIFO bytes into a bounded rolling buffer.
3. Guided learning or active listening scans FIFO windows for Proflame2
   candidates.
4. Only decoded packets matching the learned profile are published to Home
   Assistant during active listening.
5. Home Assistant remains the owner of fireplace state updates and learning
   persistence.

TX always has priority over RX. If active listening is enabled, TX pauses RX and
the firmware restores the previous RX state after transmit.

## Validated RF Settings

Validated TX defaults:

- `tx_frequency_hz: 314973000`
- `data_rate_bps: 2400`
- `tx_repeat_count: 5`
- `tx_mode: proflame_native_groups`
- `native_group_timing_profile: native_remote`
- `native_group_repeat_boundary_mode: continuous_tx`
- `payload_bit_length_override: 182`
- `inter_frame_gap_us: 0`
- `post_frame_idle_gap_us: 0`

Validated RX defaults:

- `rx_frequency_hz: 314973000`
- `data_rate_bps: 2400`
- `RX FIFO Profile: rfcat_fixed_none_rfcat_wide`
- `Export window: 6000 ms`
- `Active listener scan interval: 1500 ms`

The detailed TX/RX register and waveform reference is documented in:

- `components/proflame2_tembed/README.md`

## Validation

Python tests do not require ESPHome to be installed:

```bash
./.venv/bin/python -m pytest -q
```

ESPHome validation uses a dedicated virtualenv because ESPHome and Home
Assistant currently require different dependency versions:

```bash
python3 -m venv .venv-esphome
./.venv-esphome/bin/python -m pip install -r requirements-esphome.txt
make esphome-config
make esphome-compile
make esphome-validate
```

The `make` targets stage a temporary no-space copy of the `esphome/` tree under
`/tmp/proflame2-esphome/repo` before running ESPHome. This avoids ESP-IDF path
failures when the working checkout path contains whitespace.

Compile success is not RF validation. Use an independent R820T/`rtl_433`
witness for RF checks. For release or hardware changes, validate:

- LilyGO TX is decoded by `rtl_433` and accepted by the fireplace.
- Guided learning completes from the native remote.
- Native remote or YardStick TX is reflected in Home Assistant through LilyGO
  active listening when active listening is enabled.
