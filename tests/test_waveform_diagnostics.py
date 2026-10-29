"""Raw export integrity, frame selection and sample-clock alignment."""

import json
import shutil
import subprocess
from itertools import groupby
from pathlib import Path

import pytest
from waveform_synthetic import power_fixture

from custom_components.proflame2.protocol.ecc import build_extended_integrity_words
from custom_components.proflame2.protocol.packet import ProflameFrame
from custom_components.proflame2.rf.pulse import find_proflame_pcm_candidates
from custom_components.proflame2.rf.waveform import frame_to_air_bytes
from tools.waveform_diagnostics import checksum, classify_edges, fit_sample_alignment, main, read_windows

ROOT = Path(__file__).resolve().parents[1]


def test_native_pilot_flame_zero_words_remain_available_for_waveform_review():
    # Pilot command/integrity bytes, with an anonymized ID and synthesized timing.
    # Raw acquisition must not apply the operational legacy flame-level policy.
    frame = ProflameFrame(serial_id=0x123456, cmd1=0x21, cmd2=0x00, err1=0x14, err2=0x18)
    bits = "".join(f"{byte:08b}" for byte in frame_to_air_bytes(frame))
    edges = [(1 if bit == "1" else -1) * sum(1 for _ in runs) * 417 for bit, runs in groupby(bits)]
    assert any(row["wire_words"] == list(frame.wire_words) for row in classify_edges(edges))


def record(words, *, saturated=0):
    yield (
        f"[I] PFRAW1 id=1 count={len(words)} saturated={saturated} poll_us=100000 "
        f"rearm_us=100100 resolution_hz=1000000 filter_ns=3000 idle_ns=30000000 checksum={checksum(words):08x}"
    )
    for offset in range(0, len(words), 16):
        yield f"PFRAW1 id=1 offset={offset} words=" + "".join(f"{word:08x}" for word in words[offset : offset + 16])
    yield "PFRAW1 id=1 end=1"


def test_round_trip_preserves_unrounded_edges_and_terminal_zero():
    words = [419 | (1 << 15) | (832 << 16), 427 | (1 << 15)]
    (window,) = read_windows(record(words))
    assert window.words == tuple(words)
    assert window.edges == (419, -832, 427)
    assert window.frames() == []


@pytest.mark.parametrize("suffix", [" count=2", " junk", " extra==1", " words=123z"])
def test_raw_fields_reject_ambiguous_or_malformed_text(suffix):
    lines = list(record([123]))
    lines[0] += suffix
    with pytest.raises(ValueError):
        read_windows(lines)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "corrupt", "no_end", "saturation", "resolution"])
def test_rejects_untrustworthy_exports(mutation):
    lines = list(record([0x1234] * 20))
    if mutation == "missing":
        del lines[1]
    elif mutation == "duplicate":
        lines.insert(2, lines[1])
    elif mutation == "corrupt":
        lines[1] = lines[1].replace("00001234", "00001235", 1)
    elif mutation == "no_end":
        lines.pop()
    elif mutation == "saturation":
        lines[0] = lines[0].replace("saturated=0", "saturated=2")
    else:
        lines[0] = lines[0].replace("resolution_hz=1000000", "resolution_hz=2000000")
    with pytest.raises(ValueError):
        read_windows(lines)


def test_saturated_window_is_retained_but_flagged():
    (window,) = read_windows(record([123], saturated=1))
    assert window.metadata["saturated"] == 1


def record_v2():
    lines = [line.replace("PFRAW1", "PFRAW2") for line in record([123])]
    lines[0] = lines[0].replace("id=1", "session=42 id=1").replace("rearm_us=100100", "rearm_us=90000")
    lines.insert(
        1,
        "PFRAW2 id=1 arm_us=90000 callback_us=95000 seal_us=100100 gap_us=0 callbacks=1 queue_losses=0 ignored_callbacks=0 ignored_symbols=0 final=1 timeout=0",
    )
    return lines


def test_v2_retains_capture_health_and_session_identity():
    (window,) = read_windows(record_v2())
    assert window.metadata["session"] == 42
    assert window.metadata["callback_us"] == 95000
    assert window.metadata["schema_version"] == 2
    with pytest.raises(ValueError, match="duplicate"):
        read_windows([*record_v2(), *record_v2()])


def pretrigger_record():
    history = [line.replace("PFRAW2", "PFHIST1") for line in record_v2()]
    current = [line.replace("id=1", "id=2").replace("90000", "110000") for line in record_v2()]
    current[0] = current[0].replace("poll_us=100000", "poll_us=120000")
    current[1] = (
        current[1].replace("callback_us=95000", "callback_us=115000").replace("seal_us=100100", "seal_us=120100")
    )
    return [
        "PFHIST1 parent=2 source_offset=10",
        *history,
        "PFPRE1 session=42 id=2 requested_us=75000 duration_us=123 segments=1 short=1 limited=0",
        *current,
    ]


def test_pretrigger_is_separate_from_main_edges_and_keeps_source_offset():
    (window,) = read_windows(pretrigger_record())
    assert window.edges == (-123,)
    assert window.pretrigger_info["duration_us"] == 123
    assert window.pretrigger_info["short"] == 1
    assert window.pretrigger[0].metadata["source_offset"] == 10
    assert window.pretrigger[0].metadata["id"] == 1
    assert window.pretrigger[0].edges == (-123,)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("parent=2", "parent=3"),
        ("source_offset=10", "source_offset=4096"),
        ("source_offset=10", "source_offset=-1"),
        ("requested_us=75000", "requested_us=70000"),
        ("duration_us=123", "duration_us=124"),
        ("segments=1", "segments=0"),
        ("short=1", "short=0"),
        ("PFHIST1 session=42", "PFHIST1 session=43"),
        ("PFHIST1 id=1 offset=0", "PFHIST1 id=1 offset=1"),
        ("PFHIST1 id=1 end=1", "PFHIST1 id=1 end=0"),
        ("PFHIST1 id=1 arm_us=90000", "PFHIST1 id=1 arm_us=96000"),
        ("seal_us=100100", "seal_us=111000"),
    ],
)
def test_pretrigger_rejects_corruption_and_inconsistent_provenance(before, after):
    with pytest.raises(ValueError):
        read_windows([line.replace(before, after) for line in pretrigger_record()])


