# SDR Learning Receiver Design

This document defines a native SDR learning-only receive path for Proflame2
guided learning. It is intended to support cases such as GitHub issue #11,
where YardStick guided learning receives RF bytes but cannot decode a remote
that may be easier to acquire through an SDR pulse-based receive chain.

The SDR path is not a replacement for YardStick or LilyGO. It is a way to learn
the remote profile evidence needed by the existing runtime controllers.

Important project requirement: production code must not ship or depend on
third-party decoder images or binaries such as rtl_433. rtl_433 may be used as
a development reference and validation oracle, but the integration must own the
Proflame2 SDR acquisition and decode path that ships to users.

## Goals

- Allow Home Assistant guided learning to use one device for runtime control
  and another device for learning.
- Add a manual learning path where the user can enter values collected outside
  Home Assistant, including from rtl_433.
- Add an optional SDR learning receiver that decodes Proflame2 packets with a
  native rtl_433-style OOK pulse pipeline.
- Keep YardStick TX, YardStick learning, LilyGO RX, and LilyGO TX unchanged by
  default.
- Run CPU-heavy SDR capture only while guided learning or explicit diagnostics
  are active.
- Provide debug controls for high-level summaries, pulse analysis, symbol
  windows, decoded frames, and raw IQ captures without code changes.
- Avoid mandatory SDR dependencies for ordinary HACS users who do not enable
  SDR learning.

## Non-Goals

- Do not make SDR a runtime transmitter. RTL-SDR is receive-only.
- Do not replace the selected runtime controller backend.
- Do not require LilyGO users to configure a separate learning receiver.
- Do not run SDR capture continuously by default.
- Do not use rtl_433 as a production subprocess.
- Do not copy rtl_433 source code into this project.
- Do not require rtl_433 for ordinary guided learning or runtime operation.
- Do not require GitHub or package-network access at runtime.

## Home Assistant Architecture

Today the integration treats the configured backend as both the runtime
controller and the guided-learning receiver. That remains the default.

The proposed model separates these concepts:

- Controller: the device used after setup to control the fireplace.
- Learning receiver: the device used during guided learning to observe remote
  packets and derive the remote profile.

The existing `backend_type` remains the controller selection:

- `yardstick`
- `lilygo_cc1101`
- `fake` in development builds only

Add a learning receiver selection that defaults to the selected controller:

- `controller`: use the selected controller's normal learning path.
- `manual`: skip RF capture and let the user enter decoded Proflame2 values.
- `sdr`: use the native SDR learning-only receiver.

If the learning receiver is omitted, use `controller`. This preserves current
behavior and avoids additional configuration for LilyGO users.

### Config Entry Outcome

The created config entry should still store the runtime controller in
`backend_type`. If SDR was used for learning, the entry is still a YardStick or
LilyGO entry at runtime.

Learning-source metadata may be stored for diagnostics:

- `learned_with_receiver: sdr`
- `learned_with_frequency_hz`
- `learned_with_sample_rate`
- `learned_with_sdr_source`
- `learned_with_decoder_version`

Do not make runtime control depend on the SDR unless a future receive-only
feature explicitly enables it.

### User-Facing Flow

Default path:

1. User selects YardStick or LilyGO as the controller.
2. Learning receiver defaults to that controller.
3. Existing guided learning behavior runs.

SDR learning path:

1. User selects YardStick as the controller.
2. User selects SDR as the learning receiver.
3. Guided learning starts the SDR receiver only for the learning session.
4. The SDR receiver emits decoded Proflame2 packets to the existing learning
   algorithm.
5. The resulting profile is saved as a normal YardStick-controlled fireplace.

This is especially useful when YardStick TX works but YardStick RX acquisition
does not decode a remote.

Manual learning path:

1. User selects YardStick or LilyGO as the controller.
2. User selects manual learning.
3. Home Assistant shows the required values and expected formats.
4. User runs an external capture tool, such as rtl_433, outside this integration.
5. User enters the decoded values.
6. The integration validates the values with the existing Proflame2 profile and
   frame rules.
7. The resulting profile is saved as a normal controller-backed fireplace.

This gives issue reporters a low-friction beta path without shipping rtl_433,
SDR drivers, native bindings, or a new pulse decoder in the integration.

## SDR Interface Analysis

There are two separate problems:

- SDR sample acquisition: programming the radio and obtaining IQ samples.
- Proflame2 decoding: converting IQ samples to OOK pulses, symbols, frames, and
  learned ECC profile evidence.

The project must own the second problem. The first problem may use optional SDR
access mechanisms because Home Assistant/HACS cannot practically ship USB radio
drivers inside the integration.

### Source A: `rtl_sdr` IQ Capture Helper

Use the `rtl_sdr` command-line utility to tune an RTL-SDR and stream unsigned
8-bit complex IQ samples to stdout or a bounded capture file. The integration
owns demodulation and Proflame2 decoding.

Example command shape:

```text
rtl_sdr
  -d <device>
  -f <frequency_hz>
  -s <sample_rate>
  -g <gain>
  -p <ppm>
  -n <sample_count>
  -
```

Local command surface verified on this host:

- `rtl_sdr -f <hz>` tunes frequency.
- `rtl_sdr -s <rate>` sets sample rate.
- `rtl_sdr -d <index-or-serial>` selects device.
- `rtl_sdr -g <gain>` sets gain, with `0` meaning auto in this local help.
- `rtl_sdr -p <ppm>` applies frequency correction.
- `rtl_sdr -n <sample_count>` bounds capture length.
- `rtl_sdr -` writes IQ samples to stdout.

Advantages:

- Avoids rtl_433 runtime dependency.
- Keeps the Proflame2 decode implementation in this project.
- Uses a small, common RTL-SDR acquisition tool that many users already have.
- Provides a direct path to raw IQ artifacts for issue debugging.

Disadvantages:

- Still requires users who enable SDR learning to install RTL-SDR tooling.
- `rtl_sdr` startup per capture has overhead.
- Continuous session streaming from stdout must be carefully managed.
- Device support is limited to RTL-SDR class devices.

Recommendation: keep this as a fallback or diagnostic source. Prefer Source B
for the live SDR path if optional dependency packaging is solved cleanly.

### Source B: Python RTL-SDR Binding

Use an optional Python wrapper around librtlsdr to program the device and read
IQ samples directly.

Validated status as of 2026-07-30:

