"""Preserve offline investigation calculations without private recordings."""

import json
from pathlib import Path

import numpy as np
import pytest

from tools.waveform.analysis import audit_low_gain
from tools.waveform.analysis.analyze_manual_tx_log import analyze
from tools.waveform.analysis.audit_clusters import fft_metrics
from tools.waveform.analysis.compare_offset_runs import compare, grouped
from tools.waveform.analysis.settings import datasets


def test_carrier_centroid_for_synthetic_tone():
    time = np.arange(65536) / 1_000_000
    metrics = fft_metrics(np.exp(2j * np.pi * 15000 * time))
    assert metrics["local_500hz_centroid_hz"] == pytest.approx(15000, abs=1)
    assert "not independently calibrated" in metrics["note"]


def test_log_summary_does_not_invent_acknowledgements():
    result = analyze(
        "[11:00:00.000] RF tuning: tx_hz=314973000 rx_hz=314973000 tx_offset_hz=0\n"
        "[11:00:01.000] TX result status=ok request_id=test-1 elapsed_ms=825 repeat_count=10\n"
        "[11:00:03.000] TX retransmission scheduled original_request_id=test-1\n"
        "[11:00:03.100] TX retransmission queued original_request_id=test-1\n"
        "[11:00:03.900] TX retransmission result status=ok original_request_id=test-1 elapsed_ms=825\n"
    )
    assert result["counts"]["tx_ok"] == result["counts"]["retry_ok"] == 1
    assert result["configured_tuning"][0]["offset_hz"] == 0
    assert result["missing_retry_results"] == []
    assert result["elapsed_ms"]["tx"]["median"] == 825
    assert any("No fireplace ACK" in value for value in result["limits"])
    with pytest.raises(ValueError, match="duplicate"):
        analyze("TX result status=ok request_id=a\nTX result status=ok request_id=a")


def test_private_dataset_mapping(tmp_path, monkeypatch):
    monkeypatch.delenv("PROFLAME2_WAVEFORM_ENV", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config = tmp_path / "datasets.json"
    monkeypatch.setenv("PROFLAME2_ANALYSIS_DATASETS", str(config))
    expected = {"native": "remote-run", "baseline": "old-run", "candidate": "new-run"}
    config.write_text(json.dumps(expected))
    assert datasets() == expected
    config.write_text(json.dumps({**expected, "candidate": "../private"}))
    with pytest.raises(ValueError, match="directory names"):
        datasets()


def test_local_iq_read_is_bounded_and_read_only(tmp_path, monkeypatch):
    monkeypatch.delenv("PROFLAME2_CAPTURE_SSH_HOST", raising=False)
    path = tmp_path / "sample.cu8"
    path.write_bytes(bytes(range(20)))
    assert audit_low_gain.remote_bytes(path, 2, 5) == bytes(range(4, 10))
    assert path.read_bytes() == bytes(range(20))
    with pytest.raises(ValueError, match="short"):
        audit_low_gain.remote_bytes(path, 2, 20)
    with pytest.raises(ValueError, match="bounded"):
        audit_low_gain.remote_bytes(path, -1, 2)


def test_remote_identity_comes_only_from_environment(monkeypatch):
    monkeypatch.setenv("PROFLAME2_CAPTURE_SSH_HOST", "operator@capture-host")
    monkeypatch.setenv("PROFLAME2_CAPTURE_SSH_IDENTITY", "/private/capture-key")
    commands = []

    def read(command, **kwargs):
        commands.append(command)
        return b"1234"

    monkeypatch.setattr(audit_low_gain.subprocess, "check_output", read)
    assert audit_low_gain.remote_bytes(Path("/persistent/sample.cu8"), 1, 3) == b"1234"
    assert "operator@capture-host" in commands[0]
    assert "/private/capture-key" in commands[0]
    monkeypatch.setenv("PROFLAME2_CAPTURE_SSH_HOST", "-unsafe")
    with pytest.raises(ValueError, match="SSH host"):
        audit_low_gain.remote_bytes(Path("unused"), 1, 3)


def test_offset_comparison_preserves_matching_state_requirement():
    def report(carrier):
        return {
            "source_run": "synthetic",
            "metrics": {"carrier_relative_center_hz": {"median": carrier}},
            "events": [
                {
                    "trial": "light",
                    "structural_frame_count": 5,
                    "wire_words": [[1, 2, 3]],
                    "raw_half_power_widths": {"high_1": {"count": 10, "mean": 400}},
                }
            ],
        }

    before, after = report(-45000), report(15000)
    result = compare(before, after)
    assert result["measured_carrier_shift_hz"] == 60000
    assert result["offset_minus_native_reference_hz"] is None
    assert result["state_count"] == 1
    after["events"][0]["wire_words"] = [[4, 5, 6]]
    with pytest.raises(ValueError, match="states differ"):
        compare(before, after)
    after["events"][0]["structural_frame_count"] = 10
    with pytest.raises(ValueError, match="five-copy"):
        grouped(after)
