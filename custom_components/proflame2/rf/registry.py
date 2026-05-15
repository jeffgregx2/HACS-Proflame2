"""Explicit RF backend registry for Proflame2.

This module defines which concrete RF controllers exist, which ones are
exposed in production builds, and whether guided learning is currently
supported.

The registry is consumed by backend-visibility helpers, controller-id
normalization, and config-flow backend selection. Runtime backend
instantiation still uses explicit backend handling elsewhere in the
integration.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from ..const import BACKEND_ESPHOME, BACKEND_FAKE, BACKEND_YARDSTICK

_VALID_CONTROLLER_ID_RE = re.compile(r"^[a-z0-9_-]+$")
_LEGACY_CONTROLLER_ID_ALIASES: Final[dict[str, str]] = {
    "esphome": BACKEND_ESPHOME,
}


@dataclass(frozen=True)
class BackendDefinition:
    """One explicitly registered RF backend/controller."""

    controller_id: str
    label: str
    class_path: str
    available_in_prod: bool
    supports_learning: bool
    requires_esphome_entry: bool = False


BACKEND_REGISTRY: Final[dict[str, BackendDefinition]] = {
    BACKEND_YARDSTICK: BackendDefinition(
        controller_id=BACKEND_YARDSTICK,
        label="YARD Stick One USB Controller",
        class_path="custom_components.proflame2.rf.yardstick.YardStickBackend",
        available_in_prod=True,
        supports_learning=True,
    ),
    BACKEND_ESPHOME: BackendDefinition(
        controller_id=BACKEND_ESPHOME,
        label="LilyGO T-Embed CC1101 Controller",
        class_path="custom_components.proflame2.rf.esphome_api.ESPHomeAPIBackend",
        available_in_prod=True,
        supports_learning=True,
        requires_esphome_entry=True,
    ),
    BACKEND_FAKE: BackendDefinition(
        controller_id=BACKEND_FAKE,
        label="Fake Controller (Simulated Learn/Test)",
        class_path="custom_components.proflame2.rf.fake.FakeRFBackend",
        available_in_prod=False,
        supports_learning=True,
    ),
}


def normalize_controller_id(value: str) -> str:
    """Return one normalized concrete controller id.

    Legacy aliases are accepted for backward compatibility with older config
    entries.
    """

    normalized = str(value).strip().lower()
    if not normalized:
        raise ValueError("Controller id must not be empty.")
    if not normalized.isprintable():
        raise ValueError("Controller id must contain only printable characters.")
    normalized = _LEGACY_CONTROLLER_ID_ALIASES.get(normalized, normalized)
    if not _VALID_CONTROLLER_ID_RE.fullmatch(normalized):
        raise ValueError("Controller id must contain only lowercase letters, numbers, underscores, or hyphens.")
    return normalized


def get_backend_definition(controller_id: str) -> BackendDefinition:
    """Return one registered backend definition."""

    normalized = normalize_controller_id(controller_id)
    return BACKEND_REGISTRY[normalized]


def available_backend_ids(*, dev_build: bool, include_fake: bool = False) -> tuple[str, ...]:
    """Return backends visible in the current build variant."""

    return tuple(
        controller_id
        for controller_id, definition in BACKEND_REGISTRY.items()
        if (controller_id != BACKEND_FAKE or include_fake) and (dev_build or definition.available_in_prod)
    )


def learning_backend_ids(*, dev_build: bool, include_fake: bool = False) -> tuple[str, ...]:
    """Return visible backends that currently support guided learning."""

    return tuple(
        controller_id
        for controller_id in available_backend_ids(dev_build=dev_build, include_fake=include_fake)
        if BACKEND_REGISTRY[controller_id].supports_learning
    )
