#!/usr/bin/env python3
"""Scan experimental LilyGO CC1101 RX FIFO probe artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from custom_components.proflame2.rf.capture import find_proflame_candidates


def _candidate_to_dict(candidate: Any) -> dict[str, object]:
    sample = candidate.sample
    return {
        "bit_offset": candidate.bit_offset,
        "symbol_offset": candidate.symbol_offset,
        "absolute_bit_offset": candidate.absolute_bit_offset,
        "repeat_count": candidate.repeat_count,
        "confidence": candidate.confidence,
        "remote_id": f"{sample.remote_id:06x}",
        "cmd1": f"{sample.cmd1:02x}",
        "cmd2": f"{sample.cmd2:02x}",
        "err1": f"{sample.err1:02x}",
        "err2": f"{sample.err2:02x}",
        "raw_slice_hex": candidate.raw_slice.hex(),
        "symbols": sample.symbols,
        "validation_notes": list(candidate.validation_notes),
    }


def scan_fifo_payload_hex(payload_hex: str) -> dict[str, object]:
    """Run the YardStick learning-path candidate scanner over FIFO bytes."""

    normalized = "".join(payload_hex.split())
    raw_payload = bytes.fromhex(normalized)
    candidates = find_proflame_candidates(raw_payload)
    return {
        "payload_byte_count": len(raw_payload),
        "candidate_count": len(candidates),
        "candidates": [_candidate_to_dict(candidate) for candidate in candidates],
    }


def _load_probe_payloads(path: Path) -> list[dict[str, object]]:
    if path.is_dir():
        probe_json = path / "fifo_probe.json"
        if probe_json.exists():
            return _load_probe_payloads(probe_json)
        payload_hex = path / "fifo_probe_payload.hex"
        if payload_hex.exists():
            return [{"source": str(payload_hex), "payload_hex": payload_hex.read_text(encoding="utf-8")}]
        probe_json = path / "lilygo" / "fifo_probe.json"
        if probe_json.exists():
            return _load_probe_payloads(probe_json)
        payload_hex = path / "lilygo" / "fifo_probe_payload.hex"
        if payload_hex.exists():
            return [{"source": str(payload_hex), "payload_hex": payload_hex.read_text(encoding="utf-8")}]
        raise FileNotFoundError(f"No LilyGO FIFO probe artifact found under {path}")
    if path.suffix.lower() == ".hex":
        return [{"source": str(path), "payload_hex": path.read_text(encoding="utf-8")}]
    payload = json.loads(path.read_text(encoding="utf-8"))
    probes = payload.get("probes") or []
    rows: list[dict[str, object]] = []
    for index, probe in enumerate(probes):
        if not isinstance(probe, dict):
            continue
        rows.append(
            {
                "source": str(path),
                "probe_index": index,
                "metadata": probe.get("metadata") or {},
                "payload_hex": probe.get("payload_hex") or "",
            }
        )
    return rows


def scan_fifo_probe_artifact(path: Path) -> dict[str, object]:
    rows = _load_probe_payloads(path)
    scans: list[dict[str, object]] = []
    for row in rows:
        payload_hex = str(row.get("payload_hex") or "")
        scan = (
            scan_fifo_payload_hex(payload_hex)
            if payload_hex.strip()
            else {
                "payload_byte_count": 0,
                "candidate_count": 0,
                "candidates": [],
            }
        )
        scans.append({**row, "scan": scan})
    return {
        "artifact_class": "experimental_fifo_probe_scan",
        "input": str(path),
        "probe_count": len(scans),
        "scans": scans,
        "total_candidate_count": sum(int(row["scan"]["candidate_count"]) for row in scans),
    }


def _default_output_path(path: Path) -> Path:
    if path.is_dir():
        if (path / "fifo_probe.json").exists() or (path / "fifo_probe_payload.hex").exists():
            return path / "fifo_probe_scan_report.json"
        return path / "lilygo" / "fifo_probe_scan_report.json"
    return path.with_name(f"{path.stem}_scan_report.json")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="Sample directory, fifo_probe.json, or fifo_probe_payload.hex")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    report = scan_fifo_probe_artifact(args.path)
    output = args.output or _default_output_path(args.path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
