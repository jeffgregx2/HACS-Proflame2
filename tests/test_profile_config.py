"""Tests for pure profile configuration normalization helpers."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.protocol

from custom_components.proflame2.const import (
    BACKEND_ESPHOME,
    BACKEND_FAKE,
    BACKEND_YARDSTICK,
    CONF_ACTIVE_LISTENING,
    CONF_AUX,
    CONF_BACKEND_TYPE,
    CONF_C1,
    CONF_C2,
    CONF_CPI,
    CONF_D1,
    CONF_D2,
    CONF_DEBUG_LOGGING,
    CONF_FAN,
    CONF_FIREPLACE_SHORT_NAME,
    CONF_FLAME,
    CONF_FRONT,
    CONF_LIGHT,
    CONF_NAME,
    CONF_POWER,
    CONF_PROFILE_ID,
    CONF_PROFILES,
    CONF_REMOTE_ID,
    available_backend_types,
    available_learning_backend_types,
)
from custom_components.proflame2.profile import (
    InvalidBackendError,
    InvalidNibbleError,
    InvalidRemoteIdError,
    InvalidSavedProfileError,
    build_profile_id,
    default_entry_options,
    default_feature_options,
    normalize_entry_options,
    normalize_feature_options,
    normalize_manual_profile_input,
    normalize_saved_profile_input,
    parse_nibble,
    parse_remote_id,
    sanitize_fireplace_short_name,
)
from custom_components.proflame2.protocol.models import FireplaceFeatures
from custom_components.proflame2.rf.registry import get_backend_definition, normalize_controller_id
from custom_components.proflame2.version import (
    ENABLE_FAKE_BACKEND_ENV,
    build_flavor,
    fake_backend_enabled,
    integration_version,
    is_dev_build,
)


def test_parse_remote_id_accepts_24_bit_hex() -> None:
    """Remote IDs should normalize from user-friendly hex input."""

    assert parse_remote_id("3b3f02") == 0x3B3F02
    assert parse_remote_id("0x3B3F02") == 0x3B3F02


def test_parse_remote_id_rejects_invalid_values() -> None:
    """Remote IDs must stay within the 24-bit Proflame range."""

    try:
        parse_remote_id("zzzzzz")
    except InvalidRemoteIdError:
        pass
    else:
        raise AssertionError("Expected invalid hex remote ID to raise InvalidRemoteIdError.")

    try:
        parse_remote_id("1000000")
    except InvalidRemoteIdError:
        pass
    else:
        raise AssertionError("Expected oversized remote ID to raise InvalidRemoteIdError.")


def test_parse_nibble_rejects_invalid_values() -> None:
    """C/D values must remain 4-bit integers."""

    assert parse_nibble(15) == 15
    assert parse_nibble("0xA") == 10

    try:
        parse_nibble(16)
    except InvalidNibbleError:
        pass
    else:
        raise AssertionError("Expected nibble > 15 to raise InvalidNibbleError.")


def test_normalize_manual_profile_input_splits_data_and_options() -> None:
    """Permanent identity belongs in data and feature flags in options."""

    normalized = normalize_manual_profile_input(
        {
            "name": "Living Room Fireplace",
            CONF_BACKEND_TYPE: "yardstick",
            CONF_REMOTE_ID: "3b3f02",
            CONF_C1: "5",
            CONF_D1: "7",
            CONF_C2: "1",
            CONF_D2: "8",
            CONF_FAN: True,
            CONF_LIGHT: True,
            CONF_FRONT: False,
            CONF_AUX: False,
            CONF_CPI: True,
            CONF_FIREPLACE_SHORT_NAME: " living ",
        }
    )

    assert normalized.data == {
        "name": "Living Room Fireplace",
        CONF_BACKEND_TYPE: "yardstick",
        CONF_REMOTE_ID: 0x3B3F02,
        CONF_C1: 5,
        CONF_D1: 7,
        CONF_C2: 1,
        CONF_D2: 8,
    }
    assert normalized.options == {
        CONF_FAN: True,
        CONF_LIGHT: True,
        CONF_FRONT: False,
        CONF_AUX: False,
        CONF_CPI: True,
        CONF_DEBUG_LOGGING: False,
        CONF_ACTIVE_LISTENING: False,
        CONF_FIREPLACE_SHORT_NAME: "LIVING",
        CONF_PROFILES: {},
    }


def test_normalize_manual_profile_input_uses_concrete_controller_id() -> None:
    """Legacy generic ESPHome ids should normalize to the concrete controller id."""

    normalized = normalize_manual_profile_input(
        {
            "name": "Bench Fireplace",
            CONF_BACKEND_TYPE: "esphome",
            CONF_REMOTE_ID: "3b3f02",
            CONF_C1: "5",
            CONF_D1: "7",
            CONF_C2: "1",
            CONF_D2: "8",
            CONF_FAN: True,
            CONF_LIGHT: True,
            CONF_FRONT: False,
            CONF_AUX: False,
            CONF_CPI: False,
        }
    )

    assert normalized.data[CONF_BACKEND_TYPE] == BACKEND_ESPHOME


def test_controller_id_normalization_rejects_invalid_values() -> None:
    """Controller ids should sanitize to a strict printable lowercase token."""

    assert normalize_controller_id("  LilyGo_CC1101 ") == BACKEND_ESPHOME
    assert normalize_controller_id("esphome") == BACKEND_ESPHOME

    with pytest.raises(ValueError, match="must not be empty"):
        normalize_controller_id("   ")
    with pytest.raises(ValueError, match="only lowercase letters"):
        normalize_controller_id("lilygo cc1101")
    with pytest.raises(ValueError, match="only printable characters"):
        normalize_controller_id("yardstick\x00")


def test_lilygo_controller_registry_id_is_concrete() -> None:
    """The LilyGO controller should not expose a generic ESPHome identity token."""

    definition = get_backend_definition(BACKEND_ESPHOME)

    assert definition.controller_id == BACKEND_ESPHOME
    assert definition.controller_id == "lilygo_cc1101"
    assert definition.requires_esphome_entry is True


def test_non_esphome_backends_do_not_require_esphome_link() -> None:
    """Only ESPHome-backed controllers should request an ESPHome config entry."""

    assert get_backend_definition(BACKEND_YARDSTICK).requires_esphome_entry is False
    assert get_backend_definition(BACKEND_FAKE).requires_esphome_entry is False


def test_feature_options_default_as_expected() -> None:
    """Feature defaults should match the integration design."""

    assert default_feature_options() == {
        CONF_FAN: True,
        CONF_LIGHT: True,
        CONF_FRONT: False,
        CONF_AUX: False,
        CONF_CPI: False,
    }
    assert normalize_feature_options({}) == default_feature_options()
    assert default_entry_options() == {
        CONF_FAN: True,
        CONF_LIGHT: True,
        CONF_FRONT: False,
        CONF_AUX: False,
        CONF_CPI: False,
        CONF_DEBUG_LOGGING: False,
        CONF_ACTIVE_LISTENING: False,
        CONF_FIREPLACE_SHORT_NAME: "---",
        CONF_PROFILES: {},
    }


def test_normalize_saved_profile_input_builds_complete_state() -> None:
    """Saved profiles should normalize to a complete desired state."""

    profile = normalize_saved_profile_input(
        {
            CONF_NAME: "Movie Night",
            CONF_POWER: True,
            CONF_FLAME: 1,
            CONF_FAN: 2,
            CONF_LIGHT: 3,
        },
        features=FireplaceFeatures(fan=True, light=True),
    )

    assert profile == {
        CONF_PROFILE_ID: "movie_night",
        CONF_NAME: "Movie Night",
        CONF_POWER: True,
        CONF_FLAME: 1,
        CONF_FAN: 2,
        CONF_LIGHT: 3,
        CONF_FRONT: False,
        CONF_AUX: False,
        CONF_CPI: False,
    }


def test_invalid_saved_profile_is_rejected() -> None:
    """Saved profiles should use the same atomic validation rules as services."""

    with pytest.raises(InvalidSavedProfileError, match="flame must be between 1 and 6"):
        normalize_saved_profile_input(
            {
                CONF_NAME: "Too Hot",
                CONF_POWER: True,
                CONF_FLAME: 7,
            },
            features=FireplaceFeatures(),
        )


def test_normalize_entry_options_preserves_saved_profiles() -> None:
    """Config-entry options should carry feature flags and saved profiles together."""

    normalized = normalize_entry_options(
        {
            CONF_FAN: False,
            CONF_LIGHT: True,
            CONF_FRONT: False,
            CONF_AUX: False,
            CONF_CPI: False,
            CONF_PROFILES: {
                "minimum_flame": {
                    CONF_NAME: "Minimum Flame",
                    CONF_POWER: True,
                    CONF_FLAME: 1,
                    CONF_LIGHT: 2,
                }
            },
        },
        features=FireplaceFeatures(fan=False, light=True),
    )

    assert normalized[CONF_FAN] is False
    assert normalized[CONF_PROFILES]["minimum_flame"] == {
        CONF_PROFILE_ID: "minimum_flame",
        CONF_NAME: "Minimum Flame",
        CONF_POWER: True,
        CONF_FLAME: 1,
        CONF_FAN: 0,
        CONF_LIGHT: 2,
        CONF_FRONT: False,
        CONF_AUX: False,
        CONF_CPI: False,
    }


def test_build_profile_id_slugifies_display_name() -> None:
    """Saved profile ids should be stable slug values."""

    assert build_profile_id("Evening Relax") == "evening_relax"


def test_build_metadata_defaults_to_prod_with_fake_disabled(monkeypatch) -> None:
    """Released builds should default to production with Fake hidden."""

    monkeypatch.delenv("PROFLAME2_VERSION", raising=False)
    monkeypatch.delenv("PROFLAME2_BUILD", raising=False)
    monkeypatch.delenv(ENABLE_FAKE_BACKEND_ENV, raising=False)

    assert integration_version() == "0.5.1"
    assert build_flavor() == "prod"
    assert is_dev_build() is False
    assert fake_backend_enabled() is False
    assert available_backend_types() == (BACKEND_YARDSTICK, BACKEND_ESPHOME)
    assert available_learning_backend_types() == (BACKEND_YARDSTICK, BACKEND_ESPHOME)


def test_fake_backend_requires_explicit_opt_in(monkeypatch) -> None:
    """Test fixtures may opt into Fake without exposing it by default."""

    monkeypatch.delenv("PROFLAME2_VERSION", raising=False)
    monkeypatch.setenv("PROFLAME2_BUILD", "dev")
    monkeypatch.setenv(ENABLE_FAKE_BACKEND_ENV, "true")

    assert build_flavor() == "dev"
    assert fake_backend_enabled() is True
    assert available_backend_types() == (
        BACKEND_YARDSTICK,
        BACKEND_ESPHOME,
        BACKEND_FAKE,
    )
    assert available_learning_backend_types() == (
        BACKEND_YARDSTICK,
        BACKEND_ESPHOME,
        BACKEND_FAKE,
    )


def test_prod_build_hides_fake_backend(monkeypatch) -> None:
    """Production builds should expose only vetted backends."""

    monkeypatch.setenv("PROFLAME2_BUILD", "prod")
    monkeypatch.setenv(ENABLE_FAKE_BACKEND_ENV, "true")

    assert build_flavor() == "prod"
    assert is_dev_build() is False
    assert available_backend_types() == (BACKEND_YARDSTICK, BACKEND_ESPHOME)
    assert available_learning_backend_types() == (BACKEND_YARDSTICK, BACKEND_ESPHOME)

    with pytest.raises(InvalidBackendError):
        normalize_manual_profile_input(
            {
                "name": "Living Room Fireplace",
                CONF_BACKEND_TYPE: "fake",
                CONF_REMOTE_ID: "3b3f02",
                CONF_C1: "5",
                CONF_D1: "7",
                CONF_C2: "1",
                CONF_D2: "8",
                CONF_FAN: True,
                CONF_LIGHT: True,
                CONF_FRONT: False,
                CONF_AUX: False,
                CONF_CPI: False,
            }
        )


def test_fireplace_short_name_sanitization_defaults_and_truncates() -> None:
    """Display short names should be normalized consistently for the LilyGO header."""

    assert sanitize_fireplace_short_name("") == "---"
    assert sanitize_fireplace_short_name("  den  ") == "DEN"
    assert sanitize_fireplace_short_name("living room") == "LIVING"


def test_normalize_entry_options_preserves_short_name() -> None:
    """Config entry options should carry the sanitized short name."""

    normalized = normalize_entry_options({CONF_FIREPLACE_SHORT_NAME: " patio "})

    assert normalized[CONF_FIREPLACE_SHORT_NAME] == "PATIO"
