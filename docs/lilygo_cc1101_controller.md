# LilyGO T-Embed CC1101 Controller

The LilyGO T-Embed CC1101 is a Wi-Fi fireplace controller for Proflame2. It
runs ESPHome firmware and is intended to be installed near the fireplace.

Use this guide when you want a permanent controller that can control the
fireplace and keep Home Assistant updated when the original remote is used.

## Capabilities

| Capability | Supported |
| --- | --- |
| Control the fireplace | Yes |
| Guided learning from the original remote | Yes |
| Active listening after setup | Yes |
| Home Assistant state updates from original remote | Yes |
| Additional controller software required | ESPHome Builder to configure |

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

The LilyGO controller requires firmware compatible with the Proflame2
integration installed in Home Assistant. Setup has five main parts:

1. Install the [Proflame2 Home Assistant integration](../README.md#installation).
2. Create a new ESPHome device. This creates the device YAML configuration file
   you need for the next steps.
3. Use the LilyGO YAML helper to update the device YAML configuration file.
4. Build and deploy the firmware to the LilyGO. The first install normally uses
   USB; later updates should use Over-The-Air (OTA) updates via Wi-Fi.
5. Add the LilyGO-backed fireplace in Home Assistant and run guided learning.

## Create The ESPHome Device

Using ESPHome Builder, add a new device so ESPHome creates the device YAML
configuration file for your Home Assistant installation.

1. In Home Assistant, open ESPHome Builder.
2. Select `+ New Device`.
3. Select `New Device Setup` and continue.
4. Enter a name such as `Fireplace-LilyGO`, then select `Next`.
5. Select `ESP32-S3` as the device type.
6. Continue through the ESPHome Builder prompts. If the LilyGO is connected to
   the computer by USB, ESPHome may proceed directly to compiling and flashing
   the generated starter firmware.
7. When the device has been created, select `EDIT` to open the YAML
   configuration file.

Avoid spaces and tabs in the ESPHome device name. The name is also used for the
device hostname, such as `Fireplace-LilyGO.local`.

ESPHome's guide for creating devices in Home Assistant is here:
https://esphome.io/guides/getting_started_hassio/

## Update The ESPHome YAML

After ESPHome creates the base device, use the LilyGO YAML helper to update the
device YAML configuration file:

1. In ESPHome Builder, open the YAML editor for the device you created in the
   prior step.
2. Select all of the YAML text and copy it.
3. Open the [LilyGO YAML helper](tools/lilygo-yaml-helper.html).
4. Paste the copied YAML into the left text box in the helper.
5. Select `Add LilyGO Proflame2 support`.
6. Copy the generated YAML from the right text box.
7. Return to the ESPHome Builder YAML editor.
8. Select all of the existing YAML text.
9. Paste the changed YAML from the helper.
10. Save the YAML in ESPHome Builder.

The helper preserves the ESPHome-generated `api`, `ota`, `wifi`, `logger`, and
device identity settings. It also updates the ESP32 framework to the current
validated LilyGO firmware framework, adds the Proflame2 package references, and
adds a restart switch if one is not already present.

To manually update your YAML file instead, see
[manual LilyGO YAML setup](lilygo_cc1101_manual_yaml.md).

## Build And Deploy

After updating the YAML:

1. For the first install, connect the LilyGO by USB to the computer running the
   browser with ESPHome Builder open.
2. In ESPHome Builder, select `Install` for the LilyGO device.
3. For the first install, choose `Plugged into this computer` and follow the
   browser USB prompts.
4. Confirm the ESPHome device appears online in ESPHome Builder.

After the firmware is installed and the LilyGO is online, you should not need
USB for normal firmware updates. For future updates, select `Install` and then
choose `Wireless` to build and deploy using Over-The-Air (OTA) updates via
Wi-Fi.

If the YAML helper reports missing `api`, `ota`, or `wifi` sections, redo
[Update The ESPHome YAML](#update-the-esphome-yaml) and make sure you copy the
full YAML configuration file from ESPHome Builder.

## Add The ESPHome Device To Home Assistant

After the LilyGO firmware is online, add the ESPHome device to Home Assistant:

1. Open Settings -> Devices & services -> ESPHome.
2. Select `Add device`.
3. For `Host`, enter the ESPHome device name with `.local` added, such as
   `Fireplace-LilyGO.local`.
4. Leave the port unchanged.
5. Select `Submit`.
6. Confirm the ESPHome device is online in Home Assistant.

If `.local` discovery does not work in your network, use the LilyGO IP address
instead.

## Add The Fireplace In Home Assistant

After the ESPHome device is available in Home Assistant:

1. Open Settings -> Devices & services -> Proflame 2 Fireplace.
2. Select `Add entry`.
3. Select `Learn from remote`.
4. Enter the fireplace name. This is the name visible in Home Assistant.
5. Enter a fireplace short name. Controllers with displays, such as the LilyGO,
   show this shorter name on the display.
6. Select `LilyGO T-Embed CC1101` as the `Controller Type`. This tells the
   Proflame2 integration what kind of controller you are using.
7. Select `Submit`.
8. Select the matching ESPHome device when prompted. This is the device name you
   created earlier in ESPHome, such as `Fireplace-LilyGO`.
9. Start guided learning.
10. Hold the original remote within about 3 feet of the LilyGO controller.
11. Follow the prompts and press the requested buttons on the original remote.
12. Select the fireplace features your installation supports.
13. Validate Power, Flame, Fan, and any other enabled controls.

## Active Listening

Active listening lets the LilyGO controller update Home Assistant when the
original remote changes the fireplace. It is enabled by default for LilyGO.

Important behavior:

- Only the learned fireplace is reported to Home Assistant.
- Fireplace commands from Home Assistant take priority over listening.
- You can disable active listening from the Proflame2 options or LilyGO device
  controls if needed.

## Validation

After setup, validate:

- LilyGO commands are accepted by the fireplace.
- The original remote still controls the fireplace.
- Home Assistant updates when the original remote is used.
- The controller remains reliable from its installed location.

## Advanced Details

Technical implementation notes are in
[LilyGO CC1101 controller developer notes](lilygo_cc1101_controller_dev.md).

Advanced ESPHome source/build details are in
[ESPHome firmware build guide](esphome_firmware_build.md).
