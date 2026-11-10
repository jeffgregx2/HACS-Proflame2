"""Generate a reviewable private pilot configuration; never open hardware."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from tools.waveform.environment import load_environment
from tools.waveform.waveform_gain import manual_gain
from tools.waveform.waveform_manifest import validate_manifest


def make_config(
    *,
    container: str,
    configuration: str,
    band: str,
    gain: float,
    tool_version: str,
    source: str,
    source_sha: str,
    verified: bool,
) -> dict:
    if band not in {"315", "433"} or source not in {"native", "lilygo"}:
        raise ValueError("unsupported band or source")
    gain = manual_gain(gain)
    devices = []
    for alias, role in (
        (source, "native" if source == "native" else "transmitter"),
        ("receiver", "receiver"),
        ("sdr", "sdr"),
    ):
        devices.append(
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
                        "tx_frequency_offset_hz",
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
    session = {
        "schema_version": 1,
        "session_id": source + "-pilot-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "inventory_sha256": "0" * 64,
        "tool_source_sha": source_sha,
        "source_alias": source,
        "receiver_alias": "receiver",
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "status": "planned",
        "sdr": {
            "sample_rate_hz": 1000000,
            "center_frequency_hz": 315000000 if band == "315" else 433920000,
            "gain_db": gain,
            "sample_format": "cu8",
            "tool_version": tool_version,
            "dropped_samples": None,
        },
        "trials": [],
        "artifacts": [],
        "deliberate_changes": [
            "Pilot template: device firmware/build/profile values remain unknown until verified.",
            "Tool source SHA is the checkout baseline; collector source hash identifies local edits.",
        ],
    }
    validate_manifest("inventory", {"schema_version": 1, "devices": devices})
    validate_manifest("session", session)
    return {
        "inventory": {"schema_version": 1, "devices": devices},
        "session": session,
        "receiver_command": [
            sys.executable,
            "-B",
            "-u",
            "-m",
            "tools.waveform.waveform_docker_logs",
            container,
            configuration,
            "--device",
            os.environ.get("PROFLAME2_RECEIVER_ADDRESS", "OTA"),
        ],
        "sdr_command": [
            os.environ.get("PROFLAME2_RTL433_BIN", "rtl_433"),
            "-c",
            "/dev/null",
            "-R",
            "0",
            "-F",
            "null",
            "-S",
            "none",
            "-d",
            "0",
            "-f",
            str(session["sdr"]["center_frequency_hz"]),
            "-s",
            "1000000",
            "-g",
            str(gain),
            "-w",
            "cu8:{iq}",
        ],
        "device_lock": "rtl-sdr-0",
        "recorder_options_verified": verified,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    try:
        load_environment()
    except (OSError, ValueError):
        parser.exit(2, "Private waveform configuration is missing, unsafe, or invalid.\n")
    parser.add_argument("output", type=Path)
    parser.add_argument("--container", default=os.environ.get("PROFLAME2_ESPHOME_CONTAINER"))
    parser.add_argument("--configuration", default=os.environ.get("PROFLAME2_RECEIVER_YAML"))
    parser.add_argument("--band", choices=("315", "433"), default="315")
    parser.add_argument("--gain", type=float, required=True)
    parser.add_argument("--tool-version", required=True)
    parser.add_argument("--source", choices=("native", "lilygo"), default="native")
    parser.add_argument("--recorder-options-verified", action="store_true")
    args = parser.parse_args()
    if not args.container or not args.configuration:
        parser.error("set the receiver container and YAML path in local configuration or command options")
    if args.output.resolve().is_relative_to(Path("/tmp").resolve()):
        parser.error("retained configurations must be written on persistent capture storage, not /tmp")
    source_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[2], text=True
    ).strip()
    config = make_config(
        container=args.container,
        configuration=args.configuration,
        band=args.band,
        gain=args.gain,
        tool_version=args.tool_version,
        source=args.source,
        source_sha=source_sha,
        verified=args.recorder_options_verified,
    )
    # Private recorder arguments and future inventory edits should not be world-readable.
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w") as target:
            json.dump(config, target, indent=2)
            target.write("\n")
    except BaseException:
        args.output.unlink()
        raise
    print("Pilot configuration created; review device inventory and requested SDR settings before acquisition.")


if __name__ == "__main__":
    main()
