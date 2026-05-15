"""Tests for the offline rtl_433 capture comparison helper."""

from __future__ import annotations

import importlib.util
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "tools" / "compare_rtl433_captures.py"
SPEC = importlib.util.spec_from_file_location("compare_rtl433_captures", MODULE_PATH)
assert SPEC is not None
assert SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


MODEL_CAPTURE = """
Detected OOK package\t2026-05-01 07:24:32

time      : 2026-05-01 07:24:32
model     : Proflame2-Remote
Id        : 3b3f02
Cmd1      : 01
Cmd2      : 06
Err1      : 76
Err2      : de
Pilot     : 0
Light     : 0
Thermostat: 0
Power     : 1
Front     : 0
Fan       : 0
Aux       : 0
Flame     : 6
Integrity : CHECKSUM
Analyzing pulses...
Total count:  340,  width: 397.47 ms
Pulse width distribution:
 [ 0] count:   35,  width: 1224 us [1220;1240]
 [ 1] count:  225,  width:  408 us [400;420]
 [ 2] count:   80,  width:  820 us [812;832]
Gap width distribution:
 [ 0] count:   80,  width:  832 us [824;840]
 [ 1] count:  248,  width:  424 us [408;488]
 [ 2] count:    7,  width:  576 us [548;612]
 [ 3] count:    4,  width: 5240 us [5188;5320]
Pulse+gap period distribution:
 [ 0] count:   45,  width: 1920 us [1636;2072]
 [ 1] count:  185,  width:  832 us [816;1024]
Gap+pulse period distribution:
 [ 0] count:  101,  width: 1240 us [1232;1248]
 [ 1] count:  175,  width:  824 us [820;832]
Timing distribution:
 [ 0] count:   35,  width: 1224 us [1220;1240]
 [ 1] count:  473,  width:  416 us [400;488]
 [ 2] count:  160,  width:  824 us [812;840]
Level estimates [high, low]:  21754,     12
RSSI: 1.2 dB SNR: 31.4 dB Noise: -31.4 dB
Frequency offsets [F1, F2]:   19414,      0\t(+74.1 kHz, +0.0 kHz)
Guessing modulation: Pulse Width Modulation with sync/delimiter
Use a flex decoder with -X 'n=name,m=OOK_PWM,s=408,l=820,r=5324,g=0,t=0,y=1224'
[pulse_slicer_pwm] Analyzer Device
codes     : {8}b6, {9}bf0, {9}fa8, {9}fc8, {9}f70, {8}6d, {9}df0
repeated 5 times
"""


ANALYZER_ONLY_CAPTURE = """
Detected OOK package\t2026-05-01 07:28:45
Analyzing pulses...
Total count:  340,  width: 418.16 ms
Pulse width distribution:
 [ 0] count:   35,  width: 1264 us [1216;1320]
 [ 1] count:  225,  width:  432 us [428;484]
 [ 2] count:   80,  width:  848 us [848;904]
Gap width distribution:
 [ 0] count:  260,  width:  396 us [344;404]
 [ 1] count:   75,  width:  816 us [764;872]
 [ 2] count:    4,  width: 10720 us [10684;10776]
Pulse+gap period distribution:
 [ 0] count:  110,  width: 1664 us [1616;1720]
Gap+pulse period distribution:
 [ 0] count:  131,  width: 1248 us [1196;1304]
Timing distribution:
 [ 0] count:   35,  width: 1264 us [1216;1320]
 [ 1] count:  485,  width:  412 us [344;484]
 [ 2] count:  155,  width:  832 us [764;904]
Guessing modulation: Pulse Width Modulation with sync/delimiter
Use a flex decoder with -X 'n=name,m=OOK_PWM,s=432,l=848,r=10780,g=0,t=0,y=1264'
[pulse_slicer_pwm] Analyzer Device
codes     : {8}b6, {9}bf0, {9}fa8, {9}fc8, {9}f70, {8}6d, {9}df0, {8}b6, {9}bf0, {9}fa8, {9}fc8, {9}f70, {8}6d, {9}df0
"""


TRUNCATED_CAPTURE = """
Total count: 5
Guessing modulation: Un-modulated signal
codes:
{8}dead
"""


def test_parse_model_decoded_capture() -> None:
    result = MODULE.parse_capture_text(MODEL_CAPTURE, source_filename="native_on.txt")

    assert result.source_filename == "native_on.txt"
    assert result.model_decoded is True
    assert result.model == "Proflame2-Remote"
    assert result.decoded_fields["Id"] == "3b3f02"
    assert result.decoded_fields["Cmd1"] == "01"
    assert result.decoded_fields["Cmd2"] == "06"
    assert result.decoded_fields["Err1"] == "76"
    assert result.decoded_fields["Err2"] == "de"
    assert result.total_count == 340
    assert result.width_ms == 397.47
    assert len(result.pulse_widths) == 3
    assert len(result.gap_widths) == 4
    assert len(result.pulse_gap_periods) == 2
    assert len(result.gap_pulse_periods) == 2
    assert len(result.timing_distribution) == 3
    assert result.guessed_modulation == "Pulse Width Modulation with sync/delimiter"
    assert result.flex_decoder == "-X 'n=name,m=OOK_PWM,s=408,l=820,r=5324,g=0,t=0,y=1224'"
    assert result.flex_params == {"s": 408, "l": 820, "r": 5324, "y": 1224}
    assert result.analyzer_codes == [
        "{8}b6",
        "{9}bf0",
        "{9}fa8",
        "{9}fc8",
        "{9}f70",
        "{8}6d",
        "{9}df0",
    ]
    assert result.repeated_times == 5
    assert result.missing_sections == []


