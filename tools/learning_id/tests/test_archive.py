"""Exercise the distributable ZIP and its extracted source, never RF hardware."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest
from test_cli import commands
from test_guided import PROMPT, record, session
from test_radio import radio as radio

ARCHIVE = os.environ.get("PROFLAME2_HELPER_ARCHIVE")
PYTHON = os.environ.get("PROFLAME2_HELPER_PYTHON")
ROOT = Path(__file__).resolve().parents[3]
CORPUS = json.loads((ROOT / "tests/fixtures/rtl433_helper_decoder_output.json").read_text())
pytestmark = pytest.mark.skipif(not ARCHIVE or not PYTHON, reason="Run make learning-id-package-test")


def run(*args, data=None, source=ARCHIVE, cwd=None):
    return subprocess.run(
        [PYTHON, str(source), *args],
        input=data,
        text=True,
        capture_output=True,
        timeout=15,
        cwd=cwd,
    )


def test_archive_contains_only_inspectable_sources_and_provenance():
    path = Path(ARCHIVE)
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        assert {"__main__.py", "README.md", "LICENSE", "proflame2_learning_id/BUILD_INFO.json"} <= set(names)
        documents = {"README.md", "LICENSE", "proflame2_learning_id/BUILD_INFO.json"}
        assert all(name.endswith(".py") or name in documents for name in names)
        assert not any("test" in name or "private" in name or "build_archive" in name for name in names)
        for name in names:
            if name.endswith(".py"):
                compile(archive.read(name).decode("utf-8"), name, "exec")
        info = json.loads(archive.read("proflame2_learning_id/BUILD_INFO.json"))
        hashes = {
            name: hashlib.sha256(archive.read(name)).hexdigest()
            for name in sorted(names)
            if name != "proflame2_learning_id/BUILD_INFO.json"
        }
        assert info["content_sha256"] == hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
        assert (
            info["version"] == json.loads((ROOT / "custom_components/proflame2/manifest.json").read_text())["version"]
        )
        assert len(info["source_commit"]) == 40
        assert type(info["source_dirty"]) is bool
        assert "/home/" not in json.dumps(info) and "/Users/" not in json.dumps(info)
    checksum = path.with_suffix(".pyz.sha256").read_text()
    assert checksum == f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"


@pytest.mark.parametrize("args", [("--help",), ("--rf-band", "315", "--help"), ()])
def test_help_lists_options_without_the_inspection_block(args):
    result = run(*args)
    assert result.returncode == 0, result.stderr
    for text in (
        "--inspect-pyz-archive",
        "--rf-band",
        "--rtl433",
        "--timeout",
        "--unguided",
        "--input-mode",
    ):
        assert text in result.stdout
    assert "unzip -l" not in result.stdout
    assert "If you wish" not in result.stdout


@pytest.mark.parametrize("managed", [False, True])
def test_inspection_flag_prints_instructions_and_exits_without_a_receiver(radio, managed):
    root, executable, env = radio
    args = ["--rf-band", "315", "--rtl433", str(executable)] if managed else []
    result = subprocess.run(
        [PYTHON, ARCHIVE, *args, "--inspect-pyz-archive"],
        capture_output=True,
        text=True,
        env=env,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("If you wish to examine the contents of this archive before running it:")
    for text in ("unzip -l", "unzip -p", "__main__.py", "python3 -m zipfile -l"):
        assert text in result.stdout
    assert not result.stderr
    assert not (root / "pid").exists()


def test_version_reports_product_commit_and_dirty_status():
    with zipfile.ZipFile(ARCHIVE) as archive:
        info = json.loads(archive.read("proflame2_learning_id/BUILD_INFO.json"))
    result = run("--version")
    assert result.returncode == 0
    assert result.stdout.strip() == f"{info['version']} source={info['source_commit']} dirty={info['source_dirty']}"


@pytest.mark.parametrize("extended", [False, True])
def test_archive_and_extracted_source_match_without_installation(tmp_path, extended):
    data = "\n".join(map(json.dumps, commands(5, extended))) + "\n"
    extracted = tmp_path / "review"
    with zipfile.ZipFile(ARCHIVE) as archive:
        archive.extractall(extracted)
    compressed = run("--input-mode", "file", data=data, cwd=tmp_path)
    unpacked = run("--input-mode", "file", data=data, source=extracted / "__main__.py", cwd=tmp_path)
    assert compressed.returncode == unpacked.returncode == 0, compressed.stderr + unpacked.stderr
    assert compressed.stdout == unpacked.stdout
    assert compressed.stdout.startswith("PF2-1-E-" if extended else "PF2-1-L-")
    assert not list(extracted.rglob("__pycache__"))
    assert not list(tmp_path.glob("*.json"))


def test_runtime_has_no_installed_dependencies():
    result = subprocess.run(
        [
            PYTHON,
            "-I",
            "-c",
            "import importlib.util; assert all(importlib.util.find_spec(n) is None for n in ('pip', 'homeassistant', 'proflame2_learning_id', 'proflame2_protocol'))",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("band", ["315", "433"])
def test_managed_archive_releases_its_receiver(radio, band):
    root, executable, env = radio
    result = subprocess.run(
        [PYTHON, ARCHIVE, "--rf-band", band, "--rtl433", str(executable), "--unguided", "--timeout", "2"],
        env=env,
        text=True,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("PF2-1-L-")
    assert (root / "stopped").exists()


@pytest.mark.parametrize("extended", [False, True])
def test_guided_archive_confirms_buttons_and_finishes_off(extended):
    with session(command=[PYTHON, ARCHIVE, "--input-mode", "live", "--timeout", "2"]) as capture:
        capture.prepare()
        state = dict(Power=0, Light=0, Fan=0, Flame=6)
        for _ in range(60):
            match = capture.read_until(PROMPT)
            control = "Power" if match.group(1) else match.group(2)
            target = int(match.group(1) == "ON") if control == "Power" else int(match.group(4))
            state[control] = target
            capture.send(record(state, extended))
            capture.read_until(f"Received: {control}")
            if control == "Power" and target == 0:
                break
        else:
            pytest.fail("Guided archive did not finish")
        capture.read_until("Your Learning ID is ready")
        capture.process.wait(timeout=5)
        assert capture.process.returncode == 0
        assert capture.process.stdout.read().startswith("PF2-1-E-" if extended else "PF2-1-L-")


@pytest.mark.parametrize(
    "args",
    [
        ("--rf-band", "433", "input.json"),
        ("--rf-band", "315", "--input-mode", "file"),
        ("--rtl433", "absent"),
        ("--unknown",),
        ("--rf-band", "315", "--timeout", "0"),
        ("--rf-band", "315", "--timeout", "nan"),
    ],
)
def test_argument_errors_do_not_start_the_receiver(radio, args):
    root, executable, env = radio
    if "--rf-band" in args:
        args = (*args, "--rtl433", str(executable))
    result = subprocess.run([PYTHON, ARCHIVE, *args], capture_output=True, text=True, env=env, timeout=10)
    assert result.returncode == 2
    assert not result.stdout
    assert not (root / "pid").exists()


@pytest.mark.parametrize("mode", ["bad", "silent"])
def test_archive_failure_stops_its_receiver_without_an_id(radio, mode):
    root, executable, env = radio
    env["RADIO_TEST_MODE"] = mode
    result = subprocess.run(
        [PYTHON, ARCHIVE, "--rf-band", "315", "--rtl433", str(executable), "--unguided", "--timeout", "0.1"],
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    assert result.returncode == 1
    assert not result.stdout
    assert (root / "stopped").exists()


def test_archive_cancellation_stops_its_receiver(radio, monkeypatch):
    root, executable, env = radio
    monkeypatch.setenv("RADIO_TEST_ROOT", env["RADIO_TEST_ROOT"])
    monkeypatch.setenv("RADIO_TEST_MODE", "silent")
    with session(command=[PYTHON, ARCHIVE, "--rf-band", "315", "--rtl433", str(executable)]) as capture:
        # The PTY child inherits the same environment as its synthetic receiver.
        capture.read_until("Press Return when it is OFF and you are ready")
        deadline = time.monotonic() + 5
        while not (root / "ready").exists():
            assert time.monotonic() < deadline
            time.sleep(0.01)
        capture.process.send_signal(signal.SIGTERM)
        capture.process.wait(timeout=5)
        assert capture.process.returncode == 130
        assert not capture.process.stdout.read()
        assert (root / "stopped").exists()


def test_named_file_and_late_conflicts(tmp_path):
    rows = commands(5, True)
    capture = tmp_path / "capture.json"
    capture.write_text("\n".join(map(json.dumps, rows)) + "\n")
    result = run(str(capture))
    assert result.returncode == 0, result.stderr
    assert "Confirmed" in result.stderr
    bad = rows[-1].copy()
    bad["raw_words"] = "654321" + bad["raw_words"][6:]
    with capture.open("a") as stream:
        stream.write(json.dumps(bad) + "\n")
    result = run(str(capture))
    assert result.returncode == 1
    assert not result.stdout


@pytest.mark.parametrize("remote", CORPUS["remotes"], ids=lambda remote: remote["name"])
def test_archive_preserves_recorded_remote_evidence(remote):
    rows = remote["records"]
    extended = rows[0]["protocol_variant"] == "extended_10_word"
    if extended:
        fixed = 0x84 if "mendota" in remote["name"] else 0x80
        rows = [row for row in rows if row["cmd1"] & 0x8C == fixed and row["thermostat"] == 0]
    result = run("--input-mode", "file", data="\n".join(map(json.dumps, rows)) + "\n")
    if extended:
        assert result.returncode == 0, result.stderr
        assert "Confirmed" in result.stderr
        assert result.stdout.startswith("PF2-1-E-")
    else:
        # These recorded legacy remotes have insufficient repeated evidence.
        assert result.returncode == 1
        assert not result.stdout


def test_repeatable_builder_and_no_overwrite(tmp_path):
    builder = ROOT / "tools/learning_id/build_archive.py"
    outputs = [tmp_path / "first", tmp_path / "second"]
    for output in outputs:
        result = subprocess.run(
            [sys.executable, "-B", str(builder), "--output-dir", str(output)], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
    first, second = [output / "proflame2-learning-id.pyz" for output in outputs]
    assert first.read_bytes() == second.read_bytes()
    result = subprocess.run(
        [sys.executable, "-B", str(builder), "--output-dir", str(outputs[0])], capture_output=True, text=True
    )
    assert result.returncode == 1
    assert "already exists" in result.stderr
    assert first.read_bytes() == second.read_bytes()


def test_release_builder_requires_clean_known_source(tmp_path):
    source = tmp_path / "source"
    for name in ("tools/learning_id", "custom_components/proflame2/protocol"):
        shutil.copytree(ROOT / name, source / name, ignore=shutil.ignore_patterns("tests", "__pycache__"))
    shutil.copy2(ROOT / "LICENSE", source / "LICENSE")
    shutil.copy2(
        ROOT / "custom_components/proflame2/manifest.json", source / "custom_components/proflame2/manifest.json"
    )
    builder = source / "tools/learning_id/build_archive.py"

    def build(name, *, release=True):
        args = [sys.executable, "-B", str(builder), "--output-dir", str(tmp_path / name)]
        if release:
            args.append("--require-clean")
        return subprocess.run(args, capture_output=True, text=True)

    assert build("unknown").returncode == 1
    for args in (
        ["init", "-q"],
        ["add", "."],
        ["-c", "user.name=Test", "-c", "user.email=test@localhost", "commit", "-qm", "Synthetic source snapshot"],
    ):
        subprocess.run(["git", *args], cwd=source, check=True, capture_output=True)
    clean = build("clean")
    assert clean.returncode == 0, clean.stderr
    with (source / "tools/learning_id/cli.py").open("a") as stream:
        stream.write("\n# Synthetic uncommitted test change.\n")
    dirty = build("dirty")
    assert dirty.returncode == 1
    assert "clean Git checkout" in dirty.stderr
    assert not (tmp_path / "dirty").exists()
    assert build("development", release=False).returncode == 0
    with zipfile.ZipFile(tmp_path / "development/proflame2-learning-id.pyz") as archive:
        assert json.loads(archive.read("proflame2_learning_id/BUILD_INFO.json"))["source_dirty"] is True
        assert "Synthetic uncommitted test change" in archive.read("proflame2_learning_id/cli.py").decode()