- This project is licensed as GNU GPL v3.0. GitHub reports the repository as
  GPL-3.0, and the local `LICENSE` file is the GNU GPL version 3 text.
- `pyrtlsdr` is published as GPLv3 and wraps `librtlsdr`.
- The upstream Osmocom `rtl-sdr` repository is GPL licensed and GitHub reports
  it as GPL-2.0. Before bundling any binary, confirm whether the exact
  `librtlsdr` artifact is GPL-2.0-only or GPL-2.0-or-later, because GPLv2-only
  and GPLv3 are not generally treated as interchangeable.
- `pyrtlsdrlib` is a helper package that distributes prebuilt `librtlsdr`
  binaries for common platforms, including Linux x86_64 and aarch64.

Advantages:

- Cleaner lifecycle control than spawning `rtl_sdr`.
- Can stream samples continuously across learning prompts.
- Avoids subprocess buffering and process cleanup complexity.
- Fits the project's GPLv3 licensing better than originally assumed if the
  Python dependency is `pyrtlsdr` under GPLv3.
- Gives us direct control over sample windowing, cancellation, device reset, and
  diagnostics inside the learning session.

Disadvantages:

- Adds optional native-library dependency.
- Home Assistant OS/container users may need manual host package installation.
- Packaging and support burden is higher than a subprocess helper.
- Directly declaring `pyrtlsdr` or `pyrtlsdr[lib]` in `manifest.json`
  `requirements` would make every HACS install attempt to install SDR support,
  including users with no SDR and systems where native USB access cannot work.
- Bundled binary support needs a platform policy for x86_64, aarch64, armv7, and
  Home Assistant OS/container environments.
- The `pyrtlsdrlib` package is convenient, but it shifts trust and update
  cadence to a third-party binary distribution. Treat it as optional until we
  are comfortable with its provenance, supported platforms, and license notices.

Recommendation: Source B is the preferred live SDR architecture if packaging is
kept optional. Do not put SDR dependencies in the normal HACS manifest. Implement
manual rtl_433-assisted learning first, then implement the native SDR code
against an internal source interface that can use one of the dependency models
below.

#### Source B Dependency Models

Model 1: one GPLv3 HACS package, optional user-installed dependency.

- Keep the normal HACS integration exactly one package.
- Do not list `pyrtlsdr` or `pyrtlsdrlib` in `manifest.json`.
- Add YAML/config detection for `sdr_source: pyrtlsdr`.
- Import `rtlsdr` lazily only when SDR learning starts.
- If the import fails, show a clear setup error with the exact Python package and
  system library requirements.
- User installs the dependency in their HA environment if their platform allows
  it.

This avoids forcing SDR dependencies on ordinary users. It is the safest first
Source B implementation for HACS, but support burden is higher because users may
need to install OS libraries or Python packages manually.

Model 2: two HACS repositories or release channels.

- `HACS-Proflame2`: normal controller integration with no SDR Python
  dependency.
- `HACS-Proflame2-SDR`: same integration plus Source B requirements and SDR
  docs.
- Both stay GPLv3.
- The SDR repository can pin `pyrtlsdr` and optionally `pyrtlsdrlib`.
- The normal repository remains easy to install and has no native dependency
  risk.

This matches the "two kits" idea. It is operationally clear for users, but it
creates maintenance overhead: every integration change must either be merged
between repositories or generated from one source tree into two release outputs.
If we choose this model, prefer one source branch and two release workflows
rather than manually maintaining two diverging codebases.

Model 3: one repository with generated release artifacts.

- Keep one source tree.
- Generate a standard HACS release artifact without SDR dependencies.
- Generate an SDR beta artifact with `manifest.json` requirements amended to
  include `pyrtlsdr` and possibly `pyrtlsdrlib`.
- Publish the SDR artifact only as a beta/manual install channel.

This reduces source divergence but may not fit normal HACS installation as well
as separate repositories. It is useful for controlled issue #11 beta testing.

Model 4: separate GPL helper process.

- Ship or document a standalone GPL-compatible helper package that owns
  `pyrtlsdr/librtlsdr` imports.
- Home Assistant talks to it over a narrow local protocol that streams bounded
  IQ buffers or already-sliced OOK pulse rows.
- The main integration can keep SDR dependencies out of its HA process.

This is clean architecturally and can isolate CPU/USB crashes, but it is larger
than needed for the first beta and adds install/service management.

Preferred path:

1. Manual rtl_433-assisted learning in the main integration.
2. Replay-file SDR decoder in the main integration with no native dependency.
3. Source B live receiver behind lazy optional imports.
4. Decide between user-installed dependency, SDR beta artifact, or separate SDR
   repository after local hardware validation proves the native decoder is worth
   carrying.

### Source C: Replay File Source

Read saved IQ files, especially unsigned 8-bit complex files, from disk.

Advantages:

- Essential for repeatable tests.
- Allows issue reporters to provide evidence without the exact hardware being
  present locally.
- Enables deterministic decoder development.

Recommendation: implement early. Replay fixtures are the safest way to evolve
the native decoder.

### Source D: rtl_433 Development Oracle

Use rtl_433 only outside production runtime:

- compare local captures against known-good rtl_433 behavior,
- ask issue reporters whether rtl_433 decodes a remote,
- generate reference decode logs,
- cross-check our native pulse decoder.

Do not make rtl_433 part of the shipped learning receiver. Do not require it in
Home Assistant.

### Source E: Manual rtl_433-Assisted Entry

Manual entry is the lowest-risk beta path for issue #11 because it does not add
runtime dependencies, native SDR libraries, subprocess capture, or a new pulse
decoder. The integration only provides a place to enter validated Proflame2
values; the user is responsible for running rtl_433 or another external decoder.

The existing manual profile path already accepts:

- `remote_id`
- `c1`
- `d1`
- `c2`
- `d2`

rtl_433's local Proflame2 decoder emits:

- `id`
- `cmd1`
- `cmd2`
- `err1`
- `err2`

Therefore the manual-learning enhancement should support two input modes:

- Direct profile mode: user enters `remote_id`, `c1`, `d1`, `c2`, and `d2`.
- Sample-derived mode: user enters one or more rtl_433 rows containing `id`,
  `cmd1`, `err1`, `cmd2`, and `err2`; the integration derives `c1/d1/c2/d2`
  using the existing ECC derivation helpers.

