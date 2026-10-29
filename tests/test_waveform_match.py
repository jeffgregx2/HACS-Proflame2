"""Offline RF discovery and conservative matching do not alter TX or fake clocks."""

import json
import os
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from waveform_synthetic import power_fixture

from tools.waveform_diagnostics import RawWindow
from tools.waveform_match import (
    RFEvent,
    detect_iq,
    fit_local_alignment,
    main,
    match_events,
    read_events,
    write_comparison,
)

ROOT = Path(__file__).resolve().parents[1]


def native_windows():
    fixture = power_fixture()
    windows = []
    for capture in fixture["captures"]:
        encoded = "".join(capture["chunks"])
        words = tuple(int(encoded[index : index + 8], 16) for index in range(0, len(encoded), 8))
        windows.append(RawWindow({**capture, "session": fixture["session"]["id"]}, words))
    return windows


def event_from_window(window, *, event_id=1, start_sample=10000):
    runs = list(window.edges)
    while runs and runs[0] < 0:
        runs.pop(0)
    while runs and runs[-1] < 0:
        runs.pop()
    return RFEvent(event_id, 1000000, start_sample, start_sample + sum(map(abs, runs)), tuple(runs), 30000, False)


def event_lines():
    return [
        {
            "kind": "engine",
            "schema_version": 1,
            "sample_rate_hz": 1000000,
            "sample_format": "cu8",
            "detector": "rtl_433",
        },
        {
            "kind": "event",
            "id": 1,
            "start_sample": 100,
            "end_sample": 1300,
            "pulse_count": 2,
            "signed_samples": [400, -400, 400],
            "terminal_gap_samples": 30000,
            "capacity_reached": False,
        },
        {"kind": "end", "samples_read": 40000, "event_count": 1},
    ]


def test_exact_integer_sample_events_exclude_detector_terminal_gap():
    (event,) = read_events(map(json.dumps, event_lines()), sample_rate_hz=1000000, total_samples=40000)
    assert event.start_sample == 100
    assert event.end_sample == 1300
    assert event.signed_samples == (400, -400, 400)
    assert event.terminal_gap_samples == 30000


@pytest.mark.parametrize(
    "mutation",
    ["header", "footer", "whole_event", "bounds", "sign", "duration", "order", "rate", "duplicate", "after_end"],
)
def test_detector_rejects_missing_and_malformed_output(mutation):
    records = event_lines()
    if mutation == "header":
        records.pop(0)
    elif mutation == "footer":
        records.pop()
    elif mutation == "whole_event":
        records.pop(1)
    elif mutation == "bounds":
        records[1]["end_sample"] = 50000
    elif mutation == "sign":
        records[1]["signed_samples"] = [400, 400, 400]
    elif mutation == "duration":
        records[1]["signed_samples"][0] = 401
    elif mutation == "order":
        records[1]["id"] = 2
    elif mutation == "rate":
        records[0]["sample_rate_hz"] = 250000
    elif mutation == "duplicate":
        records.insert(1, records[0])
    else:
        records.append(records[-1])
    with pytest.raises(ValueError):
        read_events(map(json.dumps, records), sample_rate_hz=1000000, total_samples=40000)


def test_empty_detector_stream_is_not_a_complete_silence_capture():
    with pytest.raises(ValueError, match="header or footer"):
        read_events([], sample_rate_hz=1000000, total_samples=40000)


def test_matching_native_commands_is_not_based_on_prompt_order():
    on, off = native_windows()
    events = [event_from_window(off, event_id=1), event_from_window(on, event_id=2, start_sample=1000000)]
    report = match_events([on, off], events)
    assert [item["status"] for item in report["matches"]] == ["matched", "matched"]
    assert [item["candidates"][0]["event_id"] for item in report["matches"]] == [2, 1]
    assert report["ready_for_precision_comparison"] is False
    assert report["global_clock_alignment"]["status"] == "unsupported"


