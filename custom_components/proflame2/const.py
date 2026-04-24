"""Constants for the Proflame2 Home Assistant integration."""

from __future__ import annotations

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
