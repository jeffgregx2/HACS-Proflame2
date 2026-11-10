"""Native test environments stay separate from shared source and other runs."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def runner_environment(tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    calls = tmp_path / "calls.jsonl"
    interpreter = tmp_path / "native-python"
    interpreter.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys, time\n"
        "args = sys.argv[1:]\n"
        "if args[:2] == ['-m', 'venv']:\n"
        "    target = pathlib.Path(args[2]) / 'bin' / 'python'\n"
        "    target.parent.mkdir(parents=True)\n"
        "    target.write_bytes(pathlib.Path(__file__).read_bytes())\n"
        "    target.chmod(0o755)\n"
        "phase = args[1]\n"
        "with open(os.environ['RUNNER_CALLS'], 'a') as output:\n"
        "    output.write(json.dumps({'args': args, 'python': __file__,\n"
        "                             'coverage': os.environ.get('COVERAGE_FILE')}) + '\\n')\n"
        "if phase == os.environ.get('PAUSE_PHASE'):\n"
        "    pathlib.Path(os.environ['RUNNER_STARTED']).touch()\n"
        "    time.sleep(0.5)\n"
        "sys.exit(7 if phase == os.environ.get('FAIL_PHASE') else 0)\n"
    )
    interpreter.chmod(0o755)
    return {
        **os.environ,
        "TMPDIR": str(scratch),
        "TEST_BOOTSTRAP_PYTHON": str(interpreter),
        "RUNNER_CALLS": str(calls),
        "RUNNER_STARTED": str(tmp_path / "started"),
    }


@pytest.mark.parametrize("target", ["test", "firmware-unit-test"])
def test_make_uses_new_native_environment_and_cleans_it(runner_environment, target):
    env = runner_environment
    for _ in range(2):
        result = subprocess.run(["make", target], cwd=ROOT, env=env, capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stdout + result.stderr
        assert not list(Path(env["TMPDIR"]).iterdir())
    calls = [json.loads(line) for line in Path(env["RUNNER_CALLS"]).read_text().splitlines()]
    assert [call["args"][1] for call in calls] == ["venv", "pip", "pytest"] * 2
    assert calls[0]["args"][2] != calls[3]["args"][2]
    for call in calls:
        if call["args"][1] == "venv":
            continue
        assert Path(call["python"]).is_relative_to(Path(env["TMPDIR"]))
        assert Path(call["coverage"]).is_relative_to(Path(env["TMPDIR"]))
    assert "--no-cache-dir" in calls[1]["args"]
    cache_arg = next(arg for arg in calls[2]["args"] if arg.startswith("cache_dir="))
    assert Path(cache_arg.split("=", 1)[1]).is_relative_to(Path(env["TMPDIR"]))
    if target == "firmware-unit-test":
        assert "tests/test_esphome_cpp_unit.py" in calls[2]["args"]


@pytest.mark.parametrize("phase", ["pip", "pytest"])
def test_native_runner_cleans_failed_install_or_test(runner_environment, phase):
    env = {**runner_environment, "FAIL_PHASE": phase}
    result = subprocess.run(["bash", "tools/run_tests.sh"], cwd=ROOT, env=env, capture_output=True, check=False)
    assert result.returncode == 7
    assert not list(Path(env["TMPDIR"]).iterdir())


def test_native_runner_cleans_on_termination(runner_environment):
    env = {**runner_environment, "PAUSE_PHASE": "pytest"}
    process = subprocess.Popen(["bash", "tools/run_tests.sh"], cwd=ROOT, env=env)
    try:
        deadline = time.monotonic() + 5
        while not Path(env["RUNNER_STARTED"]).exists():
            assert time.monotonic() < deadline
            time.sleep(0.01)
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=5) == 143
        assert not list(Path(env["TMPDIR"]).iterdir())
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
