"""AliExpress background tasks.

**Phase 3 implements one task: a connection health check.** Product import,
price and inventory synchronisation, and order automation are later phases. The
task exists now because it is the smallest useful piece of background work, and
building it proves the whole path — Celery, context propagation, the tenant
boundary, retries — before anything depends on it.

**Tenant context is bound per connection, not per task.** The sweep runs on no
tenant's behalf; each connection it finds belongs to exactly one. Binding
context inside the loop is what keeps every tenant-scoped query correct even
though the sweep itself is unscoped, and clearing it afterwards is what stops
one tenant's context leaking into the next iteration.

Tasks are synchronous — Celery workers are not asyncio — while the service layer
is async, so each unit of work runs through ``asyncio.run``. That gives every
connection its own event loop and its own database session, which is also what
keeps one failure from aborting the sweep.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from app.core.config import settings
from app.core.context import clear_context, set_tenant_id
from app.core.logging import get_logger
from app.database.session import transaction
from app.integrations.aliexpress.exceptions import AliExpressError
from app.integrations.aliexpress.service import AliExpressService
from app.repositories.integration import IntegrationMaintenanceRepository
from app.workers.base import BaseTask
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


async def _check_one(connection_id: uuid.UUID, tenant_id: uuid.UUID) -> bool:
    """Health-check a single connection inside its own transaction.

    Binding tenant context here rather than at the sweep level is what makes the
    tenant-scoped repository inside the service behave correctly. The ``finally``
    clears it: worker processes reuse threads, and a leaked tenant id would
    silently scope the next connection's queries to the wrong customer.
    """
    set_tenant_id(tenant_id)
    try:
        async with transaction() as session:
            service = AliExpressService(session)
            connection = await service.get_connection()

            if connection is None or connection.id != connection_id:
                # The connection was removed, or belongs to a different tenant
                # than the sweep recorded. Either way there is nothing to do,
                # and acting on a mismatch would be acting across a boundary.
                logger.info("aliexpress_health_check_skipped", connection_id=str(connection_id))
                return False

            return await service.check_health(connection)
    finally:
        clear_context()


@celery_app.task(
    base=BaseTask,
    bind=True,
    name="integrations.aliexpress.health_check",
    # Bounded so a stalled provider cannot occupy a worker indefinitely.
    soft_time_limit=120,
    time_limit=180,
)
def aliexpress_health_check(self: Any, connection_id: str, tenant_id: str, **_: Any) -> bool:
    """Refresh a near-expiry token and record whether the connection is usable.

    Retries come from :class:`BaseTask`: exponential backoff with jitter, up to
    three attempts. **Safe to retry** because the work is idempotent — checking
    a healthy connection twice changes nothing, and a token refresh that already
    succeeded leaves the second attempt with nothing to do.

    Returns a boolean rather than raising on an unhealthy connection. An expired
    grant is a fact to record, not a task failure to retry; retrying it would
    burn the retry budget on something no amount of retrying can fix.
    """
    try:
        healthy = asyncio.run(_check_one(uuid.UUID(connection_id), uuid.UUID(tenant_id)))
    except AliExpressError as exc:
        if exc.retryable:
            # Transient — let BaseTask back off and try again.
            raise
        logger.warning(
            "aliexpress_health_check_unrecoverable",
            connection_id=connection_id,
            reason=exc.code,
        )
        return False

    logger.info(
        "aliexpress_health_check_completed",
        connection_id=connection_id,
        healthy=healthy,
    )
    return healthy


async def _find_expiring() -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Find connections due for a health check, across every tenant."""
    async with transaction() as session:
        repository = IntegrationMaintenanceRepository(session)
        connections = await repository.find_expiring(
            within_seconds=settings.aliexpress.token_refresh_margin_seconds
        )
        # Materialised as plain ids before the session closes, so no detached
        # ORM instance escapes the transaction.
        return [(c.id, c.tenant_id) for c in connections]


@celery_app.task(
    base=BaseTask,
    bind=True,
    name="integrations.aliexpress.sweep_health_checks",
    soft_time_limit=300,
    time_limit=360,
)
def aliexpress_sweep_health_checks(self: Any, **_: Any) -> int:
    """Queue a health check for every connection nearing token expiry.

    Fans out rather than doing the work inline: one slow or broken connection
    must not delay or fail the others, and individual checks retry
    independently.

    Intended to run on a schedule once Celery beat is configured. Nothing
    schedules it yet — that belongs with the phase that needs regular
    synchronisation.
    """
    due = asyncio.run(_find_expiring())

    for connection_id, tenant_id in due:
        aliexpress_health_check.apply_async(
            kwargs={
                "connection_id": str(connection_id),
                "tenant_id": str(tenant_id),
            }
        )

    logger.info("aliexpress_health_sweep_queued", count=len(due))
    return len(due)


__all__ = ["aliexpress_health_check", "aliexpress_sweep_health_checks"]
