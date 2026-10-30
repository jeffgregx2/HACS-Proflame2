"""Managed reception stops only its own receiver, including when RF is quiet."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time

import pytest
from test_guided import session

PYTHON = os.environ.get("PROFLAME2_HELPER_PYTHON")
pytestmark = pytest.mark.skipif(not PYTHON, reason="Run tools/learning_id/validate_package.py")


def transform(value, cd):
    high, low = value >> 4, value & 15
    return ((((cd >> 4) ^ high ^ (high << 1) ^ (low << 1)) & 15) << 4) | ((cd ^ high ^ low) & 15)


@pytest.fixture
def radio(tmp_path):
    rows = []
    for index in range(7):
        cmd1, cmd2 = 1 | (index << 4), 1 + index % 6
        row = dict(
            model="Proflame2-Remote",
            protocol_variant="legacy_7_word",
            raw_words=bytes([0x12, 0x34, 0x56, cmd1, cmd2, transform(cmd1, 0x57), transform(cmd2, 0x18)]).hex(),
        )
        rows.extend([row, row.copy()])
    data = tmp_path / "rows.json"
    data.write_text("\n".join(map(json.dumps, rows)) + "\n")
    executable = tmp_path / "receiver"
    executable.write_text(
        f"#!{PYTHON}\n"
        "import json, os, pathlib, signal, sys, time\n"
        "if '-V' in sys.argv:\n"
        "    print('rtl_433 version test-decoder')\n"
        "    sys.exit(0)\n"
        "if '-y' in sys.argv:\n"
        "    mode = os.environ.get('PROBE_TEST_MODE', '')\n"
        "    pcm = sys.argv[sys.argv.index('-y') + 1]\n"
        "    legacy = pcm.startswith('{182}') or mode == 'legacy_only'\n"
        "    row = dict(model='Proflame2-Remote', protocol_variant='legacy_7_word' if legacy else 'extended_10_word',\n"
        "               raw_words='123456212614bc' if legacy else '123456812015c800216e')\n"
        "    if mode == 'bad_integrity' and not legacy: row['raw_words'] = row['raw_words'][:-2] + '00'\n"
        "    if mode == 'missing_marker': row.pop('protocol_variant')\n"
        "    print('{broken' if mode == 'bad_json' else json.dumps(row))\n"
        "    sys.exit(0)\n"
        "root = pathlib.Path(os.environ['RADIO_TEST_ROOT'])\n"
        "(root / 'pid').write_text(str(os.getpid()))\n"
        "(root / 'args.json').write_text(json.dumps(sys.argv[1:]))\n"
        "def stop(sig, frame):\n"
        "    (root / 'stopped').touch()\n"
        "    sys.exit(0)\n"
        "signal.signal(signal.SIGTERM, stop)\n"
        "signal.signal(signal.SIGINT, stop)\n"
        "(root / 'ready').touch()\n"
        "mode = os.environ.get('RADIO_TEST_MODE', 'success')\n"
        "print('STARTUP_DIAGNOSTIC_MARKER', file=sys.stderr, flush=True)\n"
        "if mode == 'success':\n"
        "    print((root / 'rows.json').read_text(), flush=True)\n"
        "elif mode == 'bad':\n"
        "    print('{broken', flush=True)\n"
        "while True: time.sleep(0.1)\n"
    )
    executable.chmod(0o700)
    return tmp_path, executable, {**os.environ, "RADIO_TEST_ROOT": str(tmp_path)}


@pytest.mark.parametrize("band", ["315", "433"])
@pytest.mark.parametrize(("mode", "status"), [("success", 0), ("bad", 1), ("silent", 1)])
def test_receiver_is_stopped_after_success_failure_or_timeout(radio, band, mode, status):
    root, executable, env = radio
    env["RADIO_TEST_MODE"] = mode
    result = subprocess.run(
        [
            PYTHON,
            "-I",
            "-m",
            "proflame2_learning_id.radio",
            "--rf-band",
            band,
            "--rtl433",
            str(executable),
            "--unguided",
            "--timeout",
            "0.5",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == status, result.stderr
    assert (root / "stopped").is_file()
    assert json.loads((root / "args.json").read_text()) == [
        "-R",
        "207",
        "-f",
        "315M" if band == "315" else "433.92M",
        "-F",
        "json",
    ]
    if status:
        assert not result.stdout
        assert "rtl_433 diagnostics (bounded tail):" in result.stderr
        assert "STARTUP_DIAGNOSTIC_MARKER" in result.stderr
    else:
        assert result.stdout.startswith("PF2-1-L-")
        assert "STARTUP_DIAGNOSTIC_MARKER" not in result.stderr
    assert "Receiver: rtl_433 version test-decoder" in result.stderr


@pytest.mark.parametrize("stop_signal", [signal.SIGINT, signal.SIGTERM])
def test_cancellation_also_stops_the_owned_receiver(radio, stop_signal):
    root, executable, env = radio
    env["RADIO_TEST_MODE"] = "silent"
    process = subprocess.Popen(
        [
            PYTHON,
            "-I",
            "-m",
            "proflame2_learning_id.radio",
            "--rf-band",
            "315",
            "--rtl433",
            str(executable),
            "--unguided",
            "--timeout",
            "30",
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 5
        while not (root / "ready").exists():
            assert time.monotonic() < deadline
            time.sleep(0.01)
        process.send_signal(stop_signal)
        stdout, _ = process.communicate(timeout=10)
        assert process.returncode == 130
        assert not stdout
        assert (root / "stopped").is_file()
    finally:
        if process.poll() is None:
            process.terminate()
            process.communicate(timeout=10)


def test_missing_receiver_executable_is_a_clean_failure(tmp_path):
    result = subprocess.run(
        [
            PYTHON,
            "-I",
            "-m",
            "proflame2_learning_id.radio",
            "--rf-band",
            "315",
            "--rtl433",
            str(tmp_path / "absent"),
            "--unguided",
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 1
    assert "could not be started" in result.stderr
    assert "Traceback" not in result.stderr
    assert not result.stdout


@pytest.mark.parametrize("mode", ["legacy_only", "bad_integrity", "missing_marker", "bad_json"])
def test_incompatible_decoder_is_rejected_before_opening_the_receiver(radio, mode):
    root, executable, env = radio
    env["PROBE_TEST_MODE"] = mode
    result = subprocess.run(
        [
            PYTHON,
            "-I",
            "-m",
            "proflame2_learning_id.radio",
            "--rf-band",
            "315",
            "--rtl433",
            str(executable),
            "--unguided",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1
    assert "not compatible" in result.stderr
    assert "--rtl433" in result.stderr
    assert not (root / "pid").exists()
    assert not result.stdout


@pytest.mark.parametrize("from_path", [False, True])
def test_interactive_replacement_path_is_checked_before_learning(radio, monkeypatch, from_path):
    root, executable, env = radio
    monkeypatch.setenv("RADIO_TEST_ROOT", env["RADIO_TEST_ROOT"])
    old = root / "rtl_433"
    old.write_text(
        f"#!{PYTHON}\nimport json, sys\n"
        "if '-V' in sys.argv: print('rtl_433 version older-decoder')\n"
        "elif '-y' in sys.argv: print(json.dumps(dict(model='Proflame2-Remote', protocol_variant='legacy_7_word', raw_words='123456212614bc')))\n"
        "else: sys.exit(99)\n"
    )
    old.chmod(0o700)
    command = [PYTHON, "-I", "-m", "proflame2_learning_id.radio", "--rf-band", "315"]
    if from_path:
        monkeypatch.setenv("PATH", str(root) + os.pathsep + os.environ["PATH"])
    else:
        command += ["--rtl433", str(old)]
    with session(command=command) as capture:
        capture.read_until("Enter the full path to the compatible rtl_433 executable")
        selected = "The rtl_433 in the PATH" if from_path else "The selected rtl_433"
        assert (
            f"{selected} is not compatible. You will need to supply the full path to a compatible version."
            in capture.transcript
        )
        assert "legacy offline decoding check failed" not in capture.transcript
        assert not (root / "pid").exists()
        capture.keyboard(str(executable))
        capture.read_until("Press Return when it is OFF and you are ready")
        assert "Offline compatibility checks passed." in capture.transcript
        assert "STARTUP_DIAGNOSTIC_MARKER" not in capture.transcript
        deadline = time.monotonic() + 5
        while not (root / "ready").exists():
            assert time.monotonic() < deadline
            time.sleep(0.01)
        capture.keyboard("q")
        capture.process.wait(timeout=5)
        assert capture.process.returncode == 130
        assert not capture.process.stdout.read()
        assert (root / "stopped").is_file()


@pytest.mark.skipif(
    not os.environ.get("PROFLAME2_RECEIVER_PROBE_BINARY"), reason="Supply a built rtl_433 for offline capability checks"
)
def test_actual_decoder_capability_checks():
    result = subprocess.run(
        [
            PYTHON,
            "-I",
            "-c",
            "import sys; from proflame2_learning_id.receiver_probe import check; check(sys.argv[1])",
            os.environ["PROFLAME2_RECEIVER_PROBE_BINARY"],
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
