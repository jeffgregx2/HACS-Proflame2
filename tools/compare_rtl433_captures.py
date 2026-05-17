"""Compare rtl_433 ``-A`` capture text files for Proflame2 TX debugging.

Purpose
-------
This tool compares offline ``rtl_433`` capture text from native remotes,
YardStick transmissions, and LilyGO/CC1101 transmissions. It normalizes the
parts of the capture that matter for Proflame2 debugging:

- decoded Proflame2 fields when model decode occurs
- analyzer code sequence from ``[pulse_slicer_pwm]``
- pulse and gap timing families
- repeat/reset gap behavior
- per-file deltas against a baseline capture

Use this when the analyzer codes match but ``rtl_433`` model decode still fails,
or when pulse histograms need to be compared without manually reading each file.

Typical workflow
----------------
1. Capture a transmission with ``rtl_433`` and redirect stdout to a text file.
2. Repeat for native, YardStick, and LilyGO transmissions.
3. Compare them with this script.

Example
-------
    python tools/compare_rtl433_captures.py native_on.txt yardstick_on.txt lilygo_on.txt
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")
CAPTURE_COMMAND_EXAMPLE = "rtl_433 -f 315M -g 40 -A -R 207"

DECODE_FIELD_NAMES = (
    "Id",
    "Cmd1",
    "Cmd2",
    "Err1",
    "Err2",
    "Power",
    "Flame",
    "Fan",
    "Light",
    "Thermostat",
    "Front",
    "Aux",
    "Pilot",
    "Integrity",
)


@dataclass(frozen=True)
class DistributionRow:
    index: int | None
    count: int | None
    nominal_width_us: int | None
    minimum_width_us: int | None = None
    maximum_width_us: int | None = None


@dataclass(frozen=True)
class RepeatPattern:
    chunk: tuple[str, ...]
    repeat_len: int
    repeat_count: int
    all_repeats_identical: bool


@dataclass
class CaptureParseResult:
    source_filename: str
    model: str | None = None
    decoded_fields: dict[str, str] = field(default_factory=dict)
    total_count: int | None = None
    width_ms: float | None = None
    pulse_widths: list[DistributionRow] = field(default_factory=list)
    gap_widths: list[DistributionRow] = field(default_factory=list)
    pulse_gap_periods: list[DistributionRow] = field(default_factory=list)
    gap_pulse_periods: list[DistributionRow] = field(default_factory=list)
    timing_distribution: list[DistributionRow] = field(default_factory=list)
    guessed_modulation: str | None = None
    flex_decoder: str | None = None
    analyzer_codes: list[str] = field(default_factory=list)
    repeated_times: int | None = None
    level_estimates_high: int | None = None
    level_estimates_low: int | None = None
    rssi_db: float | None = None
    snr_db: float | None = None
    noise_db: float | None = None
    frequency_offsets: list[str] = field(default_factory=list)
    missing_sections: list[str] = field(default_factory=list)
    flex_params: dict[str, int] = field(default_factory=dict)

    @property
    def model_decoded(self) -> bool:
        return self.model is not None


@dataclass(frozen=True)
class FamilySummary:
    width_us: int | None
    count: int | None


def parse_capture_file(path: Path) -> CaptureParseResult:
    return parse_capture_text(path.read_text(encoding="utf-8"), source_filename=path.name)


def parse_capture_text(text: str, *, source_filename: str = "<memory>") -> CaptureParseResult:
    result = CaptureParseResult(source_filename=source_filename)
    current_section: str | None = None
    collecting_codes = False
    header_map = {
        "Pulse width distribution:": "pulse_widths",
        "Gap width distribution:": "gap_widths",
        "Pulse+gap period distribution:": "pulse_gap_periods",
        "Gap+pulse period distribution:": "gap_pulse_periods",
        "Timing distribution:": "timing_distribution",
    }
    seen_sections: set[str] = set()

    for raw_line in text.splitlines():
        line = ANSI_ESCAPE_RE.sub("", raw_line).strip()
        if not line:
            collecting_codes = False
            current_section = None
            continue

        model_match = re.match(r"model\s*:\s*(.+)$", line, re.IGNORECASE)
        if model_match:
            result.model = model_match.group(1).strip()
            continue

        total_match = re.match(r"Total count:\s*(\d+)(?:,\s*width:\s*([0-9.]+)\s*ms)?", line)
        if total_match:
            result.total_count = int(total_match.group(1))
            if total_match.group(2):
                result.width_ms = float(total_match.group(2))
            continue

        guessed_match = re.match(r"Guessing modulation:\s*(.+)$", line)
        if guessed_match:
            result.guessed_modulation = guessed_match.group(1).strip()
            continue

        flex_match = re.match(r"Use (?:a )?flex decoder(?: with)?\s*(.+)$", line, re.IGNORECASE)
        if flex_match:
            result.flex_decoder = flex_match.group(1).strip()
            result.flex_params = _parse_flex_params(result.flex_decoder)
            continue

        repeated_match = re.match(r"repeated\s+(\d+)\s+times", line, re.IGNORECASE)
        if repeated_match:
            result.repeated_times = int(repeated_match.group(1))
            continue

        level_match = re.match(r"Level estimates\s*\[high,\s*low\]:\s*(-?\d+)\s*,\s*(-?\d+)", line)
        if level_match:
            result.level_estimates_high = int(level_match.group(1))
            result.level_estimates_low = int(level_match.group(2))
            continue

        rssi_match = re.search(
            r"RSSI:\s*(-?[0-9.]+)\s*dB.*SNR:\s*(-?[0-9.]+)\s*dB.*Noise:\s*(-?[0-9.]+)\s*dB",
            line,
        )
        if rssi_match:
            result.rssi_db = float(rssi_match.group(1))
            result.snr_db = float(rssi_match.group(2))
            result.noise_db = float(rssi_match.group(3))
            continue

        frequency_match = re.search(r"Frequency offsets\s*\[[^]]+\]\s*:\s*(.+)$", line)
        if frequency_match:
            result.frequency_offsets.append(frequency_match.group(1).strip())
            continue

        field_match = re.match(rf"({'|'.join(DECODE_FIELD_NAMES)})\s*:\s*(.+)$", line)
        if field_match:
            result.decoded_fields[field_match.group(1)] = field_match.group(2).strip()
            continue

        if line in header_map:
            current_section = header_map[line]
            seen_sections.add(current_section)
            collecting_codes = False
            continue

        if re.match(r"codes\s*:", line, re.IGNORECASE):
            collecting_codes = True
            current_section = None
            result.analyzer_codes.extend(_extract_codes(line))
            continue

        if collecting_codes:
            codes = _extract_codes(line)
            if codes:
                result.analyzer_codes.extend(codes)
                continue
            collecting_codes = False

        if current_section is not None:
            row = _parse_distribution_row(line)
            if row is not None:
                getattr(result, current_section).append(row)
                continue
            current_section = None

    expected_sections = [
        "pulse_widths",
        "gap_widths",
        "pulse_gap_periods",
        "gap_pulse_periods",
        "timing_distribution",
    ]
    if not result.model_decoded:
        result.missing_sections.append("model_decode")
    if not result.analyzer_codes:
        result.missing_sections.append("analyzer_codes")
    if result.flex_decoder is None:
        result.missing_sections.append("flex_decoder")
    if result.guessed_modulation is None:
        result.missing_sections.append("guessed_modulation")
    for section in expected_sections:
        if section not in seen_sections or not getattr(result, section):
            result.missing_sections.append(section)

    return result


def _extract_codes(line: str) -> list[str]:
    return re.findall(r"\{\d+\}[0-9a-fA-F]+", line)


def _parse_distribution_row(line: str) -> DistributionRow | None:
    indexed = re.match(
        r"\[\s*(\d+)\]\s+count:\s*(\d+),\s*width:\s*(\d+)\s*us(?:\s*\[\s*(\d+)\s*;\s*(\d+)\s*\])?",
        line,
    )
    if indexed:
        return DistributionRow(
            index=int(indexed.group(1)),
            count=int(indexed.group(2)),
            nominal_width_us=int(indexed.group(3)),
            minimum_width_us=int(indexed.group(4)) if indexed.group(4) else None,
            maximum_width_us=int(indexed.group(5)) if indexed.group(5) else None,
        )

    simple = re.match(r"(\d+)\s+\w+\s+at\s+(\d+)\s*us", line)
    if simple:
        return DistributionRow(
            index=None,
            count=int(simple.group(1)),
            nominal_width_us=int(simple.group(2)),
        )
    return None


def _parse_flex_params(line: str) -> dict[str, int]:
    params: dict[str, int] = {}
    for key, value in re.findall(r"([slry])=(\d+)", line):
        params[key] = int(value)
    return params


def detect_repeat_pattern(codes: list[str]) -> RepeatPattern | None:
    if not codes:
        return None
    for repeat_len in range(1, len(codes) + 1):
        if len(codes) % repeat_len != 0:
            continue
        chunk = tuple(codes[:repeat_len])
        repeat_count = len(codes) // repeat_len
        if all(tuple(codes[index : index + repeat_len]) == chunk for index in range(0, len(codes), repeat_len)):
            return RepeatPattern(
                chunk=chunk,
                repeat_len=repeat_len,
                repeat_count=repeat_count,
                all_repeats_identical=True,
            )
    return RepeatPattern(chunk=tuple(codes), repeat_len=len(codes), repeat_count=1, all_repeats_identical=True)


def summarize_families(rows: Iterable[DistributionRow]) -> tuple[FamilySummary, FamilySummary, FamilySummary]:
    ordered = sorted(
        (row for row in rows if row.nominal_width_us is not None),
        key=lambda row: row.nominal_width_us,
    )
    families = [FamilySummary(None, None), FamilySummary(None, None), FamilySummary(None, None)]
    if not ordered:
        return tuple(families)  # type: ignore[return-value]
    if len(ordered) == 1:
        families[0] = FamilySummary(ordered[0].nominal_width_us, ordered[0].count)
    elif len(ordered) == 2:
        families[0] = FamilySummary(ordered[0].nominal_width_us, ordered[0].count)
        families[2] = FamilySummary(ordered[1].nominal_width_us, ordered[1].count)
    else:
        families[0] = FamilySummary(ordered[0].nominal_width_us, ordered[0].count)
        families[1] = FamilySummary(ordered[1].nominal_width_us, ordered[1].count)
        families[2] = FamilySummary(ordered[-1].nominal_width_us, ordered[-1].count)
    return tuple(families)  # type: ignore[return-value]


def _closest_family(rows: list[DistributionRow], target_us: int | None) -> FamilySummary:
    if not rows or target_us is None:
        return FamilySummary(None, None)
    candidates = [row for row in rows if row.nominal_width_us is not None]
    if not candidates:
        return FamilySummary(None, None)
    winner = min(candidates, key=lambda row: abs(row.nominal_width_us - target_us))
    return FamilySummary(winner.nominal_width_us, winner.count)


def infer_pulse_families(capture: CaptureParseResult) -> tuple[FamilySummary, FamilySummary, FamilySummary]:
    if capture.flex_params:
        return (
            _closest_family(capture.pulse_widths, capture.flex_params.get("s")),
            _closest_family(capture.pulse_widths, capture.flex_params.get("l")),
            _closest_family(capture.pulse_widths, capture.flex_params.get("y")),
        )
    short_pulse, long_pulse, sync = summarize_families(capture.pulse_widths)
    return short_pulse, long_pulse, sync


def infer_gap_families(capture: CaptureParseResult) -> tuple[FamilySummary, FamilySummary, FamilySummary]:
    if capture.flex_params:
        return (
            _closest_family(capture.gap_widths, capture.flex_params.get("s")),
            _closest_family(capture.gap_widths, capture.flex_params.get("l")),
            _closest_family(capture.gap_widths, capture.flex_params.get("r")),
        )
    return summarize_families(capture.gap_widths)


def first_code_difference_index(a: list[str], b: list[str]) -> int | None:
    limit = min(len(a), len(b))
    for index in range(limit):
        if a[index] != b[index]:
            return index
    if len(a) != len(b):
        return limit
    return None


def _format_optional(value: object) -> str:
    if value is None:
        return "-"
    return str(value)


def _format_distribution_rows(rows: list[DistributionRow]) -> str:
    if not rows:
        return "-"
    return ", ".join(
        f"[{_format_optional(row.index)}] {_format_optional(row.count)}x{_format_optional(row.nominal_width_us)}"
        for row in rows
    )


def _format_ms_delta(value: float | None, baseline: float | None) -> str:
    if value is None or baseline is None:
        return "-"
    delta = value - baseline
    pct = (delta / baseline * 100.0) if baseline else 0.0
    return f"{delta:+.2f} ms ({pct:+.1f}%)"


def _format_us_delta(value: int | None, baseline: int | None) -> str:
    if value is None or baseline is None:
        return "-"
    return f"{value - baseline:+d} us"


def _capture_summary_line(capture: CaptureParseResult, baseline: CaptureParseResult) -> str:
    pulse_short, pulse_long, pulse_sync = infer_pulse_families(capture)
    gap_short, gap_long, gap_reset = infer_gap_families(capture)
    repeat_pattern = detect_repeat_pattern(capture.analyzer_codes)
    pattern_summary = "-"
    if repeat_pattern is not None:
        chunk_preview = " ".join(repeat_pattern.chunk)
        pattern_summary = (
            f"repeat_len={repeat_pattern.repeat_len} repeat_count={repeat_pattern.repeat_count} "
            f"pattern={chunk_preview}"
        )
    codes_match = capture.analyzer_codes == baseline.analyzer_codes
    code_diff = (
        "match"
        if codes_match
        else f"diff@{first_code_difference_index(baseline.analyzer_codes, capture.analyzer_codes)}"
    )
    decoded = "yes" if capture.model_decoded else "no"
    fields = capture.decoded_fields
    decoded_summary = (
        " ".join(
            f"{name}={fields[name]}"
            for name in ("Id", "Cmd1", "Cmd2", "Err1", "Err2", "Power", "Flame")
            if name in fields
        )
        or "-"
    )
    return (
        f"{capture.source_filename}\n"
        f"  decoded={decoded} model={_format_optional(capture.model)} fields={decoded_summary}\n"
        f"  total_count={_format_optional(capture.total_count)} width_ms={_format_optional(capture.width_ms)} "
        f"sync={_format_optional(pulse_sync.width_us)}/{_format_optional(pulse_sync.count)} "
        f"short_pulse={_format_optional(pulse_short.width_us)}/{_format_optional(pulse_short.count)} "
        f"long_pulse={_format_optional(pulse_long.width_us)}/{_format_optional(pulse_long.count)}\n"
        f"  short_gap={_format_optional(gap_short.width_us)}/{_format_optional(gap_short.count)} "
        f"long_gap={_format_optional(gap_long.width_us)}/{_format_optional(gap_long.count)} "
        f"reset_gap={_format_optional(gap_reset.width_us)}/{_format_optional(gap_reset.count)}\n"
        f"  pulse_families={_format_distribution_rows(capture.pulse_widths)}\n"
        f"  gap_families={_format_distribution_rows(capture.gap_widths)}\n"
        f"  guessed_modulation={_format_optional(capture.guessed_modulation)} "
        f"flex={_format_optional(capture.flex_decoder)}\n"
        f"  codes={len(capture.analyzer_codes)} code_match_vs_baseline={code_diff}\n"
        f"  pattern={pattern_summary}\n"
        f"  missing_sections={', '.join(capture.missing_sections) if capture.missing_sections else '-'}"
    )


def _normalized_diff_line(capture: CaptureParseResult, baseline: CaptureParseResult) -> str:
    pulse_short, pulse_long, pulse_sync = infer_pulse_families(capture)
    gap_short, gap_long, gap_reset = infer_gap_families(capture)
    base_pulse_short, base_pulse_long, base_pulse_sync = infer_pulse_families(baseline)
    base_gap_short, base_gap_long, base_gap_reset = infer_gap_families(baseline)
    code_diff = first_code_difference_index(baseline.analyzer_codes, capture.analyzer_codes)
    field_mismatches = []
    for key in ("Id", "Cmd1", "Cmd2", "Err1", "Err2", "Power", "Flame"):
        if baseline.decoded_fields.get(key) != capture.decoded_fields.get(key):
            field_mismatches.append(key)
    return (
        f"{capture.source_filename}\n"
        f"  total_count_delta={_format_optional(None if capture.total_count is None or baseline.total_count is None else capture.total_count - baseline.total_count)} "
        f"width_delta={_format_ms_delta(capture.width_ms, baseline.width_ms)}\n"
        f"  sync_delta={_format_us_delta(pulse_sync.width_us, base_pulse_sync.width_us)} "
        f"short_pulse_delta={_format_us_delta(pulse_short.width_us, base_pulse_short.width_us)} "
        f"long_pulse_delta={_format_us_delta(pulse_long.width_us, base_pulse_long.width_us)}\n"
        f"  short_gap_delta={_format_us_delta(gap_short.width_us, base_gap_short.width_us)} "
        f"long_gap_delta={_format_us_delta(gap_long.width_us, base_gap_long.width_us)} "
        f"repeat_gap_delta={_format_us_delta(gap_reset.width_us, base_gap_reset.width_us)}\n"
        f"  model_decoded_mismatch={'yes' if capture.model_decoded != baseline.model_decoded else 'no'} "
        f"decoded_field_mismatch={','.join(field_mismatches) if field_mismatches else 'no'} "
        f"analyzer_codes_mismatch={'no' if code_diff is None else f'yes first_diff_index={code_diff}'}"
    )


def format_comparison(captures: list[CaptureParseResult]) -> str:
    if not captures:
        return "No captures provided."
    baseline = captures[0]
    sections = ["Comparison Summary"]
    sections.extend(_capture_summary_line(capture, baseline) for capture in captures)
    sections.append("")
    sections.append(f"Normalized Diff vs Baseline ({baseline.source_filename})")
    sections.extend(_normalized_diff_line(capture, baseline) for capture in captures[1:])
    return "\n".join(sections)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=("Compare offline rtl_433 -A capture text files for Proflame2 pulse, gap, and code analysis."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example capture command:\n"
            f"  {CAPTURE_COMMAND_EXAMPLE} > native_on_A.txt\n\n"
            "Example comparison:\n"
            "  python tools/compare_rtl433_captures.py native_on_A.txt yardstick_on_A.txt lilygo_on_A.txt"
        ),
    )
    parser.add_argument(
        "captures",
        nargs="*",
        help="rtl_433 -A text files to compare, using the first file as baseline",
    )
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        parser.print_help(sys.stdout)
        return 0
    args = parser.parse_args(argv)

    captures = [parse_capture_file(Path(path)) for path in args.captures]
    print(format_comparison(captures))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
