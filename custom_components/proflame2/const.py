"""Constants for the Proflame2 Home Assistant integration."""

from __future__ import annotations

from .version import BUILD_FLAVOR, INTEGRATION_VERSION, is_dev_build

DOMAIN = "proflame2"
MANUFACTURER = "Proflame2"

CONF_BACKEND_TYPE = "backend_type"
CONF_C1 = "c1"
CONF_C2 = "c2"
CONF_D1 = "d1"
CONF_D2 = "d2"
CONF_REMOTE_ID = "remote_id"
CONF_CONFIG_ENTRY_ID = "config_entry_id"
CONF_NAME = "name"
CONF_POWER = "power"
CONF_FLAME = "flame"
CONF_PROFILE_ID = "profile_id"
CONF_PROFILES = "profiles"

CONF_FAN = "fan"
CONF_LIGHT = "light"
CONF_FRONT = "front"
CONF_AUX = "aux"
CONF_CPI = "cpi"

BACKEND_FAKE = "fake"
BACKEND_YARDSTICK = "yardstick"
BACKEND_TYPES: tuple[str, ...] = (BACKEND_YARDSTICK, BACKEND_FAKE)
PRODUCTION_BACKEND_TYPES: tuple[str, ...] = (BACKEND_YARDSTICK,)
BACKEND_LABELS: dict[str, str] = {
    BACKEND_YARDSTICK: "YARD Stick One USB Controller",
    BACKEND_FAKE: "Fake Controller (Simulated Learn/Test)",
}


def available_backend_types() -> tuple[str, ...]:
    """Return the backend types exposed by the current build."""

    return BACKEND_TYPES if is_dev_build() else PRODUCTION_BACKEND_TYPES


def available_backend_labels() -> dict[str, str]:
    """Return the backend labels exposed by the current build."""

    return {backend_type: BACKEND_LABELS[backend_type] for backend_type in available_backend_types()}


FEATURE_OPTION_KEYS: tuple[str, ...] = (
    CONF_FAN,
    CONF_LIGHT,
    CONF_FRONT,
    CONF_AUX,
    CONF_CPI,
)

DEFAULT_FEATURE_OPTIONS: dict[str, bool] = {
    CONF_FAN: True,
    CONF_LIGHT: True,
    CONF_FRONT: False,
    CONF_AUX: False,
    CONF_CPI: False,
}

SERVICE_SET_STATE = "set_state"
SERVICE_APPLY_PROFILE = "apply_profile"

DATA_RUNTIME_ENTRIES = "runtime_entries"
DATA_SERVICES_REGISTERED = "services_registered"
DATA_LEARNING_BACKEND_FACTORY = "learning_backend_factory"
DATA_LEARNING_TIMEOUT = "learning_timeout"
DATA_LEARNING_RECEIVE_TIMEOUT = "learning_receive_timeout"
DATA_FAKE_LEARNING_DELAY = "fake_learning_delay"