Sample-derived mode is friendlier for issue reporters because rtl_433 does not
require them to understand C/D profile constants. It also preserves validation:
all rows must share one remote id, and command/error pairs must converge to one
stable C/D profile for both command bytes.

Useful reporter commands:

```text
rtl_433 -f 315M -R 207 -M level -F json
```

Ask reporters to press Power once so the first capture turns the fireplace on,
then use Temp Down and Temp Up for additional captures so the command/error rows
are less ambiguous without repeated fireplace power cycling. Once enough
evidence has been accepted, show a confirmation screen and continue to feature
selection without asking for another power press.

Warn users that rtl_433 may emit delayed duplicate rows for a previous remote
press. In testing, the delayed repeat appeared about 5 seconds later. For each
prompt, users should paste the newest JSON row that appears after the requested
button press and ignore older repeated rows.

## Manual Learning Design

Manual learning is a supported profile creation path, not a runtime backend. It
does not receive RF, transmit RF, launch rtl_433, import rtl_433 code, or require
SDR hardware. It converts user-provided decoded evidence into the same permanent
profile values stored by the existing direct manual setup.

### Manual Learning Modes

Mode 1: direct profile entry.

- Existing behavior.
- User enters `remote_id`, `c1`, `d1`, `c2`, and `d2`.
- Best for maintainers or users who already know their learned profile.
- Keep this unchanged for backward compatibility.

Mode 2: guided rtl_433 sample-derived entry.

- New behavior.
- User starts rtl_433 externally.
- Home Assistant prompts for one remote action at a time: Power On first, then
  Temp Down and Temp Up.
- User pastes decoded rtl_433 Proflame2 rows for that prompted action.
- Integration derives `remote_id`, `c1`, `d1`, `c2`, and `d2`.
- Best for issue reporters because rtl_433 reports command/error bytes directly.
- After profile derivation succeeds, Home Assistant confirms that the remote was
  learned, then proceeds to feature selection.

Both modes then create an ordinary YardStick or LilyGO config entry.

### Required rtl_433 Evidence

Each sample row must describe one decoded Proflame2 frame:

- `id`: 24-bit remote id.
- `cmd1`: first command byte.
- `cmd2`: second command byte.
- `err1`: validation byte paired with `cmd1`.
- `err2`: validation byte paired with `cmd2`.

Accepted input formats should include:

- one JSON object per line from `rtl_433 -F json`,
- copied text containing `id=... cmd1=... cmd2=... err1=... err2=...`,
- uppercase or lowercase field names,
- values with or without `0x` prefixes.

String values should be parsed as hexadecimal by default because rtl_433 formats
these fields as hex-like protocol bytes. Numeric JSON values may be accepted as
already-decoded integers.

Example accepted rows:

```text
{"model":"Proflame2-Remote","id":"3b3f02","cmd1":"01","cmd2":"16","err1":"76","err2":"ef"}
model=Proflame2-Remote id=3b3f02 cmd1=31 cmd2=26 err1=25 err2=bc
```

Reporter capture command:

```text
rtl_433 -f 315M -R 207 -M level -F json
```

If that produces no decode, ask them to retry at the Proflame2 reference:

```text
rtl_433 -f 314.973M -R 207 -M level -F json
```

The integration should not instruct users to install rtl_433 as a dependency.
It can document rtl_433 as an external diagnostic tool.

### Validation Rules

Manual sample-derived validation must be strict:

- At least one complete row is required.
- Every row must include `id`, `cmd1`, `cmd2`, `err1`, and `err2`.
- Every row must use the same `id`.
- `id` must fit in 24 bits.
- `cmd1`, `cmd2`, `err1`, and `err2` must fit in one byte.
- Derive `c1/d1` from all `(cmd1, err1)` pairs.
- Derive `c2/d2` from all `(cmd2, err2)` pairs.
- Reject if any command/error set has no stable C/D candidate.
- Reject if any command/error set remains ambiguous.
- Do not guess, average, or accept partial profile data.

Current ECC helpers already support the core derivation:

- `derive_ecc_profile(cmd1_samples, cmd2_samples)`
- `derive_stable_cd(samples)`
- `split_cd(cd_value)`

The manual path should wrap those helpers instead of adding a second ECC
implementation.

### Config Flow Design

The top-level setup menu should offer:

- `learn`: guided RF learning from the selected controller.
- `manual_rtl433`: paste rtl_433 decoded rows and derive the profile.
- `manual`: enter the final profile values directly.

The failed-learning menu should offer:

- `retry_learn`
- `manual_rtl433`
- `manual`

The `manual_rtl433` setup form should collect:

- fireplace display name,
- fireplace short display name,
- controller type.

The `manual_rtl433_prompt` form should repeat as needed and collect:

- pasted rtl_433 decoded rows for the current prompted Power On, Temp Down, or
  Temp Up action.
- a user-facing reminder to paste the newest JSON row after the requested
  button press and ignore duplicate rows from earlier presses.

The `manual_rtl433_power_off` form should appear after enough evidence has been
accepted:

- user sees that rtl_433 output allowed Home Assistant to learn the remote,
- no additional rtl_433 evidence is required,
- continuing advances to the existing feature-selection form.

If the selected controller is LilyGO, collect the existing ESPHome config-entry
link before the rtl_433 sample prompts. This preserves current LilyGO
configuration semantics and avoids any LilyGO RX/TX changes.

The created config entry should store the same data shape as direct manual
entry:

- `backend_type`
- `remote_id`
- `c1`
- `d1`
- `c2`
- `d2`
- optional `esphome_entry_id`

Options should store the same feature flags and debug/listening options as the
existing manual/guided paths.

Diagnostic metadata may include:

- `learned_with_receiver: manual_rtl433`
- `manual_source: rtl_433`
- `manual_sample_count`
- `manual_distinct_cmd1_count`
- `manual_distinct_cmd2_count`

Do not store raw pasted rtl_433 rows in the config entry by default. They are
support evidence and may include environmental metadata from rtl_433 output. If
needed, store them only in packet-debug artifacts when explicit debug logging is
enabled.

### User-Facing Errors

Errors should be actionable and not expose internal exceptions:

- `invalid_rtl433_samples`: rows are missing required fields or values are not
  valid hex/integer values.
- `rtl433_remote_id_mismatch`: rows contain more than one remote id.
- `rtl433_profile_derivation_failed`: rows are contradictory or do not prove one
  stable profile.

