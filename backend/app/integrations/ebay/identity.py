"""Who the seller is, according to eBay rather than according to us.

One call, ``getUser``, and one field that matters.

**Why ``userId`` and not ``username``.** eBay's own OpenAPI specification is
explicit about both:

    ``userId``   — "The eBay immutable user ID of the user's account and can
                    always be used to identify the user."
    ``username`` — "The user name, which was specified by the user when they
                    created the account. **This value can be changed by the
                    user.**"

Keying a connection on a name the seller can change means that after one rename
the same account looks like a different one: a reconnect would create a second
row, the deletion contract could not find what to erase, and the uniqueness
guarantee that stops two tenants claiming one seller would be silently void.
``username`` is stored for display only, and is refreshed on every verify.

The endpoint lives on ``apiz.ebay.com`` — not ``api.ebay.com``. That is a real
eBay quirk, and pointing at the wrong host returns a 404 that reads like a
permissions failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.ebay.exceptions import (
    EbayIdentityUnavailableError,
    EbayTokenRevokedError,
)

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class EbaySellerIdentity:
    """The subset of ``getUser`` this platform keeps.

    Everything else the response can carry — the ``businessAccount`` and
    ``individualAccount`` containers, with names, addresses, phone numbers and
    email — is dropped on the floor here and never reaches a caller, a log or a
    column. The narrow ``commerce.identity.readonly`` scope means most of it is
    not returned at all; discarding the rest at the boundary means a future
    scope change cannot quietly start persisting personal data.
    """

    user_id: str
    username: str | None
    account_type: str | None
    registration_marketplace_id: str | None
    status: str | None


def _extract(payload: Any) -> EbaySellerIdentity:
    if not isinstance(payload, dict):
        raise EbayIdentityUnavailableError("eBay returned a malformed identity response.")

    user_id = payload.get("userId")
    if not isinstance(user_id, str) or not user_id.strip():
        # Without the immutable id there is nothing safe to key on, so this is
        # a hard failure rather than a connection with a placeholder identity.
        raise EbayIdentityUnavailableError("eBay returned no immutable user id.")

    def _text(key: str) -> str | None:
        value = payload.get(key)
        return value.strip() if isinstance(value, str) and value.strip() else None

    return EbaySellerIdentity(
        user_id=user_id.strip(),
        username=_text("username"),
        account_type=_text("accountType"),
        registration_marketplace_id=_text("registrationMarketplaceId"),
        status=_text("status"),
    )


async def fetch_seller_identity(*, access_token: str) -> EbaySellerIdentity:
    """Call ``GET /commerce/identity/v1/user/`` with a User access token.

    The trailing slash is the path eBay's specification publishes, and it is
    reproduced exactly rather than tidied away.
    """
    timeout = httpx.Timeout(
        settings.ebay.request_timeout_seconds,
        connect=settings.ebay.connect_timeout_seconds,
    )
    url = f"{settings.ebay.identity_api_base}/commerce/identity/v1/user/"
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(
            url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
        )

    if response.status_code == httpx.codes.OK:
        identity = _extract(response.json())
        # The identifier is eBay personal data. It is not logged, here or
        # anywhere else — only the fact that the call succeeded.
        logger.info("ebay_identity_fetched", has_username=identity.username is not None)
        return identity

    if response.status_code in (httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN):
        # The token is not usable. Treated as revoked so the caller asks for
        # consent again rather than retrying with the same credential forever.
        logger.warning("ebay_identity_unauthorized", status_code=response.status_code)
        raise EbayTokenRevokedError()

    logger.warning("ebay_identity_unavailable", status_code=response.status_code)
    raise EbayIdentityUnavailableError()


__all__ = ["EbaySellerIdentity", "fetch_seller_identity"]
