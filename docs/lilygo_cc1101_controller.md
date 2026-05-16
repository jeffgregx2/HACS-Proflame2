# LilyGO T-Embed CC1101 Controller

The LilyGO T-Embed CC1101 is a Wi-Fi fireplace controller for Proflame2. It runs
ESPHome firmware and is intended to be installed near the fireplace.

Use this guide when you want a permanent controller that can both control the
fireplace and keep Home Assistant updated when the original remote is used.

## Capabilities

| Capability | Supported |
| --- | --- |
| Control the fireplace | Yes |
| Guided learning from the original remote | Yes |
| Active listening after setup | Yes |
| Home Assistant state updates from original remote | Yes |
| Additional controller software required | Yes, ESPHome firmware |

## Pros And Cons

Pros:

- Can be placed near the fireplace over Wi-Fi.
- Supports active listening, so Home Assistant can track original remote changes.
- Has a built-in screen for local status.
- Good fit for a permanent installation.

Cons:

- Requires compatible LilyGO T-Embed CC1101 hardware.
- Requires ESPHome firmware setup.
- Requires USB power near the fireplace.
- Depends on Wi-Fi reliability.

## Placement

During guided learning, hold the original remote within about 3 feet of the
LilyGO controller.

For normal operation, start with the LilyGO within about 15 feet of the
fireplace. This is only a starting point. Your home layout, fireplace enclosure,
metal, Wi-Fi placement, and local interference can change the reliable range.

## Setup Overview

1. Install the Proflame2 Home Assistant integration.
2. Create the LilyGO device in ESPHome Device Builder.
3. Let ESPHome generate the local device YAML, API key, OTA password, and Wi-Fi
   secrets.
4. Edit the generated YAML using
   `esphome/examples/lilygo_cc1101_example.yaml` as the reference.
5. Build and deploy the LilyGO ESPHome firmware.
6. Confirm the ESPHome device is online in Home Assistant.
7. Add or reconfigure the Proflame2 integration.
8. Select `LilyGO T-Embed CC1101` as the controller.
9. Select the matching ESPHome device when prompted.
10. Run guided learning with the original remote.
11. Validate basic controls.
12. Enable active listening if you want Home Assistant to track original remote
    changes.

## ESPHome Device Creation

This repository provides the Proflame2 ESPHome configuration, but ESPHome should
create the local device first. That keeps the API encryption key, OTA password,
and Wi-Fi secrets local to your Home Assistant installation.

If you have not created the LilyGO device in ESPHome yet:

1. In Home Assistant, install/open the ESPHome Device Builder add-on.
2. Create a new ESPHome device.
3. Enter a device name such as `lilygo-proflame2`.
4. Enter your Wi-Fi details if ESPHome asks for them.
5. Save the generated device YAML.
6. Open the generated YAML for editing.
7. Keep the generated local sections for `esphome:`, `api:`, `ota:`, `wifi:`,
   `logger:`, and generated `!secret` values.
8. Apply the Proflame2-specific edits from
   `esphome/examples/lilygo_cc1101_example.yaml`.
9. Build and deploy from ESPHome.
10. Confirm the ESPHome device appears online in Home Assistant.

ESPHome's guide for creating devices in Home Assistant is here:
https://esphome.io/guides/getting_started_hassio/

## Package References

For normal release use, prefer release-pinned GitHub package references in the
ESPHome YAML:

```yaml
packages:
  proflame2_tembed_base: github://jeffgregx2/HACS-Proflame2/esphome/packages/proflame2_tembed_base.yaml@<release-or-branch>
  proflame2_tembed_display: github://jeffgregx2/HACS-Proflame2/esphome/packages/proflame2_tembed_display.yaml@<release-or-branch>
```

Local installation by copying the ESPHome package files into your ESPHome
configuration is also possible. Use the GitHub package path unless you have a
specific reason to maintain local copies.

## Guided Learning

Guided learning connects the controller to your fireplace using the original
remote.

Home Assistant will ask you to press buttons on the original remote. Keep the
remote within about 3 feet of the LilyGO controller while learning. When learning
is complete, Home Assistant stores the values needed to control that fireplace.

## Active Listening

Active listening lets the LilyGO controller update Home Assistant when the
original remote changes the fireplace.

Important behavior:

- Active listening is disabled by default.
- It can be enabled from the Proflame2 options or LilyGO device controls.
- Only the learned fireplace is reported to Home Assistant.
- Fireplace commands from Home Assistant still take priority.

## Validation

After setup, validate:

- LilyGO commands are accepted by the fireplace.
- The original remote still controls the fireplace.
- If active listening is enabled, Home Assistant updates when the original
  remote is used.
- The controller remains reliable from its installed location.

## Technical Details

Technical implementation notes are in
[LilyGO CC1101 controller developer notes](lilygo_cc1101_controller_dev.md).