For `rtl433_profile_derivation_failed`, tell the user to capture more distinct
button presses or recapture the data. Do not imply that the remote is unsupported
until rtl_433 evidence and YardStick evidence have both been reviewed.

### Manual Learning Implementation Guide

Suggested code changes:

- Add `CONF_RTL433_SAMPLES = "rtl433_samples"` in `const.py`.
- Add profile helpers in `profile.py`:
  - `parse_rtl433_samples(text)`,
  - `normalize_manual_rtl433_profile_input(user_input)`.
- Accept JSON lines and key/value text lines in the parser.
- Reuse `derive_ecc_profile()` from `protocol/ecc.py`.
- Add `async_step_manual_rtl433()` in `config_flow.py`.
- Add `async_step_manual_rtl433_prompt()` for the prompted paste loop.
- Add `async_step_manual_rtl433_power_off()` for the learning-complete
  confirmation prompt.
- Add `_manual_rtl433_profile_schema()` for setup and
  `_manual_rtl433_prompt_schema()` with a multiline text selector.
- Add `manual_rtl433` to the top-level setup menu and failed-learning menu.
- Accumulate samples until the same confidence thresholds as guided learning are
  met.
- Populate a successful `LearnResult` and reuse the existing `learn_features`
  step for final entry creation.
- Reuse `async_step_manual_esphome()` for LilyGO controller linking.
- Add translations for the new menu option, form, field, and errors.

Focused tests:

- parser accepts rtl_433 JSON lines,
- parser accepts copied key/value text,
- parser rejects missing fields,
- sample-derived normalization derives expected `remote_id/c1/d1/c2/d2`,
- mixed remote ids are rejected,
- contradictory rows are rejected,
- config flow exposes the new menu option,
- config flow captures Power On first, then cycles Temp Down/Temp Up before
  showing the learning-complete confirmation,
- config flow creates a YardStick runtime entry from prompted pasted rows,
- config flow still creates LilyGO entries through the existing ESPHome link
  step,
- failed guided learning offers rtl_433-assisted manual fallback,
- existing direct manual entry tests remain unchanged.

### Release Positioning

Manual rtl_433-assisted learning can be released before native SDR support.

Recommended beta message:

- YardStick/LilyGO guided learning remains the normal path.
- Users whose remote decodes in rtl_433 but not YardStick learning can paste the
  rtl_433 rows into manual learning.
- The integration does not install, run, or depend on rtl_433.
- This path helps distinguish YardStick acquisition failures from Proflame2
  protocol variants.

## Generic Versus Device-Specific Support

The first production support matrix should be specific:

- RTL2832U/R820T/R820T2 class RTL-SDR USB dongles.
- Optional replay of captured unsigned 8-bit complex IQ files.

Do not advertise generic "all SDRs" support.

The internal source interface should be generic enough to add other SDR sources
later:

```python
class SDRSampleSource(Protocol):
    async def open(self) -> SDRSourceInfo: ...
    async def read_samples(self, sample_count: int, timeout: float) -> bytes: ...
    async def close(self) -> None: ...
```

The first preferred live source is `RTLSDRPythonSampleSource` using `pyrtlsdr`
behind lazy optional imports. `RTLSDRCommandSampleSource` can remain a fallback
or diagnostic source, and future sources can include SoapySDR without changing
the Proflame2 decoder.

## Source B Implementation Design

Source B means using a Python RTL-SDR binding, preferably `pyrtlsdr`, as the live
IQ source. It should be treated as an optional learning-only capability.

This is not legal advice. The engineering conclusion is:

- GPLv3 is not a blocker for `pyrtlsdr` because this project is already GPLv3.
- The exact `librtlsdr` license text and binary provenance still matter before
  bundling libraries.
- HACS install behavior is the larger practical risk: normal users should not
  receive or build native SDR dependencies unless they explicitly choose SDR
  learning.

### Recommended Dependency Strategy

Use one source tree and keep the default HACS artifact dependency-free:

- Standard release:
  - no `pyrtlsdr` in `manifest.json`,
  - no `pyrtlsdrlib` in `manifest.json`,
  - manual rtl_433-assisted learning available,
  - SDR replay tests available for maintainers.
- SDR beta release:
  - either still no manifest requirement, with user-installed dependency, or
  - generated beta artifact that adds `pyrtlsdr` to `manifest.json`.
- Separate SDR kit:
  - only if HACS/manual install limitations make generated artifacts too awkward.

Do not maintain two hand-edited codebases. If two kits are used, generate them
from one branch:

- common source package,
- standard manifest template,
- SDR manifest template,
- release workflow that builds both artifacts,
- shared tests against both manifests.

### Runtime Import Policy

The main integration must import without SDR dependencies installed.

Rules:

- Never import `rtlsdr` at module import time.
- Import inside `RTLSDRPythonSampleSource.open()`.
- Convert `ImportError`, missing shared library errors, and USB open failures
  into a structured `SDRBackendUnavailableError`.
- Include setup diagnostics:
  - selected source,
  - Python package import result,
  - `librtlsdr` load result,
  - device index/serial,
  - platform machine,
  - Home Assistant container/OS hint if detectable.

### Source Interface

Implement Source B behind the same source interface as replay and command-mode
sources:

```python
class SDRSampleSource(Protocol):
    async def open(self) -> SDRSourceInfo: ...
    async def read_samples(self, sample_count: int, timeout: float) -> bytes: ...
    async def close(self) -> None: ...
```

`RTLSDRPythonSampleSource` responsibilities:

- lazy import `rtlsdr.RtlSdr`,
- open device by index or serial where supported,
- set center frequency,
- set sample rate,
- set gain or auto gain,
- set frequency correction PPM,
- reset/read buffers if the binding exposes a safe operation,
- read bounded sample windows,
- return unsigned 8-bit interleaved IQ bytes or a clearly documented converted
  internal representation,
- cancel async reads and close USB handles in `finally`.

Avoid letting the SDR source decode Proflame2. It should only return IQ samples
plus source metadata.

### Platform Policy

Initial supported platforms:

- Linux x86_64 with user-installed `librtlsdr`.
- Linux aarch64 with user-installed `librtlsdr`.
- Replay files on every platform.

Conditional/beta platforms:

- Linux x86_64/aarch64 using `pyrtlsdrlib` wheels.
- Home Assistant Container when the USB device is passed through and libraries
  are installed in the container environment.
