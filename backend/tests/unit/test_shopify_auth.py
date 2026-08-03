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
from app.integrations.shopify.exceptions import ShopifyInvalidShopError

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

    def test_rejects_custom_storefront_domain(self) -> None:
        with pytest.raises(ShopifyInvalidShopError):
            normalise_shop_domain("tenwer.com")

    def test_rejects_localhost(self) -> None:
        with pytest.raises(ShopifyInvalidShopError):
            normalise_shop_domain("localhost")
        with pytest.raises(ShopifyInvalidShopError):
            normalise_shop_domain("http://127.0.0.1/admin")

    def test_strips_trailing_slash_and_port(self) -> None:
        assert (
            normalise_shop_domain("https://mystore.myshopify.com:443/") == "mystore.myshopify.com"
        )


class TestWebhookDeliveryAddress:
    def test_per_topic_under_webhooks_suffix(self) -> None:
        from app.integrations.shopify.service import webhook_delivery_address

        assert (
            webhook_delivery_address(
                base="https://api.example.com/api/v1/integrations/shopify/webhooks",
                topic="orders/create",
            )
            == "https://api.example.com/api/v1/integrations/shopify/webhooks/orders-create"
        )

    def test_shared_callback_and_singular_webhook(self) -> None:
        from app.integrations.shopify.service import webhook_delivery_address

        shared = "https://api.example.com/api/v1/integrations/shopify/callback"
        assert webhook_delivery_address(base=shared, topic="orders/create") == shared.rstrip("/")
        singular = "https://api.example.com/api/v1/integrations/shopify/webhook"
        assert webhook_delivery_address(base=singular, topic="app/uninstalled") == singular.rstrip(
            "/"
        )

    def test_rejects_unknown_base(self) -> None:
        from app.integrations.shopify.exceptions import ShopifyWebhookConfigError
        from app.integrations.shopify.service import validate_webhook_callback_base

        with pytest.raises(ShopifyWebhookConfigError):
            validate_webhook_callback_base("https://api.example.com/hooks")


class TestAppendFrontendQuery:
    def test_appends_to_path_without_query(self) -> None:
        from app.integrations.shopify.service import append_frontend_query

        assert (
            append_frontend_query("http://localhost:3000/settings/integrations", shopify="hmac")
            == "http://localhost:3000/settings/integrations?shopify=hmac"
        )

    def test_preserves_existing_query(self) -> None:
        from app.integrations.shopify.service import append_frontend_query

        assert (
            append_frontend_query(
                "http://localhost:3000/settings/integrations?x=1",
                shopify="connected",
            )
            == "http://localhost:3000/settings/integrations?x=1&shopify=connected"
        )

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
