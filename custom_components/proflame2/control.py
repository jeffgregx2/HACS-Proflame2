"""Shared state-validation helpers for services and saved profiles."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .const import (
    CONF_AUX,
    CONF_CPI,
    CONF_FAN,
    CONF_FLAME,
    CONF_FRONT,
    CONF_LIGHT,
    CONF_POWER,
)
from .protocol.models import FireplaceFeatures, FireplaceState


class StateValidationError(ValueError):
    """Raised when a requested fireplace state is invalid."""


def default_manual_state() -> FireplaceState:
    """Return the default state used when no prior state is known."""

    return FireplaceState(power=False, flame=0, fan=0, light=0)


def build_requested_state(
    features: FireplaceFeatures,
    data: Mapping[str, Any],
) -> tuple[FireplaceState, tuple[str, ...]]:
    """Validate service or profile input and build a full semantic state.

    This keeps the integration's atomic control rules in one place so manual
    service calls and saved-profile application cannot drift apart.
    """

    if CONF_POWER not in data:
        raise StateValidationError("power is required.")

    power = bool(data[CONF_POWER])
    flame_value = data.get(CONF_FLAME)

    if power:
        if flame_value is None:
            raise StateValidationError("flame is required when power is true.")
        flame = int(flame_value)
        if not 1 <= flame <= 6:
            raise StateValidationError("flame must be between 1 and 6 when power is true.")
    else:
        if flame_value is not None and int(flame_value) not in (0,):
            raise StateValidationError("flame must be omitted or 0 when power is false.")
        flame = 0

    warnings: list[str] = []
    fan = _validate_feature_number(features, data, CONF_FAN, 0, 6, warnings)
    light = _validate_feature_number(features, data, CONF_LIGHT, 0, 6, warnings)
    front = _validate_feature_bool(features, data, CONF_FRONT, warnings)
    aux = _validate_feature_bool(features, data, CONF_AUX, warnings)
    cpi = _validate_feature_bool(features, data, CONF_CPI, warnings)

    return (
        FireplaceState(
            power=power,
            flame=flame,
            fan=fan,
            light=light,
            front=front,
            aux=aux,
            cpi=cpi,
        ),
        tuple(warnings),
    )


def build_staged_state(
    features: FireplaceFeatures,
    base_state: FireplaceState | None,
    changes: Mapping[str, Any],
) -> FireplaceState:
    """Merge a partial control edit into a full desired fireplace state."""

    state = base_state or default_manual_state()
    power = state.power
    flame = state.flame if state.flame > 0 else 1
    fan = state.fan if features.fan else 0
    light = state.light if features.light else 0
    front = state.front if features.front else False
    aux = state.aux if features.aux else False
    cpi = state.cpi if features.cpi else False

    if CONF_POWER in changes:
        power = bool(changes[CONF_POWER])
    if CONF_FLAME in changes and changes[CONF_FLAME] is not None:
        flame = int(changes[CONF_FLAME])
        if flame > 0:
            power = True

    if CONF_FAN in changes and features.fan and power:
        fan = int(changes[CONF_FAN])
    if CONF_LIGHT in changes and features.light and power:
        light = int(changes[CONF_LIGHT])
    if CONF_FRONT in changes and features.front and power:
        front = bool(changes[CONF_FRONT])
    if CONF_AUX in changes and features.aux and power:
        aux = bool(changes[CONF_AUX])
    if CONF_CPI in changes and features.cpi and power:
        cpi = bool(changes[CONF_CPI])

    if power:
        requested_state, _warnings = build_requested_state(
            features,
            {
                CONF_POWER: power,
                CONF_FLAME: flame,
                CONF_FAN: fan,
                CONF_LIGHT: light,
                CONF_FRONT: front,
                CONF_AUX: aux,
                CONF_CPI: cpi,
            },
        )
        return requested_state

    requested_state = FireplaceState(
        power=False,
        flame=flame,
        fan=fan,
        light=light,
        front=front,
        aux=aux,
        cpi=cpi,
    )
    return requested_state


def _validate_feature_number(
    features: FireplaceFeatures,
    data: Mapping[str, Any],
    key: str,
    minimum: int,
    maximum: int,
    warnings: list[str],
) -> int:
    """Validate an optional numeric feature field."""

    if key not in data:
        return 0

    value = int(data[key])
    if not minimum <= value <= maximum:
        raise StateValidationError(f"{key} must be between {minimum} and {maximum}.")

    if not getattr(features, key):
        warnings.append(f"Ignored {key} because it is disabled for this fireplace.")
        return 0

    return value


def _validate_feature_bool(
    features: FireplaceFeatures,
    data: Mapping[str, Any],
    key: str,
    warnings: list[str],
) -> bool:
    """Validate an optional boolean feature field."""

    if key not in data:
        return False

    value = bool(data[key])
    if not getattr(features, key):
        warnings.append(f"Ignored {key} because it is disabled for this fireplace.")
        return False
    return value