- Home Assistant OS only if dependency installation is proven practical and
  supportable.

Do not claim support for:

- generic SDR devices,
- SoapySDR devices,
- Windows or macOS Home Assistant deployments,
- armv7 until a real install path is validated.

### Configuration

Hidden YAML for Source B:

```yaml
proflame2:
  learning_receiver: sdr
  sdr_source: pyrtlsdr
  sdr_device: "0"
  sdr_frequency_hz: 315000000
  sdr_sample_rate: 250000
  sdr_gain: auto
  sdr_ppm: 0
  sdr_window_seconds: 1.0
```

Production defaults:

- `learning_receiver: controller`
- no SDR source opened at startup,
- Source B unavailable unless explicitly selected,
- missing Source B dependencies reported only when selected.

### Failure Handling

Expected failures should be actionable:

- missing Python package,
- missing shared library,
- unsupported platform,
- no RTL-SDR devices found,
- USB permission denied,
- device busy,
- sample timeout,
- capture contained no RF energy,
- capture contained RF energy but no Proflame2 candidates.

Do not fall back silently from `pyrtlsdr` to another source. Silent fallback makes
issue #11 evidence harder to interpret. If fallback is useful, make it explicit
in configuration.

### Tests

Unit tests:

- import absence is handled without failing integration import,
- fake `rtlsdr` module receives expected frequency/rate/gain/ppm settings,
- read windows are bounded by sample count and timeout,
- close is idempotent,
- USB/device errors normalize to user-facing unavailable reasons,
- `learning_receiver=controller` never imports `rtlsdr`.

Integration tests:

- SDR replay source can complete learning from known IQ fixtures,
- Source B selection fails cleanly when dependency is absent,
- Source B decoded packets feed the same learning/ECC path as controller
  learning,
- YardStick TX remains the runtime controller after SDR learning.

Release tests:

- standard manifest does not include `pyrtlsdr` or `pyrtlsdrlib`,
- SDR beta manifest includes only the intentionally selected SDR dependencies,
- both artifacts report GPLv3 licensing consistently,
- package metadata includes notices for third-party GPL dependencies when the SDR
  kit includes them.

## Receiver Lifecycle And CPU Control

SDR capture should run only when needed:

- Guided learning session starts.
- SDR sample source opens and configures the receiver.
- Each learning prompt collects bounded sample windows.
- The native decoder scans windows until a packet is found or the prompt
  timeout expires.
- Learning session succeeds, fails, is cancelled, or times out.
- The sample source is closed in `finally`.

Do not run SDR capture for normal runtime control. Do not run it at Home
Assistant startup.

Use a lock file to prevent multiple learning sessions from competing for the
same SDR:

- Default lock path: `/tmp/proflame2_sdr_learning.lock`
- Lock contents: pid, flow id, device selector, frequency, sample rate.
- If the lock is held, guided learning should fail cleanly with a user-facing
  "SDR already in use" error.

Subprocess source rules, if using `rtl_sdr`:

- Use `subprocess.Popen` with an argument list, never a shell string.
- Read stdout as bytes.
- Read stderr non-blocking to avoid deadlocks.
- Bound sample counts per read.
- Terminate on close; kill after a short grace period.
- Include exit code and last stderr lines in diagnostics.

## Native Proflame2 SDR Decode Pipeline

The native decoder should follow the same broad strategy as rtl_433 without
depending on rtl_433 code:

1. Capture complex IQ samples.
2. Convert IQ to an amplitude envelope.
3. Estimate noise floor and signal threshold.
4. Slice the envelope into high/low logic.
5. Convert logic runs into pulse widths.
6. Find Proflame2-like pulse rows.
7. Convert pulses to Proflame2 symbols.
8. Reuse existing Proflame2 frame validation and ECC learning.

### IQ Format

Initial format:

- unsigned 8-bit complex IQ (`cu8`)
- interleaved I/Q bytes
- center value near 127.5

Sample rate:

- default `250000` samples/second
- Proflame2 data rate is 2400 symbols/second
- one Proflame2 symbol is approximately 416.7 us
- at 250 ksps this gives about 104 samples per symbol, enough for OOK pulse
  extraction without excessive CPU

### Envelope

For each IQ pair:

```text
i = I - 127.5
q = Q - 127.5
power = i*i + q*q
```

Use integer or float math depending on profiling. The first implementation
should favor clarity and fixture-driven correctness over micro-optimization.

Optional smoothing:

- short moving average or exponential smoothing over a small number of samples,
- configurable but disabled or conservative by default.

### Thresholding

Use adaptive thresholding:

- estimate baseline noise from lower percentiles of envelope power,
- estimate signal level from upper percentiles,
- set threshold between noise and signal,
- apply hysteresis to prevent chattering near threshold.

Debug outputs:

- noise estimate,
- signal estimate,
- threshold,
- high sample ratio,
- longest high and low runs,
- estimated SNR if available.

User knobs:

- fixed threshold override,
- threshold percentile settings,
- hysteresis ratio,
- minimum high/low run samples,
- merge-gap samples.

### Pulse Extraction

Convert sliced logic samples into run records:

```python
@dataclass(frozen=True)
class OOKRun:
    level: int
    start_sample: int
    end_sample: int
    duration_us: float
```

Normalize short glitches:

- drop runs shorter than a configurable minimum,
- merge adjacent same-level runs after glitch removal,
- optionally merge short low gaps inside a high pulse if fixture evidence
  supports it.

### Symbol Recovery

Proflame2 uses the existing project symbol alphabet:

- `S` -> Manchester pair `11`
- `1` -> Manchester pair `10`
- `0` -> Manchester pair `01`
- `Z` -> Manchester pair `00`

The existing byte-window decoder already validates:

- 7 words,
- 13 symbols per word,
- sync/start/end guards,
- parity,
- command layout,
- trailing zero guard.

The SDR decoder should produce one or more candidate Manchester bit streams or
symbol streams, then reuse the same structural validation logic rather than
duplicating frame rules.

Implementation options:

- Convert pulses directly into the symbol alphabet, then validate with a new
  shared `capture.py` helper that accepts symbols.
- Convert pulses into Manchester bits, pack into bytes, then call existing
  `find_proflame_candidates()`.

Recommendation: add a symbol-stream validator in `capture.py` so SDR decoding
does not need to invent artificial byte packing when it already has pulse-level
symbol boundaries.

## Local rtl_433 Reference Path

