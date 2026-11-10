# LilyGO Stack Panic Investigation

Track the panic separately from issue #26's fireplace reception problem. A
successful firmware TX result or a decoded SDR packet is not an acknowledgement
from the fireplace.

## Evidence

- Retained reports initially showed a core-0 cache error and a core-0 invalid
  memory read in the Wi-Fi power-management timer path.
- With Wi-Fi power saving disabled, another panic reached
  `vApplicationStackOverflowHook` on core 1. Disabling Wi-Fi power saving is
  therefore not a sufficient workaround. The affected task was not identified
  in the retained report; stack-boundary corruption is also possible.
- Reports repeatedly followed delayed retransmission and RMT RX restoration,
  before the retransmission-result line appeared. Timing alone does not prove
  the fault originates in the RMT driver.
- The installed-build ELF reports a 4224-byte `RadioRuntimeEvent`, including
  3640 bytes of timing diagnostics. The old completion handler placed that event
  on its stack while calling RX restoration, driver setup, and logging.
- Battery-powered tests reproduced panics. Hundreds of earlier USB-logged
  cycles did not. Power source, placement, and logging must be controlled
  separately; a defective battery is not established.

## Bounded Application Change

The completion handler now receives into a main-task-owned runtime buffer,
separate from the worker's event buffer. Queue-by-value ownership is preserved;
neither task reads the other's mutable scratch buffer. This removes the large
local event without increasing task stacks or changing RF timings.

Host regression tests execute original and retry completion for seven- and
ten-word packets, success/failure handling, and worker-buffer reuse during RX
restoration. Compiler stack-usage reports limit the host completion frame to
1024 bytes; a negative control reinstates the old local event and must exceed
4096 bytes. These are host measurements, not a complete Xtensa task/ISR stack
bound. Hardware retesting is required before calling the panic fixed.

The host compiler measured 4496 bytes for the negative control and 272 bytes
with runtime-owned completion storage. The complete regression run passed
1106 tests, with one pre-existing optional external-source check deselected.

## Controlled ESPHome Comparisons

The published stable versions after 2026.8.2 are 2026.9.0, 2026.9.1, and
2026.10.0. There is no later 2026.8 patch release at the time of this analysis.
Use 2026.9.1 as the September comparison, then 2026.10.0 if useful.

1. Preserve the failing build's firmware and matching ELF using scrubbed
   filenames, together with ESPHome/framework versions and YAML settings.
2. For the first version comparison, keep the same failing firmware source,
   ESP-IDF selection, power source, location, and test cadence. Keep Wi-Fi power
   saving unchanged. Upgrade only the Builder version, rebuild, and install.
3. Repeat the response-driven command cycle: issue the next command as soon as
   the fireplace responds. Record panics, missed commands, and unexpected state
   changes separately. Preserve the ELF for each build.
4. Test the application stack-buffer change initially on 2026.8.2 to separate
   its effect from an ESPHome upgrade. Then test the same application source
   on newer Builder versions.
5. Record the tested source commit, Builder version, actual ESP-IDF version,
   framework, retry setting, settling delay, power-save setting, power source,
   and outcome. A short successful test does not prove the fault is resolved.

### Selecting a Builder Version

The Home Assistant CLI update command does not provide a version selector.
Its `--version` flag is not a way to request an older add-on release. Check the
installed CLI's `ha addons update --help` before relying on version-specific
instructions. Restoring an existing add-on backup is a separate rollback route.

For an exact version, use an isolated Docker Builder on a separate Linux host
or VM with a private copy of the device configuration and secrets:

```sh
docker run --rm --name esphome-version-test --network host \
  -v "$PWD/esphome-test-config:/config" \
  ghcr.io/esphome/esphome:2026.9.1 dashboard /config
```

Open that host's Builder on port 6052 and use its normal install workflow.
Stop the existing Builder first if it occupies that port. The dashboard must
remain on a trusted network; do not expose it publicly. Replace the image tag
with `2026.10.0` for the next comparison. This does not change the HA integration
or update other devices unless their firmware is explicitly installed.

References: [HA CLI update implementation](https://github.com/home-assistant/cli/blob/master/cmd/apps_update.go),
[ESPHome Docker installation](https://esphome.io/guides/getting_started_command_line/).

## Compatibility Validation and Scope

Configuration/schema tests and complete display-example source generation are
run on 2026.8.2, 2026.9.1, and 2026.10.0. CI retains the existing full firmware
compile on its pinned baseline; the added version matrix is configuration and
source generation only. Newer-version firmware compilation and on-device panic
testing are separate gates, not implied by those tests.

The component explicitly includes `esp_driver_rmt`, matching ESPHome's own
receiver dependency declaration so newer unused-driver pruning retains it.

All committed `origin/dev` changes were already ancestors of the diagnostic
branch when audited. Issue #25's old temporary checkout was missing, and its
branch had no implementation commits. Diagnostic categories were reapplied
directly rather than pretending to merge unavailable changes. Existing sensor
names, payloads, enabled defaults, and TX-confirmation contracts are unchanged.
Long-payload presentation remains separate follow-up work.
