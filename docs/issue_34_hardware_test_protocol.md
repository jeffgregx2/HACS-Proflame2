# Issue #34 hardware validation protocol

Updated: 2026-11-09. Status: R1/R2 reports reviewed; automatic recovery is
supported, with RF confirmation and a command-free reload check outstanding.

This procedure checks that Proflame2 automatically recovers its LilyGO display
connection and active listening after Home Assistant startup or an ESPHome entry
reload. Run preparation, R1, R2 and cleanup in that order. No other hardware test
cases are required by this protocol.

Allow approximately **10–20 minutes for R1 and R2 after preparation**, assuming
HA restarts in a few minutes and neither case fails. This is a provisional
estimate with moderate confidence; installation, logging setup and investigation
of failures are additional time. Do not extend outages just to fill that time.

## Equipment and software

- Test Home Assistant with the Proflame2 integration containing the issue #34
  recovery fix. Record the installed source revision: a version label alone may
  not distinguish development builds.
- A configured, learned Test LilyGO with a working display and Wi-Fi connection.
- A native remote or another transmitter matching the learned profile and RF band.
- A way to turn the Test LilyGO fully off and back on.

No firmware update is needed for the integration fix. Released beta6 firmware is
sufficient, but does not have a center-button shutdown gesture. An already
installed shutdown-enabled test build may be used; record its version.

Leave Production unchanged. RF commands addressed to a physical fireplace can
operate it, even when the receiving LilyGO belongs to Test HA. Use your designated
test fireplace/profile and keep the test area attended.

## Preparation

1. Identify the Test Proflame2 fireplace entry and its **linked ESPHome entry**
   under **Settings → Devices & services**. Record both names and the Proflame2
   `config_entry_id` from the HA log. All log checks below refer to that ID.
2. In **Edit fireplace settings**, enable **active listening**, set refresh to
   zero, enable **Enable packet debug log**, and save. That option is required
   for the transport's `display_state_update invoke` messages; YAML logger levels
   alone do not enable them. Disable optional delayed retransmission on the LilyGO device.
   Suspend automations targeting the test fireplace and let pending commands
   finish. Record settings to restore after testing. Do not run guided learning.
3. Ensure integration DEBUG logging will be enabled **from HA startup**. In HA's
   `configuration.yaml`, merge the following into the existing `logger` section;
   do not create duplicate `logger` or `logs` sections or replace unrelated values:

   ```yaml
   logger:
     logs:
       custom_components.proflame2: debug
   ```

   Validate the configuration and restart Test HA to apply it if needed. Keep a
   copy of the original logging configuration for cleanup. This preparation
   restart is separate from the restart performed in R1. Do not rely solely on
   the integration's temporary **Enable debug logging** control surviving a
   restart. Firmware DEBUG logging and waveform capture are not needed.
   To verify the YAML logger setting after startup, search the full log for
   `Proflame2 version information:` at DEBUG level. Power-change messages need
   not be DEBUG. A view showing only warnings/errors cannot verify this setting.
4. Confirm that the Test LilyGO is online and its display agrees with HA. Using
   the matching transmitter, send one command and confirm HA follows it. Return
   the fireplace to Power OFF and let its state settle. There is no fixed
   baseline waiting period.
5. Use Docker Compose logs for collection. HA's System Logs view may omit INFO
   and DEBUG messages even when the integration emits them. From the Test HA
   Compose directory, run the following after the container is running:

   ```bash
   docker compose logs --follow --no-color homeassistant 2>&1 | tee "issue-34-$(date -u +%Y%m%dT%H%M%SZ).log"
   ```

   Replace `homeassistant` if your Compose service has a different name. This
   prints retained container logs and then follows new messages; do not add
   `--tail 0`, which would omit startup evidence. Keep the terminal open through
   the test. Press Ctrl+C after collection to stop following; it does not stop HA.
   Keep the saved file private. Record action times and the time zone separately.
   A Compose down/up replaces the container and ends an existing follower: start
   a new capture immediately after up, before restoring LilyGO power. Save logs
   before another down/up removes the container that holds them.

Optional: run rtl_433 on the matching band, or enable **PCM Payload Logging**, to
help investigate a missed receive. Neither is required for a passing run.

### How to power the test controller off and on

- **Without a battery:** unplug USB to turn it off; reconnect USB to turn it on.
- **With shutdown-enabled test firmware and a battery:** disconnect USB, then
  hold the center button. The shutdown prompt appears after about four seconds;
  keep holding until it shuts down at approximately ten seconds total. Release
  the button. Reconnect USB to restart. Release early to cancel the countdown.
  If the screen says **Cannot shut down / Disconnect USB**, release the button,
  disconnect USB and start the hold again.
