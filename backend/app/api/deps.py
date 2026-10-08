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

import ipaddress
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.client_ip import client_ip_or_unknown
from app.core.config import settings
from app.core.context import AuthenticatedUser, reset_tenant_id, set_principal, set_tenant_id
from app.core.exceptions import (
    AuthenticationError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitExceededError,
    ReauthenticationRequiredError,
)
from app.core.logging import get_logger
from app.core.platform_permissions import PlatformPermission
from app.core.rate_limit import limiter
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
from app.services.platform_admin import AuditContext as PlatformAuditContext
from app.services.platform_admin import PlatformPrincipal
from app.services.platform_workspace import PlatformWorkspace

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


# scope="function": FastAPI (0.118+) runs the exit code of a default
# ("request"-scoped) yield dependency *after* the response has been sent. With
# that default the commit above happened after the client already had its
# 2xx — a client that acted on it (register, then create) could reference rows
# not yet committed, and a failing commit was reported as success. Function
# scope commits before the response leaves. Only this dependency yields; a
# request-scoped yield dependency may not depend on it (FastAPI refuses at
# start-up), which keeps the ordering from being undone silently.
# CURSOR-REVIEW[DP-CR-018]: see docs/reviews/cursor/checkpoints/DP-CR-018.md.
DbSession = Annotated[AsyncSession, Depends(get_db_session, scope="function")]


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

    # Older access tokens omit ``email_verified``. When enforcement is off,
    # treat that as verified so existing sessions keep working. When
    # enforcement is on, missing means unverified — the user must refresh.
    if claims.is_verified is None:
        token_verified = not settings.security.require_email_verification
    else:
        token_verified = claims.is_verified

    principal = AuthenticatedUser(
        user_id=claims.user_id,
        tenant_id=claims.tenant_id,
        # The token carries no email claim — it is personal data and would be
        # written into every log line and error report that includes a decoded
        # token. Endpoints that need it read the user record.
        email="",
        roles=frozenset(claims.roles),
        is_verified=token_verified,
    )

    # Binds tenant and user context as a single step, so the three can never
    # disagree. Everything downstream reads from context.
    set_principal(principal)
    request.state.tenant_id = principal.tenant_id
    request.state.user_id = principal.user_id

    return principal


CurrentPrincipal = Annotated[AuthenticatedUser, Depends(get_current_principal)]


# --- Billing (Track E6b) -------------------------------------------------------


async def require_billing_write(session: DbSession, _principal: CurrentPrincipal) -> None:
    """New imports need an active trial or plan (no-op without Stripe).
    Refreshes and syncs do not use this, so existing data keeps flowing.
    Depends on the principal because that is what binds the tenant."""
    from app.services.entitlements import BillingGate

    await BillingGate(session).require_can_write()


BillingWrite = Depends(require_billing_write)


# --- Platform operators (Track E5, D-015) ------------------------------------


def require_platform_network(request: Request) -> None:
    """404 unless the panel is enabled and the caller is on an allowed network.

    404, not 403: on a deployment that has not enabled the panel, or to a
    caller outside the allow-list, the platform routes do not exist. Runs
    before anything else on every platform route, including sign-in.
    """
    from app.core.client_ip import resolve_client_ip

    networks = settings.platform_admin.networks
    raw = resolve_client_ip(request)
    if not networks or raw is None:
        raise NotFoundError()
    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        raise NotFoundError() from None
    if not any(address in network for network in networks):
        raise NotFoundError()


async def get_platform_principal(
    request: Request, credentials: BearerCredentials, session: DbSession
) -> PlatformPrincipal:
    """The signed-in platform operator and their open session. A tenant
    token, even an owner's, is refused: it carries the tenant audience, which
    platform tokens never do. A revoked or expired session is refused even
    while its token is still within ``exp`` (D-018)."""
    from app.core.tokens import decode_platform_token
    from app.services.platform_admin import PlatformAdminService

    require_platform_network(request)
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("An access token is required to use this endpoint.")
    claims = decode_platform_token(credentials.credentials)
    return await PlatformAdminService(session).principal(
        admin_id=claims.admin_id, session_id=claims.session_id
    )


PlatformNetwork = Depends(require_platform_network)
RequirePlatformAdmin = Annotated[PlatformPrincipal, Depends(get_platform_principal)]


def platform_audit_context(request: Request) -> PlatformAuditContext:
    from app.core.client_ip import resolve_client_ip
    from app.core.context import get_request_id

    agent = request.headers.get("user-agent")
    return PlatformAuditContext(
        client_ip=resolve_client_ip(request),
        user_agent=agent[:256] if agent else None,
        request_id=get_request_id(),
    )


