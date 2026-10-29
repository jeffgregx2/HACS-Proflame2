"""Diagnostic review keeps repeated associations and measurement bias explicit."""

import json
import random
from itertools import groupby
from types import SimpleNamespace

import pytest

from tools import waveform_pretrigger_review as pretrigger
from tools import waveform_review as review
from tools.waveform_pretrigger_review import structural_frames


def pair(rate=1000000):
    rng = random.Random(42)
    edges = tuple((1 if i % 2 == 0 else -1) * 417 * rng.choice((1, 2, 3)) for i in range(201))
    window = SimpleNamespace(edges=edges, metadata={"id": 1})
    event = {
        "event_id": 1,
        "start_sample": 100000,
        "signed_samples": [int(e * rate / 1000000) for e in edges],
        "end_sample": 100000 + int(sum(map(abs, edges)) * rate / 1000000),
    }
    return window, event


@pytest.mark.parametrize("rate", [1000000, 2000000])
def test_longer_burst_fit_keeps_units_and_original_runs(rate):
    window, event = pair(rate)
    original = tuple(window.edges)
    result = review.review_pair(window, event, sample_rate=rate)
    assert result["matching_runs"] == len(window.edges)
    assert result["fit"]["status"] == "fitted"
    assert result["fit"]["samples_per_us"] == pytest.approx(rate / 1000000)
    assert result["fit"]["sample_offset"] == pytest.approx(100000)
    assert window.edges == original


def test_repeated_signature_is_not_assigned_by_order():
    window, event = pair()
    event["signed_samples"] = event["signed_samples"][:100]
    window.edges = tuple(event["signed_samples"] * 2)
    assert review.review_pair(window, event, sample_rate=1000000) is None
    window.edges = window.edges[:20]
    assert review.review_pair(window, event, sample_rate=1000000) is None


def test_session_review_never_promotes_ambiguous_events(tmp_path, monkeypatch):
    window, event = pair()
    session, analysis = tmp_path / "session", tmp_path / "analysis"
    (session / "trial-001").mkdir(parents=True)
    (analysis / "trial-001").mkdir(parents=True)
    (analysis / "trial-001.log").write_text("Replay progress, not a trial directory")
    (session / "trial-001/receiver.stdout").write_text("mock receiver")
    monkeypatch.setattr(review, "read_windows", lambda *args, **kwargs: [window])
    matches = {
        "provenance": {"sample_rate_hz": 1000000},
        "matches": [
            {
                "status": "matched",
                "capture_id": 1,
                "candidates": [{"event_id": 1, "evidence": "decoded_payload_and_edges"}],
            },
            {"status": "ambiguous", "capture_id": 2, "candidates": []},
        ],
    }
    (analysis / "trial-001/matches.json").write_text(json.dumps(matches))
    (analysis / "trial-001/events.json").write_text(json.dumps([event]))
    report = review.review_session(session, analysis)
    assert report["precision_calibrated"] is False
    assert len(report["diagnostic_alignments"]) == 1
    assert report["trials"][0]["baseline_statuses"]["ambiguous"] == 1
    assert report["widths"]["high_1"]["delta_median_us"] == 0


@pytest.mark.parametrize("gap", [11, 12, 18, 24])
def test_structural_review_retains_short_gap_repeats_without_changing_operational_policy(gap):
    from custom_components.proflame2.protocol.packet import ProflameFrame
    from custom_components.proflame2.rf.waveform import frame_to_air_bytes
    from tools.waveform_diagnostics import classify_edges

    # Native pilot Fan-6 command/integrity bytes; anonymized serial and synthetic
    # nominal timing, not a raw capture or a TX-acceptance claim.
    frame = ProflameFrame(serial_id=0x123456, cmd1=1, cmd2=0x61, err1=0x76, err2=0x9F)
    core = "".join(f"{byte:08b}" for byte in frame_to_air_bytes(frame))[:182]
    bits = (core + "0" * gap) * 3
    edges = tuple((1 if bit == "1" else -1) * sum(1 for _ in runs) * 417 for bit, runs in groupby(bits))
    rows = structural_frames(edges)
    assert len(rows) == 3
    assert all(row["wire_words"] == list(frame.wire_words) for row in rows)
    assert [row["gap_bits"] for row in rows] == [gap, gap, gap]
    assert len(classify_edges(edges)) == (3 if gap >= 18 else 0)


def test_structural_review_rejects_invalid_manchester_and_short_noise():
    from custom_components.proflame2.protocol.packet import ProflameFrame
    from custom_components.proflame2.rf.waveform import frame_to_air_bytes

    frame = ProflameFrame(serial_id=0x123456, cmd1=1, cmd2=0x61, err1=0x76, err2=0x9F)
    bits = "".join(f"{byte:08b}" for byte in frame_to_air_bytes(frame))[:182]
    corrupted = bits[:10] + ("1" if bits[10] == "0" else "0") + bits[11:]
    edges = tuple((1 if bit == "1" else -1) * sum(1 for _ in runs) * 417 for bit, runs in groupby(corrupted))
    assert structural_frames(edges) == []
    assert structural_frames((417, -417) * 20) == []


@pytest.mark.parametrize("event_count", [1, 2])
def test_pretrigger_review_keeps_repeated_event_ambiguity_and_uncalibrated_status(tmp_path, monkeypatch, event_count):
    window, event = pair()
    window.metadata["arm_us"] = 1
    window.pretrigger, window.pretrigger_info = (), {}
    event["capacity_reached"] = False
    session, analysis = tmp_path / "session", tmp_path / "analysis"
    (session / "trial-001").mkdir(parents=True)
    (analysis / "trial-001").mkdir(parents=True)
    (analysis / "trial-001.log").write_text("Not a trial directory")
    (session / "trial-001/receiver.stdout").write_text("mock")
    (analysis / "trial-001/matches.json").write_text(json.dumps({"provenance": {"sample_rate_hz": 1000000}}))
    (analysis / "trial-001/events.json").write_text(
        json.dumps([{**event, "event_id": i + 1} for i in range(event_count)])
    )
    monkeypatch.setattr(pretrigger, "read_windows", lambda *args, **kwargs: [window])
    monkeypatch.setattr(pretrigger, "structural_frames", lambda edges: [{"wire_words": [1, 2, 3, 4, 5, 6, 7]}])
    monkeypatch.setattr(pretrigger, "review_pair", lambda *args, **kwargs: None)
    report = pretrigger.review(session, analysis)
    row = report["trials"][0]["windows"][0]
    assert report["precision_calibrated"] is False
    assert report["history_is_continuous"] is False
    assert row["status"] == ("ambiguous" if event_count == 2 else "unique_structural_event")
    assert len(row["candidates"]) == event_count
