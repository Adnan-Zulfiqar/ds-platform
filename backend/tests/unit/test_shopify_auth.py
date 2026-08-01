"""Shopify OAuth/HMAC helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac

import pytest

from app.integrations.shopify.auth import (
    normalise_shop_domain,
    verify_oauth_hmac,
    verify_webhook_hmac,
)

pytestmark = pytest.mark.unit


class TestNormaliseShop:
    def test_accepts_full_domain(self) -> None:
        assert normalise_shop_domain("MyStore.myshopify.com") == "mystore.myshopify.com"

    def test_accepts_bare_name(self) -> None:
        assert normalise_shop_domain("mystore") == "mystore.myshopify.com"

    def test_strips_scheme(self) -> None:
        assert (
            normalise_shop_domain("https://mystore.myshopify.com/admin") == "mystore.myshopify.com"
        )


class TestHmac:
    def test_oauth_hmac_round_trip(self) -> None:
        secret = "shpss_test_secret"
        items = [
            ("code", "abc"),
            ("shop", "mystore.myshopify.com"),
            ("state", "xyz"),
            ("timestamp", "123"),
        ]
        pairs = sorted(items, key=lambda i: i[0])
        message = "&".join(f"{k}={v}" for k, v in pairs)
        digest = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
        assert verify_oauth_hmac(query_items=[*items, ("hmac", digest)], secret=secret)

    def test_oauth_hmac_rejects_tamper(self) -> None:
        assert not verify_oauth_hmac(
            query_items=[("shop", "x"), ("hmac", "00" * 32)],
            secret="secret",
        )

    def test_webhook_hmac(self) -> None:
        secret = "shpss_test_secret"
        body = b'{"id":1}'
        digest = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
        assert verify_webhook_hmac(raw_body=body, header_hmac=digest, secret=secret)
