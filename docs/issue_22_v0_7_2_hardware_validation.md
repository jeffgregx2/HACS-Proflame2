# v0.7.2 public ESPHome transport hardware validation

## Purpose and acceptance

Validate the candidate HA integration with **unchanged released v0.7.1 LilyGO
firmware**. A pass requires physical fireplace response, HA controls and native
remote observations, learning, display synchronization and automatic recovery.
An rtl_433 decode or Ready status alone does not establish fireplace response.

Run only after automated migrated-transport tests pass. This document is
self-contained; prior hardware runs are context, not acceptance for this build.
No test has been completed merely because a result field below exists.

## Preparation

1. Use the test HA instance and identify its linked LilyGO and fireplace. Record
   HA Core version, candidate integration commit/version, controller ESPHome
   version, Proflame2 firmware **v0.7.1**, RF band and remote variant. Verify the
   linked device identity; do not infer it from a similar friendly name.
2. Save the existing configuration/Learning ID outside the Git tree. Preserve a
   restore/rollback path. Use a temporary fireplace entry for learning only after
   removing/unloading its conflicting controller assignment; never assign the
   same controller to two fireplaces or run two HA clients that command it.
3. Arrange direct observation of the physical fireplace. Use only supported
   controls. For burner-off tests, enable Allow flame OFF only if the native
   remote/fireplace supports it. Check CPI only if that feature is available.
4. Use the correct band. A native remote should be about three feet from LilyGO
   during learning. Do not change placement or Wi-Fi between baseline/candidate.
5. Disable refresh and delayed retransmission for the ordinary control/timing
   checks. Record original settings for restoration. After using the native
   remote, wait for its repeated transmission to occur before commanding HA;
   do not interpret that native retransmission as a spontaneous HA command.
6. Enable persistent HA debug logging so it survives HA restart. Merge into the
   existing `logger`/`logs` sections of `configuration.yaml`, without replacing
   unrelated settings:

   ```yaml
   logger:
     logs:
       custom_components.proflame2: debug
   ```

   Validate/restart HA to apply this preparation change. Then start collecting
   HA Core raw/process logs. For Docker, from its Compose directory:

   ```bash
   docker compose logs --follow --no-color homeassistant 2>&1 | tee "$HOME/proflame2-v072-hardware.log"
   ```

   Replace the service name if needed; choose an external non-Git destination.
   Start a new capture after container recreation. The condensed HA log viewer
   may omit INFO/DEBUG. Verify a `Proflame2 version information` entry is present.
7. Optionally capture rtl_433 on 315M or 433.92M as appropriate, and collect
   LilyGO logs. These help separate radio transmission from UI/confirmation.
   Store all captures outside Git and `/srv`. Existing v0.7.1 firmware does not
   need DEBUG firmware, PCM logging, diagnostic capture mode or rebuilding.
8. Establish an actual controller power-cycle method. Unplugging USB **does not
   turn off a battery-powered LilyGO**. Use a test unit already prepared for
   USB-only power or another owner-approved genuine power interruption. Do not
   dismantle a unit or flash v0.8.0 shutdown firmware for this protocol. If no
   safe genuine interruption is available, mark the cold-cycle test blocked;
   an ESPHome restart checks reboot recovery but does not replace cold-cycle
   acceptance. Never use fireplace Power OFF as controller power removal.

Prepare a result sheet with test ID, timestamps/time zone, expected action,
actual fireplace observation, HA/display observation and log reference. Record
failures and incomplete cases; do not fill them from assumptions.

## H0 — Baseline and integration-only upgrade

1. With HA integration v0.7.1 and LilyGO firmware v0.7.1, run one short baseline:
   HA Power ON, change flame, light and fan individually, then Power OFF. Use
   only supported features. Confirm every action at the physical fireplace.
2. Note normal command response and Ready completion. If timing instrumentation
   is available, record action invocation, controller TX completion and HA Ready
   separately. Do not subtract timestamps from unsynchronized clocks.
