# Issue 15 Extended-Frame Analysis Tool

`analyze_issue_15.py` is an offline, deterministic analysis tool for the
committed Issue #15 TMFSLA capture fixture. It does not contact Home Assistant,
ESPHome hardware, GitHub, or any radio device.

Run it from the repository root:

```bash
PYTHONPATH=. ./.venv/bin/python tools/proflame_extended/analyze_issue_15.py \
  --output artifacts/issue_15_extended/analysis-baseline.json
```

The output records capture counts, observed word values, controlled consecutive
word deltas, direct legacy C/D tests, simple checksums, named CRC-8 tests, the
smallest observed input-word sets for `W9` and `W10`, and affine-fit limits.

An affine fit is not an encoder algorithm. The tool records its input-bit rank
to make that limit explicit. A rank below 32 means the captures do not exercise
enough independent state bits to identify a general bitwise model.

The fixture is the test oracle. Update it only by adding raw, labelled source
evidence with provenance; never replace it with a generated checksum table.
