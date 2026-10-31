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
3. Open the
   [LilyGO YAML helper](https://jeffgregx2.github.io/HACS-Proflame2/tools/lilygo-yaml-helper.html).
4. Paste the copied YAML into the left text box in the helper.
5. Select `Add LilyGO Proflame2 support`.
6. Copy the generated YAML from the right text box.
7. Return to the ESPHome Builder YAML editor.
8. Select all of the existing YAML text.
9. Paste the changed YAML from the helper.
10. Save the YAML in ESPHome Builder.

The helper preserves the ESPHome-generated `api`, `ota`, `wifi`, `logger`, and
device identity settings. It also sets the ESP32 framework to ESP-IDF to support
retained crash reporting, adds the Proflame2 package references, and
adds a restart switch if one is not already present.

## Firmware Framework

Use ESP-IDF for the current LilyGO firmware. In the existing `esp32:` section,
verify this setting before rebuilding:

```yaml
esp32:
  board: esp32-s3-devkitc-1
  framework:
    type: esp-idf
```

Keep the existing board selection and other device settings unless they are
incorrect. Updating `proflame2_package_ref` alone does not change the framework
in your device YAML. The public YAML helper follows the stable `main` branch;
when testing a newer prerelease, check the framework setting even if the helper
created the YAML. Older helper output may need this manual correction.

ESP-IDF enables retained crash reporting on supported ESPHome builds. See the
[manual setup guide](lilygo_cc1101_manual_yaml.md) for migration and diagnostic
details.

## Radio Band

The default LilyGO radio band is 315 MHz. If the label on the original remote
states 433.92 MHz, add this setting to the top-level `substitutions:` block in
the device YAML before building the firmware:

```yaml
substitutions:
  proflame2_rf_band: "433"
```

If `substitutions:` already exists, add or update the setting inside that block;
do not create a second block. Rebuild and install the firmware for the change
to take effect. To return to the default band, use `"315"` or remove the setting,
then rebuild and install again.

A LilyGO can use only one radio band at a
time, so fireplaces on different bands need separate LilyGO controllers.

To manually update your YAML file instead, see
[manual LilyGO YAML setup](lilygo_cc1101_manual_yaml.md).

## Reliable Transmission

The default settings are intended for normal use; most users should not need to
adjust them. Starting with v0.7.0-beta4, LilyGO uses these defaults:

| Setting | Default | Where to change it |
| --- | --- | --- |
| `proflame2_diagnostic_repeat_count_override` | `10` | ESPHome device YAML, under `substitutions:`; rebuild and install afterward |
| Delayed Retransmission | OFF | The ESPHome device's configuration controls in Home Assistant |

### Why Commands Are Repeated

One press on a native remote sends several copies of the same command in rapid
succession. Our captured native remote sends five copies in one burst; later
duplicate bursts were observed about 15-16 seconds apart. That timing should not
be assumed for every remote model. Copies within a burst and a later repeat of
the whole burst are two different things.

Earlier stable LilyGO firmware also sent five copies, but did not send a later
repeat burst. Extensive repeated testing found that the fireplace occasionally
ignored a command even when an SDR running rtl_433 decoded it correctly. A
successful transmission or decode does not confirm that the fireplace received
and acted on the command.

### Ten Copies Per Command

Increasing the number of copies from five to ten appeared to meaningfully
improve fireplace reception in testing. This is now the default, so no YAML
change is needed unless you previously configured a different override.

The tradeoff is roughly twice the transmission time: about 0.8 seconds for a
legacy remote profile and 1.15 seconds for an extended profile. LilyGO cannot
listen to the native remote during that burst, and the radio cannot start
another transmission until the current one finishes. See
[Using the original remote](#using-the-original-remote) for overlapping changes.

### Optional Delayed Retransmission

When enabled, **Delayed Retransmission** sends the whole command burst again
about two seconds after the first burst finishes, unless newer activity cancels
it. This is separate from the ten copies sent within each burst; the two-second
interval is LilyGO's retry timing, not the native remote timing measured above.

The successful ten-copy tests did not require this extra burst, so the default
is OFF. It remains available for troubleshooting intermittent reception. The
switch returns to OFF when the controller reboots. Neither setting guarantees
reception; controller placement, range, and interference still matter.

## Using The Original Remote

LilyGO cannot listen to the original remote while it is transmitting.
**Delayed Retransmission** is OFF by default. When enabled, an HA command can produce a second transmit
burst about two seconds after the first finishes. LilyGO listens between the
bursts, but a remote command overlapping either burst may be missed or collide
at the fireplace. A remote command received before the retry cancels that retry;
a command that LilyGO cannot hear cannot cancel it.

Avoid making conflicting changes through HA and the remote at the same time.
The fireplace does not acknowledge commands, so HA and the LilyGO display show
the last commanded or observed state, not independently confirmed fireplace
state. The ESPHome device's **Delayed Retransmission** switch can disable the
extra burst for troubleshooting, but the first burst still prevents listening
while it is being sent.

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

If normal guided learning cannot learn your remote, use the
[rtl_433 Learning ID helper](Using_rtl_433_for_learning.md). The LilyGO still
controls the fireplace and Active Listening after setup; the SDR and helper
are only needed to generate the Learning ID.

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
