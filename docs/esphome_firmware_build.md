# ESPHome Firmware Build Guide

This guide is for LilyGO T-Embed CC1101 users. The Proflame2 integration does
not distribute prebuilt firmware. You build and deploy firmware locally with
ESPHome.

## Requirements

- Home Assistant with ESPHome Builder, or the ESPHome CLI.
- LilyGO T-Embed CC1101 hardware.
- Network access from Home Assistant to the LilyGO device.
- The Proflame2 Home Assistant integration installed.

## Build Model

The firmware is provided as ESPHome source:

- External component source under `esphome/components/proflame2_tembed`.
- Package files under `esphome/packages`.
- Example YAML under `esphome/examples`.

Use the LilyGO example and packages as the reference starting point:

- `esphome/examples/lilygo_cc1101_example.yaml`
- `esphome/packages/proflame2_tembed_base.yaml`
- `esphome/packages/proflame2_tembed_display.yaml`
- `esphome/packages/proflame2_tembed_debug.yaml`

## Recommended User Flow

1. Install the Proflame2 Home Assistant integration.
2. Create a new ESPHome device with ESPHome Builder.
3. Let ESPHome generate the YAML and local secrets.
4. Keep the generated `esphome:`, `wifi:`, `api:`, `ota:`, and `logger:` sections.
5. Replace the generated `esp32:` block with the LilyGO target from the example YAML.
6. Add the Proflame2 `packages:` block from the example YAML.
7. Build the firmware in ESPHome Builder.
8. Deploy the firmware to the LilyGO device.
9. Confirm the ESPHome device is online in Home Assistant.
10. Add or reconfigure the Proflame2 integration to use the LilyGO CC1101 controller.
11. Run guided learning from Home Assistant.

Do not manually create Proflame2-specific API encryption or OTA secrets. The
generated ESPHome device already has local API/OTA credentials; keep those.

## Creating The Base ESPHome Device

If you are starting from scratch, use ESPHome Builder first:

1. Open Home Assistant.
2. Open the ESPHome Device Builder add-on.
3. Create a new device.
4. Give it a name, for example `lilygo-proflame2`.
5. Enter Wi-Fi details if prompted.
6. Let ESPHome create the initial YAML file.
7. Open that generated YAML for editing.

At this point, the YAML should already contain local device identity, API, OTA,
Wi-Fi, and logging configuration. Keep those generated sections. Then apply the
Proflame2 changes from `esphome/examples/lilygo_cc1101_example.yaml`.

ESPHome's getting-started guide covers the base device creation flow:
https://esphome.io/guides/getting_started_hassio/

## Package References

For normal release use, prefer release-pinned `github://` package references:

```yaml
packages:
  proflame2_tembed_base: github://jeffgregx2/HACS-Proflame2/esphome/packages/proflame2_tembed_base.yaml@<release-or-branch>
  proflame2_tembed_display: github://jeffgregx2/HACS-Proflame2/esphome/packages/proflame2_tembed_display.yaml@<release-or-branch>
```

For local checkout development or manual sync into ESPHome, use local includes
from this repository layout:

```yaml
packages:
  proflame2_tembed_base: !include ../packages/proflame2_tembed_base.yaml
  proflame2_tembed_display: !include ../packages/proflame2_tembed_display.yaml
```

## Debug Firmware

Normal users should not need deep diagnostic controls. Those controls belong in
debug firmware.

When debug diagnostics are needed, include the debug package:

```yaml
packages:
  proflame2_tembed_debug: github://jeffgregx2/HACS-Proflame2/esphome/packages/proflame2_tembed_debug.yaml@<release-or-branch>
```

Debug firmware may expose manual capture and profile diagnostics. Production
firmware should keep the UI focused on normal operation.

## After Deployment

After firmware deployment:

1. Confirm the ESPHome device is available in Home Assistant.
2. Confirm the Proflame2 integration can link to the ESPHome device.
3. Run guided learning.
4. Validate TX from Home Assistant.
5. Enable active listening if desired and validate native remote state updates.
