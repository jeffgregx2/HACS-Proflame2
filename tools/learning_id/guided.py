"""Terminal-guided native-remote capture; keyboard input is separate from RF JSON.

No commands are transmitted. Every acknowledged action is a decoded remote
command, not an acknowledgement from the fireplace. Preparation data is discarded
so an already-buffered command cannot satisfy the first Power ON instruction.
"""

from __future__ import annotations

import json
import os
import select
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import BinaryIO, TextIO

from proflame2_protocol.packet import ProflameFrame
from proflame2_protocol.rtl433 import normalize_rtl433_record

from .evidence import Evidence

LIMIT_LINE = 16384
LIMIT_BYTES = 16 * 1024 * 1024
LIMIT_RECORDS = 100000
FIELDS = {"Power": ("cmd1", 0, 1), "Light": ("cmd1", 4, 7), "Fan": ("cmd2", 4, 7), "Flame": ("cmd2", 0, 7)}


def level(frame: ProflameFrame, control: str) -> int:
    field, shift, mask = FIELDS[control]
    return (getattr(frame, field) >> shift) & mask


class Receiver:
    """Bound JSON input while also servicing a separate controlling terminal."""

    def __init__(self, stream: BinaryIO):
        self.stream = stream
        self.pending = bytearray()
        self.total = self.lines = 0
        self.drop_partial = False

    def event(self, timeout: float | None, terminal: TextIO | None = None) -> tuple[str, bytes | str]:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            if b"\n" in self.pending:
                index = self.pending.index(b"\n") + 1
                if index > LIMIT_LINE:
                    raise ValueError("A JSON input line exceeds the length limit.")
                line = bytes(self.pending[:index])
                del self.pending[:index]
                self.lines += 1
                if self.lines > LIMIT_RECORDS:
                    raise ValueError("Input record limit reached.")
                if self.drop_partial:
                    self.drop_partial = False
                    continue
                return "rf", line
            if len(self.pending) > LIMIT_LINE:
                raise ValueError("A JSON input line exceeds the length limit.")
            remaining = None if deadline is None else max(0, deadline - time.monotonic())
            readers = [self.stream] if terminal is None else [self.stream, terminal]
            ready, _, _ = select.select(readers, [], [], remaining)
            if not ready:
                raise ValueError("No requested command received before the timeout. No Learning ID generated.")
            if terminal is not None and terminal in ready:
                answer = terminal.readline(128)
                if not answer:
                    raise ValueError("Keyboard input ended. No Learning ID generated.")
                return "keyboard", answer.strip().lower()
            chunk = os.read(self.stream.fileno(), 4096)
            if not chunk:
                raise ValueError("RF input ended before guided learning completed.")
            self.total += len(chunk)
            if self.total > LIMIT_BYTES:
                raise ValueError("Input size limit reached.")
            self.pending.extend(chunk)

    def answer(self, terminal: TextIO, message: str) -> str:
        print(f"\n{message}", file=sys.stderr, flush=True)
        while True:
            kind, value = self.event(None, terminal)
            if kind == "keyboard":
                if value == "q":
                    raise KeyboardInterrupt
                # Discard decoded data accumulated during preparation as well.
                tail = bytes(self.pending[-1:])
                self.pending.clear()
                while select.select([self.stream], [], [], 0)[0]:
                    chunk = os.read(self.stream.fileno(), 4096)
                    if not chunk:
                        raise ValueError("RF input ended before guided learning completed.")
                    self.total += len(chunk)
                    if self.total > LIMIT_BYTES:
                        raise ValueError("Input size limit reached.")
                    tail = chunk[-1:]
                self.drop_partial = bool(tail and tail != b"\n")
                return str(value)


@dataclass(frozen=True)
class Target:
    control: str
    value: int

    def receipt(self) -> str:
        value = ("ON" if self.value else "OFF") if self.control == "Power" else str(self.value)
        return f"Received: {self.control} {value}."


@dataclass(frozen=True)
class Pause:
    message: str


class Guide:
    """Generate small, repeatable state changes from actual observed levels."""

    def __init__(self, evidence: Evidence, controls: tuple[str, str]):
        self.evidence = evidence
        self.controls = controls
        self.frame: ProflameFrame | None = None

    def move(self, control: str, target: int) -> Iterator[Target]:
        while level(self.frame, control) != target:
            current = level(self.frame, control)
            yield Target(control, current + (1 if target > current else -1))

    def steps(self) -> Iterator[Target | Pause]:
        yield Target("Power", 1)
        if self.controls[0] == "Power":
            for cycle in range(3):
                if cycle:
                    yield Pause(
                        "Allow the fireplace's normal shutdown time. Press Return when ready to turn it on again (q cancels)."
                    )
                    yield Target("Power", 1)
                for value in (6, 5, 4, 3, 2, 1, 2, 3, 4, 5, 6):
                    yield from self.move("Flame", value)
                yield Target("Power", 0)
        else:
            first, second = self.controls
            for _ in range(3):
                # Keep initial adjustment and sweep together in the same remote mode.
                for control in (first, second):
                    yield from self.move(control, 1)
                    for value in (2, 3, 2, 1):
                        yield from self.move(control, value)
        raise ValueError("The guided sequence did not collect enough consistent matches. Save JSON for support.")

    def instruction(self, target: Target) -> str:
        if target.control == "Power":
            return f"Press Power {'ON' if target.value else 'OFF'} once. Waiting for its received command..."
        current = level(self.frame, target.control)
        direction = "UP" if target.value > current else "DOWN"
        bars = "bar" if target.value == 1 else "bars"
        return (
            f"Select {target.control} on the remote. Press {direction} ONCE; "
            f"the remote should show {target.value} {bars}. Waiting for receipt..."
        )


