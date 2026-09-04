# Issue 15 Extended-Frame Data Collection

Use this procedure to collect evidence for an extended Proflame2 remote whose
ten-word frames do not use the legacy C/D integrity relationship. This is a
diagnostic capture only; do not start guided learning during the procedure.

## Automatic Learning Fallback

Normal guided learning now recognizes an accepted extended RMT frame when it
cannot derive a consistent C/D profile. It automatically enables the packet
debug log at `/config/proflame2_debug.log`, preserves the already accepted
extended captures, and asks for four additional labeled state changes: flame,
light, AUX, and thermostat/pilot/fan mode. If the alternate integrity format
is still not supported, learning fails without creating a fireplace profile
and identifies that log for attachment to the issue report.

The automatic fallback is the preferred first diagnostic path. Use the manual
matrix below when more state coverage is required to reverse engineer the
extended words or their integrity relationship.

## Setup

1. Install firmware containing the Issue 15 RMT pulse path.
2. Enable ESPHome `DEBUG` logging and open the LilyGO device log.
3. Set **Active Listener RX Path** to `rmt_pulse`.
4. Set **Enable Capture** to `rmt_pulse`.
5. Keep the native remote approximately 1-3 ft from the LilyGO.
6. Do not send Home Assistant commands during the capture.

## Capture Matrix

Capture one clean press for every available state transition below. Wait about
two seconds between presses. Record the native-remote button and the fireplace
state before and after each capture.

| Function | Required captures |
| --- | --- |
| Power | Off to on, then on to off |
| Flame | Each available level, including off and high |
| Light | Each available level, including off and high |
| AUX | Off and on while holding power, flame, and light constant |
| Thermostat | Off, normal thermostat, smart thermostat, and at least three setpoints |
| Pilot | Both CPI and IPI states, if the remote supports them |
| Fan | Each available level, including off, if supported |

For every capture, preserve each line containing:

```text
RX RMT pulse capture schema=2 capture_id=...
RX RMT pulse capture discarded reason=...
```

After the final button press, wait about two seconds and set **Enable Capture**
to `off`. Paste the complete log block from enabling through disabling capture.

## Capture Record

For each `capture_id`, provide one row with:

| Field | Value |
| --- | --- |
| Capture ID | ESPHome `capture_id` |
| Native remote button | Exact button pressed |
| Starting fireplace state | Power, flame, light, fan, AUX, thermostat, pilot |
| Expected result | Expected physical change |
| Observed result | Actual physical change |
| PCM bits | `pcm_bits` from the capture line |
| PCM hex | Complete `pcm_hex` from the capture line |

The required evidence is the unedited RMT log, not a manually decoded or
reformatted bitstream. The comparison needs state pairs that change one
function at a time so the extended words and their integrity relationship can
be isolated.
