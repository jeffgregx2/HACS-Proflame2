"""Optional GPIO evidence remains distinct from RMT and rejects damaged logs."""

import pytest

from tools.waveform.waveform_diagnostics import checksum, read_windows
from tools.waveform.waveform_gpio_edges import edge_checksum, read_gpio_edges


def records(edges=(200001, 200834), *, flags="clipped=0 raced=0 storm=0"):
    yield "PFGPIO1 session=42 capacity=2048 irq_budget=32 irq_window_us=1000 cache_off_safe=0"
    yield f"PFEDGE1 session=42 id=1 count={len(edges)} checksum={edge_checksum(edges):08x}"
    yield (
        "PFEDGE1 id=1 request_us=25000 start_us=20000 ready_us=20001 snapshot_us=110000 "
        f"first_seq=0 total={len(edges)} {flags} repeats=0"
    )
    if edges:
        yield "PFEDGE1 id=1 offset=0 edges=" + "".join(f"{edge:016x}" for edge in edges)
    yield "PFEDGE1 id=1 end=1"


def raw():
    yield (
        f"PFRAW2 session=42 id=1 count=1 saturated=0 poll_us=110000 rearm_us=100000 "
        f"resolution_hz=1000000 filter_ns=0 idle_ns=30000000 checksum={checksum([0]):08x}"
    )
    yield (
        "PFRAW2 id=1 arm_us=100000 callback_us=109000 seal_us=110000 gap_us=0 "
        "callbacks=1 queue_losses=0 ignored_callbacks=0 ignored_symbols=0 final=1 timeout=0"
    )
    yield "PFRAW2 id=1 offset=0 words=00000000"
    yield "PFRAW2 id=1 end=1"


def test_gpio_roundtrip_keeps_raw_rmt_unchanged():
    (original,) = read_windows(raw())
    (window,) = read_windows([*records(), *raw()])
    assert window.words == original.words
    assert window.edges == original.edges
    assert window.gpio_edges.encoded_edges == (200001, 200834)
    evidence = window.gpio_edges.as_json()
    assert evidence["cache_off_safe"] is False
    assert evidence["edges"] == [{"time_us": 100000, "level": 1}, {"time_us": 100417, "level": 0}]


@pytest.mark.parametrize("flags", ["clipped=1 raced=0 storm=0", "clipped=1 raced=1 storm=1"])
def test_empty_or_degraded_evidence_is_retained_and_flagged(flags):
    _, _, windows = read_gpio_edges(records((), flags=flags))
    assert windows[(42, 1)].encoded_edges == ()
    assert windows[(42, 1)].metadata["clipped"] == 1


@pytest.mark.parametrize(
    "damage",
    [
        "checksum",
        "missing",
        "duplicate",
        "offset",
        "truncated",
        "announcement",
        "no_health",
        "count",
        "timestamp",
        "unknown",
        "flag",
        "orphan",
    ],
)
def test_gpio_reader_rejects_corrupt_incomplete_or_ambiguous_evidence(damage):
    lines = list(records())
    if damage == "checksum":
        lines[1] = lines[1][:-8] + "00000000"
    elif damage == "missing":
        del lines[3]
    elif damage == "duplicate":
        lines.insert(4, lines[3])
    elif damage == "offset":
        lines[3] = lines[3].replace("offset=0", "offset=1")
    elif damage == "truncated":
        lines.pop()
    elif damage == "announcement":
        lines.pop(0)
    elif damage == "no_health":
        del lines[2]
    elif damage == "count":
        lines[1] = lines[1].replace("count=2", "count=2049")
    elif damage == "timestamp":
        lines = list(records((220003, 200834)))
    elif damage == "unknown":
        lines[2] += " unknown=0"
    elif damage == "flag":
        lines[2] = lines[2].replace("clipped=0", "clipped=2")
    elif damage == "orphan":
        lines[3] = lines[3].replace("id=1", "id=2")
    with pytest.raises(ValueError):
        read_gpio_edges(lines)


def test_missing_entire_gpio_or_rmt_record_is_not_silent():
    with pytest.raises(ValueError, match="missing GPIO"):
        read_windows([next(records()), *raw()])
    with pytest.raises(ValueError, match="orphan GPIO"):
        read_windows(records())


def test_uninstrumented_logs_still_read():
    (window,) = read_windows(raw())
    assert window.gpio_edges is None


@pytest.mark.parametrize(
    "field,replacement", [("request_us=25000", "request_us=25001"), ("snapshot_us=110000", "snapshot_us=109999")]
)
def test_observer_must_belong_to_the_same_rmt_acquisition(field, replacement):
    lines = [line.replace(field, replacement) for line in records()]
    with pytest.raises(ValueError, match="timestamps do not match"):
        read_windows([*lines, *raw()])
