"""AliExpress OAuth and request signing.

Two concerns, both pure functions over their inputs so they can be tested
exhaustively without a network:

1. **Request signing** — the Open Platform authenticates every call with an HMAC
   over the sorted parameters, not with a bearer header.
2. **The OAuth state token** — the CSRF defence for the authorization redirect.

> **Verify before live traffic.** The signing scheme below implements the
> documented Open Platform algorithm (sorted concatenation, HMAC-SHA256, upper
> hex). AliExpress has shipped more than one signing method across API
> generations and gateways, and the exact parameter names differ by endpoint.
> Confirm both against the current developer documentation for the account in
> use. The algorithm is isolated in :func:`sign_request` precisely so that
> correcting it is a change to one function.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlparse

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Parameters excluded from the signature base string.
#:
#: `sign` cannot sign itself. `sign_method` is excluded because the gateway
#: reads it before verifying and including it makes the base string depend on a
#: value the server has already consumed.
_UNSIGNED_PARAMS = frozenset({"sign", "sign_method"})


def sign_request(params: dict[str, Any], *, app_secret: str, api_path: str = "") -> str:
    """Compute the request signature.

    The documented scheme: sort parameters by key, concatenate ``key + value``
    with no separators, prefix the API path for REST-style endpoints, then
    HMAC-SHA256 with the app secret and upper-case the hex digest.

    Values are serialised the way they will be transmitted — a dict or list
    becomes compact JSON — because signing a different representation than the
    one sent produces a signature the gateway cannot reproduce, which surfaces
    as an opaque "invalid signature" error.

    The secret is used only as the HMAC key and never appears in the base
    string, so nothing derived from it can leak through a logged payload.
    """
    signable = {
        key: value
        for key, value in params.items()
        if key not in _UNSIGNED_PARAMS and value is not None
    }

    parts: list[str] = []
    for key in sorted(signable):
        value = signable[key]
        if isinstance(value, dict | list):
            rendered = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
        elif isinstance(value, bool):
            # Python's "True" is not what any gateway expects.
            rendered = "true" if value else "false"
        else:
            rendered = str(value)
        parts.append(f"{key}{rendered}")

    base = f"{api_path}{''.join(parts)}" if api_path else "".join(parts)

    digest = hmac.new(
        app_secret.encode("utf-8"),
        base.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return digest.upper()


def signing_path_for(url: str) -> str:
    """Derive the path that must prefix the signature base string for a URL.

    The Open Platform has two request styles and they sign differently:

    * **REST style** (``/rest/auth/token/create``) — the base string is prefixed
      with the API path, *excluding* the ``/rest`` routing segment. So the
      prefix for that URL is ``/auth/token/create``.
    * **TOP style** (``/sync``) — the method travels as a ``method`` parameter
      and no path is prefixed.

    Getting this wrong produces an "invalid signature" error that says nothing
    about which half is wrong, so it is derived here rather than passed by hand
    at each call site.
    """
    path = urlparse(url).path.rstrip("/")

    if path.startswith("/rest"):
        return path[len("/rest") :] or ""
    if path in {"/sync", ""}:
        return ""
    return path


def build_signed_params(
    params: dict[str, Any], *, app_key: str, app_secret: str, api_path: str = ""
) -> dict[str, Any]:
    """Add the standard envelope to a parameter set and sign it.

    ``timestamp`` is milliseconds since the epoch, which the gateway uses to
    reject replayed requests. A clock more than a few minutes out will therefore
    fail every call — worth knowing when diagnosing a deployment where nothing
    works and nothing looks wrong.
    """
    enveloped: dict[str, Any] = {
        **params,
        "app_key": app_key,
        "timestamp": str(int(time.time() * 1000)),
        "sign_method": "sha256",
    }
    enveloped["sign"] = sign_request(enveloped, app_secret=app_secret, api_path=api_path)
    return enveloped


# ---------------------------------------------------------------------------
# OAuth
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OAuthState:
    """The value round-tripped through the authorization redirect.

    **This is the CSRF defence for the OAuth flow.** Without it, an attacker can
    complete a consent flow with their own AliExpress account and deliver the
    resulting code to a victim's browser, binding the attacker's supplier
    account to the victim's workspace — every order the victim fulfils would be
    placed through it.

    The token itself is random and opaque; the binding it protects (which tenant
    and user started the flow) is held server-side in Redis and looked up on the
    callback. Putting the tenant id *in* the token would let a caller edit it.
    """

    token: str
    tenant_id: str
    user_id: str | None
    created_at: datetime

    @classmethod
    def create(cls, *, tenant_id: str, user_id: str | None) -> OAuthState:
        return cls(
            # 256 bits from a CSPRNG. `secrets`, never `random`, which is
            # seeded predictably and reproducible.
            token=secrets.token_urlsafe(32),
            tenant_id=tenant_id,
            user_id=user_id,
            created_at=datetime.now(UTC),
        )

    def to_dict(self) -> dict[str, str | None]:
        return {
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, token: str, data: dict[str, Any]) -> OAuthState:
        return cls(
            token=token,
            tenant_id=str(data["tenant_id"]),
            user_id=data.get("user_id"),
            created_at=datetime.fromisoformat(str(data["created_at"])),
        )


def build_authorization_url(*, app_key: str, state: str) -> str:
    """Build the URL the user's browser is sent to for consent.

    ``redirect_uri`` must match the value registered in the AliExpress developer
    console character for character, including scheme and any trailing slash. A
    mismatch is rejected before a code is ever issued, and the error surfaces on
    AliExpress's own page rather than in our logs — which makes it a
    disproportionately confusing failure to diagnose.
    """
    query = urlencode(
        {
            "response_type": "code",
            "force_auth": "true",
            "client_id": app_key,
            "redirect_uri": settings.aliexpress.redirect_uri,
            "state": state,
        }
    )
    return f"{settings.aliexpress.authorize_url}?{query}"


def resolve_token_expiry(*, expires_in: int | None, expire_time: int | None) -> datetime | None:
    """Normalise the two expiry representations AliExpress uses.

    ``expires_in`` is a duration in seconds; ``expire_time`` is an absolute
    epoch timestamp in **milliseconds**. Treating the latter as seconds yields a
    date in 1970 and a connection that appears permanently expired, so the two
    are distinguished by magnitude rather than assumed.

    Returns ``None`` when neither is supplied, which callers must treat as
    "unknown, therefore expired" — the model's ``is_token_expired`` does.
    """
    if expires_in:
        return datetime.now(UTC) + timedelta(seconds=int(expires_in))

    if expire_time:
        # Anything past this magnitude cannot be a seconds-based timestamp for
        # any date this decade, so it is milliseconds.
        seconds = int(expire_time) / 1000 if expire_time > 10_000_000_000 else int(expire_time)
        return datetime.fromtimestamp(seconds, tz=UTC)

    logger.warning("aliexpress_token_expiry_missing")
    return None


__all__ = [
    "OAuthState",
    "build_authorization_url",
    "build_signed_params",
    "resolve_token_expiry",
    "sign_request",
    "signing_path_for",
]