3. Update **only the HA integration** to the candidate and restart HA. Keep
   controller firmware, RF band, placement, feature settings and profile intact.
4. Without re-learning or rebuilding firmware, confirm the same fireplace is
   present, its settings/Learning ID retained and controller Ready. Confirm logs
   report candidate integration with Proflame2 firmware v0.7.1. Normal startup
   synchronization must also restore the display without sending RF merely to
   update the display.

Pass: unchanged default v0.7.1 firmware/configuration becomes usable. Any firmware
requirement fails the v0.7.2 compatibility gate. Resolve baseline failures before
using this run as a candidate comparison.

## H1 — Physical control, service calls, display and timing

1. Start OFF with supported accessory levels low. Via HA, Power ON; change flame,
   light and fan separately; restore low settings; then Power OFF. Confirm the
   physical fireplace and LilyGO/HA displays after every change.
2. Repeat five ordinary distinct HA commands to compare responsiveness with H0.
   Record any noticeable delay, delayed Ready, timeout or extra radio command.
   With logs, measure the same interval used in H0; controller `elapsed_ms`
   alone measures radio work and is not the complete HA response time.
3. If supported, verify flame 3 survives OFF/ON. With flame-OFF enabled, set
   flame 0 while ON and adjust light/fan; burner stays off. A subsequent HA
   OFF/ON from retained flame 0 starts at flame 1. Disable the flag afterward if
   that was the original setting.
4. Call `proflame2.set_state` for this fireplace through Developer tools with an
   explicit known state. Confirm it is physically obeyed and Ready returns.
   Call explicit `power: false` again while HA already shows OFF; the command
   must still transmit. Preserve other values by omitting them.

   Example in **Developer tools → Actions → YAML mode**, using the test entry's
   `config_entry_id` from its Proflame2 startup/control log (replace the
   placeholder; do not use the ESPHome entry ID):

   ```yaml
   action: proflame2.set_state
   data:
     config_entry_id: REPLACE_WITH_TEST_FIREPLACE_ENTRY_ID
     power: false
   ```

5. If CPI is supported, change its mode while fireplace power is OFF and verify
   the native mode/receiver indication if available, plus TX via rtl_433/logs.
   Restore the original pilot mode. Do not claim physical mode acceptance if no
   receiver indication is observable.

Pass: all supported operations retain their behavior, no stale success or
unexpected replay, Ready after local completion and no reproducible regression
in responsiveness. Requested confidence is valid after HA TX; observed
confidence requires a real native-remote observation.

## H2 — Active listening and switching between transmitters

1. Enable active listening. Using only the native remote, perform Power ON,
   change flame, change light and fan if supported, then Power OFF. Confirm
   physical fireplace, HA state and LilyGO display follow each command.
2. Wait for the native remote's delayed repeat, then use HA to send one known
   command. Return to the native remote and send another distinct command.
   Both directions must continue working; HA must not transmit in response to
   observing the native command alone.
3. Disable active listening through fireplace settings. Send one native command:
   physical fireplace responds, but HA need not follow. Send a HA command and
   confirm physical control/Ready still work. Re-enable active listening and
   confirm one more native command updates HA without restart or re-learning.

Pass: RX events are accepted from the linked controller, RX survives TX and
policy changes, and control still works with listening disabled. If a command is
missed, preserve matching logs; do not diagnose it solely from the UI.

## H3 — Learning and restoration of the existing profile

1. Save the current entry's configuration externally. Remove the saved test
   fireplace entry using normal HA UI only when ready to relearn it. This avoids
   violating one-controller ownership. Do not remove the ESPHome controller.
2. Add a LilyGO-backed fireplace using the unchanged controller firmware and
   run guided learning with the native remote. Follow each current prompt;
   watch for the corresponding learning header/instruction on LilyGO.
3. Complete learning, verify the resulting identity and controls, and perform
   one physical ON/setting/OFF cycle. Confirm normal active listening returns
   after learning, with no stuck capture/learning screen.
4. Restore the original supported/optional settings. If learning fails, preserve
   logs and recover the saved entry/profile before further physical operation.

