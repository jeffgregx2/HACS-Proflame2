"""Fixed collection never substitutes commands or silently discards failed trials."""

import errno
import json
import socket
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from tools import waveform_collect as collector
from tools import waveform_pilot as pilot
from tools import waveform_relocate
from tools.waveform_manifest import validate_session_files


def configuration():
    def device(alias, role):
        return {
            "alias": alias,
            "role": role,
            "source_sha": None,
            "firmware_version": None,
            "framework": None,
            "esphome_version": None,
            "sdk_version": None,
            "compiler_version": None,
            "configuration": dict.fromkeys(
                (
                    "rf_band",
                    "profile",
                    "rx_mode",
                    "repeat_count",
                    "delayed_retry",
                    "pre_tx_settle_us",
                    "radio_ready_timeout_us",
                    "register_trace",
                    "pcm_logging",
                )
            ),
            "registers": None,
        }

    return {
        "inventory": {"schema_version": 1, "devices": [device("native", "native"), device("receiver", "receiver")]},
        "session": {
            "schema_version": 1,
            "session_id": "bench",
            "inventory_sha256": "0" * 64,
            "tool_source_sha": "a" * 40,
            "source_alias": "native",
            "receiver_alias": "receiver",
            "started_utc": "2026-10-28T00:00:00Z",
            "status": "planned",
            "sdr": {
                "sample_rate_hz": 1000000,
                "center_frequency_hz": 315000000,
                "gain_db": 20,
                "sample_format": "cu8",
                "tool_version": "verified-installed-version",
                "dropped_samples": None,
            },
            "trials": [],
            "artifacts": [],
            "deliberate_changes": [],
        },
        "device_lock": "test-device",
        "receiver_command": ["receiver-logger"],
        "sdr_command": ["recorder", "{iq}"],
        "recorder_options_verified": True,
    }


def test_fixed_protocol():
    assert collector.protocol() == list(collector.SEQUENCE) * 5 + ["quiet"]
    for cycles in (0, 21):
        with pytest.raises(ValueError):
            collector.protocol(cycles)


def test_relocation_verifies_and_removes_only_original(tmp_path):
    source = tmp_path / "old"
    source.mkdir()
    (source / "empty").mkdir()
    (source / "data.cu8").write_bytes(b"\x00\xff" * 100)
    destination = tmp_path / "persistent" / "run"
    waveform_relocate.relocate(source, destination)
    assert not source.exists()
    assert (destination / "data.cu8").read_bytes() == b"\x00\xff" * 100
    assert (destination / "empty").is_dir()
    assert list(destination.parent.iterdir()) == [destination]


def test_relocation_retains_original_on_verification_failure(tmp_path, monkeypatch):
    source = tmp_path / "old"
    source.mkdir()
    (source / "data").write_bytes(b"original")
    original = waveform_relocate.inventory
    monkeypatch.setattr(waveform_relocate, "inventory", lambda p: original(p) if p == source else {})
    with pytest.raises(ValueError, match="verification failed"):
        waveform_relocate.relocate(source, tmp_path / "persistent" / "run")
    assert (source / "data").read_bytes() == b"original"
    assert list((tmp_path / "persistent").iterdir()) == []


def test_relocation_refuses_links_and_existing_destination(tmp_path):
    source = tmp_path / "old"
    source.mkdir()
    (source / "link").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(ValueError, match="links"):
        waveform_relocate.relocate(source, tmp_path / "new")
    assert source.exists()
    with pytest.raises(FileExistsError):
        waveform_relocate.relocate(source, source)


def test_low_storage_refuses_before_recorders_or_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(collector.shutil, "disk_usage", lambda _: Mock(free=1024))
    record = Mock()
    monkeypatch.setattr(collector, "collect_trial", record)
    with pytest.raises(OSError) as caught:
        collector.run(configuration(), tmp_path / "session", 1)
    assert caught.value.errno == errno.ENOSPC
    assert not (tmp_path / "session").exists()
    record.assert_not_called()


def test_low_storage_during_capture_preserves_metadata_reserve(tmp_path, monkeypatch):
    monkeypatch.setattr(collector.shutil, "disk_usage", lambda _: Mock(free=1024))
    capture = collector.Capture([], tmp_path / "receiver.stdout", tmp_path / "signal.cu8", 1000000)
    with pytest.raises(OSError) as caught:
        capture.text()
    assert caught.value.errno == errno.ENOSPC


