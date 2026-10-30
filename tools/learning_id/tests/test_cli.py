"""Black-box tests against the wheel installed in a dependency-free environment."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

PYTHON = os.environ.get("PROFLAME2_HELPER_PYTHON")
pytestmark = pytest.mark.skipif(not PYTHON, reason="Run tools/learning_id/validate_package.py")


def transform(command, cd=0):
    high, low = command >> 4, command & 15
    return ((((cd >> 4) ^ high ^ (high << 1) ^ (low << 1)) & 15) << 4) | ((cd ^ high ^ low) & 15)


def lane(values):
    value = 0
    for word in values:
        value = transform(value ^ word)
    return value


def commands(count=7, extended=False):
    records = []
    for index in range(count):
        cmd1, cmd2 = (0x81 if extended else 1) | ((index % 7) << 4), 1 + index % 6
        words = [0x12, 0x34, 0x56, cmd1, cmd2]
        if extended:
            words += [0x15, 0xC8, 0]
            words += [lane(words[:8:2]), lane(words[1:8:2])]
        else:
            words += [transform(cmd1, 0x57), transform(cmd2, 0x18)]
        record = dict(
            model="Proflame2-Remote",
            protocol_variant="extended_10_word" if extended else "legacy_7_word",
            raw_words=bytes(words).hex(),
        )
        records += [record, record.copy()]
    return records


def run(records, *args, mode="file"):
    text = "\n".join(json.dumps(record) for record in records) + "\n"
    return subprocess.run(
        [PYTHON, "-I", "-m", "proflame2_learning_id.cli", "--input-mode", mode, "--unguided", *args],
        input=text,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("extended", [False, True])
@pytest.mark.parametrize(("count", "label"), [(3, "Preliminary"), (4, "Confirmation incomplete"), (5, "Confirmed")])
def test_file_levels_and_single_stdout_id(count, label, extended):
    result = run(commands(count, extended))
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("PF2-1-E-" if extended else "PF2-1-L-")
    assert len(result.stdout.splitlines()) == 1
    assert label in result.stderr
    assert "repeat" in result.stderr or "received at least twice" in result.stderr
    assert result.stdout.strip() not in result.stderr
    assert "Press" not in result.stderr


def test_late_conflict_after_gate_three_never_emits_id():
    records = commands()
    bad = records[-1].copy()
    bad["raw_words"] = "654321" + bad["raw_words"][6:]
    result = run([*records, bad])
    assert result.returncode != 0
    assert result.stdout == ""
    assert "disagree" in result.stderr
    assert "654321" not in result.stderr


@pytest.mark.parametrize("count", [1, 2])
def test_insufficient_file_evidence(count):
    result = run(commands(count))
    assert result.returncode != 0
    assert not result.stdout
    assert "No Learning ID" in result.stderr


def test_repeats_do_not_increase_distinct_commands():
    result = run(commands(1) * 50, "-v")
    assert result.returncode != 0
    assert not result.stdout
    assert "2 more needed before your Learning ID is ready." in result.stderr
    assert result.stderr.count("2 more needed before your Learning ID is ready.") == 1


def test_seven_commands_without_repeats_do_not_pass_first_gate():
    result = run(commands()[::2])
    assert result.returncode != 0
    assert not result.stdout


def test_extended_telemetry_is_not_a_new_command():
    base = bytes.fromhex(commands(1, True)[0]["raw_words"])
    records = []
    for temperature in range(20):
        words = list(base)
        words[6] = temperature
        words[8:] = [lane(words[:8:2]), lane(words[1:8:2])]
        records.append(
            dict(model="Proflame2-Remote", protocol_variant="extended_10_word", raw_words=bytes(words).hex())
        )
    result = run(records, "-v")
    assert result.returncode != 0
    assert "2 more needed before your Learning ID is ready." in result.stderr


@pytest.mark.parametrize("extended", [False, True])
def test_live_stops_only_at_third_gate(extended):
    result = run(commands(4, extended), mode="live")
    assert result.returncode != 0
    assert not result.stdout
    result = run(commands(5, extended), "-v", mode="live")
    assert result.returncode == 0, result.stderr
    assert "Your Learning ID is ready." in result.stderr
    assert "Use Light and Fan values 0, 3, and 6." in result.stderr
    assert result.stderr.count("A plausible Learning ID has been created. Now we need to confirm it.") == 1
    assert "different commands received" not in result.stderr
    assert "commands still need" not in result.stderr
    assert "Step 1 of 3" not in result.stderr


def test_live_switches_from_discovery_to_matches_without_misleading_zero():
    records = commands()
    unique, repeats = records[::2], records[1::2]
    result = run([*unique, *repeats], "-v", mode="live")
    assert result.returncode == 0, result.stderr
    lines = result.stderr.splitlines()
    assert "2 more needed before your Learning ID is ready." in lines
    assert "3 matches needed to confirm your Learning ID." in lines
    assert "2 matches needed to confirm your Learning ID." in lines
    assert "1 match needed to confirm your Learning ID." in lines
    assert "0 more needed before your Learning ID is ready." not in lines
    assert "0 matches needed to confirm your Learning ID." not in lines
    assert "Your Learning ID is ready." in lines


def test_plausible_notice_is_shown_without_verbose_mode():
    result = run(commands(), mode="live")
    assert result.returncode == 0, result.stderr
    assert result.stderr.strip() == "A plausible Learning ID has been created. Now we need to confirm it."


@pytest.mark.parametrize("extended", [False, True])
def test_late_required_variation_needs_independent_checks(extended):
    records = commands(extended=extended)
    for row in records[:-2]:
        words = list(bytes.fromhex(row["raw_words"]))
        words[4] = 6
        if extended:
            words[8:] = [lane(words[:8:2]), lane(words[1:8:2])]
        else:
            words[6] = transform(6, 0x18)
        row["raw_words"] = bytes(words).hex()
    # Previously seen but un-repeated commands can be held-out checks; commands
    # actually used in derivation cannot also count as independent confirmation.
    records = [*records[::2], records[1], records[3], records[-1]]
    result = run(records, "-v", mode="live")
    assert result.returncode == 0, result.stderr
    assert "Your Learning ID is ready." in result.stderr
    assert result.stdout.startswith("PF2-1-E-" if extended else "PF2-1-L-")


@pytest.mark.parametrize("extended", [False, True])
def test_reported_light_fan_sequence_completes_when_repeated(extended):
    # Synthetic reproduction of the reported sequence, not a hardware capture.
    light = fan = 0
    records = []
    for _ in range(2):
        actions = [("power", 1)]
        actions += [("light", value) for value in (1, 2, 3, 2, 1)]
        actions += [("fan", value) for value in (1, 2, 3, 2, 1)]
        actions += [("power", 0)]
        for control, value in actions:
            if control == "light":
                light = value
            elif control == "fan":
                fan = value
            power = value if control == "power" else 1
            words = [0x12, 0x34, 0x56, (0x80 if extended else 0) | power | (light << 4), 6 | (fan << 4)]
            if extended:
                words += [0x15, 0xC8, 0]
                words += [lane(words[:8:2]), lane(words[1:8:2])]
            else:
                words += [transform(words[3], 0x57), transform(words[4], 0x18)]
            records.append(
                dict(
                    model="Proflame2-Remote",
                    protocol_variant="extended_10_word" if extended else "legacy_7_word",
                    raw_words=bytes(words).hex(),
                )
            )
    result = run(records, "-v", mode="live")
    assert result.returncode == 0, result.stderr
    assert "Your Learning ID is ready." in result.stderr


@pytest.mark.parametrize("extended", [False, True])
def test_three_repeated_commands_and_two_single_checks_confirm(extended):
    rows = commands(5, extended)
    base = rows[:6]
    for additional, expected in (([], 1), ([rows[6]], 2), ([rows[6], rows[8]], 3)):
        result = run([*base, *base, *additional], mode="live")
        assert result.returncode == (0 if expected == 3 else 1), result.stderr
        assert bool(result.stdout) == (expected == 3)


def test_legacy_compatibility_and_unrelated_models():
    records = commands()
    for record in records:
        del record["protocol_variant"]
        words = bytes.fromhex(record.pop("raw_words"))
        record.update(
            id=int.from_bytes(words[:3], "big"),
            cmd1=words[3],
            cmd2=words[4],
            err1=words[5],
            err2=words[6],
            mic="CHECKSUM",
        )
    result = run([dict(model="Other-device", id=1), *records])
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "change", [dict(cmd1=0), dict(id=True), dict(cmd1=256), dict(mic=[]), dict(parity_valid=False)]
)
def test_conflicting_and_invalid_fields_rejected(change):
    records = commands()
    records[-1].update(change)
    result = run(records)
    assert result.returncode != 0
    assert not result.stdout
    assert "Traceback" not in result.stderr


def test_extended_never_enters_legacy_path():
    records = commands(7, True)
    del records[-1]["protocol_variant"]
    result = run(records)
    assert result.returncode != 0
    assert not result.stdout


def test_extended_unknown_pilot_is_accepted():
    records = commands(7, True)
    for record in records:
        record["pilot"] = -1
    result = run(records)
    assert result.returncode == 0, result.stderr
    assert "Confirmed" in result.stderr


@pytest.mark.parametrize("pilot", [0, 1, -2, True, "-1"])
def test_extended_pilot_cannot_claim_an_unestablished_mapping(pilot):
    records = commands(7, True)
    records[-1]["pilot"] = pilot
    result = run(records)
    assert result.returncode != 0
    assert not result.stdout


def test_named_file_and_regular_stdin_defaults(tmp_path):
    path = tmp_path / "capture.json"
    path.write_text("\n".join(map(json.dumps, commands(3))))
    command = [PYTHON, "-I", "-m", "proflame2_learning_id.cli"]
    result = subprocess.run([*command, str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "Preliminary" in result.stderr
    with path.open() as stream:
        result = subprocess.run(command, stdin=stream, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "Preliminary" in result.stderr


def test_pipe_defaults_to_live():
    result = subprocess.run(
        [PYTHON, "-I", "-m", "proflame2_learning_id.cli"],
        input="\n".join(map(json.dumps, commands(3))),
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert not result.stdout


@pytest.mark.parametrize(
    "payload", ["{broken", "[]", "x" * 16385, '{"model":"Proflame2-Remote","cmd1":"private-invalid-value"}']
)
def test_bad_json_and_input_bounds_do_not_echo_input(payload):
    result = subprocess.run(
        [PYTHON, "-I", "-m", "proflame2_learning_id.cli", "--input-mode", "file"],
        input=payload,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert not result.stdout
    assert "private-invalid-value" not in result.stderr
    assert "Traceback" not in result.stderr


def test_live_timeout_on_partial_line():
    process = subprocess.Popen(
        [PYTHON, "-I", "-m", "proflame2_learning_id.cli", "--unguided", "--timeout", "0.05"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        process.stdin.write("{")
        process.stdin.flush()
        process.wait(timeout=5)
        assert process.returncode != 0
        assert process.stdout.read() == ""
        assert "timed out" in process.stderr.read()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdin.close()
        process.stdout.close()
        process.stderr.close()


def test_console_entrypoint_never_requests_local_features():
    binary = Path(PYTHON).with_name("proflame2-learning-id")
    result = subprocess.run([str(binary), "--help"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "--input-mode" in result.stdout
    assert "--features" not in result.stdout
    result = subprocess.run([str(binary)], input="", capture_output=True, text=True)
    assert result.returncode != 0
    assert "--features" not in result.stderr
    assert not result.stdout


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("\n" * 100001, "record limit"),
        ((" " * 16000 + "\n") * 1050, "size limit"),
    ],
    ids=["record-count", "total-bytes"],
)
def test_input_volume_and_record_limits(text, error):
    result = subprocess.run(
        [PYTHON, "-I", "-m", "proflame2_learning_id.cli", "--input-mode", "file"],
        input=text,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert not result.stdout
    assert error in result.stderr, result.stderr


def test_missing_legacy_command_variation_cannot_pass_by_quantity():
    records = commands()
    for record in records:
        words = list(bytes.fromhex(record["raw_words"]))
        words[3] = 1
        words[5] = transform(1, 0x57)
        record["raw_words"] = bytes(words).hex()
    result = run(records, "-v")
    assert result.returncode != 0
    assert not result.stdout
    assert "power, light, or mode" in result.stderr
