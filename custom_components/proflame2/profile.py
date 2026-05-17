"""Pure helpers for normalizing and validating fireplace profile configuration."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .const import (
    BACKEND_ESPHOME,
    CONF_ACTIVE_LISTENING,
    CONF_AUX,
    CONF_BACKEND_TYPE,
    CONF_C1,
    CONF_C2,
    CONF_CPI,
    CONF_D1,
    CONF_D2,
    CONF_DEBUG_LOGGING,
    CONF_ESPHOME_ENTRY_ID,
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
    DEFAULT_DEBUG_LOGGING,
    DEFAULT_FEATURE_OPTIONS,
    DEFAULT_FIREPLACE_SHORT_NAME,
    FEATURE_OPTION_KEYS,
    MAX_FIREPLACE_SHORT_NAME_LENGTH,
    available_backend_types,
)
from .control import StateValidationError, build_requested_state
from .protocol.models import FireplaceFeatures
from .rf.registry import normalize_controller_id


class InvalidRemoteIdError(ValueError):
    """Raised when a remote ID does not represent a 24-bit hex value."""


class InvalidNibbleError(ValueError):
    """Raised when a C/D value is outside the valid nibble range."""


class InvalidBackendError(ValueError):
    """Raised when the requested backend is unsupported."""


class InvalidProfileNameError(ValueError):
    """Raised when a saved profile name is missing or invalid."""


class DuplicateProfileIdError(ValueError):
    """Raised when two saved profiles would use the same internal id."""


class InvalidSavedProfileError(ValueError):
    """Raised when a saved profile does not contain a valid desired state."""


@dataclass(frozen=True)
class ManualProfileInput:
    """Normalized profile data and feature options from config flow input."""

    data: dict[str, Any]
    options: dict[str, Any]


def parse_remote_id(value: str | int) -> int:
    """Parse a remote ID as a normalized 24-bit integer."""

    if isinstance(value, int):
        remote_id = value
    else:
        text = str(value).strip().lower()
        if text.startswith("0x"):
            text = text[2:]
        if not text:
            raise InvalidRemoteIdError("Remote ID is required.")
        if any(character not in "0123456789abcdef" for character in text):
            raise InvalidRemoteIdError("Remote ID must be a hexadecimal value.")
        remote_id = int(text, 16)

    if not 0 <= remote_id <= 0xFFFFFF:
        raise InvalidRemoteIdError("Remote ID must fit in 24 bits.")
    return remote_id


def parse_nibble(value: str | int) -> int:
    """Parse a nibble value as an integer in the range 0..15."""

    if isinstance(value, int):
        nibble = value
    else:
        text = str(value).strip().lower()
        base = 16 if text.startswith("0x") else 10
        try:
            nibble = int(text, base)
        except ValueError as exc:
            raise InvalidNibbleError("C/D values must be integers in the range 0..15.") from exc

    if not 0 <= nibble <= 0x0F:
        raise InvalidNibbleError("C/D values must be integers in the range 0..15.")
    return nibble


def sanitize_fireplace_short_name(value: Any) -> str:
    """Normalize the short name shown on the LilyGO display."""

    text = " ".join(str(value or "").split()).strip()
    if not text:
        return DEFAULT_FIREPLACE_SHORT_NAME
    return text[:MAX_FIREPLACE_SHORT_NAME_LENGTH].upper()


def default_feature_options() -> dict[str, bool]:
    """Return the default feature flag options."""

    return dict(DEFAULT_FEATURE_OPTIONS)


def default_entry_options() -> dict[str, Any]:
    """Return default config-entry options including saved profiles."""

    return {
        **default_feature_options(),
        CONF_DEBUG_LOGGING: DEFAULT_DEBUG_LOGGING,
        CONF_ACTIVE_LISTENING: False,
        CONF_FIREPLACE_SHORT_NAME: DEFAULT_FIREPLACE_SHORT_NAME,
        CONF_PROFILES: {},
    }


def normalize_feature_options(user_input: dict[str, Any]) -> dict[str, bool]:
    """Normalize user-selected feature flags into config entry options."""

    options = default_feature_options()
    for key in FEATURE_OPTION_KEYS:
        if key in user_input:
            options[key] = bool(user_input[key])
    return options


def normalize_entry_options(
    raw_options: dict[str, Any] | None,
    *,
    features: FireplaceFeatures | None = None,
) -> dict[str, Any]:
    """Normalize config-entry options to feature flags plus saved profiles."""

    normalized = default_entry_options()
    if raw_options is None:
        return normalized

    normalized.update(normalize_feature_options(raw_options))
    normalized[CONF_DEBUG_LOGGING] = bool(raw_options.get(CONF_DEBUG_LOGGING, DEFAULT_DEBUG_LOGGING))
    if CONF_ACTIVE_LISTENING in raw_options:
        normalized[CONF_ACTIVE_LISTENING] = bool(raw_options[CONF_ACTIVE_LISTENING])
    else:
        normalized[CONF_ACTIVE_LISTENING] = raw_options.get(CONF_BACKEND_TYPE) == BACKEND_ESPHOME
    normalized[CONF_FIREPLACE_SHORT_NAME] = sanitize_fireplace_short_name(
        raw_options.get(CONF_FIREPLACE_SHORT_NAME, DEFAULT_FIREPLACE_SHORT_NAME)
    )
    normalized[CONF_PROFILES] = normalize_profiles(
        raw_options.get(CONF_PROFILES, {}),
        features=features or fireplace_features_from_options(normalized),
    )
    return normalized


def normalize_manual_profile_input(user_input: dict[str, Any]) -> ManualProfileInput:
    """Normalize config-flow input into config entry data and options."""

    backend_type = normalize_controller_id(user_input[CONF_BACKEND_TYPE])
    if backend_type not in available_backend_types():
        raise InvalidBackendError(f"Unsupported backend type: {backend_type}")

    data = {
        "name": str(user_input["name"]).strip(),
        CONF_BACKEND_TYPE: backend_type,
        CONF_REMOTE_ID: parse_remote_id(user_input[CONF_REMOTE_ID]),
        CONF_C1: parse_nibble(user_input[CONF_C1]),
        CONF_D1: parse_nibble(user_input[CONF_D1]),
        CONF_C2: parse_nibble(user_input[CONF_C2]),
        CONF_D2: parse_nibble(user_input[CONF_D2]),
    }
    linked_esphome_entry_id = user_input.get(CONF_ESPHOME_ENTRY_ID)
    if isinstance(linked_esphome_entry_id, str) and linked_esphome_entry_id:
        data[CONF_ESPHOME_ENTRY_ID] = linked_esphome_entry_id
    return ManualProfileInput(
        data=data,
        options=normalize_entry_options(user_input),
    )


def remote_id_as_hex(remote_id: int) -> str:
    """Format a remote ID as a fixed-width lowercase hex string."""

    return f"{remote_id:06x}"


def fireplace_features_from_options(options: dict[str, Any] | None) -> FireplaceFeatures:
    """Build feature flags from config-entry options."""

    normalized = normalize_feature_options(options or {})
    return FireplaceFeatures(
        fan=normalized[CONF_FAN],
        light=normalized[CONF_LIGHT],
        front=normalized[CONF_FRONT],
        aux=normalized[CONF_AUX],
        cpi=normalized[CONF_CPI],
    )


def build_profile_id(name: str) -> str:
    """Generate a stable internal profile id from a display name."""

    text = str(name).strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    if not slug:
        raise InvalidProfileNameError("Profile name is required.")
    return slug


def normalize_profiles(
    raw_profiles: dict[str, Any] | None,
    *,
    features: FireplaceFeatures,
) -> dict[str, dict[str, Any]]:
    """Normalize the saved profile map stored in config-entry options."""

    normalized: dict[str, dict[str, Any]] = {}
    for profile_id, payload in (raw_profiles or {}).items():
        normalized_profile = normalize_saved_profile_input(
            payload,
            features=features,
            profile_id=str(profile_id),
        )
        if normalized_profile[CONF_PROFILE_ID] in normalized:
            raise DuplicateProfileIdError(f"Duplicate profile id: {normalized_profile[CONF_PROFILE_ID]}")
        normalized[normalized_profile[CONF_PROFILE_ID]] = normalized_profile
    return normalized


def normalize_saved_profile_input(
    user_input: dict[str, Any],
    *,
    features: FireplaceFeatures,
    profile_id: str | None = None,
) -> dict[str, Any]:
    """Normalize one saved profile into a complete desired fireplace state."""

    name = str(user_input.get(CONF_NAME, "")).strip()
    if not name:
        raise InvalidProfileNameError("Profile name is required.")

    normalized_profile_id = profile_id or build_profile_id(name)

    try:
        state, _warnings = build_requested_state(features, user_input)
    except StateValidationError as exc:
        raise InvalidSavedProfileError(str(exc)) from exc

    return {
        CONF_PROFILE_ID: normalized_profile_id,
        CONF_NAME: name,
        CONF_POWER: state.power,
        CONF_FLAME: state.flame,
        CONF_FAN: state.fan,
        CONF_LIGHT: state.light,
        CONF_FRONT: state.front,
        CONF_AUX: state.aux,
        CONF_CPI: state.cpi,
    }
