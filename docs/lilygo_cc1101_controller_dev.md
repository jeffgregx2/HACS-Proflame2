# LilyGO CC1101 Controller Developer Notes

These notes describe the LilyGO backend at the level needed to implement or
verify a compatible controller. End-user setup is documented in
[LilyGO T-Embed CC1101 Controller](lilygo_cc1101_controller.md).

## Scope

The LilyGO controller is an ESPHome device built around an ESP32-S3 and CC1101.
Home Assistant owns profile storage, command policy, and Proflame2 packet
construction. The firmware is responsible for:

- Emitting the exact transmit bitstream supplied by Home Assistant.
- Capturing CC1101 FIFO byte windows for guided learning.
- Filtering active-listening packets against the learned serial/C/D profile.
- Publishing only validated matching packets to Home Assistant.

Packet layout, ECC behavior, and transmit structure are derived from
[SmartFire](https://github.com/johnellinwood/SmartFire). This project does not
try to be a second independent protocol specification; use SmartFire and
[custom_components/proflame2/rf/waveform.py](../custom_components/proflame2/rf/waveform.py)
as the packet-format references.

## Hardware Defaults

The production ESPHome package configures the controller for:

| Setting | Value |
| --- | --- |
| TX frequency | `314973000` Hz |
| RX frequency | `314973000` Hz |
| Data rate | `2400` bps |
| Modulation | ASK/OOK |
| TX payload bit length | `182` bits |
| TX repeat count | `5` native repeats |
| TX data pin | `GDO0` |
| RX export window | `6000` ms |
| Active-listener scan interval | `1500` ms |
| Active-listener duplicate suppression | `5000` ms |

The firmware treats TX and RX as mutually exclusive radio states. TX has
priority: active listening is paused before transmit and restored afterward if
it was enabled.

## TX Model

Home Assistant sends an already-encoded air payload, payload bit length, and
repeat count. Firmware must not recompute ECC, reinterpret state, or modify the
payload. Its job is to reproduce the RF waveform.

The working LilyGO TX path is `proflame_native_groups` with
`native_remote` timing and `continuous_tx` repeat boundaries. A command is sent
as one RF burst containing five native repeats, not as five independent service
calls. This matches the fireplace behavior and lets `rtl_433` observe one
logical Proflame2 command.

The timing-critical transmit loop is intentionally monolithic. The order of
preload writes, enter-TX, TX-ready waits, scheduled GDO writes, repeat
boundaries, and set-idle calls is RF-visible. Do not refactor the inner timing
loop without scope/rtl_433/fireplace validation.

### TX CC1101 Register Settings

The production TX path configures the CC1101 with these register values for
`314973000` Hz and `2400` bps:

| Register | Value | Purpose |
| --- | --- | --- |
| `IOCFG2` | `0x2E` | Leave GDO2 as high-impedance GPIO-style output. |
| `IOCFG1` | `0x2E` | Leave GDO1 unused. |
| `IOCFG0` | `0x2E` | Let firmware drive GDO0 directly for async OOK data. |
| `FIFOTHR` | `0x47` | Conservative FIFO threshold. |
| `PKTLEN` | `0xFF` | Not used for the async GDO transmit waveform. |
| `PKTCTRL1` | `0x04` | Address/check features disabled for this use. |
| `PKTCTRL0` | `0x32` | OOK packet configuration retained from the validated path. |
| `FSCTRL1` | `0x06` | Frequency synthesizer control. |
| `FSCTRL0` | `0x00` | Frequency offset disabled. |
| `FREQ2` | `0x0C` | Frequency word for `314973000` Hz. |
| `FREQ1` | `0x1D` | Frequency word for `314973000` Hz. |
| `FREQ0` | `0x45` | Frequency word for `314973000` Hz. |
| `MDMCFG4` | `0xF6` | Data-rate exponent/filter setting for 2400 bps TX. |
| `MDMCFG3` | `0x83` | Data-rate mantissa for 2400 bps TX. |
| `MDMCFG2` | `0x30` | ASK/OOK, no Manchester. |
| `MDMCFG1` | `0x22` | Channel spacing/config value from validated path. |
| `MDMCFG0` | `0xF8` | Channel spacing/config value from validated path. |
| `DEVIATN` | `0x00` | No FSK deviation for OOK. |
| `MCSM1` | `0x30` | State-machine behavior used by validated TX. |
| `MCSM0` | `0x18` | Calibration behavior used by validated TX. |
| `FOCCFG` | `0x16` | Frequency offset compensation disabled/low impact. |
| `BSCFG` | `0x6C` | Bit synchronization config from validated path. |
| `AGCCTRL2` | `0x43` | AGC config from validated TX path. |
| `AGCCTRL1` | `0x40` | AGC config from validated TX path. |
| `AGCCTRL0` | `0x91` | AGC config from validated TX path. |
| `FREND1` | `0x56` | Front-end TX/RX config from validated path. |
| `FREND0` | `0x11` | OOK PA table entry selection; `PA_POWER=1`. |
| `FSCAL3` | `0xE9` | Frequency synthesizer calibration. |
| `FSCAL2` | `0x2A` | Frequency synthesizer calibration. |
| `FSCAL1` | `0x00` | Frequency synthesizer calibration. |
| `FSCAL0` | `0x1F` | Frequency synthesizer calibration. |
| `TEST2` | `0x81` | CC1101 test register from validated path. |
| `TEST1` | `0x35` | CC1101 test register from validated path. |
| `TEST0` | `0x09` | CC1101 test register from validated path. |
| `PATABLE[0]` | `0x00` | Carrier off. |
| `PATABLE[1..7]` | `0xC6` | Carrier on. |

The PA table is not optional. If every PATABLE entry is nonzero, "off" symbols
become continuous carrier and the fireplace will not see the intended OOK
waveform.

## RX Model

The working RX path uses the CC1101 as an OOK slicer and FIFO byte source. The
firmware drains raw FIFO bytes into a rolling memory window, then scans that
window for Proflame2 candidates. Accepted active-listening packets must match
the learned serial/C/D profile before they are sent to Home Assistant.

The abandoned RX path used GDO edge-interval capture and attempted to infer
packet ownership from repeated edge windows. It produced repeat/burst structure
but did not produce stable packet-owned windows across fan and flame datasets.
Do not resurrect that design as the normal RX strategy.

### RX CC1101 Register Settings

The active FIFO capture/listener path uses this production register set for
`314973000` Hz and `2400` bps:

| Register | Value | Purpose |
| --- | --- | --- |
| `IOCFG2` | `0x2E` | GDO2 unused for FIFO RX. |
| `IOCFG1` | `0x2E` | GDO1 unused. |
| `IOCFG0` | `0x2E` | GDO0 unused for RX edge ownership. |
| `FIFOTHR` | `0x47` | FIFO threshold used by polling drain loop. |
| `SYNC1` | `0x00` | Sync disabled. |
| `SYNC0` | `0x00` | Sync disabled. |
| `PKTLEN` | `0xFF` | Fixed-length byte window source; software scans offsets. |
| `PKTCTRL1` | `0x00` | Address/status filtering disabled. |
| `PKTCTRL0` | `0x00` | Fixed packet mode, CRC/whitening disabled. |
| `FSCTRL1` | `0x06` | Frequency synthesizer control. |
| `FSCTRL0` | `0x00` | Frequency offset disabled. |
| `FREQ2` | `0x0C` | Frequency word for `314973000` Hz. |
| `FREQ1` | `0x1D` | Frequency word for `314973000` Hz. |
| `FREQ0` | `0x45` | Frequency word for `314973000` Hz. |
| `MDMCFG4` | `0x56` | Wide RX bandwidth with 2400 bps exponent. |
| `MDMCFG3` | `0x83` | Data-rate mantissa for 2400 bps. |
| `MDMCFG2` | `0x30` | ASK/OOK, no Manchester, no sync requirement. |
| `MDMCFG1` | `0x02` | Channel spacing/config value from rfcat-like RX. |
| `MDMCFG0` | `0xF8` | Channel spacing/config value from rfcat-like RX. |
| `DEVIATN` | `0x00` | No FSK deviation for OOK. |
| `MCSM1` | `0x3F` | Stay in RX/FIFO-friendly state-machine behavior. |
| `MCSM0` | `0x18` | Calibration behavior used by validated RX. |
| `FOCCFG` | `0x17` | rfcat-like frequency offset compensation setting. |
| `BSCFG` | `0x6C` | Bit synchronization config from validated RX. |
| `AGCCTRL2` | `0x03` | rfcat-like slicer/AGC threshold behavior. |
| `AGCCTRL1` | `0x40` | AGC config from validated RX path. |
| `AGCCTRL0` | `0x91` | AGC config from validated RX path. |
| `FREND1` | `0xB6` | Wide-bandwidth front-end setting. |
| `FREND0` | `0x10` | RX front-end/OOK setting from rfcat-like profile. |
| `FSCAL3` | `0xE9` | Frequency synthesizer calibration. |
| `FSCAL2` | `0x2A` | Frequency synthesizer calibration. |
| `FSCAL1` | `0x00` | Frequency synthesizer calibration. |
| `FSCAL0` | `0x1F` | Frequency synthesizer calibration. |
| `TEST2` | `0x88` | Wide-bandwidth RX test register. |
| `TEST1` | `0x31` | Wide-bandwidth RX test register. |
| `TEST0` | `0x09` | CC1101 test register from validated RX path. |

These values are intentionally close to the rfcat/YardStick receive model:
disable sync/Manchester/CRC assumptions, accept a byte window, and let software
try bit offsets and candidate boundaries. A single register change can turn
valid Proflame2 traffic into all-`0xFF`/all-`0x00` noise or unstable candidate
windows.

## Active Listening

Active listening is enabled by default for LilyGO entries. The firmware performs
enough decode work to reject noise and non-matching serial/C/D packets locally.
This keeps Home Assistant from processing every FIFO window.

The production behavior is:

- Maintain a rolling FIFO byte buffer.
- Scan about every `1500` ms after the initial `6000` ms window is available.
- Suppress repeated packets from the same transmission for `5000` ms.
- Publish only decoded packets matching the learned profile.
- Count dropped packets and expose additional debug counters/snapshots when
  debug support is enabled.

Idle/no-candidate RF noise should not be treated as a dropped Proflame2 packet.
It may be logged at a suppressed diagnostic cadence, but it should not flood
Home Assistant or increment the production dropped-packet counter.

## Validation Expectations

After firmware or register changes, validate at least:

- LilyGO TX is accepted by the fireplace.
- LilyGO TX is decoded by `rtl_433` as one logical command.
- Native remote changes are received when active listening is enabled.
- TX still works after active listening has been enabled.
- Active listening resumes after TX.
- Home Assistant remains responsive and no tight retry/scan loop is introduced.

For deeper RF validation, compare the current implementation against
[esphome/components/proflame2_tembed/README.md](../esphome/components/proflame2_tembed/README.md),
which preserves the detailed scope-derived timing notes.
