# Measured Proflame2 Waveform Notes

These are bounded development observations, not a complete protocol specification
or a change to production RX/TX behavior. Keep native behavior, decoder acceptance
policy and measurement-system artifacts distinct. Existing packet/ECC references
remain authoritative for their documented scope.

## Evidence Scope

Evidence alias `WAVE-NATIVE-02`: one known legacy seven-word remote, configured
for the 315 band, manual operation with thermostat disabled. An SDR recording and
a passive CC1101/RMT receiver observed the same Light/Fan sequences. Private raw
recordings, archive hashes, derived events and comparison reports are retained
outside Git. No physical acknowledgement from a fireplace is inferred.

Other remote models, extended frames, other bands and thermostat behavior have
not been characterized by this timing sample. Do not generalize its timing range
into a universal minimum, maximum or guaranteed tolerance.

## Observed Repeat Gaps

The SDR structural review found **11 or 12 additional low PCM bit periods after
the 182-bit legacy core** in 72 decodable inter-repeat boundaries: 45 at 11 bits
and 27 at 12 bits. The core includes the low half of its final end guard. Thus
these extra bits are not the entire continuous low pulse between the last high
edge of one copy and the first high edge of the next.

At the nominal 2400 bit/s rate, the additional 11-12 periods correspond to about
4.6-5.0 ms. Including that final low half-bit gives approximately 5.0-5.4 ms of
continuous low level. Quantized bit counts and these nominal conversions are not
independent measurements of oscillator accuracy or exact edge tolerances.

The production seven-word PCM scanner currently requires at least **18 zero
bits after the core**, except for its specifically supported terminal truncation.
That is an implementation acceptance rule, **not a demonstrated native protocol
minimum**. It can undercount structurally intact native repeats in this sample.
The analysis-only structural reviewer validates word guards, Manchester symbols,
marker bits and parity without requiring that operational repeat-gap rule.
Integrity must be checked separately. Structural results must not enter learning
or control as accepted semantic frames merely because this reviewer found them.

Zero gap counts at an SDR event boundary are censored by the detector ending the
event at the last high pulse. They are not observations of zero on-air spacing.

## Other Timing Observations

- Four clean SDR events each contain five structurally intact copies. Their
  first-to-last-edge burst spans are approximately 396.6-396.9 ms; adjacent copy
  start intervals range from 80.325 to 80.593 ms in those events. This supports a
  five-copy native burst for this remote, not a universal timing requirement.
- Two identical final Light-state bursts begin about 15.906 seconds apart.
  Keep this separate from the repeats *within* one burst and from the LilyGO's
  optional delayed retransmission. Payload equality alone does not identify the
  physical transmitter or establish a universal remote retry timer.
- Observed RMT-versus-SDR short-pulse width offsets depend strongly on SDR
  detector threshold. They must not become protocol tolerances or TX corrections
  without independent measurement calibration.
- Missing leading RMT coverage and acquisition/export gaps describe the capture
  system, not the native protocol. A retained 75 ms sum of prior measurements
  does not guarantee 75 ms of contiguous RF coverage before a trigger.

## Protocol Update Inventory

### Subsequent Three-Source Pilot

Evidence alias `WAVE-COMPARE-03` compares the same legacy command states from
the native remote, a LilyGO identified as v0.6.0 and a LilyGO whose startup log
identifies v0.7.0-beta3. The latter has delayed retransmission enabled. Original
private artifacts, manifest hashes, replay sources and derived metrics are
retained. These are pilot distributions, not validated fireplace timing limits.

- Tested seven-word command sets match across all three sources.
- Median received frame-start spacing is approximately 80.47 ms native and
  80.74 ms for both LilyGO versions. Median repeat LOW runs are about 5.25 ms
  for all three. This does not demonstrate a beta-specific schedule regression.
- In the SDR structural-frame interiors, short HIGH pulse p05-p95 is 380-389 us
  native, versus 368-422 us for both LilyGO versions. The LilyGO widths cluster
  near 369/421 us; longer HIGHs also have two clusters about 52 us apart.
  This distinction is observed independently of RMT, but detector/signal-level
  effects and transmitter behavior must be separated before assigning a cause.
- The beta3 run has 24 identical-command burst pairs, with median 2.437 seconds
  start-to-start and 2.039 seconds from earlier RF end to later RF start. This
  is consistent with its configured delayed repeat, not an independent guarantee
  of sender request identity or fireplace acceptance.