def test_failed_metadata_write_is_not_published_and_cleans_scratch(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        args[1].write("partial")
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(collector.json, "dump", fail)
    with pytest.raises(OSError):
        collector.write_json(tmp_path / "trial.json", {})
    assert list(tmp_path.iterdir()) == []


def test_metadata_publication_never_replaces_existing_evidence(tmp_path):
    target = tmp_path / "trial.json"
    collector.write_json(target, {"original": True})
    with pytest.raises(FileExistsError):
        collector.write_json(target, {"replacement": True})
    assert json.loads(target.read_text()) == {"original": True}
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("error_code", [errno.ENOSPC, errno.EDQUOT, errno.EIO])
def test_pilot_reports_os_errno_without_private_path(tmp_path, monkeypatch, capsys, error_code):
    monkeypatch.setattr(pilot.tempfile, "mkdtemp", lambda **kwargs: str(tmp_path))
    monkeypatch.setattr(pilot, "run", Mock(side_effect=OSError(error_code, "private path")))
    code, _ = pilot.launch(configuration(), 1, output_root=tmp_path)
    assert code == 1
    output = capsys.readouterr().err
    assert "private path" not in output
    assert (
        "quota exceeded" if error_code == errno.EDQUOT else "storage" if error_code == errno.ENOSPC else "EIO"
    ) in output


def test_pilot_launcher_dry_run_never_starts_recorders(monkeypatch, capsys):
    run = Mock(side_effect=AssertionError("must not start"))
    monkeypatch.setattr(pilot, "run", run)
    assert pilot.launch(configuration(), 1, True) == (0, None)
    assert "005 quiet (60s)" in capsys.readouterr().out
    run.assert_not_called()


@pytest.mark.parametrize("outcome", ["success", "failure", "abort", "archive_failure"])
def test_pilot_launcher_preserves_and_packages_each_attempt(tmp_path, monkeypatch, capsys, outcome):
    monkeypatch.setattr(pilot.tempfile, "mkdtemp", lambda **kwargs: str(tmp_path))

    def run(config, session, cycles):
        session.mkdir()
        (session / "session.json").write_text("{}")
        if outcome == "failure":
            folder = session / "trial-001-light_6"
            folder.mkdir()
            (folder / "sdr.stderr").write_text("usb_open error -3\nprivate detail")
            raise RuntimeError("sdr recorder exited before capture completed (exit 1)")
        if outcome == "abort":
            raise KeyboardInterrupt

    monkeypatch.setattr(pilot, "run", run)
    pack = Mock(side_effect=ValueError("bad archive") if outcome == "archive_failure" else None)
    monkeypatch.setattr(pilot, "pack_session", pack)
    code, path = pilot.launch(configuration(), 1, output_root=tmp_path)
    assert path == tmp_path
    assert code == (0 if outcome == "success" else 130 if outcome == "abort" else 1)
    assert (path / "session/session.json").exists()
    pack.assert_called_once_with(path / "session", path / "session.tar.gz", status=pilot.archive_status)
    captured = capsys.readouterr()
    stderr = captured.err
    assert "No more buttons are needed" in captured.out
    if outcome != "archive_failure":
        assert "ALL DONE" in captured.out
    assert "private detail" not in stderr
    if outcome == "failure":
        assert "SDR USB access denied" in stderr
        assert "sdr recorder exited with code 1" in stderr


@pytest.mark.parametrize(
    "change",
    [
        lambda c: c.update(receiver_command="shell command"),
        lambda c: c.update(sdr_command=["recorder"]),
        lambda c: c.update(sdr_command=["recorder", "{iq}{iq}"]),
        lambda c: c.update(device_lock="../bad"),
        lambda c: c.update(recorder_options_verified=False),
        lambda c: c["session"]["sdr"].update(sample_format="cf32"),
        lambda c: c["session"]["sdr"].update(gain_db=None),
        lambda c: c["session"]["sdr"].update(tool_version=None),
        lambda c: c["session"]["sdr"].update(sample_rate_hz=1),
        lambda c: c["session"].update(receiver_alias="native"),
        lambda c: c["session"].update(source_alias="receiver"),
    ],
)
def test_reject_invalid_configuration(change):
    config = configuration()
    change(config)
    with pytest.raises(ValueError):
        collector.validate_config(config)


def test_dry_run_opens_nothing(tmp_path, monkeypatch, capsys):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(configuration()))
    monkeypatch.setattr(sys, "argv", ["collect", str(path), str(tmp_path / "unused"), "--dry-run", "--cycles", "1"])
    monkeypatch.setattr(collector, "Recorder", Mock(side_effect=AssertionError("must not open hardware")))
    collector.main()
    assert "005 quiet (60s)" in capsys.readouterr().out
    assert not (tmp_path / "unused").exists()


def test_recorder_process_lifecycle(tmp_path):
    recorder = collector.Recorder(
        [sys.executable, "-u", "-c", "import time; print('ready'); time.sleep(60)"], tmp_path / "rx"
    )
    try:
        recorder.check()
        assert recorder.process.poll() is None
    finally:
        recorder.stop()
    assert recorder.process.poll() is not None
    assert recorder.stack._exit_callbacks == [] or not recorder.stack._exit_callbacks


