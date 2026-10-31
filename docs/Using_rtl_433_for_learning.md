# Using rtl_433 for Learning

Use this guide if **Learn from remote** in Home Assistant cannot learn your
remote, or you have been asked to try this method for troubleshooting.

The Learning ID helper listens while you press buttons on your original
remote. It gives you a **Learning ID** to paste into Home Assistant. The helper
tells you what to press and confirms each button press it receives.

Your computer does not transmit fireplace commands, but pressing the original
remote can operate the fireplace. Follow its normal operating instructions.
After learning, your LilyGO or YardStick remains the controller; you do not
need to leave the helper or SDR running.

## What You Need

- Your original fireplace remote.
- An RTL-SDR-compatible USB receiver and antenna, described below.
- A Linux or Mac computer with Python 3.10 or newer. Windows is not supported
  by this helper.
- The [Proflame2 version of rtl_433](https://github.com/jeffgregx2/rtl_433),
  installed on the computer connected to the receiver.
- **`proflame2-learning-id.pyz`**, downloaded from the matching
  [Proflame2 release](https://github.com/jeffgregx2/HACS-Proflame2/releases).

The computer can be different from the one running Home Assistant.
Home Assistant does not install or run the helper for you.

## Choosing an SDR Receiver

An **SDR (Software Defined Radio)** is a radio receiver that plugs into your
computer, usually by USB. An antenna lets it listen to your fireplace remote.
Choose an **RTL-SDR-compatible** receiver that can receive **315 MHz and
433.92 MHz**. A USB Wi-Fi adapter, Bluetooth adapter, LilyGO, or CC1101 module
is not a substitute for this receiver.

One receiver used successfully during Proflame2 development is the
**Nooelec RTL-SDR, FM+DAB, DVB-T USB Stick Set with RTL2832U & R820T**,
model **TV28Tv2**. Nooelec also calls this receiver the **NESDR Mini**
(previously TV28T v2).

- [Amazon product listing](https://www.amazon.com/dp/B008S7AVTC). Search for the
  product name above or Amazon product code **`B008S7AVTC`** to find this exact
  listing, rather than a different Nooelec model.
- [Manufacturer's NESDR Mini page](https://www.nooelec.com/store/nesdr-mini.html).

This receiver includes an antenna and covers both 315 MHz and 433.92 MHz.

If buying a receiver without an antenna kit, also buy an antenna suitable for
your remote's band and the receiver's connector. Follow the manufacturer's
installation instructions, and use a USB adapter if your computer needs one.

Try normal **Learn from remote** first. You do not need to buy an SDR if your
remote can already be learned successfully through your controller.

<a id="choose-the-correct-rtl_433-build"></a>

## Install rtl_433

rtl_433 is the separate program that reads radio messages from the USB
receiver. Despite its name, it works with both 315 MHz and 433 MHz remotes.

Use the [Proflame2 version of rtl_433](https://github.com/jeffgregx2/rtl_433).
It supports both older and newer Proflame2 remotes. Other versions may not
understand newer remotes. The helper checks compatibility automatically when
it starts; you do not need to compare version codes.

Installing this version currently requires building it from source. See
[rtl_433 installation steps](learning_id_advanced_usage.md#installing-the-proflame2-version-of-rtl_433).
This is the most technical part of the setup. If you are not comfortable with
it, ask for help before buying hardware. The helper download does not include
rtl_433 or the receiver's drivers.

<a id="download-and-inspect-the-helper"></a>

## Download the Helper

1. Open the [Proflame2 releases](https://github.com/jeffgregx2/HACS-Proflame2/releases)
   and select the release matching your integration. The helper is available
   starting with **v0.7.0-beta4**.
2. Under **Assets**, download **`proflame2-learning-id.pyz`**. Do not choose
   the **Source code** ZIP instead.
3. You can also download **`proflame2-learning-id.pyz.sha256`** to
   [check the download](learning_id_advanced_usage.md#checking-the-download).

The helper runs directly from that one `.pyz` file. You do not need to install
it or extract it. It does not install packages or save settings on your computer.

If you wish to examine the Python source before running it, see
[Inspecting the helper](learning_id_advanced_usage.md#inspecting-the-helper).
These instructions are optional; they are not part of the learning process.

## Generate Your Learning ID

Plug in the SDR and attach its antenna. Keep the original remote near the
antenna. Close any other program using the SDR.

Open a terminal in the folder where you downloaded
`proflame2-learning-id.pyz`. Check the frequency label on your original remote,
then run the matching command.

**For a 315 MHz remote:**

```sh
python3 proflame2-learning-id.pyz --rf-band 315 --timeout 600
```

**For a 433.92 MHz remote:**

```sh
python3 proflame2-learning-id.pyz --rf-band 433 --timeout 600
```

If you do not know the remote's frequency, check its label or ask for help.
Do not select it based only on your country or the fireplace brand.

Follow the instructions printed in the terminal:

1. Turn the fireplace **OFF** and disable thermostat mode on the remote.
   Press **Return on the computer keyboard** when ready.
2. Answer whether your remote can adjust both Light and Fan. Press Return for
   Yes, or type `n` and press Return for No. The helper adapts if one of these
   controls is unavailable.
3. Press the requested button on the **original remote**, then wait for the
   helper to confirm receipt and show the next instruction. Do not press
   Return after each remote button press. Do not use Home Assistant controls
   during learning.
4. Follow the final **Power OFF** instruction. When finished, the helper prints
   **Your Learning ID is ready**, followed by the ID, and exits.

Take your time following the prompts. After each button press, confirmation
should normally appear within a second or two.
Some remotes switch back to Flame automatically; select the control named in
the instruction before pressing Up or Down. If you press the wrong button,
read the helper's next instruction rather than guessing how to correct it.

The message **A plausible Learning ID has been created** means learning is
still in progress. Continue until **Your Learning ID is ready** appears.
To cancel, type `q` and press Return, or press Ctrl-C.

If the helper says rtl_433 is missing or incompatible, it will ask where the
compatible program is installed. Enter its full path without surrounding
quotes. If you do not know the path, type `q` to cancel and finish the
[rtl_433 installation](#install-rtl_433) before trying again.

## Save and Import Your ID

Copy the complete Learning ID. It starts with `PF2-`. Save it with your
fireplace records and label it with the remote it belongs to. You can reuse it
after reinstalling the integration or replacing the controller. A different
remote needs its own ID, even if it is the same model.

In Home Assistant:

1. Open **Settings > Devices & services > Proflame 2 Fireplace**.
2. Choose **Add entry**, then **Enter a Learning ID**.
3. Paste the complete ID and select the controller dedicated to this fireplace.
4. Choose the fireplace features to enable, then finish setup.

See [Using Learning IDs](add_fireplace_profile.md#Using_learning_ids) for more
setup details. Set the controller to the same radio band as the remote, then
test its controls and, if enabled, Active Listening.

Keep your Learning ID private: it contains the information needed to reproduce
your remote's commands. When finished, you can delete the downloaded helper
and checksum. Keep the saved ID; you do not need to repeat learning just because
you deleted the helper.

## Troubleshooting

- **Python cannot be found:** install Python 3.10 or newer, then reopen the
  terminal and try again.
- **The helper file cannot be found:** open the terminal in the download folder,
  or drag the `.pyz` file into the terminal to obtain its full path.
- **No button press is received:** check the radio band, antenna, and USB
  connection. Close other programs using the SDR and move the remote nearer
  the antenna.
- **The requested setting does not match:** check which control is selected on
  the remote, then follow the helper's next instruction.
- **Home Assistant rejects the ID:** copy the entire value, including its
  `PF2-` prefix, and update the integration if needed.
- **Learning succeeds but the fireplace does not respond:** check the
  controller's radio band and placement. Learning an ID does not prove that
  the fireplace received a later command.

If you need help, report the exact error, your remote model and frequency,
and your software versions. Review logs for personal information before
sharing them. Only share your Learning ID or recordings deliberately.

Saved recordings, source inspection, and developer options are covered in
[Advanced Learning ID usage](learning_id_advanced_usage.md).

## Older JSON-Paste Instructions

Older instructions asked you to paste rtl_433 JSON output into Home Assistant.
That method has been replaced by the Learning ID helper described above.
Home Assistant now accepts the resulting Learning ID, not the JSON output.
