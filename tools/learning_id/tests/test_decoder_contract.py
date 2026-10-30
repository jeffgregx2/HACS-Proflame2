"""Installed-helper acceptance against JSON emitted by the actual C decoder."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

PYTHON = os.environ.get("PROFLAME2_HELPER_PYTHON")
pytestmark = pytest.mark.skipif(not PYTHON, reason="Run tools/learning_id/validate_package.py")
CORPUS = json.loads(
    (Path(__file__).resolve().parents[3] / "tests/fixtures/rtl433_helper_decoder_output.json").read_text()
)


@pytest.mark.parametrize("remote", CORPUS["remotes"], ids=lambda remote: remote["name"])
def test_every_decoder_record_normalizes_in_installed_package(remote):
    result = subprocess.run(
        [
            PYTHON,
            "-I",
            "-c",
            "import json, sys; from proflame2_protocol.rtl433 import normalize_rtl433_record; "
            "records=json.load(sys.stdin); "
            "assert all(bytes(normalize_rtl433_record(r).frame.wire_words).hex() == r['raw_words'] for r in records)",
        ],
        input=json.dumps(remote["records"]),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("remote", CORPUS["remotes"], ids=lambda remote: remote["name"])
def test_capture_evidence_is_not_manufactured_to_pass_learning(remote):
    records = remote["records"]
    extended = records[0]["protocol_variant"] == "extended_10_word"
    if extended:
        # Select a consistent manual-mode template, keeping only observed repeats.
        fixed = 0x84 if "mendota" in remote["name"] else 0x80
        records = [record for record in records if record["cmd1"] & 0x8C == fixed and record["thermostat"] == 0]
    result = subprocess.run(
        [PYTHON, "-I", "-m", "proflame2_learning_id.cli", "--input-mode", "file"],
        input="\n".join(map(json.dumps, records)),
        capture_output=True,
        text=True,
    )
    if not extended:
        assert result.returncode == 1
        assert not result.stdout
        assert "No Learning ID" in result.stderr
        return
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("PF2-1-E-")
    assert len(result.stdout.splitlines()) == 1
    restored = subprocess.run(
        [
            PYTHON,
            "-I",
            "-c",
            "import json, sys; from dataclasses import asdict; "
            "from proflame2_protocol.learning_id import decode_learning_id; "
            "print(json.dumps(asdict(decode_learning_id(sys.stdin.read().strip()))))",
        ],
        input=result.stdout,
        capture_output=True,
        text=True,
        check=True,
    )
    profile = json.loads(restored.stdout)
    assert profile["serial_id"] == records[0]["id"]
    assert profile["extended_template"] == {
        "w4_base": fixed,
        "w6": records[0]["w6"],
        "w7": 0xC8,
        "w8": records[0]["w8"],
    }
