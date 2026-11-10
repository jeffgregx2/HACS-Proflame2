"""Pilot semantics at the shared HA/helper rtl_433 boundary, using synthetic frames."""

from __future__ import annotations

import pytest

from custom_components.proflame2.protocol.ecc import build_extended_integrity_words
from custom_components.proflame2.protocol.rtl433 import normalize_rtl433_record


def record(pilot, raw=True):
    w4 = 0x21 | (pilot << 7)
    w9, w10 = build_extended_integrity_words(0x123456, w4, 3, 20, 200, 0)
    words = (0x12, 0x34, 0x56, w4, 3, 20, 200, 0, w9, w10)
    result = dict(model="Proflame2-Remote", protocol_variant="extended_10_word")
    if raw:
        result["raw_words"] = bytes(words).hex()
    else:
        result.update(id=0x123456, cmd1=w4, cmd2=3, w6=20, w7=200, w8=0, w9=w9, w10=w10)
    return result


@pytest.mark.parametrize("pilot", [0, 1])
@pytest.mark.parametrize("raw", [False, True])
@pytest.mark.parametrize("reported", ["missing", -1, "matching"])
def test_extended_current_and_historical_pilot(pilot, raw, reported):
    row = record(pilot, raw)
    if reported != "missing":
        row["pilot"] = pilot if reported == "matching" else reported
    normalized = normalize_rtl433_record(row)
    assert normalized.frame.cmd1 >> 7 == pilot
    assert normalized.legacy_compatibility is False


@pytest.mark.parametrize("pilot", [0, 1])
@pytest.mark.parametrize("raw", [False, True])
def test_extended_conflicting_pilot_is_rejected(pilot, raw):
    row = record(pilot, raw)
    row["pilot"] = 1 - pilot
    with pytest.raises(ValueError, match="disagree.*pilot"):
        normalize_rtl433_record(row)


@pytest.mark.parametrize("value", [True, False, -2, 2, 0.0, 1.0, "0", "1", "-1", None, []])
def test_extended_pilot_requires_an_allowed_integer(value):
    row = record(1)
    row["pilot"] = value
    with pytest.raises(ValueError, match="Extended pilot"):
        normalize_rtl433_record(row)


@pytest.mark.parametrize("pilot", [0, 1])
def test_matching_pilot_does_not_bypass_frame_integrity(pilot):
    row = record(pilot, raw=False)
    row["pilot"] = pilot
    row["w10"] ^= 1
    with pytest.raises(ValueError):
        normalize_rtl433_record(row)
