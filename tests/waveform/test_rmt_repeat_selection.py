"""Actual C++ selection/receiver regressions with compact synthetic RF inputs."""

from __future__ import annotations

import json
import random
import re
import shutil
import subprocess
from itertools import groupby
from pathlib import Path

import pytest
from test_waveform_diagnostics import _write_receiver_stubs

from custom_components.proflame2.protocol.ecc import build_err_byte, build_extended_integrity_words
from custom_components.proflame2.protocol.packet import ProflameFrame
from custom_components.proflame2.rf.pulse import decode_pcm_words, find_proflame_pcm_candidates
from custom_components.proflame2.rf.waveform import frame_to_air_bytes

ROOT = Path(__file__).resolve().parents[2]
COMPONENT = ROOT / "esphome/components/proflame2_tembed"
PROFILE = (0x123456, 5, 7, 1, 8)


def bits_for(*, extended=False, cmd1=1, cmd2=6, serial=PROFILE[0], w6=20, w7=200, w8=0, integrity_fault=0):
    if extended:
        w9, w10 = build_extended_integrity_words(serial, cmd1, cmd2, w6, w7, w8)
        frame = ProflameFrame(
            serial_id=serial,
            cmd1=cmd1,
            cmd2=cmd2,
            err1=w6,
            err2=w7,
            extension_words=(w8, w9 ^ (integrity_fault == 9), w10 ^ (integrity_fault == 10)),
        )
    else:
        frame = ProflameFrame(
            serial_id=serial,
            cmd1=cmd1,
            cmd2=cmd2,
            err1=build_err_byte(cmd1, *PROFILE[1:3]),
            err2=build_err_byte(cmd2, *PROFILE[3:5]),
        )
    return "".join(f"{byte:08b}" for byte in frame_to_air_bytes(frame))[: 259 if extended else 181]


def stretched(bits, at=None):
    if at is None:
        at = next(match.end() for match in re.finditer("0+", bits) if len(match[0]) in (1, 2))
    return bits[:at] + "0" + bits[at:]


