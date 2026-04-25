"""Tests for encoding fireplace state."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.protocol

from custom_components.proflame2.protocol.ecc import derive_ecc_profile
from custom_components.proflame2.protocol.encoder import build_cmd1, build_cmd2, encode_state
from custom_components.proflame2.protocol.models import FireplaceFeatures, FireplaceState, RemoteProfile


def test_encoder_maps_validated_manual_state(remote_profile) -> None:
    """Cmd1/Cmd2 should reflect the validated state mapping."""

    state = FireplaceState(
        power=True,
        flame=1,
        fan=2,
        light=3,
        front=True,
        aux=False,
        thermostat=False,
        cpi=False,
    )

    assert build_cmd1(state) == 0x31
    assert build_cmd2(state) == 0xA1

    frame = encode_state(state, remote_profile)
    assert frame.serial_id == 0x3B3F02
    assert frame.cmd1 == 0x31
    assert frame.cmd2 == 0xA1


def test_encoder_matches_capture_backed_profile(rtl433_samples) -> None:
    """Derived C/D values should reproduce known Err bytes for valid manual states."""

    profile = derive_ecc_profile(
        [(sample["cmd"], sample["err"]) for sample in rtl433_samples["cmd1_samples"]],
        [(sample["cmd"], sample["err"]) for sample in rtl433_samples["cmd2_samples"]],
    )

    remote_profile = RemoteProfile(
        serial_id=0x3B3F02,
        ecc=profile,
        features=FireplaceFeatures(),
    )

    low_state = FireplaceState(power=True, flame=1, fan=0, light=0)
    low_frame = encode_state(low_state, remote_profile)
    assert low_frame.cmd1 == 0x01
    assert low_frame.err1 == 0x76
    assert low_frame.cmd2 == 0x01
    assert low_frame.err2 == 0x39

    high_state = FireplaceState(power=True, flame=6, fan=2, light=3)
    high_frame = encode_state(high_state, remote_profile)
    assert high_frame.cmd1 == 0x31
    assert high_frame.err1 == 0x25
    assert high_frame.cmd2 == 0x26
    assert high_frame.err2 == 0xBC


def test_encoder_rejects_invalid_manual_flame() -> None:
    """Manual mode should not allow flame zero while powered on."""

    with pytest.raises(ValueError, match="between 1 and 6"):
        FireplaceState(power=True, flame=0).validate()


def test_encoder_rejects_native_thermostat_for_v1() -> None:
    """Native thermostat is intentionally suppressed in v1."""

    with pytest.raises(ValueError, match="disabled"):
        FireplaceState(power=True, flame=1, thermostat=True).validate()
