"""Constants for the Proflame2 Home Assistant integration."""

from __future__ import annotations

from .version import fake_backend_enabled, is_dev_build

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
CONF_PILOT = "pilot"
CONF_THERMOSTAT = "thermostat"
CONF_ACTION_LABEL = "action_label"
CONF_PROFILE_ID = "profile_id"
CONF_PROFILES = "profiles"

CONF_FAN = "fan"
CONF_LIGHT = "light"
CONF_FRONT = "front"
CONF_AUX = "aux"
CONF_CPI = "cpi"
CONF_DEBUG_LOGGING = "debug_logging"
CONF_ACTIVE_LISTENING = "active_listening"
CONF_INITIAL_FRAME = "initial_frame"
CONF_INITIAL_PACKET_SOURCE = "initial_packet_source"
CONF_ESPHOME_ENTRY_ID = "esphome_entry_id"
CONF_FIREPLACE_SHORT_NAME = "fireplace_short_name"

BACKEND_FAKE = "fake"
BACKEND_ESPHOME = "lilygo_cc1101"
BACKEND_YARDSTICK = "yardstick"


def available_backend_types() -> tuple[str, ...]:
    """Return the backend types exposed by the current build."""
    from .rf.registry import available_backend_ids

    return available_backend_ids(dev_build=is_dev_build(), include_fake=fake_backend_enabled())


def available_backend_labels() -> dict[str, str]:
    """Return the backend labels exposed by the current build."""

    from .rf.registry import get_backend_definition

    return {backend_type: get_backend_definition(backend_type).label for backend_type in available_backend_types()}


def available_learning_backend_types() -> tuple[str, ...]:
    """Return the backend types that currently support guided learning."""

    from .rf.registry import learning_backend_ids

    return learning_backend_ids(dev_build=is_dev_build(), include_fake=fake_backend_enabled())


def available_learning_backend_labels() -> dict[str, str]:
    """Return the labels for backends that currently support guided learning."""

    from .rf.registry import get_backend_definition

    return {
        backend_type: get_backend_definition(backend_type).label for backend_type in available_learning_backend_types()
    }


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

DEFAULT_FIREPLACE_SHORT_NAME = "---"
MAX_FIREPLACE_SHORT_NAME_LENGTH = 6

DEFAULT_DEBUG_LOGGING = False

SERVICE_SET_STATE = "set_state"
SERVICE_APPLY_PROFILE = "apply_profile"
SERVICE_DISPLAY_STATE_UPDATE = "display_state_update"

DATA_RUNTIME_ENTRIES = "runtime_entries"
DATA_SERVICES_REGISTERED = "services_registered"
DATA_LEARNING_BACKEND_FACTORY = "learning_backend_factory"
DATA_LEARNING_TIMEOUT = "learning_timeout"
DATA_LEARNING_RECEIVE_TIMEOUT = "learning_receive_timeout"
DATA_FAKE_LEARNING_DELAY = "fake_learning_delay"
DATA_YARDSTICK_LEARNING_FREQUENCY_HZ = "yardstick_learning_frequency_hz"
DATA_YARDSTICK_LEARNING_PACKET_LENGTH_BYTES = "yardstick_learning_packet_length_bytes"
DATA_YARDSTICK_LEARNING_SWEEP_ENABLED = "yardstick_learning_sweep_enabled"
DATA_ACTIVE_LISTENING = "active_listening"
DATA_CONTROL_DEBOUNCE_SECONDS = "control_debounce_seconds"
DATA_CONFIRMATION_WINDOW_SECONDS = "confirmation_window_seconds"
DATA_CONFIRMATION_RECEIVE_TIMEOUT_SECONDS = "confirmation_receive_timeout_seconds"
DATA_ESPHOME_TRANSPORT_FACTORY = "esphome_transport_factory"

STATE_CONFIDENCE_OBSERVED = "observed"
STATE_CONFIDENCE_REQUESTED = "requested"
STATE_CONFIDENCE_RESTORED = "restored"
STATE_CONFIDENCE_UNKNOWN = "unknown"

OPERATIONAL_STATUS_READY = "ready"
OPERATIONAL_STATUS_PENDING = "pending"
OPERATIONAL_STATUS_SENDING = "sending"
OPERATIONAL_STATUS_CONFIRMING = "confirming"
OPERATIONAL_STATUS_FAILED = "failed"
OPERATIONAL_STATUS_LEARNING = "learning"
OPERATIONAL_STATUS_UNAVAILABLE = "unavailable"

DEFAULT_CONTROL_DEBOUNCE_SECONDS = 1.5
DEFAULT_CONFIRMATION_WINDOW_SECONDS = 20.0
DEFAULT_CONFIRMATION_RECEIVE_TIMEOUT_SECONDS = 2.5
