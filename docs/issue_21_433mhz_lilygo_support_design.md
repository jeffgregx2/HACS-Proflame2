# Issue #21 LilyGO 433.92 MHz Support Design

## Purpose

Add support for Proflame2 fireplaces that use the 433.92 MHz CE radio band on
the LilyGO T-Embed CC1101 controller. The initial reporter hardware is a SIT
Proflame2 remote, part number `0.584.053`, used in Australia.

This is an internal implementation and validation plan. It does not claim that
433.92 MHz learning or control works until hardware validation is complete.

## Product Decision

The RF band is a device-wide LilyGO configuration, not a Home Assistant
fireplace setting. One LilyGO radio can operate on one band at a time and one
controller already supports one fireplace.

The only user-facing setting is this package substitution:

```yaml
substitutions:
  proflame2_rf_band: "315"
```

Supported values are:

| Value | RF frequency | T-Embed RF switch |
| --- | ---: | --- |
| `315` | `314973000` Hz | `SW1=HIGH`, `SW0=LOW` |
| `433` | `433920000` Hz | `SW1=HIGH`, `SW0=HIGH` |

`315` remains the default and preserves the current controller behavior. The
implementation must not expose independent TX/RX frequencies, raw RF-switch
pins, or a Home Assistant configuration-flow option.

## Current Boundary

The current package hardcodes both TX and RX to `314973000` Hz. Firmware also
hardcodes the T-Embed RF switch to the 315 MHz path during `setup()`. Although
the CC1101 frequency word is calculated from the configured value and used by
both TX and RX, changing only a frequency value does not select the matching
RF path.

The Issue #21 FIFO evidence is therefore not useful for protocol analysis: the
receiver was tuned to the wrong band. Retest protocol behavior only after the
433 MHz radio configuration is in place.

## Design

### 1. Single Source Of Truth

Add an `rf_band` component option that accepts only `315` and `433`. The base
package passes `${proflame2_rf_band}` to that option. The component maps the
selected band to both frequencies and RF-switch levels internally.

The existing `tx_frequency_hz` and `rx_frequency_hz` package fields are
removed. Firmware-owned frequency members may remain as derived implementation
state, but must not be independently configurable through the production
package.

Configuration validation must fail before compilation for any other value,
including an empty value, a raw frequency, or mismatched TX/RX intent.

### 2. Firmware Band Model

Create a small dependency-free C++ RF-band helper, separate from ESPHome
component lifecycle code. It must define:

- `RF_BAND_315` and `RF_BAND_433`.
- Canonical frequency, RF-switch state, and human-readable name for each band.
- An invalid/unknown enum result that cannot silently select a radio path.

`Proflame2TEmbedComponent` receives the validated selected band during code
generation. Before initializing the CC1101, it applies the selected switch
state, derives equal TX/RX frequencies from the band model, and logs the
selected band and frequency at `INFO`.

The selected band is fixed for the lifetime of the firmware boot. There is no
runtime band switching, Home Assistant action, or persisted HA setting in this
change.

### 3. CC1101 Initialization And Radio State

Update the component initialization sequence so that it:

1. Enables board power.
2. Configures `SW1` and `SW0` from the selected RF-band model.
3. Initializes the CC1101 with the derived TX frequency.
4. Uses the same derived frequency whenever FIFO or RMT receive is configured.
5. Restores receive after TX on the already-selected band.

Preserve existing OOK modulation, data rate, native PWM timing, payload
validation, repeat behavior, and 7-word/10-word frame handling. Band selection
must not alter packet contents or transmit scheduling; it changes only the RF
carrier and matching board path.

The current CC1101 register profile has been validated only at 315 MHz. Keep
the profile initially, but require 433.92 MHz hardware validation of RX and TX.
If the radio fails to receive/decode or transmit reliably at 433.92 MHz, record
the observed registers and derive a 433-specific register profile from measured
evidence rather than guessing tuning values.

### 4. Package And Documentation

Update the following:

- `esphome/packages/proflame2_tembed_base.yaml`: add
  `proflame2_rf_band: "315"`; pass it as the sole RF-band component setting;
  remove the two public frequency fields.
- ESPHome component README and contract documentation: replace separate
  user-facing RX/TX-frequency configuration with the two supported bands and
  their fixed internal frequencies.
- LilyGO end-user guide: show the one-line YAML override for a 433.92 MHz
  remote and state that a LilyGO must use one band at a time.
- Root README: retain the one-controller/one-fireplace rule and document the
  one-band-per-LilyGO restriction. Do not list `0.584.053` as supported until
  guided learning and bidirectional hardware tests pass.
- YAML helper: preserve/add the `proflame2_rf_band` substitution with a
  `315` default; do not generate raw frequency or RF-switch settings.

No Home Assistant config flow, options flow, profile model, or ESPHome API
contract change is required.

## Implementation Tasks

- [x] Add the dependency-free C++ RF-band model and mapping helper.
- [x] Add the validated `rf_band` ESPHome component schema option and
      code-generation setter.
- [x] Replace fixed RF-switch GPIO writes with selected-band values.
- [x] Derive both TX and RX frequencies only from the selected band.
- [x] Add startup configuration logging that includes the selected band,
      frequency, and RF-switch values.
