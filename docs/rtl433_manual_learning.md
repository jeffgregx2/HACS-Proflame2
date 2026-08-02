# rtl_433-Assisted Manual Learning

Most users should use normal `Learn from remote` setup with their selected
controller. Use rtl_433-assisted manual learning only when normal learning does
not decode your original remote, or when a maintainer asks you to collect
rtl_433 evidence.

This mode does not use rtl_433 to control the fireplace. It uses rtl_433 only
to read your original remote during setup. After setup, the fireplace is still
controlled by your selected Proflame2 controller, either LilyGO or YardStick.

## What You Need

- A supported Proflame2 controller:
  - LilyGO T-Embed CC1101, already flashed and added to Home Assistant through
    ESPHome, or
  - YardStick One, connected to the Home Assistant host by USB.
- The original Proflame2 remote for the fireplace.
- A separate SDR receiver that works with rtl_433.
- The `rtl_433` command-line program installed on a computer where the SDR is
  connected.

The easiest SDR choice is usually an RTL-SDR compatible USB dongle. rtl_433 also
supports other SDR inputs through SoapySDR, but those setups are more advanced.

Home Assistant does not install, run, or manage rtl_433 for this learning mode.
You run rtl_433 yourself in a terminal and paste its decoded Proflame2 output
into the Home Assistant setup form.

## Install rtl_433

Follow the rtl_433 project installation instructions for your operating system:
https://github.com/merbanan/rtl_433

### Linux / Ubuntu

On Debian or Ubuntu, install the packaged command-line tool:

```bash
sudo apt install rtl-433
```

On other Linux distributions, install `rtl_433` from your distribution's
package manager when available.

### macOS

macOS does not include Homebrew or rtl_433 by default. If Homebrew is not
installed yet, install Homebrew first:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Then install rtl_433:

```bash
brew install rtl_433
```

### Windows

rtl_433 can run on Windows. The simplest path is to use the Windows ZIP file
from the rtl_433 GitHub releases page:
https://github.com/merbanan/rtl_433/releases

1. Download the latest Windows ZIP asset. For most modern Windows systems, use
   the `rtl_433-win-msvc-x64-...zip` file.
2. Extract the ZIP file to a folder such as `C:\rtl_433`.
3. Open Command Prompt or PowerShell in that folder.
4. Run `rtl_433.exe -V`.

For RTL-SDR USB dongles, Windows may also need the WinUSB driver installed for
the SDR. The usual tool for this is Zadig:
https://zadig.akeo.ie/

When using Zadig, select the RTL-SDR device interface, commonly shown as
`Bulk-In, Interface (Interface 0)`, and install the `WinUSB` driver. Do not
change drivers for unrelated USB devices.

Windows users can also build rtl_433 from source, but that is more advanced.
The upstream build notes describe Visual Studio and MinGW options:
https://github.com/merbanan/rtl_433/blob/master/docs/BUILDING.md

Confirm rtl_433 starts and can see your SDR:

```bash
rtl_433 -V
rtl_433 -d help
```

If rtl_433 cannot see the SDR, fix that before starting Proflame2 setup. Common
causes are USB passthrough problems, missing SDR drivers, or another program
already using the SDR.

## Start rtl_433

Connect the SDR to the computer running rtl_433. Place the original fireplace
remote within a few feet of the SDR antenna.

Run this command:

```bash
rtl_433 -f 315M -R 207 -M level -F json
```

If a maintainer asks you to test the Proflame reference frequency exactly, use:

```bash
rtl_433 -f 314.973M -R 207 -M level -F json
```

Leave this terminal open while you complete Home Assistant setup.

## Start Manual Learning In Home Assistant

1. Open Settings -> Devices & services -> Proflame 2 Fireplace.
2. Select `Add entry`.
3. Select `Learn from rtl_433 output`.
4. Enter the fireplace name.
5. Enter a fireplace short name.
6. Select the controller that will control the fireplace after setup:
   - `LilyGO T-Embed CC1101`, or
   - `YARD Stick One USB Controller`.
7. Select `Submit`.
8. For LilyGO, select the matching ESPHome device when prompted.

After this, Home Assistant will ask for one remote button press at a time.

## What To Press

Follow the prompt shown by Home Assistant. The prompts normally use `Temp Down`
and `Temp Up` first because those are low-impact controls and avoid repeatedly
turning the fireplace on and off.

When Home Assistant has enough valid evidence, it will ask you to press `Power`
once. That final power press is only to leave the fireplace off before setup
continues.

## What To Paste

After each prompted button press, look at the rtl_433 terminal. Paste the
decoded Proflame2 line for that press into the Home Assistant text box.

With `-F json`, the line usually looks like this:

```json
{"time":"2026-08-02 10:15:22","model":"Proflame2-Remote","id":"3b3f02","cmd1":"01","cmd2":"16","err1":"76","err2":"ef"}
```

Paste the whole JSON line. It is okay if the line includes additional fields.
The integration uses these values:

- `id`
- `cmd1`
- `cmd2`
- `err1`
- `err2`

The paste box can also accept copied key/value text if your rtl_433 output is
not JSON, for example:

```text
model=Proflame2-Remote id=3b3f02 cmd1=01 cmd2=16 err1=76 err2=ef
```

Do not paste unrelated output from other devices. If rtl_433 prints multiple
lines for one button press, paste the Proflame2 line that contains `id`,
`cmd1`, `cmd2`, `err1`, and `err2`.

## Finishing Setup

Home Assistant accepts each valid pasted row and keeps prompting until it can
derive a stable remote profile. When enough evidence is collected:

1. Home Assistant asks you to press `Power` once.
2. Select `Submit`.
3. Choose the fireplace features your installation supports.
4. Validate basic controls from Home Assistant.

The saved profile is the same kind of profile produced by normal learning. You
do not need to keep the SDR or rtl_433 running after setup.

## Troubleshooting

If Home Assistant says the paste is invalid:

- Confirm the pasted line includes `id`, `cmd1`, `cmd2`, `err1`, and `err2`.
- Confirm rtl_433 was run with `-R 207`.
- Press the prompted button again and paste the new decoded line.

If Home Assistant says the remote IDs do not match:

- Make sure only one Proflame2 remote is being used during setup.
- Do not paste lines from a neighbor's fireplace or another remote.

If rtl_433 does not print anything:

- Move the original remote closer to the SDR antenna.
- Try `rtl_433 -f 314.973M -R 207 -M level -F json`.
- Confirm the SDR is visible with `rtl_433 -d help`.
- Confirm no other program is using the SDR.

If rtl_433 decodes the remote but Home Assistant cannot derive a profile, save
the rtl_433 output and open a GitHub issue with the pasted rows and your remote
model number.
