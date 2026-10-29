"""Local-only settings are literal, private, and independent of source paths."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools.waveform.environment import capture_root, config_directory, load_environment


def private_file(tmp_path, text):
    path = tmp_path / "waveform.env"
    path.write_text(text)
    path.chmod(0o600)
    return path


def test_external_configuration_and_exported_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("PROFLAME2_CAPTURE_ROOT", "/persistent/already-selected")
    monkeypatch.delenv("PROFLAME2_TEST_LITERAL", raising=False)
    path = private_file(tmp_path, 'PROFLAME2_CAPTURE_ROOT=/other\nPROFLAME2_TEST_LITERAL="$(touch forbidden)"\n')
    load_environment(path)
    assert os.environ["PROFLAME2_TEST_LITERAL"] == "$(touch forbidden)"
    assert capture_root() == Path("/persistent/already-selected")
    monkeypatch.delenv("PROFLAME2_TEST_LITERAL")


def test_xdg_defaults_and_no_configuration_file(tmp_path, monkeypatch):
    monkeypatch.delenv("PROFLAME2_WAVEFORM_ENV", raising=False)
    monkeypatch.delenv("PROFLAME2_CAPTURE_ROOT", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "settings"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "data"))
    load_environment()
    assert config_directory() == tmp_path / "settings/proflame2"
    assert capture_root() == tmp_path / "data/proflame2/waveform"


@pytest.mark.parametrize("text", ["invalid", "OTHER=value", "export PROFLAME2_TEST=value", "PROFLAME2_TEST=two words"])
def test_invalid_configuration_rejected_without_values(tmp_path, text):
    with pytest.raises(ValueError, match="line 1") as error:
        load_environment(private_file(tmp_path, text))
    assert str(error.value) in {
        "invalid waveform configuration key on line 1",
        "invalid waveform configuration value on line 1",
    }


def test_shared_permissions_and_source_configuration_rejected(tmp_path):
    path = private_file(tmp_path, "PROFLAME2_TEST=secret")
    path.chmod(0o640)
    with pytest.raises(ValueError, match="owner-only"):
        load_environment(path)
    with pytest.raises(ValueError, match="outside"):
        load_environment(Path(__file__).resolve().parents[2] / "waveform.env")
    other = tmp_path / "another-checkout"
    other.mkdir()
    (other / ".git").touch()
    with pytest.raises(ValueError, match="outside"):
        load_environment(other / "waveform.env")


def test_explicit_missing_configuration_is_not_ignored(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_environment(tmp_path / "missing")


def test_command_wrapper_loads_without_shell_or_secret_output(tmp_path):
    path = private_file(tmp_path, "PROFLAME2_TEST=private-value")
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "tools.waveform.environment",
            "--",
            sys.executable,
            "-B",
            "-c",
            "import os; assert os.environ['PROFLAME2_TEST'] == 'private-value'",
        ],
        env={**os.environ, "PROFLAME2_WAVEFORM_ENV": str(path)},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "private-value" not in result.stdout + result.stderr