Pass: start/update/stop learning actions and RX event delivery work with v0.7.1
firmware; no required optional end-confirmation action or stale learning state.
This is a required successful learning pass with an available native remote;
do not claim an extended-native-remote pass using generated packets.

## H4 — Genuine controller power cycle, quiet recovery and offline commands

1. Leave HA running and physically set fireplace OFF. Fully turn the controller
   off using the genuine method arranged in preparation. Record power-off time.
2. Wait until HA's linked ESPHome endpoint reports unavailable. Record that time
   separately: native API loss detection can take approximately 90–110 seconds
   and is not a new completion timeout. Observe the fireplace status becomes
   unavailable automatically, without changing a setting to provoke detection.
3. With the controller still off and fireplace already physically OFF, invoke
   explicit `proflame2.set_state` with `power: false` once. Expect bounded
   unavailable/error feedback, not successful TX/Ready or a permanently busy UI.
   Do not queue an ON command as an outage test.
4. Restore controller power and record when its ESPHome endpoint becomes usable.
   Issue **no** command, settings edit or reload to assist recovery. Verify HA
   becomes Ready, active listening resumes and the display resynchronizes.
   Last Request ID may show unknown before the first HA transmission after
   boot; this must not prevent readiness or the first command in step 5.
   The Last Request ID entity may show `unknown` before the first transmission
   after boot; this alone must not prevent Ready, listening or display sync.
   Count transport recovery after public readiness, separately from Wi-Fi/API
   reconnection time. Existing 15-second recovery cadence plus action time is
   the reference, not a new delay introduced by this migration.
5. After quiet recovery, use the native remote for one known command, wait for
   its repeat, then use HA for a physical ON/setting/OFF cycle. Confirm actual
   receiver operation and no unexpected replay of the outage-test command.

Pass: offline recognition, bounded failed operation, quiet recovery, display/RX
restoration and physical control all work. No false Ready, stuck confirmation,
log flooding, manual reload requirement or spontaneous ON. Mark blocked rather
than passed if only a reboot or simulated state change was possible.

## H5 — HA startup while controller is offline

1. Keep the controller genuinely off. Restart Test HA and restart log capture if
   its container was recreated. Do not change firmware or delete its entry.
2. Leave it off until the log shows the final bounded startup retry still
   waiting (`delay=30.0s`, normally about 50 seconds plus action time after the
   initial defer). The integration must not need to be removed/re-added.
3. Power the controller on. Do not press HA controls or reload an integration.
   Record public endpoint availability and subsequent Ready/display restoration.
4. Use the native remote for two distinct changes, at least five seconds apart;
   then, after its repeat has finished, issue one HA command. Confirm native
   updates reach HA and the HA command is physically obeyed.

Pass: late ESPHome discovery and service registration recover after startup
retries finish, without private runtime fallback or a permanently stopped
listener. Check operation resumes on the existing recovery cadence after public
readiness rather than imposing a fixed whole-system reconnection time.

## H6 — Reload and binding lifecycle

1. With the controller online, reload the linked ESPHome entry from HA UI. Leave
   Proflame2 loaded. Wait for automatic recovery, then verify one native RX and
   one physically obeyed HA command.
2. With Test LilyGO online and the fireplace idle/OFF, go to **Settings →
   Devices & services → Entities**. Find **Proflame2 Last Request ID** belonging
   to the **test ESPHome LilyGO device**, not a Proflame2 fireplace entity or the
   production controller. Open its settings and copy the current **Entity ID**.
3. Edit only the Entity ID, adding `_binding_test` to the end of the existing
   ID; retain the `sensor.` prefix. Save/update. Do not change the name or unique
   ID instead. Wait for the linked ESPHome entry to finish any reload/reconnect.
4. Without restarting HA or manually reloading Proflame2, send one normal HA
   command. Verify rtl_433 sees the intended packet, Proflame2 returns Ready,
   and the renamed Last Request ID entity receives the new request value.