PlatformAudit = Annotated[PlatformAuditContext, Depends(platform_audit_context)]


def require_platform_permission(
    permission: PlatformPermission,
) -> Callable[..., Awaitable[PlatformPrincipal]]:
    """Authorization as a dependency (CLAUDE.md §7.8), on the server for every
    platform route. A refusal is a security event and is audited."""

    async def dependency(
        principal: RequirePlatformAdmin, session: DbSession, ctx: PlatformAudit
    ) -> PlatformPrincipal:
        from app.services.platform_admin import PlatformAdminService

        if not principal.can(permission):
            await PlatformAdminService(session).audit_permission_denied(
                principal.admin, permission=permission.value, ctx=ctx
            )
            raise PermissionDeniedError()
        return principal

    return dependency


def require_platform_reauth(
    permission: PlatformPermission,
) -> Callable[..., Awaitable[PlatformPrincipal]]:
    """The permission, plus password and code re-entered in this session
    within ``REAUTH_WINDOW_MINUTES``. For actions that change access."""
    check = require_platform_permission(permission)

    # A default, not ``Annotated[..., Depends(check)]``: under postponed
    # annotations FastAPI resolves annotation strings in module globals, where
    # the local ``check`` does not exist, and would read it as a query field.
    async def dependency(
        principal: PlatformPrincipal = Depends(check),  # noqa: B008 — FastAPI's dependency idiom
    ) -> PlatformPrincipal:
        if not principal.reauthenticated_recently():
            raise ReauthenticationRequiredError()
        return principal

    return dependency


def platform_workspace(
    permission: PlatformPermission = PlatformPermission.WORKSPACE_DATA_READ,
) -> Callable[..., AsyncGenerator[PlatformWorkspace]]:
    """Enter one workspace as an operator (D-019).

    Checks the permission, confirms the workspace exists (404 otherwise, as
    for any unknown id), audits the visit, then sets the **tenant context**
    to that workspace for the rest of the request. Everything the handler
    reads goes through the ordinary tenant-scoped repositories, so the tenant
    predicate is applied exactly as for the merchant's own requests, and the
    context is cleared again when the request ends.
    """
    check = require_platform_permission(permission)

    async def dependency(
        tenant_id: uuid.UUID,
        request: Request,
        session: DbSession,
        ctx: PlatformAudit,
        principal: PlatformPrincipal = Depends(check),  # noqa: B008 — FastAPI's dependency idiom
    ) -> AsyncGenerator[PlatformWorkspace]:
        from app.services.platform_admin import PlatformAdminService

        tenant = await TenantRepository(session).get_by_id(tenant_id)
        if tenant is None:
            raise NotFoundError.for_resource("Workspace", tenant_id)
        if request.method == "GET":
            # Changes write their own, more specific audit row.
            route = getattr(request.scope.get("route"), "path", request.url.path)
            await PlatformAdminService(session).record_workspace_view(
                principal, tenant.id, route=route, ctx=ctx
            )
        token = set_tenant_id(tenant.id)
        try:
            yield PlatformWorkspace(principal=principal, tenant=tenant)
        finally:
            try:
                reset_tenant_id(token)
            except ValueError:  # torn down in another context: clear it outright
                set_tenant_id(None)

    return dependency


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
        return require_verified(principal)

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
        # Role gates sit on every tenant-data endpoint; H4 is enforced here so
        # it cannot be skipped by copying an endpoint that only checked roles.
        return require_verified(principal)

    return _check


#: Common authorization requirements, named for readability at the endpoint.
#:
#: Use these rather than `CurrentPrincipal` on any endpoint that has a privilege
#: requirement. `CurrentPrincipal` alone answers "is this caller authenticated";
#: it does not answer "may they do this".
RequireOwner = Annotated[AuthenticatedUser, Depends(require_roles(RoleName.OWNER))]
RequireAdmin = Annotated[AuthenticatedUser, Depends(require_minimum_role(RoleName.ADMIN))]
RequireMember = Annotated[AuthenticatedUser, Depends(require_minimum_role(RoleName.MEMBER))]

#: The floor for tenant data access.
#:
#: `viewer` is the lowest real role, so this admits every legitimate user. It is
#: still a meaningful check rather than a no-op: it rejects a token carrying no
#: roles, or only roles this deployment does not recognise. Such a token is
#: cryptographically valid — it can be produced by a user whose roles were
#: revoked mid-session, or by an older token after a role rename — and without
#: this it would read tenant data unchallenged.
RequireViewer = Annotated[AuthenticatedUser, Depends(require_minimum_role(RoleName.VIEWER))]


