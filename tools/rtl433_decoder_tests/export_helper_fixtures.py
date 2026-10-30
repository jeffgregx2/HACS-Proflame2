"""Export actual C-decoder JSON for the independently tested Learning ID helper.

This development-only tool reads an explicit rtl_433 checkout without modifying
it. Its test harness and corpus remain owned by rtl_433. No radio is opened.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import runpy
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory


def export(source: Path, output: Path) -> None:
    """Replay recorded PCM or derived legacy rows through the C device decoder."""
    source = source.resolve()
    status = subprocess.check_output(["git", "-C", str(source), "status", "--porcelain"], text=True)
    if status.strip():
        raise ValueError("Use a clean decoder checkout so fixture provenance is reproducible.")
    revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    fixtures = source / "tests/proflame2"
    runner = runpy.run_path(str(fixtures / "test_decoder.py"))
    groups = []
    inputs = {}
    with TemporaryDirectory(prefix="proflame2-json-export.") as temp:
        binary = Path(temp) / "decoder"
        runner["build"](source, source / "tests/proflame2-test.c", binary, sanitize=True)

        def decode(bits: str) -> dict:
            result = subprocess.run([str(binary), runner["code"](bits)], capture_output=True, text=True, check=True)
            if "Sanitizer" in result.stderr or "runtime error:" in result.stderr:
                raise RuntimeError("Decoder sanitizer failure.")
            return json.loads(result.stdout)

        def read(name: str) -> dict:
            path = fixtures / name
            inputs[name] = hashlib.sha256(path.read_bytes()).hexdigest()
            return json.loads(path.read_text())

        for remote in read("real_remote_legacy_captures.json")["remotes"]:
            groups.append(
                {
                    "name": f"legacy-{remote['capture_id']}",
                    "source": remote["source"],
                    "signal_kind": "derived PCM from recorded wire words; not live IQ",
                    "records": [decode(runner["encode"](words)) for words in remote["words"]],
                }
            )
        for name in ("issue_15_tmfsla_extended_capture.json", "issue_26_mendota_extended_capture.json"):
            corpus = read(name)
            groups.append(
                {
                    "name": name.removesuffix("_capture.json"),
                    "source": corpus["sources"],
                    "signal_kind": "recorded PCM replay; not live IQ",
                    "records": [
                        decode(runner["raw_bits"](row))
                        for row in corpus["captures"]
                        if row.get("expected_decode") != "rejected"
                    ],
                }
            )
    artifact = {
        "decoder_commit": revision,
        "generator": "tools/rtl433_decoder_tests/export_helper_fixtures.py --source <decoder-checkout> --output <fixture>",
        "decoder_entry": "tests/proflame2-test.c invokes src/devices/proflame2.c; ASan/UBSan enabled",
        "input_sha256": inputs,
        "note": "Observed repeats retained. No observations or integrity bytes fabricated for learning gates.",
        "remotes": groups,
    }
    output.write_text(json.dumps(artifact, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    export(args.source, args.output)


if __name__ == "__main__":
    main()