def session_record():
    return [
        "PFSESSION session=42 state=armed duration_ms=30000",
        *record_v2(),
        "PFSESSION session=42 state=paused exports=1 suppressed=3 end_us=101000",
    ]


@pytest.mark.parametrize("state", ["paused", "aborted"])
def test_complete_sessions_validate_and_allow_suppressed_windows(state):
    lines = [line.replace("state=paused", f"state={state}") for line in session_record()]
    assert len(read_windows(lines, require_complete_sessions=True)) == 1


@pytest.mark.parametrize(
    "mutation", ["start", "closure", "whole_window", "count", "failed", "duplicate", "outside", "malformed", "empty"]
)
def test_complete_sessions_detect_whole_record_loss_and_bad_lifecycle(mutation):
    lines = session_record()
    if mutation == "start":
        lines.pop(0)
    elif mutation == "closure":
        lines.pop()
    elif mutation == "whole_window":
        lines = [lines[0], lines[-1]]
    elif mutation == "count":
        lines[-1] = lines[-1].replace("exports=1", "exports=2")
    elif mutation == "failed":
        lines[-1] = "PFSESSION session=42 state=failed code=17"
    elif mutation == "duplicate":
        lines.append(lines[-1])
    elif mutation == "outside":
        lines = [*record_v2(), lines[0], lines[-1]]
    elif mutation == "malformed":
        lines[0] += " junk"
    else:
        lines = []
    with pytest.raises(ValueError):
        read_windows(lines, require_complete_sessions=True)


def test_complete_empty_session_is_distinct_from_missing_log():
    lines = [session_record()[0], "PFSESSION session=42 state=paused exports=0 suppressed=3 end_us=101000"]
    assert read_windows(lines, require_complete_sessions=True) == []


@pytest.mark.parametrize("duration", [90000, 120000, 120001])
def test_quiet_capture_budget_matches_firmware_schema(duration):
    lines = [
        f"PFSESSION session=42 state=armed duration_ms={duration}",
        "PFSESSION session=42 state=paused exports=0 suppressed=0 end_us=200000000",
    ]
    if duration > 120000:
        with pytest.raises(ValueError, match="invalid session start"):
            read_windows(lines, require_complete_sessions=True)
    else:
        assert read_windows(lines, require_complete_sessions=True) == []


def test_synthetic_power_exports_are_complete_and_decode_without_losing_raw_context():
    fixture = power_fixture()
    session = fixture["session"]
    lines = [f"PFSESSION session={session['id']} state=armed duration_ms={session['duration_ms']}"]
    for capture in fixture["captures"]:
        identity = capture["id"]
        lines.append(
            f"PFRAW2 session={session['id']} id={identity} count={capture['count']} saturated=0 "
            f"poll_us={capture['poll_us']} rearm_us={capture['arm_us']} resolution_hz=1000000 "
            f"filter_ns=0 idle_ns=30000000 checksum={capture['checksum']}"
        )
        lines.append(
            f"PFRAW2 id={identity} arm_us={capture['arm_us']} callback_us={capture['callback_us']} "
            f"seal_us={capture['seal_us']} gap_us={capture['gap_us']} callbacks=1 queue_losses=0 "
            "ignored_callbacks=0 ignored_symbols=0 final=1 timeout=0"
        )
        for index, chunk in enumerate(capture["chunks"]):
            lines.append(f"PFRAW2 id={identity} offset={index * 16} words={chunk}")
        lines.append(f"PFRAW2 id={identity} end=1")
    lines.append(
        f"PFSESSION session={session['id']} state={session['state']} exports={session['exports']} "
        f"suppressed={session['suppressed']} end_us={session['end_us']}"
    )
    windows = read_windows(lines, require_complete_sessions=True)
    assert [len(window.words) for window in windows] == [capture["count"] for capture in fixture["captures"]]
    expected = ([18, 52, 86, 1, 6, 118, 222], [18, 52, 86, 0, 6, 87, 222])
    for window, words in zip(windows, expected, strict=True):
        frames = window.frames()
        assert frames
        assert all(frame["wire_words"] == words for frame in frames)
        assert frames[0]["edge_start"] > 0  # Earlier measured context is retained.
        assert len(window.words) == window.metadata["count"]
        assert checksum(window.words) == window.metadata["checksum"]


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "arm", "order", "flags", "version", "late"])
def test_v2_health_is_required_and_consistent(mutation):
    lines = record_v2()
    if mutation == "missing":
        del lines[1]
    elif mutation == "duplicate":
        lines.insert(2, lines[1])
    elif mutation == "arm":
        lines[1] = lines[1].replace("arm_us=90000", "arm_us=91000")
    elif mutation == "order":
        lines[1] = lines[1].replace("seal_us=100100", "seal_us=99000")
    elif mutation == "flags":
        lines[1] = lines[1].replace("final=1", "final=3")
    elif mutation == "version":
        lines[-1] = lines[-1].replace("PFRAW2", "PFRAW1")
    else:
        lines[1], lines[2] = lines[2], lines[1]
    with pytest.raises(ValueError):
        read_windows(lines)


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("include", [False, True])
def test_import_cli_retains_original_words_and_explicit_timestamp_limits(tmp_path, monkeypatch, version, include):
    source = tmp_path / "receiver.log"
    source.write_text("\n".join(record_v2() if version == 2 else record([123])))
    output = tmp_path / "selected.json"
    monkeypatch.setattr(
        "sys.argv", ["import", str(source), "--output", str(output), *(["--include-rejected"] if include else [])]
    )
    main()
    saved = json.loads(output.read_text())
    assert len(saved["windows"]) == int(include)
    if include:
        assert saved["windows"][0]["words"] == [123]
        assert saved["windows"][0]["complete_burst"] is False
    with pytest.raises(FileExistsError):
        main()


