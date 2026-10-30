# rtl_433 Proflame2 C decoder checks

This runner compiles and invokes the actual external rtl_433 Proflame2 device
decoder. It does not use the Python frame decoder as an acceptance oracle.
The Python standard library and a C compiler are sufficient. Binaries and
derived signals are created in an automatically cleaned temporary directory.

```sh
python tools/rtl433_decoder_tests/run_tests.py --source /path/to/updated/rtl_433
```

AddressSanitizer and UndefinedBehaviorSanitizer are enabled by default. Use
`--no-sanitize` only for an additional ordinary build check, not as a substitute
for sanitizer validation. To reproduce the historical overflow, point `--source`
at the pre-fix checkout and `--harness-source` at the updated checkout containing
`tests/proflame2-test.c`, then add `--baseline`.

For actual pulse-detector, PCM-slicer, and C-device-decoder replay, also provide
`--rtl433-binary /path/to/built/rtl_433`. Without this option, the pipeline test
is explicitly skipped. Alternatively, `--build-pipeline` runs CMake/CTest and
replay in an automatically cleaned build directory; CMake must be on PATH.
Replay signals are synthesized from protocol words,
including silence/noise controls. They are derived test signals, not live SDR
recordings. JSON and CSV output are checked through the actual executable.

`golden_output.json` freezes the JSON contract using synthetic identity
`0x123456`. Compare records by keys and values, not object key order. `id` and
all individual bytes are JSON integers; human-readable hexadecimal formatting
does not change their JSON types. `raw_words` contains exactly 14 or 20 lowercase
hexadecimal characters in on-air word order, with no prefix or separators.
Legacy output uses `protocol_variant=legacy_7_word` and `mic=PARITY`; extended
output uses `protocol_variant=extended_10_word` and `mic=DIGEST`. The latter
describes the documented custom alternating-word calculation, not a CRC.

Legacy previously reported `CHECKSUM` despite checking word parity only. The new
`PARITY` label is an intentional compatibility correction. Legacy command/error
bytes remain available. Extended W6/W7 are not legacy error bytes, so extended
records expose W6-W10 and omit `err1`, `err2`, and the unestablished `pilot` field.
Power and flame zero are independent, including power on with flame zero.

Capture-derived rows retain their provenance in the existing
`tests/fixtures/issue_15_tmfsla_extended_capture.json`,
`tests/fixtures/issue_26_mendota_extended_capture.json`, and
`tests/fixtures/real_remote_legacy_captures.json` files. The runner verifies all
120 accepted extended rows, keeps malformed rows rejected, and exercises
framing, bounds, integrity mutations, and terminal-bit cases.
