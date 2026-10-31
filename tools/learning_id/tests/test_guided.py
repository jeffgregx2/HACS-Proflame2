"""Drive installed guided capture using a real controlling PTY and RF pipe.

Frames are synthetic test signals. No SDR, native remote, or fireplace is used.
"""

from __future__ import annotations

import json
import os
import pty
import re
import select
import signal
import subprocess
import sys
import time
from contextlib import contextmanager, suppress
from itertools import groupby
from pathlib import Path

import pytest

PYTHON = os.environ.get("PROFLAME2_HELPER_PYTHON")
pytestmark = pytest.mark.skipif(not PYTHON, reason="Run tools/learning_id/validate_package.py")
PROMPT = re.compile(
    r"Press Power (ON|OFF) once\.|Select (Light|Fan|Flame) on the remote\. "
    r"Press (UP|DOWN) ONCE; the remote should show (\d+) bars?\."
)


def transform(command, cd=0):
    high, low = command >> 4, command & 15
    return ((((cd >> 4) ^ high ^ (high << 1) ^ (low << 1)) & 15) << 4) | ((cd ^ high ^ low) & 15)


def lane(values):
    value = 0
    for word in values:
        value = transform(value ^ word)
    return value


def record(state, extended, temperature=200):
    cmd1 = (0x80 if extended else 0) | state["Power"] | (state["Light"] << 4)
    cmd2 = state["Flame"] | (state["Fan"] << 4)
    words = [0x12, 0x34, 0x56, cmd1, cmd2]
    if extended:
        words += [0x15, temperature, 0]
        words += [lane(words[:8:2]), lane(words[1:8:2])]
    else:
        words += [transform(cmd1, 0x57), transform(cmd2, 0x18)]
    return dict(
        model="Proflame2-Remote",
        protocol_variant="extended_10_word" if extended else "legacy_7_word",
        raw_words=bytes(words).hex(),
    )


class Session:
    def __init__(self, process, master):
        self.process = process
        self.master = master
        self.buffer = ""
        self.transcript = ""

    def diagnostics(self):
        """Request a child traceback without waiting indefinitely for its output."""
        if self.process.poll() is None:
            with suppress(ProcessLookupError):
                self.process.send_signal(signal.SIGUSR1)
        deadline = time.monotonic() + 0.5
        size = 0
        while size < 65536:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.master], [], [], remaining)[0]:
                break
            try:
                chunk = os.read(self.master, min(4096, 65536 - size))
            except OSError:
                break
            if not chunk:
                break
            size += len(chunk)
            self.transcript += chunk.decode(errors="replace")
        return self.transcript

    def wait(self, timeout=5):
        try:
            return self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            print("Helper did not exit before timeout. Child diagnostics:\n" + self.diagnostics(), file=sys.stderr)
            raise

    def read_until(self, pattern, timeout=5):
        pattern = re.compile(pattern) if isinstance(pattern, str) else pattern
        deadline = time.monotonic() + timeout
        while True:
            match = pattern.search(self.buffer)
            if match:
                self.buffer = self.buffer[match.end() :]
                return match
            remaining = deadline - time.monotonic()
            assert remaining > 0, self.transcript
            assert select.select([self.master], [], [], remaining)[0], self.transcript
            try:
                chunk = os.read(self.master, 4096).decode()
            except OSError:
                pytest.fail(self.transcript)
            assert chunk, self.transcript
            self.buffer += chunk
            self.transcript += chunk

    def send(self, row):
        self.process.stdin.write(json.dumps(row) + "\n")
        self.process.stdin.flush()

    def keyboard(self, text=""):
        os.write(self.master, (text + "\n").encode())

    def prepare(self, choice="", delay=0):
        self.read_until("Press Return when it is OFF and you are ready")
        if delay:
            time.sleep(delay)
            assert self.process.poll() is None
        self.keyboard()
        self.read_until("Does your remote let you adjust both the light and fan\\?")
        self.keyboard("n" if choice else "")
        if choice:
            self.read_until("Can you adjust the light using your remote\\?")
            self.keyboard("n" if choice == "power" else "")


