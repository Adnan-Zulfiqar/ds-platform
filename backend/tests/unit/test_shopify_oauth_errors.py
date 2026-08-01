"""Shopify OAuth failure classification (no live Shopify)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.integrations.shopify.exceptions import (
    ShopifyOAuthHmacError,
    ShopifyOAuthStateError,
)
from app.integrations.shopify.service import ShopifyService

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_invalid_hmac_is_not_reported_as_state_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.integrations.shopify.service.is_encryption_configured",
        lambda: True,
    )
    service = ShopifyService(MagicMock())
    service._require_app_credentials = MagicMock(return_value=("key", "secret"))
    monkeypatch.setattr(
        "app.integrations.shopify.service.verify_oauth_hmac",
        lambda **_: False,
    )

    with pytest.raises(ShopifyOAuthHmacError):
        await service.complete_connection(
            query_string="code=x&shop=x.myshopify.com&state=y&hmac=00"
        )


@pytest.mark.asyncio
async def test_missing_callback_fields_raise_state_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.integrations.shopify.service.is_encryption_configured",
        lambda: True,
    )
    service = ShopifyService(MagicMock())
    service._require_app_credentials = MagicMock(return_value=("key", "secret"))
    monkeypatch.setattr(
        "app.integrations.shopify.service.verify_oauth_hmac",
        lambda **_: True,
    )

    with pytest.raises(ShopifyOAuthStateError):
        await service.complete_connection(query_string="hmac=00")


@pytest.mark.asyncio
async def test_missing_redis_state_raises_state_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.integrations.shopify.service.is_encryption_configured",
        lambda: True,
    )
    service = ShopifyService(MagicMock())
    service._require_app_credentials = MagicMock(return_value=("key", "secret"))
    service._consume_state = AsyncMock(side_effect=ShopifyOAuthStateError())
    monkeypatch.setattr(
        "app.integrations.shopify.service.verify_oauth_hmac",
        lambda **_: True,
    )

    with pytest.raises(ShopifyOAuthStateError):
        await service.complete_connection(
            query_string="code=x&shop=demo.myshopify.com&state=missing&hmac=00"
        )
