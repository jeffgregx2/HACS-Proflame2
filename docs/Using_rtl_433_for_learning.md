# Using rtl_433 for Learning

Use this when normal **Learn from remote** setup fails or a maintainer asks for
additional remote captures. An SDR receiver listens to your native remote;
rtl_433 decodes its transmissions, and the separate `proflame2-learning-id`
helper generates a Learning ID to import into Home Assistant.

This process does not transmit fireplace commands from your computer. Pressing
the native remote can still operate the fireplace. After setup, your LilyGO or
YardStick controls the fireplace; the SDR and helper are not needed for normal use.

## What You Need

- The original native remote.
- A separate RTL-SDR-compatible receiver and a suitable antenna, connected to a
  computer. A LilyGO or YardStick is not a substitute for the SDR in this flow.
- An rtl_433 build that understands your remote's frame format.
- Python 3.10 or newer and the companion `proflame2-learning-id` helper.
- A supported controller dedicated to this fireplace for normal HA operation.

Home Assistant does not install or run rtl_433 or the helper. You run them in a
terminal on the computer connected to the SDR. This can be a different computer
from Home Assistant.

## Choose the Correct rtl_433 Build

**Older rtl_433 builds decode only legacy Proflame2 frames.** Installing the
latest package from an operating-system package manager does not, by itself,
establish support for extended remotes. The helper cannot recover extended
fields that an older decoder never outputs.

