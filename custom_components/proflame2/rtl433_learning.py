"""Adapt standalone Learning IDs to HA profile data and local setup options.

The helper owns collection and confirmation. This boundary only decodes its
remote-only ID; it never launches rtl_433, transmits, or copies feature choices.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .const import (
    CONF_BACKEND_TYPE,
    CONF_ESPHOME_ENTRY_ID,
    CONF_EXTENDED_W4_BASE,
    CONF_EXTENDED_W6,
    CONF_EXTENDED_W7,
    CONF_EXTENDED_W8,
    CONF_LEARNING_ID,
    CONF_NAME,
    CONF_PROTOCOL_VARIANT,
    CONF_REMOTE_ID,
    available_backend_types,
)
from .profile import InvalidBackendError, ManualProfileInput, normalize_entry_options
from .protocol.learning_id import decode_learning_id
from .rf.registry import normalize_controller_id


class InvalidLearningIdError(ValueError):
    """The supplied Learning ID cannot be imported."""


def normalize_learning_id_profile_input(user_input: dict[str, Any]) -> ManualProfileInput:
    """Restore learned remote fields while keeping setup choices local to HA."""

    value = user_input.get(CONF_LEARNING_ID, "")
    try:
        profile = decode_learning_id(value.strip() if isinstance(value, str) else value)
    except ValueError:
        raise InvalidLearningIdError("Invalid Learning ID.") from None

    backend_type = normalize_controller_id(user_input[CONF_BACKEND_TYPE])
    if backend_type not in available_backend_types():
        raise InvalidBackendError(f"Unsupported backend type: {backend_type}")
    data = {
        CONF_NAME: str(user_input[CONF_NAME]).strip(),
        CONF_BACKEND_TYPE: backend_type,
        CONF_REMOTE_ID: profile.serial_id,
        CONF_PROTOCOL_VARIANT: profile.protocol_variant,
    }
    if profile.is_extended:
        template = profile.extended_template
        data.update(
            {
                CONF_EXTENDED_W4_BASE: template.w4_base,
                CONF_EXTENDED_W6: template.w6,
                CONF_EXTENDED_W7: template.w7,
                CONF_EXTENDED_W8: template.w8,
            }
        )
    else:
        data.update(asdict(profile.ecc))
    linked_esphome_entry_id = user_input.get(CONF_ESPHOME_ENTRY_ID)
    if isinstance(linked_esphome_entry_id, str) and linked_esphome_entry_id:
        data[CONF_ESPHOME_ENTRY_ID] = linked_esphome_entry_id
    return ManualProfileInput(data=data, options=normalize_entry_options(user_input))
