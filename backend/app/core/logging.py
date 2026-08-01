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


#: Substrings that mark a field as carrying a credential.
#:
#: Matched as substrings, case-insensitively, so ``token`` also covers
#: ``access_token``, ``refresh_token`` and ``tokenValue``. Deliberately broad:
#: the cost of redacting a harmless field is a less useful log line, while the
#: cost of missing one is a credential in a log aggregator that a dozen people
#: and a retention policy can reach.
_SENSITIVE_FIELD_MARKERS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
    "auth_header",
    "api_key",
    "apikey",
    "private_key",
    "encryption_key",
    "credential",
    "cookie",
    "session_id",
    "otp",
    "signature",
)

#: Fields whose names match a marker but which are safe and useful to keep.
#:
#: Without these the redaction would blind exactly the diagnostics it exists to
#: protect: ``token_type`` says *which kind* of token was rejected, and
#: ``signature_header_present`` is a boolean that answers whether AliExpress
#: signs webhook deliveries at all. Neither carries a secret.
_SENSITIVE_FIELD_ALLOWLIST: frozenset[str] = frozenset(
    {
        "token_type",
        "token_expiry",
        "expires_at",
        "signature_header_present",
        "has_token",
        "token_count",
    }
)

_REDACTED = "[redacted]"


def _is_sensitive(field: str) -> bool:
    lowered = field.lower()
    if lowered in _SENSITIVE_FIELD_ALLOWLIST:
        return False
    return any(marker in lowered for marker in _SENSITIVE_FIELD_MARKERS)


def _redact_secrets(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Replace credential-shaped values before anything is written.

    **A safety net, not the primary defence.** Call sites are expected not to
    log secrets, and an audit found none that did. But discipline is a property
    of the code as it is today, and this pipeline is also fed by third-party
    libraries and by whatever gets written next year.

    The value is replaced rather than the key dropped, so a reader can still see
    that the field was present — "there was an Authorization header and it was
    redacted" is useful; silence is not.

    Nested dictionaries are walked because payloads arrive as one object. Depth
    is bounded: a cycle or a pathological structure must not turn a log line
    into an infinite loop.
    """
    return _redact_mapping(event_dict, depth=0)


def _redact_mapping(mapping: MutableMapping[str, Any], *, depth: int) -> MutableMapping[str, Any]:
    if depth > 4:
        return mapping

    for key, value in list(mapping.items()):
        if _is_sensitive(key):
            # Booleans and integers derived from a secret are not the secret —
            # `password_valid=False` is a useful diagnostic and reveals nothing.
            if not isinstance(value, bool | int | float | None.__class__):
                mapping[key] = _REDACTED
            continue

        if isinstance(value, dict):
            mapping[key] = _redact_mapping(dict(value), depth=depth + 1)
        elif isinstance(value, list | tuple):
            mapping[key] = [
                _redact_mapping(dict(item), depth=depth + 1) if isinstance(item, dict) else item
                for item in value
            ]

    return mapping


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
        # Last of the enriching processors, so it also covers fields added
        # above it. Placed before rendering so that neither the JSON nor the
        # console output can carry a credential.
        _redact_secrets,
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
