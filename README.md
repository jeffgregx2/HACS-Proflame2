# Proflame2 Home Assistant Integration

Turn a Proflame2-controlled fireplace from an isolated handheld-remote appliance into a first-class Home Assistant device.

This project provides a Home Assistant integration for fireplaces using the Proflame 2 RF protocol, including systems commonly found in Mendota and other premium gas fireplace installations. The goal is not simply to replace the handheld remote. The goal is to make the fireplace fully automatable, scene-aware, profile-driven, and controllable as part of the broader smart home.

Instead of relying on repeated +/- button presses, this integration is designed around explicit full-state control: power, flame height, fan speed, light level, front burner, aux output, and CPI where supported.

The integration supports three complementary control models: saved Profiles, direct Service Layer control, and debounced Home Assistant UI controls. Saved profiles can be applied through the `proflame2.apply_profile` service or through per-fireplace profile button entities for simple one-click operation, while `proflame2.set_state` provides direct atomic control when finer precision is needed.

This integration is under active development. HACS custom-repository installation is supported for testing, but formal release packaging and default HACS repository inclusion are not complete yet.

Profiles are intended for one-click activation of complete desired fireplace states such as Minimum Flame, Evening Relax, or Warmup. They make common day-to-day use simple and predictable.

Direct service calls remain equally valid when finer control is needed for advanced automations, scripts, or custom logic. The goal is not to force one model, but to provide the right level of control for the use case while hiding unnecessary protocol details like Cmd1, Cmd2, and ECC values from normal operation.

## Why This Exists

Many Proflame2 fireplaces ship with a capable RF protocol but a limited handheld remote. Important settings such as flame height and fan speed are often exposed only as incremental controls. Power-on may start the fireplace at maximum flame, and reaching a preferred operating state can require a long sequence of button presses.

That creates a poor user experience and often wastes fuel. A user may want a low-flame ambience mode, a fan-assisted warmup mode, or a specific evening profile, but the remote makes those states tedious to reach. In practice, people often leave the fireplace at whatever state is easiest to select rather than the state they actually want.

This integration changes the model. A fireplace can become part of Home Assistant scenes, schedules, dashboards, automations, and temperature-aware control policies. A desired fireplace state can be applied directly instead of recreated manually every time.

## Major Goals

### Full Home Assistant Integration

The project exists to make a Proflame2 fireplace a proper Home Assistant device. Once integrated, the fireplace can participate in scenes, scripts, automations, presence logic, dashboard controls, schedules, and future thermostat-style policies.

This enables use cases that the stock remote cannot provide, such as one-click fireplace profiles, occupancy-aware operation, time-based behavior, and temperature-aware flame modulation.

### Explicit State Control

The Proflame2 protocol is state-based: packets describe the full desired fireplace state, not just a button press. This allows deterministic commands such as “power on with flame level 1, fan level 0, and light level 3” instead of sending a series of remote-control increments.

This is the core technical capability that makes richer automation possible.

### One-Click Fireplace Profiles

A profile is a complete fireplace state that can be applied as a single action. Examples include Morning Warmup, Evening Relax, Movie Night, Guests Over, or Minimum Flame Ambience.

A profile may include power, flame height, fan speed, light level, front burner state, aux state, and CPI where supported. The point is to move from “press the remote many times” to “apply the state I want.”

### Guided Remote Learning

The original handheld remote is only needed for initial setup. During learning, the integration captures the remote serial ID and derives the protocol ECC constants (C/D values) needed to generate valid packets for that fireplace profile.

After setup, the integration can operate independently without requiring the original remote for normal use.

### Pluggable RF Backends

The project is not tied to a single RF device. YARD Stick One is the primary supported backend and the reference platform for validation. It provides TX/RX capability for learning remote identity, validating packets, and operating the fireplace directly.

Future support is planned for lower-cost hardware, especially an ESPHome-based CC1101 backend. The architecture is intentionally backend-neutral so the Home Assistant integration does not need to care whether RF is handled by YARD Stick One, a future ESPHome node, or another supported transport.

### HACS-Native Setup Experience

