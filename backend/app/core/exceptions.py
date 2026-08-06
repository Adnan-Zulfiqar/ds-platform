"""Application exception hierarchy.

Services and repositories raise these domain exceptions; they never raise
``HTTPException``. That separation is what keeps the domain layer independent of
FastAPI — the same service can be driven by a Celery task or a CLI without
dragging HTTP semantics along. Translation to HTTP responses happens in exactly
one place: ``app.api.error_handlers``.

Every exception carries a stable machine-readable ``code``. Clients branch on
the code, never on the human-readable message, so messages can be reworded or
localised without breaking integrations.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for every expected application error.

    ``status_code`` lives here rather than in the HTTP layer because the mapping
    from a domain failure to a status is a property of the failure itself. The
    HTTP layer stays a dumb translator, which keeps it free of a growing
    if/elif chain.
    """

    code: str = "internal_error"
    status_code: int = 500
    message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.__class__.message
        self.details = details or {}
        super().__init__(self.message)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r}, message={self.message!r})"


# ---------------------------------------------------------------------------
# 4xx — the caller can fix these.
# ---------------------------------------------------------------------------


class ValidationError(AppError):
    """Input failed business-rule validation.

    Distinct from Pydantic's schema validation, which is handled separately and
    produces field-level errors. This one covers rules that need context a
    schema cannot see, such as "this SKU already exists for your tenant".
    """

    code = "validation_error"
    status_code = 422
    message = "The request payload failed validation."


class CurrencyMismatchError(ValidationError):
    """Cross-currency arithmetic without an explicit conversion."""

    code = "currency_mismatch"
    message = "Monetary amounts in different currencies cannot be combined."


class FxUnavailableError(ValidationError):
    """A required FX rate is missing, expired, or the provider is unavailable."""

    code = "fx_unavailable"
    message = "Pricing cannot be calculated because a valid currency conversion is not available."


class AuthenticationError(AppError):
    """No valid credentials were supplied."""

    code = "authentication_required"
    status_code = 401
    message = "Authentication is required to access this resource."


class InvalidCredentialsError(AuthenticationError):
    code = "invalid_credentials"
    message = "The supplied credentials are not valid."


class TokenExpiredError(AuthenticationError):
    code = "token_expired"
    message = "The authentication token has expired."


class PermissionDeniedError(AppError):
    """Authenticated, but not allowed to perform this action."""

    code = "permission_denied"
    status_code = 403
    message = "You do not have permission to perform this action."


class NotFoundError(AppError):
    """The requested resource does not exist, or is invisible to this tenant.

    Note that cross-tenant access surfaces as 404 rather than 403. Returning 403
    would confirm that the resource exists, letting an attacker enumerate other
    tenants' identifiers.
    """

    code = "not_found"
    status_code = 404
    message = "The requested resource was not found."

    @classmethod
    def for_resource(cls, resource: str, identifier: Any = None) -> NotFoundError:
        if identifier is None:
            return cls(f"{resource} was not found.")
        return cls(
            f"{resource} with identifier {identifier!r} was not found.",
            details={"resource": resource, "identifier": str(identifier)},
        )


class ConflictError(AppError):
    """The request conflicts with current state, e.g. a uniqueness violation."""

    code = "conflict"
    status_code = 409
    message = "The request conflicts with the current state of the resource."


class RateLimitExceededError(AppError):
    """Too many requests within the configured window."""

    code = "rate_limit_exceeded"
    status_code = 429
    message = "Rate limit exceeded. Please retry later."

    def __init__(
        self,
        message: str | None = None,
        *,
        retry_after_seconds: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details=details)
        self.retry_after_seconds = retry_after_seconds


# ---------------------------------------------------------------------------
# 5xx — the caller cannot fix these.
# ---------------------------------------------------------------------------


class InfrastructureError(AppError):
    """A dependency the platform relies on failed."""

    code = "infrastructure_error"
    status_code = 503
    message = "A required service is temporarily unavailable."


class DatabaseError(InfrastructureError):
    code = "database_error"
    message = "A database error occurred."


class CacheError(InfrastructureError):
    code = "cache_error"
    message = "A cache error occurred."


class ExternalServiceError(InfrastructureError):
    """An outbound third-party call failed.

    Phase 0 defines this so that the marketplace integrations added in later
    phases have a correct place to land from day one, instead of inventing a
    parallel error hierarchy.
    """

    code = "external_service_error"
    message = "An external service returned an error."

    def __init__(
        self,
        message: str | None = None,
        *,
        service: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        merged = {**(details or {})}
        if service:
            merged["service"] = service
        super().__init__(message, details=merged)
        self.service = service


class TenantIsolationError(AppError):
    """A tenant-scoped operation was attempted without tenant context.

    Always a bug rather than a client mistake. It is surfaced as a 500 because
    the safe response to "we do not know whose data this is" is to fail.
    """

    code = "tenant_isolation_error"
    status_code = 500
    message = "The operation could not be scoped to a tenant."