def test_recorder_start_failure_closes_files(tmp_path):
    with pytest.raises(FileNotFoundError):
        collector.Recorder([str(tmp_path / "missing")], tmp_path / "rx")
    assert (tmp_path / "rx.stdout").exists()


def test_capture_size_limits_and_early_exit(tmp_path, monkeypatch):
    log = tmp_path / "receiver.stdout"
    log.write_text("ready")
    iq = tmp_path / "signal.cu8"
    iq.write_bytes(b"1234")
    recorder = Mock()
    capture = collector.Capture([recorder], log, iq, 1)
    assert capture.wait(0) == "ready"
    recorder.check.side_effect = RuntimeError("exited")
    with pytest.raises(RuntimeError, match="exited"):
        capture.wait(0)
    recorder.check.side_effect = None
    with pytest.raises(TimeoutError):
        capture.wait(0, lambda _: False)
    capture.max_iq_bytes = 2
    with pytest.raises(RuntimeError, match="IQ exceeded"):
        capture.text()
    capture.max_iq_bytes = 10
    monkeypatch.setattr(collector, "MAX_LOG_BYTES", 1)
    with pytest.raises(RuntimeError, match="log exceeded"):
        capture.text()


def test_aborted_and_failed_sessions_remain_verifiable(tmp_path, monkeypatch):
    for error, status in ((KeyboardInterrupt(), "aborted"), (RuntimeError("recorder failed"), "failed")):
        root = tmp_path / status
        monkeypatch.setattr(collector, "collect_trial", Mock(side_effect=error))
        with pytest.raises(type(error)):
            collector.run(configuration(), root, 1)
        session = json.loads((root / "session.json").read_text())
        assert session["status"] == status
        validate_session_files(session, root)


def test_completed_session_hashes_and_sequence(tmp_path, monkeypatch):
    seen = []

    def trial(root, config, index, command):
        seen.append(command)
        return {"trial_id": str(index)}

    monkeypatch.setattr(collector, "collect_trial", trial)
    root = tmp_path / "complete"
    collector.run(configuration(), root, 1)
    session = json.loads((root / "session.json").read_text())
    assert session["status"] == "complete"
    assert seen == list(collector.SEQUENCE) + ["quiet"]
    validate_session_files(session, root)
    with pytest.raises(FileExistsError):
        collector.run(configuration(), root, 1)


def test_pilot_default_storage_is_persistent():
    assert pilot.CAPTURE_ROOT == Path("/srv/Captures/HACS-proflame2/waveform")
    assert not pilot.CAPTURE_ROOT.is_relative_to("/tmp")


def test_device_lease_does_not_steal_owner(tmp_path, monkeypatch):
    config = configuration()
    config["device_lock"] = "lease-" + tmp_path.name
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as owner:
        owner.bind("\0proflame2-sdr-" + config["device_lock"])
        collect = Mock()
        monkeypatch.setattr(collector, "collect_trial", collect)
        with pytest.raises(OSError):
            collector.run(config, tmp_path / "locked", 1)
        collect.assert_not_called()
    session = json.loads((tmp_path / "locked/session.json").read_text())
    assert session["status"] == "failed"


@pytest.mark.parametrize("data", [b"q\n", b"", b"x" * 4097, b"actual light=5\n"])
def test_operator_input_is_bounded_and_abortable(tmp_path, monkeypatch, data):
    log = tmp_path / "receiver.stdout"
    log.write_text("ready")
    capture = collector.Capture([], log, tmp_path / "signal.cu8", 1)
    monkeypatch.setattr(collector.select, "select", lambda *args: ([sys.stdin], [], []))
    monkeypatch.setattr(collector.os, "read", lambda *args: data)
    monkeypatch.setattr(sys, "stdin", Mock(fileno=lambda: 0))
    markers = []
    if data in (b"q\n", b""):
        with pytest.raises(KeyboardInterrupt):
            capture.wait(1, lambda _: False, lambda note, timestamp: markers.append(note))
    elif len(data) > 4096:
        with pytest.raises(RuntimeError, match="bounded marker buffer"):
            capture.wait(1, lambda _: False, lambda note, timestamp: markers.append(note))
    else:
        capture.wait(1, lambda _: bool(markers), lambda note, timestamp: markers.append(note))
        assert markers == ["actual light=5"]


def test_previous_step_return_is_discarded_before_new_prompt(tmp_path, monkeypatch, capsys):
    capture = collector.Capture([], tmp_path / "receiver.stdout", tmp_path / "signal.cu8", 1)
    capture.input_buffer = "\n"
    ready = iter([([sys.stdin], [], []), ([], [], [])])
    monkeypatch.setattr(collector.select, "select", lambda *args: next(ready))
    monkeypatch.setattr(collector.os, "read", lambda *args: b"\n")
    monkeypatch.setattr(sys, "stdin", Mock(fileno=lambda: 0))
    capture.discard_queued_input()
    assert capture.input_buffer == ""
    assert "Cleared queued keyboard input" in capsys.readouterr().out


