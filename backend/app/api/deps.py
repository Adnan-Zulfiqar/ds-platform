"""FastAPI dependencies — the composition root.

This module is where concrete implementations are wired to the abstractions the
rest of the application depends on. Endpoints declare what they need as a
parameter; nothing constructs its own database session or repository.

Keeping the wiring in one file means swapping an implementation — a different
cache, a read-replica session for reporting — is a change here rather than a
change at every call site.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.context import set_tenant_id
from app.core.exceptions import AuthenticationError, NotFoundError, PermissionDeniedError
from app.core.logging import get_logger
from app.core.redis import CacheClient
from app.database.session import session_factory
from app.models.tenant import Tenant
from app.repositories.tenant import TenantRepository
from app.repositories.user import UserRepository

logger = get_logger(__name__)


async def get_db_session() -> AsyncGenerator[AsyncSession]:
    """Provide a request-scoped session wrapped in one transaction.

    Commit on success, roll back on any exception. Handlers therefore never call
    ``commit()``: a request either fully succeeds or leaves no trace, and a
    handler that raises halfway through cannot leave a partial write behind.
    """
    session = session_factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


DbSession = Annotated[AsyncSession, Depends(get_db_session)]


def get_cache() -> CacheClient:
    return CacheClient()


Cache = Annotated[CacheClient, Depends(get_cache)]


# ---------------------------------------------------------------------------
# Tenant resolution
# ---------------------------------------------------------------------------


async def resolve_tenant(
    request: Request,
    session: DbSession,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
) -> Tenant:
    """Resolve and bind the tenant for this request.

    **Phase 0 limitation — read this before deploying.**

    The authoritative source of tenant identity is the authenticated principal:
    the tenant claim inside a verified access token. Authentication does not
    exist yet, so that source is unavailable.

    Until it does, the tenant is read from an ``X-Tenant-ID`` header. A header is
    client-controlled, so this is *not* an access control mechanism — anyone
    could name any tenant. It exists so that the tenant-scoping machinery is
    real and exercised by tests from the start rather than being bolted on later.

    Two safeguards keep the gap from becoming a production breach:

    * The header path refuses to operate in a deployed environment, so shipping
      this as-is fails loudly instead of leaking data silently.
    * When the auth phase lands it replaces the body of this function only. Every
      endpoint and repository already depends on the abstraction, so nothing
      else has to change.
    """
    if settings.environment.is_deployed:
        # Defensive: this branch is unreachable in a correctly built deployment
        # because the auth phase replaces this resolver. If it is ever reached,
        # something is badly wrong and the safe response is to refuse.
        logger.error("header_tenant_resolution_attempted_in_deployed_environment")
        raise AuthenticationError("Tenant resolution requires authentication in this environment.")

    if not x_tenant_id:
        raise AuthenticationError(
            "X-Tenant-ID header is required until authentication is implemented."
        )

    try:
        tenant_uuid = uuid.UUID(x_tenant_id)
    except ValueError as exc:
        raise AuthenticationError("X-Tenant-ID must be a valid UUID.") from exc

    tenant = await TenantRepository(session).get_by_id(tenant_uuid)
    if tenant is None:
        raise NotFoundError.for_resource("Tenant", tenant_uuid)
    if not tenant.is_active:
        raise PermissionDeniedError("This tenant account is not active.")

    # Bind before any repository runs. Everything downstream reads the tenant
    # from context rather than receiving it as an argument.
    set_tenant_id(tenant.id)
    request.state.tenant_id = tenant.id
    return tenant


CurrentTenant = Annotated[Tenant, Depends(resolve_tenant)]


# ---------------------------------------------------------------------------
# Repositories
# ---------------------------------------------------------------------------
#
# Repositories depend on CurrentTenant rather than DbSession alone. That
# ordering is deliberate: FastAPI resolves resolve_tenant first, so tenant
# context is guaranteed to be bound before any tenant-scoped query can run.


def get_user_repository(session: DbSession, _tenant: CurrentTenant) -> UserRepository:
    return UserRepository(session)


UserRepo = Annotated[UserRepository, Depends(get_user_repository)]


def get_tenant_repository(session: DbSession) -> TenantRepository:
    """Unscoped tenant repository.

    Not exposed to customer-facing endpoints — see the note in
    ``app.repositories.tenant``.
    """
    return TenantRepository(session)


TenantRepo = Annotated[TenantRepository, Depends(get_tenant_repository)]


__all__ = [
    "Cache",
    "CurrentTenant",
    "DbSession",
    "TenantRepo",
    "UserRepo",
    "get_cache",
    "get_db_session",
    "get_tenant_repository",
    "get_user_repository",
    "resolve_tenant",
]
