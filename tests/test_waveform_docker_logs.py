"""Docker log supervision terminates owned children without touching hardware."""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tools.waveform_collect import validate_config
from tools.waveform_docker_logs import SUPERVISOR, docker_command, stream_logs
from tools.waveform_pilot_config import make_config


@pytest.mark.parametrize("mode", ["eof", "heartbeat_loss", "term"])
def test_supervisor_reaps_logger(mode):
    code = "import os,time; print(os.getpid(), flush=True); time.sleep(60)"
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", SUPERVISOR, sys.executable, "-u", "-c", code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        process.stdin.write("heartbeat\n")
        process.stdin.flush()
        pid = int(process.stdout.readline())
        if mode == "eof":
            process.stdin.close()
        elif mode == "term":
            process.terminate()
        process.wait(timeout=4)
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        for file in (process.stdin, process.stdout, process.stderr):
            file.close()


def test_no_heartbeat_never_starts_child(tmp_path):
    marker = tmp_path / "must-not-exist"
    code = f"from pathlib import Path; Path({str(marker)!r}).touch()"
    result = subprocess.run(
        [sys.executable, "-u", "-c", SUPERVISOR, sys.executable, "-c", code], input=b"", capture_output=True, timeout=3
    )
    assert result.returncode == 2
    assert not marker.exists()


def test_wrapper_closes_stdin_and_waits_for_cleanup():
    command = [sys.executable, "-u", "-c", SUPERVISOR, sys.executable, "-c", "import time; time.sleep(60)"]
    start = time.monotonic()
    assert stream_logs(command, duration=0.25) == 0
    assert time.monotonic() - start < 3


def test_command_uses_network_logging_without_reset():
    command = docker_command("builder", "/config/receiver.yaml", "OTA")
    assert "-i" in command and "-t" not in command
    assert command[-3:] == ["--device", "OTA", "--no-states"]
    assert "--reset" not in command


@pytest.mark.parametrize(
    "args",
    [
        ("-bad", "/config/receiver.yaml", "OTA"),
        ("builder", "relative.yaml", "OTA"),
        ("builder", "/config/receiver.yaml", "/dev/ttyUSB0"),
    ],
)
def test_rejects_serial_or_ambiguous_arguments(args):
    with pytest.raises(ValueError):
        docker_command(*args)


def test_rejects_unbounded_duration():
    with pytest.raises(ValueError):
        stream_logs(["unused"], duration=240)


def test_offline_decoder_import_needs_no_home_assistant_or_site_packages():
    root = Path(__file__).resolve().parents[1]
    code = (
        f"import sys; sys.path.insert(0, {str(root)!r}); "
        "from tools.waveform_diagnostics import classify_edges; "
        "assert classify_edges([]) == []; "
        "assert 'custom_components.proflame2' not in sys.modules; "
        "assert 'homeassistant' not in sys.modules"
    )
    subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", code],
        check=True,
        capture_output=True,
        timeout=10,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )


@pytest.mark.parametrize("band", ["315", "433"])
@pytest.mark.parametrize("source", ["native", "lilygo"])
def test_pilot_config_defaults_are_explicit_and_not_deployed_claims(band, source):
    config = make_config(
        container="builder",
        configuration="/config/receiver.yaml",
        band=band,
        gain=20,
        tool_version="verified-installed-version",
        source=source,
        source_sha="a" * 40,
        verified=False,
    )
    with pytest.raises(ValueError, match="verify installed"):
        validate_config(config)
    config["recorder_options_verified"] = True
    validate_config(config)
    assert config["session"]["sdr"]["center_frequency_hz"] == (315000000 if band == "315" else 433920000)
    assert "-w" in config["sdr_command"]
    assert "tools.waveform_docker_logs" in config["receiver_command"]
    assert all(device["firmware_version"] is None for device in config["inventory"]["devices"])
