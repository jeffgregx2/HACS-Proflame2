from __future__ import annotations

import json
from pathlib import Path

from custom_components.proflame2.protocol.packet import ProflameFrame
from custom_components.proflame2.rf.capture import frame_to_air_bytes
from tools.proflame_capture_analysis.scan_lilygo_fifo_probe import (
    scan_fifo_payload_hex,
    scan_fifo_probe_artifact,
)


def _known_good_payload_hex() -> str:
    frame = ProflameFrame(serial_id=0x3B3F02, cmd1=0x01, err1=0x76, cmd2=0x31, err2=0x6A)
    return (b"\x00\xff" + frame_to_air_bytes(frame) + b"\x55").hex()


def test_fifo_probe_scanner_finds_embedded_proflame_candidate() -> None:
    report = scan_fifo_payload_hex(_known_good_payload_hex())

    assert report["candidate_count"] == 1
    candidate = report["candidates"][0]
    assert candidate["remote_id"] == "3b3f02"
    assert candidate["cmd1"] == "01"
    assert candidate["cmd2"] == "31"
    assert candidate["err1"] == "76"
    assert candidate["err2"] == "6a"


def test_fifo_probe_scanner_reads_collector_artifact(tmp_path: Path) -> None:
    sample_dir = tmp_path / "sample"
    lilygo_dir = sample_dir / "lilygo"
    lilygo_dir.mkdir(parents=True)
    (lilygo_dir / "fifo_probe.json").write_text(
        json.dumps(
            {
                "artifact_class": "experimental_fifo_probe",
                "probes": [
                    {
                        "metadata": {"probe_id": 1},
                        "payload_hex": _known_good_payload_hex(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = scan_fifo_probe_artifact(sample_dir)

    assert report["artifact_class"] == "experimental_fifo_probe_scan"
    assert report["probe_count"] == 1
    assert report["total_candidate_count"] == 1
    assert report["scans"][0]["scan"]["candidates"][0]["remote_id"] == "3b3f02"


def test_fifo_probe_scanner_reads_lilygo_artifact_directory(tmp_path: Path) -> None:
    lilygo_dir = tmp_path / "lilygo"
    lilygo_dir.mkdir()
    (lilygo_dir / "fifo_probe.json").write_text(
        json.dumps(
            {
                "artifact_class": "experimental_fifo_probe",
                "probes": [
                    {
                        "metadata": {"probe_id": 1, "profile": "rfcat_infinite_carrier"},
                        "payload_hex": _known_good_payload_hex(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = scan_fifo_probe_artifact(lilygo_dir)

    assert report["probe_count"] == 1
    assert report["total_candidate_count"] == 1
    assert report["scans"][0]["metadata"]["profile"] == "rfcat_infinite_carrier"