This section maps the local `rtl_433/` source tree to the narrow behavior this
project should reimplement. It is a design reference only; do not copy rtl_433
source into this integration.

The important local files are:

- `rtl_433/src/rtl_433.c`
- `rtl_433/src/sdr.c`
- `rtl_433/src/baseband.c`
- `rtl_433/src/pulse_detect.c`
- `rtl_433/src/pulse_slicer.c`
- `rtl_433/src/r_api.c`
- `rtl_433/src/bitbuffer.c`
- `rtl_433/src/devices/proflame2.c`

### Acquisition And SDR Setup

The live SDR startup path is:

1. `main()` initializes config, parses command line/config files, selects the
   first configured frequency, and calls `pulse_detect_set_levels()`.
2. `start_sdr()` opens the selected SDR, records sample size, sets sample rate,
   applies SDR-specific settings, applies gain/ppm, resets device buffers,
   activates the stream, tunes center frequency, and starts asynchronous reads.
3. `sdr_open()` dispatches to RTL-SDR, rtl_tcp, or SoapySDR. For the initial
   Proflame2 implementation, only the RTL-SDR shape is needed.
4. `sdr_open_rtl()` discovers devices, selects by index or serial, opens the
   RTL-SDR, records unsigned 8-bit complex sample format, and stores device
   metadata.
5. `sdr_set_sample_rate()`, `sdr_set_center_freq()`,
   `sdr_set_freq_correction()`, `sdr_set_tuner_gain()`,
   `sdr_apply_settings()`, and `sdr_reset()` map directly to librtlsdr calls.
6. `sdr_start()` eventually calls `rtlsdr_read_async()`. Each callback receives
   a byte buffer of interleaved `cu8` IQ samples and forwards it as an
   `SDR_EV_DATA` event with sample rate and center frequency metadata.

For this integration, the equivalent should be a small RTL-SDR source that can:

- select an RTL-SDR by index or serial,
- tune to `sdr_frequency_hz`,
- set `sdr_sample_rate`,
- set auto or manual gain,
- set ppm correction,
- reset buffers before capture,
- stream bounded `cu8` IQ windows,
- report device metadata and recent source errors.

This can be implemented first through the `rtl_sdr` helper process and later
through a direct librtlsdr binding behind the same source interface.

### IQ To Envelope

rtl_433's `sdr_handler()` receives IQ buffers and computes AM/OOK evidence
before pulse detection:

- For `cu8`, the default path uses `envelope_detect()`.
- Optional `magest` uses `magnitude_est_cu8()`.
- The envelope is low-pass filtered before pulse detection.
- Silent frames may be skipped by squelch unless analyzers/dumpers are active.

The minimum native implementation should mirror the default `cu8` path:

1. Treat each sample as interleaved unsigned bytes: `I, Q`.
2. Center each component around 127/128.
3. Compute an envelope-power estimate.
4. Apply a small IIR or moving-average low-pass filter.
5. Track average level for diagnostics and optional squelch.

For local compatibility testing, include a debug knob to switch between:

- `envelope`: squared I/Q power, closest to rtl_433 default for `cu8`.
- `magnitude`: approximate magnitude, useful when comparing with `-Y magest`.

### Envelope To Pulse Package

rtl_433's `pulse_detect_package()` is the core OOK slicer. It maintains state
across sample buffers and returns a complete OOK pulse package when it sees a
long enough terminal gap.

The important state is:

- low/noise estimate,
- high/signal estimate,
- threshold between low and high,
- hysteresis around that threshold,
- current state: idle, pulse, possible gap, real gap,
- current run length,
- collected pulse and gap widths,
- package sample offset.

Important heuristics to preserve in our own implementation:

- Ignore very short pulses as glitches.
- Allow an initial lead-in period so the low/noise estimate can settle.
- Start a package only after the signal rises above threshold plus hysteresis.
- End a package when a gap is much larger than the largest pulse and at least a
  minimum duration, or when a hard maximum gap duration is reached.
- Store pulse and gap widths in samples, with sample rate metadata.

For Proflame2 at 250 ksps, the nominal 2400 baud symbol width is about 104
samples. The existing rtl_433 constants imply that the minimum pulse sample
filter is well below a valid Proflame2 symbol and should reject only short
spikes.

### Pulse Package To Bit Rows

rtl_433 routes an OOK package through `run_ook_demods()`. The Proflame2 decoder
is registered as `OOK_PULSE_PCM`, so it uses `pulse_slicer_pcm()`.

The PCM slicer does not decode Proflame2 fields directly. It converts pulse/gap
durations into a `bitbuffer_t`:

- Convert configured widths from microseconds to sample counts.
- Use `short_width` and `long_width` as the nominal bit period.
- For Proflame2 both are 417 us, so this is NRZ-style PCM: high time emits one
  or more `1` bits, low time emits one or more `0` bits.
- Estimate/tune the exact bit period from observed pulse/gap runs when enough
  one- or two-bit runs are available.
- Round each pulse width to a count of high bits.
- Round each gap width to a count of low bits.
- Cap long zero runs so they do not overflow rows.
- Add a new row when gap is greater than `gap_limit` and not greater than
  `reset_limit`.
- End the message when the final pulse is reached or gap is greater than
  `reset_limit`.
- Call the device decoder with the resulting bit rows.

The Proflame2 device settings in the local rtl_433 decoder are:

- modulation: OOK PCM,
- symbol/bit width: 417 us,
- gap limit: 1000 us,
- reset limit: 6000 us.

The local `pulse_slicer.c` also contains Proflame2-specific debug logging that
prints slicer settings, each pulse/gap pair's emitted bits, row boundaries, and
final rows. Our Python version should provide equivalent diagnostics under
`sdr_debug_level: pulse` or `trace`.

### Bit Rows To Manchester Symbols

The Proflame2 decoder consumes the PCM slicer's bit rows. It expects each row to
start at a Proflame2 word boundary:

- Each word begins with four raw bits `1110`: sync symbol `11` plus start/guard
  bit `10`.
- The next 22 raw bits represent 11 Manchester-decoded bits.
- rtl_433's generic Manchester decoder reads pairs and stops if the pair has two
  equal bits.
- The decoded bit value is the second bit of each valid Manchester pair.
- Proflame2 then inverts the decoded data/flag bits to match the G.E.T.
  Manchester convention used by this protocol.

Each decoded word is validated as:

- 8 data bits,
- one pad bit,
- one parity bit,
- one end guard bit,
- pad bit is 1 only for the first word,
- parity over data bits plus pad bit must match,
- end guard must be 1.

Seven valid words produce the frame bytes:

- serial byte 1,
- serial byte 2,
- serial byte 3,
- command byte 1,
- command byte 2,
- error/check byte 1,
- error/check byte 2.

### Best Integration Boundary

The best implementation boundary for this project is just before the Proflame2
word validator.

The SDR path should produce one or both of:

- Manchester-coded raw bit strings from pulse rows.
- Existing project symbol strings using `S`, `1`, `0`, and `Z`.

Then it should reuse the existing Python validation in `capture.py` and
`waveform.py`:

- `symbols_to_air_bytes()` can convert a symbol string into packed Manchester
  bytes.
- `find_proflame_candidates()` can scan packed Manchester bytes for valid
  frames.
- A new helper can validate symbol windows directly to avoid artificial byte
  alignment when pulse timing already supplied symbol boundaries.

Preferred implementation order:

1. Add an SDR replay source and native envelope/pulse/PCM slicer.
2. Convert PCM rows to symbol/Manchester windows.
3. Feed packed bytes into `find_proflame_candidates()` first, because that
   reuses current validation with minimal risk.
4. Add direct symbol-window validation only if byte packing loses useful
   alignment evidence or complicates diagnostics.

This keeps the SDR receiver learning-only and prevents changes to YardStick,
LilyGO, or TX paths.

### Candidate Search

The decoder should not assume a capture begins at a packet boundary.

Search strategy:

- scan every plausible pulse offset,
- estimate symbol duration around 416.7 us,
- allow timing tolerance,
- look for repeated valid frames,
- prefer candidates with repeat agreement,
- preserve best failure reason when no candidate validates.

Timing tolerances should be configurable for debugging but conservative by
default.

## Packet Mapping

The native decoder emits `CaptureSample` and `ProflamePacket` directly.

Mapping from decoded frame words:

- serial word 1/2/3 -> `ProflameFrame.serial_id`
- command word 1 -> `ProflameFrame.cmd1`
- command word 2 -> `ProflameFrame.cmd2`
- error word 1 -> `ProflameFrame.err1`
- error word 2 -> `ProflameFrame.err2`

Build packets with:

- `source="sdr_learning"`
- `raw` as the packed Manchester bytes when available,
- `received_at` from host time,
- `rssi` or SNR from envelope estimates when available,
- warnings for low confidence, weak trailing guard, or marginal timing.

## Internal Interfaces

Do not force SDR into `RFBackend`, because `RFBackend.send()` is required.

Add a smaller receive-only learning interface:

```python
class LearningReceiver(Protocol):
    async def connect(self) -> None: ...
    async def close(self, *, reason: str | None = None) -> None: ...
    async def receive(self, timeout: float | None = None) -> ProflamePacket | None: ...
```

Existing controller backends can be adapted with a thin wrapper because they
already implement `receive()`.

Guided learning should depend on the receive-only interface. Runtime control
should continue to depend on `RFBackend`.

Suggested modules:

- `custom_components/proflame2/learning_receivers.py`
- `custom_components/proflame2/rf/sdr.py`
- `custom_components/proflame2/rf/sdr_sources.py`
- `custom_components/proflame2/rf/sdr_demod.py`
- `tests/test_sdr_demod.py`
- `tests/test_sdr_learning_receiver.py`
- `tests/test_learning_receiver_selection.py`

## Configuration Design

Initial hidden YAML keys:

```yaml
proflame2:
  learning_receiver: sdr
  sdr_source: rtl_sdr_command
  sdr_device: "0"
  sdr_frequency_hz: 315000000
  sdr_sample_rate: 250000
  sdr_gain: auto
  sdr_ppm: 0
  sdr_window_seconds: 1.0
  sdr_debug_logging: false
  sdr_debug_level: summary
  sdr_raw_capture: none
```

Possible values:

- `learning_receiver`: `controller`, `manual`, `sdr`
- `sdr_source`: `rtl_sdr_command`, `pyrtlsdr`, `replay_file`
- `sdr_device`: RTL-SDR index or serial supported by the selected source
- `sdr_gain`: `auto` or numeric dB string
- `sdr_debug_level`: `summary`, `pulse`, `protocol`, `trace`
- `sdr_raw_capture`: `none`, `failed`, `all`

Future UI:

- Add "Learning receiver" to the guided-learning setup form.
- Default to "Selected controller".
- Show "Manual values" as the conservative fallback when RF learning fails.
- Show "SDR receiver" only when enabled by YAML or advanced/dev option.
- Keep manual rtl_433-assisted entry available without enabling SDR.
- Store the selected runtime controller as `backend_type`.
- Store learning receiver only as diagnostic metadata unless the user wants it
  as their default for future re-learning.

## Debug And Logging Design

Default logging should be compact:

- decoder version,
- source type,
- frequency,
- sample rate,
- capture window count,
- candidate count,
- accepted packet count,
- reject counts,
- best failure reason.

Pulse debug should add:

- envelope statistics,
- threshold values,
- run counts,
- run duration histograms,
- pulse-row summaries,
- symbol timing estimates.

Protocol debug should add:

- candidate symbol windows,
- decoded word bits,
- parity results,
- guard validation results,
- repeated-frame agreement,
- command/error bytes.

Trace/debug capture should add:

- bounded raw IQ files,
- sliced logic files,
- pulse-run JSON,
- symbol-stream text,
- candidate validation JSON,
- source stderr if using a subprocess sample source.

Artifact retention:

- Store artifacts under the existing packet-debug directory.
- Use per-session subdirectories.
- Raw IQ capture is disabled by default because it can grow quickly.
- Raw IQ capture should have size and time limits.

User-facing timeout diagnostics should include:

- whether the SDR source opened,
- whether samples were captured,
- whether non-idle RF energy was detected,
- whether pulse rows were found,
- whether Proflame2 candidate windows were found,
- best protocol failure reason.

## Implementation Plan

### Phase 0: Design And Status

- Add this design document.
- Record that YardStick large-block and lowball paths are not recommended for
  issue #11 beta learning.
- Keep existing YardStick/LilyGO behavior unchanged.

### Phase 1: Learning Receiver Abstraction

