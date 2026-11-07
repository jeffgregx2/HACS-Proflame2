# Issue #34 hardware validation protocol

Status: H0 and H1 completed by the maintainer on 2026-11-06; H2–H6 pending.
Hardware acceptance is partial. Testing will continue in a few days, starting
with H2. Firmware shutdown work is deferred to v0.8.0. Created 2026-11-06;
results updated 2026-11-06.

Validates the HA integration's offline logging and automatic LilyGO display
recovery described in the [design](issue_34_offline_display_sync_design.md).
**No firmware update is required. Use released beta6 LilyGO firmware when
validating the v0.7.0 integration.** The experimental center-button shutdown aid,
its notification and OTA-confirmation changes are excluded from v0.7.0; see the
[deferred v0.8.0 design](controller_shutdown_design.md).

Install the HA integration containing the fix before testing. A firmware version
or an integration version label alone does not prove that this local fix is
installed; record the installed source revision and any local changes.

## Preparation

1. Use the Test HA instance and its linked Test LilyGO. Leave Production unchanged.
   Record HA version, integration revision, LilyGO firmware version, active
   listening setting and test time zone. Record the fireplace configuration entry
   ID from the logs so messages from different entries are not mixed.
2. Start with a known, synchronized HA state and a visible LilyGO display. Keep
   fireplace power OFF for outage tests. Record the displayed settings; do not
   change them during an outage or its recovery. This tests recovery even when
   the intended display state has not changed.
3. Set refresh to zero, disable optional delayed retransmission, and suspend
   automations targeting this test fireplace. Let previous commands finish.
   Keep other transmitters quiet during the display-only checks. Record settings
   to restore afterward. A later listening check deliberately uses RF commands.
4. Enable **Debug logging** for the Proflame2 integration in HA. Keep it enabled
   throughout each case; disabling it downloads the log. ESPHome firmware DEBUG
   logging and waveform Start/Stop capture are not needed for these tests.
5. Optionally enable **PCM Payload Logging** to identify HA `direction=TX`
   records. An rtl_433 receiver tuned to the configured band provides additional
   RF evidence. Ambient RF by itself does not identify the transmitter.
6. Allow at least 90 seconds of healthy operation before starting. Do not run
   guided learning, edit settings or issue commands during a recovery measurement.

Keep one timed notebook of actions alongside the downloaded HA log. Record
controller power restoration and HA's ESPHome availability separately: boot and
Wi-Fi connection time are outside the integration's 15-second polling interval.
Keep downloaded logs and device-specific results private; do not commit them.

## What to measure

Count only messages beginning with these display synchronization stems for the
selected configuration entry:

| Log message stem | Expected behavior within one failure episode |
| --- | --- |
| `Proflame2 display synchronization interrupted` | One WARNING for an ordinary offline episode. An unexpected exception may separately produce one ERROR with a traceback; preserve it for investigation. |
| `Proflame2 display synchronization still interrupted` | DEBUG summaries no more often than once per 60 seconds. |
| `Proflame2 display synchronization recovered` | One INFO after actual display synchronization and at least 60 seconds of healthy observations, followed by successful confirmation. |

ESPHome's own connection logs, startup synchronization messages and active-listener
interruption messages are separate paths. They do not count against these bounds.
An expected disconnection should not generate display ERROR tracebacks.

Useful operation should resume on an eligible check after the native API is ready,
normally within 15 seconds plus connection/action time. The 60-second stability
window delays the recovery announcement, **not** display restoration or listening.
For an idle, known-state test, flag no display restoration within two minutes of
ESPHome becoming available for investigation. After restoration, allow up to two
minutes for the stable recovery INFO. These are investigation thresholds, not
claims that reconnection always takes that long.

An outage entirely between polling checks may go unobserved. Each offline phase
below must last long enough for HA to report ESPHome unavailable and for at least
one subsequent display check. Do not infer success from power cycling alone.

## H0 — Repeatable linked-entry outage without firmware changes

This provides a reproducible runtime-unavailable fault when a battery prevents
USB removal from turning LilyGO off. It does not establish physical power-loss
or cold-boot behavior.

1. Leave the Proflame2 fireplace entry loaded. Disable only its linked **Test
   LilyGO ESPHome configuration entry** in HA. Do not disable the Proflame2 entry,
   since that would stop the recovery task being tested.
2. Confirm HA reports the linked ESPHome device unavailable. Hold the entry
   disabled for five minutes after the first display interruption warning.
   Expect one display warning and DEBUG summaries no more often than once per
   minute, as described above.
3. Enable the same ESPHome entry again. Do not issue a fireplace command or
   reload Proflame2. Verify automatic display synchronization and one stable
   recovery INFO after the healthy window.
4. Run with active listening OFF and ON, verifying reception after the enabled
   run. Repeat short observed disable/enable cycles using H3's timing to test
   flapping. Record the fault type as **ESPHome entry disabled**, not power loss.

If the linked entry does not become unavailable, preserve the log and report
that observation; do not treat the attempt as a successful outage test.

## H1 — Sustained power loss, listening OFF

