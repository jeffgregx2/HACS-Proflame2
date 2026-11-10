"""Low manual gain never silently selects AGC or rewrites the baseline setup."""

import copy
import json

import pytest

from tools.waveform import waveform_collect as collector
from tools.waveform import waveform_pilot as pilot
from tools.waveform.waveform_gain import inspect_capture, manual_gain
from tools.waveform.waveform_manifest import validate_session_files
from tools.waveform.waveform_pilot_config import make_config


def configuration():
    return make_config(
        container="esphome",
        configuration="/config/receiver.yaml",
        band="315",
        gain=20,
        tool_version="verified-test-version",
        source="native",
        source_sha="a" * 40,
        verified=True,
    )


@pytest.mark.parametrize("value", [0, 0.01, -0.01, float("nan"), float("inf"), -float("inf")])
def test_rejects_automatic_or_nonfinite_gain(value):
    with pytest.raises(ValueError, match="gain"):
        manual_gain(value)
    config = configuration()
    with pytest.raises(ValueError, match="gain"):
        pilot.capture_overrides(config, gain=value, source=None, gain_check=False)
    config["session"]["sdr"]["gain_db"] = value
    with pytest.raises(ValueError, match="gain"):
        collector.validate_config(config)


def test_attempt_overrides_metadata_and_command_without_mutating_original():
    baseline = configuration()
    baseline["inventory"]["devices"][0]["firmware_version"] = "native-only"
    original = copy.deepcopy(baseline)
    config = pilot.capture_overrides(baseline, gain=0.9, source="lilygo", gain_check=True)
    collector.validate_config(config)
    assert baseline == original
    args = config["sdr_command"]
    assert args[args.index("-g") + 1] == "0.9"
    assert "-v" in args
    assert args[-3:] == ["-v", "-F", "log:/dev/stderr"]
    assert config["session"]["sdr"]["gain_db"] == 0.9
    assert config["session"]["source_alias"] == "lilygo"
    source = config["inventory"]["devices"][0]
    assert source["role"] == "transmitter"
    assert source["firmware_version"] is None
    assert collector.configured_protocol(config, 1) == ["light_6", "light_0"]
    assert collector.configured_protocol(baseline, 1) == list(collector.SEQUENCE) + ["quiet"]
    with pytest.raises(ValueError):
        collector.configured_protocol(config, 2)


@pytest.mark.parametrize(
    "args",
    [
        ["rtl_433", "{iq}"],
        ["rtl_433", "-g"],
        ["rtl_433", "-g", "20", "-g", "30", "{iq}"],
        ["rtl_433", "-g", "20", "-g30", "{iq}"],
        ["other", "-g", "20", "{iq}"],
    ],
)
def test_refuses_ambiguous_gain_commands(args):
    config = configuration()
    config["sdr_command"] = args
    with pytest.raises(ValueError):
        pilot.capture_overrides(config, gain=0.9, source=None, gain_check=True)


def test_short_check_requires_explicit_gain_and_no_hardware_during_dry_run(monkeypatch, capsys):
    with pytest.raises(ValueError):
        pilot.capture_overrides(configuration(), gain=None, source=None, gain_check=True)
    config = pilot.capture_overrides(configuration(), gain=0.9, source="lilygo", gain_check=True)
    monkeypatch.setattr(pilot, "run", lambda *args: pytest.fail("hardware opened"))
    assert pilot.launch(config, 1, True) == (0, None)
    text = capsys.readouterr().out
    assert "light_6" in text and "light_0" in text
    assert "fan_6" not in text and "005 quiet" not in text


def test_source_alias_preserves_only_same_device_and_rejects_conflicts():
    config = configuration()
    config["inventory"]["devices"][0]["firmware_version"] = "verified-native-model"
    same = pilot.capture_overrides(config, gain=None, source="native", gain_check=False)
    assert same["inventory"]["devices"][0]["firmware_version"] == "verified-native-model"
    assert same["sdr_command"] == config["sdr_command"]
    other = copy.deepcopy(config["inventory"]["devices"][0])
    other["alias"] = "lilygo"
    config["inventory"]["devices"].append(other)
    with pytest.raises(ValueError, match="conflicts"):
        pilot.capture_overrides(config, gain=None, source="lilygo", gain_check=False)
    with pytest.raises(ValueError, match="unsupported source"):
        pilot.capture_overrides(config, gain=None, source="other", gain_check=False)
    with pytest.raises(ValueError):
        collector.configured_protocol({"capture_protocol": "typo"}, 1)


@pytest.mark.parametrize("clipped", [False, True])
def test_streamed_clipping_screen(tmp_path, clipped):
    iq = tmp_path / "signal.cu8"
    iq.write_bytes((b"\x00\xff" if clipped else b"\x60\xa0") * 20001)
    log = tmp_path / "sdr.stderr"
    log.write_text("[SDR] Tuner gain set to 0.900000 dB.\n")
    report = inspect_capture(iq, log, 0.9, 100000)
    assert report["manual_gain_confirmed"]
    assert report["reported_gain_db"] == 0.9
    assert report["clipped_fraction"] == float(clipped)
    assert report["peak_100ms_clipped_fraction"] == float(clipped)
    assert report["duration_s"] == pytest.approx(0.20001)
    assert report["sampled_iq_pairs"] > 625


