# rtl_433-Assisted Manual Learning

Most users should use normal `Learn from remote` setup with their selected
controller. Use rtl_433-assisted manual learning only when normal learning does
not decode your original remote, or when a maintainer asks you to collect
rtl_433 evidence.

This mode does not use rtl_433 to control the fireplace. It uses rtl_433 only
to read your original remote during setup. After setup, the fireplace is still
controlled by your selected Proflame2 controller, either LilyGO or YardStick.

## What You Need

For an independent Learning ID workflow instead of pasting each command into
Home Assistant, see [Standalone Learning ID Tool](#standalone-learning-id-tool).

- A supported Proflame2 controller:
  - LilyGO T-Embed CC1101, already flashed and added to Home Assistant through
    ESPHome, or
  - YardStick One, connected to the Home Assistant host by USB.
- The original Proflame2 remote for the fireplace.
- A separate SDR receiver that works with rtl_433.
- The `rtl_433` command-line program installed on a computer where the SDR is
  connected.

The easiest SDR choice is usually an RTL-SDR compatible USB dongle. A known
working, inexpensive example is the
[RTL-SDR compatible USB dongle](https://www.amazon.com/dp/B008S7AVTC).
rtl_433 also supports other SDR inputs through SoapySDR, but those setups are
more advanced.

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

Follow the prompt shown by Home Assistant. The prompts will have you power on
the fireplace and then select `Temp Down` and `Temp Up`, possibly more than
once. These commands are chosen because temperature changes are low-impact and
avoid repeatedly turning the fireplace on and off.

When Home Assistant has enough valid evidence, it will show a confirmation
screen and continue to feature selection.

## What To Paste

After each prompted button press, look at the rtl_433 terminal. Paste the newest
decoded Proflame2 JSON line that appears after that button press into the Home
Assistant text box.

The line usually looks like this:

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

Do not paste unrelated output from other devices. Also ignore duplicate
Proflame2 lines from earlier button presses. Some fireplaces or remotes may
repeat the previous command after a delay. In testing, that delayed repeat
appeared about 5 seconds later, and it can appear near the time you are
preparing for the next prompt.

If rtl_433 prints multiple lines, use the newest Proflame2 JSON line that:

- appeared after the button press Home Assistant requested,
- contains `id`, `cmd1`, `cmd2`, `err1`, and `err2`,
- matches the button you just pressed when the JSON fields make that obvious,
  such as `power` or `flame`.

## Finishing Setup

Home Assistant accepts each valid pasted row and keeps prompting until it can
derive a stable remote profile. When enough evidence is collected:

1. Home Assistant confirms that it learned the remote.
2. Select `Submit`.
3. Choose the fireplace features your installation supports.
4. Validate basic controls from Home Assistant.

The saved profile is the same kind of profile produced by normal learning. You
do not need to keep the SDR or rtl_433 running after setup.

## Troubleshooting

If Home Assistant says the paste is invalid:

- Confirm the pasted line includes `id`, `cmd1`, `cmd2`, `err1`, and `err2`.
- Confirm rtl_433 was run with `-R 207` and `-F json`.
- Press the prompted button again and paste the new decoded line.

If Home Assistant says the remote IDs do not match:

- Make sure only one Proflame2 remote is being used during setup.
- Do not paste lines from a neighbor's fireplace or another remote.

Each time you press a button on the fireplace remote, rtl_433 should output a
line. If it does not:

- Verify the remote did turn on the fireplace.
- Verify the `Temp Down` and `Temp Up` buttons are changing the fireplace.
- Ensure the fireplace remote is less than 3 feet away, or about 1 meter, from
  the SDR antenna.
- Confirm the SDR is visible with `rtl_433 -d help`.
- Confirm no other program is using the SDR.

If rtl_433 decodes the remote but Home Assistant cannot learn the remote, save
the rtl_433 output and open a GitHub issue with the pasted rows and your remote
model number.

If rtl_433 decodes the remote and Home Assistant learns the remote but the
fireplace still does not respond to Home Assistant commands, run this raw
capture command while pressing the same remote buttons again:

```bash
rtl_433 -f 315M -R 207 -M level -F json -S all
```

Attach the generated `.cu8` files to the GitHub issue along with the JSON lines
from rtl_433 and your remote model number.

## Standalone Learning ID Tool

The development helper can create a Learning ID from saved rtl_433 JSON or a
live pipe. It does not require Home Assistant or rtl_433 to be installed when
reading a saved file. It never transmits, starts rtl_433, or changes HA settings.

Install from this source checkout in a Python 3.10 or newer virtual environment:

```bash
python3 -m venv .venv-learning-id
. .venv-learning-id/bin/activate
python -m pip install .
proflame2-learning-id --help
```

These commands describe the source version containing the helper, not a PyPI
release. Extended ten-word input requires a version of rtl_433 with extended
Proflame2 support. The baseline decoder reviewed for this implementation accepts
legacy seven-word packets only; installing a distribution package alone does
not establish extended support. The matching decoder adds explicit
`protocol_variant`, complete `raw_words`, and extended W6-W10 fields.
Extended output reports `Pilot: -1` (`"pilot": -1` in JSON), meaning that the
pilot setting is unknown. This is not an inferred on/off value. Once the
extended pilot mapping is validated, the decoder and helper can be updated to
use `0/1`. Earlier extended JSON records that omit this field remain accepted.

The helper collects remote information only. It does not ask which fireplace
features to enable, and feature choices are not encoded in the Learning ID.
Select those in HA during import. See [Using rtl_433 for learning](Using_rtl_433_for_learning.md)
for the complete standalone workflow.

For live collection:

```bash
rtl_433 -R 207 -f 315M -F json | proflame2-learning-id -v
```

Use your fireplace remote to change several settings, such as Light Up or Down
and Fan Up or Down. Pause after each change to give the remote time to send it
again. If a change still needs confirmation, make that same change again.

The helper counts different commands separately from repeated copies. Each
counted command must be received at least twice. It derives a profile after at
least three different commands, checks two additional different commands, then
checks two more: at least seven commands in total for full confirmation. Legacy
commands must vary both command bytes; extended evidence must include different
manual-mode W5 commands. `-v` explains missing evidence. Repeated identical
commands and temperature changes alone cannot advance the different-command
count. Confirmation means the capture checks passed; it does not establish
successful operation with a physical fireplace.

Live collection normally finishes only after full confirmation. The default
time limit is 120 seconds; use `--timeout SECONDS` to select a limit up to 3600
seconds. If input ends, times out, or exceeds a safety limit before confirmation,
no ID is printed and the command exits unsuccessfully.

For saved newline-delimited JSON:

```bash
proflame2-learning-id capture.json
proflame2-learning-id < capture.json
cat capture.json | proflame2-learning-id --input-mode file
```

Named files and regular-file input redirection default to file mode. Pipes
default to live mode because the helper cannot tell a saved file piped through
`cat` from a running rtl_433 process. Use `--input-mode file|live` to override.

File mode checks the entire file before printing anything on stdout:

- **Preliminary:** At least three different commands agree, each received at
  least twice, and the profile can be generated. More different commands are
  needed to finish confirmation.
- **Confirmation incomplete:** At least five different commands agree, each
  received at least twice. Additional checks passed, but full confirmation
  requires at least seven different commands with matching repeats.
- **Confirmed:** At least seven different commands agree, each received at least
  twice, and no conflicting commands were found anywhere in the file.

The final explanation always appears on stderr, even without `-v`. These labels
describe evidence checks, not numerical probabilities. File mode may succeed
with a preliminary or incompletely confirmed ID; below the first gate, no ID
can be generated. Any contradictory or invalid relevant record prevents output,
including a conflict after seven otherwise confirmed commands. The helper does
not silently choose a different profile or ignore conflicting fireplace data.
Clearly unrelated device records are ignored.

Successful stdout contains exactly one Learning ID. Import it through the
integration's existing Learning ID setup option, then validate your fireplace's
controls. Installation options such as controller, RF frequency, and refresh
period are not part of the ID; import keeps refresh disabled by default.
The helper does not save raw input by default or include captured records in
error messages. Input is limited to 16 KiB per line, 16 MiB total, and 100,000
lines. The confirmation count is bounded to 4,096 different commands.