5. In the same entity's settings, turn **Enabled** OFF and save/update. Wait for
   any automatic ESPHome reload/reconnect to finish. Keep the fireplace physically
   OFF, then call `proflame2.set_state` with `power: false` as shown in H1.
   Expect a missing/disabled required-telemetry error and no successful completion;
   the TX action must not be dispatched with that entity disabled.
6. Return to the Entities list. If necessary, filter **Status → Disabled** to
   find that same entity. Turn **Enabled** ON and save/update. Let ESPHome
   reconnect; without manually reloading Proflame2, repeat the OFF service call.
   Verify the expected packet, Ready status and updated Last Request ID.
7. Restore the original Entity ID, let any automatic reload finish and repeat
   one command to confirm operation still works. No relearning is required.

This checks binding by stable registry identity even when an entity's editable
ID changes. Use only the selected test entity, and restore it enabled with its
original ID. A noncritical firmware-version rename is supplemental; the critical
Last Request ID case above is the focused remaining acceptance check.

Pass: public binding recreation and entity lifecycle changes are handled without
stale identity, wrong-device operations or lost subscriptions. User-disabled
telemetry has a clear supported recovery path.

## H7 — Additional paths, without repeating the complete protocol

- **Extended frames:** if the physical fireplace/native remote is legacy, use
  YardStick as a controlled extended transmitter to Test LilyGO with a separate
  saved test profile. Verify extended reception/learning where supported by the
  current learning flow. Then use an isolated second receiver or rtl_433 to
  decode one extended command transmitted by LilyGO. Use a synthetic identity
  different from the real fireplace's paired remote, and keep the real fireplace
  OFF during this generated-traffic test. Restore the real profile
  afterward. This validates generated extended traffic, not extended native
  remote RF behavior or a physical extended fireplace. Retain the legacy physical
  acceptance and automated extended coverage as separate evidence.
- **Other radio band:** where two matching radios are available, verify one
  transmit/receive cycle on the other band using generated traffic. Keep LilyGO's
  released v0.7.1 firmware binary/YAML band setting unchanged for its primary
  acceptance; do not make an alternate firmware rebuild a compatibility gate.
  Record unavailable supplemental band hardware as untested, not passed.
- **Refresh, if used:** after all normal tests, enable a short refresh period,
  leave the native remote alone and confirm one current-ON refresh is transmitted
  without a completion error. Turn OFF; no refresh transmission should occur.
  This tests action dispatch, not an hour-long fireplace keepalive soak. Restore
  refresh to its original value.
- **Delayed retransmission, if used:** enable it on LilyGO, send one command and
  confirm its existing local repeat; restore the original setting. It must not
  produce a second HA transaction or interfere with subsequent confirmation.
- **YardStick, if available:** with its own controller/profile, verify one normal
  ON/setting/OFF sequence via rtl_433 or its physical fireplace. It is not a
  replacement for LilyGO physical acceptance and does not certify unrelated
  YardStick setup issue #38.

Generated extended RX/TX coverage is required when the changed transport serves
extended configurations and test radios are available; alternative automated
coverage and missing hardware must be reviewed explicitly before release.
No extended native remote is assumed available. Band, delayed retransmission
and YardStick supplemental checks should not become repeated full test cycles.

## H8 — Persisted registry upgrade (automated one-off)

This replaces a manual two-installation registry comparison. Run
`scripts/validate_registry_upgrade.py` with two isolated Python environments:

- Source: HA **2025.8.0**, Python 3.13, test helper package
  `pytest-homeassistant-custom-component==0.13.269`, `aioesphomeapi==37.2.2`,
  `pycares<5`, and the integration's `requirements.txt`.
- Destination: HA **2026.10.4**, Python 3.14, test helper package
  `pytest-homeassistant-custom-component==0.13.375`, `pycares>=5,<6`, and
  `requirements.txt`.

Use environments outside source; do not point this test at live HA storage.
From the repository root, supply the two interpreter paths:

```bash
python3 scripts/validate_registry_upgrade.py \
  --from-python /PATH/TO/HA-2025.8/bin/python \
  --to-python /PATH/TO/HA-2026.10/bin/python
```

The orchestrator creates its own temporary synthetic configuration, runs each
phase in a separate process and removes the fixture afterward. It makes no
network connection or RF transmission and does not start integration services.

1. HA 2025.8 creates and saves real config-entry/device/entity registry files:
   two LilyGO-backed fireplaces (identifier-based and MAC-only controllers),
   a YardStick-backed fireplace including band selection, and an unrelated
   device. Fireplace sensor, switch, number and profile-button bindings are
   represented, along with controller telemetry.
2. HA 2026.10 loads those same files through its actual registry storage
   migrations. Compare saved entry IDs/data/options, device UUIDs, identifiers,
   connections, ownership, parent links, entity IDs/UUIDs/unique IDs and entity
   device assignments before invoking our reconciliation.
3. Exercise Proflame2 identity reconciliation, controller resolution and parent
   linking, plus device registration using the actual entity `device_info`.
   Verify no duplicate or reassigned devices/entities and no YardStick parent.
4. Save the modern registries and load them in another HA 2026.10 process.
   Repeat the checks to verify persistence and idempotence.

Pass requires all three phases to report PASS. This tests actual persisted
registry migration and our registry code, using HA test helpers to construct
the isolated core and synthetic entries. It does not simulate a complete live
HA upgrade, load the full ESPHome integration, exercise service discovery or
validate radio operation. H1/H2/H6 retain communication/lifecycle acceptance;
there is no need to repeat the whole hardware protocol on both HA versions
solely to establish registry migration.

## Result record and release decision

| Test | Result (not run/partial/pass/fail/blocked) | Actual receiver/HA observations and log reference |
| --- | --- | --- |
| H0 — Baseline/HA-only upgrade | Pass (owner report) | Existing configuration test succeeded. |
| H1 — Physical control/timing | Pass (owner report) | Tested against the physical fireplace; successful. Exact timings and individual optional controls were not itemized. |
| H1 — set_state service | Pass (owner report) | Service-call test successful, with rtl_433 observation. |
| H2 — Active listening | Pass (owner report) | Successful; rtl_433 used. Physical-fireplace observation not separately reported for this case. |
| H3 — Guided learning | Pass (owner report) | Test setup learning successful. |
| H4 — Cold cycle/offline command/recovery | Pass (owner report) | Successful. Offline refusal named unavailable required telemetry; this is the known-offline path rather than missing completion after dispatch. |
| H5 — Offline startup/late registration | Pass (owner report) | Successful. |
| H6 — ESPHome reload | Pass (owner report) | Reload test successful. |
| H6 — Entity rename/disable/re-enable/restore binding | Pass — functional binding (owner report) | Rename/live update, disabled refusal with no RF and re-enable/control recovery passed. Original ID and correct controller association are restored; owner confirms the entity updates during active listening and HA operations. Owner subsequently upgraded HA from reported 2026.3.0b0 to 2026.8.0 and the entity returned to the ESPHome device page. The presentation symptom is no longer present; its exact cause is unconfirmed. |
| H7 — Generated extended RX/TX | Pass (owner report) | Successful; rtl_433 used. |
| H7 — Other band, if available | Not reported | The latest report does not identify a second-band run. |
| H7 — Supplemental paths | Pass (owner report) | Supplemental testing reported successful; individual optional checks not itemized. Delayed retransmission applies only to LilyGO; it is not a YardStick capability. |
| H8 — Persisted HA 2025.8 → 2026.10 registry upgrade | Pass (automated) | Synthetic real-storage upgrade: six devices and 16 entities preserved through migration, integration reconciliation and a second modern load. HA 2025.8.0 → 2026.10.4. No live installation or RF operation used. |

### Owner acceptance report — 2026-11-18

