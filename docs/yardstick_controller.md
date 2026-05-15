# YardStick One Controller

The YardStick backend uses a USB YardStick One with rfcat as the Proflame2 RF
controller. It is useful when you prefer a directly attached USB RF backend and
do not want to build ESPHome firmware.

## Capabilities

| Capability | Supported |
| --- | --- |
| Transmit Proflame2 commands | Yes |
| Guided learning from the native remote | Yes |
| Receive for learning and validation | Yes |
| Active listening | Receive-capable, environment-dependent |
| ESPHome firmware required | No |

## When To Choose YardStick

YardStick is a good fit when:

- You already have a YardStick One.
- You can pass the USB device reliably into Home Assistant.
- You want to avoid ESPHome firmware setup.
- You want a direct RF backend attached to the Home Assistant host.

Tradeoffs:

- Requires USB passthrough if Home Assistant runs in a VM or container.
- Depends on the rfcat/libusb stack.
- Placement is limited by USB location unless you extend the USB setup.
- For always-on passive state tracking, LilyGO is usually the cleaner deployment model.

## Setup Overview

1. Connect the YardStick One to the Home Assistant host.
2. Ensure the host/container/VM can access the USB device.
3. Install the Proflame2 Home Assistant integration.
4. Add the Proflame2 integration.
5. Select the YardStick RF backend.
6. Run guided learning using the original fireplace remote.
7. Validate basic controls.

## Learning Flow

During guided learning, Home Assistant uses the YardStick backend to receive
native remote packets. Valid decoded packets are used to learn the fireplace
remote serial ID and profile values.

The learned profile is then used to generate valid Proflame2 transmit packets
for normal operation.

## USB Notes

If Home Assistant runs in a VM, Docker container, or supervised environment,
make sure the YardStick USB device is passed through to the Home Assistant
runtime. USB instability can cause receive or transmit failures even when the
integration configuration is correct.

If you see intermittent failures:

- Confirm the YardStick device is still visible to Home Assistant.
- Check VM/container USB passthrough rules.
- Avoid sharing an unstable USB controller with other critical devices.
- Restarting Home Assistant may not fix a lower-level USB passthrough problem.

## Validation

After setup, validate at least:

- YardStick transmit is accepted by the fireplace.
- `rtl_433` can decode YardStick transmissions if you use it as an external witness.
- Guided learning can receive native remote packets.
- Home Assistant controls reflect the expected state after commands are sent.