- [x] Update the base package with `proflame2_rf_band: "315"` and remove
      exposed per-direction frequency settings.
- [x] Update the YAML helper, user documentation, component README, and
      ESPHome contract documentation.
- [x] Add test fixtures/configurations for both supported substitutions.
- [ ] Complete host, ESPHome compile, and hardware validation below.

**Current status:** Host coverage, Python regression tests, schema validation,
and both staged ESPHome builds are complete. Physical 315 MHz regression and
433.92 MHz receive/transmit validation remain required before release.

## Automated Test And Coverage Plan

### Host C++ Unit Tests

Extend the existing `gcov` firmware host-test harness with the RF-band helper.
The helper must be compiled without ESPHome or ESP-IDF headers and tested for:

- Mapping `315` and `433` after ESPHome schema validation.
- Exact frequency and switch mapping for both values.
- Schema rejection of empty, unknown, numeric-frequency, and mixed-case values.
- No fallback from an invalid value to 315 MHz.

Require 100% line and branch coverage for the new RF-band helper. Keep the
existing TX helper coverage gate: at least 98% line coverage for request
validation and 100% for legacy/extended payload-layout selection. Update the
host test runner so every C++ source with a stated threshold is included in its
own `gcov` assertion.

### ESPHome Schema And Compile Tests

- Add a 315 MHz package validation fixture using the default substitution.
- Add a 433 MHz package validation fixture that overrides only
  `proflame2_rf_band: "433"`. Keep validation-only YAML under
  `tests/fixtures/esphome/`; it must be staged into a temporary directory for
  CI and never placed in the dashboard-mounted `esphome/` tree.
- Add a negative schema test that verifies an unsupported value fails
  configuration validation with an actionable error.
- Compile both valid fixtures in CI against the staged local component. This
  validates generated C++ for both code-generation paths; one compile alone is
  insufficient because it cannot prove the 433 setting reaches the component.
- Preserve the current production package test, including its local external
  component override, so validation never silently loads an older remote
  component schema from GitHub.

### Python Regression Tests

No packet encoder, decoder, learning, or Home Assistant transport behavior is
expected to change. Run the full Python suite and retain existing regression
coverage for both legacy seven-word and extended ten-word frames. Add or update
scaffold/contract tests to assert:

- The production package exports only `proflame2_rf_band` for RF selection.
- The default remains 315.
- The component is given `rf_band`, not user-provided TX/RX frequencies.
- The documented one-band restriction and supported YAML values remain present.

Run Python coverage with `pytest-cov` as an informational report for the
affected Python test modules. Do not use a project-wide percentage as a release
gate until a stable baseline exists; the C++ RF-band helper has the mandatory
100% line-and-branch gate.

### Required Commands

```bash
make firmware-unit-test
make test
make lint-python
make format-python-check
make format-cpp-check
make esphome-validate
git diff --check
```

The CI workflow must run host coverage plus both valid ESPHome configuration
compiles. It must fail if either configuration, the invalid-value check, or the
C++ coverage thresholds fail.

## Hardware Validation Plan

Automated tests prove mapping and generated configuration, not RF behavior.
The Issue #21 reporter must validate a 433 MHz beta with the original remote
available for recovery.

### 433.92 MHz Acceptance

1. Build with `proflame2_rf_band: "433"` and confirm the startup log reports
   `433.92 MHz`, `SW1=HIGH`, and `SW0=HIGH`.
2. With RMT pulse capture active, press Power On once and confirm a bounded,
   non-empty capture tied to the press. Do not use FIFO byte growth as success
   evidence.
3. Run guided learning and record whether it completes. Attach raw diagnostics
   if it fails.
4. Confirm native remote changes update LilyGO and Home Assistant for Power,
   Flame, Light, and each installed optional feature.
5. From Home Assistant, test Power On/Off and one low-risk supported control at
   a time. Confirm both the fireplace response and post-TX receiver recovery.
6. If available, independently capture LilyGO TX with
   `rtl_433 -f 433.92M -R 207 -M level -F json` and retain the output with the
   test report.
7. Repeat the native-remote and HA-control checks after a reboot to verify the
   YAML-selected band is restored.

### 315 MHz Regression

1. Build without an RF-band override and confirm the startup log reports
   `315 MHz`, `SW1=HIGH`, and `SW0=LOW`.
2. Perform guided learning, native remote active listening, and HA Power,
   Flame, Light, and AUX control on an already-supported 315 MHz remote.
3. Confirm both legacy seven-word and supported extended ten-word learned
   profiles retain their existing behavior. The band change must not alter
   payload length, pulse timing, or repeat scheduling.

## Release Criteria

The feature is ready for beta only when all automated checks pass and the
433.92 MHz reporter has demonstrated successful guided learning, active
listening, and at least basic HA transmit control. It is ready for a general
release only after the 315 MHz regression test also passes and user-facing
documentation names the `0.584.053` remote as tested.

If 433 MHz learning succeeds but HA transmit fails, release only the diagnostic
receive evidence if explicitly useful; do not present 433 MHz as supported.