This is intended to be a normal Home Assistant integration, not a collection of scripts. Setup should happen through a guided config flow that handles RF backend selection, remote learning, feature selection, validation, diagnostics, and saved profile management.

Each fireplace should be represented by its own config entry. Homes with multiple Proflame2 fireplaces can add the integration multiple times and manage each unit independently.

### Optional Home Assistant Thermostat Policy

Native fireplace thermostat behavior appears limited and may extinguish the visible flame completely when the target temperature is reached. That can save gas, but it also removes the visual value of the fireplace.

A future Home Assistant policy layer should allow external temperature control while preserving a better user experience. For example, the fireplace could start at a higher flame for warmup, reduce flame as the room approaches target, and then either hold minimum flame, turn the flame off, or power off depending on user preference.

The intended configuration concept is:

* minimum_flame
* flame_off
* power_off

under a clearly named option such as thermostat_target_behavior.

## Design Philosophy

**Protocol Truth First**

A correct UI is useless if the RF packet is wrong. The integration must preserve the actual Proflame2 packet structure, ECC behavior, repeat behavior, timing, preamble, and on-air encoding.

SmartFire has already proven much of the protocol behavior, and this project should remain protocol-faithful rather than “cleaning up” details that the receiver may depend on.

**Atomic Full-State Control**

Home Assistant entities normally encourage immediate per-entity changes. That does not line up cleanly with the Proflame2 protocol, because the fireplace receives complete state packets.

The integration therefore preserves atomic packet sends even when the UI exposes individual controls. A service call or debounced UI edit composes the entire desired fireplace state and sends it as one logical Proflame2 command. This avoids unnecessary intermediate fireplace state changes and better reflects how the protocol actually works.

Profiles and direct set_state calls are equally valid ways to reach that atomic model. Profiles improve reuse and day-to-day ergonomics. Direct service calls improve precision when the desired state is calculated dynamically.

**Feature-Gated Fireplace Profiles**

Power and flame control are core requirements. Other features are installation-dependent and should be enabled per fireplace profile.

Optional features include fan, light, front burner, aux, and CPI. Aux and CPI should default to disabled because many installations will not support them. Fan and light may be enabled by default, but the user must be able to change feature support during setup or later configuration.

**Manual Mode First**

The first production implementation should focus on manual full-state control. Native thermostat mode should not be exposed as a primary control path until its behavior is better understood.

Manual control means power is explicit, flame is treated as level 1 through 6 when powered on, and flame level 0 is not exposed as a normal manual setpoint. If the user wants no flame, the integration should use power off unless a later validated thermostat strategy intentionally does otherwise.

## Planned User Workflow

1. Install the integration through HACS.
2. Add a Proflame2 fireplace integration entry in Home Assistant.
3. Select and configure the RF backend.
4. Put the integration into learn mode.
5. Press the existing handheld remote so the integration can learn the remote ID and C/D values.
6. Select supported fireplace features.
7. Save the fireplace entry with its permanent learned identity values.
8. Create one or more saved Profiles for that fireplace if named operating modes are useful.
9. Use either proflame2.apply_profile or proflame2.set_state depending on the use case.
10. Build scripts, scenes, dashboards, and automations on top of whichever control path best fits the task.

The remote is not expected to be needed after learning, except as a fallback control.

## Supported and Planned Hardware

**Primary Supported Backend**

YARD Stick One is the first supported RF backend. It is more expensive than the eventual low-cost target hardware, but it provides a capable TX/RX platform and is suitable for production use, development, validation, and diagnostics.

Developer note:

* Yard Stick TX packets are decodable by `rtl_433`.
* Fireplace acceptance still requires physical RF validation against the real receiver.
* The production Yard Stick backend now uses a dedicated worker process so `rflib` and libusb do not run inside the main Home Assistant process.
* Worker restart provides a recovery boundary for many `rflib` / libusb hangs, crashes, and timeouts without restarting Home Assistant.
* The Yard Stick / `rflib` stack may still fail if the VM or USB stack wedges below the process boundary, but worker restart is now the first-line recovery model before resorting to a Home Assistant restart, container restart, or USB reattach.
* In virtualized environments, a wedged Yard Stick or libusb path may affect other devices that share the same virtual USB controller. Prefer a dedicated USB controller or passthrough path for Yard Stick testing where possible.
* For bench TX work, prefer the long-lived `scripts/yardstick_tx_console.py` tool over repeated one-shot invocations.
* The stock remote sends a command burst as multiple repeated identical frames. The fireplace appears to require multiple matching frames before accepting the command, so the Yard Stick backend uses explicit software repetition: five separate `RFxmit(payload)` calls mirroring the observed remote burst.

