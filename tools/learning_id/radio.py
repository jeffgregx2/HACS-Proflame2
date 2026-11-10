"""Own an rtl_433 receiver for one helper invocation, then stop that receiver.

An externally created shell pipeline is not owned by the helper. This entry
point instead launches its own receive-only child and cleans it up even when
the remote becomes quiet after the Learning ID is printed.
"""

from __future__ import annotations

import argparse
import io
import os
import select
import signal
import subprocess
import sys
import threading
from collections import deque

from .cli import main as collect
from .help_text import add_inspection_option
from .receiver_probe import ReceiverCompatibilityError, check


def choose(executable: str, interactive: bool) -> tuple[str, str]:
    """Ask for a replacement executable before opening any radio hardware."""
    while True:
        try:
            return executable, check(executable)
        except ReceiverCompatibilityError as exc:
            selected = "The rtl_433 in the PATH" if executable == "rtl_433" else "The selected rtl_433"
            print(
                f"{selected} is not compatible. You will need to supply the full path to a compatible version.",
                file=sys.stderr,
            )
            if not interactive:
                print(str(exc), file=sys.stderr)
        if not interactive:
            raise ValueError("Supply --rtl433 with an extended-capable executable path.")
        try:
            with open("/dev/tty", encoding="utf-8") as terminal:
                print(
                    "\nEnter the full path to the compatible rtl_433 executable (without quotes), or q to cancel:",
                    file=sys.stderr,
                    flush=True,
                )
                answer = terminal.readline(4096).strip()
        except OSError:
            raise ValueError(
                "Supply --rtl433 with an extended-capable executable path; no interactive terminal is available."
            ) from None
        if answer.lower() == "q":
            raise KeyboardInterrupt
        if not answer:
            raise ValueError("No compatible rtl_433 executable selected.")
        executable = os.path.expanduser(answer)


class Diagnostics:
    """Drain receiver stderr without interleaving it with prompts or blocking RF."""

    def __init__(self, stream):
        self.stream = stream
        self.tail = deque(maxlen=8)
        self.stopping = threading.Event()
        self.worker = threading.Thread(target=self.read, daemon=True)
        self.worker.start()

    def read(self):
        try:
            while not self.stopping.is_set():
                if not select.select([self.stream], [], [], 0.1)[0]:
                    continue
                chunk = os.read(self.stream.fileno(), 4096)
                if not chunk:
                    return
                self.tail.append(chunk)
        except (OSError, ValueError):
            return

    def finish(self, show: bool):
        # The receiver has stopped; normally EOF drains its remaining output.
        self.worker.join(timeout=1)
        self.stopping.set()
        self.worker.join(timeout=1)
        self.stream.close()
        if show and self.tail:
            print("\nrtl_433 diagnostics (bounded tail):", file=sys.stderr)
            print(b"".join(self.tail).decode("utf-8", errors="replace").strip(), file=sys.stderr)


def stop(receiver: subprocess.Popen) -> None:
    if receiver.poll() is None:
        try:
            receiver.terminate()
        except ProcessLookupError:
            pass
        try:
            receiver.wait(timeout=3)
        except subprocess.TimeoutExpired:
            receiver.kill()
            receiver.wait()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Other arguments are passed to the helper, for example --timeout 600 or --unguided.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--rf-band", choices=("315", "433"), required=True, help="Native remote's radio band.")
    parser.add_argument("--rtl433", default="rtl_433", help="rtl_433 executable name or path.")
    add_inspection_option(parser)
    args, helper_args = parser.parse_known_args(argv)
    frequency = "315M" if args.rf_band == "315" else "433.92M"

    def interrupted(_signal, _frame):
        raise KeyboardInterrupt

    handlers = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGINT, signal.SIGTERM)}
    receiver = None
    original_stdin = sys.stdin
    stream = None
    diagnostics = None
    status = 1
    try:
        executable, label = choose(args.rtl433, interactive="--unguided" not in helper_args)
        print(f"Receiver: {label}\nOffline compatibility checks passed.", file=sys.stderr, flush=True)
        receiver = subprocess.Popen(
            [executable, "-R", "207", "-f", frequency, "-F", "json"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        diagnostics = Diagnostics(receiver.stderr)
        stream = io.TextIOWrapper(receiver.stdout, encoding="utf-8")
        sys.stdin = stream
        status = collect(["--input-mode", "live", *helper_args])
        return status
    except ValueError as exc:
        print(f"{exc} No Learning ID generated.", file=sys.stderr)
        return 1
    except OSError:
        print("Unable to start rtl_433. Check --rtl433 and SDR access. No Learning ID generated.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        status = 130
        return 130
    finally:
        for sig in handlers:
            signal.signal(sig, signal.SIG_IGN)
        sys.stdin = original_stdin
        if stream is not None:
            stream.close()
        if receiver is not None:
            stop(receiver)
        if diagnostics is not None:
            diagnostics.finish(show=status not in (0, 130))
        for sig, handler in handlers.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    raise SystemExit(main())
