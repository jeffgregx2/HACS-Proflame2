# Proflame2 Learning ID Helper

This archive contains readable Python source and its license. There is no
bundled Python interpreter, rtl_433 binary, installer, or native library.
Supported use: Linux or macOS with Python 3.10 or newer. You are responsible
for installing your SDR and a compatible rtl_433 build.

## Optional Source Inspection

Inspect the archive using ZIP tools, without executing the helper:

```sh
unzip -l proflame2-learning-id.pyz
unzip -p proflame2-learning-id.pyz __main__.py
unzip proflame2-learning-id.pyz -d proflame2-learning-id-source
```

If `unzip` is unavailable, use Python's standard-library ZIP tool instead;
these commands also do not execute the helper:

```sh
python3 -m zipfile -l proflame2-learning-id.pyz
python3 -m zipfile -e proflame2-learning-id.pyz proflame2-learning-id-source
```

Read `__main__.py`, the modules in `proflame2_learning_id/` and
`proflame2_protocol/`, and `LICENSE`. `proflame2_learning_id/BUILD_INFO.json`
records the product version, source commit, local-change status, and a digest
of the packaged content. It contains no build-machine paths or usernames.

Verify the separate release checksum with `sha256sum -c` on Linux or
`shasum -a 256 -c` on macOS. A checksum detects mismatched/corrupt downloads;
it is not a digital signature or a replacement for trusting/reviewing source.

## Run the Helper

```sh
python3 proflame2-learning-id.pyz --help
python3 proflame2-learning-id.pyz --rf-band 315 --timeout 600
```

`--help` is concise. Use `--inspect-pyz-archive` to display the detailed
inspection instructions and exit without starting a receiver or learning.
That flag executes helper code to print text; the ZIP commands above do not.

Use `--rf-band 433` for a remote labeled 433.92 MHz. To run the extracted
source instead, replace the archive path with
`proflame2-learning-id-source/__main__.py`. File input is also supported:

```sh
python3 proflame2-learning-id.pyz remote-capture.json -v
```

The helper receives only. Its guided instructions ask you to press native
remote buttons, which may operate the fireplace. Follow its normal operating
instructions. No fireplace acknowledgement is inferred from received packets.

Nothing is installed. No virtual environment, pip download, persistent
configuration, or automatic log file is created. Normal exit/cancellation
stops only the rtl_433 receiver started by the helper. Delete this archive and
any extracted review directory when finished. Keep your Learning ID private
and save it separately if you need it later.

Detailed instructions: `docs/Using_rtl_433_for_learning.md` in HACS-Proflame2.