@contextmanager
def session(timeout="2", command=None):
    master, slave = pty.openpty()
    command = command or [PYTHON, "-I", "-m", "proflame2_learning_id.cli", "--input-mode", "live", "--timeout", timeout]
    try:
        process = subprocess.Popen(
            [command[0], "-I", "-u", str(Path(__file__).with_name("pty_child.py")), str(slave), *command[1:]],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=slave,
            text=True,
            pass_fds=(slave,),
            start_new_session=True,
        )
    except BaseException:
        os.close(master)
        raise
    finally:
        os.close(slave)
    capture = Session(process, master)
    try:
        yield capture
    finally:
        failed = sys.exc_info()[0] is not None
        if failed and process.poll() is None:
            print("Guided test failed. Child diagnostics:\n" + capture.diagnostics(), file=sys.stderr)
        # Release the controlling terminal before reaping on macOS, and include
        # the synthetic receiver without touching another test's process group.
        os.close(master)
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            message = "Guided test cleanup could not reap its child within 5 seconds."
            if failed:
                print(message, file=sys.stderr)
            else:
                pytest.fail(message)
        finally:
            for stream in (process.stdin, process.stdout):
                with suppress(OSError):
                    stream.close()


def test_session_reports_a_live_child_traceback():
    with session() as capture:
        capture.read_until("Press Return when it is OFF and you are ready")
        transcript = capture.diagnostics()
        assert "guided.py" in transcript
        assert "pty_child.py" in transcript


def test_session_cleanup_preserves_the_original_failure_and_closes_terminal(monkeypatch, capsys):
    real_wait = None
    try:
        with pytest.raises(RuntimeError, match="original guided failure"):
            with session() as capture:
                capture.read_until("Press Return when it is OFF and you are ready")
                real_wait = capture.process.wait

                def timeout_wait(timeout):
                    raise subprocess.TimeoutExpired("test child", timeout)

                monkeypatch.setattr(capture.process, "wait", timeout_wait)
                raise RuntimeError("original guided failure")
        assert "cleanup could not reap its child" in capsys.readouterr().err
        with pytest.raises(OSError):
            os.fstat(capture.master)
        assert capture.process.stdin.closed
        assert capture.process.stdout.closed
    finally:
        if real_wait is not None:
            real_wait(timeout=5)


@pytest.mark.parametrize("extended", [False, True])
@pytest.mark.parametrize("choice", ["", "flame", "power"])
@pytest.mark.parametrize("repeats", [1, 3])
@pytest.mark.parametrize("initial_levels", [(5, 4, 6), (0, 0, 1)])
def test_guided_confirms_each_action_and_finishes_off(extended, choice, repeats, initial_levels):
    with session() as capture:
        capture.prepare(choice)
        state = dict(Power=0, **dict(zip(("Light", "Fan", "Flame"), initial_levels, strict=True)))
        actions = []
        for _ in range(100):
            match = capture.read_until(
                re.compile(
                    PROMPT.pattern + r"|Allow the fireplace's normal shutdown time\.|Your Learning ID is ready\."
                )
            )
            if match.group(0).startswith("Your Learning ID"):
                break
            if match.group(0).startswith("Allow"):
                assert not state["Power"]
                capture.keyboard()
                continue
            control = "Power" if match.group(1) else match.group(2)
            target = int(match.group(1) == "ON") if control == "Power" else int(match.group(4))
            if control != "Power":
                assert abs(state[control] - target) == 1
            state[control] = target
            actions.append((control, target))
            for repeat in range(repeats):
                capture.send(record(state, extended, 190 + repeat))
            capture.read_until(
                f"Received: {control} {('ON' if target else 'OFF') if control == 'Power' else target}\\."
            )
        else:
            pytest.fail("Guided sequence did not finish")
        capture.process.wait(timeout=5)
        assert capture.process.returncode == 0, capture.transcript
        assert not state["Power"]
        assert "\r\n\r\nSelect" in capture.transcript
        if choice != "power" and repeats == 1:
            groups = [list(group) for _, group in groupby(actions, key=lambda action: action[0])]
            expected = [("Light", value) for value in (2, 3, 2, 1)]
            assert groups[1][-len(expected) :] == expected
            if initial_levels[0] == 0:
                assert groups[1] == [("Light", 1), *expected]
            assert "the remote should show 1 bar." in capture.transcript
            assert "the remote should show 2 bars." in capture.transcript
        token = capture.process.stdout.read().strip()
        assert token.startswith("PF2-1-E-" if extended else "PF2-1-L-")
        assert len(token.splitlines()) == 1


