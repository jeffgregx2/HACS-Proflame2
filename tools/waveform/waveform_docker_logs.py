"""Bounded ESPHome Docker log reader with an in-container heartbeat watchdog."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import time

# This supervisor runs inside the existing container. No source, config or
# credentials are copied. EOF, lost heartbeat or deadline stops its own logger.
SUPERVISOR = r"""
import os, select, signal, subprocess, sys, time
child = None
def stop(signum, frame):
    raise KeyboardInterrupt
signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
try:
    if not select.select([0], [], [], 2)[0] or not os.read(0, 4096):
        raise SystemExit(2)
    child = subprocess.Popen(sys.argv[1:], stdin=subprocess.DEVNULL, start_new_session=True)
    end = time.monotonic() + 240
    while child.poll() is None:
        if time.monotonic() >= end:
            raise SystemExit(2)
        if not select.select([0], [], [], 2)[0] or not os.read(0, 4096):
            break
    if child.poll() is not None:
        raise SystemExit(child.returncode)
finally:
    if child is not None:
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            child.wait(timeout=0.25)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=0.25)
        finally:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
"""


def docker_command(container: str, configuration: str, device: str) -> list[str]:
    if not container or container.startswith("-") or not configuration.startswith("/") or not device:
        raise ValueError("container, absolute YAML path and network device are required")
    if device != "OTA" and ("/" in device or device.startswith("-")):
        raise ValueError("only network API logging is permitted")
    return [
        "docker",
        "exec",
        "-i",
        "-e",
        "PYTHONUNBUFFERED=1",
        container,
        "python",
        "-u",
        "-c",
        SUPERVISOR,
        "esphome",
        "logs",
        configuration,
        "--device",
        device,
        "--no-states",
    ]


def stream_logs(command: list[str], duration: float = 230) -> int:
    if not 0 < duration <= 230:
        raise ValueError("logger duration must be in (0, 230] seconds")
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    previous = {s: signal.signal(s, stop) for s in (signal.SIGTERM, signal.SIGINT)}
    client = None
    try:
        # Keep Docker outside the collector's process group: the wrapper closes
        # stdin and gives the supervisor time to clean up before Docker exits.
        client = subprocess.Popen(command, stdin=subprocess.PIPE, start_new_session=True)
        end = time.monotonic() + duration
        while not stopping and time.monotonic() < end and client.poll() is None:
            try:
                client.stdin.write(b"heartbeat\n")
                client.stdin.flush()
            except BrokenPipeError:
                break
            time.sleep(0.25)
    finally:
        if client is not None:
            try:
                client.stdin.close()
            except BrokenPipeError:
                pass
            try:
                client.wait(timeout=2.5)
            except subprocess.TimeoutExpired:
                os.killpg(client.pid, signal.SIGKILL)
                client.wait(timeout=1)
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    return client.returncode


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("container")
    parser.add_argument("configuration")
    parser.add_argument("--device", default="OTA")
    args = parser.parse_args()
    raise SystemExit(stream_logs(docker_command(args.container, args.configuration, args.device)))


if __name__ == "__main__":
    main()
