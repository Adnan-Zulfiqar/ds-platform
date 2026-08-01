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


def normalise_shop_domain(shop: str) -> str:
    """Return ``example.myshopify.com`` from common user inputs."""
    value = shop.strip().lower()
    value = value.removeprefix("https://").removeprefix("http://")
    value = value.split("/")[0]
    if value.endswith(".myshopify.com"):
        return value
    # Bare store name
    if "." not in value:
        return f"{value}.myshopify.com"
    return value


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