@pytest.mark.parametrize("extended", [False, True])
@pytest.mark.parametrize("repeats", [1, 5])
def test_selects_frame_without_modifying_measured_pulses(extended, repeats):
    if extended:
        integrity = build_extended_integrity_words(0x123456, 0x81, 6, 0x15, 0xC8, 0)
        frame = ProflameFrame(
            serial_id=0x123456, cmd1=0x81, cmd2=6, err1=0x15, err2=0xC8, extension_words=(0, *integrity)
        )
    else:
        frame = ProflameFrame(serial_id=0x123456, cmd1=1, cmd2=6, err1=0x76, err2=0xDE)
    length = 260 if extended else 182
    bits = ("".join(f"{byte:08b}" for byte in frame_to_air_bytes(frame))[:length] + "0" * 20) * repeats
    runs = []
    for bit in bits:
        if runs and (runs[-1] > 0) == (bit == "1"):
            runs[-1] += 417 if bit == "1" else -417
        else:
            runs.append(417 if bit == "1" else -417)
    words = []
    for index in range(0, len(runs), 2):
        first = runs[index]
        second = runs[index + 1] if index + 1 < len(runs) else 0
        words.append(abs(first) | ((first > 0) << 15) | (abs(second) << 16) | ((second > 0) << 31))
    (window,) = read_windows(record(words))
    frames = window.frames()
    assert len(frames) == repeats
    assert all(item["wire_words"] == list(frame.wire_words) for item in frames)
    assert frames[0]["wire_words"] == list(frame.wire_words)
    assert window.edges == tuple(runs)
    assert frames[0]["edge_end"] <= len(runs)


def test_alignment_recovers_offset_and_clock_drift():
    anchors = [(1_000_000 + i * 1_000_000, 800 + (1_000_000 + i * 1_000_000) * 0.250025) for i in range(5)]
    fit = fit_sample_alignment(anchors)
    assert fit["samples_per_us"] == pytest.approx(0.250025)
    assert fit["sample_offset"] == pytest.approx(800)
    assert fit["max_residual_samples"] < 1e-6


@pytest.mark.parametrize("anchors", [[], [(0, 0)], [(1, 0), (1, 2)], [(0, 2), (1, 0)], [(0, 0), (float("nan"), 1)]])
def test_alignment_rejects_invalid_anchors(anchors):
    with pytest.raises(ValueError):
        fit_sample_alignment(anchors)


