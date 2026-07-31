"""Authentication request and response schemas.

Password fields use ``SecretStr``. Pydantic renders it as ``**********`` in any
repr, so a validation error, an exception traceback, or a debug log that
includes the model cannot spill a plaintext password. The value is only read
where it is actually needed, via ``get_secret_value()``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import EmailStr, Field, SecretStr, field_validator

from app.schemas.base import CamelCaseModel
from app.schemas.user import UserRead


class TenantRead(CamelCaseModel):
    """Tenant information returned alongside an authenticated identity."""

    id: uuid.UUID
    name: str
    slug: str
    status: str
    timezone: str
    default_currency: str


class RegisterRequest(CamelCaseModel):
    """Payload for creating a new tenant and its first user.

    Deliberately does **not** accept a role. The first user of a tenant is
    always the owner; letting the request name a role would let a caller
    self-assign privileges that do not yet exist to grant.
    """

    company_name: str = Field(min_length=1, max_length=255)
    email: EmailStr
    password: SecretStr = Field(
        description="Validated against the configured password policy server-side."
    )
    first_name: str | None = Field(default=None, max_length=128)
    last_name: str | None = Field(default=None, max_length=128)

    @field_validator("company_name")
    @classmethod
    def _reject_blank_company_name(cls, value: str) -> str:
        # min_length runs before whitespace stripping, so a name of only spaces
        # would otherwise pass and produce a tenant called "".
        stripped = value.strip()
        if not stripped:
            raise ValueError("Company name cannot be blank.")
        return stripped


class LoginRequest(CamelCaseModel):
    """Sign-in credentials."""

    email: EmailStr
    password: SecretStr

    # Accepted and echoed by the client's session handling. It does not extend
    # the refresh token's lifetime: a longer-lived credential is a larger
    # window for a stolen token, so the server-side TTL is fixed and this only
    # controls whether the client persists the session across a browser restart.
    remember_me: bool = False


class RefreshRequest(CamelCaseModel):
    """Refresh token exchange.

    The token is optional in the body because it is normally read from the
    httpOnly cookie, which JavaScript cannot access and therefore an XSS payload
    cannot steal. The body field exists for non-browser clients — mobile apps,
    server-to-server integrations — that have no cookie jar.
    """

    refresh_token: str | None = None


class LogoutRequest(CamelCaseModel):
    """Sign-out. Same cookie-or-body arrangement as :class:`RefreshRequest`."""

    refresh_token: str | None = None


class VerifyEmailConfirmRequest(CamelCaseModel):
    """Raw verification token from the mailer / development log."""

    token: str = Field(min_length=16, max_length=256)


class TokenResponse(CamelCaseModel):
    """Issued tokens.

    Shaped to match the OAuth 2 token response so that standard client
    libraries can consume it without a custom adapter.

    ``refresh_token`` is omitted from the body when the server has set it as a
    cookie — returning it in both places would put the credential into
    JavaScript's reach and undo the point of the httpOnly cookie.
    """

    access_token: str
    # The OAuth 2 token *type* label, not a secret — hence the suppression.
    token_type: str = "bearer"  # noqa: S105
    expires_in: int = Field(description="Access token lifetime in seconds.")
    refresh_token: str | None = None
    access_expires_at: datetime
    refresh_expires_at: datetime


class AuthenticatedIdentity(CamelCaseModel):
    """Who the caller is, which tenant they belong to, and what they may do."""

    user: UserRead
    tenant: TenantRead
    roles: list[str] = Field(description="Role names held by this user.")


class AuthResponse(CamelCaseModel):
    """Response to a successful registration or sign-in."""

    identity: AuthenticatedIdentity
    tokens: TokenResponse


__all__ = [
    "AuthResponse",
    "AuthenticatedIdentity",
    "LoginRequest",
    "LogoutRequest",
    "RefreshRequest",
    "RegisterRequest",
    "TenantRead",
    "TokenResponse",
    "VerifyEmailConfirmRequest",
]