def test_repeated_payload_bursts_and_competing_claims_are_ambiguous():
    on = native_windows()[0]
    events = [event_from_window(on), event_from_window(on, event_id=2, start_sample=1000000)]
    assert match_events([on], events)["matches"][0]["status"] == "ambiguous"
    assert all(item["status"] == "ambiguous" for item in match_events([on, on], events[:1])["matches"])


def test_unhealthy_and_unknown_windows_do_not_get_fabricated_matches():
    window = native_windows()[0]
    bad = RawWindow({**window.metadata, "saturated": 1}, window.words)
    assert match_events([bad], [event_from_window(window)])["matches"][0]["status"] == "capture_unhealthy"
    assert match_events([window], [])["matches"][0]["status"] == "unmatched"
    event = event_from_window(window)
    assert match_events([window], [replace(event, capacity_reached=True)])["matches"][0]["status"] == "unmatched"
    with pytest.raises(ValueError, match="duplicate"):
        match_events([window], [event, event])


def test_long_repetitive_noise_does_not_match_without_payload_evidence():
    runs = (417, -417) * 100
    words = tuple(417 | (1 << 15) | (417 << 16) for _ in range(100))
    window = RawWindow({"id": 1}, words)
    event = RFEvent(1, 1000000, 0, sum(map(abs, runs)), runs, 30000, False)
    assert match_events([window], [event])["matches"][0]["status"] == "unmatched"


