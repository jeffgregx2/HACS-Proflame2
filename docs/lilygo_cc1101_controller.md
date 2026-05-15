# LilyGO T-Embed CC1101 Controller

The LilyGO T-Embed CC1101 backend uses an ESPHome device as the Proflame2 RF
controller. It is designed to be installed near the fireplace and connected to
Home Assistant over the ESPHome native API.

## Capabilities

| Capability | Supported |
| --- | --- |
| Transmit Proflame2 commands | Yes |
| Guided learning from the native remote | Yes |
| Active listening for matching remote packets | Yes |
| Home Assistant state synchronization | Yes |
| ESPHome diagnostic entities | Yes |

Active listening only publishes packets that match the learned fireplace
identity/profile. Packets from other Proflame2 remotes and unrelated RF noise
are dropped by the firmware before they become Home Assistant state updates.

## When To Choose LilyGO

LilyGO is a good fit when:

- You want a network RF node located near the fireplace.
- You do not want a USB RF device attached to the Home Assistant host.
- You want always-on active listening for native remote state tracking.
- You are comfortable building and deploying ESPHome firmware.

Tradeoffs:

- Requires compatible LilyGO T-Embed CC1101 hardware.
- Requires ESPHome firmware build and deployment.
- Firmware updates are handled by the user through ESPHome.

## Setup Overview

1. Install the Proflame2 Home Assistant integration.
2. Create the LilyGO device in ESPHome Builder so ESPHome generates local API, OTA, and Wi-Fi secrets.
3. Edit the generated ESPHome YAML using `esphome/examples/lilygo_cc1101_example.yaml` as the overlay reference.
4. Build and deploy the LilyGO ESPHome firmware.
5. Confirm the ESPHome device is online in Home Assistant.
6. Add the Proflame2 integration.
7. Select the LilyGO CC1101 RF backend.
8. Link the integration to the ESPHome device.
9. Run guided learning using the original fireplace remote.
10. Validate basic controls.
11. Enable active listening if you want state updates from the native remote.

## ESPHome Firmware

This repository provides ESPHome source and package files. It does not provide
prebuilt firmware binaries.

For general ESPHome device creation, see the ESPHome getting-started guide:
https://esphome.io/guides/getting_started_hassio/

For Proflame2-specific firmware guidance, see:
[ESPHome firmware build guide](esphome_firmware_build.md)

Use this repository file as the current LilyGO Proflame2 YAML reference:

- `esphome/examples/lilygo_cc1101_example.yaml`
- `esphome/packages/proflame2_tembed_base.yaml`
- `esphome/packages/proflame2_tembed_display.yaml`
- `esphome/packages/proflame2_tembed_debug.yaml`

Production firmware should use the normal base/display packages. Debug-only
packages expose deeper diagnostic controls and raw FIFO capture tools intended
for troubleshooting, not normal operation.

### ESPHome Builder Order

If you have not created the LilyGO device in ESPHome yet:

1. In Home Assistant, install/open the ESPHome Device Builder add-on.
2. Choose the ESPHome option to create a new device.
3. Enter a device name such as `lilygo-proflame2`.
4. Enter your Wi-Fi details if ESPHome asks for them.
5. Select an ESP32 board/device target. This initial choice only creates the
   YAML; you will replace the generated `esp32:` block with the LilyGO target
   from the Proflame2 example.
6. Save the generated device YAML.
7. Open the generated YAML for editing.
8. Keep the generated local sections for `api:`, `ota:`, `wifi:`, `logger:`,
   `esphome:`, and any generated secrets.
9. Apply the Proflame2-specific edits from `esphome/examples/lilygo_cc1101_example.yaml`.
10. Build and deploy from ESPHome.
11. Confirm the ESPHome device appears online in Home Assistant.
12. Start Proflame2 guided learning and select the LilyGO backend.

The important point is that ESPHome creates the local API encryption key and OTA
password for you. The Proflame2 example is an overlay on top of that generated
device YAML, not a replacement for the generated local credentials.

ESPHome's own guide for creating devices in Home Assistant is here:
https://esphome.io/guides/getting_started_hassio/

### Package References

For normal release use, prefer release-pinned `github://` package references:

```yaml
packages:
  proflame2_tembed_base: github://jeffgregx2/HACS-Proflame2/esphome/packages/proflame2_tembed_base.yaml@<release-or-branch>
  proflame2_tembed_display: github://jeffgregx2/HACS-Proflame2/esphome/packages/proflame2_tembed_display.yaml@<release-or-branch>
```

For local checkout development or manual sync into ESPHome, use local includes:

```yaml
packages:
  proflame2_tembed_base: !include ../packages/proflame2_tembed_base.yaml
  proflame2_tembed_display: !include ../packages/proflame2_tembed_display.yaml
```

The debug package is intentionally omitted from production examples. Add it only
when troubleshooting low-level RF/FIFO behavior.

## Learning Flow

During guided learning, Home Assistant asks you to press buttons on the native
remote. The LilyGO device captures CC1101 FIFO byte windows and sends them to
Home Assistant. Home Assistant validates accepted candidates and stores the
learned identity/profile for the fireplace.

Only validated candidates are promoted into the learned fireplace profile.

## Active Listening

Active listening lets the LilyGO device monitor Proflame2 packets after setup.
When it sees a valid packet matching the learned fireplace identity/profile, it
publishes the packet to Home Assistant so the UI can track changes made by the
native remote or another compatible controller.

Important behavior:

- Active listening is disabled by default.
- It can be enabled from Home Assistant options/device control.
- TX has priority; RX pauses during transmit and resumes afterward.
- Repeats within a single transmission are suppressed.
- Nonmatching or invalid packets are dropped.

## Validation

After setup, validate at least:

- LilyGO transmit is accepted by the fireplace.
- `rtl_433` can decode LilyGO transmissions if you use it as an external witness.
- Native remote changes are reflected in Home Assistant when active listening is enabled.
- TX still works after active listening has been enabled.
