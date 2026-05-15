"""Stable Home Assistant identity helpers for Proflame2 entries."""

from __future__ import annotations

from .const import DOMAIN


def fireplace_device_identifier(fireplace_id: str) -> tuple[str, str]:
    """Return the primary device identifier for one configured fireplace."""

    return (DOMAIN, f"fireplace:{fireplace_id}")


def legacy_fireplace_device_identifier(config_entry_id: str) -> tuple[str, str]:
    """Return the legacy config-entry-scoped fireplace identifier."""

    return (DOMAIN, config_entry_id)


def fireplace_device_identifiers(fireplace_id: str) -> set[tuple[str, str]]:
    """Return the Home Assistant device identifier set for one fireplace."""

    return {fireplace_device_identifier(fireplace_id)}


def controller_device_identifier(controller_id: str) -> tuple[str, str]:
    """Return the Proflame2 namespaced controller identifier when needed."""

    return (DOMAIN, f"controller:{controller_id}")


def primary_entity_unique_id(config_entry_id: str) -> str:
    """Return the primary entity unique id for one fireplace."""

    return f"{config_entry_id}_primary"


def runtime_entity_unique_id(config_entry_id: str, key: str) -> str:
    """Return a config-entry-scoped secondary entity unique id."""

    return f"{config_entry_id}_{key}"
