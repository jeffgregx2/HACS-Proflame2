"""Learning ID wire-format and transmit-profile preservation tests."""

from __future__ import annotations

import base64
import itertools
import zlib
from dataclasses import replace

import pytest

from custom_components.proflame2.protocol.encoder import encode_packet
from custom_components.proflame2.protocol.learning_id import decode_learning_id, encode_learning_id
from custom_components.proflame2.protocol.models import (
    PROTOCOL_VARIANT_EXTENDED_10_WORD,
    ECCProfile,
    ExtendedFrameTemplate,
    FireplaceFeatures,
    FireplaceState,
    RemoteProfile,
)

LEGACY = RemoteProfile(0x123456, ECCProfile(5, 7, 1, 8))
EXTENDED = RemoteProfile(
    0x123456,
    ECCProfile(0, 0, 0, 0),
    protocol_variant=PROTOCOL_VARIANT_EXTENDED_10_WORD,
    extended_template=ExtendedFrameTemplate(0x80, 0x42, 0xC8, 0x19),
)


def _checked_id(prefix: str, payload: bytes) -> str:
    """Build intentionally invalid payloads with a valid outer checksum."""
    crc = zlib.crc32(prefix.encode("ascii") + payload).to_bytes(4, "big")
    return prefix + base64.b32encode(payload + crc).decode("ascii").rstrip("=")


@pytest.mark.parametrize(
    ("profile", "expected", "payload"),
    [
        (LEGACY, "PF2-1-L-CI2FMVYYTNVRS7Q", "1234565718"),
        (EXTENDED, "PF2-1-E-CI2FNACCZAMYKTI4W4", "1234568042c819"),
    ],
)
def test_documented_vectors(profile, expected, payload) -> None:
    """Keep the documented encoding stable, including packing and byte order."""
    assert encode_learning_id(profile) == expected
    assert decode_learning_id(expected) == profile
    assert _checked_id(expected[:8], bytes.fromhex(payload)) == expected
    assert len(expected) <= 28


@pytest.mark.parametrize("profile", [LEGACY, EXTENDED])
@pytest.mark.parametrize("flags", itertools.product((False, True), repeat=5))
def test_feature_choices_never_change_learning_id(profile, flags) -> None:
    configured = replace(profile, features=FireplaceFeatures(*flags))
    assert encode_learning_id(configured) == encode_learning_id(profile)
    assert decode_learning_id(encode_learning_id(configured)) == profile


@pytest.mark.parametrize("profile", [LEGACY, EXTENDED])
@pytest.mark.parametrize("serial", [0, 1, 0xFFFFFF])
def test_serial_boundaries(profile, serial) -> None:
    profile = replace(profile, serial_id=serial)
    assert decode_learning_id(encode_learning_id(profile)) == profile


@pytest.mark.parametrize("profile", [LEGACY, EXTENDED])
@pytest.mark.parametrize("serial", [-1, 0x1000000, True, 1.5])
def test_invalid_serial_rejected(profile, serial) -> None:
    with pytest.raises(ValueError, match="out of range"):
        encode_learning_id(replace(profile, serial_id=serial))


@pytest.mark.parametrize("field", ["c1", "d1", "c2", "d2"])
@pytest.mark.parametrize("value", [-1, 16, True, 1.5])
def test_invalid_legacy_nibbles_rejected(field, value) -> None:
    with pytest.raises(ValueError, match="out of range"):
        encode_learning_id(replace(LEGACY, ecc=replace(LEGACY.ecc, **{field: value})))


def test_legacy_nibble_boundaries() -> None:
    for nibble in (0, 15):
        profile = replace(LEGACY, ecc=ECCProfile(nibble, nibble, nibble, nibble))
        assert decode_learning_id(encode_learning_id(profile)) == profile


def test_export_does_not_inspect_local_feature_configuration() -> None:
    assert encode_learning_id(replace(LEGACY, features=None)) == encode_learning_id(LEGACY)


@pytest.mark.parametrize("profile", [LEGACY, EXTENDED])
def test_transmit_frames_preserved(profile) -> None:
    """Copied IDs must reproduce every supported manual command combination."""
    profile = replace(profile, features=FireplaceFeatures(True, True, True, True, True))
    restored = replace(decode_learning_id(encode_learning_id(profile)), features=profile.features)
    for flame, fan, light in itertools.product(range(1, 7), range(7), range(8)):
        state = FireplaceState(True, flame=flame, fan=fan, light=light, front=True, aux=True, cpi=True)
        assert encode_packet(state, restored).frame == encode_packet(state, profile).frame
    off = FireplaceState(False, flame=0)
    assert encode_packet(off, restored).frame == encode_packet(off, profile).frame


def test_extended_normalizes_dynamic_w4_and_ignores_unused_ecc() -> None:
    profile = replace(
        EXTENDED, ecc=ECCProfile(5, 7, 1, 8), extended_template=ExtendedFrameTemplate(0xF3, 0x42, 0xC8, 0x19)
    )
    restored = decode_learning_id(encode_learning_id(profile))
    assert restored.extended_template == ExtendedFrameTemplate(0x80, 0x42, 0xC8, 0x19)
    state = FireplaceState(True, flame=6, light=4)
    assert encode_packet(state, restored).frame == encode_packet(state, profile).frame


@pytest.mark.parametrize("profile", [LEGACY, EXTENDED])
def test_every_character_is_checksum_protected(profile) -> None:
    value = encode_learning_id(profile)
    for index in range(8, len(value)):
        replacement = "A" if value[index] != "A" else "B"
        with pytest.raises(ValueError):
            decode_learning_id(value[:index] + replacement + value[index + 1 :])
    with pytest.raises(ValueError):
        decode_learning_id(value.replace("-L-", "-E-") if not profile.is_extended else value.replace("-E-", "-L-"))


@pytest.mark.parametrize(
    "value",
    [
        None,
        123,
        "",
        "PF2-1-L-CI2FMA2XDBVBZFK7",
        "PF2-1-X-CI2FMA2XDBVBZFK7",
        "PF2-1-L-CI2FMA2XDBVBZFK",
        "PF2-1-L-CI2FMVYYTNVRS7Q=",
        "PF2-1-L-ci2fma2xdbvbzfk7",
        "PF2-1-L-!I2FMA2XDBVBZFK7",
        "PF2-1-L-\u00e9I2FMA2XDBVBZFK7",
    ],
)
def test_invalid_format_rejected(value) -> None:
    with pytest.raises(ValueError):
        decode_learning_id(value)


@pytest.mark.parametrize("old_id", ["PF2-1-L-CI2FMA2XDBVBZFK7", "PF2-1-E-CI2FMA4AILEBTPLKVK4Q"])
def test_experimental_feature_bearing_ids_require_regeneration(old_id) -> None:
    with pytest.raises(ValueError, match="length"):
        decode_learning_id(old_id)


def test_noncanonical_w4_and_base32_padding_bits_rejected() -> None:
    with pytest.raises(ValueError, match="Noncanonical"):
        decode_learning_id(_checked_id("PF2-1-E-", bytes.fromhex("1234568142c819")))
    value = encode_learning_id(EXTENDED)
    # The last Base32 symbol has two unused zero bits; changing them is not canonical.
    with pytest.raises(ValueError, match="Noncanonical"):
        decode_learning_id(value[:-1] + "5")


def test_unknown_exchange_version_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        decode_learning_id(encode_learning_id(LEGACY).replace("PF2-1-", "PF2-9-"))
