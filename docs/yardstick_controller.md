# YardStick One Controller

The YardStick One is a USB fireplace controller for Proflame2. It connects to
the Home Assistant host by USB and does not require ESPHome firmware.

Use this guide when you want a USB controller attached to the Home Assistant
host.

One YardStick can be assigned to one fireplace in this integration. Home
Assistant rejects assigning a second fireplace to the same controller.
Selecting among multiple YardStick USB radios is not currently supported.

## Capabilities

| Capability | Supported |
| --- | --- |
| Control the fireplace | Yes |
| Guided learning from the original remote | Yes |
| Active listening after setup | No |
| Home Assistant state updates from original remote | No |
| Additional controller software required | No ESPHome firmware |

The integration supports legacy seven-word and extended ten-word remotes in
the selected 315 or 433 MHz band. Extended support has passed recorded-packet and automated
tests; confirmation with a physical extended fireplace remains pending.

## Radio Band

Choose **315 MHz** or **433 MHz** during fireplace setup, before guided learning
starts. The default is 315 MHz. Match the band used by the fireplace and its
original remote. The 433 MHz setting operates at 433.92 MHz.

Home Assistant automatically creates two devices under the one Proflame2 setup:
**YardStick One** and your **fireplace**. You do not add the controller separately.
To change the band later, open Settings → Devices & services → Devices →
YardStick One, then change **RF band** in its Configuration section. The fireplace
uses that controller setting. LilyGO's band remains configured in ESPHome YAML.

A band change waits for an active transmission to finish, clears pending
commands, and applies the radio settings without sending a fireplace command.
Finish learning before changing bands. If configuration fails, the previous
selection stays saved and fireplace controls pause. Select the desired band
again to retry. Repeating the saved band also retries a failed configuration.

Existing YardStick installations keep 315 MHz and gain the controller device
automatically. Removing the Proflame2 setup removes both devices together.

Both bands have automated software coverage. Physical 433 MHz operation and
extended-fireplace acceptance still require hardware validation.

## Pros And Cons

Pros:

- No additional controller firmware to build or install.
- Plugs into the Home Assistant host by USB.
- Supports guided learning from the original remote.
- Controls the fireplace after setup.

Cons:

- Does not support active listening after setup.
- USB passthrough can be difficult in some VM or container installations.
- Placement is limited by USB cable length and host location.
- No local screen or status display.

## Placement

During guided learning, hold the original remote within about 3 feet of the
YardStick controller.

For normal operation, start with the YardStick within about 20 feet of the
fireplace. This is only a starting point. Your home layout, fireplace enclosure,
metal, USB placement, and local interference can change the reliable range.

A USB extension cable can help place the YardStick closer to the fireplace.

## Setup Overview

YardStick setup has four main parts:

1. Install the [Proflame2 Home Assistant integration](../README.md#installation).
2. Connect the YardStick One to the Home Assistant host by USB.
3. Confirm Home Assistant can access the YardStick USB device.
4. Add the YardStick-backed fireplace in Home Assistant and run guided learning.

YardStick does not use ESPHome, so there is no ESPHome firmware, ESPHome
Builder setup, or ESPHome device selection step.

## Connect The YardStick

Connect the YardStick One to the computer running Home Assistant by USB. If
Home Assistant runs in a VM, Docker container, or supervised environment, make
sure the YardStick USB device is passed through to the Home Assistant runtime.

If you see intermittent failures:

- Confirm the YardStick device is still visible to Home Assistant.
- Check VM/container USB passthrough rules.
- Try a different USB port or powered USB hub.
- Try a USB extension cable to move the YardStick closer to the fireplace.
- If the YardStick is connected directly to a USB 3.0 port, try a USB extension
  cable to separate it from the port. USB 3.0 connectors can sometimes create
  interference for nearby radio devices.

## Add The Fireplace In Home Assistant

After the YardStick is connected and available to Home Assistant:

1. Open Settings -> Devices & services -> Proflame 2 Fireplace.
2. Select `Add entry`.
3. Select `Learn from remote`.
4. Enter the fireplace name. This is the name visible in Home Assistant.
5. Enter a fireplace short name.
6. Select `YARD Stick One USB Controller` as the `Controller Type`.
7. Select `Submit`.
8. Start guided learning.
9. Hold the original remote within about 3 feet of the YardStick controller.
10. Follow the prompts and press the requested buttons on the original remote.
11. Select the fireplace features your installation supports.
12. Validate Power, Flame, Fan, and any other enabled controls.

## Guided Learning

Guided learning connects the controller to your fireplace using the original
remote. Home Assistant will ask you to press buttons on the original remote.
Keep the remote within about 3 feet of the YardStick while learning.

Turn OFF thermostat and SMART mode before starting. If either mode is detected
on an extended remote, Home Assistant asks you to turn it off and continues
automatically when a manual-mode packet is received. Follow the displayed
instruction again after the mode change. If learning does not continue, press
Power once after turning both modes off.

Changes in room temperature, setpoint, or Fahrenheit/Celsius selection do not
require restarting extended learning. Those changes and repeated packets do
not count as additional control actions; continue following the button prompts.

When learning is complete, Home Assistant stores the values needed to control
that fireplace.

YardStick receive is used for guided learning only. YardStick does not keep Home
Assistant updated when the original remote is used after setup.

If YardStick guided learning cannot learn your remote, use the
[rtl_433 Learning ID helper](Using_rtl_433_for_learning.md). YardStick still
controls the fireplace after setup; the SDR and helper are only needed to
generate the Learning ID.

## Validation

After setup, validate:

- YardStick commands are accepted by the fireplace.
- The original remote still controls the fireplace.
- Home Assistant controls reflect the expected state after commands are sent.
- The controller remains reliable from its installed location.

## Technical Details

Technical implementation notes are in
[YardStick controller developer notes](yardstick_controller_dev.md).