**Future Hardware Targets**

Lower-cost support is planned for an ESPHome-based CC1101 backend. This is likely the best long-term deployment model for many users because it avoids direct USB attachment to the Home Assistant host and allows a small dedicated RF node to live near the fireplace.

Until CC1101 support is implemented and validated, it should be treated as future support rather than a current capability.

## Installation

For early testing, install this integration as a HACS custom repository:

1. Open `HACS`.
2. Go to `Custom repositories`.
3. Add repository URL:
   `https://github.com/jeffgregx2/HACS-Proflame2`
4. Choose category: `Integration`.
5. Install the integration from HACS.

There is no formal release yet. HACS should currently install from the repository default branch, which is intended to be `dev` for now.

## Example Home Assistant Usage

The integration supports both profile-based control and direct service-layer control.

Users can create saved profiles inside the integration options flow and apply them using `proflame2.apply_profile` or the generated per-fireplace profile button entities for simple one-click activation of common fireplace states.

Direct proflame2.set_state remains available for advanced users, scripts, and custom automations when finer control is desired. Both approaches are valid. Profiles optimize convenience and reuse, while direct service calls optimize precision and flexibility.

**Apply a Saved Profile**

```yaml
alias: Fireplace - Evening Relax
sequence:
  - service: proflame2.apply_profile
    target:
      device_id: YOUR_FIREPLACE_DEVICE_ID
    data:
      profile_id: evening_relax
```

**Advanced Direct Control (set_state)**

```yaml
alias: Fireplace - Minimum Flame Ambience
sequence:
  - service: proflame2.set_state
    target:
      device_id: YOUR_FIREPLACE_DEVICE_ID
    data:
      power: true
      flame: 1
      fan: 0
      light: 2
      front: false
      aux: false
```

## Current Development Status

**Implemented today:**

* protocol domain model
* command byte encoding and decoding
* ECC calculation and stable C/D derivation validated against real captures
* SmartFire-compatible logical frame generation
* SmartFire-compatible waveform generation
* unified ProflamePacket model for TX/RX/runtime
* backend-independent remote learning orchestration
* Home Assistant config flow with manual entry and guided learn-from-remote workflow
* persistent fireplace profile storage (remote ID + C/D)
* per-fireplace saved profile management through options flow
* per-fireplace profile activation button entities
* `proflame2.apply_profile` as a primary user-facing control path
* proflame2.set_state for advanced/direct control
* single primary read-only fireplace entity that doubles as the compact Lovelace-facing summary
* debounced Home Assistant control entities for power, flame, and enabled optional features
* per-fireplace saved profile button entities
* post-TX confirmation listening with requested vs observed state confidence tracking
* restored-state bootstrapping so fireplaces come back with the last known state after restart
* separate last issue sensor for alerting and automation
* diagnostic entities hidden by default
* packet debug logging plus split decode-failure logs
* fake RF backend for deterministic testing
* production Yard Stick worker-process isolation for `rflib` / libusb operations
* real Yard Stick One RX for guided learning and post-TX confirmation
* real Yard Stick One TX using explicit five-frame software burst transmission

**Still in progress:**

* protocol-faithful hardware timing validation
* production backend setup and long-run operational validation
* expanded diagnostics polish
* long-session Yard Stick worker / USB recovery hardening
* HACS release hardening

**Future work:**

* ESPHome-based CC1101 backend
* native thermostat investigation
* optional HA thermostat policy layer
* global/shared profiles (v2, not initial release)

## Planned Feature Support

**Required features:**

* power
* flame control

**Optional feature-gated controls:**

* fan
* light
* front burner
* aux
* CPI / Continuous Pilot Ignition

