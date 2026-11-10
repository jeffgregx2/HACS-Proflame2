# Remote Identification Records

Record part numbers and FCC IDs independently, exactly as reported on the label.
Do not derive a retail part number or protocol variant from the digits in an FCC
ID. Do not assume a one-to-one relationship between these identifiers; add other
observed pairings as evidence becomes available. FCC IDs are supplementary lookup
aids when the model number is illegible, not guaranteed identifiers for a model.

The README summarizes known models and flags ongoing investigations. These
records retain the supporting identification evidence. An entry here does not
establish reliable control or compatibility with every release.

| Model / reported part number | Seen as FCC ID | Evidence and status |
| --- | --- | --- |
| `0.584.040` | `T99058402300` | Direct label observation reported by the maintainer on 2026-10-27. This is a tested legacy remote. |
| `0.584.043` | TBD | Legacy Power On/Off captures from the [issue #11 report](https://github.com/jeffgregx2/HACS-Proflame2/issues/11#issuecomment-5158248072) are preserved as regression vectors. Wall-mounted remote; FCC ID not confirmed. |
| `0.584.053` | TBD | [Issue #21](https://github.com/jeffgregx2/HACS-Proflame2/issues/21) identifies the CE variant used in Australia. The reporter confirmed operation with v0.7.0-alpha1. That report does not establish seven- versus ten-word framing; protocol type remains TBD. |
| `0.584.080`, type `TMFSLA` on the issue #15 label | `T99058402300` | [Issue #15 label details](https://github.com/jeffgregx2/HACS-Proflame2/issues/15#issuecomment-5425956604) identify a tested extended-frame remote. A [2026-10-31 report in issue #26](https://github.com/jeffgregx2/HACS-Proflame2/issues/26#issuecomment-6539741821) confirms the same part number/FCC-ID pairing on an Enviro Q1, with power, flame, fan and active listening working on v0.7.0-beta3. No new capture or frame-length evidence accompanies that report; other controls were not tested. |
| `0.584.090`, type `GTMFSLA` | `T99058404300` | Mendota remote. Model, type and FCC ID are visible in the [issue #26 label photo](https://github.com/jeffgregx2/HACS-Proflame2/issues/26#issuecomment-6386673559). Control reliability remains under investigation; this is not a confirmed-working entry. |
| Partially legible: `0.584.04...` | `T99058402300` | [Issue #28 report](https://github.com/jeffgregx2/HACS-Proflame2/issues/28#issuecomment-6443554341). Exact part number and protocol variant remain unconfirmed. The reporter initially experienced control failures on v0.6.0 and reported initial success after installing v0.7.0-beta2 and rebuilding with ESP-IDF. |

An audit on 2026-10-27 of all open and closed issue bodies and comments, the
available remote label photo in issue #26, and repository notes found only the
two distinct FCC IDs recorded above. A subsequent direct label report added the
`0.584.040` pairing with the already recorded `T99058402300`. No confirmed FCC-ID
pairing was found in those records for `0.584.043`.
The reported `0.584.053` is the 433.92 MHz CE variant;
its report does not establish an FCC ID. An unrecorded ID is not evidence that
the remote lacks certification or is unsupported.

The [FCC filing for T99058402300](https://fccid.io/T99058402300) lists model
`058402300` and 315 MHz operation. That filing model must not replace a user's
reported retail part number: issue #15 explicitly pairs this FCC ID with
`0.584.080`, and issue #28 reports a surviving `0.584.04...` label prefix.
The direct label observation for `0.584.040` confirms that the same FCC ID is
used on at least two retail models. It therefore cannot distinguish legacy
from extended-frame remotes by itself.

For future support reports, request the full part number, FCC ID where present,
any labeled frequency and type, and a readable label photo. If a label is damaged,
retain the readable prefix and mark the rest unknown. Record firmware/integration
versions and distinguish initial success from sustained hardware testing.

## Transmitted Remote ID

The RF remote ID (`serial_id` in the integration) is a 24-bit value carried in
the first three decoded data words. The integration uses it to distinguish
remote profiles and match received commands. It also contributes to extended
frame integrity calculations.

This is a device/pairing identifier, not a model number. There is no established
mapping from its value or prefixes to a retail model or FCC ID. It is useful
for checking that captures belong to the intended remote and validating frame
integrity, but does not belong in the model compatibility table. Treat it as
device-specific diagnostic information and apply the project's privacy rules
before publishing captures or logs containing it.

A printed type designation, such as `TMFSLA`, is a separate label field and can
be recorded alongside the model number when available.
