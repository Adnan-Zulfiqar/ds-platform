"""Shopify publish idempotency for Celery at-least-once delivery (audit A-04)."""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.integrations.shopify.sync import ShopifySyncService, _deterministic_handle

pytestmark = pytest.mark.unit


class TestDeterministicHandle:
    def test_is_stable_for_a_given_product_id(self) -> None:
        product_id = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        assert _deterministic_handle(product_id) == (
            "droppilot-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        )
        assert _deterministic_handle(product_id) == _deterministic_handle(product_id)

    def test_differs_across_products(self) -> None:
        a = uuid.uuid4()
        b = uuid.uuid4()
        assert _deterministic_handle(a) != _deterministic_handle(b)


class TestCreateOrAdopt:
    @pytest.mark.asyncio
    async def test_posts_when_no_product_exists_at_handle(self) -> None:
        client = MagicMock()
        client.get = AsyncMock(return_value={"products": []})
        client.post = AsyncMock(return_value={"product": {"id": 99, "handle": "droppilot-x"}})
        body: dict[str, Any] = {"product": {"title": "Widget"}}

        result = await ShopifySyncService._create_or_adopt(client, body=body, handle="droppilot-x")

        client.get.assert_awaited_once_with(
            "/products.json", params={"handle": "droppilot-x", "limit": 1}
        )
        client.post.assert_awaited_once()
        assert body["product"]["handle"] == "droppilot-x"
        assert result["product"]["id"] == 99

    @pytest.mark.asyncio
    async def test_adopts_existing_product_instead_of_posting_again(self) -> None:
        """Simulates Celery redelivery after Shopify create but before local listing commit."""
        existing = {"id": 42, "handle": "droppilot-retry", "title": "Already there"}
        client = MagicMock()
        client.get = AsyncMock(return_value={"products": [existing]})
        client.post = AsyncMock()
        body: dict[str, Any] = {"product": {"title": "Widget"}}

        result = await ShopifySyncService._create_or_adopt(
            client, body=body, handle="droppilot-retry"
        )

        client.post.assert_not_awaited()
        assert result == {"product": existing}
        assert "handle" not in body["product"]
