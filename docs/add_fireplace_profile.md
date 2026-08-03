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

When you add the Proflame2 integration, Home Assistant asks how you want to
learn or enter the fireplace remote profile. Most users should choose
`Learn from remote`.

## Learn From Remote

Recommended for normal setup.

Use this when you have a supported Proflame2 controller ready:

- LilyGO T-Embed CC1101, or
- YardStick One.

Home Assistant uses the selected controller to listen while you press buttons on
the original fireplace remote. When learning completes, that same controller is
used to control the fireplace.

Choose this option unless normal learning has already failed or a maintainer
asked you to use a different learning path.

## Learn From rtl_433 Output

Advanced fallback for troubleshooting.

Use this when normal `Learn from remote` does not complete, but rtl_433 can
decode your original Proflame2 remote with a separate SDR receiver.

This option uses different hardware for learning:

- the selected Proflame2 controller, such as LilyGO or YardStick, is still the
  device that will control the fireplace after setup,
- the SDR and rtl_433 are used only during setup to decode the original remote.

Home Assistant will ask you to run rtl_433 outside Home Assistant, press remote
buttons, and paste rtl_433 JSON output into the setup flow.

Full instructions are in
[rtl_433-assisted manual learning](rtl433_manual_learning.md).

## Manual Entry

Advanced maintainer option.

Use this only if you already know the permanent remote values:

- remote ID,
- C1,
- D1,
- C2,
- D2.

Most users will not know these values. This option does not listen to a remote
and does not run rtl_433.

## Which Option Should I Choose?

Choose `Learn from remote` for normal setup.

Choose `Learn from rtl_433 output` only when normal learning fails and you have
an SDR receiver running rtl_433.

Choose `Manual entry` only when you already have the exact remote profile values
from prior troubleshooting or maintainer guidance.
