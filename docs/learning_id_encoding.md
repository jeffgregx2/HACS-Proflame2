# Learning ID Encoding

## Availability And Lifecycle

Each configured Proflame2 fireplace exposes an enabled-by-default, read-only
**Learning ID** sensor in its Home Assistant device's Diagnostic section.
The integration generates the ID from the loaded remote profile on every startup
or integration reload. It does not persist or restore a cached ID. No ESPHome
firmware update is required.

A Learning ID describes the specific native remote, not the HA installation or
the features the user enabled. Save it for future setup of that remote. Changing
fan, light, front-burner, auxiliary, or CPI feature choices does not change the ID.
A different native remote requires its own ID, even if it has the same model number.

If export fails validation, the sensor reads `unknown` and a warning is logged
without including profile values. Existing control continues; the next load
retries generation. The ID is excluded from the runtime diagnostic export, but
HA's recorder or state logging can still retain entity states.

Choose **Enter a Learning ID** during setup, paste the complete ID, and select a
name and controller. Next, choose supported features for this fireplace. Outer
whitespace from copying is trimmed; the ID itself must be canonical. Only the
decoded remote fields are stored, not the token. Refresh interval defaults to
zero and is configured separately, as are feature choices and active listening.

During development, regenerate experimental IDs after a schema revision by
reloading the integration and copying the sensor value. Existing learned
fireplace entries do not need to be relearned. Unreleased schema revisions keep
the same format version; version increments apply after a format is released.

See [Using Learning IDs](add_fireplace_profile.md#Using_learning_ids) and
[Using rtl_433 for learning](Using_rtl_433_for_learning.md) for user instructions.

## Text Format

```text
PF2-1-L-<Base32(payload || checksum)>   legacy seven-word remote
PF2-1-E-<Base32(payload || checksum)>   extended ten-word remote
```

`PF2` identifies Proflame2; `1` is the exchange-format version, not the firmware
version. `L` and `E` distinguish the radio protocol. The prefix is eight ASCII
bytes, including the final hyphen. Unknown versions and protocol markers are rejected.

The body is RFC 4648 Base32 (`A-Z`, `2-7`), uppercase, with no `=` padding. It is
packed binary, not JSON or a live RF packet. Legacy IDs contain 15 body characters
(23 total); extended IDs contain 18 body characters (26 total). Unused low bits
in the last Base32 symbol must be zero: three for legacy and two for extended.
Lowercase, extra whitespace, padding, and noncanonical encodings are rejected.

## Binary Layout

Offsets are zero-based in the decoded body. Multi-byte values are unsigned and
big-endian. There is no feature byte or reserved feature-flags field.

### Legacy Payload

| Offset | Size | Contents |
|---|---|---|
| 0 | 3 bytes | Remote ID |
| 3 | 1 byte | `(C1 << 4) \| D1` |
| 4 | 1 byte | `(C2 << 4) \| D2` |
| 5 | 4 bytes | CRC32 checksum |

Each C/D constant fits in a nibble (`0-15`). The payload is five bytes; the full
decoded body including checksum is nine bytes.

### Extended Payload

| Offset | Size | Contents |
|---|---|---|
| 0 | 3 bytes | Remote ID |
| 3 | 1 byte | Fixed W4 flags (`w4_base & 0x8C`) |
| 4 | 1 byte | W6 template value |
| 5 | 1 byte | W7 template value |
| 6 | 1 byte | W8 template value |
| 7 | 4 bytes | CRC32 checksum |

The payload is seven bytes; the full decoded body including checksum is eleven
bytes. Live power, thermostat, and light bits are removed from W4. W5 comes from
the requested command; W9/W10 are recomputed using the remote ID and command and
template words. Legacy C/D constants are unused for extended profiles. W7 is a
stored manual-transmit template value, not a current sensor reading.

### Checksum

Append four bytes containing CRC-32/ISO-HDLC, as returned by Python `zlib.crc32`:

```python
checksum = zlib.crc32(prefix.encode("ascii") + payload).to_bytes(4, "big")
```

Include the complete prefix to protect protocol and version selection. Base32
encode the payload followed by its checksum. This checksum detects accidental
corruption; it neither authenticates the ID nor replaces RF integrity words.

## Worked Examples

These use synthetic remote ID `0x123456`, not a captured device identifier.

| Type | Payload hex, excluding CRC | Learning ID |
|---|---|---|
| Legacy: C1=5, D1=7, C2=1, D2=8 | `1234565718` | `PF2-1-L-CI2FMVYYTNVRS7Q` |
| Extended: W4=0x80, W6=0x42, W7=0xC8, W8=0x19 | `1234568042c819` | `PF2-1-E-CI2FNACCZAMYKTI4W4` |

`protocol/learning_id.py` implements the codec. Its `RemoteProfile` return value
uses model-default features only as a placeholder; features are not decoded from
the ID. HA collects actual choices in its setup form. Tests verify invariant IDs
across all feature combinations and preservation of RF frames once the caller
supplies the same local feature configuration.

## Scope And Privacy

The ID contains enough information to reproduce the remote's manual commands.
It is not encrypted and should be kept private unless deliberately shared.

It excludes feature choices, current fireplace settings, presets, friendly names,
controller/backend selection, HA entry/device IDs, network details, credentials,
and RF band. Importing it does not configure the controller's band or guarantee
fireplace reception. The format does not enable thermostat control.
