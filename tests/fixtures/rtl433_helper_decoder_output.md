# Decoder-to-Helper Contract Fixture

`rtl433_helper_decoder_output.json` contains the actual JSON emitted by the
rtl_433 C device decoder for four recorded remotes: two legacy (four records)
and two extended (120 records). The manifest records the decoder commit, input
hashes, source references, generation command, and replay classification.

Regenerate only from a clean, reviewed extended-capable rtl_433 checkout:

```sh
python3 -B tools/rtl433_decoder_tests/export_helper_fixtures.py \
  --source /path/to/rtl_433-checkout \
  --output tests/fixtures/rtl433_helper_decoder_output.json
```

The exporter compiles the decoder's existing C harness with address and undefined
behavior sanitizers in an automatically cleaned scratch directory. It does not
modify rtl_433, open an SDR, or synthesize observations for learning. Legacy PCM
is derived from recorded words; extended PCM is replayed directly from captures.
This checks the C decoder JSON boundary, not the RF/pulse detector or live reception.

All records must normalize with the installed Python package without changing
their wire words. Extended learning tests select a consistent manual-mode
template and preserve the recorded repeats. The legacy evidence has only two
commands per remote and no matching repeats; it must fail helper learning rather
than generate an unsupported ID. Synthetic legacy sequences separately exercise
all confirmation gates and HA import.

Raw recordings, private host metadata, and generated Learning IDs do not belong
in this fixture. Review exported data and the source commit before updating it.