def test_raw_cpp_helpers_and_production_exclusion(tmp_path):
    compiler = shutil.which("g++")
    assert compiler
    source = tmp_path / "raw.cpp"
    source.write_text("""#include "raw_ook_window.h"
#ifdef USE_PROFLAME2_WAVEFORM_DIAGNOSTICS
using esphome::proflame2_tembed::RawOokWindow;
RawOokWindow diagnostic_window;
int main() {
 diagnostic_window.words[0] = RawOokWindow::pack(419, true, 832, false);
 diagnostic_window.count = 1;
 return diagnostic_window.words[0] == 0x034081A3 && diagnostic_window.checksum() != 0 ? 0 : 1;
}
#else
int main() { return 0; }
#endif
""")
    for enabled in (False, True):
        binary = tmp_path / str(enabled)
        subprocess.run(
            [
                compiler,
                "-std=c++17",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-I",
                str(ROOT / "esphome/components/proflame2_tembed"),
                *(["-DUSE_PROFLAME2_WAVEFORM_DIAGNOSTICS"] if enabled else []),
                str(source),
                "-o",
                str(binary),
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run([str(binary)], check=True)
        symbols = subprocess.run(["nm", "-C", str(binary)], capture_output=True, text=True, check=True).stdout
        assert ("diagnostic_window" in symbols) is enabled


def test_cpp_pretrigger_history_bounds_and_segment_provenance(tmp_path):
    source = tmp_path / "history.cpp"
    source.write_text("""#include <cassert>
#include "raw_ook_window.h"
using namespace esphome::proflame2_tembed;
RawOokWindow window;
RawOokHistory history;
int main() {
 window.count = 100; window.sequence = 1; window.arm_us = 123;
 for (auto& word : window.words) word = RawOokWindow::pack(500, true, 500, false);
 const auto original = window.checksum();
 history.append(window);
 assert(history.count == 75 && history.duration_us == 75000 && !history.limited);
 assert(history.segment_count == 1 && history.segments[0].source_offset == 25);
 assert(history.segments[0].arm_us == 123 && original == window.checksum());
 window.count = 10; window.sequence = 2;
 history.append(window);
 assert(history.count == 75 && history.segment_count == 2);
 assert(history.segments[0].source_offset == 35 && history.segments[0].count == 65);
 assert(history.segments[1].count == 10 && history.segments[1].sequence == 2);
 window.count = 0; history.append(window);
 assert(history.count == 75);
 window.count = 2;
 window.words[0] = window.words[1] = RawOokWindow::pack(32767, true, 32767, false);
 history.append(window);
 assert(history.count == 2 && history.duration_us == 131068 && history.segment_count == 1);
 window.count = RawOokWindow::CAPACITY;
 for (auto& word : window.words) word = RawOokWindow::pack(1, true, 1, false);
 history.append(window);
 assert(history.count == 4096 && history.duration_us == 8192 && history.segment_count == 1 && history.limited);
 window.count = 1; window.words[0] = RawOokWindow::pack(500, true, 500, false);
 for (unsigned i = 0; i < 40; i++) { window.sequence = i; history.append(window); }
 assert(history.segment_count == 32 && history.count == 32 && history.duration_us == 32000);
 assert(history.segments[0].sequence == 8 && history.segments[31].sequence == 39);
 window.count = 4096;
 for (auto& word : window.words) word = 0;
 history.append(window);
 assert(history.count == 4096 && history.duration_us == 0 && history.segment_count == 1);
}
""")
    binary = tmp_path / "history"
    subprocess.run(
        [
            shutil.which("g++"),
            "-std=c++17",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-DUSE_PROFLAME2_WAVEFORM_DIAGNOSTICS",
            "-I",
            str(ROOT / "esphome/components/proflame2_tembed"),
            str(source),
            "-o",
            str(binary),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run([str(binary)], check=True)


def test_firmware_prefix_filter_accepts_known_remotes_and_preserves_context(tmp_path):
    """Run the firmware screen against every archived raw row, not a Python copy."""
    rows = []
    for filename in ("issue_15_tmfsla_extended_capture.json", "issue_26_mendota_extended_capture.json"):
        captures = json.loads((ROOT / "tests/fixtures" / filename).read_text())["captures"]
        for row in captures:
            bits = "".join(f"{byte:08b}" for byte in bytes.fromhex(row["pcm_hex"]))[: row["pcm_bit_length"]]
            # These archived rows contain malformed symbols/parity in the prefix,
            # independently rejected by the existing host decoder as well.
            intact = filename != "issue_15_tmfsla_extended_capture.json" or row["label"] not in {"A06", "C03", "D04"}
            if not intact:
                assert find_proflame_pcm_candidates(bits) == []
            rows.append((bits, intact))
    legacy = json.loads((ROOT / "tests/fixtures/real_remote_legacy_captures.json").read_text())
    for remote in legacy["remotes"]:
        for words in remote["words"]:
            frame = ProflameFrame(
                serial_id=int(remote["serial_id"], 16), cmd1=words[3], cmd2=words[4], err1=words[5], err2=words[6]
            )
            rows.append(("".join(f"{byte:08b}" for byte in frame_to_air_bytes(frame))[:181], True))
    source = tmp_path / "prefix.cpp"
    source.write_text("""#include <iostream>
#include "raw_ook_window.h"
using esphome::proflame2_tembed::RawOokWindow;
RawOokWindow window;
int main() {
 std::cin >> window.count;
 for (size_t i=0; i<window.count; i++) std::cin >> std::hex >> window.words[i];
 const auto before = window.checksum();
 const bool selected = window.has_frame_prefix();
 if (before != window.checksum()) return 2;
 std::cout << selected;
}
""")
    binary = tmp_path / "prefix"
    subprocess.run(
        [
            shutil.which("g++"),
            "-std=c++17",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-DUSE_PROFLAME2_WAVEFORM_DIAGNOSTICS",
            "-I",
            str(ROOT / "esphome/components/proflame2_tembed"),
            str(source),
            "-o",
            str(binary),
        ],
        check=True,
        capture_output=True,
    )

    def selected(bits):
        runs = []
        for bit in bits:
            if runs and runs[-1][0] == bit:
                runs[-1][1] += 417
            else:
                runs.append([bit, 417])
        # Preserve measured noise before, and a malformed partial repeat after.
        words = [17 | (1 << 15) | (23 << 16)]
        for index in range(0, len(runs), 2):
            first, duration = runs[index]
            second, duration2 = runs[index + 1] if index + 1 < len(runs) else ("0", 0)
            words.append(duration | ((first == "1") << 15) | (duration2 << 16) | ((second == "1") << 31))
        result = subprocess.run(
            [str(binary)],
            input=f"{len(words)} " + " ".join(f"{word:x}" for word in words),
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout == "1"

    for bits, intact in rows:
        assert selected(bits) is intact
        assert selected(bits + "0" * 40 + bits[:100]) is intact
    assert not selected("10" * 1000)
    assert not selected("1" * 20)
    assert not selected(rows[0][0][:100])
    raw_fixture = power_fixture()
    for capture in raw_fixture["captures"]:
        packed = "".join(capture["chunks"])
        words = [packed[index : index + 8] for index in range(0, len(packed), 8)]
        result = subprocess.run(
            [str(binary)], input=f"{len(words)} " + " ".join(words), capture_output=True, text=True, check=True
        )
        assert result.stdout == "1"


def _write_receiver_stubs(tmp_path):
    headers = {
        "driver/rmt_rx.h": """#pragma once
#include <cstddef>
#include <cstdint>
using esp_err_t = int;
using rmt_channel_handle_t = void*;
using gpio_num_t = int;
constexpr int RMT_CLK_SRC_DEFAULT = 0;
struct rmt_symbol_word_t { unsigned duration0:15; unsigned level0:1; unsigned duration1:15; unsigned level1:1; };
struct rmt_rx_done_event_data_t { rmt_symbol_word_t* received_symbols; size_t num_symbols; struct { bool is_last; } flags; };
struct rmt_rx_channel_config_t {
 gpio_num_t gpio_num; int clk_src; uint32_t resolution_hz; size_t mem_block_symbols; int intr_priority;
 struct { bool invert_in, with_dma, allow_pd, io_loop_back; } flags;
};
struct rmt_rx_event_callbacks_t { bool (*on_recv_done)(rmt_channel_handle_t, const rmt_rx_done_event_data_t*, void*); };
struct rmt_receive_config_t { uint32_t signal_range_min_ns, signal_range_max_ns; struct { bool en_partial_rx; } flags; };
inline size_t dma_capacity = 0;
inline int fault = 0;
inline int allocations = 0;
inline bool driver_enabled = false;
inline int receive_calls = 0;
inline bool fail_rearm = false;
inline int rmt_new_rx_channel(const rmt_rx_channel_config_t* cfg, void** c) { if (fault == 3) return 9; dma_capacity = cfg->flags.with_dma ? cfg->mem_block_symbols : 0; *c = (void*)1; return 0; }
inline int rmt_rx_register_event_callbacks(void*, const rmt_rx_event_callbacks_t*, void*) { return fault == 5 ? 9 : 0; }
inline int rmt_enable(void*) { if (fault == 6) return 9; driver_enabled = true; return 0; }
inline int rmt_disable(void*) { if (fault == 7) return 9; driver_enabled = false; return 0; }
inline int rmt_del_channel(void*) { return fault == 8 ? 9 : 0; }
inline int rmt_receive(void*, void*, size_t bytes, const rmt_receive_config_t*) {
 if (dma_capacity && bytes > dma_capacity * sizeof(rmt_symbol_word_t)) return 9;
 receive_calls++; return fail_rearm && receive_calls > 1 ? 17 : 0;
}
""",
        "freertos/FreeRTOS.h": """#pragma once
using BaseType_t = int;
constexpr int pdFALSE = 0, pdTRUE = 1;
using portMUX_TYPE = int;
#define portMUX_INITIALIZER_UNLOCKED 0
inline void (*critical_hook)() = nullptr;
inline void portENTER_CRITICAL(int*) { if (critical_hook) critical_hook(); }
inline void portEXIT_CRITICAL(int*) {}
inline void portENTER_CRITICAL_ISR(int*) {}
inline void portEXIT_CRITICAL_ISR(int*) {}
#define portNUM_PROCESSORS 2
""",
        "freertos/queue.h": """#pragma once
#include <cstring>
#include "FreeRTOS.h"
using QueueHandle_t = void*;
inline char queued[128];
inline size_t queue_size;
inline bool pending = false;
inline bool queue_fail = false;
inline void* xQueueCreate(int, size_t size) { queue_size = size; return fault == 4 ? nullptr : queued; }
inline int xQueueSendFromISR(void*, const void* event, int*) { if (queue_fail) return 0; std::memcpy(queued, event, queue_size); pending = true; return 1; }
inline int xQueueReceive(void*, void* event, int) { if (!pending) return 0; pending=false; std::memcpy(event, queued, queue_size); return 1; }
inline void vQueueDelete(void*) {}
""",
        "esphome/core/defines.h": "#pragma once\n",
        "esp_err.h": """#pragma once
constexpr int ESP_OK = 0;
constexpr int ESP_ERR_INVALID_STATE = 3;
inline const char* esp_err_to_name(int) { return "test_error"; }
""",
        "esp_timer.h": """#pragma once
#include <cstdint>
inline int64_t mock_time = 100000;
inline int64_t esp_timer_get_time() { return mock_time; }
""",
        "esp_heap_caps.h": """#pragma once
#include <cstdlib>
#include "driver/rmt_rx.h"
constexpr int MALLOC_CAP_INTERNAL = 1, MALLOC_CAP_DMA = 2, MALLOC_CAP_8BIT = 4, MALLOC_CAP_SPIRAM = 8;
struct multi_heap_info_t { size_t total_free_bytes, largest_free_block, minimum_free_bytes; };
inline void heap_caps_get_info(multi_heap_info_t* info, int caps) {
 if (caps & MALLOC_CAP_SPIRAM) { *info = {0,0,0}; return; }
 *info = {100000 - static_cast<size_t>(allocations) * 1000, 80000, 70000};
}
inline void* heap_caps_calloc(size_t n, size_t size, int) { if (fault == 2) return nullptr; allocations++; return std::calloc(n, size); }
inline void* heap_caps_aligned_calloc(size_t alignment, size_t n, size_t size, int) {
 if (fault == 1) return nullptr;
 void* p = nullptr; if (posix_memalign(&p, alignment, n * size)) return nullptr; allocations++; return p;
}
inline void heap_caps_free(void* p) { if (p) allocations--; std::free(p); }
""",
        "esphome/core/log.h": """#pragma once
#include <cstdio>
#define ESP_LOGI(tag, fmt, ...) do { printf(fmt, __VA_ARGS__); printf("\\n"); } while (0)
#define ESP_LOGE(tag, fmt, ...) ESP_LOGI(tag, fmt, __VA_ARGS__)
""",
        "esp_attr.h": "#pragma once\n#define IRAM_ATTR\n",
        "esp_random.h": "#pragma once\ninline unsigned esp_random() { return 42; }\n",
        "driver/gpio.h": """#pragma once
#include "esp_err.h"
using gpio_num_t = int;
#define GPIO_IS_VALID_GPIO(pin) ((pin) >= 0 && (pin) < 49)
inline int gpio_fault = 0, ipc_fault = -1, quiesced = 0, gpio_level = 0;
inline void (*gpio_handler)(void*) = nullptr;
inline void* gpio_argument = nullptr;
inline bool gpio_enabled = false;
inline int gpio_get_level(int) { return gpio_level; }
inline int gpio_intr_disable(int) { if (gpio_fault == 2) return 9; gpio_enabled=false; return 0; }
inline int gpio_isr_handler_add(int, void (*fn)(void*), void* arg) {
 if (gpio_fault == 1) { return 9; } gpio_handler=fn; gpio_argument=arg; gpio_enabled=true; return 0;
}
inline int gpio_isr_handler_remove(int) {
 if (gpio_fault == 3) { return 9; } gpio_handler=nullptr; gpio_argument=nullptr; return 0;
}
""",
        "esphome/core/gpio.h": """#pragma once
#include "driver/gpio.h"
namespace esphome {
namespace gpio { constexpr int INTERRUPT_ANY_EDGE = 3; }
class InternalGPIOPin {
 public:
 int get_pin() const { return 3; }
 template<typename T> void attach_interrupt(void (*fn)(T*), T* arg, int) const {
   gpio_isr_handler_add(3,reinterpret_cast<void (*)(void*)>(fn),arg);
 }
};
}
""",
        "hal/gpio_ll.h": """#pragma once
#include "driver/gpio.h"
inline int gpio_ll_get_level(void*, int) { return gpio_level; }
""",
        "soc/gpio_struct.h": "#pragma once\ninline int GPIO = 0;\n",
        "esp_ipc.h": """#pragma once
#include "driver/gpio.h"
inline int esp_ipc_call_blocking(int core, void (*fn)(void*), void* arg) {
 if (core == ipc_fault) { return 9; } quiesced |= (1 << core); fn(arg); return 0;
}
""",
    }
    for filename, content in headers.items():
        path = tmp_path / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def test_actual_rmt_receiver_compiles_and_exports_with_driver_stub(tmp_path):
    """Exercise both real receiver compile paths, not just a copied helper."""
    _write_receiver_stubs(tmp_path)
    source = tmp_path / "receiver_test.cpp"
    source.write_text("""#include <array>
#include <string>
#include "esp_timer.h"
#define private public
#include "rmt_ook_receiver.h"
#undef private
using namespace esphome::proflame2_tembed;
RmtOokReceiver receiver;
#ifdef USE_PROFLAME2_GPIO_EDGE_DIAGNOSTICS
GPIOEdgeObserver::Ring* overwrite_ring = nullptr;
int lock_entries = 0;
void overwrite_on_copy() {
 if (++lock_entries == 2) { overwrite_ring->total += GPIOEdgeObserver::CAPACITY; critical_hook = nullptr; }
}
#endif
int main(int argc, char** argv) {
 std::string error;
 const std::string mode = argc > 1 ? argv[1] : "normal";
#ifdef USE_PROFLAME2_GPIO_EDGE_DIAGNOSTICS
 esphome::InternalGPIOPin input;
 receiver.set_gpio_edge_observer_pin(&input);
 if (mode == "observer") {
   GPIOEdgeObserver observer;
   if (!observer.begin(&input, error) || !gpio_handler || allocations != 1) return 101;
   mock_time += 417; gpio_level=1; gpio_handler(gpio_argument);
   mock_time += 417; gpio_level=0; gpio_handler(gpio_argument);
   observer.snapshot(100000);
   if (observer.context_->saved.count != 2 || observer.context_->saved.edges[0] != 200835) return 102;
   // Export is frozen, but IRQ collection continues while RMT is unarmed.
   mock_time += 417; gpio_level=1; gpio_handler(gpio_argument);
   if (observer.context_->saved.count != 2 || observer.context_->ring.total != 3) return 103;
   gpio_handler(gpio_argument);
   if (observer.context_->ring.repeated_levels != 1) return 116;
   while (!observer.export_snapshot(42,1)) {}
   gpio_fault=2;
   if (observer.end(error) || allocations != 1) return 104;
   gpio_fault=3;
   if (observer.end(error) || allocations != 1) return 105;
   gpio_fault=0; ipc_fault=1;
   if (observer.end(error) || allocations != 1 || gpio_handler) return 106;
   ipc_fault=-1;
   if (!observer.end(error) || allocations || quiesced != 3) return 107;
   gpio_fault=1;
   if (observer.begin(&input,error)) return 108;
   gpio_fault=0;
   if (!observer.end(error) || allocations) return 109;
   fault=2;
   if (observer.begin(&input,error) || allocations) return 110;
   fault=0;
   if (!observer.begin(&input,error)) return 111;
   auto& ring = observer.context_->ring;
   for (unsigned i=0; i<2050; i++) {
     mock_time+=417; gpio_level=!gpio_level; gpio_handler(gpio_argument);
   }
   observer.snapshot(100000);
   if (observer.context_->saved.count != 2048 || !observer.context_->saved.clipped || ring.storm) return 112;
   overwrite_ring = &ring; lock_entries = 0; critical_hook = &overwrite_on_copy;
   observer.snapshot(100000);
   if (!observer.context_->saved.raced || observer.context_->saved.count) return 117;
   for (unsigned i=0; i<40; i++) { mock_time++; gpio_level=!gpio_level; if (gpio_enabled) gpio_handler(gpio_argument); }
   if (!ring.storm || ring.active || gpio_enabled) return 113;
   auto total=ring.total;
   gpio_handler(gpio_argument);
   if (ring.total != total) return 114;
   if (!observer.end(error) || allocations) return 115;
   return 0;
 }
#endif
#ifdef USE_PROFLAME2_WAVEFORM_DIAGNOSTICS
 RawCaptureLimits limits;
 limits.max_exports = mode == "noise_budget" ? 32 : mode == "rearm_fail" ? 2 : 1;
 limits.capture_rejected = true;
 receiver.set_diagnostic_limits(limits);
 if (mode.rfind("begin_fail_", 0) == 0) {
   fault = mode.back() - '0';
   if (receiver.begin(3, error) || error.empty() || receiver.active() || allocations != 0) return 11;
   return 0;
 }
#endif
 if (!receiver.begin(3, error)) return 1;
#ifdef USE_PROFLAME2_WAVEFORM_DIAGNOSTICS
 if (dma_capacity != 4096) return 21;
#else
 if (dma_capacity != 0) return 22;
#endif
#ifdef USE_PROFLAME2_WAVEFORM_DIAGNOSTICS
 if (mode == "quiet") {
   mock_time += 30000001;
   RmtOokCapture empty;
   if (receiver.poll(empty, error)) return 17;
   receiver.export_raw_window();
   if (receiver.diagnostic_state_ != RawCaptureState::PAUSED || driver_enabled) return 18;
   receiver.end();
   return allocations == 0 ? 0 : 19;
 }
 if (mode == "history") {
   limits.capture_rejected = false;
   receiver.set_diagnostic_limits(limits);
   for (unsigned sample=0; sample<3; sample++) {
     for (unsigned i=0; i<40; i++) receiver.symbol_buffer_()[i] = {417, 1, 417, 0};
     mock_time += 40000;
     rmt_rx_done_event_data_t previous{receiver.symbol_buffer_(), 40, {true}};
     receiver.on_diagnostic_receive_done_(nullptr, &previous, receiver.diagnostic_context_);
     RmtOokCapture ignored;
     receiver.poll(ignored, error);
     receiver.export_raw_window();
   }
   if (receiver.diagnostic_suppressed_ != 3 || receive_calls != 4) return 23;
   limits.capture_rejected = true;
   receiver.set_diagnostic_limits(limits);
 }
#endif
 const unsigned count = mode == "saturation" ? 4096 : 40;
 for (unsigned i=0; i<count; i++) receiver.symbol_buffer_()[i] = {417, 1, 417, 0};
 rmt_rx_done_event_data_t event{receiver.symbol_buffer_(), count, {true}};
#ifdef USE_PROFLAME2_WAVEFORM_DIAGNOSTICS
 queue_fail = mode == "queue_loss";
 event.num_symbols = mode == "saturation" ? count : 20;
 event.flags.is_last = false;
 receiver.on_diagnostic_receive_done_(nullptr, &event, receiver.diagnostic_context_);
 if (mode == "timeout") {
   mock_time += 30000001;
 } else if (mode != "saturation") {
   event.received_symbols += 20;
   event.flags.is_last = true;
   receiver.on_diagnostic_receive_done_(nullptr, &event, receiver.diagnostic_context_);
 }
 if (mode != "timeout") {
   // Late callbacks cannot mutate a sealed window.
   event.received_symbols[0].duration0 = 42;
   receiver.on_diagnostic_receive_done_(nullptr, &event, receiver.diagnostic_context_);
 }
#else
 receiver.on_receive_done_(nullptr, &event, receiver.queue_);
#endif
 RmtOokCapture capture;
#ifdef USE_PROFLAME2_WAVEFORM_DIAGNOSTICS
 if (mode == "stop_fail") fault = 7;
#endif
 const bool converted = receiver.poll(capture, error);
#ifdef USE_PROFLAME2_WAVEFORM_DIAGNOSTICS
 if (mode == "stop_fail") {
   const int expected_allocations =
#ifdef USE_PROFLAME2_GPIO_EDGE_DIAGNOSTICS
     3;
#else
     2;
#endif
   if (converted || receiver.diagnostic_state_ != RawCaptureState::FAILED || allocations != expected_allocations) return 12;
   receiver.end();
   if (!receiver.active() || allocations != 2) return 13;
   fault = 0;
   receiver.end();
   return allocations == 0 ? 0 : 14;
 }
#endif
 if (!converted && mode != "timeout") return 2;
#ifdef USE_PROFLAME2_WAVEFORM_DIAGNOSTICS
 if (driver_enabled) return 3;
 fail_rearm = mode == "rearm_fail";
 receiver.export_raw_window();
 if (receiver.diagnostic_state_ != RawCaptureState::EXPORTING || driver_enabled || receive_calls != (mode == "history" ? 4 : 1)) return 7;
 if (mode == "abort_export") {
   receiver.end();
   return 0;
 }
 for (unsigned pass=0; pass<1024 && receiver.diagnostic_state_ == RawCaptureState::EXPORTING; pass++)
   receiver.export_raw_window();
 if (mode == "noise_budget") {
   for (unsigned sample=0; sample<5; sample++) {
     event.received_symbols = receiver.symbol_buffer_();
     event.num_symbols = 40;
     event.flags.is_last = true;
     receiver.on_diagnostic_receive_done_(nullptr, &event, receiver.diagnostic_context_);
     receiver.poll(capture, error);
     receiver.export_raw_window();
     for (unsigned pass=0; pass<1024 && receiver.diagnostic_state_ == RawCaptureState::EXPORTING; pass++)
       receiver.export_raw_window();
   }
   mock_time += 30000001;
   receiver.poll(capture, error);
   receiver.export_raw_window();
   if (receiver.diagnostic_exports_ != 4 || receiver.diagnostic_suppressed_ != 3) return 20;
 }
 if (mode == "rearm_fail") {
   if (receiver.diagnostic_state_ != RawCaptureState::FAILED) return 4;
 } else if (receiver.diagnostic_state_ != RawCaptureState::PAUSED) return 5;
 if (driver_enabled && mode != "rearm_fail") return 6;
#endif
#ifdef USE_PROFLAME2_WAVEFORM_DIAGNOSTICS
 if (mode == "delete_fail") {
   fault = 8;
   receiver.end();
   if (!receiver.active() || allocations != 2) return 15;
   fault = 0;
 }
#endif
 receiver.end();
 if (allocations != 0) return 16;
 return 0;
}
""")
    component = ROOT / "esphome/components/proflame2_tembed"
    compiler = shutil.which("g++")
    assert compiler
    for enabled, gpio_observer in ((False, False), (True, False), (True, True)):
        binary = tmp_path / f"receiver-{enabled}-{gpio_observer}"
        compiled = subprocess.run(
            [
                compiler,
                "-std=c++17",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-I",
                str(tmp_path),
                "-I",
                str(component),
                *(["-DUSE_PROFLAME2_WAVEFORM_DIAGNOSTICS"] if enabled else []),
                *(["-DUSE_PROFLAME2_GPIO_EDGE_DIAGNOSTICS"] if gpio_observer else []),
                str(source),
                str(component / "rmt_ook_receiver.cpp"),
                str(component / "gpio_edge_observer.cpp"),
                str(component / "buffered_ook_capture.cpp"),
                "-o",
                str(binary),
            ],
            capture_output=True,
            text=True,
        )
        assert compiled.returncode == 0, compiled.stdout + compiled.stderr
        for mode in (
            [
                "normal",
                "queue_loss",
                "timeout",
                "saturation",
                "rearm_fail",
                "abort_export",
                "stop_fail",
                "delete_fail",
                "quiet",
                "noise_budget",
                "history",
            ]
            + [f"begin_fail_{number}" for number in range(1, 7)]
            if enabled
            else ["normal"]
        ):
            result = subprocess.run([str(binary), mode], capture_output=True, text=True, check=True)
            if enabled:
                memory_lines = [line for line in result.stdout.splitlines() if line.startswith("PFMEM1 ")]
                assert memory_lines
                assert "phase=before_allocate" in memory_lines[0]
                if mode in {"normal", "quiet", "abort_export"}:
                    phases = [line.split("phase=", 1)[1].split()[0] for line in memory_lines]
                    assert phases == ["before_allocate", "allocated", "capture_end", "released"]
                    assert "internal_free=100000" in memory_lines[0]
                    assert f"internal_free={97000 if gpio_observer else 98000}" in memory_lines[1]
                    assert "internal_free=100000" in memory_lines[-1]
                    assert all("internal_largest=80000 internal_min=70000" in line for line in memory_lines)
                    assert all(
                        "dma_free=" in line and "dma_largest=80000 dma_min=70000" in line for line in memory_lines
                    )
                    assert all("psram_free=0 psram_largest=0 psram_min=0" in line for line in memory_lines)
                if mode.startswith("begin_fail_"):
                    assert "PFRAW" not in result.stdout
                    assert "phase=released" in memory_lines[-1]
                    continue
                if mode == "stop_fail":
                    assert "state=failed" in result.stdout
                    continue
                if mode == "quiet":
                    assert read_windows(result.stdout.splitlines(), require_complete_sessions=True) == []
                    continue
                if mode == "noise_budget":
                    assert len(read_windows(result.stdout.splitlines(), require_complete_sessions=True)) == 4
                    continue
                if mode == "abort_export":
                    assert "state=aborted exports=0" in result.stdout
                    with pytest.raises(ValueError, match="incomplete"):
                        read_windows(result.stdout.splitlines(), require_complete_sessions=True)
                    continue
                (window,) = read_windows(result.stdout.splitlines())
                if mode == "history":
                    assert window.pretrigger_info == {
                        "requested_us": 75000,
                        "duration_us": 75060,
                        "short": 0,
                        "limited": 0,
                    }
                    assert [segment.metadata["id"] for segment in window.pretrigger] == [1, 2, 3]
                    assert [len(segment.words) for segment in window.pretrigger] == [10, 40, 40]
                    assert window.pretrigger[0].metadata["source_offset"] == 30
                    assert all(segment.edges == (417, -417) * len(segment.words) for segment in window.pretrigger)
                    for needle in ("PFHIST1 id=1 offset=0", "PFHIST1 id=2 end=1", "PFPRE1 session=42"):
                        damaged = [line for line in result.stdout.splitlines() if needle not in line]
                        with pytest.raises(ValueError):
                            read_windows(damaged)
                else:
                    assert window.pretrigger_info["short"] == 1
                assert window.edges == (417, -417) * (4096 if mode == "saturation" else 20 if mode == "timeout" else 40)
                assert window.metadata["queue_losses"] == int(mode == "queue_loss")
                assert window.metadata["timeout"] == int(mode == "timeout")
                assert window.metadata["saturated"] == int(mode == "saturation")
                if mode not in {"rearm_fail", "delete_fail"}:
                    assert read_windows(result.stdout.splitlines(), require_complete_sessions=True) == [window]
                else:
                    with pytest.raises(ValueError, match="failed"):
                        read_windows(result.stdout.splitlines(), require_complete_sessions=True)
            else:
                assert not result.stdout
        symbols = subprocess.run(["nm", "-C", str(binary)], capture_output=True, text=True, check=True).stdout
        assert ("export_raw_window" in symbols) is enabled
        assert (b"PFRAW2" in binary.read_bytes()) is enabled
        assert (b"PFSESSION" in binary.read_bytes()) is enabled
        assert (b"PFMEM1" in binary.read_bytes()) is enabled
        assert b"PFBATCH1" not in binary.read_bytes()
        assert "BufferedOokCapture" not in symbols
        assert ("log_diagnostic_memory_" in symbols) is enabled
        assert ("GPIOEdgeObserver::begin" in symbols) is gpio_observer
        assert (b"PFEDGE1" in binary.read_bytes()) is gpio_observer
        if gpio_observer:
            observed = subprocess.run([str(binary), "observer"], capture_output=True, text=True, check=True)
            from tools.waveform_gpio_edges import read_gpio_edges

            _, sessions, captures = read_gpio_edges(
                [
                    "PFGPIO1 session=42 capacity=2048 irq_budget=32 irq_window_us=1000 cache_off_safe=0",
                    *observed.stdout.splitlines(),
                ]
            )
            assert sessions == {42}
            assert captures[(42, 1)].encoded_edges == (200835, 201668)


def test_buffered_capture_real_task_handoff_and_deferred_export(tmp_path):
    """Compile the real task implementation against a threaded host RTOS model."""
    _write_receiver_stubs(tmp_path)
    runtime = ROOT / "tests/cpp/buffered_capture_runtime.h"
    (tmp_path / "buffered_capture_runtime.h").write_text(f'#pragma once\n#include "{runtime}"\n')
    for name in (
        "driver/rmt_rx.h",
        "freertos/FreeRTOS.h",
        "freertos/queue.h",
        "freertos/task.h",
        "freertos/semphr.h",
        "esp_timer.h",
    ):
        (tmp_path / name).write_text('#pragma once\n#include "buffered_capture_runtime.h"\n')
    heap = tmp_path / "esp_heap_caps.h"
    heap.write_text(
        heap.read_text().replace(
            "int) { if (fault == 2)",
            "int caps) { if (fault == 20 && (caps & MALLOC_CAP_SPIRAM)) return nullptr; if (fault == 2)",
        )
    )
    (tmp_path / "esp_random.h").write_text(
        "#pragma once\n#include <atomic>\ninline std::atomic<unsigned> session_identity{41};\n"
        "inline unsigned esp_random() { return ++session_identity; }\n"
    )
    component = ROOT / "esphome/components/proflame2_tembed"
    legacy = ProflameFrame(serial_id=0x123456, cmd1=1, cmd2=6, err1=0x76, err2=0xDE)
    rows = [("legacy", "".join(f"{byte:08b}" for byte in frame_to_air_bytes(legacy))[:182])]
    extended = json.loads((ROOT / "tests/fixtures/issue_26_mendota_extended_capture.json").read_text())["captures"][0]
    rows.append(
        (
            "extended",
            "".join(f"{byte:08b}" for byte in bytes.fromhex(extended["pcm_hex"]))[: extended["pcm_bit_length"]],
        )
    )
    for observer in (False, True):
        binary = tmp_path / f"buffered-{observer}"
        result = subprocess.run(
            [
                "g++",
                "-std=c++17",
                "-pthread",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-fsanitize=address,undefined",
                "-fno-omit-frame-pointer",
                "-I",
                str(tmp_path),
                "-I",
                str(ROOT / "tests/cpp"),
                "-I",
                str(component),
                "-DUSE_PROFLAME2_WAVEFORM_DIAGNOSTICS",
                "-DUSE_PROFLAME2_BUFFERED_WAVEFORM_DIAGNOSTICS",
                *(["-DUSE_PROFLAME2_GPIO_EDGE_DIAGNOSTICS"] if observer else []),
                str(ROOT / "tests/cpp/test_buffered_capture.cpp"),
                str(component / "buffered_ook_capture.cpp"),
                str(component / "gpio_edge_observer.cpp"),
                "-o",
                str(binary),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        for label, bits in rows:
            runs = [(key, 417 * len(list(group))) for key, group in groupby(bits)]
            packed = []
            for index in range(0, len(runs), 2):
                bit0, d0 = runs[index]
                bit1, d1 = runs[index + 1] if index + 1 < len(runs) else ("0", 0)
                packed.append(d0 | ((bit0 == "1") << 15) | (d1 << 16) | ((bit1 == "1") << 31))
            data = f"{len(packed)} " + " ".join(f"{word:x}" for word in packed)
            modes = (
                ["normal"]
                if label == "extended"
                else [
                    "normal",
                    "restart",
                    "quiet",
                    "deadline",
                    "partial",
                    "timeout_partial",
                    "limit",
                    "pool_loss",
                    "callback_loss",
                    "retention_loss",
                    "queue_loss",
                    "rearm_fail",
                    "stop_fail",
                    "delete_fail",
                    *[f"setup_fail_{fault}" for fault in (1, 2, 3, 4, 5, 6, 9, 20, 30)],
                ]
            )
            for mode in modes:
                result = subprocess.run([str(binary), mode], input=data, capture_output=True, text=True, timeout=10)
                assert result.returncode == 0, (observer, label, mode, result.stdout, result.stderr)
                if mode.startswith("setup_fail_"):
                    assert "PFRAW" not in result.stdout
                    assert "phase=released" in result.stdout
                elif mode in {
                    "pool_loss",
                    "callback_loss",
                    "retention_loss",
                    "queue_loss",
                    "rearm_fail",
                    "stop_fail",
                    "delete_fail",
                }:
                    assert "state=failed" in result.stdout
                    with pytest.raises(ValueError):
                        read_windows(result.stdout.splitlines(), require_complete_sessions=True)
                else:
                    windows = read_windows(result.stdout.splitlines(), require_complete_sessions=True)
                    assert len(windows) == (
                        0 if mode in {"quiet", "deadline"} else 3 if mode == "restart" else 2 if mode == "normal" else 1
                    )
                    assert "PFSTOP1" in result.stdout
                    if windows:
                        assert result.stdout.index("PFSTOP1") < result.stdout.index("PFRAW2 session=")
                        if mode in {"normal", "restart", "limit"}:
                            assert all(window.words == tuple(packed) for window in windows)
