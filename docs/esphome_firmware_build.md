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
