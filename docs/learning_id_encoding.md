# Learning ID Encoding

## Availability And Lifecycle

Each configured Proflame2 fireplace exposes an enabled-by-default, read-only
**Learning ID** sensor in its Home Assistant device's Diagnostic section.
The integration generates the ID from the loaded remote profile on every
startup or integration reload. It does not persist or restore a cached ID.
Changes to the encoder therefore take effect on the next load without relearning.
No ESPHome firmware update is required.

If profile export fails validation, the sensor reads `unknown` and a warning is
logged without including profile values. Existing control continues; the next
load retries generation. The ID is not added to the runtime diagnostic export.
Like other HA entity states, it can still be recorded by Home Assistant's recorder
or included in state logs when state logging is enabled.

To copy a profile, choose **Enter Learning ID** when adding a fireplace, paste
the complete ID, and choose a name and controller. Both legacy and extended
profiles are supported. Outer whitespace from copying is trimmed; the ID itself
must use the canonical encoding below. Only the decoded profile fields are
stored, not the supplied ID string. Feature flags are restored from the ID and
can subsequently be changed through **Edit supported features**.

For older records containing a remote ID and C/D values, use **Enter legacy
values** in the setup menu instead. This path supports legacy profiles only.
The companion capture tool can use the same pure Python codec without depending
on HA storage; it is a separate follow-up to the import/export UI.

## Text Format

```text
PF2-1-L-<Base32(payload || checksum)>   legacy seven-word profile
PF2-1-E-<Base32(payload || checksum)>   extended ten-word profile
```

`PF2` identifies Proflame2; `1` is the exchange-format version, not a firmware
version. `L` and `E` visibly distinguish the radio protocol. These prefixes are
eight ASCII bytes long, including the final hyphen.

The body uses RFC 4648 Base32 (`A-Z`, `2-7`), uppercase and without `=` padding.
This is packed binary, not JSON, compressed JSON, or a copy of a live RF frame.
Version 1 accepts only canonical text: no whitespace, lowercase, alternative
padding, or nonzero unused Base32 bits. Unknown versions/types are rejected.

Legacy IDs contain 16 body characters, **24 characters total**. Extended IDs
contain 20 body characters, **28 characters total**. Extended Base32 encoding
has four unused low bits in its final symbol; these must be zero.

## Binary Layout

Offsets are zero-based within the decoded body. Multi-byte integers are
unsigned, most-significant byte first. The profile's remote ID is a 24-bit value.

### Legacy Payload

| Offset | Size | Contents |
|---|---|---|
| 0 | 3 bytes | Remote ID |
| 3 | 1 byte | Supported-feature flags |
| 4 | 1 byte | `(C1 << 4) \| D1` |
| 5 | 1 byte | `(C2 << 4) \| D2` |
| 6 | 4 bytes | CRC32 checksum |

Each C/D value must fit in one nibble (`0-15`). Legacy payloads are six bytes;
the decoded body including checksum is ten bytes.

### Extended Payload

| Offset | Size | Contents |
|---|---|---|
| 0 | 3 bytes | Remote ID |
| 3 | 1 byte | Supported-feature flags |
| 4 | 1 byte | Fixed W4 flags (`w4_base & 0x8C`) |
| 5 | 1 byte | W6 template value |
| 6 | 1 byte | W7 template value |
| 7 | 1 byte | W8 template value |
| 8 | 4 bytes | CRC32 checksum |

Extended payloads are eight bytes; the decoded body including checksum is twelve
bytes. Live power, thermostat, and light bits are removed from W4. W5 is generated
from the requested command; W9/W10 are recomputed by the extended integrity
algorithm using the remote ID and command/template words. Legacy C/D constants
are not used or stored for extended profiles; the decoder supplies zeroes for
the model's unused ECC fields. W7 is the stored manual-transmit template value,
not an automatically sampled current temperature.

### Feature Flags

| Bit | Feature |
|---|---|
| 0 | Fan |
| 1 | Light |
| 2 | Front burner |
| 3 | Auxiliary |
| 4 | CPI/pilot |
| 5-7 | Reserved; must be zero |

These flags describe the configured capabilities, not the current on/off states.
For example, fan and light enabled with all other features disabled is `0x03`.

### Checksum

The four checksum bytes are the unsigned CRC-32/ISO-HDLC result (as returned by
Python `zlib.crc32`), stored big-endian:

```python
checksum = zlib.crc32(prefix.encode("ascii") + payload).to_bytes(4, "big")
```

Include the entire eight-byte text prefix, so protocol/version changes are also
protected. Encode the payload followed by this checksum in Base32. This checksum
is separate from the RF integrity words and detects accidental corruption; it
does not authenticate the ID.

## Worked Examples

These are synthetic profiles, not captured device identifiers. Both use remote
ID `0x123456` and feature byte `0x03`.

| Type | Payload hex, excluding CRC | Learning ID |
|---|---|---|
| Legacy: C1=5, D1=7, C2=1, D2=8 | `123456035718` | `PF2-1-L-CI2FMA2XDBVBZFK7` |
| Extended: W4=0x80, W6=0x42, W7=0xC8, W8=0x19 | `123456038042c819` | `PF2-1-E-CI2FMA4AILEBTPLKVK4Q` |

`protocol/learning_id.py` implements encoding and strict decoding. Tests lock
these vectors and verify that decoding preserves generated manual RF frames for
both protocols, including all flame/fan/light combinations.

## Scope And Privacy

The ID contains the data necessary to reproduce the profile's manual commands.
It is **not encrypted** and should not be posted publicly without considering
that exposure. Normal integration logs do not intentionally print the ID.

It excludes current fireplace settings, saved presets, friendly names, controller
and backend selection, HA entry/device IDs, network addresses and credentials.
It also excludes the RF band: the LilyGO band is configured globally in firmware,
not per fireplace. Importing a profile will not configure a controller's frequency
or guarantee hardware compatibility. The format does not enable thermostat control.
