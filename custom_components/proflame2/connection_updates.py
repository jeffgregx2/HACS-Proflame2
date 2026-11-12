"""Publish connection transitions through one bounded, per-entry notification."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant, callback

if TYPE_CHECKING:
    from .rf.base import RFBackend

CONNECTION_PUBLICATION_DELAY_SECONDS = 0.1


class ConnectionStatePublisher:
    """Coalesce events without postponing the first event's publication deadline."""

    def __init__(
        self,
        hass: HomeAssistant,
        backend: RFBackend,
        *,
        is_current: Callable[[], bool],
        publish: Callable[[], None],
    ) -> None:
        self._loop = hass.loop
        self._is_current = is_current
        self._publish = publish
        self._timer: asyncio.TimerHandle | None = None
        self._closed = False
        self._unsubscribe = backend.add_connection_listener(self._connection_changed)

    @callback
    def _connection_changed(self, _connected: bool) -> None:
        if self._closed or not self._is_current() or self._timer is not None:
            return
        self._timer = self._loop.call_later(CONNECTION_PUBLICATION_DELAY_SECONDS, self._publish_current)

    @callback
    def _publish_current(self) -> None:
        self._timer = None
        if not self._closed and self._is_current():
            self._publish()

    @callback
    def close(self) -> None:
        """Detach before backend close, including failed setup or repeated cleanup."""

        if self._closed:
            return
        self._closed = True
        self._unsubscribe()
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
