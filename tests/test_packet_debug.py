"""Tests for optional packet debug logging lifecycle."""

from __future__ import annotations

import pytest

homeassistant = pytest.importorskip("homeassistant")
pytest.importorskip("pytest_homeassistant_custom_component")

pytestmark = pytest.mark.ha

from custom_components.proflame2 import packet_debug
from custom_components.proflame2.packet_debug import (
    DECODE_FAILURE_LOG_FILENAME,
    LOG_FILENAME,
    async_disable_packet_debug_logging,
    async_enable_packet_debug_logging,
    get_packet_debug_logger,
    get_packet_decode_failure_logger,
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading integrations from the local custom_components directory."""

    yield


async def test_packet_debug_logging_enable_disable_is_ref_counted(
    hass, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(
        hass.config,
        "path",
        lambda *parts: str(tmp_path.joinpath(*parts)),
    )
    try:
        first_paths = await async_enable_packet_debug_logging(hass)
        second_paths = await async_enable_packet_debug_logging(hass)

        assert first_paths == second_paths
        assert first_paths.primary_log_path.name == LOG_FILENAME
        assert first_paths.decode_failure_log_path.name == DECODE_FAILURE_LOG_FILENAME
        assert packet_debug._ENABLE_COUNT == 2
        assert packet_debug._HANDLER is not None
        assert packet_debug._QUEUE_HANDLER in get_packet_debug_logger().handlers
        assert packet_debug._DECODE_FAILURE_HANDLER is not None
        assert packet_debug._DECODE_FAILURE_QUEUE_HANDLER in get_packet_decode_failure_logger().handlers

        await async_disable_packet_debug_logging(hass)

        assert packet_debug._ENABLE_COUNT == 1
        assert packet_debug._HANDLER is not None

        await async_disable_packet_debug_logging(hass)

        assert packet_debug._ENABLE_COUNT == 0
        assert packet_debug._HANDLER is None
        assert packet_debug._QUEUE_HANDLER is None
        assert packet_debug._QUEUE_LISTENER is None
        assert packet_debug._LOG_QUEUE is None
        assert packet_debug._DECODE_FAILURE_HANDLER is None
        assert packet_debug._DECODE_FAILURE_QUEUE_HANDLER is None
        assert packet_debug._DECODE_FAILURE_QUEUE_LISTENER is None
        assert packet_debug._DECODE_FAILURE_LOG_QUEUE is None
    finally:
        while packet_debug._ENABLE_COUNT > 0 or packet_debug._HANDLER is not None:
            await async_disable_packet_debug_logging(hass)


async def test_packet_debug_disable_without_enable_is_noop(hass) -> None:
    await async_disable_packet_debug_logging(hass)

    assert packet_debug._ENABLE_COUNT == 0
    assert packet_debug._HANDLER is None
