"""Launch the fixed pilot from any directory; preserve and package each attempt."""

from __future__ import annotations

import argparse
import copy
import errno
import json
import os
import re
import signal
import sys
import tempfile
from pathlib import Path

from tools.waveform.environment import capture_root, config_directory, load_environment
from tools.waveform.waveform_archive import pack_session
from tools.waveform.waveform_collect import (
    METADATA_RESERVE_BYTES,
    check_storage,
    configured_protocol,
    run,
    validate_config,
)
from tools.waveform.waveform_gain import manual_gain


def capture_overrides(
    config: dict, *, gain: float | None, source: str | None, gain_check: bool, source_tx_offset_hz: int | None = None
) -> dict:
    """Change this attempt only; retain the reviewed baseline configuration."""
    result = copy.deepcopy(config)
    notes = result["session"]["deliberate_changes"]
    if gain is not None:
        gain = manual_gain(gain)
        args = result["sdr_command"]
        if Path(args[0]).name != "rtl_433" or args.count("-g") != 1:
            raise ValueError("gain override requires an explicit rtl_433 command with exactly one -g option")
        index = args.index("-g") + 1
        if index >= len(args):
            raise ValueError("rtl_433 -g is missing its value")
        # Refuse mixed option spellings rather than allowing an override to lose.
        if any(arg.startswith("-g") and arg != "-g" for arg in args[1:]):
            raise ValueError("ambiguous rtl_433 gain options")
        args[index] = str(gain)
        # -F null suppresses structured notices even with -v. Keep packet output
        # disabled, but explicitly route tuner confirmation to the recorder log.
        args.extend(["-v", "-F", "log:/dev/stderr"])
        result["session"]["sdr"]["gain_db"] = gain
        notes.append(f"SDR fixed manual gain requested: {gain:g} dB; actual tuner gain recorded separately.")
    if source is not None:
        if source not in {"native", "lilygo"}:
            raise ValueError("unsupported source")
        original = result["session"]["source_alias"]
        devices = result["inventory"]["devices"]
        if source != original and any(device["alias"] == source for device in devices):
            raise ValueError("source alias conflicts with another inventory device")
        device = next(device for device in devices if device["alias"] == original)
        if source != original:
            # Do not attribute the original device's build/profile to a different source.
            for key in device:
                if key not in {"alias", "role"}:
                    device[key] = dict.fromkeys(device[key]) if isinstance(device[key], dict) else None
        device.update(alias=source, role="native" if source == "native" else "transmitter")
        result["session"]["source_alias"] = source
        notes.append(f"Operator selected source: {source}; verify installed firmware/profile separately.")
    if source_tx_offset_hz is not None:
        if not -100000 <= source_tx_offset_hz <= 100000:
            raise ValueError("source TX offset must be within +/-100000 Hz")
        device = next(d for d in result["inventory"]["devices"] if d["alias"] == result["session"]["source_alias"])
        if device["role"] != "transmitter":
            raise ValueError("source TX offset metadata requires a transmitter source")
        device["configuration"]["tx_frequency_offset_hz"] = source_tx_offset_hz
        notes.append(
            f"Operator-reported TX-only offset: {source_tx_offset_hz} Hz; verify against transmitter INFO log."
        )
    if gain_check:
        if gain is None:
            raise ValueError("gain_check requires --sdr-gain to explicitly configure and verify manual gain")
        manual_gain(result["session"]["sdr"]["gain_db"])
        result["capture_protocol"] = "gain_check"
        notes.append("Short SDR gain check: Light 6 then Light 0; no fan or quiet trial.")
    return result


def archive_status(message: str) -> None:
    print(message, flush=True)


def failure_hint(session: Path) -> str | None:
    """Recognize common startup errors without echoing private log contents."""
    for path in session.glob("*/sdr.stderr"):
        with path.open("rb") as source:
            source.seek(max(0, path.stat().st_size - 65536))
            tail = source.read(65536)
        if b"usb_open error -3" in tail:
            return (
                "SDR USB access denied. Check device group/ACL permissions; reconnect after changing group membership."
            )
        if b"usb_claim_interface error" in tail or b"usb_open error -6" in tail:
            return "SDR interface unavailable. Check for another recorder or a kernel driver using it."
    return None


