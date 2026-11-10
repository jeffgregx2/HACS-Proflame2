# rtl_433 decoder regression compatibility entry point

The decoder runner, capture vectors, frozen JSON examples, and output-layout
checks now live entirely in rtl_433 under `tests/proflame2/`. Its CMake/CTest
configuration runs the corpus and detector replay when Python 3.10+ is available.
No HACS-Proflame2 installation is needed to validate that checkout.

This entry point delegates to that checkout for compatibility:

```sh
python tools/rtl433_decoder_tests/run_tests.py --source /path/to/rtl_433 --build-pipeline
```

See `tests/proflame2/README.md` in rtl_433 for standalone commands, sanitizer
options, capture provenance, and the JSON/output contract. HACS protocol-learning
fixtures remain here for the independent Python tool's own tests.
