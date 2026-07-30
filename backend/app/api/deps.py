"""FastAPI dependencies — the composition root.

This module is where concrete implementations are wired to the abstractions the
rest of the application depends on. Endpoints declare what they need as a
parameter; nothing constructs its own database session or repository.

**Phase 1 replaced the identity source.** Phase 0 read the tenant from an
``X-Tenant-ID`` header, which was client-controlled and therefore not access
control. It now comes from the verified claims of a signed access token. As
predicted when that placeholder was written, the change is confined to this
module: no endpoint, service, or repository signature changed, because they all
already depended on the abstraction rather than on the header.

The resolution chain:

    Request → Bearer token → verified claims → AuthenticatedUser
            → bound context → repository tenant filtering
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import AuthenticatedUser, set_principal
from app.core.exceptions import (
    AuthenticationError,
    NotFoundError,
    PermissionDeniedError,
)
from app.core.logging import get_logger
from app.core.redis import CacheClient
from app.core.tokens import TokenType, decode_token
from app.database.session import session_factory
from app.models.role import RoleName
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.refresh_token import RefreshTokenRepository
from app.repositories.role import RoleRepository
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
# Authentication
# ---------------------------------------------------------------------------

# auto_error=False so a missing header reaches our own handler and produces the
# standard error envelope. Left at the default, FastAPI would emit its own
# response shape and clients would need a second error parser for this one case.
_bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token.")

BearerCredentials = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)]


async def get_current_principal(
    request: Request, credentials: BearerCredentials
) -> AuthenticatedUser:
    """Verify the access token and bind the authenticated identity.

    **No database read.** Identity and roles come from the token's signed
    claims, so an authenticated request costs one signature verification rather
    than a query. That is what makes stateless tokens worth using.

    The cost is bounded staleness: a user deactivated, deleted, or stripped of a
    role mid-session keeps their existing access until the token expires —
    fifteen minutes by default. Refresh re-reads both from the database, so the
    window never exceeds one access-token lifetime.

    Where that window is unacceptable — deleting a user, changing a password —
    revoke the refresh tokens as well, which ends the session at the next
    refresh. Endpoints needing the live database row depend on
    :func:`get_current_user` instead.
    """
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("An access token is required to use this endpoint.")

    claims = decode_token(credentials.credentials, expected_type=TokenType.ACCESS)

    principal = AuthenticatedUser(
        user_id=claims.user_id,
        tenant_id=claims.tenant_id,
        # The token carries no email claim — it is personal data and would be
        # written into every log line and error report that includes a decoded
        # token. Endpoints that need it read the user record.
        email="",
        roles=frozenset(claims.roles),
    )

    # Binds tenant and user context as a single step, so the three can never
    # disagree. Everything downstream reads from context.
    set_principal(principal)
    request.state.tenant_id = principal.tenant_id
    request.state.user_id = principal.user_id

    return principal


CurrentPrincipal = Annotated[AuthenticatedUser, Depends(get_current_principal)]


async def get_optional_principal(
    request: Request, credentials: BearerCredentials
) -> AuthenticatedUser | None:
    """Bind the identity when a valid token is present, otherwise return ``None``.

    For endpoints that serve both signed-in and anonymous callers. An *invalid*
    token still fails loudly rather than being treated as anonymous — silently
    downgrading a rejected token to "not signed in" would hide expiry bugs and
    make debugging a client integration miserable.
    """
    if credentials is None or not credentials.credentials:
        return None
    return await get_current_principal(request, credentials)


OptionalPrincipal = Annotated[AuthenticatedUser | None, Depends(get_optional_principal)]


async def get_current_user(session: DbSession, principal: CurrentPrincipal) -> User:
    """Load the authenticated user's database row.

    Depend on this only when the live record is needed — a profile page, or a
    check that must not tolerate the staleness described above. Ordinary
    endpoints should use :class:`CurrentPrincipal` and avoid the query.

    Re-checks ``is_active`` because this reads current state: a token issued
    before deactivation is cryptographically valid but must not be honoured once
    we have looked.
    """
    user = await UserRepository(session).get_by_id(principal.user_id)
    if user is None:
        # Valid signature, but the user is gone or belongs to another tenant.
        # The lookup is tenant-scoped, so a token whose tenant claim was tampered
        # with lands here rather than reading another tenant's user.
        logger.warning(
            "authenticated_user_not_found",
            user_id=str(principal.user_id),
            tenant_id=str(principal.tenant_id),
        )
        raise AuthenticationError("The session is no longer valid. Please sign in again.")

    if not user.is_active:
        raise PermissionDeniedError("This account has been deactivated.")

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_current_tenant(session: DbSession, principal: CurrentPrincipal) -> Tenant:
    """Load the tenant the authenticated user belongs to."""
    tenant = await TenantRepository(session).get_by_id(principal.tenant_id)
    if tenant is None:
        raise NotFoundError.for_resource("Tenant", principal.tenant_id)
    if not tenant.is_active:
        raise PermissionDeniedError("This account is not active.")
    return tenant


CurrentTenant = Annotated[Tenant, Depends(get_current_tenant)]


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


def require_roles(*allowed: RoleName) -> Callable[[AuthenticatedUser], AuthenticatedUser]:
    """Build a dependency that admits only the listed roles.

    A factory rather than a fixed set of dependencies, so a new requirement is
    ``Depends(require_roles(RoleName.ADMIN))`` at the endpoint rather than a new
    function here.

    Enforced as a dependency rather than inside the handler because a dependency
    runs before the handler body and appears in the OpenAPI document — an
    authorization check buried in a function body is easy to omit when the next
    endpoint is copied from this one.
    """
    allowed_values = frozenset(role.value for role in allowed)

    def _check(principal: CurrentPrincipal) -> AuthenticatedUser:
        if not principal.roles.intersection(allowed_values):
            logger.warning(
                "authorization_denied",
                user_id=str(principal.user_id),
                required=sorted(allowed_values),
                held=sorted(principal.roles),
            )
            raise PermissionDeniedError("You do not have permission to perform this action.")
        return principal

    return _check


def require_minimum_role(
    minimum: RoleName,
) -> Callable[[AuthenticatedUser], AuthenticatedUser]:
    """Build a dependency admitting the given role or any that outranks it.

    Preferred over :func:`require_roles` for hierarchical checks: listing
    ``ADMIN, OWNER`` explicitly means a role inserted above admin later would
    silently fail to gain access it should have.
    """
    threshold = minimum.rank

    def _check(principal: CurrentPrincipal) -> AuthenticatedUser:
        # An unrecognised role name is ignored rather than raising: a token
        # issued before a role was renamed should degrade to "insufficient
        # privileges", not to a 500.
        held_ranks: list[int] = []
        for name in principal.roles:
            try:
                held_ranks.append(RoleName(name).rank)
            except ValueError:
                logger.warning("unknown_role_in_token", role=name)

        if not held_ranks or max(held_ranks) < threshold:
            logger.warning(
                "authorization_denied",
                user_id=str(principal.user_id),
                minimum_required=minimum.value,
                held=sorted(principal.roles),
            )
            raise PermissionDeniedError("You do not have permission to perform this action.")
        return principal

    return _check


#: Common authorization requirements, named for readability at the endpoint.
RequireOwner = Annotated[AuthenticatedUser, Depends(require_roles(RoleName.OWNER))]
RequireAdmin = Annotated[AuthenticatedUser, Depends(require_minimum_role(RoleName.ADMIN))]
RequireMember = Annotated[AuthenticatedUser, Depends(require_minimum_role(RoleName.MEMBER))]


# ---------------------------------------------------------------------------
# Repositories
# ---------------------------------------------------------------------------
#
# Tenant-scoped repositories depend on CurrentPrincipal rather than DbSession
# alone. That ordering is what guarantees tenant context is bound before any
# scoped query can run — FastAPI resolves the principal first.


def get_user_repository(session: DbSession, _principal: CurrentPrincipal) -> UserRepository:
    return UserRepository(session)


UserRepo = Annotated[UserRepository, Depends(get_user_repository)]


def get_role_repository(session: DbSession, _principal: CurrentPrincipal) -> RoleRepository:
    return RoleRepository(session)


RoleRepo = Annotated[RoleRepository, Depends(get_role_repository)]


def get_refresh_token_repository(session: DbSession) -> RefreshTokenRepository:
    """Unscoped by necessity — see ``app.repositories.refresh_token``.

    Takes no principal because it is used on the refresh path, before one
    exists.
    """
    return RefreshTokenRepository(session)


RefreshTokenRepo = Annotated[RefreshTokenRepository, Depends(get_refresh_token_repository)]


def get_tenant_repository(session: DbSession) -> TenantRepository:
    """Unscoped tenant repository.

    Not exposed to customer-facing endpoints — see the note in
    ``app.repositories.tenant``.
    """
    return TenantRepository(session)


TenantRepo = Annotated[TenantRepository, Depends(get_tenant_repository)]


__all__ = [
    "BearerCredentials",
    "Cache",
    "CurrentPrincipal",
    "CurrentTenant",
    "CurrentUser",
    "DbSession",
    "OptionalPrincipal",
    "RefreshTokenRepo",
    "RequireAdmin",
    "RequireMember",
    "RequireOwner",
    "RoleRepo",
    "TenantRepo",
    "UserRepo",
    "get_cache",
    "get_current_principal",
    "get_current_tenant",
    "get_current_user",
    "get_db_session",
    "get_optional_principal",
    "get_refresh_token_repository",
    "get_role_repository",
    "get_tenant_repository",
    "get_user_repository",
    "require_minimum_role",
    "require_roles",
]