Owner reports H0/H3/H1 ordinary controls/H1 set_state/H2/H4/H5/H6 reload/H7
extended/H7 supplemental successful. All tests used rtl_433; physical fireplace
operation was explicitly confirmed for H1 ordinary control. Do not extend that
physical observation to other rows. H6 binding remains pending clarification.
The report does not restate exact HA/integration/firmware versions, individual
optional checks or timing measurements; retain this limitation when assessing
unchanged-v0.7.1-firmware acceptance.

H4's known-offline error is consistent with the implementation: required public
telemetry was unavailable, so readiness was rejected. The more explanatory
missing-confirmation message applies after an action was dispatched while the
public endpoint appeared usable but completion telemetry never arrived. Neither
case identifies whether loss was caused by power, Wi-Fi or another disconnection.
The named telemetry entity is the first unavailable field encountered, not
proof of a fault specific to that sensor. The initial report required no
connection-handling change. At the owner’s subsequent request, the known-offline
user message now names possible power-off and network/Wi-Fi causes; the
technical field remains in logs. Disabled/missing telemetry configuration errors
retain their specific guidance. Detection, retries and rollback are unchanged.

### Owner-reported control and lifecycle results — 2026-11-17

Results concern the local 0.7.2-debug candidate; firmware was most recently
reported as v0.8.0. No new version log or timing measurements accompany this
report. These are passing subchecks, with the full-protocol status above kept
separate.

| Reported scenario | Result | Observation |
| --- | --- | --- |
| Restart HA with Test LilyGO running | Pass | All attempted operations worked; rtl_433 received the packets. |
| Shut down Test LilyGO, then attempt controls | Pass | The new missing-confirmation message appeared; attempted changes reverted. |
| Start Test LilyGO again | Pass | Operations resumed after a short time; rtl_433 received packets. |
| Shut down LilyGO, then restart HA | Pass | The error reported ESPHome offline, distinct from missing confirmation. |
| Start LilyGO after HA's offline startup | Pass | LilyGO returned online and fireplace control resumed; rtl_433 received packets. |

The owner also reports no noticed behavior or timing difference during smoke
testing. No additional fix is indicated by these results. RF packets observed
by rtl_433 establish transmission/reception at that receiver, not an independent
physical-fireplace acknowledgement. Guided learning, native-remote active RX,
remaining binding tests and unchanged-v0.7.1-firmware acceptance are not inferred
from this report.

Record precise integration/firmware versions, actual results, baseline/candidate
timings and unresolved concerns. A log saying TX completed is controller evidence;
only observed receiver behavior establishes that the physical fireplace obeyed.
Unexplained false success, lost recovery/listening, duplicate transmissions,
required firmware change or reproducible response-time regression blocks v0.7.2.

After testing, restore original settings/logging and the real fireplace profile;
leave the physical fireplace in the intended state. Keep private captures outside
source. No release, push or HACS review request is implied by a test pass.


### H6 binding follow-up — 2026-11-18

Owner confirms the first five actions in the condensed six-action H6 checklist:
selection, rename, real-time updates/control, refusal while disabled with no RF,
and successful re-enable/recovery. The final action, restoring the original
entity ID by removing the temporary suffix, failed: the entity disappeared
from the ESPHome device diagnostics after saving and remained absent after
browser refresh/navigation. No disabled entry was shown on that device.

H6 is not a full pass. Determine global Entities-list presence (all filters
cleared, including hidden/disabled), current ID, HA/firmware versions and errors
around the save before attempting recovery that could obscure the state.
The current Proflame2 binding observer reads/subscribes to ESPHome entities;
it does not remove or rename them. That source finding is not a root-cause
explanation or proof of an upstream fault. No code change, reload, entity
recreation or firmware operation was performed by the agent.


### H6 registry presence confirmed — 2026-11-18

Owner found Last Request ID in the global Entities list with its original ID
restored and the correct test ESPHome controller association. No unexpected
log errors were reported. The entity was therefore not deleted or detached;
the outstanding device-page absence does not establish a transport failure.
Check Enabled/Visible settings and a command's request-ID update under the
restored ID before marking the final action passed. No implementation fix is
indicated by registry presence alone.