def test_edge_only_match_keeps_unknown_protocol_label_and_relative_scope():
    # Deterministic nonperiodic widths, not a synthetic Proflame frame.
    runs = tuple((1 if i % 2 == 0 else -1) * (417 if ((i * i + i // 7) % 7) < 4 else 834) for i in range(101))
    words = []
    for index in range(0, len(runs), 2):
        first = runs[index]
        second = runs[index + 1] if index + 1 < len(runs) else 0
        words.append(abs(first) | ((first > 0) << 15) | (abs(second) << 16) | ((second > 0) << 31))
    window = RawWindow({"id": 1}, tuple(words))
    event = RFEvent(1, 1000000, 1000, 1000 + sum(map(abs, runs)), runs, 30000, False)
    result = match_events([window], [event])["matches"][0]
    assert result["status"] == "matched"
    assert result["candidates"][0]["evidence"] == "edge_signature_only"
    assert result["candidates"][0]["local_alignment"]["coordinate"] == "receiver_window_relative_us"


def test_local_fit_retains_outliers_and_reports_clock_scope():
    anchors = [(float(i * 10000), 700 + i * 10000 * 0.250025) for i in range(20)]
    # Preserve monotonicity while making two points inconsistent with the consensus.
    anchors[5] = (anchors[5][0], anchors[5][1] + 300)
    anchors[12] = (anchors[12][0], anchors[12][1] - 400)
    result = fit_local_alignment(anchors, sample_rate_hz=250000)
    assert result["status"] == "fitted"
    assert result["samples_per_us"] == pytest.approx(0.250025)
    assert result["drift_ppm"] == pytest.approx(100)
    assert result["outlier_indices"] == [5, 12]
    assert result["coordinate"] == "receiver_window_relative_us"
    assert len(result["residual_samples"]) == 20


def test_local_fit_rejects_excessive_drift_and_missing_evidence():
    result = fit_local_alignment([(i * 10000, i * 5000) for i in range(10)], sample_rate_hz=1000000)
    assert result["status"] == "unsupported"
    assert result["reason"] == "drift_outside_limit"
    short = fit_local_alignment([(i * 100, i * 100) for i in range(10)], sample_rate_hz=1000000)
    assert short["reason"] == "insufficient_edge_span"
    with pytest.raises(ValueError):
        fit_local_alignment([(0, 0)] * 6, sample_rate_hz=1000000)


def test_iq_windows_use_rf_event_samples_not_callback_timestamps(tmp_path):
    window = native_windows()[0]
    event = event_from_window(window)
    source = tmp_path / "source.cu8"
    source.write_bytes(bytes(range(256)) * 5000)
    output = tmp_path / "comparison"
    result = write_comparison(source, output, [window], [event], margin_ms=1)
    artifact = result["iq_windows"][0]
    assert artifact["start_sample"] == event.start_sample - 1000
    assert artifact["end_sample"] == event.end_sample + 1000
    expected = source.read_bytes()[artifact["start_sample"] * 2 : artifact["end_sample"] * 2]
    assert (output / artifact["filename"]).read_bytes() == expected
    with pytest.raises(FileExistsError):
        write_comparison(source, output, [window], [event])


def test_partial_comparison_cleanup_preserves_existing_paths(tmp_path, monkeypatch):
    window = native_windows()[0]
    source = tmp_path / "source.cu8"
    source.write_bytes(bytes(1000000))
    output = tmp_path / "comparison"

    def fail(*args, **kwargs):
        raise OSError("test disk failure")

    monkeypatch.setattr("tools.waveform_match.extract_iq", fail)
    with pytest.raises(OSError):
        write_comparison(source, output, [window], [event_from_window(window)])
    assert not output.exists()
    assert source.is_file()


@pytest.mark.parametrize("rate,level", [(999, None), (1000000, float("nan")), (1000000, 1)])
def test_invalid_detector_configuration_is_rejected_before_execution(tmp_path, rate, level):
    with pytest.raises(ValueError):
        detect_iq(tmp_path / "absent.cu8", tmp_path / "absent-detector", sample_rate_hz=rate, fixed_level_db=level)


def test_detector_process_failure_and_timeout_clean_owned_scratch(tmp_path, monkeypatch):
    source = tmp_path / "source.cu8"
    source.write_bytes(bytes(32))
    owned = []

    def failed_process(*args, **kwargs):
        owned.append(Path(kwargs["stdout"].name).parent)
        return subprocess.CompletedProcess(args, 2)

    monkeypatch.setattr("tools.waveform_match.subprocess.run", failed_process)
    with pytest.raises(ValueError, match="failed"):
        detect_iq(source, tmp_path / "unused", sample_rate_hz=1000000)
    assert all(not directory.exists() for directory in owned)

    def timed_out(*args, **kwargs):
        owned.append(Path(kwargs["stdout"].name).parent)
        raise subprocess.TimeoutExpired(args, 300)

    monkeypatch.setattr("tools.waveform_match.subprocess.run", timed_out)
    with pytest.raises(subprocess.TimeoutExpired):
        detect_iq(source, tmp_path / "unused", sample_rate_hz=1000000)
    assert all(not directory.exists() for directory in owned)


def test_cli_records_provenance_and_refuses_existing_output(tmp_path, monkeypatch, capsys):
    window = native_windows()[0]
    source = tmp_path / "pilot.cu8"
    source.write_bytes(bytes(1000000))
    receiver = tmp_path / "receiver.log"
    receiver.write_text("strict receiver log validated through injected parser")
    detector = tmp_path / "detector"
    detector.write_bytes(b"test detector")
    output = tmp_path / "comparison"
    monkeypatch.setattr("tools.waveform_match.read_windows", lambda *args, **kwargs: [window])
    monkeypatch.setattr("tools.waveform_match.detect_iq", lambda *args, **kwargs: [event_from_window(window)])
    monkeypatch.setattr(
        "sys.argv",
        [
            "match",
            str(source),
            str(receiver),
            "--detector",
            str(detector),
            "--sample-rate",
            "1000000",
            "--output",
            str(output),
        ],
    )
    main()
    assert json.loads(capsys.readouterr().out)["precision_ready"] is False
    report = json.loads((output / "matches.json").read_text())
    assert report["provenance"]["detector"]["bytes"] == 13
    assert len(report["provenance"]["iq"]["sha256"]) == 64
    with pytest.raises(SystemExit):
        main()


@pytest.fixture(scope="module")
def detector(tmp_path_factory):
    source = os.environ.get("PROFLAME2_RTL433_SOURCE")
    if not source:
        pytest.skip("Set PROFLAME2_RTL433_SOURCE for the real upstream detector tests")
    build = tmp_path_factory.mktemp("rtl433-detector")
    cmake = os.environ.get("CMAKE") or shutil.which("cmake")
    assert cmake, "Install CMake or set CMAKE to its executable"
    subprocess.run(
        [cmake, "-S", str(ROOT / "tools/waveform_rtl433"), "-B", str(build), f"-DRTL433_SOURCE={source}"],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    subprocess.run(
        [cmake, "--build", str(build), "--target", "waveform_rtl433", "-j", "4"],
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    )
    return build / "waveform_rtl433"


def test_real_upstream_detector_processes_native_derived_iq(detector, tmp_path):
    windows = native_windows()
    source = tmp_path / "native-derived.cu8"
    with source.open("wb") as recording:
        recording.write(bytes([128, 128]) * 30000)
        for window in windows:
            for duration in window.edges:
                recording.write(bytes([210 if duration > 0 else 128, 128]) * abs(duration))
            recording.write(bytes([128, 128]) * 120000)
    events = detect_iq(source, detector, sample_rate_hz=1000000)
    decoded = [frame["wire_words"] for event in events for frame in event.frames()]
    assert [59, 63, 2, 1, 6, 118, 222] in decoded
    assert [59, 63, 2, 0, 6, 87, 222] in decoded
    report = match_events(windows, events)
    assert all(item["status"] == "matched" for item in report["matches"])
    # Synthetic IQ validates plumbing, not calibration against real RF equipment.
    assert report["ready_for_precision_comparison"] is False


def test_real_detector_silence_and_truncated_input(detector, tmp_path):
    quiet = tmp_path / "quiet.cu8"
    quiet.write_bytes(bytes([128, 128]) * 100000)
    assert detect_iq(quiet, detector, sample_rate_hz=1000000) == []
    quiet.write_bytes(bytes(3))
    with pytest.raises(ValueError, match="truncated"):
        detect_iq(quiet, detector, sample_rate_hz=1000000)


def test_real_detector_extended_frame_and_threshold_replay(detector, tmp_path):
    fixture = json.loads((ROOT / "tests/fixtures/issue_26_mendota_extended_capture.json").read_text())
    capture = fixture["captures"][0]
    bits = "".join(f"{byte:08b}" for byte in bytes.fromhex(capture["pcm_hex"]))[: capture["pcm_bit_length"]]
    source = tmp_path / "extended-derived.cu8"
    with source.open("wb") as recording:
        recording.write(bytes([128, 128]) * 30000)
        for bit in bits:
            recording.write(bytes([210 if bit == "1" else 128, 128]) * 417)
        recording.write(bytes([128, 128]) * 120000)
    before = source.read_bytes()
    for level in (None, -12, -6):
        events = detect_iq(source, detector, sample_rate_hz=1000000, fixed_level_db=level)
        frames = [frame for event in events for frame in event.frames()]
        assert any(frame["wire_words"] == capture["words"] for frame in frames)
    assert source.read_bytes() == before


def test_analysis_runner_cleans_build_directory_on_failure(tmp_path):
    fake = tmp_path / "cmake"
    arguments = tmp_path / "arguments.txt"
    fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$TEST_ARGUMENTS"\nexit 7\n')
    fake.chmod(0o700)
    result = subprocess.run(
        [str(ROOT / "tools/run_waveform_analysis.sh"), "unused.cu8", "unused.log", "1000000", str(tmp_path / "output")],
        env={
            **os.environ,
            "CMAKE": str(fake),
            "TEST_ARGUMENTS": str(arguments),
            "PROFLAME2_RTL433_SOURCE": str(tmp_path),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 7
    args = arguments.read_text().splitlines()
    build = Path(args[args.index("-B") + 1])
    assert not build.parent.exists()
    assert not (tmp_path / "output").exists()
