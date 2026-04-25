"""Shared state-validation helpers for services and saved profiles."""

from __future__ import annotations

from typing import Any, Mapping

from .protocol.models import FireplaceFeatures, FireplaceState

from .const import (
    CONF_AUX,
    CONF_CPI,
    CONF_FAN,
    CONF_FLAME,
    CONF_FRONT,
    CONF_LIGHT,
    CONF_POWER,
)


class StateValidationError(ValueError):
    """Raised when a requested fireplace state is invalid."""


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
