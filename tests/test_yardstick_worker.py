"""Focused tests for Yard Stick worker-side pure helpers."""

from __future__ import annotations

import os

from custom_components.proflame2.rf.yardstick_worker import (
    KIND_ERROR,
    KIND_OK,
    YardStickBackendUnavailableError,
    _response,
    _worker_status,
    normalize_yardstick_backend_error,
)


def test_worker_normalize_yardstick_backend_error_preserves_existing_error() -> None:
    original = YardStickBackendUnavailableError("already normalized")

    assert normalize_yardstick_backend_error(original) is original


def test_worker_normalize_yardstick_backend_error_maps_common_failures() -> None:
    cases = (
        (ImportError("no rflib"), "rflib Python package is not installed"),
        (PermissionError("denied"), "access was denied"),
        (RuntimeError("No backend available"), "libusb backend could not be loaded"),
        (RuntimeError("No dongle found"), "No YARD Stick One device was found"),
        (RuntimeError("resource busy"), "access was denied"),
        (RuntimeError("something else"), "YARD Stick One support is unavailable"),
    )

    for exc, expected_message in cases:
        normalized = normalize_yardstick_backend_error(exc)
        assert isinstance(normalized, YardStickBackendUnavailableError)
        assert expected_message in str(normalized)


def test_worker_status_and_response_shape() -> None:
    status = _worker_status(generation=3, radio=object(), last_error=None)
    response = _response(
        request_id=7,
        kind=KIND_OK,
        status=status,
        timing_ms=12.345,
        payload={"answer": 42},
    )

    assert status.worker_pid == os.getpid()
    assert status.worker_generation == 3
    assert status.backend_available is True
    assert status.radio_open is True
    assert response["request_id"] == 7
    assert response["kind"] == KIND_OK
    assert response["success"] is True
    assert response["timing_ms"] == 12.35
    assert response["payload"] == {"answer": 42}
    assert response["status"]["worker_generation"] == 3


def test_worker_error_response_is_not_successful() -> None:
    status = _worker_status(generation=1, radio=None, last_error="boom")
    response = _response(
        request_id=8,
        kind=KIND_ERROR,
        status=status,
        timing_ms=1.0,
        error_class="RuntimeError",
        error="boom",
        tb="traceback",
    )

    assert response["success"] is False
    assert response["error_class"] == "RuntimeError"
    assert response["error"] == "boom"
    assert response["traceback"] == "traceback"
    assert response["status"]["backend_available"] is False
