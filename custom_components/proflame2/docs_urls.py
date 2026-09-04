"""Documentation URL helpers for the Proflame2 integration."""

from __future__ import annotations

import os

REPOSITORY_URL = "https://github.com/jeffgregx2/HACS-Proflame2"
DOCUMENTATION_REF_ENV = "PROFLAME2_DOCUMENTATION_REF"
DEFAULT_DOCUMENTATION_REF = "v0.6.0-beta2"

ADD_FIREPLACE_PROFILE_DOC = "docs/add_fireplace_profile.md"
ADD_FIREPLACE_PROFILE_OPTIONS_ANCHOR = "add-fireplace-profile-options"
LILYGO_CONTROLLER_DOC = "docs/lilygo_cc1101_controller.md"
RTL433_MANUAL_LEARNING_DOC = "docs/rtl433_manual_learning.md"


def documentation_ref() -> str:
    """Return the repository ref used for user-facing documentation links."""

    return os.getenv(DOCUMENTATION_REF_ENV, DEFAULT_DOCUMENTATION_REF).strip()


def documentation_url(path: str, anchor: str | None = None) -> str:
    """Build a GitHub documentation URL for the configured repository ref."""

    normalized_path = path.strip().lstrip("/")
    url = f"{REPOSITORY_URL}/blob/{documentation_ref()}/{normalized_path}"
    if anchor:
        url = f"{url}#{anchor.strip().lstrip('#')}"
    return url


def add_fireplace_profile_url() -> str:
    """Return the setup-option documentation URL."""

    return documentation_url(
        ADD_FIREPLACE_PROFILE_DOC,
        ADD_FIREPLACE_PROFILE_OPTIONS_ANCHOR,
    )


def lilygo_controller_url() -> str:
    """Return the LilyGO controller setup documentation URL."""

    return documentation_url(LILYGO_CONTROLLER_DOC)


def rtl433_manual_learning_url() -> str:
    """Return the rtl_433-assisted manual learning documentation URL."""

    return documentation_url(RTL433_MANUAL_LEARNING_DOC)