With released beta6 firmware there is no controller shutdown gesture. For a
USB-only unit, unplug USB. If a battery keeps it running, use H0 for reproducible
runtime-outage coverage and leave physical power-loss acceptance pending until a
real power interruption is available. No access-point changes are required for
H0. Fireplace Power OFF changes the fireplace state, not controller power.

1. Disable active listening, save settings, and establish the healthy baseline.
2. Fully switch the LilyGO off. Removing USB may leave it powered by its battery;
   verify the controller actually shuts down and HA reports it unavailable.
3. Record the first display interruption warning. Leave the controller offline
   for **five minutes after that warning**. Look for repeated warnings, ERRORs or
   tracebacks and check DEBUG summary spacing.
4. Restore power. Do not issue a HA command, restart HA, reload an integration or
   alter the fireplace settings. Record when ESPHome becomes available and when
   the LilyGO display returns to the recorded HA state.
5. Leave it healthy for at least two minutes after display restoration. Record
   the recovery INFO and verify that it occurs once.

**Pass:** display restores automatically despite unchanged HA state; one ordinary
display interruption warning; bounded DEBUG summaries; one stable recovery INFO;
no RF fireplace command caused by display recovery. This case specifically proves
that recovery does not depend on active listening or a user command.

## H2 — Sustained power loss, listening ON

1. Enable active listening and establish a healthy baseline.
2. Repeat H1 without changing any other configuration during the episode.
3. After stable recovery, use a transmitter matching the learned test profile to
   send three known, distinct states, spaced several seconds apart. Use your
   established safe test arrangement; RF commands using the real fireplace's
   identity can also operate that fireplace. Record transmitted state and HA's
   observed state for each. An independent rtl_433 capture helps distinguish RF
   reception failures from API recovery failures.

**Pass:** H1's display and logging criteria hold, and active listening resumes
automatically. Investigate any missing state with its RF/PCM evidence rather than
assuming that every RF loss is a reconnect defect.

## H3 — Repeated flapping within one episode

Run with listening OFF first, then repeat with listening ON. Saving that setting
starts a separate run; do not edit it during the run.

1. Switch the controller off until HA reports it unavailable and the display
   interruption warning appears.
2. Restore it and wait for ESPHome availability and display restoration. Keep it
   online for only **20–30 seconds after restoration**, then switch it off again.
3. Keep each following offline period long enough for HA to show unavailable and
   a display check to observe it, normally at least 30 seconds after HA's
   unavailable indication. Repeat the restore/short-online/off sequence three
   times. Record the actual times, not just planned delays.
4. Finally restore power and leave the connection healthy for at least two
   minutes after display restoration.

**Pass:** display operation resumes during the short available periods, but no
stable recovery INFO is announced before the final healthy window completes.
The observed interruptions remain one episode with one ordinary warning and
rate-limited DEBUG summaries. The final stable connection produces one recovery
INFO. If an online interval accidentally exceeds the stability window, mark that
run inconclusive for the single-episode criterion and repeat it.

## H4 — A new outage after confirmed recovery

1. Complete H1 or H3 and wait for its recovery INFO.
2. Cause another observable power loss, hold it offline for at least one minute,
   then restore power and wait for stable recovery again.

**Pass:** the new episode gets its own single interruption warning and eventual
single recovery INFO. Suppression must reset after confirmed recovery; the second
outage must neither disappear from the log nor flood it.

## H5 — HA starts while the controller is offline

1. With the controller fully off, restart Test HA. Keep LilyGO off for at least
   two minutes after HA startup to exercise the bounded startup retry path.
2. Confirm the selected fireplace is loaded and preserve the startup messages.
3. Restore LilyGO power without a fireplace command or another HA restart/reload.
4. Wait for display restoration and stable recovery. Run once with listening OFF
   and once with listening ON; for the latter, repeat H2's receive check.

**Pass:** automatic recovery continues after the startup retries have finished.
The display returns, and listening resumes when enabled. Startup messages are
assessed separately from periodic display episode logs. HA restart creates new
tasks, so warning counts from before and after the restart are separate.

## H6 — Replace the linked ESPHome runtime

1. Establish a healthy baseline. Reload only the **linked Test ESPHome entry** in
   HA, leaving the Proflame2 entry loaded. Do not reload both together: that would
   hide a stale-runtime binding problem by replacing the Proflame2 task too.
2. Wait for ESPHome to return and for automatic synchronization/recovery.
3. Repeat with listening disabled and enabled. In the enabled run, verify receive
   operation as in H2 after reconnection.

**Pass:** the existing Proflame2 task uses the returning ESPHome runtime without
requiring a Proflame2 reload or command. Existing display contents alone do not
prove a new display action occurred, because an ESPHome entry reload need not
clear the screen. Preserve the log for review and treat this case as supporting
evidence alongside H1/H2's visible display restoration. The automated transport
tests explicitly verify runtime replacement and the successful update action.

## Optional stress — Connectivity loss without a controller reboot

If existing network controls allow it, temporarily disconnect **only Test
LilyGO** from the local network while leaving it powered. A per-client Wi-Fi
disconnect/block can provide this fault. Blocking Internet access alone does not
disconnect HA's local ESPHome API. A rule that blocks only new connections may
also leave an established API connection intact.

