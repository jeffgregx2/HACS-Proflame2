"""Build metadata for the Proflame2 integration.

GitHub release automation can inject a release version by updating the
manifest version and this module's default version string, or by setting the
``PROFLAME2_VERSION`` / ``PROFLAME2_BUILD`` environment variables during a
packaged build.
"""

from __future__ import annotations

import os

DEFAULT_INTEGRATION_VERSION = "0.1.0-dev"
BUILD_DEV = "dev"
BUILD_PROD = "prod"


def _derive_build_from_version(version: str) -> str:
    text = str(version).strip().lower()
    if any(token in text for token in ("dev", "alpha", "beta", "rc")):
        return BUILD_DEV
    return BUILD_PROD


def integration_version() -> str:
    """Return the active integration version string."""

    return os.getenv("PROFLAME2_VERSION", DEFAULT_INTEGRATION_VERSION).strip()


def build_flavor() -> str:
    """Return the active build flavor.

    ``PROFLAME2_BUILD`` can force ``dev`` or ``prod``. If unset, the build
    flavor is derived from the version string so prerelease builds behave as
    development builds by default.
    """

    explicit = os.getenv("PROFLAME2_BUILD")
    if explicit is not None:
        normalized = explicit.strip().lower()
        if normalized in (BUILD_DEV, BUILD_PROD):
            return normalized
    return _derive_build_from_version(integration_version())


def is_dev_build() -> bool:
    """Return ``True`` when the active build should expose dev-only UX."""

    return build_flavor() == BUILD_DEV


INTEGRATION_VERSION = integration_version()
BUILD_FLAVOR = build_flavor()
IS_DEV_BUILD = is_dev_build()
