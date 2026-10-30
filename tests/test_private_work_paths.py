"""Keep new captures external and historical analysis in its relocated directory."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from tools.proflame_capture import yardstick_diagnostic_probe
from tools.proflame_capture.models import CaptureSessionConfig
from tools.proflame_capture.run_capture_session import _build_parser
from tools.proflame_capture_analysis.mark_invalidated_analysis import parse_args


def test_capture_model_and_cli_use_external_output_defaults(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("PROFLAME2_CAPTURE_ROOT", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    expected = tmp_path / "proflame2/waveform/coordinated"
    assert CaptureSessionConfig().output_root == expected
    assert Path(_build_parser().parse_args([]).output_root) == expected


def test_capture_output_overrides(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PROFLAME2_CAPTURE_ROOT", str(tmp_path / "capture-store"))
    assert CaptureSessionConfig().output_root == tmp_path / "capture-store/coordinated"
    assert Path(_build_parser().parse_args([]).output_root) == tmp_path / "capture-store/coordinated"
    explicit = tmp_path / "explicit"
    assert CaptureSessionConfig(output_root=explicit).output_root == explicit
    assert Path(_build_parser().parse_args(["--output-root", str(explicit)]).output_root) == explicit


def test_analysis_invalidation_uses_relocated_archive(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["mark_invalidated_analysis", "--dry-run"])
    assert Path(parse_args().root) == Path("docs/private/analysis/live")


def test_yardstick_probe_uses_external_output_without_hardware(tmp_path, monkeypatch) -> None:
    """Probe the destination using a stub; never connect to an actual radio."""

    class Collector:
        def __init__(self, **kwargs):
            pass

        def start_session(self, session):
            pass

        def start_sample(self, sample):
            assert sample.sample_dir == tmp_path / "capture-store/yardstick_probe"

        def is_complete(self):
            return True

        def finalize_sample(self, sample):
            return SimpleNamespace(reject_reason=None, valid=True, metadata={})

        def close(self):
            pass

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PROFLAME2_CAPTURE_ROOT", str(tmp_path / "capture-store"))
    monkeypatch.setattr(yardstick_diagnostic_probe, "LiveYardStickDiagnosticSource", lambda: object())
    monkeypatch.setattr(yardstick_diagnostic_probe, "YardStickDiagnosticCollector", Collector)
    assert yardstick_diagnostic_probe.main([]) == 0
    assert (tmp_path / "capture-store/yardstick_probe").is_dir()
    assert not (tmp_path / "analysis").exists()
    assert not (tmp_path / "docs").exists()
