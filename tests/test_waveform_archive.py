"""Private capture handoffs reject incomplete, unsafe and modified archives."""

import io
import json
import tarfile

import pytest

from tools import waveform_archive as archiver
from tools import waveform_observation as observations
from tools.waveform_collect import artifact


@pytest.fixture
def session_directory(tmp_path):
    root = tmp_path / "session"
    root.mkdir()
    inventory = {"schema_version": 1, "devices": []}
    for alias, role in (("native", "native"), ("receiver", "receiver")):
        inventory["devices"].append(
            {
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
        )
    (root / "inventory.json").write_text(json.dumps(inventory))
    folder = root / "trial-001-light_6"
    folder.mkdir()
    trial = {
        "schema_version": 1,
        "trial_id": folder.name,
        "source_alias": "native",
        "command": "light_6",
        "desired_state": {"light": 6},
        "decoded_state": None,
        "prompt_monotonic_ns": 1,
        "action_monotonic_ns": None,
        "rf_sample_start": None,
        "receiver_capture_ids": [],
        "retry_of": None,
        "outcome": "unobserved",
        "quality_flags": [],
        "notes": "",
    }
    (folder / "trial.json").write_text(json.dumps(trial))
    (folder / "acquisition.json").write_text(json.dumps({"status": "complete", "iq_samples": 2}))
    (folder / "signal.cu8").write_bytes(b"\x80\x80\x81\x81")
    for name in ("receiver.stdout", "receiver.stderr", "sdr.stdout", "sdr.stderr"):
        (folder / name).write_bytes(b"")
    (folder / "receiver.stdout").write_text(
        "PFSESSION session=5 state=armed duration_ms=90000\n"
        "PFSESSION session=5 state=aborted exports=0 suppressed=0 end_us=100000\n"
    )
    session = {
        "schema_version": 1,
        "session_id": "pilot",
        "inventory_sha256": artifact(root / "inventory.json", root)["sha256"],
        "tool_source_sha": "a" * 40,
        "source_alias": "native",
        "receiver_alias": "receiver",
        "started_utc": "2026-10-28T00:00:00Z",
        "status": "complete",
        "sdr": {
            "sample_rate_hz": 1000000,
            "center_frequency_hz": 315000000,
            "gain_db": 20,
            "sample_format": "cu8",
            "tool_version": "verified-local-version",
            "dropped_samples": None,
        },
        "trials": [folder.name],
        "artifacts": [],
        "deliberate_changes": [],
    }
    (root / "session.json").write_text(json.dumps(session))
    refresh(root)
    return root


def refresh(root):
    path = root / "session.json"
    session = json.loads(path.read_text())
    session["artifacts"] = [artifact(p, root) for p in sorted(root.rglob("*")) if p.is_file() and p != path]
    path.write_text(json.dumps(session))


def test_round_trip_is_private_and_byte_exact(session_directory, tmp_path):
    path = tmp_path / "capture.tar.gz"
    session = archiver.pack_session(session_directory, path)
    assert archiver.verify_archive(path) == session
    assert path.stat().st_mode & 0o777 == 0o600
    with tarfile.open(path) as archive:
        for member in archive:
            assert member.uid == member.gid == member.mtime == 0
            assert member.uname == member.gname == ""
            assert archive.extractfile(member).read() == (session_directory / member.name).read_bytes()
    assert not list(tmp_path.glob("proflame2-archive-*"))
    with pytest.raises(FileExistsError):
        archiver.pack_session(session_directory, path)


@pytest.mark.parametrize(
    "mutation",
    [
        "changed",
        "extra",
        "missing",
        "symlink",
        "bad_inventory_hash",
        "unlisted_trial",
        "wrong_source",
        "partial",
        "sample_count",
        "missing_recording",
    ],
)
def test_invalid_session_is_not_published(session_directory, tmp_path, mutation):
    root = session_directory
    trial_path = root / "trial-001-light_6/trial.json"
    if mutation == "changed":
        (root / "trial-001-light_6/signal.cu8").write_bytes(b"bad!")
    elif mutation == "extra":
        (root / "extra.bin").write_bytes(b"not declared")
    elif mutation == "missing":
        trial_path.unlink()
    elif mutation == "symlink":
        (root / "link").symlink_to(trial_path)
    elif mutation == "bad_inventory_hash":
        session = json.loads((root / "session.json").read_text())
        session["inventory_sha256"] = "0" * 64
        (root / "session.json").write_text(json.dumps(session))
    elif mutation == "unlisted_trial":
        session = json.loads((root / "session.json").read_text())
        session["trials"] = []
        (root / "session.json").write_text(json.dumps(session))
    elif mutation == "wrong_source":
        trial = json.loads(trial_path.read_text())
        trial["source_alias"] = "other"
        trial_path.write_text(json.dumps(trial))
        refresh(root)
    elif mutation in {"partial", "sample_count"}:
        path = root / "trial-001-light_6/acquisition.json"
        value = json.loads(path.read_text())
        value.update({"status": "failed"} if mutation == "partial" else {"iq_samples": 999})
        path.write_text(json.dumps(value))
        refresh(root)
    else:
        (root / "trial-001-light_6/sdr.stdout").unlink()
        refresh(root)
    destination = tmp_path / "invalid.tar.gz"
    with pytest.raises(ValueError):
        archiver.pack_session(root, destination)
    assert not destination.exists()


@pytest.mark.parametrize("status", ["failed", "aborted"])
def test_failed_evidence_can_be_archived_without_becoming_success(session_directory, tmp_path, status):
    path = session_directory / "session.json"
    session = json.loads(path.read_text())
    session.update(status=status, trials=[])
    path.write_text(json.dumps(session))
    archive = tmp_path / "failed.tar.gz"
    archiver.pack_session(session_directory, archive)
    assert archiver.verify_archive(archive)["status"] == status


@pytest.mark.parametrize(
    "kind",
    ["traversal", "absolute", "duplicate", "symlink", "directory", "missing_manifest", "metadata_limit", "bad_json"],
)
def test_untrusted_archive_never_extracts(tmp_path, monkeypatch, kind):
    path = tmp_path / "unsafe.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        name = {"traversal": "../escape", "absolute": "/escape"}.get(kind, "session.json")
        data = b"{}" if kind != "bad_json" else b'{"a": 1, "a": 2}'
        member = tarfile.TarInfo("other.bin" if kind == "missing_manifest" else name)
        member.size = len(data)
        if kind == "symlink":
            member.type = tarfile.SYMTYPE
            member.linkname = "../escape"
        elif kind == "directory":
            member.type = tarfile.DIRTYPE
        archive.addfile(member, io.BytesIO(data))
        if kind == "duplicate":
            archive.addfile(member, io.BytesIO(data))
    if kind == "metadata_limit":
        monkeypatch.setattr(archiver, "MAX_METADATA", 1)
    with pytest.raises((ValueError, json.JSONDecodeError)):
        archiver.verify_archive(path)
    assert not (tmp_path / "escape").exists()


@pytest.mark.parametrize("failure", [ValueError, KeyboardInterrupt])
def test_failed_final_validation_cleans_owned_scratch(session_directory, tmp_path, monkeypatch, failure):
    destination = tmp_path / "not-published.tar.gz"
    monkeypatch.setattr(archiver, "verify_archive", lambda _, **kwargs: (_ for _ in ()).throw(failure("changed")))
    with pytest.raises(failure):
        archiver.pack_session(session_directory, destination)
    assert not destination.exists()
    assert not list(tmp_path.glob("proflame2-archive-*"))


def test_archive_cli(session_directory, tmp_path, monkeypatch, capsys):
    archive = tmp_path / "cli.tar.gz"
    for args in (["pack", str(session_directory), str(archive)], ["verify", str(archive)]):
        monkeypatch.setattr("sys.argv", ["archive", *args])
        archiver.main()
        assert "status=complete" in capsys.readouterr().out


def test_metadata_requires_object():
    with pytest.raises(ValueError):
        archiver.metadata(b"[]")


def test_archive_progress_reports_all_phases_without_changing_bytes(session_directory, tmp_path):
    updates = []
    destination = tmp_path / "progress.tar.gz"
    original = archiver.inspect_directory(session_directory)
    assert archiver.pack_session(session_directory, destination, status=updates.append) == original
    assert archiver.verify_archive(destination) == original
    for phase in ("Checking source files", "Compressing recordings", "Verifying archive"):
        assert any(message.startswith(phase) and "starting" in message for message in updates)
        assert any(message.startswith(phase) and "; done" in message for message in updates)
    assert any("(100%)" in message for message in updates)


def test_progress_is_periodic_not_per_chunk(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(archiver.time, "monotonic", lambda: clock[0])
    messages = []
    progress = archiver.Progress("Compressing", messages.append, 100)
    progress.advance(10)
    assert len(messages) == 1
    clock[0] = 30
    progress.advance(40)
    assert len(messages) == 2
    assert "(50%)" in messages[-1]
    assert "elapsed 30s" in messages[-1]
    progress.advance(10)
    assert len(messages) == 2
    clock[0] = 60
    progress.advance(40)
    assert "(100%)" in messages[-1]


def test_complete_session_requires_valid_receiver_footer(session_directory, tmp_path):
    (session_directory / "trial-001-light_6/receiver.stdout").write_text(
        "PFSESSION session=5 state=armed duration_ms=90000\n"
    )
    refresh(session_directory)
    with pytest.raises(ValueError, match="incomplete receiver"):
        archiver.pack_session(session_directory, tmp_path / "incomplete.tar.gz")


def test_aggregate_metadata_budget(session_directory, tmp_path, monkeypatch):
    monkeypatch.setattr(archiver, "MAX_METADATA_TOTAL", 1)
    with pytest.raises(ValueError, match="aggregate"):
        archiver.pack_session(session_directory, tmp_path / "oversized.tar.gz")


def test_truncated_compressed_transfer_is_rejected(session_directory, tmp_path):
    path = tmp_path / "truncated.tar.gz"
    archiver.pack_session(session_directory, path)
    path.write_bytes(path.read_bytes()[:-8])
    with pytest.raises((EOFError, OSError, tarfile.ReadError)):
        archiver.verify_archive(path)


def test_observation_preserves_original_hashes_and_unknowns(session_directory, tmp_path):
    before = {p: p.read_bytes() for p in session_directory.rglob("*") if p.is_file()}
    output = tmp_path / "observation.json"
    result = observations.record_observation(
        session_directory,
        "trial-001-light_6",
        output,
        operator_state={"light": 5},
        notes="Actual slider selection was 5",
    )
    assert result["operator_state"] == {"light": 5}
    assert result["physical_outcome"] == "unobserved"
    assert result["rf_association"] is None
    assert (
        result["source_trial_sha256"]
        == artifact(session_directory / "trial-001-light_6/trial.json", session_directory)["sha256"]
    )
    assert before == {p: p.read_bytes() for p in before}
    assert archiver.inspect_directory(session_directory)["status"] == "complete"
    with pytest.raises(FileExistsError):
        observations.record_observation(session_directory, "trial-001-light_6", output, operator_state=None)


@pytest.mark.parametrize("change", ["state", "outcome", "notes", "path", "inside", "missing"])
def test_invalid_observation_is_not_written(session_directory, tmp_path, change):
    output = tmp_path / "invalid-observation.json"
    args = {"operator_state": None}
    trial_id = "trial-001-light_6"
    if change == "state":
        args["operator_state"] = {"light": 7}
    elif change == "outcome":
        args["outcome"] = "acknowledged_by_fireplace"
    elif change == "notes":
        args["notes"] = "x" * 4097
    elif change == "path":
        trial_id = "../escape"
    elif change == "inside":
        output = session_directory / "new-observation.json"
    else:
        trial_id = "missing"
    with pytest.raises(ValueError):
        observations.record_observation(session_directory, trial_id, output, **args)
    assert not output.exists()


def test_observation_cli(session_directory, tmp_path, monkeypatch, capsys):
    path = tmp_path / "observation-cli.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "observation",
            str(session_directory),
            "trial-001-light_6",
            str(path),
            "--operator-state",
            '{"light":5}',
            "--outcome",
            "ambiguous",
        ],
    )
    observations.main()
    assert "original captures unchanged" in capsys.readouterr().out
    assert json.loads(path.read_text())["physical_outcome"] == "ambiguous"
