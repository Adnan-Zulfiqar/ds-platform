"""Shopify OAuth helpers — install URL, HMAC verification, shop normalisation."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

from app.core.config import settings


@dataclass(frozen=True, slots=True)
class OAuthState:
    token: str

    @classmethod
    def issue(cls) -> OAuthState:
        return cls(token=secrets.token_urlsafe(32))


SHOPIFY_DOMAIN_SUFFIX = ".myshopify.com"


def is_canonical_shop_domain(value: str) -> bool:
    """Whether ``value`` is already exactly ``<label>.myshopify.com``.

    The single definition of "canonical" in this codebase. ``normalise_shop_domain``
    below *produces* this shape from merchant input; the GraphQL client *requires*
    it and normalises nothing, so a caller cannot smuggle a scheme, path, port or
    lookalike host past the OAuth boundary and into an outbound request. One rule,
    two enforcement points — a second, subtly different validator is exactly how a
    host like ``shop.myshopify.com.attacker.test`` eventually gets through.

    Rejects by construction: anything with ``/``, ``:``, ``@``, ``?``, ``#``,
    whitespace or uppercase; a bare handle with no suffix; a multi-label prefix
    such as ``a.b.myshopify.com``; and any host that merely *contains* the suffix
    rather than ending with it.
    """
    if not value or value != value.strip() or value != value.lower():
        return False
    if any(character in value for character in "/:@?#\\ \t\n"):
        return False
    if not value.endswith(SHOPIFY_DOMAIN_SUFFIX):
        return False
    label = value.removesuffix(SHOPIFY_DOMAIN_SUFFIX)
    if not label or "." in label:
        return False
    # Shopify handles are alphanumeric plus hyphens, and cannot start or end
    # with one. ``isalnum`` on the hyphen-stripped label also rejects the empty
    # string that a label of only hyphens would leave behind.
    if label.startswith("-") or label.endswith("-"):
        return False
    return label.replace("-", "").isalnum() and label.replace("-", "").isascii()


def normalise_shop_domain(shop: str) -> str:
    """Return ``example.myshopify.com`` from common user inputs.

    Custom storefront domains (e.g. ``tenwer.com``) are rejected: Shopify's
    OAuth authorize endpoint only accepts ``*.myshopify.com``. Sending a custom
    domain produces Shopify's opaque "Unauthorized Access" page.

    This is the *input* end of the authority — it forgives what a merchant is
    likely to paste. The result is always checked against
    :func:`is_canonical_shop_domain` before being returned, so everything
    downstream can rely on one shape.
    """
    from app.integrations.shopify.exceptions import ShopifyInvalidShopError

    value = shop.strip().lower()
    value = value.removeprefix("https://").removeprefix("http://")
    value = value.split("/")[0]
    value = value.removeprefix("www.")
    value = value.split(":")[0]  # drop accidental :443 / :80
    if value in {"localhost", "127.0.0.1"} or value.endswith(".localhost"):
        raise ShopifyInvalidShopError(
            "Localhost is not a Shopify store domain. Use your *.myshopify.com admin domain."
        )
    if value.endswith(SHOPIFY_DOMAIN_SUFFIX):
        if not is_canonical_shop_domain(value):
            raise ShopifyInvalidShopError()
        return value
    # Bare store handle — no dots (custom domains always contain one).
    if "." not in value and value.replace("-", "").isalnum():
        candidate = f"{value}{SHOPIFY_DOMAIN_SUFFIX}"
        if not is_canonical_shop_domain(candidate):
            raise ShopifyInvalidShopError()
        return candidate
    raise ShopifyInvalidShopError(
        f"'{value}' is not a Shopify admin domain. Use something like "
        "your-store.myshopify.com from Shopify Admin -> Settings -> Domains."
    )


def build_authorization_url(*, shop_domain: str, state: str) -> str:
    params = {
        "client_id": settings.shopify.api_key,
        "scope": settings.shopify.scopes,
        "redirect_uri": settings.shopify.callback_url,
        "state": state,
    }
    return f"https://{shop_domain}/admin/oauth/authorize?{urlencode(params)}"


def verify_oauth_hmac(*, query_items: list[tuple[str, str]], secret: str) -> bool:
    """Verify Shopify's callback ``hmac`` query parameter.

    Shopify signs every query parameter except ``hmac`` (and historically
    ``signature``) using the app secret.
    """
    pairs = [(k, v) for k, v in query_items if k not in {"hmac", "signature"}]
    pairs.sort(key=lambda item: item[0])
    message = "&".join(f"{k}={v}" for k, v in pairs)
    provided = dict(query_items).get("hmac", "")
    digest = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, provided)


def verify_webhook_hmac(*, raw_body: bytes, header_hmac: str, secret: str) -> bool:
    """Verify ``X-Shopify-Hmac-SHA256`` (base64) over the raw body."""
    if not header_hmac:
        return False
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
    import base64

    expected = base64.b64encode(digest).decode("ascii")
    return hmac.compare_digest(expected, header_hmac.strip())
