"""Temporary helper environments clean up without consuming the JSON input."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

RUNNER = Path(__file__).resolve().parents[1] / "tools/learning_id/run.sh"
STUB = """#!/usr/bin/env python3
import json, os, pathlib, sys, time
args = sys.argv[1:]
if args[:2] == ["-m", "venv"]:
    root = pathlib.Path(args[2]).parent
    pathlib.Path(os.environ["RUNNER_ROOT_FILE"]).write_text(str(root))
    if os.environ.get("FAIL_VENV"):
        sys.exit(8)
    python = root / "venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text(pathlib.Path(__file__).read_text())
    python.chmod(0o700)
elif args[:2] == ["-m", "pip"]:
    assert sys.stdin.read() == ""
    source = pathlib.Path(args[-1])
    assert (source / "pyproject.toml").is_file()
    assert (source / "custom_components/proflame2/protocol/learning_id.py").is_file()
    print("Installer output must not enter the JSON/ID stream")
    sys.exit(int(os.environ.get("INSTALL_STATUS", "0")))
elif args[:2] == ["-m", "proflame2_learning_id.radio"]:
    print(json.dumps({"module": args[1], "args": args[2:]}))
else:
    assert args[:2] == ["-m", "proflame2_learning_id.cli"]
    if os.environ.get("BLOCK_HELPER"):
        pathlib.Path(os.environ["RUNNER_READY_FILE"]).touch()
        time.sleep(60)
    print(json.dumps({"args": args[2:], "input": sys.stdin.read()}))
    sys.exit(int(os.environ.get("HELPER_STATUS", "0")))
"""


@pytest.fixture
def runner_env(tmp_path):
    bootstrap = tmp_path / "python-stub"
    bootstrap.write_text(STUB)
    bootstrap.chmod(0o700)
    return {
        **os.environ,
        "LEARNING_ID_PYTHON": str(bootstrap),
        "RUNNER_ROOT_FILE": str(tmp_path / "root"),
        "RUNNER_READY_FILE": str(tmp_path / "ready"),
    }


@pytest.mark.parametrize(
    ("setting", "status"), [(None, 0), ("FAIL_VENV", 8), ("INSTALL_STATUS", 9), ("HELPER_STATUS", 7)]
)
def test_runner_forwards_input_arguments_status_and_cleans(runner_env, setting, status):
    if setting:
        runner_env[setting] = str(status)
    result = subprocess.run(
        ["bash", str(RUNNER), "--input-mode", "live", "--timeout", "600", "-v"],
        env=runner_env,
        input='{"model":"test"}\n',
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == status
    root = Path(Path(runner_env["RUNNER_ROOT_FILE"]).read_text())
    assert root.parent == Path("/tmp")
    assert not root.exists()
    if setting in (None, "HELPER_STATUS"):
        assert json.loads(result.stdout) == {
            "args": ["--input-mode", "live", "--timeout", "600", "-v"],
            "input": '{"model":"test"}\n',
        }
    else:
        assert not result.stdout


@pytest.mark.parametrize("band_args", [["--rf-band", "315"], ["--rf-band=433"]])
def test_runner_dispatches_managed_radio_mode(runner_env, band_args):
    result = subprocess.run(
        ["bash", str(RUNNER), "--timeout", "600", *band_args],
        env=runner_env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "module": "proflame2_learning_id.radio",
        "args": ["--timeout", "600", *band_args],
    }
    assert not Path(Path(runner_env["RUNNER_ROOT_FILE"]).read_text()).exists()


@pytest.mark.parametrize(("stop_signal", "status"), [(signal.SIGTERM, 143), (signal.SIGINT, 130)])
def test_runner_cleans_on_interruption(runner_env, stop_signal, status):
    runner_env["BLOCK_HELPER"] = "1"
    process = subprocess.Popen(
        ["bash", str(RUNNER)], env=runner_env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    try:
        deadline = time.monotonic() + 10
        while not Path(runner_env["RUNNER_READY_FILE"]).exists():
            assert process.poll() is None
            assert time.monotonic() < deadline
            time.sleep(0.02)
        process.send_signal(stop_signal)
        process.communicate(timeout=10)
        assert process.returncode == status
        assert not Path(Path(runner_env["RUNNER_ROOT_FILE"]).read_text()).exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