def launch(
    config: dict, cycles: int, dry_run: bool = False, *, output_root: Path | None = None
) -> tuple[int, Path | None]:
    validate_config(config)
    commands = configured_protocol(config, cycles)
    print(
        f"Capture source: {config['session']['source_alias']}; requested SDR gain: {config['session']['sdr']['gain_db']} dB."
    )
    if dry_run:
        print("Dry run: no recorders or device actions will be started.")
        for index, command in enumerate(commands, 1):
            timing = "60s" if command == "quiet" else "up to 60s to reach target, then 15s quiet recording"
            print(f"{index:03d} {command} ({timing})")
        return 0, None
    # Retained evidence belongs on persistent storage, including failed attempts.
    output_root = output_root or capture_root() / "runs"
    output_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    workspace = Path(tempfile.mkdtemp(prefix="proflame2-pilot-", dir=output_root))
    session = workspace / "session"
    print(f"Results for this attempt: {workspace}", flush=True)
    print("Use the native remote for a native-source session. Follow the fixed prompts; q/Enter or Ctrl-C aborts.")
    code = 0
    try:
        run(config, session, cycles)
    except KeyboardInterrupt:
        print("Pilot aborted; recorded evidence retained.", file=sys.stderr)
        code = 130
    except Exception as error:
        # Do not dump configuration/log values or an unnecessary traceback.
        print(
            f"Pilot failed ({type(error).__name__}). Check the recorder logs in the results directory.", file=sys.stderr
        )
        if str(error) == "SDR manual gain was not confirmed by recorder diagnostics":
            print(
                "SDR manual gain was not confirmed by recorder diagnostics. "
                "Recordings are retained; remaining trials were not run.",
                file=sys.stderr,
            )
        if isinstance(error, OSError):
            if error.errno == errno.EDQUOT:
                print(
                    "Disk quota exceeded (EDQUOT): this user's allowance is exhausted even if df shows free space. "
                    "Preserve/move prior captures to persistent capture storage before retrying.",
                    file=sys.stderr,
                )
            elif error.errno == errno.ENOSPC:
                print(
                    "Capture storage ran out of space or has insufficient recording headroom. "
                    "Preserve/move previous captures or increase capture-storage capacity before retrying.",
                    file=sys.stderr,
                )
            else:
                print(f"Operating-system error: {errno.errorcode.get(error.errno, 'UNKNOWN')}.", file=sys.stderr)
        if isinstance(error, TimeoutError) and str(error) in {
            "No new receiver Start confirmation was received for this trial",
            "Target was not confirmed with Return within 60 seconds",
            "No receiver Stop confirmation was received for this trial",
        }:
            print(str(error), file=sys.stderr)
        recorder_failure = re.fullmatch(
            r"(sdr|receiver) recorder exited before capture completed \(exit (-?\d+)\)", str(error)
        )
        if recorder_failure:
            print(f"{recorder_failure[1]} recorder exited with code {recorder_failure[2]}.", file=sys.stderr)
        hint = failure_hint(session)
        if hint:
            print(hint, file=sys.stderr)
        code = 1
    finally:
        if (session / "session.json").is_file():
            try:
                print(
                    "CAPTURE FINISHED. Creating and verifying the private archive.\n"
                    "No more buttons are needed. Large SDR recordings can take several minutes; please wait...",
                    flush=True,
                )
                # Gzip can expand incompressible IQ slightly. Keep originals and
                # enough room for metadata instead of filling the filesystem.
                source_bytes = sum(path.stat().st_size for path in session.rglob("*") if path.is_file())
                check_storage(workspace, source_bytes + source_bytes // 100 + METADATA_RESERVE_BYTES)
                pack_session(session, workspace / "session.tar.gz", status=archive_status)
                print(f"Private archive: {workspace / 'session.tar.gz'}", flush=True)
                print("ALL DONE. The script is exiting; keep the archive and original session files.", flush=True)
            except Exception:
                print(
                    "Archive verification failed; original files retained. Do not treat this attempt as complete.",
                    file=sys.stderr,
                )
                code = code or 1
    return code, workspace


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    try:
        load_environment()
    except (OSError, ValueError):
        parser.exit(2, "Private waveform configuration is missing, unsafe, or invalid.\n")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(os.environ.get("PROFLAME2_PILOT_CONFIG", config_directory() / "native-pilot.json")),
    )
    parser.add_argument("--output-root", type=Path, default=capture_root() / "runs")
    parser.add_argument("--cycles", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--sdr-gain", type=float, help="override fixed rtl_433 gain for this attempt, e.g. 0.9 dB; 0 is AGC"
    )
    parser.add_argument("--source", choices=("native", "lilygo"), help="identify the actual transmitting source")
    parser.add_argument(
        "--source-tx-offset-hz",
        type=int,
        help="record the transmitter's installed offset in Hz; metadata only, does not configure hardware",
    )
    parser.add_argument(
        "--gain-check", action="store_true", help="short Light 6/0 run with automatic clipping screening"
    )
    args = parser.parse_args()
    if args.output_root.resolve().is_relative_to(Path("/tmp").resolve()):
        parser.error("retained captures must not be stored under /tmp; choose persistent storage")

    def interrupted(_signum, _frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        config = json.loads(args.config.read_text())
        config = capture_overrides(
            config,
            gain=args.sdr_gain,
            source=args.source,
            gain_check=args.gain_check,
            source_tx_offset_hz=args.source_tx_offset_hz,
        )
        code, _ = launch(config, args.cycles, args.dry_run, output_root=args.output_root)
    except (OSError, ValueError, KeyError):
        print("Pilot configuration is missing or invalid. Prepare/review it before running the test.", file=sys.stderr)
        code = 2
    finally:
        signal.signal(signal.SIGTERM, previous)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