@pytest.mark.parametrize("data", [b"q\n", b"", b"x" * 4097])
def test_discarding_old_input_keeps_abort_and_size_guards(tmp_path, monkeypatch, data):
    capture = collector.Capture([], tmp_path / "receiver.stdout", tmp_path / "signal.cu8", 1)
    ready = iter([([sys.stdin], [], []), ([], [], [])])
    monkeypatch.setattr(collector.select, "select", lambda *args: next(ready))
    monkeypatch.setattr(collector.os, "read", lambda *args: data)
    monkeypatch.setattr(sys, "stdin", Mock(fileno=lambda: 0))
    with pytest.raises(KeyboardInterrupt if len(data) < 4096 else RuntimeError):
        capture.discard_queued_input()


@pytest.mark.parametrize(
    "failure",
    [
        None,
        "short_budget",
        "early_close",
        "broken_export",
        "sdr_start",
        "start_timeout",
        "target_timeout",
        "stop_timeout",
        "receiver_failed",
    ],
)
@pytest.mark.parametrize("buffered", [False, True])
def test_trial_lifecycle(tmp_path, monkeypatch, failure, capsys, buffered):
    config = configuration()
    stopped = []

    class Recorder:
        started_ns = 1

        def __init__(self, args, prefix):
            if failure == "sdr_start" and prefix.name == "sdr":
                raise RuntimeError("launch failed")
            self.name = prefix.name
            self.label = prefix.name
            prefix.with_suffix(".stdout").write_text("")
            prefix.with_suffix(".stderr").write_text("")
            if self.name == "sdr":
                Path(args[-1]).write_bytes(b"1234")

        def stop(self):
            if self.name not in stopped:
                stopped.append(self.name)

    class Capture:
        def __init__(self, *args):
            self.step = 0

        def text(self):
            return ""

        def discard_queued_input(self):
            assert self.step == 3  # After Start/preparation, before target confirmation.

        def wait(self, seconds, predicate=lambda _: True, marker=None):
            self.step += 1
            if self.step == 2:
                if failure == "start_timeout":
                    raise TimeoutError
                return f"PFSESSION session=5 state=armed duration_ms={1000 if failure == 'short_budget' else 90000}"
            if self.step == 4:
                if failure == "target_timeout":
                    raise TimeoutError
                if failure == "early_close":
                    predicate("PFSESSION session=5 state=aborted")
                marker("actual light=5", 10**20)
            if self.step == 5:
                assert marker is not None
                assert seconds == 16  # A separate, full post-confirmation observation interval.
            if self.step == 6 and failure == "stop_timeout":
                raise TimeoutError
            if self.step == 6 and failure == "receiver_failed":
                predicate("PFSESSION session=5 state=failed code=9")
            if self.step == 6 and buffered:
                assert seconds == 600
                assert not predicate("PFSTOP1 session=5 ended_us=100000\n")
                assert stopped == ["sdr"]
            return (
                "PFSESSION session=5 state=armed duration_ms=90000\n"
                "PFSESSION session=5 state=aborted exports=0 suppressed=0 end_us=100000"
            )

    monkeypatch.setattr(collector, "Recorder", Recorder)
    monkeypatch.setattr(collector, "Capture", Capture)
    monkeypatch.setattr(
        collector,
        "read_windows",
        Mock(side_effect=ValueError("incomplete") if failure == "broken_export" else None, return_value=[]),
    )
    if failure:
        with pytest.raises((RuntimeError, ValueError, TimeoutError)):
            collector.collect_trial(tmp_path, config, 1, "light_6")
    else:
        trial = collector.collect_trial(tmp_path, config, 1, "light_6")
        assert trial["desired_state"] == {"light": 6}
        assert trial["decoded_state"] is None
        assert trial["rf_sample_start"] is None
        assert "no_receiver_exports" in trial["quality_flags"]
        acquisition = json.loads((tmp_path / trial["trial_id"] / "acquisition.json").read_text())
        assert acquisition["markers"][0]["note"] == "actual light=5"
        assert acquisition["iq_samples"] == 2
        assert acquisition["target_confirmed_monotonic_ns"] == 10**20
        assert acquisition["action_timeout_seconds"] == 60
        output = capsys.readouterr().out
        assert "Do NOT press Return after each remote button press" in output
        assert "TARGET CONFIRMED by operator" in output
        assert "START CONFIRMED by receiver" in output
        assert "STOP CONFIRMED by receiver" in output
        assert ("ACQUISITION STOPPED" in output) is buffered
    assert stopped == (["receiver"] if failure == "sdr_start" else ["sdr", "receiver"])
