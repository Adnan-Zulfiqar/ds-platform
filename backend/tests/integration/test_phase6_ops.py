"""Integration tests for Phase 6 operations APIs.

Stores, pricing, notifications, and analytics — real HTTP + database. AliExpress
is not required for these paths; inventory sync that would call the supplier is
covered separately when a catalogue fixture is available.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from tests.integration.test_products import auth_header, register

pytestmark = pytest.mark.integration


async def _tenant(client: AsyncClient) -> dict[str, str]:
    body = await register(client)
    return auth_header(body)


class TestStores:
    async def test_create_list_and_statistics(self, client: AsyncClient) -> None:
        headers = await _tenant(client)

        created = await client.post(
            "/api/v1/stores",
            headers=headers,
            json={
                "name": "Main Shopify",
                "slug": "main-shopify",
                "platform": "shopify",
                "currency": "USD",
            },
        )
        assert created.status_code == 201, created.text
        store = created.json()
        assert store["slug"] == "main-shopify"
        assert "credentials" not in store
        assert store["healthScore"] >= 0

        listed = await client.get("/api/v1/stores", headers=headers)
        assert listed.status_code == 200
        assert listed.json()["meta"]["totalItems"] >= 1

        stats = await client.get("/api/v1/stores/statistics", headers=headers)
        assert stats.status_code == 200
        body = stats.json()
        assert body["totalStores"] >= 1

        health = await client.get(f"/api/v1/stores/{store['id']}/health", headers=headers)
        assert health.status_code == 200
        assert "status" in health.json() or "healthScore" in health.json()

    async def test_foreign_store_is_404(self, client: AsyncClient) -> None:
        a = await _tenant(client)
        b = await _tenant(client)

        created = await client.post(
            "/api/v1/stores",
            headers=a,
            json={"name": "A Store", "slug": "a-store", "platform": "manual"},
        )
        assert created.status_code == 201
        store_id = created.json()["id"]

        response = await client.get(f"/api/v1/stores/{store_id}", headers=b)
        assert response.status_code == 404


class TestPricing:
    async def test_create_preview_and_apply_empty_catalogue(self, client: AsyncClient) -> None:
        headers = await _tenant(client)

        rule = await client.post(
            "/api/v1/pricing/rules",
            headers=headers,
            json={
                "name": "Thirty percent",
                "scope": "global",
                "strategy": "percentage_markup",
                "markupPercent": "30",
                "minProfit": "1",
            },
        )
        assert rule.status_code == 201, rule.text
        assert rule.json()["markupPercent"] == "30.0000" or rule.json()["markupPercent"].startswith(
            "30"
        )

        preview = await client.post("/api/v1/pricing/preview", headers=headers, json={})
        assert preview.status_code == 200, preview.text
        assert preview.json()["wouldChange"] == 0

        apply = await client.post("/api/v1/pricing/apply", headers=headers, json={})
        assert apply.status_code == 200, apply.text
        assert apply.json() == []


class TestNotifications:
    async def test_empty_inbox_and_mark_all(self, client: AsyncClient) -> None:
        headers = await _tenant(client)

        listed = await client.get("/api/v1/notifications", headers=headers)
        assert listed.status_code == 200
        assert listed.json()["meta"]["totalItems"] == 0

        unread = await client.get("/api/v1/notifications/unread-count", headers=headers)
        assert unread.status_code == 200
        assert unread.json()["unread"] == 0

        marked = await client.post("/api/v1/notifications/read-all", headers=headers)
        assert marked.status_code == 200
        assert marked.json()["unread"] == 0


class TestAnalytics:
    async def test_dashboard_returns_real_zeros(self, client: AsyncClient) -> None:
        headers = await _tenant(client)

        response = await client.get(
            "/api/v1/analytics/dashboard",
            headers=headers,
            params={"periodDays": 30},
        )
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        assert body["orderCount"] == 0
        assert body["productCount"] == 0
        assert body["storeCount"] == 0
        assert isinstance(body["salesSeries"], list)
        assert isinstance(body["recentActivity"], list)


class TestAutomation:
    async def test_create_and_list_rules(self, client: AsyncClient) -> None:
        headers = await _tenant(client)

        created = await client.post(
            "/api/v1/automation/rules",
            headers=headers,
            json={
                "name": "Hourly inventory",
                "action": "sync_inventory",
                "schedule": "hourly",
            },
        )
        assert created.status_code == 201, created.text
        assert created.json()["action"] == "sync_inventory"

        listed = await client.get("/api/v1/automation/rules", headers=headers)
        assert listed.status_code == 200
        assert listed.json()["meta"]["totalItems"] >= 1


class TestInventoryList:
    async def test_inventory_list_empty(self, client: AsyncClient) -> None:
        headers = await _tenant(client)
        response = await client.get("/api/v1/inventory", headers=headers)
        assert response.status_code == 200
        assert response.json()["meta"]["totalItems"] == 0
