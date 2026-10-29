"""Buffered logs must expose data loss and preserve arm-call uncertainty."""

import pytest

from tools.waveform.waveform_diagnostics import checksum, read_windows


def records():
    return [
        "PFSESSION session=42 state=armed duration_ms=90000",
        "PFBATCH1 session=42 slots=2 retained_limit=32 deferred_export=1",
        "PFSTOP1 session=42 ended_us=120000",
        "PFBOUND1 session=42 id=1 arm_before_us=100000 arm_after_us=100010 gpio_anchor_us=100000 gpio_anchor_observed=0",
        f"PFRAW2 session=42 id=1 count=1 saturated=0 poll_us=110000 rearm_us=100000 resolution_hz=1000000 filter_ns=0 idle_ns=30000000 checksum={checksum([0]):08x}",
        "PFRAW2 id=1 arm_us=100000 callback_us=109000 seal_us=110000 gap_us=0 callbacks=1 queue_losses=0 ignored_callbacks=0 ignored_symbols=0 final=1 timeout=0",
        "PFRAW2 id=1 offset=0 words=00000000",
        "PFRAW2 id=1 end=1",
        "PFACQ1 session=42 pool_losses=0 queue_losses=0 callback_losses=0 retention_losses=0 driver_error=0 stack_free_bytes=4096 ended_us=120000",
        "PFSESSION session=42 state=paused exports=1 suppressed=0 end_us=120000",
    ]


def test_buffered_import_keeps_arm_bounds():
    (window,) = read_windows(records(), require_complete_sessions=True)
    assert window.metadata["arm_after_us"] == 100010
    assert window.metadata["gpio_anchor_observed"] == 0
    assert window.words == (0,)


@pytest.mark.parametrize(
    "field", ["pool_losses", "queue_losses", "callback_losses", "retention_losses", "driver_error"]
)
def test_loss_is_rejected_even_if_closure_claims_success(field):
    damaged = [line.replace(f"{field}=0", f"{field}=1") for line in records()]
    with pytest.raises(ValueError):
        read_windows(damaged, require_complete_sessions=True)


@pytest.mark.parametrize("remove", ["PFBATCH1", "PFSTOP1", "PFBOUND1", "PFACQ1"])
def test_required_buffered_records_cannot_disappear(remove):
    damaged = [line for line in records() if not line.startswith(remove)]
    with pytest.raises(ValueError):
        read_windows(damaged, require_complete_sessions=True)


@pytest.mark.parametrize(
    "old,new",
    [
        ("arm_after_us=100010", "arm_after_us=99999"),
        ("arm_before_us=100000", "arm_before_us=100001"),
        ("ended_us=120000", "ended_us=100001"),
        ("gpio_anchor_us=100000", "gpio_anchor_us=120001"),
        ("gpio_anchor_observed=0", "gpio_anchor_observed=2"),
    ],
)
def test_invalid_timing_is_rejected(old, new):
    with pytest.raises(ValueError):
        read_windows([line.replace(old, new) for line in records()], require_complete_sessions=True)
