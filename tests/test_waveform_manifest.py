"""Capture manifests retain unknowns and reject unsafe/corrupt artifacts."""

import copy
import hashlib
import json

import pytest

from tools.waveform_manifest import main, validate_manifest, validate_session_files


def inventory():
    return {
        "schema_version": 1,
        "devices": [
            {
                "alias": "receiver-a",
                "role": "receiver",
                "source_sha": None,
                "firmware_version": None,
                "framework": "esp-idf",
                "esphome_version": None,
                "sdk_version": None,
                "compiler_version": None,
                "configuration": {
                    key: None
                    for key in (
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
                },
                "registers": None,
            }
        ],
    }


def session():
    return {
        "schema_version": 1,
        "session_id": "pilot-1",
        "inventory_sha256": "0" * 64,
        "tool_source_sha": "0" * 40,
        "source_alias": "native-a",
        "receiver_alias": "receiver-a",
        "started_utc": "2026-10-28T12:00:00Z",
        "status": "planned",
        "sdr": {
            "sample_rate_hz": 1000000,
            "center_frequency_hz": 315000000,
            "gain_db": 30,
            "sample_format": "cu8",
            "tool_version": None,
            "dropped_samples": None,
        },
        "trials": [],
        "artifacts": [],
        "deliberate_changes": [],
    }


def trial():
    return {
        "schema_version": 1,
        "trial_id": "trial-1",
        "source_alias": "native-a",
        "command": "light_6",
        "desired_state": {"power": True, "light": 6},
        "decoded_state": None,
        "prompt_monotonic_ns": 100,
        "action_monotonic_ns": None,
        "rf_sample_start": None,
        "receiver_capture_ids": [],
        "retry_of": None,
        "outcome": "unobserved",
        "quality_flags": [],
        "notes": "",
    }


@pytest.mark.parametrize("kind,factory", [("inventory", inventory), ("session", session), ("trial", trial)])
def test_schemas_accept_explicit_unknown_and_independent_outcomes(kind, factory):
    validate_manifest(kind, factory())


@pytest.mark.parametrize("mutation", ["version", "alias", "missing", "register", "unknown"])
def test_invalid_inventory_is_rejected_without_echoing_private_values(mutation):
    doc = inventory()
    if mutation == "version":
        doc["schema_version"] = 99
    elif mutation == "alias":
        doc["devices"].append(copy.deepcopy(doc["devices"][0]))
    elif mutation == "missing":
        del doc["devices"][0]["sdk_version"]
    elif mutation == "register":
        doc["devices"][0]["registers"] = {"FREQ0": 256}
    else:
        doc["private_extra_field"] = "do-not-echo-this-value"
    with pytest.raises(ValueError) as error:
        validate_manifest("inventory", doc)
    assert "do-not-echo-this-value" not in str(error.value)


@pytest.mark.parametrize("filename", ["../private.bin", "/private.bin", "a/../../private.bin", "a\\private.bin"])
def test_session_paths_are_relative_and_contained(filename):
    doc = session()
    doc["artifacts"] = [{"filename": filename, "sha256": "0" * 64, "bytes": 0}]
    with pytest.raises(ValueError):
        validate_manifest("session", doc)


def test_hash_checking_and_escaping_links(tmp_path):
    root = tmp_path / "session"
    root.mkdir()
    data = b"sample-recording"
    artifact = root / "recording.cu8"
    artifact.write_bytes(data)
    doc = session()
    doc["artifacts"] = [{"filename": "recording.cu8", "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}]
    validate_session_files(doc, root)
    artifact.write_bytes(b"X" * len(data))
    with pytest.raises(ValueError, match="checksum"):
        validate_session_files(doc, root)
    artifact.write_bytes(b"changed")
    with pytest.raises(ValueError, match="size"):
        validate_session_files(doc, root)
    artifact.unlink()
    outside = tmp_path / "outside.cu8"
    outside.write_bytes(data)
    artifact.symlink_to(outside)
    with pytest.raises(ValueError, match="outside"):
        validate_session_files(doc, root)


def test_trial_action_must_not_precede_prompt():
    doc = trial()
    doc["action_monotonic_ns"] = 99
    with pytest.raises(ValueError, match="precedes"):
        validate_manifest("trial", doc)


def test_duplicate_artifacts_and_unsupported_manifest_kind():
    doc = session()
    artifact = {"filename": "recording.cu8", "sha256": "0" * 64, "bytes": 0}
    doc["artifacts"] = [artifact, artifact]
    with pytest.raises(ValueError, match="duplicate"):
        validate_manifest("session", doc)
    with pytest.raises(ValueError, match="unsupported"):
        validate_manifest("other", {})


@pytest.mark.parametrize("kind,factory", [("inventory", inventory), ("session", session), ("trial", trial)])
def test_manifest_cli(tmp_path, monkeypatch, capsys, kind, factory):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(factory()))
    monkeypatch.setattr("sys.argv", ["manifest", kind, str(path), *(["--verify-files"] if kind == "session" else [])])
    main()
    assert "passed" in capsys.readouterr().out
    if kind != "session":
        monkeypatch.setattr("sys.argv", ["manifest", kind, str(path), "--verify-files"])
        with pytest.raises(SystemExit):
            main()
