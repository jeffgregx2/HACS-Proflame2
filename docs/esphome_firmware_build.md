# ESPHome Firmware Build Reference

This reference is for advanced LilyGO T-Embed CC1101 users and developers. If
you are setting up a LilyGO controller for normal use, start with the
[LilyGO CC1101 controller guide](lilygo_cc1101_controller.md).

The normal user setup path is:

1. Create the ESPHome device in ESPHome Builder.
2. Apply `esphome/examples/lilygo_cc1101_overlay.yaml` to the generated YAML.
3. Build and deploy from ESPHome Builder.
4. Add or reconfigure the Proflame2 integration in Home Assistant.

## Source-Only Firmware

This project distributes ESPHome source and configuration. It does not
distribute prebuilt firmware binaries.

Normal LilyGO firmware uses these package files through the overlay:

- `esphome/packages/proflame2_tembed_base.yaml`
- `esphome/packages/proflame2_tembed_display.yaml`

Debug firmware can additionally include:

- `esphome/packages/proflame2_tembed_debug.yaml`

The debug package exposes manual FIFO capture/profile controls and other
low-level diagnostics. It is intentionally omitted from normal firmware.

Normal firmware defaults its guided-learning and active-listening receive path
to `rmt_pulse`. The persistent `Active Listener RX Path` entity can be set to
`fifo` as a rollback. For an initial local-build default, add:

```yaml
substitutions:
  proflame2_active_listener_rx_path_default: "fifo"
```

An already-flashed device retains its selected path, so change the entity once
in Home Assistant when moving an existing device to FIFO.

## Release Pinning

The overlay uses a release substitution:

```yaml
substitutions:
  proflame2_package_ref: "main"
```

The package references use that value:

```yaml
packages:
  proflame2_tembed_base: github://jeffgregx2/HACS-Proflame2/esphome/packages/proflame2_tembed_base.yaml@${proflame2_package_ref}
  proflame2_tembed_display: github://jeffgregx2/HACS-Proflame2/esphome/packages/proflame2_tembed_display.yaml@${proflame2_package_ref}
```

The default `"main"` value uses the latest repository version when you rebuild
the LilyGO firmware. For reproducible builds, pin the value to a release tag
such as `"v0.3.0"`. If you pin a release tag, update it whenever you upgrade the
Proflame2 integration and rebuild the LilyGO firmware.

## Local Checkout Development

For local development or manual sync into ESPHome, use local includes instead
of GitHub package references:

```yaml
packages:
  proflame2_tembed_base: !include ../packages/proflame2_tembed_base.yaml
  proflame2_tembed_display: !include ../packages/proflame2_tembed_display.yaml
```

Only use local includes when the package files exist at that relative path in
your ESPHome configuration directory.

## Validation

Python tests do not require ESPHome to be installed:

```bash
./.venv/bin/python -m pytest -q
```

ESPHome validation uses a dedicated virtualenv because ESPHome and Home
Assistant currently require different dependency versions:

```bash
python3 -m venv .venv-esphome
./.venv-esphome/bin/python -m pip install -r requirements-esphome.txt
make esphome-config
make esphome-compile
make esphome-validate
```

Compile success is not RF validation. For release or hardware changes, validate:

- LilyGO TX is decoded by `rtl_433` and accepted by the fireplace.
- Guided learning completes from the native remote.
- Native remote or YardStick TX is reflected in Home Assistant through LilyGO
  active listening when active listening is enabled.

The repository `make esphome-*` validation targets stage a unique temporary copy
under `${TMPDIR:-/tmp}/proflame2-esphome.*` and overlay its local external component after ESPHome
resolves package metadata. That validation therefore builds the checkout's
uncommitted C++ source; it does not alter the GitHub-based production package.

Validation and `tools/prepopulate_esphome_deps.sh` remove their owned temporary
workspace on success, failure, and handled interrupts. Concurrent runs never
share or delete each other's stage. Dependency caches and virtual environments
remain outside these stages so subsequent builds can reuse them.

Pytest owns an isolated temporary workspace and does not retain it, even for
failed tests or keyboard interruption. An explicit `--basetemp` overrides this
automatic workspace and makes that directory the caller's responsibility.
Coverage reports are kept in the checkout as ignored `.coverage` and
`coverage.xml` files, not shared `/tmp` files. Save any required debugging
artifacts explicitly outside temporary workspaces. Forced termination
(`SIGKILL`), power loss, or filesystem errors can still leave a workspace;
check that no process or Git worktree uses it before manual removal. Never
delete raw captures, firmware ELF evidence, or another project's test files.

The live SDR collector intentionally keeps its `flock` coordination file after
closing. Removing that inode during competing capture sessions can defeat
exclusive device ownership. Tests use isolated lock files in their own temporary
directories instead of the live collector's lock.
