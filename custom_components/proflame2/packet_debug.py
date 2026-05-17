"""Optional rotating file logging for verbose RF packet troubleshooting."""

from __future__ import annotations

from dataclasses import dataclass
from logging import Formatter, Logger, getLogger
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from pathlib import Path
from queue import SimpleQueue
from typing import cast

from homeassistant.core import HomeAssistant

LOGGER_NAME = "custom_components.proflame2.packet_debug"
DECODE_FAILURE_LOGGER_NAME = "custom_components.proflame2.packet_debug.decode_failures"
LOG_FILENAME = "proflame2_debug.log"
DECODE_FAILURE_LOG_FILENAME = "proflame2_decode_failures.log"
MAX_BYTES = 1_048_576
BACKUP_COUNT = 2

_LOGGER = getLogger(LOGGER_NAME)
_DECODE_FAILURE_LOGGER = getLogger(DECODE_FAILURE_LOGGER_NAME)
_HANDLER: RotatingFileHandler | None = None
_QUEUE_HANDLER: QueueHandler | None = None
_QUEUE_LISTENER: QueueListener | None = None
_LOG_QUEUE: SimpleQueue | None = None
_DECODE_FAILURE_HANDLER: RotatingFileHandler | None = None
_DECODE_FAILURE_QUEUE_HANDLER: QueueHandler | None = None
_DECODE_FAILURE_QUEUE_LISTENER: QueueListener | None = None
_DECODE_FAILURE_LOG_QUEUE: SimpleQueue | None = None
_ENABLE_COUNT = 0


@dataclass(frozen=True)
class PacketDebugLogPaths:
    """Paths for the packet debug logs enabled for one learning session."""

    primary_log_path: Path
    decode_failure_log_path: Path


def get_packet_debug_logger() -> Logger:
    """Return the dedicated packet debug logger."""

    return _LOGGER


def get_packet_decode_failure_logger() -> Logger:
    """Return the dedicated decode-failure debug logger."""

    return _DECODE_FAILURE_LOGGER


def _create_handler(log_path: Path) -> RotatingFileHandler:
    """Create the rotating file handler in a blocking-safe helper."""

    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_path,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setLevel("INFO")
    handler.setFormatter(Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    return handler


def _enable_logger_with_queue(
    logger: Logger,
    handler: RotatingFileHandler,
) -> tuple[SimpleQueue, QueueHandler, QueueListener]:
    """Attach one queue-backed rotating handler to a logger."""

    log_queue = SimpleQueue()
    queue_handler = QueueHandler(log_queue)
    queue_handler.setLevel("INFO")
    listener = QueueListener(log_queue, handler, respect_handler_level=True)
    listener.start()

    logger.addHandler(queue_handler)
    logger.setLevel("INFO")
    logger.propagate = False
    return log_queue, queue_handler, listener


async def async_enable_packet_debug_logging(hass: HomeAssistant) -> PacketDebugLogPaths:
    """Enable the dedicated rotating packet debug log files off the event loop."""

    global _HANDLER, _QUEUE_HANDLER, _QUEUE_LISTENER, _LOG_QUEUE, _ENABLE_COUNT
    global _DECODE_FAILURE_HANDLER, _DECODE_FAILURE_QUEUE_HANDLER
    global _DECODE_FAILURE_QUEUE_LISTENER, _DECODE_FAILURE_LOG_QUEUE

    log_path = Path(hass.config.path(LOG_FILENAME))
    decode_failure_log_path = Path(hass.config.path(DECODE_FAILURE_LOG_FILENAME))
    if _HANDLER is None:
        handler = cast(
            RotatingFileHandler,
            await hass.async_add_executor_job(_create_handler, log_path),
        )
        log_queue, queue_handler, listener = _enable_logger_with_queue(_LOGGER, handler)
        _HANDLER = handler
        _QUEUE_HANDLER = queue_handler
        _QUEUE_LISTENER = listener
        _LOG_QUEUE = log_queue
    if _DECODE_FAILURE_HANDLER is None:
        decode_failure_handler = cast(
            RotatingFileHandler,
            await hass.async_add_executor_job(_create_handler, decode_failure_log_path),
        )
        (
            decode_failure_log_queue,
            decode_failure_queue_handler,
            decode_failure_listener,
        ) = _enable_logger_with_queue(_DECODE_FAILURE_LOGGER, decode_failure_handler)
        _DECODE_FAILURE_HANDLER = decode_failure_handler
        _DECODE_FAILURE_QUEUE_HANDLER = decode_failure_queue_handler
        _DECODE_FAILURE_QUEUE_LISTENER = decode_failure_listener
        _DECODE_FAILURE_LOG_QUEUE = decode_failure_log_queue

    _ENABLE_COUNT += 1
    return PacketDebugLogPaths(
        primary_log_path=log_path,
        decode_failure_log_path=decode_failure_log_path,
    )


def _close_handler(handler: RotatingFileHandler) -> None:
    """Close the rotating handler in a blocking-safe helper."""

    handler.close()


async def async_disable_packet_debug_logging(hass: HomeAssistant) -> None:
    """Disable the dedicated packet debug log file when no users remain."""

    global _HANDLER, _QUEUE_HANDLER, _QUEUE_LISTENER, _LOG_QUEUE, _ENABLE_COUNT
    global _DECODE_FAILURE_HANDLER, _DECODE_FAILURE_QUEUE_HANDLER
    global _DECODE_FAILURE_QUEUE_LISTENER, _DECODE_FAILURE_LOG_QUEUE

    if _ENABLE_COUNT > 0:
        _ENABLE_COUNT -= 1
    if _ENABLE_COUNT > 0 or _HANDLER is None:
        return

    handler = _HANDLER
    queue_handler = _QUEUE_HANDLER
    listener = _QUEUE_LISTENER
    if queue_handler is not None:
        _LOGGER.removeHandler(queue_handler)
    if listener is not None:
        await hass.async_add_executor_job(listener.stop)
    decode_failure_handler = _DECODE_FAILURE_HANDLER
    decode_failure_queue_handler = _DECODE_FAILURE_QUEUE_HANDLER
    decode_failure_listener = _DECODE_FAILURE_QUEUE_LISTENER
    if decode_failure_queue_handler is not None:
        _DECODE_FAILURE_LOGGER.removeHandler(decode_failure_queue_handler)
    if decode_failure_listener is not None:
        await hass.async_add_executor_job(decode_failure_listener.stop)
    _HANDLER = None
    _QUEUE_HANDLER = None
    _QUEUE_LISTENER = None
    _LOG_QUEUE = None
    _DECODE_FAILURE_HANDLER = None
    _DECODE_FAILURE_QUEUE_HANDLER = None
    _DECODE_FAILURE_QUEUE_LISTENER = None
    _DECODE_FAILURE_LOG_QUEUE = None
    await hass.async_add_executor_job(_close_handler, handler)
    if decode_failure_handler is not None:
        await hass.async_add_executor_job(_close_handler, decode_failure_handler)
