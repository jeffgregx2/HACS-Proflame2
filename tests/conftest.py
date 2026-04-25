"""Shared test fixtures."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from custom_components.proflame2.protocol.models import ECCProfile, FireplaceFeatures, RemoteProfile


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "rtl433_samples.json"


@pytest.fixture(autouse=True)
def enable_event_loop_debug():
    """Provide a current event loop for HA's pytest plugin.

    ``pytest-homeassistant-custom-component`` defines an autouse fixture with
    the same name. That fixture assumes a current event loop already exists,
    which is not true for these synchronous protocol tests under Python 3.13.
    Overriding it locally keeps the HA config-flow tests working while also
    letting the non-HA protocol tests run in the same environment.
    """

    created_loop = False
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        created_loop = True
    loop.set_debug(True)

    yield

    if created_loop:
        loop.close()
        asyncio.set_event_loop(None)


@pytest.fixture
def rtl433_samples() -> dict:
    """Return the capture-backed rtl_433 fixture data."""

    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def remote_profile() -> RemoteProfile:
    """Return the capture-derived remote profile."""

    return RemoteProfile(
        serial_id=0x3B3F02,
        ecc=ECCProfile(c1=0x05, d1=0x07, c2=0x01, d2=0x08),
        features=FireplaceFeatures(),
    )
