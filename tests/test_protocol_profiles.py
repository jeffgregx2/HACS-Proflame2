"""Tests for internal Proflame2 protocol profile definitions."""

from __future__ import annotations

import json

from custom_components.proflame2.protocol.profiles import (
    DEFAULT_PROTOCOL_PROFILE,
    GENERIC_PROFLAME2_315,
    ProtocolFeature,
    ProtocolModulation,
    get_protocol_profile,
)


def test_default_protocol_profile_is_generic_315() -> None:
    assert DEFAULT_PROTOCOL_PROFILE is GENERIC_PROFLAME2_315
    assert DEFAULT_PROTOCOL_PROFILE.id == "generic_proflame2_315"
    assert DEFAULT_PROTOCOL_PROFILE.label == "Generic Proflame2 / 315 MHz"
    assert DEFAULT_PROTOCOL_PROFILE.frequency_hz == 314_973_000
    assert DEFAULT_PROTOCOL_PROFILE.modulation == ProtocolModulation.ASK_OOK
    assert DEFAULT_PROTOCOL_PROFILE.data_rate_bps == 2_400
    assert DEFAULT_PROTOCOL_PROFILE.tx_repeat_count == 5
    assert DEFAULT_PROTOCOL_PROFILE.default_rx_frequency_hz == 315_000_000


def test_generic_protocol_profile_supported_features_are_code_defined() -> None:
    assert DEFAULT_PROTOCOL_PROFILE.supported_features == frozenset(
        {
            ProtocolFeature.FLAME,
            ProtocolFeature.FAN,
            ProtocolFeature.LIGHT,
            ProtocolFeature.FRONT,
            ProtocolFeature.AUX,
            ProtocolFeature.CPI,
        }
    )


def test_lookup_returns_registered_profile() -> None:
    assert get_protocol_profile("generic_proflame2_315") is GENERIC_PROFLAME2_315


def test_protocol_enum_values_preserve_string_semantics() -> None:
    for value in (ProtocolModulation.ASK_OOK, *ProtocolFeature):
        assert isinstance(value, str)
        assert str(value) == value.value
        assert f"{value}" == value.value
        assert format(value, ">10") == format(value.value, ">10")
        assert json.loads(json.dumps(value)) == value.value
