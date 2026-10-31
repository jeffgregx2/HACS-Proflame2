# Advanced Learning ID Usage

For the normal guided flow, use [Using rtl_433 for Learning](Using_rtl_433_for_learning.md).
This reference covers installation, optional download checks, saved captures,
and developer use. You do not need to understand the radio protocol to use
the guided helper.

## Installing the Proflame2 Version of rtl_433

The helper requires the [Proflame2 rtl_433 project](https://github.com/jeffgregx2/rtl_433).
An ordinary package-manager installation of rtl_433 may not support all
Proflame2 remotes. Downloading the helper does not install this program.

First install the receiver's drivers and the tools for building rtl_433.
Follow your receiver manufacturer's instructions for USB access. The following
dependencies match the rtl_433 project's Linux/macOS build guidance.

**Debian or Ubuntu:**

```sh
sudo apt-get install git libtool libusb-1.0-0-dev librtlsdr-dev rtl-sdr build-essential cmake pkg-config
```

**macOS with Homebrew already installed:**

```sh
brew install git rtl-sdr cmake pkg-config
```

Then, in a folder where you keep downloaded source, run:

```sh
git clone https://github.com/jeffgregx2/rtl_433.git proflame2-rtl_433
cd proflame2-rtl_433
cmake -B build -DENABLE_RTLSDR=ON
cmake --build build
```

Use a new folder rather than overwriting an existing checkout. This uses the
project's default source; no branch selection is needed. These commands build
rtl_433 locally without replacing a system-installed copy.

The resulting program is `build/src/rtl_433`. To obtain its full path, while
still in that folder run:

```sh
printf '%s/build/src/rtl_433\n' "$PWD"
```

Copy that path. Enter it when the helper asks for a compatible rtl_433 program,
or supply it using `--rtl433`. For example, replace the placeholder below with
your program's actual full path:

```sh
python3 proflame2-learning-id.pyz --rf-band 315 --timeout 600 --rtl433 /full/path/to/rtl_433
```

The helper checks the program automatically before opening the receiver. You
do not need to compare version strings or examine decoded JSON yourself.
For other Linux distributions, consult the rtl_433 project's build guide using
the source downloaded above, not its examples for cloning the upstream project.

## Checking the Download

Download `proflame2-learning-id.pyz` and `proflame2-learning-id.pyz.sha256`
from the same Proflame2 release. In their download folder, run:

**Linux:**

```sh
sha256sum -c proflame2-learning-id.pyz.sha256
```

**macOS:**

```sh
shasum -a 256 -c proflame2-learning-id.pyz.sha256
```

An `OK` result means the file matches the published checksum. A checksum
detects a damaged or mismatched download; it is not a digital signature or
proof that software is safe.

## Inspecting the Helper

The `.pyz` contains readable Python source, not a compiled program or bundled
Python installation. If you wish to examine it without running its code:

```sh
python3 -m zipfile -l proflame2-learning-id.pyz
python3 -m zipfile -e proflame2-learning-id.pyz proflame2-learning-id-source
```

Read `__main__.py`, `README.md`, and the files in `proflame2_learning_id/` and
`proflame2_protocol/`. `LICENSE` contains the license; `BUILD_INFO.json` within
`proflame2_learning_id/` records version and source information.

Alternatively, use `unzip -l` to list the archive or `unzip` to extract it.
You can run the reviewed extracted source with:

```sh
python3 proflame2-learning-id-source/__main__.py --rf-band 315 --timeout 600
```

The helper's optional `--inspect-pyz-archive` flag prints inspection instructions
and exits. Unlike the ZIP commands above, that flag executes helper code to
print the text. `--help` lists options; `--version` reports the packaged version.

The helper installs no packages, saves no configuration or log files, and does
not create Python bytecode caches. It starts and stops only its own rtl_433
receiver. Delete the downloaded files and any extracted review folder when
finished, keeping your Learning ID separately.

## Saved Captures and Unguided Input

Use these modes only when requested for support or when you need to retain
recordings. The guided flow is easier and confirms completion automatically.

To record decoded commands, run the compatible rtl_433 separately:

```sh
rtl_433 -R 207 -f 315M -F json > remote-capture.json
```

For a 433.92 MHz remote, use `-f 433.92M`. If needed, replace `rtl_433` with
its full path. Use only one native remote, with thermostat mode disabled.
Change several light/fan settings and return to the same combinations so each
can be received again. Intermediate settings also count. Allow the remote
time to repeat each command; this is not necessarily a two-second interval.
If these controls are unavailable, use nonzero flame settings and allow normal
shutdown/startup time between power changes. Avoid flame zero, which can turn
the fireplace off.

Press Ctrl-C to finish recording, then run:

```sh
python3 proflame2-learning-id.pyz remote-capture.json -v
```

Do not merge stderr into the recording with `2>&1`; startup messages are not
JSON. The file is checked in full, and conflicting remote data prevents an ID.
File mode can produce an ID marked **Preliminary** or **Confirmation incomplete**.
Collect more evidence before treating such an ID as confirmed. Home Assistant
can validate the ID's format but cannot establish how it was collected.

For an automated live workflow, `--unguided` disables button prompts but still
requires full confirmation. Live input otherwise expects an interactive
terminal. An external shell pipeline does not give the helper ownership of
rtl_433; stop that receiver separately if it keeps running after learning.
Use `--input-mode file` when piping an existing saved file.

For protocol fields and confirmation requirements, consult
[Learning ID encoding](learning_id_encoding.md) and the
[implementation specification](rtl433_extended_protocol_implementation_plan.md).
Keep Learning IDs and recordings private unless deliberately sharing them for
support, and review all material for identifying information first.

## Developer Source Runner

End users should use the release `.pyz`, not a source checkout. Developers can
run the same helper from the HACS-Proflame2 repository:

```sh
bash tools/learning_id/run.sh --rf-band 315 --timeout 600
```

The runner creates a temporary native Python environment under `/tmp` and
removes it and staged sources on completion, failure, or handled interruption.
It needs venv/pip support and network access for build dependencies. Set
`LEARNING_ID_PYTHON` to select an interpreter. No persistent environment or
build metadata is created in the source checkout. Forced termination or machine
failure cannot guarantee cleanup; save recordings outside temporary storage.

See [Learning ID packaging](learning_id_packaging.md) for development builds,
archive tests, and release publication. Decoder fixtures and implementation
history belong in developer documentation, not the guided end-user procedure.
