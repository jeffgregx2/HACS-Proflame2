"""Fixed, receive-only waveform collection with explicit operator markers.

Recorder commands are trusted local configuration, never shell strings. This tool
does not send device actions: the operator arms/stops the passive receiver.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import re
import select
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

from tools.waveform_diagnostics import read_windows
from tools.waveform_gain import inspect_capture, manual_gain
from tools.waveform_manifest import validate_manifest, validate_session_files

SEQUENCE = ("light_6", "light_0", "fan_6", "fan_0")
ARMED = re.compile(r"PFSESSION session=(\d+) state=armed duration_ms=(\d+)")
CLOSED = re.compile(r"PFSESSION session=(\d+) state=(?:paused|aborted)\b")
MAX_LOG_BYTES = 32 * 1024 * 1024
METADATA_RESERVE_BYTES = 64 * 1024 * 1024


def check_storage(path: Path, required: int = METADATA_RESERVE_BYTES) -> None:
    """Keep metadata headroom; never delete another attempt to make space."""
    free = shutil.disk_usage(path).free
    if free < required:
        raise OSError(
            errno.ENOSPC,
            f"Capture storage is low: {free // (1024 * 1024)} MiB free; "
            f"at least {required // (1024 * 1024)} MiB required. "
            "Preserve/move earlier captures or increase capture-storage capacity before retrying.",
        )


def protocol(cycles: int = 5) -> list[str]:
    if not 1 <= cycles <= 20:
        raise ValueError("cycles must be between 1 and 20")
    return [command for _ in range(cycles) for command in SEQUENCE] + ["quiet"]


def configured_protocol(config: dict, cycles: int) -> list[str]:
    mode = config.get("capture_protocol", "standard")
    if mode == "standard":
        return protocol(cycles)
    if mode == "gain_check" and cycles == 1:
        return ["light_6", "light_0"]
    raise ValueError("gain_check requires one cycle; unknown capture protocols are not supported")


def write_json(path: Path, value: object) -> None:
    # Publish only complete metadata. An out-of-space failure must not leave an
    # empty final file that looks like a saved trial/session.
    descriptor, temporary = tempfile.mkstemp(prefix=".metadata-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as target:
            json.dump(value, target, indent=2)
            target.write("\n")
        os.link(temporary, path)
    finally:
        os.unlink(temporary)


def artifact(path: Path, root: Path) -> dict:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return {"filename": str(path.relative_to(root)), "sha256": digest.hexdigest(), "bytes": path.stat().st_size}


class Recorder:
    """Own one process group and regular-file streams; never pipe-block a recorder."""

    def __init__(self, command: list[str], prefix: Path):
        if not command or any(not isinstance(arg, str) or not arg for arg in command):
            raise ValueError("recorder requires a nonempty argument array")
        self.stack = ExitStack()
        self.label = prefix.name
        self.stopped = False
        self.process = None
        try:
            stdout = self.stack.enter_context(prefix.with_suffix(".stdout").open("xb"))
            stderr = self.stack.enter_context(prefix.with_suffix(".stderr").open("xb"))
            self.started_ns = time.monotonic_ns()
            self.process = subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, start_new_session=True
            )
        except BaseException:
            self.stack.close()
            raise

    def check(self) -> None:
        if self.stopped:
            return
        code = self.process.poll()
        if code is not None:
            raise RuntimeError(f"{self.label} recorder exited before capture completed (exit {code})")

    def stop(self) -> None:
        if self.stopped:
            return
        self.stopped = True
        # Include descendants even when a launcher has already exited.
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait(timeout=3)
        finally:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.stack.close()


class Capture:
    def __init__(self, recorders: list[Recorder], log: Path, iq: Path, sample_rate: int):
        self.recorders = recorders
        self.log = log
        self.iq = iq
        self.max_iq_bytes = sample_rate * 2 * 240
        self.input_buffer = ""

    def discard_queued_input(self) -> None:
        """A Return typed for an earlier step must not confirm a new target."""
        discarded = self.input_buffer
        self.input_buffer = ""
        while select.select([sys.stdin], [], [], 0)[0]:
            block = os.read(sys.stdin.fileno(), 4096)
            if not block:
                raise KeyboardInterrupt
            discarded += block.decode(errors="replace")
            if len(discarded) > 4096:
                raise RuntimeError("queued operator input exceeded capture limit")
        if any(line.strip() == "q" for line in discarded.splitlines()):
            raise KeyboardInterrupt
        if discarded:
            print("Cleared queued keyboard input from the previous step. Wait for the new TARGET prompt.", flush=True)

    def text(self) -> str:
        check_storage(self.log.parent)
        if self.iq.exists() and self.iq.stat().st_size > self.max_iq_bytes:
            raise RuntimeError("IQ exceeded bounded recording budget")
        for path in self.log.parent.iterdir():
            if path.suffix in {".stdout", ".stderr"} and path.stat().st_size > MAX_LOG_BYTES:
                raise RuntimeError("recorder log exceeded capture limit")
        return self.log.read_text(errors="replace")

    def wait(self, seconds: float, predicate=lambda _: True, marker=None) -> str:
        deadline = time.monotonic() + seconds
        while True:
            for recorder in self.recorders:
                recorder.check()
            text = self.text()
            if predicate(text):
                return text
            if time.monotonic() >= deadline:
                raise TimeoutError("capture readiness/closure deadline expired")
            if marker is not None and select.select([sys.stdin], [], [], 0)[0]:
                block = os.read(sys.stdin.fileno(), 4096)
                if not block:
                    raise KeyboardInterrupt
                self.input_buffer += block.decode(errors="replace")
                if len(self.input_buffer) > 4096:
                    raise RuntimeError("operator input exceeded bounded marker buffer")
                while "\n" in self.input_buffer:
                    line, self.input_buffer = self.input_buffer.split("\n", 1)
                    if line.strip() == "q":
                        raise KeyboardInterrupt
                    marker(line.strip(), time.monotonic_ns())
            time.sleep(0.05)


def collect_trial(root: Path, config: dict, index: int, command: str) -> dict:
    # One recorder's maximum IQ budget plus bounded logs and metadata reserve.
    # This is a per-trial guard, not a promise that arbitrary retries fit storage.
    check_storage(
        root, config["session"]["sdr"]["sample_rate_hz"] * 2 * 240 + 2 * MAX_LOG_BYTES + METADATA_RESERVE_BYTES
    )
    trial_id = f"trial-{index:03d}-{command}"
    folder = root / trial_id
    folder.mkdir(mode=0o700)
    duration = 60 if command == "quiet" else 15
    action_timeout = 0 if command == "quiet" else 60
    required_budget = duration + action_timeout
    markers = []
    recorders = []
    metadata = {
        "command": command,
        "window_seconds": duration,
        "action_timeout_seconds": action_timeout,
        "status": "failed",
    }
    trial = None
    try:
        receiver = Recorder(config["receiver_command"], folder / "receiver")
        recorders.append(receiver)
        iq = folder / "signal.cu8"
        sdr_command = [arg.replace("{iq}", str(iq)) for arg in config["sdr_command"]]
        sdr = Recorder(sdr_command, folder / "sdr")
        recorders.append(sdr)
        capture = Capture(recorders, folder / "receiver.stdout", iq, config["session"]["sdr"]["sample_rate_hz"])
        metadata["recorder_start_monotonic_ns"] = {"receiver": receiver.started_ns, "sdr": sdr.started_ns}
        print("Waiting for SDR data and receiver logging before requesting capture...", flush=True)

        def recorders_ready(_text):
            if not iq.is_file() or iq.stat().st_size == 0:
                return False
            if "tools.waveform_docker_logs" in config["receiver_command"]:
                return "Successful handshake" in (folder / "receiver.stderr").read_text(errors="replace")
            return True

        capture.wait(20, recorders_ready)
        print(
            f"\n{trial_id} - NEW CAPTURE\nClick 'Waveform Capture Start' on the receiver NOW (again for EVERY trial).\n"
            "Do not press the remote yet. Waiting for the receiver to confirm Start...",
            flush=True,
        )
        try:
            text = capture.wait(90, lambda text: ARMED.search(text) is not None)
        except TimeoutError as error:
            raise TimeoutError("No new receiver Start confirmation was received for this trial") from error
        matches = list(ARMED.finditer(text))
        if len(matches) != 1:
            raise RuntimeError("expected one fresh receiver session")
        session_id, budget_ms = matches[0].groups()
        armed_seen = time.monotonic()
        if int(budget_ms) < (required_budget + 5) * 1000:
            raise RuntimeError("receiver session budget too short; configure at least 90 seconds for quiet sample")
        metadata["receiver_session"] = session_id
        print("START CONFIRMED by receiver. Preparing the recording...", flush=True)
        # Positive file growth is readiness evidence, not proof of RF/sample continuity.
        pretrigger_end = time.monotonic() + 2
        capture.wait(3, lambda _: time.monotonic() >= pretrigger_end)
        if CLOSED.search(capture.text()):
            raise RuntimeError("receiver closed before prompt")
        if time.monotonic() - armed_seen + required_budget + 2 >= int(budget_ms) / 1000:
            raise RuntimeError("insufficient receiver budget remaining before prompt")
        capture.discard_queued_input()
        prompt_ns = time.monotonic_ns()
        field, _, level = command.partition("_")
        trial = {
            "schema_version": 1,
            "trial_id": trial_id,
            "source_alias": config["session"]["source_alias"],
            "command": command,
            "desired_state": None if command == "quiet" else {field: int(level)},
            "decoded_state": None,
            "prompt_monotonic_ns": prompt_ns,
            "action_monotonic_ns": None,
            "rf_sample_start": None,
            "receiver_capture_ids": [],
            "retry_of": None,
            "outcome": "unobserved",
            "quality_flags": ["rf_alignment_pending", "sample_continuity_unverified"],
            "notes": "Return confirms target reached, not an RF edge timestamp. See acquisition.json.",
        }
        target = command.replace("_", " ").title()
        if command == "quiet":
            print(
                "QUIET SAMPLE: do not touch the remote or HA controls for 60 seconds. No Return is needed.", flush=True
            )
        else:
            direction = "UP" if level == "6" else "DOWN"
            source_role = next(
                device["role"]
                for device in config["inventory"]["devices"]
                if device["alias"] == config["session"]["source_alias"]
            )
            control_instruction = (
                f"Use the NATIVE REMOTE. Select {field.title()} and press {direction} repeatedly until its DISPLAY shows {level}."
                if source_role == "native"
                else f"Use the selected transmitter's HA control to set {field.title()} to {level}."
            )
            print(
                f"TARGET: {target}. Keep the fireplace ON in manual mode.\n{control_instruction}\n"
                f"ONLY AFTER reaching {target}, press Return ONCE on this keyboard.\n"
                "Do NOT press Return after each remote button press. You have 60 seconds; q then Return aborts.",
                flush=True,
            )

        def marker(note, timestamp):
            if len(markers) >= 1024 or len(note) > 256:
                raise RuntimeError("operator markers exceeded capture limit")
            markers.append({"monotonic_ns": timestamp, "note": note})
            if command == "quiet" and "operator_activity_during_quiet" not in trial["quality_flags"]:
                trial["quality_flags"].append("operator_activity_during_quiet")
            if trial["action_monotonic_ns"] is None:
                trial["action_monotonic_ns"] = timestamp
                if command != "quiet":
                    print(
                        f"TARGET CONFIRMED by operator: {target}. Keep the remote and HA controls untouched.\n"
                        "Recording 15 more seconds for repeats; wait for the Stop instruction...",
                        flush=True,
                    )
            elif command != "quiet":
                print("Return already received. Still recording; no further button presses are needed.", flush=True)

        def ensure_open(text):
            if CLOSED.search(text):
                raise RuntimeError("receiver closed before requested observation window ended")

        if command != "quiet":

            def target_confirmed(text):
                ensure_open(text)
                return trial["action_monotonic_ns"] is not None

            try:
                capture.wait(action_timeout, target_confirmed, marker)
            except TimeoutError as error:
                raise TimeoutError("Target was not confirmed with Return within 60 seconds") from error
            metadata["target_confirmed_monotonic_ns"] = trial["action_monotonic_ns"]

        end = time.monotonic() + duration

        def recording_complete(text):
            ensure_open(text)
            return time.monotonic() >= end

        capture.wait(duration + 1, recording_complete, marker)
        metadata["window_end_monotonic_ns"] = time.monotonic_ns()
        print(
            "RECORDING WINDOW COMPLETE. Click 'Waveform Capture Stop' on the receiver NOW.\n"
            "Do not press Start yet. Waiting for the receiver to confirm Stop...",
            flush=True,
        )
        try:
            draining = False
            last_status = time.monotonic()

            def export_complete(text):
                nonlocal draining, last_status
                if re.search(rf"PFSESSION session={session_id} state=failed\b", text):
                    raise RuntimeError("Receiver reported failed acquisition/export; preserve the recorder logs")
                if not draining and re.search(rf"PFSTOP1 session={session_id} ended_us=\d+", text):
                    draining = True
                    # RF acquisition has ended; do not grow IQ while logs drain.
                    for recorder in recorders:
                        if recorder.label == "sdr":
                            recorder.stop()
                    print("ACQUISITION STOPPED. Saving buffered waveform logs; no more buttons are needed.", flush=True)
                if draining and time.monotonic() - last_status >= 30:
                    print("Still receiving buffered waveform logs; please wait...", flush=True)
                    last_status = time.monotonic()
                return any(m.group(1) == session_id for m in CLOSED.finditer(text))

            text = capture.wait(600, export_complete)
        except TimeoutError as error:
            raise TimeoutError("No receiver Stop confirmation was received for this trial") from error
        windows = read_windows(text.splitlines(), require_complete_sessions=True)
        print("STOP CONFIRMED by receiver. Saving this trial before preparing the next one.", flush=True)
        trial["receiver_capture_ids"] = [f"{session_id}:{w.metadata['id']}" for w in windows]
        if len(list(ARMED.finditer(text))) != 1:
            raise RuntimeError("receiver restarted during trial")
        if not windows:
            trial["quality_flags"].append("no_receiver_exports")
        metadata["status"] = "complete"
    finally:
        for recorder in reversed(recorders):
            recorder.stop()
        metadata["markers"] = markers
        metadata["stopped_monotonic_ns"] = time.monotonic_ns()
        if (folder / "signal.cu8").exists():
            size = (folder / "signal.cu8").stat().st_size
            metadata["iq_samples"] = size // 2
            metadata["truncated_iq"] = bool(size % 2)
            if not size or size % 2:
                metadata["status"] = "failed"
        else:
            metadata["status"] = "failed"
        if trial is not None:
            if metadata["status"] != "complete":
                trial["quality_flags"].append("incomplete_acquisition")
            validate_manifest("trial", trial)
            write_json(folder / "trial.json", trial)
        write_json(folder / "acquisition.json", metadata)
    if not iq.exists() or iq.stat().st_size == 0 or iq.stat().st_size % 2:
        raise RuntimeError("missing, empty or truncated CU8 recording")
    return trial


def validate_config(config: dict) -> None:
    validate_manifest("inventory", config["inventory"])
    validate_manifest("session", config["session"])
    devices = {device["alias"]: device for device in config["inventory"]["devices"]}
    session = config["session"]
    if devices.get(session["receiver_alias"], {}).get("role") != "receiver":
        raise ValueError("receiver alias must identify an inventory receiver")
    if devices.get(session["source_alias"], {}).get("role") not in {"native", "transmitter"}:
        raise ValueError("source alias must identify a native remote or transmitter")
    if session["sdr"]["sample_format"] != "cu8" or session["sdr"]["gain_db"] is None:
        raise ValueError("collection requires CU8 and explicit fixed SDR gain")
    manual_gain(session["sdr"]["gain_db"])
    if not 100000 <= session["sdr"]["sample_rate_hz"] <= 2400000:
        raise ValueError("sample rate outside supported detector range")
    if not session["sdr"]["tool_version"]:
        raise ValueError("record the installed SDR tool version before collection")
    if not config.get("recorder_options_verified"):
        raise ValueError("verify installed recorder help/options before collection")
    for name in ("receiver_command", "sdr_command"):
        args = config[name]
        if not isinstance(args, list) or not args or any(not isinstance(a, str) or not a for a in args):
            raise ValueError("recorder commands must be argument arrays")
    if sum(arg.count("{iq}") for arg in config["sdr_command"]) != 1:
        raise ValueError("SDR command must contain exactly one {iq} output placeholder")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", config["device_lock"]):
        raise ValueError("invalid device lock name")


def run(config: dict, output: Path, cycles: int = 5) -> None:
    validate_config(config)
    commands = configured_protocol(config, cycles)
    check_storage(
        output.parent, config["session"]["sdr"]["sample_rate_hz"] * 2 * 240 + 2 * MAX_LOG_BYTES + METADATA_RESERVE_BYTES
    )
    output.mkdir(mode=0o700, parents=False)
    session = dict(config["session"])
    session.update(status="acquiring", started_utc=datetime.now(timezone.utc).isoformat(), trials=[], artifacts=[])
    inventory_path = output / "inventory.json"
    write_json(inventory_path, config["inventory"])
    session["inventory_sha256"] = artifact(inventory_path, output)["sha256"]
    write_json(
        output / "collector.json",
        {
            "commands": commands,
            "capture_protocol": config.get("capture_protocol", "standard"),
            "gain_check_source": (
                artifact(Path(__file__).with_name("waveform_gain.py"), Path(__file__).parent)
                if config.get("capture_protocol") == "gain_check"
                else None
            ),
            "source": artifact(Path(__file__), Path(__file__).parent),
            "recorder_arguments": {key: config[key] for key in ("receiver_command", "sdr_command")},
            "recorder_options_verified": config["recorder_options_verified"],
        },
    )
    try:
        # Linux abstract sockets are released by the kernel, including on SIGKILL.
        # This cooperative lease leaves no stale inode and never kills a foreign owner.
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as lease:
            lease.bind("\0proflame2-sdr-" + config["device_lock"])
            print(
                "Before continuing: manual mode, thermostat OFF, power ON, flame 6, light 0, fan 0. "
                "Leave pilot/aux/front unchanged. No commands are sent by this tool.",
                flush=True,
            )
            for index, command in enumerate(commands, 1):
                trial = collect_trial(output, config, index, command)
                session["trials"].append(trial["trial_id"])
                if config.get("capture_protocol") == "gain_check":
                    trial_root = output / trial["trial_id"]
                    print("Checking sampled SDR clipping and reported tuner gain...", flush=True)
                    quality = inspect_capture(
                        trial_root / "signal.cu8",
                        trial_root / "sdr.stderr",
                        session["sdr"]["gain_db"],
                        session["sdr"]["sample_rate_hz"],
                    )
                    write_json(trial_root / "gain-check.json", quality)
                    gain = quality["reported_gain_db"]
                    print(
                        f"SDR CHECK: reported gain={gain if gain is not None else 'UNCONFIRMED'} dB; "
                        f"{quality['screening']}; sampled clipping={quality['clipped_fraction']:.3%}; "
                        f"worst 100ms block={quality['peak_100ms_clipped_fraction']:.3%}.\n"
                        "No clipping alone does not confirm reception; retain the recording for frame analysis.",
                        flush=True,
                    )
                    if not quality["manual_gain_confirmed"]:
                        raise ValueError("SDR manual gain was not confirmed by recorder diagnostics")
        session["status"] = "complete"
    except (KeyboardInterrupt, SystemExit):
        session["status"] = "aborted"
        raise
    except Exception:
        session["status"] = "failed"
        raise
    finally:
        session["artifacts"] = [artifact(path, output) for path in sorted(output.rglob("*")) if path.is_file()]
        validate_session_files(session, output)
        write_json(output / "session.json", session)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path, help="trusted local recorder configuration and inventory/session metadata")
    parser.add_argument("output", type=Path, help="new private directory on persistent capture storage")
    parser.add_argument("--cycles", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true", help="validate metadata and print prompts; open no recorders")
    args = parser.parse_args()
    if not args.dry_run and args.output.resolve().is_relative_to(Path("/tmp").resolve()):
        parser.error("retained captures must not be stored under /tmp")
    config = json.loads(args.config.read_text())
    validate_config(config)
    if args.dry_run:
        for index, command in enumerate(configured_protocol(config, args.cycles), 1):
            timing = "60s" if command == "quiet" else "up to 60s to reach target, then 15s quiet recording"
            print(f"{index:03d} {command} ({timing})")
    else:

        def terminate(_signum, _frame):
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, terminate)
        run(config, args.output, args.cycles)


if __name__ == "__main__":
    main()
