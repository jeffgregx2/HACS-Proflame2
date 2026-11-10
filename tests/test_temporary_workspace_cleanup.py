"""Build/test scratch space must not survive a completed invocation."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def build_environment(tmp_path: Path) -> dict[str, str]:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    executable = tmp_path / "esphome"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys, time\n"
        "config = pathlib.Path(sys.argv[2])\n"
        "cwd = config.parent if config.is_absolute() else pathlib.Path.cwd()\n"
        "with open(os.environ['BUILD_CALLS'], 'a') as out:\n"
        "    out.write(json.dumps({'cwd': str(cwd), 'args': sys.argv[1:]}) + '\\n')\n"
        "if os.environ.get('PAUSE_BUILD'):\n"
        "    time.sleep(0.6)\n"
        "if os.environ.get('FAIL_BUILD') == sys.argv[1]:\n"
        "    sys.exit(7)\n"
        "if 'invalid_rf_band' in sys.argv[2]:\n"
        "    sys.exit(2)\n"
        "if sys.argv[1] == 'config':\n"
        "    cache = cwd / '.esphome/external_components/test/esphome/components/proflame2_tembed'\n"
        "    cache.mkdir(parents=True, exist_ok=True)\n"
        "    (cache / 'proflame2_tembed.cpp').write_text('stale component')\n"
        "if sys.argv[1] == 'compile' and 'SOURCE_COMPONENT' in os.environ:\n"
        "    cache = cwd / '.esphome/external_components/test/esphome/components/proflame2_tembed'\n"
        "    assert (cache / 'proflame2_tembed.cpp').read_bytes() == "
        "pathlib.Path(os.environ['SOURCE_COMPONENT']).read_bytes()\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return {
        **os.environ,
        "TMPDIR": str(scratch),
        "ESPHOME_CLI": str(executable),
        "BUILD_CALLS": str(tmp_path / "calls.jsonl"),
    }


@pytest.mark.parametrize(
    "target,expected_calls",
    [
        ("config", 1),
        ("config-433", 1),
        ("config-invalid-rf-band", 1),
        ("compile", 2),
        ("compile-433", 2),
        ("validate", 5),
    ],
)
def test_make_validation_cleans_stage_and_uses_checkout(
    build_environment: dict[str, str], target: str, expected_calls: int
) -> None:
    env = dict(build_environment)
    env["SOURCE_COMPONENT"] = str(REPO_ROOT / "esphome/components/proflame2_tembed/proflame2_tembed.cpp")
    result = subprocess.run(
        ["make", "-o", "esphome-setup", f"esphome-{target}"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    calls = [json.loads(line) for line in Path(env["BUILD_CALLS"]).read_text().splitlines()]
    assert len(calls) == expected_calls
    assert not list(Path(env["TMPDIR"]).iterdir())


@pytest.mark.parametrize("command", ["config", "compile"])
def test_make_validation_cleans_failed_build(build_environment: dict[str, str], command: str) -> None:
    env = {**build_environment, "FAIL_BUILD": command}
    result = subprocess.run(
        ["make", "-o", "esphome-setup", "esphome-compile"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert not list(Path(env["TMPDIR"]).iterdir())


def test_validation_wrapper_cleans_on_termination_and_isolates_concurrent_runs(
    tmp_path: Path, build_environment: dict[str, str]
) -> None:
    fake_make = tmp_path / "make"
    fake_make.write_text(
        f"#!{sys.executable}\n"
        "import os, pathlib, sys, time\n"
        "root = next(arg.split('=', 1)[1] for arg in sys.argv if arg.startswith('ESPHOME_WORK_ROOT='))\n"
        "pathlib.Path(root, 'started').touch()\n"
        "time.sleep(0.6)\n",
        encoding="utf-8",
    )
    fake_make.chmod(0o755)
    env = {**build_environment, "MAKE": str(fake_make)}
    processes = [
        subprocess.Popen(["bash", "tools/run_esphome_validation.sh", "unused"], cwd=REPO_ROOT, env=env)
        for _ in range(2)
    ]
    try:
        deadline = time.monotonic() + 5
        while len(list(Path(env["TMPDIR"]).glob("*/started"))) != 2:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        processes[0].send_signal(signal.SIGTERM)
        assert processes[0].wait(timeout=5) == 143
        assert processes[1].wait(timeout=5) == 0
        assert not list(Path(env["TMPDIR"]).iterdir())
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)


@pytest.mark.parametrize("fail_command", ["", "config", "compile"])
def test_dependency_prepopulation_cleans_stage_but_keeps_cache(
    tmp_path: Path, build_environment: dict[str, str], fail_command: str
) -> None:
    source = tmp_path / "source"
    (source / "esphome").mkdir(parents=True)
    (source / "esphome/validate_display_preset.yaml").touch()
    cache = tmp_path / "cache"
    cache.mkdir()
    sentinel = cache / "dependency"
    sentinel.touch()
    env = {
        **build_environment,
        "PROFLAME2_SOURCE_REPO": str(source),
        "PLATFORMIO_CORE_DIR": str(cache),
        "FAIL_BUILD": fail_command,
    }
    result = subprocess.run(
        ["bash", "tools/prepopulate_esphome_deps.sh"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == (7 if fail_command else 0), result.stdout + result.stderr
    assert sentinel.exists()
    assert not list(Path(env["TMPDIR"]).iterdir())


def test_dependency_prepopulation_never_overwrites_existing_workspace(
    tmp_path: Path, build_environment: dict[str, str]
) -> None:
    sentinel = tmp_path / "keep"
    sentinel.touch()
    env = {**build_environment, "PROFLAME2_BUILD_DIR": str(tmp_path)}
    result = subprocess.run(
        ["bash", "tools/prepopulate_esphome_deps.sh"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert sentinel.exists()


def test_dependency_prepopulation_cleans_on_termination(tmp_path: Path, build_environment: dict[str, str]) -> None:
    source = tmp_path / "source"
    (source / "esphome").mkdir(parents=True)
    (source / "esphome/validate_display_preset.yaml").touch()
    env = {**build_environment, "PROFLAME2_SOURCE_REPO": str(source), "PAUSE_BUILD": "1"}
    process = subprocess.Popen(
        ["bash", "tools/prepopulate_esphome_deps.sh"],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 5
        while not Path(env["BUILD_CALLS"]).exists():
            assert time.monotonic() < deadline
            time.sleep(0.01)
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=5) == 143
        assert not list(Path(env["TMPDIR"]).iterdir())
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


@pytest.mark.parametrize("outcome,exit_code", [("assert True", 0), ("assert False", 1), ("raise KeyboardInterrupt", 2)])
def test_pytest_cleans_owned_workspace_on_success_failure_and_interrupt(
    tmp_path: Path, outcome: str, exit_code: int
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "conftest.py").write_text((REPO_ROOT / "tests/conftest.py").read_text())
    (project / "pytest.ini").write_text("[pytest]\n")
    (project / "test_scratch.py").write_text(
        "def test_scratch(tmp_path):\n"
        "    (tmp_path / 'compiler-output').write_text('temporary')\n"
        f"    {outcome}\n"
    )
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    env = {
        **os.environ,
        "TMPDIR": str(scratch),
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTHONPATH": str(REPO_ROOT),
    }
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(project)],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == exit_code, result.stdout + result.stderr
    assert not list(scratch.iterdir())
