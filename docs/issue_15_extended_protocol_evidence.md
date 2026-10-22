# Issue 15 Extended Protocol Evidence

This document records reproducible observations for the Issue #15 SIT TMFSLA
extended-frame investigation. It is not a protocol specification. Claims not
backed by a committed fixture or cited source remain hypotheses.

## Baseline

| Field | Value |
| --- | --- |
| Date recorded | 2026-09-05 |
| Repository commit | `b091b6d219ae0fb167d8f17a11e9fd318e8db1e5` |
| Python | `Python 3.14.4` (`./.venv/bin/python`) |
| Source issue | [Issue #15](https://github.com/jeffgregx2/HACS-Proflame2/issues/15) |
| Source capture comment | [Alex's controlled A-I capture](https://github.com/jeffgregx2/HACS-Proflame2/issues/15#issuecomment-5553787513) |
| Fixture | `tests/fixtures/issue_15_tmfsla_extended_capture.json` |

## Reproduction Commands

Run from the repository root:

```bash
PYTHONPATH=. ./.venv/bin/python -m pytest tests/test_issue_15_extended_protocol.py
```

The test reads each packed PCM row from the fixture, limits it to its reported
`pcm_bit_length`, and calls `find_proflame_pcm_candidates()`.

Generate the durable baseline analysis artifact:

```bash
PYTHONPATH=. ./.venv/bin/python tools/proflame_extended/analyze_issue_15.py \
  --output artifacts/issue_15_extended/analysis-baseline.json
```

The committed artifact is checked against a fresh analyzer run by
`test_issue_15_committed_analysis_artifact_is_current`.

## Baseline Results

| Result | Evidence |
| --- | --- |
| 91 259-bit rows decode once as `extended_10_word_truncated_end_guard`. | Fixture capture IDs 15-108 excluding 21, 37, and 50. |
| Each accepted row decodes to the reporter-supplied ten-word sequence. | `test_issue_15_all_259_bit_rows_decode_to_reported_ten_words`. |
| Capture IDs 21, 37, and 50 are 260-bit anomalies and have no structurally valid candidate. | `test_issue_15_260_bit_anomalies_remain_rejected`. |
| The current legacy assignment `W4/W5/W6/W7 -> Cmd1/Cmd2/Err1/Err2` cannot produce a stable C/D profile. | `test_issue_15_legacy_cd_assignment_cannot_explain_extended_rows`. |
| The state words exercise only 14 independent input-bit changes. `W9` and `W10` admit affine fits for the observed samples, but the fits are underdetermined. | `artifacts/issue_15_extended/analysis-baseline.json`. |

## Known Field Correlations

The following claims come from the controlled state labels and decoded words in
the fixture. They are correlations, not a complete protocol definition.

| Words | Observed correlation | Fixture examples |
| --- | --- | --- |
| `W1-W3` | Remote serial identifier `08E905`. | All accepted captures. |
| `W4` | Power, light, thermostat mode, and pilot-related state. | 15-20, 47-60, 73-89, 97-104. |
| `W5` | Flame and AUX state. | 22-44, 64-68. |
| `W6` | Requested thermostat setpoint: `0x15` through `0x18` correspond to 21 through 24 C. | 73-88. |
| `W7` | Changes from `F5` to `FA` without an accompanying visible state change. | 71-72. |
| `W8` | `00` in the present capture set. | All accepted captures. |
| `W9-W10` | Interleaved integrity bytes for `W5/W7` and `W4/W6/W8`, respectively. | All 91 raw-backed accepted captures. |

## Integrity Hypothesis Results

The analyzer evaluates only the captured data. A relationship that has no
conflicts in this table is not automatically a protocol rule outside the
observed state combinations.

| Hypothesis | Result | Limitation |
| --- | --- | --- |
| `W9` is determined by observed state words | The smallest conflict-free observed input set is `W5/W7`. | The capture set contains only two `W7` values. |
| `W10` is determined by observed state words | The smallest conflict-free observed input set is `W4/W6`. | Not every possible `W4/W6` combination exists in the fixture. |
| One state word is a legacy C/D command and `W9` or `W10` is its error byte | Rejected for every direct `W4-W8` to `W9/W10` assignment, and for all 200 two-nibble bytes composed from `W4-W8`. | This does not rule out a different multi-word construction. |
| XOR or 8-bit sum of the observed input pair | No candidate matches every accepted row. | A few individual rows can match by coincidence. |
| Common named CRC-8 variants | No candidate matches every accepted row. | This does not rule out a different CRC width, byte order, or a non-CRC integrity function. |
| General affine fit from `W4-W7` | Both tail bytes fit the observed rows. | Only 14 independent input-bit changes exist; the fit is underdetermined and cannot encode new states. |

The integrity construction below is accepted for extended-frame receive
validation. Later controlled captures establish `W7` as remote ambient-
temperature telemetry, encoded in tenths of a degree Celsius and quantized in
half-degree increments. Manual fireplace control does not require Home
Assistant to model that telemetry, so the beta encoder uses a fixed 20.0 C
value (`W7=0xC8`). Native thermostat mode remains out of scope.

## Candidate Alternating-Word Integrity Lanes

Alex proposed a specific multi-word construction using the legacy transform
with C/D constants set to zero:

```text
T(value) = build_err_byte(value, 0, 0)
W9  = T(T(W5)) ^ T(W7) ^ 0x23
W10 = T(T(T(W4))) ^ T(T(W6)) ^ T(W8) ^ 0x65
```

The equivalent recursive lane form is:

```text
ecc = 0
for value in lane:
    ecc = T(ecc ^ value)
return ecc ^ final_xor
```

The analyzer and raw-PCM regression test independently verify both lanes for
all 91 accepted rows in the committed A-I fixture. Each lane derives exactly
one final XOR value: `0x23` for `W9` and `0x65` for `W10`.

For Issue #15 implementation, the construction is accepted as the
extended-frame receive integrity algorithm. The older issue comment containing
manually transcribed `W7=F0` rows has two `W10` exceptions:

| Frame | Transcribed `W10` | Candidate `W10` |
| --- | --- | --- |
| `08 E9 05 D1 06 15 F0 00 B6 E4` | `E4` | `E6` |
| `08 E9 05 E1 06 15 F0 00 B6 BF` | `BF` | `99` |

That comment does not include the source `pcm_hex` lines for those rows. They
are treated as transcription errors for the current implementation, but should
be resolved if raw evidence becomes available. The latest claim of additional
`W7=E1` observations also needs raw capture provenance before it is included in
the fixture. This integrity construction validates a frame. The controlled
temperature captures resolve `W7` sufficiently for manual-mode beta
transmission; the implementation deliberately emits `W7=0xC8` rather than
attempting native thermostat behavior. See
`issue_15_extended_protocol_design.md` for the implemented data contract and
limits.

## Independent Source Review

| Source | Revision inspected | License | Result |
| --- | --- | --- | --- |
| [rtl_433 Proflame2 decoder](https://github.com/merbanan/rtl_433/blob/cd6d4154e0198bd53ba7f3795622384c233a9ccc/src/devices/proflame2.c) | `cd6d4154e0198bd53ba7f3795622384c233a9ccc` | GPL-2.0-or-later | Decodes only seven words and does not validate its reported integrity fields. It confirms legacy RF framing and state-bit layout, but not this extended format. |
| [j2deen/proflame2-esp protocol notes](https://github.com/j2deen/proflame2-esp/blob/4cde4d28b7e4bccef09ad3a63ffaa00bf54aea60/docs/PROTOCOL.md) | `4cde4d28b7e4bccef09ad3a63ffaa00bf54aea60` | No repository license declared | References FCC ID `T99058402300`, but specifies only the legacy seven-word C/D layout. It provides no ten-word integrity algorithm. |
| [johnellinwood/smartfire](https://github.com/johnellinwood/smartfire) | repository default branch inspected 2026-09-05 | GPL-3.0 | Original source for the legacy C/D formula. It does not establish support for the captured extended layout. |

These sources corroborate the legacy format already implemented in this
project. They do not justify copying code or inferring an extended encoder.

## Open Questions

1. Does the fireplace consume `W7` in direct manual-control mode, or only when
   the remote's thermostat modes are selected?
2. Are the 260-bit rows capture artifacts or another valid frame boundary?
3. Which extended controls beyond manual power/flame/fan/light require distinct
   state words or a native thermostat model?
