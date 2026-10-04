"""JWT issuing and verification.

Two token types with deliberately different jobs:

* **Access token** — short-lived, sent on every request, carries the identity
  claims the API needs. It is *not* revocable: checking a revocation list on
  every request would put a database or cache read in front of every endpoint
  and undo the reason for using stateless tokens at all. Its short lifetime is
  the mitigation, so that lifetime is the window in which a stolen token works.

* **Refresh token** — long-lived, sent only to ``/auth/refresh``, and revocable
  because its hash is stored server-side. This is where logout and session
  termination actually take effect.

Both carry a ``jti``. For refresh tokens it is the revocation handle; for access
tokens it exists so a compromised token can be identified in logs.

The ``typ`` claim is verified on every decode. Without it, a refresh token would
be accepted as an access token — turning the long-lived credential into a
permanent API key and defeating the entire rotation design.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Final

import jwt

from app.core.config import settings
from app.core.exceptions import AuthenticationError, TokenExpiredError
from app.core.logging import get_logger

logger = get_logger(__name__)


class TokenType(StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"


#: Claims every token must contain after decoding.
_REQUIRED_CLAIMS: Final[list[str]] = ["sub", "exp", "iat", "jti", "typ", "iss", "aud"]


@dataclass(frozen=True, slots=True)
class TokenClaims:
    """Verified claims from a decoded token."""

    user_id: uuid.UUID
    tenant_id: uuid.UUID
    token_type: TokenType
    jti: str
    issued_at: datetime
    expires_at: datetime
    roles: tuple[str, ...] = ()
    #: Present on access tokens issued after Phase 7. Absent on older tokens —
    #: treated as verified when enforcement is off, unverified when on.
    is_verified: bool | None = None

    @property
    def is_access(self) -> bool:
        return self.token_type is TokenType.ACCESS


@dataclass(frozen=True, slots=True)
class IssuedToken:
    """A freshly minted token and the metadata the caller needs to persist it."""

    token: str
    jti: str
    expires_at: datetime


def _encode(
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    token_type: TokenType,
    ttl: timedelta,
    roles: tuple[str, ...] = (),
    is_verified: bool = True,
) -> IssuedToken:
    now = datetime.now(UTC)
    expires_at = now + ttl
    jti = uuid.uuid4().hex

    payload: dict[str, Any] = {
        "sub": str(user_id),
        "tid": str(tenant_id),
        "typ": token_type.value,
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "iss": settings.security.jwt_issuer,
        "aud": settings.security.jwt_audience,
    }

    # Roles ride in the access token so authorization needs no database read on
    # the hot path. The cost is staleness: a role revoked mid-session stays
    # effective until the access token expires. That is bounded by the access
    # TTL and is the accepted trade — see docs/Authentication.md.
    if token_type is TokenType.ACCESS:
        payload["roles"] = list(roles)
        payload["email_verified"] = is_verified

    token = jwt.encode(
        payload,
        settings.security.secret_key.get_secret_value(),
        algorithm=settings.security.jwt_algorithm,
    )
    return IssuedToken(token=token, jti=jti, expires_at=expires_at)


def create_access_token(
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    roles: tuple[str, ...] = (),
    is_verified: bool = True,
) -> IssuedToken:
    return _encode(
        user_id=user_id,
        tenant_id=tenant_id,
        token_type=TokenType.ACCESS,
        ttl=timedelta(minutes=settings.security.access_token_ttl_minutes),
        roles=roles,
        is_verified=is_verified,
    )


def create_refresh_token(*, user_id: uuid.UUID, tenant_id: uuid.UUID) -> IssuedToken:
    return _encode(
        user_id=user_id,
        tenant_id=tenant_id,
        token_type=TokenType.REFRESH,
        ttl=timedelta(days=settings.security.refresh_token_ttl_days),
    )


def decode_token(token: str, *, expected_type: TokenType) -> TokenClaims:
    """Verify a token and return its claims.

    Raises :class:`TokenExpiredError` when expired — the client can act on that
    by refreshing — and :class:`AuthenticationError` for every other failure,
    with a deliberately uninformative message. Telling a caller *why* their
    forged token was rejected helps them forge a better one.
    """
    try:
        payload = jwt.decode(
            token,
            settings.security.secret_key.get_secret_value(),
            # A list of exactly one algorithm. Accepting a list the token can
            # influence is the classic JWT confusion attack — an attacker
            # switches the header to "none" or to a symmetric algorithm and
            # signs with the public key.
            algorithms=[settings.security.jwt_algorithm],
            audience=settings.security.jwt_audience,
            issuer=settings.security.jwt_issuer,
            leeway=settings.security.jwt_leeway_seconds,
            options={
                "require": _REQUIRED_CLAIMS,
                "verify_signature": True,
                "verify_exp": True,
                "verify_iat": True,
                "verify_aud": True,
                "verify_iss": True,
            },
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpiredError() from exc
    except jwt.InvalidTokenError as exc:
        # Covers bad signature, wrong issuer or audience, missing claims, and
        # malformed input. Logged at debug: during credential stuffing this
        # fires constantly and would otherwise drown the logs.
        logger.debug("token_rejected", reason=type(exc).__name__)
        raise AuthenticationError("The authentication token is not valid.") from exc

    actual_type = payload.get("typ")
    if actual_type != expected_type.value:
        # A refresh token presented as an access token, or vice versa. Logged at
        # warning because a legitimate client never does this — it is either a
        # client bug or an attempt to use the long-lived credential directly.
        logger.warning(
            "token_type_mismatch",
            expected=expected_type.value,
            actual=actual_type,
            jti=payload.get("jti"),
        )
        raise AuthenticationError("The authentication token is not valid.")

    try:
        verified_claim = payload.get("email_verified")
        is_verified: bool | None
        if verified_claim is None:
            is_verified = None
        else:
            is_verified = bool(verified_claim)

        return TokenClaims(
            user_id=uuid.UUID(payload["sub"]),
            tenant_id=uuid.UUID(payload["tid"]),
            token_type=TokenType(actual_type),
            jti=str(payload["jti"]),
            issued_at=datetime.fromtimestamp(payload["iat"], tz=UTC),
            expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
            roles=tuple(payload.get("roles", ())),
            is_verified=is_verified,
        )
    except (KeyError, ValueError, TypeError) as exc:
        # A correctly signed token with malformed claims means our own issuer
        # produced something wrong, or the secret has leaked and someone is
        # experimenting. Either warrants a real log line.
        logger.error("token_claims_malformed", error=str(exc))
        raise AuthenticationError("The authentication token is not valid.") from exc


# --- Platform operators (Track E5, D-015) ------------------------------------
#
# A separate audience as well as a separate ``typ``. ``decode_token`` verifies
# the tenant audience, so a platform token is refused by every tenant route;
# ``decode_platform_token`` verifies the platform audience, so a tenant token
# — even an owner's — is refused by every platform route. Neither check
# depends on a handler remembering to look.

PLATFORM_TOKEN_TYPE: Final = "platform"  # noqa: S105 — a JWT "typ" claim value, not a credential


def platform_audience() -> str:
    return f"{settings.security.jwt_audience}:platform"


@dataclass(frozen=True, slots=True)
class PlatformClaims:
    admin_id: uuid.UUID
    jti: str
    expires_at: datetime


def create_platform_token(*, admin_id: uuid.UUID) -> IssuedToken:
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=settings.platform_admin.token_ttl_minutes)
    jti = uuid.uuid4().hex
    token = jwt.encode(
        {
            "sub": str(admin_id),
            "typ": PLATFORM_TOKEN_TYPE,
            "jti": jti,
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
            "iss": settings.security.jwt_issuer,
            "aud": platform_audience(),
        },
        settings.security.secret_key.get_secret_value(),
        algorithm=settings.security.jwt_algorithm,
    )
    return IssuedToken(token=token, jti=jti, expires_at=expires_at)


def decode_platform_token(token: str) -> PlatformClaims:
    """Uniform failure for every reason, as for tenant tokens."""
    refused = AuthenticationError("The authentication token is not valid.")
    try:
        payload = jwt.decode(
            token,
            settings.security.secret_key.get_secret_value(),
            algorithms=[settings.security.jwt_algorithm],
            audience=platform_audience(),
            issuer=settings.security.jwt_issuer,
            leeway=settings.security.jwt_leeway_seconds,
            options={"require": ["sub", "exp", "iat", "jti", "typ", "iss", "aud"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpiredError() from exc
    except jwt.InvalidTokenError as exc:
        logger.debug("platform_token_rejected", reason=type(exc).__name__)
        raise refused from exc
    if payload.get("typ") != PLATFORM_TOKEN_TYPE:
        raise refused
    try:
        return PlatformClaims(
            admin_id=uuid.UUID(payload["sub"]),
            jti=str(payload["jti"]),
            expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise refused from exc


__all__ = [
    "PLATFORM_TOKEN_TYPE",
    "IssuedToken",
    "PlatformClaims",
    "TokenClaims",
    "TokenType",
    "create_access_token",
    "create_platform_token",
    "create_refresh_token",
    "decode_platform_token",
    "decode_token",
]