def test_preparation_has_no_action_timeout_and_can_be_cancelled():
    with session(timeout="0.05") as capture:
        capture.read_until("Press Return when it is OFF and you are ready")
        time.sleep(0.15)
        assert capture.process.poll() is None
        capture.keyboard("q")
        capture.process.wait(timeout=5)
        assert capture.process.returncode == 130
        assert not capture.process.stdout.read()


def test_control_questions_reprompt_invalid_answers_and_allow_cancellation():
    with session() as capture:
        capture.read_until("Press Return when it is OFF and you are ready")
        capture.keyboard()
        question = "Does your remote let you adjust both the light and fan\\?"
        capture.read_until(question)
        capture.keyboard("maybe")
        capture.read_until(question)
        capture.keyboard("no")
        capture.read_until("Can you adjust the light using your remote\\?")
        capture.keyboard("q")
        capture.process.wait(timeout=5)
        assert capture.process.returncode == 130
        assert "Press Power ON" not in capture.transcript
        assert not capture.process.stdout.read()


def test_wrong_control_is_reported_without_advancing():
    with session() as capture:
        capture.prepare()
        capture.read_until("Press Power ON once")
        state = dict(Power=1, Light=3, Fan=1, Flame=6)
        capture.send(record(state, True))
        capture.read_until("Select Light on the remote. Press DOWN ONCE; the remote should show 2 bars")
        state["Fan"] = 2
        capture.send(record(state, True))
        capture.read_until("Still waiting for the requested setting.")
        capture.read_until("Select Light on the remote. Press DOWN ONCE; the remote should show 2 bars")
        state["Light"] = 2
        capture.send(record(state, True))
        capture.read_until("Received: Light 2.")
        capture.read_until("Select Light on the remote. Press DOWN ONCE; the remote should show 1 bar")


def test_overshot_level_requests_the_correct_reverse_direction():
    with session() as capture:
        capture.prepare()
        capture.read_until("Press Power ON once")
        state = dict(Power=1, Light=3, Fan=1, Flame=6)
        capture.send(record(state, False))
        capture.read_until("Select Light on the remote. Press DOWN ONCE; the remote should show 2 bars")
        state["Light"] = 1
        capture.send(record(state, False))
        capture.read_until("Select Light on the remote. Press UP ONCE; the remote should show 2 bars")
        state["Light"] = 2
        capture.send(record(state, False))
        capture.read_until("Received: Light 2.")


def test_preparation_frames_do_not_identify_a_remote_or_satisfy_power_on():
    with session() as capture:
        capture.read_until("Press Return when it is OFF and you are ready")
        capture.send(dict(model="Unrelated device", id=1))
        capture.send(record(dict(Power=1, Light=6, Fan=6, Flame=6), False))
        capture.keyboard()
        capture.read_until("Does your remote let you adjust both the light and fan\\?")
        capture.keyboard()
        capture.read_until("Press Power ON once")
        assert "Received:" not in capture.transcript
        capture.send(record(dict(Power=1, Light=3, Fan=1, Flame=6), True))
        capture.read_until("Received: Power ON.")
        capture.read_until("Select Light on the remote. Press DOWN ONCE; the remote should show 2 bars")


@pytest.mark.parametrize("fault", ["eof", "malformed", "timeout"])
def test_guided_failure_never_emits_an_id(fault):
    with session(timeout="0.1") as capture:
        capture.prepare()
        capture.read_until("Press Power ON once")
        if fault == "eof":
            capture.process.stdin.close()
        elif fault == "malformed":
            capture.process.stdin.write("{broken\n")
            capture.process.stdin.flush()
        capture.process.wait(timeout=5)
        assert capture.process.returncode == 1
        assert not capture.process.stdout.read()


def test_no_terminal_requires_explicit_unguided_mode():
    result = subprocess.run(
        [PYTHON, "-I", "-m", "proflame2_learning_id.cli", "--input-mode", "live"],
        input="",
        capture_output=True,
        text=True,
        start_new_session=True,
    )
    assert result.returncode == 1
    assert "interactive terminal" in result.stderr
    assert "--unguided" in result.stderr
    assert not result.stdout
