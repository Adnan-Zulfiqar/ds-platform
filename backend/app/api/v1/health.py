"""Health and readiness endpoints.

Three distinct probes, because Kubernetes asks three different questions and
answering them with one endpoint causes outages:

* ``/health/live`` — is the process alive? Never touches a dependency. If this
  checked Postgres, a brief database blip would fail liveness and the
  orchestrator would restart every replica, turning a recoverable incident into
  a full outage.
* ``/health/ready`` — can this instance serve traffic? Checks dependencies, so a
  replica that has lost Redis is pulled from the load balancer without being
  killed.
* ``/health`` — a detailed report for humans and dashboards.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from app import __version__
from app.core.config import settings
from app.core.redis import check_redis_health
from app.database.session import check_database_health
from app.schemas.common import ComponentHealth, HealthResponse, HealthStatus

router = APIRouter(tags=["health"])


@router.get("/health/live", summary="Liveness probe")
async def liveness() -> dict[str, str]:
    """Return 200 while the process is running."""
    return {"status": "alive"}


@router.get("/health/ready", summary="Readiness probe")
async def readiness(response: Response) -> dict[str, object]:
    """Report whether this instance can serve traffic.

    Returns 503 when a critical dependency is down so the orchestrator stops
    routing to this replica.
    """
    database_ok = await check_database_health()
    redis_ok = await check_redis_health()

    # The database is critical: essentially no endpoint works without it.
    # Redis is not — the cache degrades to database reads and the rate limiter
    # fails open, so a Redis outage should not remove capacity.
    ready = database_ok
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "ready": ready,
        "checks": {"database": database_ok, "redis": redis_ok},
    }


@router.get("/health", response_model=HealthResponse, summary="Detailed health report")
async def health(response: Response) -> HealthResponse:
    """Return a component-by-component health report."""
    database_ok = await check_database_health()
    redis_ok = await check_redis_health()

    components = [
        ComponentHealth(
            name="database",
            status=HealthStatus.HEALTHY if database_ok else HealthStatus.UNHEALTHY,
            detail=None if database_ok else "PostgreSQL is unreachable.",
        ),
        ComponentHealth(
            name="redis",
            status=HealthStatus.HEALTHY if redis_ok else HealthStatus.UNHEALTHY,
            detail=None if redis_ok else "Redis is unreachable; caching is degraded.",
        ),
    ]

    if not database_ok:
        overall = HealthStatus.UNHEALTHY
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    elif not redis_ok:
        overall = HealthStatus.DEGRADED
    else:
        overall = HealthStatus.HEALTHY

    return HealthResponse(
        status=overall,
        version=__version__,
        environment=settings.environment.value,
        components=components,
    )