@pytest.mark.parametrize(
    "log_text",
    [
        "",
        "Tuner gain set to Auto.",
        "Tuner gain set to 0.900000 dB.\nFailed to set tuner gain.",
        "Tuner gain set to 0.900000 dB.\nTuner gain set to Auto.",
    ],
)
def test_does_not_claim_manual_gain_without_confirmation(tmp_path, log_text):
    iq, log = tmp_path / "signal.cu8", tmp_path / "sdr.stderr"
    iq.write_bytes(b"\x80\x80" * 100)
    log.write_text(log_text)
    report = inspect_capture(iq, log, 0.9, 1000000)
    assert not report["manual_gain_confirmed"]
    assert "no-clipping does not establish RF reception" in report["limitation"]


@pytest.mark.parametrize("data", [b"", b"\x00"])
def test_refuses_incomplete_iq(tmp_path, data):
    iq = tmp_path / "signal.cu8"
    iq.write_bytes(data)
    with pytest.raises(ValueError, match="IQ pairs"):
        inspect_capture(iq, tmp_path / "unused", 0.9, 1000000)


def test_reported_tuner_gain_is_not_assumed_equal_to_requested(tmp_path):
    iq, log = tmp_path / "signal.cu8", tmp_path / "sdr.stderr"
    iq.write_bytes(b"\x7f\x80" * 20000 + b"\x00\xff" * 10000)
    log.write_text("Tuner gain set to 1.400000 dB.")
    report = inspect_capture(iq, log, 0.9, 100000)
    assert report["reported_gain_db"] == 1.4
    assert report["requested_gain_db"] == 0.9
    assert report["manual_gain_confirmed"]
    assert 0 < report["clipped_fraction"] < report["peak_100ms_clipped_fraction"]
    with pytest.raises(ValueError, match="positive"):
        inspect_capture(iq, log, 0.9, 0)


@pytest.mark.parametrize("confirmed", [True, False])
def test_check_artifacts_are_hashed_even_if_gain_unconfirmed(tmp_path, monkeypatch, confirmed):
    config = pilot.capture_overrides(configuration(), gain=0.9, source="lilygo", gain_check=True)

    def trial(root, config, index, command):
        trial_id = f"trial-{index:03d}-{command}"
        folder = root / trial_id
        folder.mkdir()
        (folder / "signal.cu8").write_bytes(b"\x80\x80" * 100)
        (folder / "sdr.stderr").write_text("Tuner gain set to 0.900000 dB." if confirmed else "")
        return {"trial_id": trial_id}

    monkeypatch.setattr(collector, "collect_trial", trial)
    root = tmp_path / "session"
    if confirmed:
        collector.run(config, root, 1)
    else:
        with pytest.raises(ValueError, match="not confirmed"):
            collector.run(config, root, 1)
    session = json.loads((root / "session.json").read_text())
    assert session["status"] == ("complete" if confirmed else "failed")
    assert len(session["trials"]) == (2 if confirmed else 1)
    assert any(a["filename"].endswith("gain-check.json") for a in session["artifacts"])
    validate_session_files(session, root)


def test_gain_confirmation_survives_null_packet_output(tmp_path, monkeypatch):
    """Model rtl_433's independent verbosity and structured-log routing gates."""
    config = pilot.capture_overrides(configuration(), gain=0.9, source="lilygo", gain_check=True)
    commands = []

    def trial(root, actual, index, command):
        commands.append(command)
        args = actual["sdr_command"]
        formats = [args[i + 1] for i, value in enumerate(args[:-1]) if value == "-F"]
        assert "null" in formats
        gain_visible = "-v" in args and "log:/dev/stderr" in formats
        trial_id = f"trial-{index:03d}-{command}"
        folder = root / trial_id
        folder.mkdir()
        (folder / "signal.cu8").write_bytes(b"\x80\x80" * 100)
        (folder / "sdr.stderr").write_text("SDR: Tuner gain set to 0.900000 dB.\n" if gain_visible else "")
        return {"trial_id": trial_id}

    monkeypatch.setattr(collector, "collect_trial", trial)
    root = tmp_path / "session"
    collector.run(config, root, 1)
    assert commands == ["light_6", "light_0"]
    assert json.loads((root / "session.json").read_text())["status"] == "complete"


def test_unconfirmed_gain_explains_why_remaining_trials_were_skipped(tmp_path, monkeypatch, capsys):
    def fail(*args):
        raise ValueError("SDR manual gain was not confirmed by recorder diagnostics")

    monkeypatch.setattr(pilot, "run", fail)
    code, _ = pilot.launch(configuration(), 1, output_root=tmp_path)
    assert code == 1
    assert "remaining trials were not run" in capsys.readouterr().err


def test_transmitter_offset_is_explicit_metadata_only():
    original = configuration()
    config = pilot.capture_overrides(original, gain=None, source="lilygo", gain_check=False, source_tx_offset_hz=60000)
    collector.validate_config(config)
    device = next(d for d in config["inventory"]["devices"] if d["alias"] == "lilygo")
    assert device["configuration"]["tx_frequency_offset_hz"] == 60000
    assert config["sdr_command"] == original["sdr_command"]
    assert config["session"]["sdr"] == original["session"]["sdr"]
    assert original["inventory"]["devices"][0]["configuration"]["tx_frequency_offset_hz"] is None
    for source, offset in [("native", 60000), ("lilygo", 100001), ("lilygo", -100001)]:
        with pytest.raises(ValueError):
            pilot.capture_overrides(original, gain=None, source=source, gain_check=False, source_tx_offset_hz=offset)
