"""Tests for encoding fireplace state."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.protocol

from custom_components.proflame2.protocol.decoder import decode_state
from custom_components.proflame2.protocol.ecc import build_extended_integrity_words, derive_ecc_profile
from custom_components.proflame2.protocol.encoder import build_cmd1, build_cmd2, encode_state
from custom_components.proflame2.protocol.models import (
    PROTOCOL_VARIANT_EXTENDED_10_WORD,
    ECCProfile,
    ExtendedFrameTemplate,
    FireplaceFeatures,
    FireplaceState,
    RemoteProfile,
)
from custom_components.proflame2.rf.waveform import build_transmission_plan, frame_to_air_bytes


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


def test_extended_encoder_builds_manual_ten_word_frame() -> None:
    """Extended manual TX uses a learned template and generated integrity words."""

    profile = RemoteProfile(
        serial_id=0x08E905,
        ecc=ECCProfile(c1=0, d1=0, c2=0, d2=0),
        features=FireplaceFeatures(),
        protocol_variant=PROTOCOL_VARIANT_EXTENDED_10_WORD,
        extended_template=ExtendedFrameTemplate(w4_base=0x80, w6=0x15, w7=0xC8, w8=0x00),
    )

    frame = encode_state(FireplaceState(power=True, flame=5), profile)
    w9, w10 = build_extended_integrity_words(0x81, 0x05, 0x15, 0xC8, 0x00)

    assert frame.wire_words == (0x08, 0xE9, 0x05, 0x81, 0x05, 0x15, 0xC8, 0x00, w9, w10)
    assert frame.is_extended

    plan = build_transmission_plan(frame)
    assert plan.air_payload_bit_length == 260
    assert len(frame_to_air_bytes(frame)) == 35
    assert decode_state(frame, profile) == FireplaceState(power=True, flame=5)

    corrupt_frame = frame.__class__(
        serial_id=frame.serial_id,
        cmd1=frame.cmd1,
        err1=frame.err1,
        cmd2=frame.cmd2,
        err2=frame.err2,
        extension_words=(frame.extension_words[0], frame.extension_words[1] ^ 0x01, frame.extension_words[2]),
    )
    with pytest.raises(ValueError, match="integrity"):
        decode_state(corrupt_frame, profile)
