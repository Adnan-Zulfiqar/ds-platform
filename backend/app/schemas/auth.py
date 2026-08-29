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


class GoogleNonceResponse(CamelCaseModel):
    """A one-time value the browser hands to Google and we later check.

    Google embeds it in the signed credential, so a token minted for a different
    sign-in attempt — or captured and replayed — fails verification here.
    """

    nonce: str
    expires_in_seconds: int


class GoogleSignInRequest(CamelCaseModel):
    """The complete credential from Google's button, plus our nonce.

    `credential` is the raw JWT exactly as Google issued it. It is never decoded
    in the browser for any purpose the backend then trusts.
    """

    credential: str = Field(min_length=1, max_length=8192)
    nonce: str | None = Field(default=None, max_length=256)
    company_name: str | None = Field(default=None, min_length=1, max_length=255)


class GoogleLinkRequest(CamelCaseModel):
    """Attach a Google account to the signed-in user."""

    credential: str = Field(min_length=1, max_length=8192)
    nonce: str | None = Field(default=None, max_length=256)


class GoogleIdentityRead(CamelCaseModel):
    """A linked provider identity, as the account page shows it.

    **The provider subject is deliberately absent.** It is Google's stable
    identifier for a person and the client has no use for it; putting it in a
    response would create another place it has to be erased from.
    """

    provider: str
    provider_email: str | None
    linked_at: datetime
    last_authenticated_at: datetime | None


class PasswordResetRequestRequest(CamelCaseModel):
    """Ask for a code. The response is identical whether or not you exist."""

    email: EmailStr


class PasswordResetChallengeResponse(CamelCaseModel):
    """Deliberately says nothing about whether an account was found."""

    challenge_id: str
    expires_in_seconds: int
    message: str


class PasswordResetVerifyRequest(CamelCaseModel):
    """Six digits against a challenge."""

    challenge_id: str = Field(min_length=1, max_length=256)
    code: str = Field(min_length=6, max_length=6, pattern=r"^[0-9]{6}$")


class PasswordResetTicketResponse(CamelCaseModel):
    """Short-lived, single-use authority to set a password."""

    reset_ticket: str
    expires_in_seconds: int


class PasswordResetCompleteRequest(CamelCaseModel):
    """Spend the ticket and set the new password."""

    reset_ticket: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=1024)
