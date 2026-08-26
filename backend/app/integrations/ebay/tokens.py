"""The eBay token service: exchange, refresh, and what a rejection means.

One module owns every call to ``/identity/v1/oauth2/token`` so the Basic-auth
header, the form encoding and the "is this recoverable?" question are answered
once.

**The encoding rule that breaks integrations.** eBay documents it plainly:

    "The authorization code returned by eBay is URL-encoded. This value must be
     URL-encoded when you pass the value in the code parameter... However, if
     the method you use to make the request URL-encodes the values you pass,
     then you must URL-decode the authorization code before using it."

FastAPI hands the callback handler the **decoded** code, and ``httpx`` encodes
form data on the way out. So the decoded value is passed straight through and
encoded exactly once. Encoding it here as well is the classic double-encoding
bug, and it surfaces as ``invalid_grant`` — which reads like an expired code
rather than a mangled one.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any, Final

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.ebay.exceptions import (
    EbayNotConfiguredError,
    EbayTokenExchangeError,
    EbayTokenRevokedError,
)

logger = get_logger(__name__)

#: eBay signals an unrecoverable grant this way. A refresh token is revoked when
#: the seller changes their eBay login name or password, revokes consent, or
#: eBay revokes it — all of which mean "ask for consent again", never "retry".
_UNRECOVERABLE: Final = frozenset({"invalid_grant", "unauthorized_client", "invalid_client"})


@dataclass(frozen=True, slots=True)
class EbayTokenSet:
    """What the token service returns.

    ``refresh_token`` is optional because the refresh grant does not reissue
    one: eBay's documented refresh response contains only ``access_token``,
    ``expires_in`` and ``token_type``. Modelling it as optional means a rotation
    — should eBay ever start sending one — is stored rather than silently
    dropped, which is the failure mode that leaves an integration authenticating
    with a token the provider has already retired.
    """

    access_token: str
    expires_in: int
    refresh_token: str | None = None
    refresh_token_expires_in: int | None = None
    scope: str | None = None


def _basic_auth_header() -> str:
    """``Basic base64(client_id:client_secret)``, per eBay's documented scheme."""
    secret = settings.ebay.client_secret
    client_id = settings.ebay.client_id.strip()
    if not client_id or secret is None or not secret.get_secret_value().strip():
        raise EbayNotConfiguredError(
            "eBay application credentials are not configured on this server."
        )
    raw = f"{client_id}:{secret.get_secret_value()}".encode()
    return f"Basic {base64.b64encode(raw).decode('ascii')}"


def _parse(payload: Any) -> EbayTokenSet:
    if not isinstance(payload, dict):
        raise EbayTokenExchangeError("eBay returned a malformed token response.")
    access = payload.get("access_token")
    expires = payload.get("expires_in")
    if not isinstance(access, str) or not access.strip():
        raise EbayTokenExchangeError("eBay returned no access token.")
    if not isinstance(expires, int) or expires <= 0:
        raise EbayTokenExchangeError("eBay returned no usable token lifetime.")

    refresh = payload.get("refresh_token")
    refresh_expires = payload.get("refresh_token_expires_in")
    scope = payload.get("scope")
    return EbayTokenSet(
        access_token=access,
        expires_in=expires,
        refresh_token=refresh if isinstance(refresh, str) and refresh.strip() else None,
        refresh_token_expires_in=refresh_expires if isinstance(refresh_expires, int) else None,
        scope=scope if isinstance(scope, str) and scope.strip() else None,
    )


async def _post(form: dict[str, str]) -> EbayTokenSet:
    timeout = httpx.Timeout(
        settings.ebay.request_timeout_seconds,
        connect=settings.ebay.connect_timeout_seconds,
    )
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": _basic_auth_header(),
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(settings.ebay.oauth_token_url, data=form, headers=headers)

    if response.status_code == httpx.codes.OK:
        return _parse(response.json())

    # eBay returns an OAuth-shaped error body. Read `error` to decide whether a
    # retry could ever help; never log the body, which echoes the grant.
    error_code = ""
    try:
        body = response.json()
        if isinstance(body, dict):
            error_code = str(body.get("error") or "")
    except ValueError:
        error_code = ""

    logger.warning(
        "ebay_token_request_rejected",
        status_code=response.status_code,
        grant_type=form.get("grant_type"),
        error_code=error_code or "unknown",
    )
    # Revocation is a *client-error* verdict: eBay documents it as `400
    # invalid_grant`. Requiring a 4xx as well as the code is the conservative
    # reading in the direction that matters — a 5xx carrying an OAuth-shaped
    # body is an eBay outage, and treating it as consent withdrawn would make
    # every merchant re-authorise because eBay had a bad afternoon. A repeating
    # 5xx surfaces as a token-exchange error instead, which is what it is.
    if error_code in _UNRECOVERABLE and response.is_client_error:
        raise EbayTokenRevokedError()
    raise EbayTokenExchangeError()


async def exchange_authorization_code(*, code: str) -> EbayTokenSet:
    """Trade a consent code for a User access token and its refresh token.

    ``code`` must be the **decoded** value — see the module docstring.
    """
    return await _post(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.ebay.redirect_uri_name,
        }
    )


async def refresh_access_token(*, refresh_token: str, scope: str) -> EbayTokenSet:
    """Mint a fresh access token from a stored refresh token.

    ``scope`` is sent explicitly rather than omitted. eBay allows either, but an
    explicit value makes the granted set an assertion rather than an assumption
    about what the original consent contained — and a scope that has drifted out
    of the consented set is refused loudly instead of quietly returning a token
    that cannot do what the caller expects.
    """
    return await _post(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": scope,
        }
    )


__all__ = [
    "EbayTokenSet",
    "exchange_authorization_code",
    "refresh_access_token",
]
