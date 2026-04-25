"""Tests for ECC helper behavior."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.protocol

from custom_components.proflame2.protocol.ecc import (
    combine_cd,
    derive_cd_candidates,
    derive_ecc_profile,
    derive_stable_cd,
    derive_unique_cd,
    err1_for,
    err2_for,
    split_cd,
)


def test_cmd1_captures_match_real_ecc(remote_profile, rtl433_samples) -> None:
    """Known Cmd1 captures should match the SmartFire-derived ECC profile."""

    for sample in rtl433_samples["cmd1_samples"]:
        assert err1_for(sample["cmd"], remote_profile.ecc) == sample["err"]


def test_cmd2_captures_match_real_ecc(remote_profile, rtl433_samples) -> None:
    """Known Cmd2 captures should match the SmartFire-derived ECC profile."""

    for sample in rtl433_samples["cmd2_samples"]:
        assert err2_for(sample["cmd"], remote_profile.ecc) == sample["err"]


def test_single_capture_derives_unique_cd() -> None:
    """A single observed command/Err pair should resolve to one CD byte here."""

    assert derive_cd_candidates(0x01, 0x76) == (0x57,)
    assert derive_unique_cd(0x16, 0xEF) == 0x18


def test_capture_sets_derive_stable_cd_values(rtl433_samples) -> None:
    """All captures in a command group should converge on one stable CD byte."""

    cmd1_samples = [(sample["cmd"], sample["err"]) for sample in rtl433_samples["cmd1_samples"]]
    cmd2_samples = [(sample["cmd"], sample["err"]) for sample in rtl433_samples["cmd2_samples"]]

    assert derive_stable_cd(cmd1_samples) == 0x57
    assert derive_stable_cd(cmd2_samples) == 0x18


def test_profile_can_be_derived_from_capture_sets(remote_profile, rtl433_samples) -> None:
    """Cmd1 and Cmd2 capture sets should build the full ECC profile."""

    profile = derive_ecc_profile(
        [(sample["cmd"], sample["err"]) for sample in rtl433_samples["cmd1_samples"]],
        [(sample["cmd"], sample["err"]) for sample in rtl433_samples["cmd2_samples"]],
    )

    assert profile == remote_profile.ecc


def test_split_and_combine_cd_round_trip() -> None:
    """CD helpers should preserve the underlying nibbles."""

    assert split_cd(0x57) == (0x05, 0x07)
    assert combine_cd(0x05, 0x07) == 0x57


def test_derive_unique_cd_raises_for_no_match() -> None:
    """Contradictory observations should fail stable derivation clearly."""

    with pytest.raises(ValueError, match="No stable"):
        derive_stable_cd([(0x01, 0x76), (0x01, 0x77)])
