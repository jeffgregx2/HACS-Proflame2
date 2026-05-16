# YardStick One Controller

The YardStick One is a USB fireplace controller for Proflame2. It connects to
the Home Assistant host by USB and does not require ESPHome firmware.

Use this guide when you want a USB controller attached to the Home Assistant
host.

## Capabilities

| Capability | Supported |
| --- | --- |
| Control the fireplace | Yes |
| Guided learning from the original remote | Yes |
| Active listening after setup | No |
| Home Assistant state updates from original remote | No |
| Additional controller software required | No ESPHome firmware |

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

1. Connect the YardStick One to the Home Assistant host.
2. Ensure Home Assistant can access the USB device.
3. Install the Proflame2 Home Assistant integration.
4. Add or reconfigure the Proflame2 integration.
5. Select `YardStick One` as the controller.
6. Run guided learning with the original remote.
7. Validate basic controls.

## Guided Learning

Guided learning connects the controller to your fireplace using the original
remote.

Home Assistant will ask you to press buttons on the original remote. Keep the
remote within about 3 feet of the YardStick while learning. When learning is
complete, Home Assistant stores the values needed to control that fireplace.

YardStick receive is used for guided learning only. YardStick does not keep Home
Assistant updated when the original remote is used after setup.

## USB Notes

If Home Assistant runs in a VM, Docker container, or supervised environment,
make sure the YardStick USB device is passed through to the Home Assistant
runtime.

If you see intermittent failures:

- Confirm the YardStick device is still visible to Home Assistant.
- Check VM/container USB passthrough rules.
- Try a different USB port or powered USB hub.
- Try a USB extension cable to move the YardStick closer to the fireplace.

## Validation

After setup, validate:

- YardStick commands are accepted by the fireplace.
- The original remote still controls the fireplace.
- Home Assistant controls reflect the expected state after commands are sent.
- The controller remains reliable from its installed location.

## Technical Details

Technical implementation notes are in
[YardStick controller developer notes](yardstick_controller_dev.md).
