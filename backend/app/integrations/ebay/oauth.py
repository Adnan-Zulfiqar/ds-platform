"""eBay seller OAuth: the scope decision, the consent URL, and the state token.

Everything here is a pure function over its inputs or a small dataclass, so the
whole authorization surface is testable without a network.

**Sourced from eBay's published OpenAPI specifications on 26 August 2026**, not
from memory. Every scope string below was read out of the ``securitySchemes``
block of the spec for the API that needs it, and every endpoint out of the
``servers`` block or the Authorization guide. Inventing a scope string produces
a consent page that fails at eBay with no diagnosable error, so guessing is not
an available shortcut.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final
from urllib.parse import urlencode

from app.core.config import settings

# --- The scope decision ------------------------------------------------------
#
# eBay's own guidance pulls in two directions:
#
#   "Create your User access tokens using all the scopes needed for the current
#    capabilities in your application, plus any capabilities you plan to add.
#    Adding a new scope to an existing User access token requires a new
#    permission grant from each of your users."
#
#   "Create tokens using only the scopes your application needs."
#
# So the set below is the forward-looking minimum: what C1 uses, plus what the
# already-planned selling milestones need, and nothing else. Asking a merchant
# to re-consent later is a worse outcome than one honest consent screen now —
# but that is an argument for *planned* capability, not for breadth.

#: Base access. Required alongside any other scope.
_SCOPE_BASE: Final = "https://api.ebay.com/oauth/api_scope"

#: ``getUser`` — the immutable ``userId``. This is what C1 actually needs.
#:
#: Deliberately the plain ``commerce.identity.readonly`` and none of its
#: siblings. ``.email.readonly``, ``.name.readonly``, ``.address.readonly`` and
#: ``.phone.readonly`` exist and would each widen the consent screen to personal
#: data this platform has no use for — and would then have to be declared under
#: EBAY-C0's storage contract. The narrow scope returns the identifier without
#: the person.
_SCOPE_IDENTITY: Final = "https://api.ebay.com/oauth/api_scope/commerce.identity.readonly"

#: Business policies (payment, return, fulfilment). A listing cannot be created
#: without them, so the selling milestone needs this from the same consent.
_SCOPE_ACCOUNT: Final = "https://api.ebay.com/oauth/api_scope/sell.account"

#: Inventory and listings.
_SCOPE_INVENTORY: Final = "https://api.ebay.com/oauth/api_scope/sell.inventory"

#: Orders and shipping fulfilment.
_SCOPE_FULFILLMENT: Final = "https://api.ebay.com/oauth/api_scope/sell.fulfillment"

#: The exact set requested at consent, in a stable order so the authorization
#: URL is deterministic and can be asserted on character for character.
#:
#: The read-only variants are absent on purpose: eBay documents that "there is
#: no need to specify a read-only scope if the corresponding view and manage
#: scope is also being specified".
#:
#: Excluded, and it is worth naming them so the omission reads as a decision
#: rather than an oversight: ``sell.finances`` and ``sell.payment.dispute``
#: (money movement and disputes — this platform touches neither), every
#: marketing and advertising scope, and the four extended identity scopes.
EBAY_OAUTH_SCOPES: Final[tuple[str, ...]] = (
    _SCOPE_BASE,
    _SCOPE_IDENTITY,
    _SCOPE_ACCOUNT,
    _SCOPE_INVENTORY,
    _SCOPE_FULFILLMENT,
)

#: What C1 itself exercises. The rest are granted for the milestones that follow,
#: and the card says so rather than implying the features already exist.
EBAY_SCOPES_USED_NOW: Final[tuple[str, ...]] = (_SCOPE_BASE, _SCOPE_IDENTITY)


def scope_parameter() -> str:
    """The ``scope`` value: space-separated, encoded once by ``urlencode``.

    Returned unencoded. ``urlencode`` in :func:`build_authorization_url` and
    ``httpx``'s form encoder each apply percent-encoding exactly once; encoding
    here as well would send ``%2520`` and produce an invalid-scope rejection.
    """
    return " ".join(EBAY_OAUTH_SCOPES)


@dataclass(frozen=True, slots=True)
class EbayOAuthState:
    """The value round-tripped through eBay's consent redirect.

    **This is the CSRF defence.** Without it an attacker can complete consent
    with their own eBay seller account and deliver the resulting code to a
    victim's browser, binding the attacker's seller account to the victim's
    workspace — every listing the victim published would go to it.

    The token is random and opaque. Everything it protects (which tenant, which
    admin, which environment, where to return) is held server-side in Redis and
    looked up on the callback, because a binding carried *inside* the token is a
    binding the caller can edit.

    Only a hash of the token is used as the Redis key, so a leaked backup or a
    ``KEYS`` dump does not hand over usable state credentials.
    """

    token: str
    tenant_id: str
    user_id: str | None
    environment: str
    return_to: str
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        tenant_id: str,
        user_id: str | None,
        environment: str,
        return_to: str,
    ) -> EbayOAuthState:
        return cls(
            # 256 bits from the OS CSPRNG. `secrets`, never `random`.
            token=secrets.token_urlsafe(32),
            tenant_id=tenant_id,
            user_id=user_id,
            environment=environment,
            return_to=return_to,
            created_at=datetime.now(UTC),
        )

    def to_dict(self) -> dict[str, str | None]:
        return {
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "environment": self.environment,
            "return_to": self.return_to,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, token: str, data: dict[str, Any]) -> EbayOAuthState:
        return cls(
            token=token,
            tenant_id=str(data["tenant_id"]),
            user_id=data.get("user_id"),
            environment=str(data["environment"]),
            return_to=str(data["return_to"]),
            created_at=datetime.fromisoformat(str(data["created_at"])),
        )


def build_authorization_url(*, state: str) -> str:
    """The consent URL a seller's browser is sent to.

    ``redirect_uri`` is the **RuName**, not a URL — see
    ``EbaySettings.redirect_uri_name``. eBay resolves it to the accept and
    decline URLs configured against it in the portal, which is why those two
    URLs never appear in this request.

    ``prompt`` and ``locale`` are documented as optional and are deliberately
    omitted: forcing a re-login on a merchant who is already signed in is a
    worse experience than the one it prevents, and the marketplace comes from
    the seller's own account rather than from a guess made here.
    """
    query = urlencode(
        {
            "client_id": settings.ebay.client_id,
            "redirect_uri": settings.ebay.redirect_uri_name,
            "response_type": "code",
            "scope": scope_parameter(),
            "state": state,
        }
    )
    return f"{settings.ebay.oauth_authorize_url}?{query}"


__all__ = [
    "EBAY_OAUTH_SCOPES",
    "EBAY_SCOPES_USED_NOW",
    "EbayOAuthState",
    "build_authorization_url",
    "scope_parameter",
]
