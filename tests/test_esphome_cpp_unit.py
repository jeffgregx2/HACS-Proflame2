"""Host-executed unit tests for ESPHome transport-only C++ helpers."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPONENT_ROOT = REPO_ROOT / "esphome" / "components" / "proflame2_tembed"
CPP_TEST = REPO_ROOT / "tests" / "cpp" / "test_tx_payload_layout.cpp"


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, cwd=cwd, text=True, capture_output=True)


def _line_coverage_percent(output: str, source: Path) -> float:
    match = re.search(
        rf"File '{re.escape(str(source))}'\nLines executed:([0-9.]+)%",
        output,
    )
    assert match is not None, f"gcov did not report line coverage for {source}"
    return float(match.group(1))


def test_tx_payload_layout_cpp_unit_and_coverage(tmp_path: Path) -> None:
    """Exercise the firmware's legacy/extended TX shape logic with gcov."""

    compiler = shutil.which("g++")
    gcov = shutil.which("gcov")
    assert compiler is not None, "g++ is required for ESPHome C++ host unit tests"
    assert gcov is not None, "gcov is required for ESPHome C++ host coverage"

    common_flags = ["-std=c++17", "-Wall", "-Wextra", "-Werror", "--coverage", f"-I{COMPONENT_ROOT}"]
    source_files = (
        COMPONENT_ROOT / "tx_controller.cpp",
        COMPONENT_ROOT / "tx_payload_layout.cpp",
        CPP_TEST,
    )
    object_files: list[Path] = []
    for source in source_files:
        output = tmp_path / f"{source.stem}.o"
        _run([compiler, *common_flags, "-c", str(source), "-o", str(output)], cwd=tmp_path)
        object_files.append(output)

    executable = tmp_path / "test_tx_payload_layout"
    _run([compiler, "--coverage", *(str(path) for path in object_files), "-o", str(executable)], cwd=tmp_path)
    _run([str(executable)], cwd=tmp_path)

    coverage_outputs = [
        _run([gcov, "-b", "-o", str(tmp_path), str(source)], cwd=tmp_path).stdout for source in source_files[:2]
    ]

    layout_report = (tmp_path / "tx_payload_layout.cpp.gcov").read_text(encoding="utf-8")
    assert "#####" not in layout_report
    assert _line_coverage_percent(coverage_outputs[0], source_files[0]) >= 98.0
    assert _line_coverage_percent(coverage_outputs[1], source_files[1]) == 100.0
