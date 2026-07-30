"""Request context and access-logging middleware.

Assigns a correlation id to every request, binds it to context so that all
downstream logs carry it, and emits one structured access log line per request.

This replaces uvicorn's access log (silenced in ``core.logging``), which emits
unstructured text with no tenant, no duration percentiles, and no correlation id.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.core.config import settings
from app.core.context import set_request_id, set_tenant_id, set_user_id
from app.core.logging import get_logger

logger = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"

# Endpoints excluded from access logging. Probes run every few seconds and
# would otherwise dominate log volume and cost.
_UNLOGGED_PATHS = frozenset({"/health", "/health/live", "/health/ready", "/metrics"})


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Establish request context and log the request/response pair."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._slow_threshold_ms = settings.observability.slow_request_ms

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Honour an inbound correlation id so a trace spans the gateway, the
        # frontend and this service. Regenerate if absent.
        incoming = request.headers.get(REQUEST_ID_HEADER)
        request_id = self._sanitise(incoming) or uuid.uuid4().hex

        # Reset context at the start of every request. ASGI servers reuse
        # tasks, and a leaked tenant id from a previous request would be a
        # cross-tenant data leak.
        set_request_id(request_id)
        set_tenant_id(None)
        set_user_id(None)

        request.state.request_id = request_id
        started = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - started) * 1000
            # Log and re-raise. The exception handlers own the response body;
            # duplicating that here would produce two different error shapes.
            logger.exception(
                "request_failed",
                method=request.method,
                path=request.url.path,
                duration_ms=round(duration_ms, 2),
            )
            raise

        duration_ms = (time.perf_counter() - started) * 1000
        response.headers[REQUEST_ID_HEADER] = request_id

        if request.url.path not in _UNLOGGED_PATHS:
            self._log_response(request, response, duration_ms)

        return response

    def _log_response(self, request: Request, response: Response, duration_ms: float) -> None:
        # Severity follows the status class so that alerting can key on level
        # alone without parsing status codes.
        if response.status_code >= 500:
            log = logger.error
        elif response.status_code >= 400 or duration_ms > self._slow_threshold_ms:
            log = logger.warning
        else:
            log = logger.info

        log(
            "request_completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=round(duration_ms, 2),
            client_ip=self._client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )

    @staticmethod
    def _sanitise(value: str | None) -> str | None:
        """Reject a malformed inbound correlation id.

        The value is echoed into response headers and log fields, so accepting
        arbitrary client input would allow header injection and log forging.
        """
        if not value:
            return None
        candidate = value.strip()[:64]
        return candidate if candidate.replace("-", "").isalnum() else None

    @staticmethod
    def _client_ip(request: Request) -> str | None:
        """Resolve the originating client IP.

        ``X-Forwarded-For`` is only trustworthy because Nginx sits in front and
        overwrites it. Exposing this service directly to the internet would make
        the header client-controlled and therefore worthless for rate limiting
        or abuse tracking.
        """
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else None


__all__ = ["REQUEST_ID_HEADER", "RequestContextMiddleware"]