@pytest.fixture(scope="module")
def selector(tmp_path_factory):
    folder = tmp_path_factory.mktemp("rmt-selection")
    _write_receiver_stubs(folder)
    binary = folder / "selection"
    compiler = shutil.which("g++")
    assert compiler, "g++ is required for firmware host tests"
    subprocess.run(
        [
            compiler,
            "-std=c++17",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-O2",
            "-I",
            str(folder),
            "-I",
            str(COMPONENT),
            str(Path(__file__).parent / "cpp/test_rmt_repeat_selection.cpp"),
            str(COMPONENT / "rmt_ook_receiver.cpp"),
            str(COMPONENT / "rmt_repeat_selection.cpp"),
            str(COMPONENT / "proflame2_decoder.cpp"),
            "-o",
            str(binary),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return binary


def run_cases(selector, cases, *, command="select"):
    source = []
    for case in cases:
        mode = case.get("mode", 1)
        context = (case.get("active", mode != 0), case.get("learning", False), mode == 1)
        values = case["segments"]
        fields = (*map(int, context), *case.get("profile", PROFILE), len(values))
        source.append(" ".join(map(str, fields)) + " " + " ".join(str(value) or "-" for value in values))
    output = subprocess.check_output([str(selector), command], input="\n".join(source) + "\n", text=True)
    results = []
    for line in output.splitlines():
        converted, replaced, skipped, agreeing, name, symbols, transitions, first_high, first_low, bits = line.split()
        results.append(
            dict(
                converted=bool(int(converted)),
                replaced=bool(int(replaced)),
                skipped=int(skipped),
                agreeing=int(agreeing),
                format=name,
                symbols=int(symbols),
                transitions=int(transitions),
                first_high=int(first_high),
                first_low=int(first_low),
                bits="" if bits == "-" else bits,
            )
        )
    assert len(results) == len(cases)
    return results


@pytest.mark.parametrize("extended", [False, True])
def test_rescue_validates_independent_repeat_and_preserves_metadata(selector, extended):
    good = bits_for(extended=extended)
    bad = stretched(good)
    assert find_proflame_pcm_candidates(bad) == []
    result = run_cases(selector, [dict(mode=2 if extended else 1, segments=[bad, good, good])])[0]
    assert result["replaced"] and result["bits"] == good
    assert result["skipped"] == 1 and result["agreeing"] == 2
    assert result["symbols"] == 250  # Window-level metadata belongs to the whole acquisition.
    assert (result["transitions"], result["first_high"], result["first_low"]) == (11, 101, 201)


@pytest.mark.parametrize("extended", [False, True])
def test_counters_duplicates_conflicts_and_unchanged_raw_fallback(selector, extended):
    good = bits_for(extended=extended)
    other = bits_for(extended=extended, cmd2=5)
    bad = stretched(good)
    cases = [
        ([bad, bad, good], good, 2),
        ([good, good], good, 0),
        ([bad, good, other], bad, 0),
        ([bad], bad, 0),
        ([bad, bad], bad, 1),
        ([bad, "0101", good], good, 1),
    ]
    results = run_cases(selector, [dict(mode=2 if extended else 1, segments=s) for s, _, _ in cases])
    for result, (_, expected, skipped) in zip(results, cases, strict=True):
        assert (result["bits"], result["skipped"]) == (expected, skipped)


@pytest.mark.parametrize("extended", [False, True])
@pytest.mark.parametrize("context", [dict(active=False), dict(learning=True)])
def test_disabled_and_learning_context_do_not_rescue_or_count(selector, extended, context):
    good = bits_for(extended=extended)
    bad = stretched(good)
    result = run_cases(selector, [dict(mode=2 if extended else 1, segments=[bad, good], **context)])[0]
    assert result["bits"] == bad and result["skipped"] == 0 and result["format"] == "disabled"


def test_wrong_identity_or_ecc_cannot_rescue_and_invalid_profile_is_disabled(selector):
    good = bits_for()
    bad = stretched(good)
    for profile in ((0x654321, *PROFILE[1:]), (PROFILE[0], 0, 0, 0, 0), (PROFILE[0], 16, 0, 0, 0)):
        result = run_cases(selector, [dict(segments=[bad, good], profile=profile)])[0]
        assert not result["replaced"] and result["bits"] == bad
    assert result["format"] == "disabled"


@pytest.mark.parametrize("lane", [9, 10])
def test_extended_integrity_and_different_valid_identities(selector, lane):
    good = bits_for(extended=True)
    bad = stretched(good)
    corrupt = bits_for(extended=True, integrity_fault=lane)
    assert find_proflame_pcm_candidates(corrupt) == []
    result = run_cases(selector, [dict(mode=2, segments=[bad, corrupt])])[0]
    assert result["bits"] == bad and not result["replaced"]
    foreign = bits_for(extended=True, serial=0x654321)
    result = run_cases(selector, [dict(mode=2, segments=[bad, good, foreign])])[0]
    assert result["bits"] == bad and not result["replaced"]


@pytest.mark.parametrize("extended", [False, True])
@pytest.mark.parametrize("cmd1,cmd2", [(1, 0), (3, 0), (0, 6), (0x61, 0x66)])
def test_receive_ranges_include_manual_zero_thermostat_and_accessories(selector, extended, cmd1, cmd2):
    good = bits_for(extended=extended, cmd1=cmd1, cmd2=cmd2)
    result = run_cases(selector, [dict(mode=2 if extended else 1, segments=[stretched(good), good])])[0]
    assert result["replaced"] and result["bits"] == good


@pytest.mark.parametrize("extended", [False, True])
@pytest.mark.parametrize("cmd2", [7, 0x76])
def test_invalid_observed_state_does_not_rescue(selector, extended, cmd2):
    good = bits_for(extended=extended)
    candidate = bits_for(extended=extended, cmd2=cmd2)
    result = run_cases(selector, [dict(mode=2 if extended else 1, segments=[stretched(good), candidate])])[0]
    assert not result["replaced"]


def test_preservation_covers_noise_original_guards_and_cross_format_frames(selector):
    legacy = bits_for()
    extended = bits_for(extended=True)
    bad_nominal = legacy[:20] + ("1" if legacy[20] == "0" else "0") + legacy[21:]
    cases = [
        dict(mode=2, segments=[extended[:181], extended]),
        dict(segments=[bad_nominal, "10" * 7 + legacy]),
    ]
    for mode, good in ((1, legacy), (2, extended)):
        cases.extend(dict(mode=mode, segments=[prefix + good, good]) for prefix in ("0", "10", "10" * 7))
        cases.append(dict(mode=mode, segments=[good + "0", good]))
    for offset in range(80):
        if offset <= 60:
            original = ("10" * 40)[:offset] + legacy + "0" + "0" * (260 - offset - 182)
        elif offset == 79:
            original = ("10" * 40)[:offset] + legacy
        else:
            continue
        assert find_proflame_pcm_candidates(original)
        cases.append(dict(mode=2, segments=[original, extended]))
    results = run_cases(selector, cases)
    for case, result in zip(cases, results, strict=True):
        assert not result["replaced"]
        assert result["bits"] == max(case["segments"], key=len)


def fixture_rows():
    for name in ("issue_15_tmfsla_extended_capture.json", "issue_26_mendota_extended_capture.json"):
        for row in json.loads((ROOT / "tests/fixtures" / name).read_text())["captures"]:
            bits = "".join(f"{byte:08b}" for byte in bytes.fromhex(row["pcm_hex"]))[: row["pcm_bit_length"]]
            yield row, bits


def test_all_existing_extended_captures_remain_identical(selector):
    cases = [dict(mode=mode, segments=[bits]) for _, bits in fixture_rows() for mode in (0, 1, 2)]
    for case, result in zip(cases, run_cases(selector, cases), strict=True):
        assert result["bits"] == case["segments"][0] and not result["replaced"]


def test_every_short_low_in_accepted_extended_fixtures_can_use_an_independent_good_repeat(selector):
    cases = []
    for row, bits in fixture_rows():
        if row.get("expected_decode") == "rejected":
            continue
        for match in re.finditer("0+", bits):
            if len(match[0]) in (1, 2):
                cases.append(dict(mode=2, segments=[stretched(bits, match.end()), bits]))
    assert len(cases) == 11233
    for case, result in zip(cases, run_cases(selector, cases), strict=True):
        assert result["replaced"] and result["bits"] == case["segments"][1]


def test_invalid_single_bit_mutations_are_not_promoted(selector):
    cases = []
    for mode in (1, 2):
        good = bits_for(extended=mode == 2)
        for i, value in enumerate(good):
            mutation = good[:i] + ("1" if value == "0" else "0") + good[i + 1 :]
            cases.append(dict(mode=mode, segments=[stretched(good), mutation]))
    assert all(not result["replaced"] for result in run_cases(selector, cases))


def test_shared_word_reader_matches_existing_python_parser(selector):
    cases = []
    for _, bits in fixture_rows():
        for count in (7, 10):
            cases.extend((offset, count, bits) for offset in range(max(0, len(bits) - (count * 26 - 1)) + 2))
    rng = random.Random(417)
    for _ in range(1500):
        count = rng.choice((7, 10))
        length = rng.choice((0, 1, 180, 181, 182, 183, 258, 259, 260, 261, 300))
        cases.append((rng.randrange(5), count, "".join(rng.choice("01") for _ in range(length))))
    # Exercise reader bounds and unsupported counts through the C++ adapter.
    cases.extend([(0, 6, "1" * 182), (0, 11, "1" * 300), (65535, 10, "1" * 260), (0, 7, "1" * 1025)])
    source = "\n".join(f"{offset} {count} {bits or '-'}" for offset, count, bits in cases)
    output = subprocess.check_output([str(selector), "words"], input=source + "\n", text=True).splitlines()
    for (offset, count, bits), line in zip(cases, output, strict=True):
        fields = list(map(int, line.split()))
        actual = tuple(fields[1:]) if fields[0] else None
        expected = decode_pcm_words(bits, offset, count) if count in (7, 10) and len(bits) <= 1024 else None
        assert actual == expected


def packed_timings(bits, *, stretch_us=None):
    runs = [(int(bit), len(list(group)) * 417) for bit, group in groupby(bits)]
    if stretch_us is not None:
        index = next(i for i, (bit, duration) in enumerate(runs) if bit == 0 and duration == 417)
        runs[index] = (0, stretch_us)
    return runs


def pack_runs(runs):
    words = []
    for i in range(0, len(runs), 2):
        level, duration = runs[i]
        word = duration | (level << 15)
        if i + 1 < len(runs):
            level, duration = runs[i + 1]
            word |= (duration | (level << 15)) << 16
        words.append(word)
    return words


@pytest.mark.parametrize("extended", [False, True])
@pytest.mark.parametrize("low_us", [665, 667, 716])
def test_actual_receiver_quantization_and_rearm_recover_stretched_low(selector, extended, low_us):
    good = bits_for(extended=extended)
    bad_runs = packed_timings(good, stretch_us=low_us)
    words = pack_runs(bad_runs + [(0, 5417)] + packed_timings(good))
    assert len(words) <= 256
    case = dict(mode=2 if extended else 1, segments=words)
    result = run_cases(selector, [case], command="poll")[0]
    assert result["converted"] and result["replaced"] and result["bits"] == good
    assert result["skipped"] == 1 and result["symbols"] == len(words)
    baseline = run_cases(selector, [{**case, "active": False}], command="poll")[0]
    assert baseline["converted"] and len(baseline["bits"]) == len(good) + 1
    assert find_proflame_pcm_candidates(baseline["bits"]) == []
    failed = run_cases(selector, [case], command="rearm-failure")[0]
    assert not failed["converted"] and not failed["replaced"] and failed["skipped"] == 0


def test_actual_receiver_bounds_and_saturating_counter(selector):
    for values in ([], [0], [0] * 257, pack_runs(packed_timings("10" * 10))):
        result = run_cases(selector, [dict(segments=values)], command="convert")[0]
        assert not result["converted"] and result["skipped"] == 0
    pairs = [(0, 0), (0, 2), (10, 3), (2**32 - 2, 1), (2**32 - 2, 2), (2**32 - 1, 100)]
    output = subprocess.check_output(
        [str(selector), "counter"], input="".join(f"{total} {delta}\n" for total, delta in pairs), text=True
    )
    assert list(map(int, output.splitlines())) == [min(2**32 - 1, total + delta) for total, delta in pairs]