def require_verified(
    principal: CurrentPrincipal,
) -> AuthenticatedUser:
    """Reject unverified accounts when email verification enforcement is on.

    No-op while ``SECURITY_REQUIRE_EMAIL_VERIFICATION`` is false — the only
    safe default until a mail provider exists. Applied as a dependency so the
    check cannot be forgotten inside a handler body (H4).
    """
    if not settings.security.require_email_verification:
        return principal
    if not principal.is_verified:
        raise PermissionDeniedError(
            "Verify your email address before using this part of the platform."
        )
    return principal


RequireVerified = Annotated[AuthenticatedUser, Depends(require_verified)]


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


def endpoint_rate_limit(
    name: str, *, limit: int, window_seconds: int
) -> Callable[[Request, AuthenticatedUser | None], Awaitable[None]]:
    """A tighter, named quota for one expensive endpoint.

    The broad middleware quota is sized for ordinary browsing, which is far too
    generous for endpoints that price a catalogue or start background work. This
    adds a second, narrower counter on top of it — through the *same* limiter, so
    there is still one implementation of the window, the circuit breaker and the
    fail-open policy.

    Counted per **tenant and user together**, falling back to client IP for
    traffic that has neither. Both halves are load-bearing. Tenant alone was
    wrong: one member of a workspace holding down a preview would exhaust the
    quota for every colleague, which turns a limiter meant to blunt abuse into
    a way for any single seat to deny the whole account. User alone would be
    worse, since a user id is only unique within a tenant here.

    The identity comes from ``principal`` — the verified token claims, resolved
    by FastAPI *before* this dependency runs because this dependency asks for
    it. That ordering is structural rather than a matter of parameter order,
    and it is why no header, query parameter or context value a caller controls
    can choose which bucket to spend. An unauthenticated request has no
    principal and is counted by address; it cannot name a tenant at all.

    Fails open with Redis, exactly as the middleware does. A cache outage
    degrading to "no quota" is the deliberate trade; a cache outage that took
    down pricing would be worse.
    """

    async def dependency(request: Request, principal: OptionalPrincipal) -> None:
        if not settings.security.rate_limit_enabled:
            return

        if principal is not None:
            identity = f"tenant:{principal.tenant_id}:user:{principal.user_id}"
        else:
            # Same resolver as the middleware: one rule for who a caller is,
            # so a forged header cannot buy a second per-endpoint quota either.
            identity = f"ip:{client_ip_or_unknown(request)}"

        decision = await limiter.consume(
            f"ratelimit:{name}:{identity}", limit=limit, window=window_seconds
        )
        if not decision.allowed:
            # Structured fields rather than the composed key: a log line is
            # searchable by tenant without anyone parsing a string, and the
            # identifiers stay out of the response the caller receives.
            logger.warning(
                "endpoint_rate_limit_exceeded",
                endpoint=name,
                tenant_id=str(principal.tenant_id) if principal else None,
                user_id=str(principal.user_id) if principal else None,
                limit=limit,
            )
            raise RateLimitExceededError(
                "You are making changes faster than they can be calculated. "
                "Please wait a moment and try again.",
                retry_after_seconds=decision.retry_after,
            )

    return dependency


def platform_rate_limit(
    name: str, *, limit: int, window_seconds: int
) -> Callable[[Request], Awaitable[None]]:
    """A named quota for a platform route, counted per client address.

    Not :func:`endpoint_rate_limit`: that one resolves the *tenant*
    principal, which refuses a platform token outright. Operators sit
    behind an IP allow-list already, so the address is the natural key.
    """

    async def dependency(request: Request) -> None:
        if not settings.security.rate_limit_enabled:
            return
        decision = await limiter.consume(
            f"ratelimit:{name}:ip:{client_ip_or_unknown(request)}",
            limit=limit,
            window=window_seconds,
        )
        if not decision.allowed:
            logger.warning("platform_rate_limit_exceeded", endpoint=name, limit=limit)
            raise RateLimitExceededError(
                "Too many attempts. Please wait and try again.",
                retry_after_seconds=decision.retry_after,
            )

    return dependency


__all__ = [
    "BearerCredentials",
    "BillingWrite",
    "Cache",
    "CurrentPrincipal",
    "CurrentTenant",
    "CurrentUser",
    "DbSession",
    "OptionalPrincipal",
    "PlatformAudit",
    "PlatformNetwork",
    "RefreshTokenRepo",
    "RequireAdmin",
    "RequireMember",
    "RequireOwner",
    "RequirePlatformAdmin",
    "RequireViewer",
    "RoleRepo",
    "TenantRepo",
    "UserRepo",
    "endpoint_rate_limit",
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
    "platform_rate_limit",
    "platform_workspace",
    "require_minimum_role",
    "require_platform_permission",
    "require_platform_reauth",
    "require_roles",
]
