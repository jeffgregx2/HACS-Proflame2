# Proflame2 Setup Guide

Use this guide when adding a fireplace profile to the Proflame2 Home Assistant
integration.

## Overall Setup Flow

1. Install the Proflame2 integration.
2. Prepare the controller that will control the fireplace:
   - LilyGO T-Embed CC1101, or
   - YardStick One.
3. Open Settings -> Devices & services -> Proflame 2 Fireplace.
4. Select `Add entry`.
5. Choose how Home Assistant should learn or receive the remote profile values.
6. Select the fireplace features your installation supports.
7. Validate basic controls from Home Assistant.

The controller you choose is the device Home Assistant will use to control the
fireplace after setup. The learning option controls how Home Assistant obtains
the original remote profile values during setup.

## Add Fireplace Profile Options

The Home Assistant help button opens this page for multiple setup screens. Use
this index to jump to the section that matches the screen you are viewing:

- [Add fireplace profile](#add-fireplace-profile): choose how to set up the
  fireplace profile.
- [Learn fireplace profile](#learn-fireplace-profile): normal setup using the
  original remote and your LilyGO or YardStick controller.
- [Learn from rtl_433 output](#learn-from-rtl_433-output): fallback setup using
  a separate SDR receiver after normal learning fails.
- [Select supported features](#select-supported-features): choose which
  fireplace controls Home Assistant should expose.
- [Manual fireplace profile](#manual-fireplace-profile): enter known remote
  profile values directly.

## Add Fireplace Profile

When you first add the Proflame2 integration, Home Assistant asks which setup
path to use.

- **Learn from remote (Recommended)**: normal setup. This uses the selected
  LilyGO or YardStick controller to listen while you press buttons on the
  original fireplace remote. Most users should choose this option.
- **Learn from rtl_433 output**: use only if normal learning does not complete
  and you have, or are willing to obtain, a separate SDR receiver.
- **Manual entry**: use only if you already know the exact remote ID and C/D
  values for the fireplace remote.

## Learn Fireplace Profile

This is the normal `Learn from remote` setup path and is recommended for most
users.

Use this when you have a supported Proflame2 controller ready:

- LilyGO T-Embed CC1101, or
- YardStick One.

**Fireplace name** is the name Home Assistant shows for the fireplace device and
its entities. Use a name that clearly identifies the fireplace, such as `Living
Room Fireplace` or `Basement Fireplace`.

**Display short name** is the short name shown on a supported Proflame2 controller
display. It is intended for small screens where the full Home Assistant device
name may not fit. Use a short label that is easy to recognize at a glance, such
as `LIVING`, `DEN`, or `BASEMENT`.

**Controller Type** is the device Home Assistant will use to control the fireplace
after setup. Choose `LilyGO T-Embed CC1101` if you are using the LilyGO
controller firmware. Choose `YardStick One` if you are using a YardStick
controller.

After you select `Submit`, Home Assistant starts listening with the selected
controller and walks you through a short sequence of original-remote button
presses. Start with the fireplace turned off so each prompted button press
matches what Home Assistant expects.

When learning completes, the same controller is used to control the fireplace.

**If you choose LilyGO T-Embed CC1101**

If you choose `LilyGO T-Embed CC1101`, Home Assistant will also ask you to choose
the LilyGO ESPHome device before learning starts. Home Assistant needs this so it
knows which ESPHome device should listen for the original remote.

Select the ESPHome device that is running the Proflame2 LilyGO firmware.

If the device is not listed, add the LilyGO controller to Home Assistant through
the ESPHome integration first, then return to Proflame2 setup. See the
[LilyGO CC1101 controller guide](lilygo_cc1101_controller.md) for setup
instructions.

## Learn From rtl_433 Output

Occasionally, normal `Learn from remote` does not complete successfully. If that
happens, you can use the separate rtl_433 utility and an SDR receiver to decode
your original Proflame2 remote.

An SDR receiver is a small USB radio receiver that can be tuned by software to
listen for many types of radio signals. In this setup, rtl_433 uses the SDR
receiver to listen for and decode the Proflame2 remote.

This option is only for learning the remote. Your LilyGO or YardStick controller
will still be the device Home Assistant uses to control the fireplace after
setup.

Use this option only after `Learn from remote` has failed.

This option requires hardware and software that are separate from the normal
Proflame2 controller:

- a LilyGO or YardStick controller to control the fireplace after setup,
- an SDR receiver (hardware to be obtained separately),
- the rtl_433 utility installed on the computer that will use the SDR receiver.

Home Assistant will not run rtl_433 for you. You will run rtl_433 yourself in a
terminal window, either on the Home Assistant computer or on another computer
connected to the SDR receiver.

On the `Learn from rtl_433 output` screen, fill in the same fireplace fields
described in [Learn fireplace profile](#learn-fireplace-profile).

Home Assistant will then tell you which remote buttons to press. After each
button press, paste the rtl_433 JSON output into the setup flow.

Full instructions including example SDR receivers needed can be found at:
[rtl_433-assisted manual learning](rtl433_manual_learning.md).

## Select Supported Features

After learning completes, Home Assistant asks which fireplace features should be
enabled.

Individual fireplaces do not support every Proflame2 feature. Enable only the
features your fireplace actually has. These choices control which Home Assistant
entities are created for the fireplace.

Available feature choices:

- `Fan supported`: enable fan speed controls.
- `Light supported`: enable accent light controls.
- `Front burner supported`: enable front burner controls for fireplaces that
  have a separately controlled front burner.
- `Aux supported`: enable auxiliary controls for fireplaces that have an
  auxiliary output.
- `CPI supported`: enable Continuous Pilot Ignition controls.
- `Enable packet debug log`: write detailed packet/debug information for
  troubleshooting. Leave this off unless you are diagnosing a problem.
- `Enable active listening`: allow Home Assistant to keep listening for remote
  updates after setup when the selected controller supports receiving. Leave
  this off unless you want Home Assistant to track changes made from the
  original remote.

This screen also shows `Display short name` again so you can confirm or adjust
the short label before the fireplace is created.

## Manual Fireplace Profile

Manual entry is not a normal setup path. Use it only when you already know the
exact remote profile values from prior troubleshooting.

In almost all situations, use `Learn from remote` first. If that fails, use
`Learn from rtl_433 output`.

Manual entry requires:

- `Remote ID`,
- `C1`,
- `D1`,
- `C2`,
- `D2`.

Most users will not know these values. This option does not listen to a remote
and does not run rtl_433.
