"""Structured logging configuration.

Logs are events with fields, not sentences. ``structlog`` emits JSON in deployed
environments so that a log aggregator can index ``tenant_id`` or ``request_id``
directly, and renders colourised console output locally where a human is reading.

Every log line automatically carries the current request id, tenant id and user
id via a context processor, so call sites do not have to remember to attach
them. That is what makes it possible to reconstruct a single customer's request
across the API and any background jobs it spawned.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog
from structlog.types import EventDict, Processor

from app.core.config import settings
from app.core.context import get_request_id, get_tenant_id, get_user_id

# Third-party loggers that are noisy at INFO and say nothing useful in production.
_NOISY_LOGGERS = {
    "uvicorn.access": logging.WARNING,  # replaced by our own request middleware
    "botocore": logging.WARNING,
    "boto3": logging.WARNING,
    "urllib3": logging.WARNING,
    "asyncio": logging.WARNING,
    "aiormq": logging.WARNING,
}


def _add_request_context(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Attach request-scoped identifiers to every event.

    Values are omitted when unset rather than logged as ``null``, which keeps
    startup and shutdown lines clean.
    """
    if request_id := get_request_id():
        event_dict["request_id"] = request_id
    if tenant_id := get_tenant_id():
        event_dict["tenant_id"] = str(tenant_id)
    if user_id := get_user_id():
        event_dict["user_id"] = str(user_id)
    return event_dict


def _add_service_metadata(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Attach static service identity, used to separate streams in aggregation."""
    event_dict["service"] = "droppilot-backend"
    event_dict["environment"] = settings.environment.value
    return event_dict


def _drop_color_message_key(_logger: Any, _method: str, event_dict: EventDict) -> EventDict:
    """Remove uvicorn's duplicate pre-coloured message.

    Uvicorn adds ``color_message`` alongside ``event``; keeping both doubles the
    size of every access log line for no benefit.
    """
    event_dict.pop("color_message", None)
    return event_dict


def configure_logging() -> None:
    """Install the logging configuration process-wide.

    Called once at application startup and again by the Celery worker bootstrap,
    so that API and worker logs share a schema.
    """
    observability = settings.observability

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        _add_service_metadata,
        _add_request_context,
        _drop_color_message_key,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    # Exception rendering differs by target: JSON needs a serialisable string,
    # the console renderer draws its own formatted traceback.
    if observability.json_output:
        shared_processors.append(structlog.processors.format_exc_info)
        renderer: Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Route stdlib logging (uvicorn, sqlalchemy, celery) through the same
    # pipeline, so third-party libraries produce identically shaped records.
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(observability.level)

    for logger_name, level in _NOISY_LOGGERS.items():
        logging.getLogger(logger_name).setLevel(level)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound logger.

    Modules call this at import time with ``__name__``.
    """
    return structlog.stdlib.get_logger(name)