- **With a battery and firmware without shutdown support:** USB removal does not
  turn it off. Do not dismantle the unit or change network settings for this
  procedure. Record R1 as blocked if a full power interruption is not available;
  R2 can still be performed.

Confirm the screen actually turns off. Fireplace Power OFF is a fireplace
command; it does not turn the LilyGO controller off. Controller shutdown does
not change fireplace power.

## R1 — HA starts while LilyGO is offline

**Purpose:** verify that recovery continues after startup retries finish, and
that the real radio and event subscription return without manual intervention.

1. Fully power off Test LilyGO. Leave it off and restart **Test HA**. There is no
   need to wait for HA to detect the outage before initiating this restart.
   If using Compose down/up, start the Docker log capture once the new container
   is running, using the command in preparation. Keep LilyGO off during capture.
2. After HA starts, confirm the Proflame2 fireplace entry is loaded rather than
   showing a setup failure. Record the HA fireplace state now: this is the state
   the LilyGO display should show when it returns. Do not assume that HA restored
   exactly the state shown before the restart.
3. Keep LilyGO off until the log for this fireplace shows
   `Proflame2 startup display sync still waiting` with `delay=30.0s`.
   This is the last bounded startup retry. The successive waits are 5, 15 and
   30 seconds, about 50 seconds plus action time after the initial
   `Proflame2 startup display sync deferred` warning. There is no separate
   retry-exhausted message. Record the final retry's timestamp.
4. Reconnect LilyGO power. Record when its ESPHome entry becomes available in HA.
   **Do not issue a HA fireplace command, edit settings or reload an integration.**
5. Before sending another RF command, confirm the LilyGO display automatically
   shows the state recorded in step 2. Record the restoration time. The display
   should recover on an eligible check, normally within 15 seconds plus API
   connection/action time after ESPHome is ready.
6. Look for `Proflame2 active listening resumed`. Then use the matching transmitter
   to send two distinct known states, at least five seconds apart, and confirm
   HA follows both. Record each transmitted state and corresponding HA update.
   Listening retries can take up to 30 seconds plus API time; do not apply the
   display's 15-second interval to listening. If no resume message appears,
   test reception once ESPHome has been available for 30 seconds; record whether
   actual reception works. A resume message alone is not proof of RF reception.
7. Save the HA log and your action times. Proceed to R2 as soon as both received
   states have been confirmed; no separate stable-recovery wait is needed here.

**Pass:** the fireplace entry loads while LilyGO is offline; the final startup
retry occurs before power restoration; the display returns automatically; and
both RF commands update HA without a HA command or Proflame2 reload.

If display restoration or listening has not returned within two minutes of
ESPHome becoming available, stop and collect the failure evidence described
below. This is an investigation threshold, not a required waiting period.

## R2 — Replace the linked ESPHome runtime

**Purpose:** verify that the running Proflame2 entry reconnects to the new real
ESPHome runtime and receives events without being reloaded itself.

1. Start with Test LilyGO online, active listening ON and the HA/display state
   synchronized. If R1 could not be performed, establish this condition using
   preparation. Keep HA and the Proflame2 entry running throughout R2.
2. Under **Settings → Devices & services → ESPHome**, find the linked Test LilyGO
   configuration entry, open its three-dot menu, and choose **Reload**.
   Reload **only that ESPHome entry**, not Proflame2 and not the entire HA server.
   Record the reload time.
3. Wait for ESPHome to become available again. Before issuing HA control commands
   or transmitting RF, look for `display_state_update invoke` for the linked
   entry in the full HA log. This transport diagnostic is emitted at WARNING
   level when **Enable packet debug log** is enabled. Record its timestamp and preserve any following
   exception. This records an automatic display action attempt; the log line
   alone does not establish that the action succeeded.
4. Use the matching transmitter to send two distinct known states, at least
   five seconds apart. Confirm HA follows both and the LilyGO display follows
   the observed state. Record each transmitted state and HA update. Do not use
   a HA command or reload Proflame2 to make recovery happen.
5. At the end of the test, leave the connection healthy while reviewing/saving
   the log. If a display synchronization interruption was logged, confirm that
   it eventually has one `Proflame2 display synchronization recovered` INFO.
   This requires at least 60 seconds of healthy observations and a successful
   display confirmation. Investigate if it is still absent two minutes after
   display restoration. A fast reload may fall between display checks and
   produce no interruption episode; in that case no recovery INFO is required.

**Pass:** the existing Proflame2 entry automatically reconnects, attempts display
synchronization, and processes both received states with matching HA/display
updates. No manual Proflame2 reload or HA fireplace command is needed. Retained
screen contents alone do not establish successful synchronization.

If the display action or listening has not returned within two minutes of
ESPHome becoming available, stop and collect failure evidence.

## Log checks for the test group

Use timestamps and the selected fireplace entry ID to separate entries and runs.
Count only the following display messages together:

| Message | Expected behavior |
| --- | --- |
| `Proflame2 display synchronization interrupted` | One WARNING per ordinary failure episode. |
| `Proflame2 display synchronization still interrupted` | DEBUG summaries no more often than once per 60 seconds. A short episode may have none. |
| `Proflame2 display synchronization recovered` | One INFO after an actual display update and at least 60 seconds of healthy observations followed by successful confirmation. |

A disconnect before stable confirmation keeps the same episode open; it must
not cause repeated display warnings or premature recovery announcements. No
additional timed disconnect cycles are required. ESPHome connection messages,
startup retry messages and active-listener messages are separate log paths and
must not be counted as display warnings. Expected disconnections should not
produce display ERROR tracebacks.

The 60-second healthy window delays the recovery announcement, not listening,
display restoration, user commands or reconnect attempts.

## Results to report

Record the HA version, integration source revision, firmware version, time zone
and test duration with the following results. Keep raw logs and device IDs
private. Results in this document will be updated from the tester's reports;
blank fields are unmeasured, not passes.

| Case | Fault/restart time | ESPHome available | Display action/restoration | RF state A: sent → HA | RF state B: sent → HA | Recovery INFO / errors | Result |
| --- | --- | --- | --- | --- | --- | --- | --- |
| R1 | Startup deferral 08:38:45; final startup retry 08:39:35 | Not separately timed; firmware version available 08:45:28; tester reports about 45 seconds to return | Tester confirms correct display; initial restoration not timed | Not documented separately | Not documented separately | Listening resumed 08:45:49; display recovery 08:46:30; one display warning; DEBUG summaries about 60 seconds apart; no ERROR in excerpts | Startup/display recovery supported; RF-state checks treated as not performed after tester clarification |
| R2 | Reload-related listener interruption 08:50:42; binding-change display warning 08:50:45 | Not separately timed; listening resumed 08:50:46 | Tester confirms display tracked RF; invocation record not supplied | YardStick light 6 → LilyGO/HA accepted 08:52:30 | YardStick fan 6 → LilyGO/HA accepted 08:52:46 | Display recovery 08:52:15; one display warning; no ERROR in excerpts | Reception and display tracking confirmed; direct LilyGO HA Power ON occurred before RX checks, so command-free recovery acceptance remains partial |

The maintainer supplied these results on 2026-11-09. Exact installed source
revision, HA version and time zone were not recorded. Labels were integration
`0.7.0-beta6` and firmware `v0.7.0-debug`. Counts refer to unique supplied records,
not duplicate occurrences in overlapping excerpts. The captured interval spans
about 14 minutes 25 seconds; total test duration was not separately measured.
Original reports and device-specific evidence are preserved privately.

R1's final startup retry and automatic display recovery are established. Its
the tester subsequently indicated that the two-command instruction in step 6
may have been missed. Record that RF check as not performed; do not mark the
whole case passed. No repeat of the long startup outage is requested.

For R2, the listener resumed before the direct HA Power ON, but the actual RF
checks occurred afterward. A HA send can re-establish radio configuration, so
this does not fully isolate command-free reception. Repeat only R2, using two
YardStick commands while leaving the LilyGO fireplace's HA controls untouched.
No LilyGO power operation is part of R2. The Power ON noted above was an
additional action seen in the submitted log, not an instruction in this
protocol. Use two distinct light/fan settings on the transmitting YardStick
profile for the receive check; leave the receiving LilyGO profile untouched.
There is no need to repeat a long outage or add fault types.

If anything fails, preserve the log **before** restarting or reloading anything.
Record the failed step, actual/expected state, timestamps, ESPHome availability,
LilyGO display, any pending transmission or learning activity, and the first
traceback. For a missed RF update, retain any available independent rtl_433 or
PCM evidence so an RF loss can be distinguished from a reconnect failure.
Do not repeat the whole protocol just to reproduce one missing packet.

## Cleanup and completion

1. Save the HA logs and action notebook. Ensure the collection covers HA startup
   and both recovery cases, including DEBUG messages. Use timestamps to avoid
   counting overlapping Docker captures twice.
2. Restore the original logger configuration, packet debug logging, refresh, delayed retransmission
   and automation settings. Apply logging configuration changes with the usual
   HA configuration validation/restart after evidence is saved.
3. Leave LilyGO powered on with USB connected for normal operation. Return the
   test fireplace to its intended final state.

Acceptance for this protocol requires R1 and R2 to pass with their evidence
recorded. Mark blocked or inconclusive steps explicitly; do not substitute a
manual reconnect for automatic recovery. Automated tests cover detailed logging
boundaries, repeated failure injection and recovery with listening disabled;
this hardware protocol focuses on actual HA startup, API/runtime replacement,
display operation and received-state updates.