**Deferred or experimental areas:**

* native thermostat semantics
* low-flame cold-start validation
* Home Assistant thermostat policy
* continuous active listening / passive observed-state synchronization as a production-ready feature
* receiver echo based confirmation and sequencing

## Safety Notes

This project controls a gas appliance. The implementation should be conservative by default and avoid unsupported assumptions.

Users must validate behavior against their own fireplace hardware. Keep the original handheld remote available as a fallback control path. Do not treat this integration as a safety system or as a replacement for the fireplace's own safety controls.

Startup behavior is especially important. A fireplace may need a high initial flame setting for reliable ignition before stepping down to a lower target level. The integration should support validated ignition behavior, including a startup settle period or receiver-echo-based sequencing if required.

Unsupported features should be feature-gated. Unknown protocol behavior should be documented and tested rather than guessed.

## Relationship to SmartFire

[SmartFire](https://github.com/johnellinwood/smartfire) demonstrated that Proflame2 fireplaces can be controlled programmatically and provides valuable reference behavior for packet construction, ECC handling, and transmit timing.

This project builds on that idea with a different product goal: a production-quality Home Assistant integration with guided learning, pluggable RF backends, multi-fireplace support, feature-gated configuration, diagnostics, and atomic full-state control.

I'd like the thank all of those that worked on SmartFire for the incredibly valuable information that made this project possible.

## Diagnostic Visibility

The default entity surface is intentionally simple. Users see one primary fireplace entity whose state is a compact human-readable fireplace summary suitable for Lovelace display. Its attributes expose operational status, state confidence, pending desired state, selected human-readable fireplace fields such as power, flame level, optional enabled features, active profile, last issue, and last update source. A separate last-issue sensor remains available for alerting and automation, and saved per-fireplace profiles also appear as one-click button entities.

Supported Home Assistant control entities are also created for the enabled features of each fireplace. Those controls do not transmit immediately. They stage a desired state, debounce for a short window, and then send one full-state Proflame2 command through the same internal execution path used by `proflame2.set_state`.

Profile buttons do not debounce. Pressing a profile button applies that fireplace's saved full-state profile immediately through the same internal execution path used by `proflame2.apply_profile`.

Protocol internals remain available as diagnostic sensors, but those entities stay disabled by default so the normal UI is not cluttered with command bytes, ECC details, waveform summaries, or backend internals.

Protocol internals such as raw packet data, Cmd1/Cmd2, Err1/Err2, C/D values, waveform summaries, and backend details exist as Home Assistant diagnostic entities but are disabled by default.

## Current Testing Notes

Current code-level status is stronger than the packaging/release status:

* guided learning works
* Yard Stick RX works
* Yard Stick TX works using the explicit five-frame software burst path
* Yard Stick production runtime now isolates `rflib` in a dedicated worker process
* debounced Home Assistant controls exist and send through the same atomic state pipeline as `proflame2.set_state`
* per-fireplace profile button entities exist
* active listening and passive observed-state synchronization exist in the codebase, but should not yet be treated as production-ready features
* ESPHome-based CC1101 support remains future work
* Yard Stick lifecycle stability is improved by worker restart, but VM/USB-level failure cases are still being improved

## Developer Note

To separate RF acquisition problems from Proflame2 decode and guided learning problems, use:

```bash
python scripts/yardstick_probe.py
```

This standalone probe listens for raw RF payloads using the same YARD Stick One `rflib` path as the integration, but does not attempt Proflame2 decode. It is intended to answer the first debugging question quickly: can the YARD Stick hear anything at all?

For longer fixed-frequency diagnostic capture, for example:

```bash
python scripts/yardstick_probe.py --fixed-frequency 314973000 --payload-length 255 --no-sweep --verbose
```

When packet debug logging is enabled from the integration UI, verbose RF logs are split into two files:

- `/config/proflame2_debug.log`
  Plausible receive flow, radio configuration, and successfully decoded packets.
- `/config/proflame2_decode_failures.log`
  Undecodable raw payloads and detailed decode-failure diagnostics.

## License

GNU General Public License v3.0 (GPL-3.0)
