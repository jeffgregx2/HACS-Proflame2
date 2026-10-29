"""Compact, versioned remote-profile exchange format (not an RF packet)."""

from __future__ import annotations

import base64
import binascii
import zlib

from .models import (
    EXTENDED_W4_FIXED_MASK,
    PROTOCOL_VARIANT_EXTENDED_10_WORD,
    ECCProfile,
    ExtendedFrameTemplate,
    FireplaceFeatures,
    RemoteProfile,
)

_FEATURES = ("fan", "light", "front", "aux", "cpi")
_LEGACY_PREFIX = "PF2-1-L-"
_EXTENDED_PREFIX = "PF2-1-E-"


def _bounded_int(value: int, maximum: int = 255) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError("Learning ID profile field is out of range.")
    return value


def encode_learning_id(profile: RemoteProfile) -> str:
    """Encode identity, capabilities and fixed transmit fields, with a CRC32."""

    serial = _bounded_int(profile.serial_id, 0xFFFFFF).to_bytes(3, "big")
    flags = 0
    for bit, name in enumerate(_FEATURES):
        value = getattr(profile.features, name)
        if type(value) is not bool:
            raise ValueError("Learning ID feature flags must be boolean.")
        flags |= int(value) << bit
    payload = serial + bytes([flags])
    if profile.is_extended:
        prefix = _EXTENDED_PREFIX
        template = profile.extended_template
        if template is None:
            raise ValueError("Learning ID requires an extended template.")
        # Normalize away live command bits; only learned fixed W4 flags belong here.
        payload += bytes(
            [
                _bounded_int(template.w4_base) & EXTENDED_W4_FIXED_MASK,
                _bounded_int(template.w6),
                _bounded_int(template.w7),
                _bounded_int(template.w8),
            ]
        )
    else:
        prefix = _LEGACY_PREFIX
        ecc = profile.ecc
        payload += bytes(
            [
                (_bounded_int(ecc.c1, 15) << 4) | _bounded_int(ecc.d1, 15),
                (_bounded_int(ecc.c2, 15) << 4) | _bounded_int(ecc.d2, 15),
            ]
        )
    checksum = zlib.crc32(prefix.encode("ascii") + payload).to_bytes(4, "big")
    return prefix + base64.b32encode(payload + checksum).decode("ascii").rstrip("=")


def decode_learning_id(value: str) -> RemoteProfile:
    """Validate and decode a canonical ID without modifying HA configuration."""

    if not isinstance(value, str):
        raise ValueError("Learning ID must be text.")
    prefix = value[:8]
    if prefix not in (_LEGACY_PREFIX, _EXTENDED_PREFIX):
        raise ValueError("Unsupported Learning ID format or version.")
    encoded = value[8:]
    expected_length = 20 if prefix == _EXTENDED_PREFIX else 16
    if len(encoded) != expected_length:
        raise ValueError("Invalid Learning ID length.")
    try:
        raw = base64.b32decode(encoded + "=" * (-len(encoded) % 8))
    except (binascii.Error, ValueError) as err:
        raise ValueError("Invalid Learning ID encoding.") from err
    payload, checksum = raw[:-4], raw[-4:]
    if checksum != zlib.crc32(prefix.encode("ascii") + payload).to_bytes(4, "big"):
        raise ValueError("Learning ID checksum mismatch.")
    if payload[3] & ~0x1F:
        raise ValueError("Unsupported Learning ID feature flags.")
    features = FireplaceFeatures(**{name: bool(payload[3] & (1 << bit)) for bit, name in enumerate(_FEATURES)})
    serial = int.from_bytes(payload[:3], "big")
    if prefix == _LEGACY_PREFIX:
        profile = RemoteProfile(
            serial, ECCProfile(payload[4] >> 4, payload[4] & 15, payload[5] >> 4, payload[5] & 15), features
        )
    else:
        profile = RemoteProfile(
            serial,
            ECCProfile(0, 0, 0, 0),
            features,
            protocol_variant=PROTOCOL_VARIANT_EXTENDED_10_WORD,
            extended_template=ExtendedFrameTemplate(*payload[4:8]),
        )
    if encode_learning_id(profile) != value:
        raise ValueError("Noncanonical Learning ID encoding.")
    return profile