The extended-capable development decoder is maintained in the project's
[rtl_433 fork](https://github.com/jeffgregx2/rtl_433), on the
`proflame2-extended` branch. The tested source revision for these instructions
is `13cae578111d92434a6c09212aae0afa0141a191`. This revision generated the
capture-backed JSON fixtures used by the helper's offline interoperability tests;
it is not a claim of completed live-radio acceptance. The companion helper is in the
HACS-Proflame2 `rtl433-enhancements` development branch. Use the helper that
generates remote-only IDs in the [documented format](learning_id_encoding.md).
Rebuild older experimental helper installations if they still ask for features.
These are development sources, not a minimum numbered upstream rtl_433 release
or a published PyPI package.

**Development availability:** the helper is not yet included in the normal
`dev` checkout or the existing stable integration release. If you do not have
the matching source checkout or an installation package supplied by the
maintainer, request help before proceeding. Do not substitute an arbitrary
rtl_433 release or assume `pip install proflame2-learning-id` is available.

Follow the [upstream rtl_433 build instructions](https://github.com/merbanan/rtl_433/blob/master/docs/BUILDING.md)
for your operating system, using the extended-capable source revision above.
For a legacy remote, a compatible legacy decoder can still supply the command
and error fields the helper needs.

Check the executable you will actually run:

```sh
rtl_433 -V
rtl_433 -d help
```

If multiple rtl_433 versions are installed, use the full path to the intended
executable. With an extended remote, its JSON output should identify
`"protocol_variant": "extended_10_word"` and include the complete `raw_words`
field, containing 20 hexadecimal characters. A row containing only legacy
command/error fields is not sufficient evidence for an extended profile.

## Run the Companion Helper Without Keeping an Environment

Open a terminal in the helper-enabled HACS-Proflame2 source checkout supplied by
the maintainer. It must contain both `tools/learning_id` and the package metadata
in `pyproject.toml`; these instructions do not apply to an ordinary older checkout.

On Linux or macOS, use the temporary runner from that checkout:

```sh
HELPER_RUNNER="$PWD/tools/learning_id/run.sh"
bash "$HELPER_RUNNER" --help
```

Each invocation creates a native Python environment and stages the helper's
packaging sources under `/tmp`. It installs and runs the helper, then removes
the environment and staged files on completion, failure, Ctrl-C, or termination.
It does not create a persistent environment or build metadata in your checkout.
Python's venv/pip support and access to install build dependencies are required.
Setup messages go to stderr; stdout remains reserved for the Learning ID.
Forced termination with SIGKILL or a machine failure cannot run cleanup.

Keep captures and saved IDs in a private, persistent directory outside `/tmp`.
The runner preserves your current directory and passes through all helper
arguments. To choose a Python interpreter, set `LEARNING_ID_PYTHON`.

### Optional Persistent Installation

If you explicitly want an installed command instead, create a virtual environment
and run `python -m pip install .` there. On Windows, use PowerShell from the
source directory:

```powershell
py -3 -m venv .venv-learning-id
.\.venv-learning-id\Scripts\python.exe -m pip install .
.\.venv-learning-id\Scripts\proflame2-learning-id.exe --help
```

For saved-file processing, Windows users can replace `bash "$HELPER_RUNNER"`
with the full helper executable path shown above. The managed `--rf-band` launch
option belongs to the Linux/macOS runner, not the standalone helper command.
Use a terminal that preserves each rtl_433 JSON line when
piping or saving output. If installation or USB-driver setup is unfamiliar,
ask for assistance and include the error text, operating system, and software
version. Do not change USB drivers for unrelated devices.

The managed live examples below use a Linux/macOS terminal. On Windows, use the
saved-JSON workflow instead; the helper's live pipe reader is not a Windows
implementation. Record JSON with Command Prompt (`cmd.exe`), rather than older
Windows PowerShell's default file redirection, to avoid changing the text encoding.

## Select the Radio Band

Use the band printed on your native remote:

| Remote band | rtl_433 option | LilyGO YAML substitution |
| --- | --- | --- |
| 315 MHz | `-f 315M` | `proflame2_rf_band: "315"` |
| 433.92 MHz | `-f 433.92M` | `proflame2_rf_band: "433"` |

Extended frames do not imply a particular frequency. Configure the controller's
band separately; a Learning ID does not contain the band.

The ID contains only learned remote information. Guided capture asks which
remote controls are available solely to choose a button sequence; this does not
configure fireplace features or change the ID's encoding. Choose fan, light,
front-burner, auxiliary, and CPI options later in Home Assistant's **Select
supported features** step.

## Generate a Learning ID

Start with the fireplace OFF and the remote's thermostat mode disabled. Use only
one remote during collection and do not use HA controls during the test. The
helper will give you time to prepare before it asks for the first button press.

For a 315 MHz remote, run:

```sh
bash "$HELPER_RUNNER" --rf-band 315 --timeout 600
```

For a 433.92 MHz remote:

```sh
bash "$HELPER_RUNNER" --rf-band 433 --timeout 600
```

This managed mode starts rtl_433, supplies its JSON to the helper, and stops
that receiver when learning finishes, fails, or is cancelled. Use
`--rtl433 /path/to/rtl_433` to choose a particular executable. It stops only
the receiver it started, not other SDR programs or processes.

Before opening the SDR, the runner performs offline decoder checks with known
legacy and extended test frames, including the extended terminal-bit case. It
checks decoded JSON, not just the reported version. If the selected program is
missing or incompatible, it asks for the full path to a compatible rtl_433;
type `q` to cancel. The selected path is not saved. Use `--rtl433` on later runs
to avoid choosing it again. With `--unguided`, an incompatible executable fails
without an interactive path prompt.

Managed reception requires an extended-capable build even for a legacy remote.
Compatible older legacy JSON can still be processed with the saved-file flow.
Shell aliases/functions are not used to start the receiver: if your working
`rtl_433` command is an alias, supply its actual executable path. `type -a rtl_433`
in another terminal can help locate available commands and aliases.

One receiver version line appears before preparation. Other startup messages
are captured separately so they do not interrupt the instructions. On failure,
the runner prints a bounded diagnostic tail; it does not create a persistent
log file. Offline compatibility does not establish SDR access or live reception.

You can still supply JSON through an external pipeline, but then the shell owns
the rtl_433 process. After the helper prints the ID, that receiver can keep
running until another transmission or Ctrl-C; the helper has already finished.
Managed mode avoids this apparent hang and releases the SDR automatically.

Place the remote near the SDR antenna. Live collection is guided by default:

1. Turn the fireplace OFF, disable thermostat mode, and press Return in the
   terminal when ready. There is no preparation countdown.
2. Answer whether your remote adjusts both Light and Fan: Return means Yes;
   `n` means No. Only after No, answer whether Light is available. The helper
   then uses Light and Flame, or Power and Flame if Light is unavailable.
3. Follow the **Power ON** prompt. Then follow each individual button prompt,
   such as **Select Light; press DOWN once; the remote should show 2 bars**. Reselect the named control
   each time because some native remotes automatically return to Flame.
4. Wait for **Received: Light 2** and the next instruction. Do not press Return
   after remote button presses: receipt is confirmed from the decoded RF data.
   The helper chooses the next setting and repeats combinations automatically.
5. When confirmation passes, follow the final Power OFF prompt. The helper
   confirms that received command and prints your Learning ID.

The first plausible-ID announcement is not completion. A wrong control or level
does not advance the requested action; read the corrected instruction. If the
remote has moved past the requested value, the helper tells you which direction
to press next. Type `q` and Return, or press Ctrl-C, to cancel without an ID.

Light/Fan and Light/Flame sequences group changes by control, sweeping levels
1 through 3 and back to 1 before switching modes. The helper stops the sequence
as soon as the evidence checks pass, avoiding unnecessary presses. The Power/Flame
fallback never requests flame zero and pauses for your confirmation before
another power-on cycle; follow the fireplace's normal shutdown/startup guidance.
With `--timeout 600`, each requested action has ten minutes for receipt. Terminal
preparation and shutdown pauses have no countdown. These are received remote
commands, not acknowledgements from the fireplace.

Live guidance needs an interactive Linux/macOS terminal separate from the JSON
pipe. For automation, add `--unguided`; that mode retains a total collection
timeout and requires fully confirmed evidence before output. Saved-file input
does not prompt for button presses.

### Button Sequence for Saved Files or Unguided Collection

If your fireplace supports fan and light, this is an example sequence:

1. Turn power on, select flame 6, light 0, and fan 0. Wait for startup to finish.
2. Set Light 1, then Light 2, then Light 3, then Light 4, then Light 5, then Light 6.
3. Set Fan 1, then Fan 2, then Fan 3, then Fan 4, then Fan 5, then Fan 6.
4. Turn power off and back on if the helper still requests variation in the
   power/other command byte. Do not rapidly cycle power; follow your fireplace's
   operating instructions and allow its normal shutdown/startup time.

**Pause after each individual setting change**, not just after each numbered
step. Each initial derivation command must be received at least twice. Let the remote send
its automatic repeat, watching `-v` progress to confirm it counted. Repeat timing
varies between remotes; do not assume two seconds is sufficient. If necessary,
return to a prior setting and then select the target setting again.

If fan or light is unavailable, use several different nonzero flame levels or
another supported control. Avoid flame zero during collection: depending on the
mode and remote, it can shut the fireplace off. On native remotes that revert to
the flame control after inactivity, reselect Light or Fan before adjusting it.

Full confirmation needs at least three different commands received twice with
the required variation, followed by validation against two other combinations.
Those two checks need only one observation each and cannot reuse commands used
in derivation. Previously received combinations outside the derivation set can
also supply these independent checks. Repeated identical commands or temperature
updates do not count as new commands.

Live collection prints a Learning ID only after full confirmation. Unguided
collection with `--timeout 600` stops unsuccessfully after ten minutes if the
required evidence has not arrived. A confirmed ID means the capture checks passed, not
that control of a physical fireplace has been tested.

## Use a Saved JSON Capture Instead

To retain a capture for later analysis, run rtl_433 separately:

```sh
rtl_433 -R 207 -f 315M -F json > remote-capture.json
```

Perform the same button sequence, then press Ctrl-C to stop recording. For a
433.92 MHz remote, substitute `-f 433.92M`. Keep stderr separate: do not add
`2>&1`, because startup/debug text is not JSON. Then run:

```sh
bash "$HELPER_RUNNER" remote-capture.json -v
```

File mode validates the entire file and may generate an ID labeled
**Preliminary** or **Confirmation incomplete** when there is less evidence than
full confirmation needs. Collect more commands/repeats before treating those
results as fully confirmed. Conflicting relevant records prevent an ID from
being generated. Explicitly use `--input-mode file` if piping a saved file
through another command; pipes otherwise default to live collection.

The confirmation level is reported alongside the ID, not encoded into it.
Home Assistant checks the ID's format and checksum but cannot establish how
many observations were collected. A successful import is not a substitute for
the helper's full confirmation or a test with your fireplace.

## Save and Import the ID

Save the complete ID and label it with the native remote used to generate it.
It continues to describe that specific remote even if you remove its HA entry,
reinstall the integration, or replace the controller. It is not interchangeable
with IDs for other remotes of the same model. A replacement remote requires its
own ID. Feature choices are not included in the ID; select them during HA setup.

In Home Assistant, open Proflame2, choose **Add entry**, then **Enter a Learning
ID**. Paste the ID, choose the controller dedicated to this fireplace, and select
which features to enable on the next setup screen. See
[Using Learning IDs](add_fireplace_profile.md#Using_learning_ids) for import
details. Test the available controls and native-remote listening afterward.

Treat the ID and capture files as sensitive: they identify your specific remote
and contain information needed to reproduce its commands. Keep them private
unless sharing them deliberately for support.

## Troubleshooting

- **No decoded output:** check SDR access, the selected frequency, antenna, and
  that another program is not already using the SDR. For extended remotes,
  verify that the correct extended-capable executable is running.
- **The helper asks for more commands:** vary actual settings; temperature-only
  updates and repeated copies of one command do not provide enough variation.
- **The helper asks for repeats:** wait for retransmission or return to a prior
  setting and select the target again. Do not simply press Up at the maximum.
- **Conflicting or invalid records:** use one remote, keep thermostat off, and
  retain the original JSON for investigation. Do not edit bytes to force an ID.
- **HA rejects the ID:** copy the entire value, including the `PF2` prefix, and
  check that the integration supports the format. Do not paste the JSON itself.
- **HA accepts the ID but the fireplace does not respond:** check controller
  band and placement. Local TX success is not a fireplace acknowledgement.

When requesting help, include software versions, remote model, frequency band,
and the exact error. Review logs and recordings for personal information before
sharing them. Share Learning IDs and raw captures only deliberately.