Verify that HA actually reports the selected ESPHome device unavailable, then
repeat H1 and H3's observation/recovery sequence. Restore the original network
setting after the test. If no suitable per-device control exists, mark this case
not run; the power-loss cases remain usable without new equipment or network
changes. Very short interruptions may exercise API races but cannot guarantee a
particular timeout or exception. Unexpected programming exceptions remain covered
by software fault injection rather than a required hardware reproduction.

## Recording results and investigating a failure


Maintain the results in this document as the maintainer supplies reports. Use
one row per run; split listening ON and OFF runs. Keep raw logs, device
identifiers and the original completed notebooks private. Record unmeasured
items explicitly rather than inferring a full pass from selected log excerpts.

The H0/H1 results below come from the maintainer's 2026-11-06 power-cycle
notebook. Times are as reported; the time zone and exact installed source
revision were not recorded. The logged integration label was `0.7.0-beta5`
and firmware label was `v0.7.0-debug`; these labels do not identify the installed
development changes. H0/H1 are recorded as completed, with the evidence limits
below. They do not need to be repeated before proceeding to H2.

| Case / listening | Fault and unavailable times | Available / display-restored times | Warning / ERROR / recovery counts | DEBUG spacing | Unintended TX | Result / log file |
| --- | --- | --- | --- | --- | --- | --- |
| H0 / initial run | Linked ESPHome entry disabled; summaries cover more than five minutes offline | Re-enabled about 16:23:47; recovery INFO 16:25:02; display restoration not separately timed | Initial warning outside excerpt; one recovery INFO shown | Approximately 60 seconds | Not independently measured | Completed by maintainer; bounded logging and automatic recovery supported by notebook |
| H0 / ON | Active listening enabled 16:25:46; sustained runtime outage observed 16:26:31 | Firmware availability logged 16:32:08; settings changed for H1 at 16:32:30 | One display interruption shown for sustained outage; final stable recovery not included before settings change | Display summaries approximately 60 seconds; listener retries continue with backoff up to 30 seconds | Not independently measured | Completed as part of H0 report; post-recovery receive and flapping acceptance remain for later cases |
| H1 / OFF | Power OFF 16:32:45; ESPHome unavailable 16:35:38; display interruption 16:35:45; outage exceeded two hours | Firmware availability 18:43:19; recovery INFO 18:44:32; HA controls returned relatively quickly, not timed | One display warning and one recovery INFO shown; maintainer reports no further entries after several minutes; excerpts are not a full-log count | Approximately 60 seconds | Not independently measured | Completed by maintainer; automatic recovery with listening OFF and bounded logging supported by notebook |
| H2 / ON | | | | | | Pending |
| H3 / OFF | | | | | | Pending |
| H3 / ON | | | | | | Pending |
| H4 | | | | | | Pending |
| H5 / OFF | | | | | | Pending |
| H5 / ON | | | | | | Pending |
| H6 / OFF | | | | | | Pending |
| H6 / ON | | | | | | Pending |
| Optional network loss | | | | | | Not run |

H1's delay between physical power-off and HA reporting ESPHome unavailable was
about three minutes. The display interruption followed about seven seconds
after that unavailable indication. This separates native API failure detection
from the integration's periodic check. The approximately 73-second interval
between availability and recovery INFO includes the intended 60-second healthy
window; it does not measure how long controls or display restoration took.

The maintainer saw no obvious connection/disconnection indication on the LilyGO
display. Automatic display restoration was not separately timed, and the report
does not independently establish the absence of RF transmissions during recovery.
These observations do not currently establish a new defect. Remaining cases
will add listening, flapping, startup and runtime-replacement coverage.

If recovery stalls, preserve evidence **before** issuing a command, reloading or
restarting anything. Record ESPHome availability, HA state, LilyGO display state,
the last successful update time and any pending send/learning activity. A real
uncertain TX request intentionally fences background display work; ordinary
disconnects without such a request must recover automatically. Do not bypass a
send fence to make a test pass.

Disable Debug logging to download the log after the case or uninterrupted group
of cases. Downloads can overlap earlier log content; use recorded time ranges
and entry IDs rather than counting entire files twice. Keep the first traceback
if one occurs. Restore the recorded configuration and network settings afterward.

H1–H6 passing establishes hardware acceptance for the exercised recovery paths;
record optional cases and fault types separately. H0 alone does not prove
cold-boot or physical power-loss recovery.

## Validation status

After the release split, the HA-only `dev` suite passed **2,170 tests**, with
seven external helper/decoder checks skipped and one deselected. Real ESPHome
configuration checks were enabled and passed. Firmware files match the committed
baseline. Hardware acceptance of the v0.7.0 recovery change is partially complete:
the maintainer completed H0 and H1, with the results and evidence limits recorded
above. H2–H6 remain pending. The reported runs used debug firmware; released-beta6
firmware coverage is not established by those version labels. The separate
shutdown observations and possible OTA rollback investigation are preserved in
the v0.8.0 shutdown design and do not establish v0.7.0 recovery acceptance.
