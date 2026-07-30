"""Security response headers.

Defence-in-depth headers applied to every response. Nginx sets some of these at
the edge too; setting them here as well means a direct-to-service deployment, a
misconfigured proxy, or a local development run is not silently unprotected.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.core.config import settings


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach security headers to every response."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._is_deployed = settings.environment.is_deployed
        self._hsts_max_age = settings.security.hsts_max_age_seconds
        self._docs_enabled = settings.docs_enabled

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)

        # Stop the browser second-guessing declared content types. Without it a
        # JSON response containing attacker-chosen text can be sniffed as HTML
        # and executed.
        response.headers["X-Content-Type-Options"] = "nosniff"

        # This service returns JSON and is never framed.
        response.headers["X-Frame-Options"] = "DENY"

        # Do not leak internal paths or query strings to third-party sites.
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Deny access to device APIs the API has no use for.
        response.headers["Permissions-Policy"] = (
            "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
            "magnetometer=(), microphone=(), payment=(), usb=()"
        )

        # Prevent responses containing customer data from being cached by any
        # shared proxy. Individual endpoints may override this deliberately.
        response.headers.setdefault("Cache-Control", "no-store")

        response.headers["Content-Security-Policy"] = self._content_security_policy(request)

        if self._is_deployed:
            # HSTS only in deployed environments: sending it from localhost
            # pins http://localhost to HTTPS in the developer's browser, which
            # is confusing and persists long after the header is removed.
            response.headers["Strict-Transport-Security"] = (
                f"max-age={self._hsts_max_age}; includeSubDomains; preload"
            )

        return response

    def _content_security_policy(self, request: Request) -> str:
        """Build the CSP.

        The API serves JSON, so the strictest possible policy applies — nothing
        may be loaded or executed. The one exception is the interactive docs,
        which are real HTML pages that load Swagger UI assets and would be
        broken by ``default-src 'none'``.
        """
        if self._docs_enabled and request.url.path in ("/docs", "/redoc", "/openapi.json"):
            return (
                "default-src 'self'; "
                "img-src 'self' data: https://fastapi.tiangolo.com; "
                "script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
                "style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
                "frame-ancestors 'none'"
            )
        return "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"


__all__ = ["SecurityHeadersMiddleware"]