def collect(stream: BinaryIO, evidence: Evidence, timeout: float, terminal: TextIO) -> None:
    """Run preparation and per-command confirmation; return only when complete."""
    receiver = Receiver(stream)
    receiver.answer(
        terminal,
        "Before starting: turn the fireplace OFF and disable thermostat mode.\n"
        "Use only the native remote during this test; do not use HA controls.\n"
        "These button presses may operate the fireplace. Follow its normal operating instructions.\n"
        "Take your time. Press Return when it is OFF and you are ready (q cancels).",
    )

    def supported(question: str) -> bool:
        while True:
            answer = receiver.answer(terminal, question + "\nPress Return for Yes, or type n for No. q cancels.")
            if answer in ("", "y", "yes", "n", "no"):
                return answer not in ("n", "no")
            print("Press Return for Yes, or type n for No.", file=sys.stderr)

    if supported("Does your remote let you adjust both the light and fan?"):
        controls = ("Light", "Fan")
    elif supported("Can you adjust the light using your remote?"):
        controls = ("Light", "Flame")
    else:
        controls = ("Power", "Flame")
    guide = Guide(evidence, controls)
    steps = guide.steps()
    target = next(steps)
    print(f"\n{guide.instruction(target)}", file=sys.stderr, flush=True)
    deadline = time.monotonic() + timeout
    last_command = None
    finishing = False
    while True:
        kind, value = receiver.event(max(0, deadline - time.monotonic()), terminal)
        if kind == "keyboard":
            if value == "q":
                raise KeyboardInterrupt
            print("No Return is needed for button presses. Waiting for the requested remote command.", file=sys.stderr)
            continue
        if not value.strip():
            continue
        try:
            record = json.loads(value)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise ValueError("Malformed JSON in guided RF input.") from exc
        normalized = normalize_rtl433_record(record)
        if normalized is None:
            continue
        frame = normalized.frame
        if frame.cmd1 & 2:
            raise ValueError("Thermostat mode is enabled. Turn it off and restart guided learning.")
        # Ignore OFF/repeat data until the requested first ON command arrives.
        if guide.frame is None and not level(frame, "Power"):
            continue
        previous_gate = evidence.gate
        if target.control != "Power" and not level(frame, "Power"):
            raise ValueError("Received Power OFF before confirmation completed. Restart guided learning when ready.")
        evidence.add(frame)
        guide.frame = frame
        if previous_gate == 0 and evidence.gate:
            print("A plausible Learning ID has been created. Now we need to confirm it.", file=sys.stderr)
        if evidence.gate == 3 and not finishing and level(frame, target.control) == target.value:
            print(target.receipt(), file=sys.stderr, flush=True)
            if not level(frame, "Power"):
                print("\nYour Learning ID is ready.", file=sys.stderr)
                return
            print("\nAll confirmation checks passed. Finish by turning the fireplace OFF.", file=sys.stderr)
            target = Target("Power", 0)
            finishing = True
            if level(frame, "Power"):
                print(f"\n{guide.instruction(target)}", file=sys.stderr, flush=True)
                last_command = (frame.cmd1, frame.cmd2)
                deadline = time.monotonic() + timeout
                continue
            deadline = time.monotonic() + timeout
        command = (frame.cmd1, frame.cmd2)
        if level(frame, target.control) != target.value:
            if command != last_command:
                print(
                    f"Received: Light {level(frame, 'Light')}, Fan {level(frame, 'Fan')}, Flame {level(frame, 'Flame')}. "
                    "Still waiting for the requested setting.",
                    file=sys.stderr,
                )
                print(f"\n{guide.instruction(target)}", file=sys.stderr, flush=True)
            last_command = command
            continue
        print(target.receipt(), file=sys.stderr, flush=True)
        if finishing:
            print("\nYour Learning ID is ready.", file=sys.stderr)
            return
        target = next(steps)
        if isinstance(target, Pause):
            receiver.answer(terminal, target.message)
            target = next(steps)
        print(f"\n{guide.instruction(target)}", file=sys.stderr, flush=True)
        last_command = command
        deadline = time.monotonic() + timeout