- Introduce `LearningReceiver` protocol.
- Add a wrapper for existing `RFBackend` instances.
- Change guided learning internals to consume the receive-only protocol.
- Keep public behavior identical when `learning_receiver=controller`.
- Tests:
	  - current YardStick guided learning still uses YardStick by default,
	  - current LilyGO guided learning still uses LilyGO by default,
	  - existing fake learning tests still pass.

### Phase 1A: Manual rtl_433-Assisted Learning

- Keep the existing manual direct-profile setup path.
- Add a manual sample-derived path that accepts rtl_433-style decoded rows:
  `id`, `cmd1`, `err1`, `cmd2`, and `err2`.
- Parse rtl_433 text values as hexadecimal by default; accept JSON numeric
  values as already-decoded integers.
- Validate that all rows share the same 24-bit remote id.
- Use existing ECC helpers to derive `c1/d1` from `cmd1/err1` samples and
  `c2/d2` from `cmd2/err2` samples.
- Reject ambiguous input with a message asking for more distinct button presses.
- Reject contradictory input with a message asking the user to recapture.
- Save the resulting entry through the same profile creation path as current
  manual setup.
- Store diagnostic metadata such as `learned_with_receiver: manual` and
  `manual_source: rtl_433`.
- Tests:
  - direct manual profile entry remains unchanged,
  - one ambiguous sample asks for more samples,
  - multiple compatible rtl_433 rows derive the expected C/D profile,
  - mixed remote ids are rejected,
  - contradictory command/error rows are rejected,
  - YardStick and LilyGO runtime controller choices still create normal entries.

### Phase 2: Replay-First Native Decoder

- Add `SDRIQCapture` fixture model.
- Add unsigned 8-bit IQ replay source.
- Implement envelope extraction.
- Implement thresholding and logic slicing.
- Implement pulse-run extraction.
- Add debug artifact serializers.
- Tests:
  - synthetic OOK IQ generated from known Proflame2 waveform decodes,
  - noise-only IQ returns no packet with useful diagnostics,
  - threshold override changes slicing deterministically,
  - pulse-run artifact shape is stable.

### Phase 3: Proflame2 Pulse Decoder

- Add symbol-stream validation helper in `capture.py`.
- Convert pulse rows to Proflame2 symbol candidates.
- Reuse existing frame validation and ECC learning.
- Score candidates by repeat agreement and timing quality.
- Tests:
  - known remote symbols decode to `0x3B3F02`,
  - shifted capture start still decodes,
  - repeated frames increase confidence,
  - malformed guard/parity failures are reported.

### Phase 4: Live RTL-SDR Sample Source

- Decide the Source B dependency model:
  - standard HACS package with lazy optional `pyrtlsdr` imports,
  - separate SDR beta artifact,
  - separate SDR HACS repository,
  - or separate helper process.
- Add `RTLSDRPythonSampleSource` using `pyrtlsdr` if Source B is selected.
- Add `RTLSDRCommandSampleSource` using `rtl_sdr` only if Source A remains useful
  as a fallback or diagnostic source.
- Configure frequency, sample rate, gain, ppm, device selector.
- Capture bounded sample windows.
- For Python binding mode, ensure cancellation calls the appropriate async read
  cancellation/close path and releases the USB handle.
- For subprocess mode, read stderr for diagnostics and ensure clean process
  termination.
- Tests:
  - optional dependency absence maps to clean unavailable error,
  - device-open failure maps to clean unavailable error,
  - frequency/sample-rate/gain/ppm settings are applied,
  - sample windows are bounded.

### Phase 5: SDR Learning Receiver

- Add `SDRLearningReceiver`.
- Connect selected sample source to native decoder.
- Implement `receive(timeout)` by scanning bounded IQ windows until a packet is
  decoded or timeout expires.
- Populate `last_receive_status`.
- Tests:
  - replay source can complete learning from known fixtures,
  - timeout preserves best SDR/protocol failure,
  - inconsistent remote ID logic still works,
  - duplicate packet logic still works.

### Phase 6: Config Plumbing

- Add hidden YAML keys for learning receiver and SDR tuning.
- Add config-flow learn setup support for selected controller plus optional
  learning receiver.
- Keep UI conservative: default to controller, hide SDR unless enabled.
- Ensure config entry runtime `backend_type` remains the selected controller.
- Tests:
  - omitted learning receiver uses controller,
  - SDR learning receiver can create a YardStick runtime entry,
  - LilyGO path remains unchanged,
  - invalid YAML values are rejected.

### Phase 7: Diagnostics And Artifact Capture

- Add packet-debug integration for SDR session logs.
- Add optional raw IQ capture.
- Add pulse/protocol trace artifacts.
- Add retention safeguards for raw IQ artifacts.
- Tests:
  - debug disabled produces no large artifacts,
  - pulse debug writes compact summaries,
  - trace mode writes bounded raw artifacts,
  - raw capture mode is never enabled by default.

### Phase 8: Local Hardware Validation

Validate with the known hand-held remote:

1. Capture raw IQ from the local RTL-SDR.
2. Decode remote `0x3B3F02` with the native SDR decoder.
3. Guided learning derives the same profile constants as existing captures.
4. Created entry uses YardStick as the runtime controller.
5. YardStick TX still controls the fireplace using the SDR-learned profile.

rtl_433 can be run manually during validation as an oracle, but it must not be
used by the implementation.

### Phase 9: Issue #11 Reporter Capture

Ask the reporter for raw IQ captures from the wall remote if they have RTL-SDR
hardware. The native replay source should decode those captures locally.

If our native decoder handles the raw IQ and derives the profile, the SDR
learning receiver has a materially better chance than YardStick RX for that
remote.

If raw IQ shows a different pulse/symbol structure, treat the work as
protocol-variant investigation.

## Release Strategy

Recommended first beta:

- Hidden YAML only.
- Learning-only.
- Runtime controller remains YardStick or LilyGO.
- Native Proflame2 SDR decoder.
- Optional `rtl_sdr` sample source or replay source.
- No continuous SDR active listening.
- No raw IQ capture by default.

Do not include YardStick large-block or lowball changes as part of this beta.

## Rollback Strategy

If SDR learning causes problems:

- Set `learning_receiver: controller` or remove the key.
- Guided learning returns to current YardStick/LilyGO behavior.
- Existing runtime config entries continue to use their controller backend.
- No profile migration is needed because learned profiles are ordinary
  Proflame2 profiles.
