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
is `13dfe201a490fb5d3f1ad12ad315ee8b6c5314b8`. The companion helper is in the
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

## Install the Companion Helper

Open a terminal in the helper-enabled HACS-Proflame2 source checkout supplied by
the maintainer. It must contain both `tools/learning_id` and the package metadata
in `pyproject.toml`; these instructions do not apply to an ordinary older checkout.

On Linux or macOS:

```sh
python3 -m venv .venv-learning-id
. .venv-learning-id/bin/activate
python -m pip install .
proflame2-learning-id --help
```

On Windows, use PowerShell from the same source directory:

```powershell
py -3 -m venv .venv-learning-id
.\.venv-learning-id\Scripts\python.exe -m pip install .
.\.venv-learning-id\Scripts\proflame2-learning-id.exe --help
```

For the following commands, Windows users can use the full helper executable
path shown above. Use a terminal that preserves each rtl_433 JSON line when
piping or saving output. If installation or USB-driver setup is unfamiliar,
ask for assistance and include the error text, operating system, and software
version. Do not change USB drivers for unrelated devices.

The live-pipe examples below use a Linux/macOS terminal. On Windows, use the
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

The helper does not ask which features your fireplace supports. The ID contains
only learned remote information. Choose fan, light, front-burner, auxiliary,
and CPI options later in Home Assistant's **Select supported features** step.

## Generate a Learning ID

Put the remote in manual mode with thermostat mode off. Use only one remote
during collection. Leave pilot, auxiliary, and front-burner settings unchanged
unless you need one of them to provide command variation.

For a 315 MHz remote, run:

```sh
rtl_433 -R 207 -f 315M -F json | proflame2-learning-id --timeout 600 -v
```

For a 433.92 MHz remote, change only the tuning option:

```sh
rtl_433 -R 207 -f 433.92M -F json | proflame2-learning-id --timeout 600 -v
```

Place the remote near the SDR antenna and follow the helper's progress messages. It collects radio
output automatically; do not paste individual lines into HA.

### Which Buttons to Press

If your fireplace supports fan and light, this is an example sequence:

1. Turn power on, select flame 6, light 0, and fan 0. Wait for startup to finish.
2. Set Light 1, then Light 2, then Light 3, then Light 4, then Light 5, then Light 6.
3. Set Fan 1, then Fan 2, then Fan 3, then Fan 4, then Fan 5, then Fan 6.
4. Turn power off and back on if the helper still requests variation in the
   power/other command byte. Do not rapidly cycle power; follow your fireplace's
   operating instructions and allow its normal shutdown/startup time.

**Pause after each individual setting change**, not just after each numbered
step. Each counted command must be received at least twice. Let the remote send
its automatic repeat, watching `-v` progress to confirm it counted. Repeat timing
varies between remotes; do not assume two seconds is sufficient. If necessary,
return to a prior setting and then select the target setting again.

If fan or light is unavailable, use several different nonzero flame levels or
another supported control. Avoid flame zero during collection: depending on the
mode and remote, it can shut the fireplace off. On native remotes that revert to
the flame control after inactivity, reselect Light or Fan before adjusting it.

At least seven different commands with matching repeats are needed for full
confirmation, and the commands must provide the variation the helper requests.
Seven commands alone do not guarantee completion. Repeated identical commands
or temperature updates do not count as new commands.

Live collection prints a Learning ID only after full confirmation. With
`--timeout 600`, it stops unsuccessfully after ten minutes if the required
evidence has not arrived. A confirmed ID means the capture checks passed, not
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
proflame2-learning-id remote-capture.json -v
```

File mode validates the entire file and may generate an ID labeled
**Preliminary** or **Confirmation incomplete** when there is less evidence than
full confirmation needs. Collect more commands/repeats before treating those
results as fully confirmed. Conflicting relevant records prevent an ID from
being generated. Explicitly use `--input-mode file` if piping a saved file
through another command; pipes otherwise default to live collection.

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
