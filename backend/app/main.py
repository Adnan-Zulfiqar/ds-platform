"""Application entry point.

Builds the FastAPI application through a factory rather than at import time.
A factory lets tests construct an isolated instance with overridden settings,
and keeps import side effects out of module load.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app import __version__
from app.api.error_handlers import register_exception_handlers
from app.api.v1 import health
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.core.redis import close_redis_clients
from app.database.session import dispose_engine
from app.middleware import (
    RateLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    """Manage startup and shutdown.

    Connection pools are created lazily on first use rather than eagerly here:
    an API replica that cannot reach Redis should still start and serve the
    endpoints that do not need it, instead of crash-looping.

    Shutdown is not optional. Without an explicit dispose, in-flight connections
    are severed abruptly and Postgres logs a stream of connection-reset errors
    on every deploy.
    """
    configure_logging()
    logger.info(
        "application_starting",
        version=__version__,
        environment=settings.environment.value,
    )

    yield

    logger.info("application_stopping")
    await dispose_engine()
    await close_redis_clients()
    logger.info("application_stopped")


def create_application() -> FastAPI:
    """Construct and configure the application."""
    configure_logging()

    app = FastAPI(
        title=settings.project_name,
        version=__version__,
        description="Multi-tenant dropshipping automation platform.",
        lifespan=lifespan,
        # Interactive docs are disabled in production — they enumerate every
        # endpoint and schema, which is free reconnaissance for an attacker.
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
    )

    _register_middleware(app)
    register_exception_handlers(app)

    # Health endpoints are mounted at the root, outside the version prefix.
    # Probes are infrastructure concerns and must not break when the API
    # version changes.
    app.include_router(health.router)
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    return app


def _register_middleware(app: FastAPI) -> None:
    """Register middleware.

    **Order is significant and counter-intuitive.** Starlette wraps each added
    middleware around the previous one, so the *last* registered runs *first* on
    the way in. Registration below is therefore written in reverse of execution
    order.

    Execution order for an incoming request:

    1. ``TrustedHostMiddleware`` — reject a forged Host header before anything
       else spends work on the request.
    2. ``RequestContextMiddleware`` — assign the correlation id. Registered
       early on the inbound path so that every later middleware, including the
       error path, can log with it attached.
    3. ``CORSMiddleware`` — answer preflight requests without waking the rest of
       the stack.
    4. ``SecurityHeadersMiddleware`` — wraps the response on the way out.
    5. ``RateLimitMiddleware`` — runs closest to the route, after tenant context
       could have been established, so quotas can be counted per tenant.
    6. ``GZipMiddleware`` — compress last, on the finished body.
    """
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        # Credentials are allowed because the frontend will authenticate with
        # httpOnly cookies. This is why allow_origins must be an explicit list:
        # the wildcard is rejected by browsers when credentials are enabled, and
        # would be a serious hole if it were not.
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-Tenant-ID"],
        expose_headers=["X-Request-ID", "X-RateLimit-Limit", "X-RateLimit-Remaining"],
        max_age=600,
    )

    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)


app = create_application()
