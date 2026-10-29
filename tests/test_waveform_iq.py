"""Bounded IQ slices preserve exact bytes and never replace source artifacts."""

import hashlib
import io
import json
from pathlib import Path

import pytest

from tools.waveform_iq import BYTES_PER_SAMPLE, extract_iq, main, sample_window


@pytest.mark.parametrize("sample_format", BYTES_PER_SAMPLE)
def test_exact_slice_and_hash(tmp_path, sample_format):
    source = tmp_path / "source.iq"
    destination = tmp_path / "slice.iq"
    width = BYTES_PER_SAMPLE[sample_format]
    data = bytes(range(256)) * 20000
    source.write_bytes(data)
    result = extract_iq(source, destination, sample_format=sample_format, start_sample=3, end_sample=400000)
    expected = data[3 * width : 400000 * width]
    assert destination.read_bytes() == expected
    assert result["sha256"] == hashlib.sha256(expected).hexdigest()
    assert result["bytes"] == len(expected)
    with pytest.raises(FileExistsError):
        extract_iq(source, destination, sample_format=sample_format, start_sample=3, end_sample=4)
    assert destination.read_bytes() == expected
    assert source.read_bytes() == data


@pytest.mark.parametrize("start,end", [(-1, 1), (1, 1), (3, 2), (0, 100), (True, 2), (0.5, 2)])
def test_rejects_invalid_or_outside_ranges(tmp_path, start, end):
    source = tmp_path / "source.iq"
    source.write_bytes(bytes(16))
    output = tmp_path / "slice.iq"
    with pytest.raises(ValueError):
        extract_iq(source, output, sample_format="cu8", start_sample=start, end_sample=end)
    assert not output.exists()


def test_rejects_truncated_input_same_output_and_bad_format(tmp_path):
    source = tmp_path / "source.iq"
    source.write_bytes(bytes(3))
    for destination, fmt in [(source, "cu8"), (tmp_path / "out.iq", "cu8"), (tmp_path / "out.iq", "bad")]:
        with pytest.raises(ValueError):
            extract_iq(source, destination, sample_format=fmt, start_sample=0, end_sample=1)
    assert source.read_bytes() == bytes(3)


def test_sample_window_expands_uncertainty_and_reports_clipping():
    fit = {"samples_per_us": 0.25, "sample_offset": 0, "max_residual_samples": 2}
    assert sample_window(100, 200, fit, margin_us=20, uncertainty_samples=3) == {
        "start_sample": 15,
        "end_sample": 60,
        "pretrigger_clipped": False,
    }
    assert sample_window(0, 100, fit)["pretrigger_clipped"] is True


@pytest.mark.parametrize(
    "start,end,margin,uncertainty",
    [(1, 1, 0, 0), (1, 2, -1, 0), (1, 2, 0, -1), (float("nan"), 2, 0, 0), (-100, -50, 0, 0)],
)
def test_rejects_bad_alignment_windows(start, end, margin, uncertainty):
    with pytest.raises(ValueError):
        sample_window(
            start,
            end,
            {"samples_per_us": 0.25, "sample_offset": 0, "max_residual_samples": 0},
            margin_us=margin,
            uncertainty_samples=uncertainty,
        )


def test_iq_cli_and_cleanup_after_short_read(tmp_path, monkeypatch, capsys):
    source = tmp_path / "source.cu8"
    destination = tmp_path / "slice.cu8"
    source.write_bytes(bytes(16))
    monkeypatch.setattr(
        "sys.argv", ["iq", str(source), str(destination), "--format", "cu8", "--start-sample", "1", "--end-sample", "3"]
    )
    main()
    assert json.loads(capsys.readouterr().out)["bytes"] == 4
    original_open = Path.open

    def short_open(path, mode="r", *args, **kwargs):
        if path == source and mode == "rb":
            return io.BytesIO(bytes(2))
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", short_open)
    incomplete = tmp_path / "incomplete.cu8"
    with pytest.raises(ValueError, match="ended"):
        extract_iq(source, incomplete, sample_format="cu8", start_sample=0, end_sample=8)
    assert not incomplete.exists()
    assert destination.exists()