def test_parse_analyzer_only_capture_without_model_decode() -> None:
    result = MODULE.parse_capture_text(ANALYZER_ONLY_CAPTURE, source_filename="lilygo_on.txt")

    assert result.model_decoded is False
    assert result.model is None
    assert result.total_count == 340
    assert result.width_ms == 418.16
    assert len(result.pulse_widths) == 3
    assert len(result.gap_widths) == 3
    assert len(result.pulse_gap_periods) == 1
    assert len(result.gap_pulse_periods) == 1
    assert len(result.timing_distribution) == 3
    assert len(result.analyzer_codes) == 14
    assert "model_decode" in result.missing_sections
    assert "analyzer_codes" not in result.missing_sections
    assert "pulse_widths" not in result.missing_sections


def test_family_inference_uses_flex_targets_for_real_capture_shapes() -> None:
    result = MODULE.parse_capture_text(MODEL_CAPTURE, source_filename="native_on.txt")

    pulse_short, pulse_long, pulse_sync = MODULE.infer_pulse_families(result)
    gap_short, gap_long, gap_reset = MODULE.infer_gap_families(result)

    assert (pulse_short.width_us, pulse_long.width_us, pulse_sync.width_us) == (408, 820, 1224)
    assert (gap_short.width_us, gap_long.width_us, gap_reset.width_us) == (424, 832, 5240)


def test_repeat_pattern_detection_finds_identical_chunks() -> None:
    result = MODULE.parse_capture_text(ANALYZER_ONLY_CAPTURE)
    repeat = MODULE.detect_repeat_pattern(result.analyzer_codes)

    assert repeat is not None
    assert repeat.repeat_len == 7
    assert repeat.repeat_count == 2
    assert repeat.all_repeats_identical is True
    assert repeat.chunk == (
        "{8}b6",
        "{9}bf0",
        "{9}fa8",
        "{9}fc8",
        "{9}f70",
        "{8}6d",
        "{9}df0",
    )


def test_comparison_reports_mismatch_against_baseline() -> None:
    baseline = MODULE.parse_capture_text(MODEL_CAPTURE, source_filename="native_on.txt")
    variant = MODULE.parse_capture_text(
        ANALYZER_ONLY_CAPTURE.replace("{9}df0", "{9}df8", 1),
        source_filename="lilygo_on.txt",
    )

    rendered = MODULE.format_comparison([baseline, variant])

    assert "Comparison Summary" in rendered
    assert "Normalized Diff vs Baseline (native_on.txt)" in rendered
    assert "code_match_vs_baseline=diff@6" in rendered
    assert "analyzer_codes_mismatch=yes first_diff_index=6" in rendered
    assert "pulse_families=" in rendered
    assert "gap_families=" in rendered
    assert "missing_sections=" in rendered


def test_missing_sections_do_not_crash() -> None:
    result = MODULE.parse_capture_text(TRUNCATED_CAPTURE, source_filename="broken.txt")

    assert result.total_count == 5
    assert result.width_ms is None
    assert result.model_decoded is False
    assert result.pulse_widths == []
    assert result.gap_widths == []
    assert result.analyzer_codes == ["{8}dead"]
    assert "pulse_widths" in result.missing_sections
    assert "gap_widths" in result.missing_sections
    assert "timing_distribution" in result.missing_sections


def test_rtl433_proflame2_pcm_debug_instrumentation_is_present() -> None:
    pulse_slicer = (REPO_ROOT / "rtl_433" / "src" / "pulse_slicer.c").read_text(encoding="utf-8")

    assert "#define PROFLAME2_PCM_DEBUG 1" in pulse_slicer
    assert "PROFLAME2_PCM_DEBUG slicer" in pulse_slicer
    assert "PROFLAME2_PCM_DEBUG pair index=%u" in pulse_slicer
    assert "PROFLAME2_PCM_DEBUG row index=%u" in pulse_slicer
    assert "PROFLAME2_PCM_DEBUG package rows=%u" in pulse_slicer
    assert "device->protocol_num == 207" in pulse_slicer
    assert 'strncmp(row_hex, "e5a9a9", 6) == 0' in pulse_slicer
    assert "first_diff_bit" in pulse_slicer


def test_main_without_arguments_prints_help_and_exits_cleanly() -> None:
    output = io.StringIO()
    with redirect_stdout(output):
        exit_code = MODULE.main([])

    rendered = output.getvalue()
    assert exit_code == 0
    assert "usage:" in rendered
    assert "captures" in rendered
    assert "Example capture command:" in rendered
    assert "rtl_433 -f 315M -g 40 -A -R 207" in rendered
    assert "Example comparison:" in rendered
