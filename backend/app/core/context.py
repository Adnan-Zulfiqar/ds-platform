"""Request-scoped context.

Two values need to reach code that sits far below the API layer without being
threaded through every function signature: the tenant being acted upon, and the
correlation id of the current request.

``contextvars`` is the correct mechanism here rather than a global: each asyncio
task gets its own copy, so concurrent requests cannot observe each other's
values. Celery tasks set the same variables at task start, which lets a single
log query follow a request from HTTP ingress through to a background job.

The tenant id in particular is a security boundary. ``require_tenant_id`` raises
instead of returning ``None`` so that a missing tenant can never be silently
interpreted as "no filter" by a repository — the failure mode of a wrong answer
here is one customer reading another customer's data.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from uuid import UUID

_tenant_id: ContextVar[UUID | None] = ContextVar("tenant_id", default=None)
_user_id: ContextVar[UUID | None] = ContextVar("user_id", default=None)
_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_principal: ContextVar[AuthenticatedUser | None] = ContextVar("principal", default=None)


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    """The verified identity behind the current request.

    A value object, not an ORM entity. Holding plain values rather than a
    ``User`` instance keeps ``core`` free of any dependency on ``models`` — the
    invariant that lets every other layer import from here — and means the
    principal cannot lazily trigger database IO when something reads an
    attribute of it deep inside a service.

    Frozen because identity must not change mid-request. Code that could
    reassign ``tenant_id`` on the principal would be one bug away from a
    cross-tenant write.
    """

    user_id: UUID
    tenant_id: UUID
    email: str
    roles: frozenset[str]
    is_active: bool = True
    is_verified: bool = False

    def has_role(self, role: str) -> bool:
        return role in self.roles

    def has_any_role(self, *roles: str) -> bool:
        return bool(self.roles.intersection(roles))

    def __repr__(self) -> str:
        # Email is deliberately omitted: this repr reaches logs and exception
        # output, and personal data should not travel there incidentally.
        return f"<AuthenticatedUser user_id={self.user_id} tenant_id={self.tenant_id}>"


class MissingTenantContextError(RuntimeError):
    """Raised when tenant-scoped work is attempted with no tenant bound.

    This is a programming error, not a client error: it means a code path
    reached a tenant-scoped repository without passing through the middleware
    or dependency that establishes context.
    """


def set_tenant_id(tenant_id: UUID | None) -> Token[UUID | None]:
    return _tenant_id.set(tenant_id)


def get_tenant_id() -> UUID | None:
    """Return the bound tenant id, or ``None``.

    Prefer :func:`require_tenant_id` anywhere the tenant is mandatory.
    """
    return _tenant_id.get()


def require_tenant_id() -> UUID:
    """Return the bound tenant id, raising if absent."""
    tenant_id = _tenant_id.get()
    if tenant_id is None:
        raise MissingTenantContextError(
            "No tenant bound to the current context. Tenant-scoped operations "
            "must run inside a request or task that establishes tenant context."
        )
    return tenant_id


def set_user_id(user_id: UUID | None) -> Token[UUID | None]:
    return _user_id.set(user_id)


def get_user_id() -> UUID | None:
    return _user_id.get()


def set_request_id(request_id: str | None) -> Token[str | None]:
    return _request_id.set(request_id)


def get_request_id() -> str | None:
    return _request_id.get()


def set_principal(principal: AuthenticatedUser | None) -> Token[AuthenticatedUser | None]:
    """Bind the authenticated identity, and the ids derived from it.

    Tenant and user ids are set here rather than by the caller so the three
    cannot disagree. A principal whose ``tenant_id`` differed from the bound
    tenant context would mean repositories filtering by one tenant while
    authorization checked another.
    """
    if principal is not None:
        set_tenant_id(principal.tenant_id)
        set_user_id(principal.user_id)
    return _principal.set(principal)


def get_principal() -> AuthenticatedUser | None:
    return _principal.get()


def require_principal() -> AuthenticatedUser:
    """Return the bound principal, raising if the request is unauthenticated.

    Raises rather than returning ``None`` for the same reason as
    :func:`require_tenant_id`: an unauthenticated request must never be
    mistaken for an authorised one by code that forgot a null check.
    """
    principal = _principal.get()
    if principal is None:
        raise MissingTenantContextError("No authenticated principal bound to the current context.")
    return principal


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Immutable snapshot of the current context.

    Useful for handing context to a Celery task, where the consumer runs in a
    different process and cannot read the producer's context variables.
    """

    tenant_id: UUID | None
    user_id: UUID | None
    request_id: str | None

    @classmethod
    def current(cls) -> RequestContext:
        return cls(
            tenant_id=get_tenant_id(),
            user_id=get_user_id(),
            request_id=get_request_id(),
        )

    def to_dict(self) -> dict[str, str | None]:
        """Serialise for transport across a process boundary."""
        return {
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "user_id": str(self.user_id) if self.user_id else None,
            "request_id": self.request_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, str | None]) -> RequestContext:
        tenant_id = data.get("tenant_id")
        user_id = data.get("user_id")
        return cls(
            tenant_id=UUID(tenant_id) if tenant_id else None,
            user_id=UUID(user_id) if user_id else None,
            request_id=data.get("request_id"),
        )

    def bind(self) -> None:
        """Apply this snapshot to the current context."""
        set_tenant_id(self.tenant_id)
        set_user_id(self.user_id)
        set_request_id(self.request_id)


def clear_context() -> None:
    """Reset all context variables.

    Called at the end of a Celery task. Worker processes reuse threads across
    tasks, so failing to clear would leak one tenant's context into the next
    task that happens to land on the same thread.
    """
    set_tenant_id(None)
    set_user_id(None)
    set_request_id(None)
    _principal.set(None)