Owner reports the HA debug environment version as **2026.3.0b0**. Record this
as reported; do not infer a different HA version or a LilyGO firmware version.
If this is HA Core, the candidate selects its pre-2026.8 legacy registry adapter,
consistent with the supported minimum. Firmware-version confirmation remains
outstanding. No live HA operation or source-code change was made by the agent.


### H6 functional restoration accepted — 2026-11-18

Owner confirms that the entity under its restored original ID updates during
active-listening activity and HA commands. With its registry presence and
correct controller association already confirmed, this completes functional
H6 rename/disable/re-enable/restore acceptance. No lost binding or missing
entity is established. Retain its absence from the device Diagnostics section
as an unexplained presentation observation; the Visible setting has not been
reported. No implementation change is warranted by this observation alone.
Exact LilyGO firmware-version confirmation remains separate from H6's result.


### Device-page visibility restored after HA upgrade — 2026-11-18

Owner upgraded the test HA environment from reported 2026.3.0b0 to 2026.8.0
and confirms Last Request ID is visible on the ESPHome device again. H6
functional binding had already passed with the restored original entity ID.
The presentation symptom is no longer observed; no Proflame2 implementation
change was required. The exact cause was not established, so do not characterize
this as a proven HA defect or a specific upstream fix.

HA 2026.8.0 selects the modern registry adapter. A post-upgrade HA transmission
and native-remote update are the focused remaining smoke checks for this actual
installation; this visibility report alone does not establish their outcome.
The owner performed the upgrade. The agent had only initiated an image pull
and had not edited deployment files or stopped/recreated containers before
handoff. No agent firmware operation, commit or push occurred.


### Post-upgrade control regression and fix — 2026-11-18

Owner reports all controls reverting after the HA 2026.8.0 upgrade, with Last
Issue Unknown. Read-only logs/registry inspection identified active ESPHome
unique-ID migration, which the legacy-only binding missed, and an error longer
than HA's sensor-state limit. Local regressions reproduced both defects before
correction. The prior H6 visibility result is valid, but the complete candidate
is not accepted until post-fix control/listening is confirmed.

After loading the fix by restarting HA, repeat only:

1. One HA ON/settings/OFF sequence, verifying Ready, updated Last Request ID
   and the expected RF output (physical receiver if readily available).
2. One native remote change, verifying HA follows; wait for the native repeat
   before subsequent HA commands.
3. Confirm no missing-telemetry error or Unknown Last Issue caused by oversized
   text. Full technical error details remain available in attributes/logs.

No firmware rebuild or configuration recreation is required. Do not repeat
learning, complete outage cycles or entity renames unless these checks expose
a new failure. The agent did not restart HA or generate RF during diagnosis.


### Post-fix owner follow-up and older-HA test preparation — 2026-11-18

Owner reports the HA 2026.8.0 follow-up is working after the correction and asks
about testing a fresh 2025.8 → 2026.8 upgrade. The current configuration has
already passed through newer HA/ESPHome registry migrations; merely changing
the image tag backwards would not produce a valid old-version baseline.
Use a matching pre-upgrade backup if available, or a separate fresh deployment
configuration created under HA 2025.8.0, then upgrade that same test directory
to 2026.8.0. Keep the current working configuration preserved. Do not run two
HA clients commanding the same controller concurrently. No downgrade, new
container, backup restore or restart was performed by the agent.


### Final release-tag firmware compatibility accepted — 2026-11-19

Owner reports completion of the agreed final compatibility smoke: current
v0.7.2 integration, HA 2026.11.2, firmware built from the exact GitHub
v0.7.1 tag with ESPHome Builder 2026.6.0; existing entries retained, one HA
transmission and one native-remote update. This supplements completed
U0–U5 staged/direct upgrade acceptance and closes the agreed released-source
firmware gate. It is not a claim that every historical build/binary was tested.
No additional broad hardware run is required without new changes/failures.
Final release metadata, checks/CI, commit/push/publication remain separate.