Do not change TX constants to match these received medians. Independent timing
calibration and outcome-linked testing remain open; the follow-up below addresses
detector sensitivity. Raw retention is distinct from structural decoder acceptance.

### Clustering Audit Follow-Up

Offline audit of 99 SDR bursts (27 native, 24 v0.6.0, 48 beta3) strongly supports
CC1101 asynchronous-input sampling as the source of the dominant LilyGO grid.
TI documents eight input samples per programmed data-rate period in section 27.1
of the [CC1101 datasheet](https://www.ti.com/lit/ds/swrs061f/swrs061f.pdf).
The source's 2400-bps register calculation predicts a 52.106-us tick; all 72
LilyGO bursts fit approximately 52.109 us with high edge-phase coherence. Native
bursts do not support that clock fit. Independent raw-power thresholds preserve
the distinction, so this is not solely an RMT or rtl_433 filtering artifact.

The recordings also reveal two limitations/leads. All LilyGO bursts heavily
clip the SDR's I/Q range, preventing a trustworthy physical amplitude/edge-shape
comparison. Their dominant carrier is about 60 kHz below the native reference
in the shared, uncalibrated SDR measurements. Both findings occur in v0.6.0 and
beta3. No fireplace acceptance limit or causal failure mechanism is established.
Reduce acquisition gain before further envelope comparison; do not change TX
defaults from this single-remote sample. Any future modem-rate experiment must
keep logical symbol timing and RX configuration independent.

### Reduced-Gain Verification

A subsequent beta3 capture at confirmed 0.9-dB manual SDR gain contains 24
unclipped bursts, each with five structurally decoded copies (120 total).
Independent half-power envelope measurements and five threshold controls retain
the approximately 52.109-us timing grid. Its carrier remains about 59.8 kHz below
the earlier native reference in relative SDR measurements. This rules out
clipping as the explanation for these two findings in this sample, but does not
calibrate absolute frequency or prove either difference causes rejected commands.
The native comparison was recorded earlier; oscillator drift remains an explicit
limitation. Raw receiver edge timing still needs separate calibration.
Keep modem-rate and TX-only carrier experiments independent, default-disabled,
and outcome-tested before changing production RF settings.

Maintain this inventory as new comparisons arrive. Each promotion into a protocol
description needs a definition, evidence scope, artifact reference, uncertainty,
and regression coverage. Contradictory observations should remain visible.

| Finding | Documentation disposition | Implementation disposition |
| --- | --- | --- |
| 11-12 additional low bits between native legacy copies | Documented above as observed behavior, not a universal bound | Assess the 18-bit acceptance rule separately; do not change production during timing analysis |
| Five copies and approximately 80.4 ms copy starts | Document as this remote's measured burst structure | Compare native and LilyGO whole bursts before proposing timing changes |
| Delayed duplicate around 15.9 seconds | Document as an observation distinct from intra-burst repeats | More samples needed before specifying a retry timer |
| 182-bit core versus terminal 181-bit RMT capture | Keep native encoding separate from acquisition truncation rules | Preserve existing terminal-boundary regressions |
| Threshold-sensitive high/low width offsets | Record measurement uncertainty, not a protocol discrepancy | Require calibration/sensitivity analysis before TX correction |
| Leading RMT loss and non-contiguous history | Record in acquisition design, not as a native waveform rule | Investigate receiver acquisition/rearm separately |
| Similar v0.6.0/beta3 timing but approximately 52-us-separated LilyGO SDR pulse clusters | Record as a bounded transmitter/detector comparison, not a protocol requirement | Audit radio configuration and threshold sensitivity before proposing timing corrections |
| Async sampling clock explains dominant TX clusters; SDR is clipped and carrier differs from native | Distinguish hardware behavior and measurement limits from native protocol requirements | Verify reduced-gain IQ before separate modem-rate/carrier experiments; no production defaults changed |

The repeat-gap regression uses native Fan-6 command/integrity bytes with an
anonymized serial and synthesized nominal timings. It tests 11/12-bit gaps beside
18/24-bit gaps and confirms that the debug reviewer and operational decoder have
different acceptance policies. It is not an archived raw waveform or proof that
a synthetic transmission is accepted by a fireplace.
