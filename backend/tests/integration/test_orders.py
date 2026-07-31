"""Integration tests for the order management endpoints.

Drives the real HTTP pipeline — auth, dependencies, repository, database — with
only the AliExpress network boundary replaced.

**What the mock returns, honestly stated.** The error envelope tests use the
**real captured response** from the Phase 5 live verification run
(``order_get_not_found.json``). The populated order payload is
documentation-derived, because this account is not a registered DS publisher
and a real order payload cannot be captured without one (M16 in
``TECHNICAL_DEBT.md``). These tests therefore prove the pipeline end to end
against the documented shape and the captured failure shape — not against a
live populated order.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fakeredis import aioredis as fake_aioredis
from httpx import AsyncClient

from app.integrations.aliexpress import service as service_module
from tests.integration.test_products import (
    auth_header,
    connect_aliexpress,
    patch_aliexpress,
    register,
)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> fake_aioredis.FakeRedis:
    """In-process Redis for the OAuth state store and the webhook counter.

    Autouse fixtures do not travel with imported helpers, so this mirrors the
    one in ``test_products`` — and additionally covers the statistics
    endpoint's counter read.
    """
    redis = fake_aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(service_module, "get_redis", lambda _purpose: redis)
    monkeypatch.setattr("app.services.order_sync.get_redis", lambda *_args, **_kwargs: redis)
    return redis


@pytest.fixture(autouse=True)
def _allow_outbound(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bypass the outbound rate limiter, which has its own tests."""
    from app.integrations.rate_limiter import RateLimitDecision

    async def _allow(self: object, tenant_id: str) -> RateLimitDecision:
        return RateLimitDecision(allowed=True, remaining=99, retry_after_seconds=0)

    monkeypatch.setattr("app.integrations.rate_limiter.OutboundRateLimiter.acquire", _allow)


ORDERS_URL = "/api/v1/orders"
SYNC_URL = "/api/v1/orders/sync"
STATISTICS_URL = "/api/v1/orders/statistics"

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "aliexpress"
PUBLISHER_NOT_REGISTERED = json.loads(
    (FIXTURES / "order_get_not_found.json").read_text(encoding="utf-8")
)

EXTERNAL_ORDER_ID = "8123456789012345"

#: Documentation-derived populated order (M16). The envelope shape
#: (`rsp_code`/`rsp_msg`) matches the live capture; the body is documented.
ORDER_DETAIL_PAYLOAD = {
    "aliexpress_ds_trade_order_get_response": {
        "rsp_code": 200,
        "rsp_msg": "Call succeeds",
        "result": {
            "order_id": int(EXTERNAL_ORDER_ID),
            "gmt_create": "2026-07-25 10:30:00",
            "order_status": "WAIT_BUYER_ACCEPT_GOODS",
            "logistics_status": "SELLER_SEND_GOODS",
            "order_amount": {"amount": "23.99", "currency_code": "USD"},
            "child_order_list": {
                "aeop_child_order_info": [
                    {
                        "product_id": 3256806389000685,
                        "product_name": "Wireless Earbuds",
                        "product_count": 2,
                        "product_price": "11.99",
                        "sku_attr": "14:771#White",
                        "child_order_id": 8123456789012346,
                    }
                ]
            },
            "logistics_info_list": {
                "aeop_order_logistics_info": [
                    {"logistics_no": "LP00123456789CN", "logistics_service": "CAINIAO_STANDARD"}
                ]
            },
            "receipt_address": {
                "contact_person": "Test Buyer",
                "address": "12 Mill Lane",
                "city": "Springfield",
                "province": "IL",
                "zip": "62704",
                "country": "US",
            },
        },
    }
}

ORDER_LIST_PAYLOAD = {
    "aliexpress_ds_commissionorder_listbyindex_response": {
        "resp_result": {
            "result": {
                "current_page_no": 1,
                "total_page_no": 1,
                "orders": {"order_dto": [{"order_id": int(EXTERNAL_ORDER_ID)}]},
            }
        }
    }
}

EMPTY_LIST_PAYLOAD = {
    "aliexpress_ds_commissionorder_listbyindex_response": {
        "resp_result": {"result": {"current_page_no": 1, "total_page_no": 1}}
    }
}


def order_supplier_handler(request: httpx.Request) -> httpx.Response:
    """Answer the token exchange, the order list, and the order detail."""
    body = request.content.decode()

    if "/auth/token" in str(request.url):
        return httpx.Response(
            200,
            json={
                "access_token": "issued-access-token",
                "refresh_token": "issued-refresh-token",
                "expires_in": 86400,
            },
        )
    if "aliexpress.ds.commissionorder.listbyindex" in body:
        # Only the first queried status returns the order; the sync
        # de-duplicates identifiers across statuses regardless.
        if "Payment+Completed" in body or "Payment Completed" in body:
            return httpx.Response(200, json=ORDER_LIST_PAYLOAD)
        return httpx.Response(200, json=EMPTY_LIST_PAYLOAD)
    if "aliexpress.ds.trade.order.get" in body:
        return httpx.Response(200, json=ORDER_DETAIL_PAYLOAD)
    return httpx.Response(200, json={"error_response": {"code": "InvalidApiPath"}})


def publisher_not_registered_handler(request: httpx.Request) -> httpx.Response:
    """The captured live failure: the account cannot query orders at all."""
    body = request.content.decode()

    if "/auth/token" in str(request.url):
        return httpx.Response(
            200, json={"access_token": "t", "refresh_token": "r", "expires_in": 8000}
        )
    if "aliexpress.ds.commissionorder.listbyindex" in body:
        return httpx.Response(200, json=ORDER_LIST_PAYLOAD)
    return httpx.Response(200, json=PUBLISHER_NOT_REGISTERED)


async def connected_tenant(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    handler: Any = order_supplier_handler,
    **overrides: Any,
) -> dict[str, str]:
    patch_aliexpress(monkeypatch, handler)
    body = await register(client, **overrides)
    headers = auth_header(body)
    await connect_aliexpress(client, headers)
    return headers


async def synced_tenant(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, **overrides: Any
) -> dict[str, str]:
    headers = await connected_tenant(client, monkeypatch, **overrides)
    response = await client.post(SYNC_URL, json={}, headers=headers)
    assert response.status_code == 201, response.text
    return headers


class TestAuthorization:
    async def test_listing_requires_authentication(self, client: AsyncClient) -> None:
        assert (await client.get(ORDERS_URL)).status_code == 401

    async def test_sync_requires_authentication(self, client: AsyncClient) -> None:
        assert (await client.post(SYNC_URL, json={})).status_code == 401

    async def test_statistics_requires_authentication(self, client: AsyncClient) -> None:
        assert (await client.get(STATISTICS_URL)).status_code == 401

    async def test_listing_is_readable_once_authenticated(self, client: AsyncClient) -> None:
        body = await register(client)
        assert (await client.get(ORDERS_URL, headers=auth_header(body))).status_code == 200


class TestSync:
    async def test_syncs_an_order_end_to_end(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers = await connected_tenant(client, monkeypatch)

        response = await client.post(SYNC_URL, json={}, headers=headers)

        assert response.status_code == 201, response.text
        run = response.json()
        assert run["status"] == "succeeded"
        assert run["ordersSeen"] == 1
        assert run["ordersCreated"] == 1
        assert run["ordersUpdated"] == 0

        listing = (await client.get(ORDERS_URL, headers=headers)).json()
        assert listing["meta"]["totalItems"] == 1
        order = listing["items"][0]
        assert order["externalId"] == EXTERNAL_ORDER_ID
        assert order["fulfillmentStatus"] == "shipped"
        assert order["paymentStatus"] == "paid"
        # Numeric(16, 4) columns serialise at full scale.
        assert order["totalAmount"] == "23.9900"
        assert order["itemCount"] == 1

    async def test_resyncing_updates_rather_than_duplicates(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The idempotency guarantee, exercised through the real unique
        constraint rather than asserted from the code."""
        headers = await synced_tenant(client, monkeypatch)

        second = await client.post(SYNC_URL, json={}, headers=headers)

        assert second.status_code == 201, second.text
        run = second.json()
        assert run["ordersCreated"] == 0
        assert run["ordersUpdated"] == 1

        listing = (await client.get(ORDERS_URL, headers=headers)).json()
        assert listing["meta"]["totalItems"] == 1

    async def test_the_captured_publisher_refusal_fails_the_run_loudly(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Replays the **real captured** `rsp_code 401` envelope. The run must
        fail as an account-level problem, not degrade into "every order is
        individually missing"."""
        headers = await connected_tenant(client, monkeypatch, publisher_not_registered_handler)

        response = await client.post(SYNC_URL, json={}, headers=headers)

        assert response.status_code == 400, response.text
        assert response.json()["code"] == "aliexpress_auth_failed"

    async def test_the_sync_window_is_bounded(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers = await connected_tenant(client, monkeypatch)

        response = await client.post(SYNC_URL, json={"sinceDays": 365}, headers=headers)

        assert response.status_code == 422

    async def test_sync_without_a_connection_is_rejected(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        patch_aliexpress(monkeypatch, order_supplier_handler)
        body = await register(client)

        response = await client.post(SYNC_URL, json={}, headers=auth_header(body))

        assert response.status_code in (404, 409), response.text


class TestListing:
    async def test_filters_by_fulfillment_status(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers = await synced_tenant(client, monkeypatch)

        shipped = await client.get(ORDERS_URL, params={"status": "shipped"}, headers=headers)
        delivered = await client.get(ORDERS_URL, params={"status": "delivered"}, headers=headers)

        assert shipped.json()["meta"]["totalItems"] == 1
        assert delivered.json()["meta"]["totalItems"] == 0

    async def test_searches_by_external_id(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers = await synced_tenant(client, monkeypatch)

        found = await client.get(ORDERS_URL, params={"q": EXTERNAL_ORDER_ID}, headers=headers)
        missed = await client.get(ORDERS_URL, params={"q": "no-such-order"}, headers=headers)

        assert found.json()["meta"]["totalItems"] == 1
        assert missed.json()["meta"]["totalItems"] == 0

    async def test_filters_by_date_window(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The date filter applies to when the buyer placed the order
        (2026-07-25 in the documented payload), not when it was synced."""
        headers = await synced_tenant(client, monkeypatch)

        inside = await client.get(
            ORDERS_URL,
            params={"dateFrom": "2026-07-01T00:00:00Z", "dateTo": "2026-07-31T00:00:00Z"},
            headers=headers,
        )
        outside = await client.get(
            ORDERS_URL, params={"dateTo": "2026-01-01T00:00:00Z"}, headers=headers
        )

        assert inside.json()["meta"]["totalItems"] == 1
        assert outside.json()["meta"]["totalItems"] == 0

    async def test_an_unknown_sort_field_is_rejected(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers = await synced_tenant(client, monkeypatch)

        response = await client.get(
            ORDERS_URL, params={"sort_by": "recipient_phone"}, headers=headers
        )

        assert response.status_code == 422


class TestDetailAndTimeline:
    async def test_the_detail_carries_items_shipments_and_address(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers = await synced_tenant(client, monkeypatch)
        listing = (await client.get(ORDERS_URL, headers=headers)).json()
        order_id = listing["items"][0]["id"]

        detail = (await client.get(f"{ORDERS_URL}/{order_id}", headers=headers)).json()

        assert detail["recipientName"] == "Test Buyer"
        assert detail["city"] == "Springfield"
        assert len(detail["items"]) == 1
        assert detail["items"][0]["skuAttributes"] == "14:771#White"
        assert detail["items"][0]["unitPrice"] == "11.9900"
        assert len(detail["shipments"]) == 1
        assert detail["shipments"][0]["trackingNumber"] == "LP00123456789CN"
        assert detail["shipments"][0]["status"] == "in_transit"

    async def test_the_timeline_tells_the_story_in_order(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two syncs across a status progression: the first import creates the
        order *at* the supplier's state (no synthetic transition), the second
        records the processing -> shipped move as a status-change event."""
        detail_calls = 0

        def progressing_handler(request: httpx.Request) -> httpx.Response:
            nonlocal detail_calls
            body = request.content.decode()
            if "/auth/token" in str(request.url):
                return httpx.Response(
                    200, json={"access_token": "t", "refresh_token": "r", "expires_in": 86400}
                )
            if "aliexpress.ds.commissionorder.listbyindex" in body:
                if "Payment+Completed" in body or "Payment Completed" in body:
                    return httpx.Response(200, json=ORDER_LIST_PAYLOAD)
                return httpx.Response(200, json=EMPTY_LIST_PAYLOAD)
            if "aliexpress.ds.trade.order.get" in body:
                detail_calls += 1
                payload = json.loads(json.dumps(ORDER_DETAIL_PAYLOAD))
                result = payload["aliexpress_ds_trade_order_get_response"]["result"]
                result["order_status"] = (
                    "WAIT_SELLER_SEND_GOODS" if detail_calls == 1 else "WAIT_BUYER_ACCEPT_GOODS"
                )
                return httpx.Response(200, json=payload)
            return httpx.Response(200, json={"error_response": {"code": "InvalidApiPath"}})

        headers = await connected_tenant(client, monkeypatch, progressing_handler)
        assert (await client.post(SYNC_URL, json={}, headers=headers)).status_code == 201
        assert (await client.post(SYNC_URL, json={}, headers=headers)).status_code == 201

        listing = (await client.get(ORDERS_URL, headers=headers)).json()
        order = listing["items"][0]
        assert order["fulfillmentStatus"] == "shipped"

        timeline = (
            await client.get(f"{ORDERS_URL}/{order['id']}/timeline", headers=headers)
        ).json()

        event_types = [entry["eventType"] for entry in timeline if entry["kind"] == "order"]
        assert "created" in event_types
        assert "synced" in event_types
        assert "status_change" in event_types

        change = next(e for e in timeline if e["eventType"] == "status_change")
        assert change["fromStatus"] == "processing"
        assert change["toStatus"] == "shipped"

        occurred = [entry["occurredAt"] for entry in timeline]
        assert occurred == sorted(occurred)

    async def test_an_unknown_order_is_404(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers = await synced_tenant(client, monkeypatch)

        response = await client.get(
            f"{ORDERS_URL}/00000000-0000-4000-8000-000000000000", headers=headers
        )

        assert response.status_code == 404


class TestTenantIsolation:
    async def test_another_tenants_order_is_404_not_403(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A 403 would confirm the order exists and enable enumeration."""
        headers_a = await synced_tenant(client, monkeypatch)
        listing = (await client.get(ORDERS_URL, headers=headers_a)).json()
        order_id = listing["items"][0]["id"]

        body_b = await register(client, companyName="Tenant B Ltd")
        headers_b = auth_header(body_b)

        response = await client.get(f"{ORDERS_URL}/{order_id}", headers=headers_b)

        assert response.status_code == 404
        timeline = await client.get(f"{ORDERS_URL}/{order_id}/timeline", headers=headers_b)
        assert timeline.status_code == 404

    async def test_listings_do_not_leak_across_tenants(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await synced_tenant(client, monkeypatch)

        body_b = await register(client, companyName="Tenant C Ltd")

        listing = (await client.get(ORDERS_URL, headers=auth_header(body_b))).json()

        assert listing["meta"]["totalItems"] == 0


class TestStatistics:
    async def test_an_empty_tenant_reports_zeros(self, client: AsyncClient) -> None:
        body = await register(client)

        stats = (await client.get(STATISTICS_URL, headers=auth_header(body))).json()

        assert stats["totalOrders"] == 0
        assert stats["lastSync"] is None
        assert stats["failedSyncsLast7Days"] == 0

    async def test_statistics_reflect_a_completed_sync(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers = await synced_tenant(client, monkeypatch)

        stats = (await client.get(STATISTICS_URL, headers=headers)).json()

        assert stats["totalOrders"] == 1
        assert stats["byStatus"]["shipped"] == 1
        assert stats["lastSync"]["status"] == "succeeded"
        assert stats["lastSync"]["ordersSeen"] == 1

    async def test_a_failed_run_is_counted(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers = await connected_tenant(client, monkeypatch, publisher_not_registered_handler)
        await client.post(SYNC_URL, json={}, headers=headers)

        stats = (await client.get(STATISTICS_URL, headers=headers)).json()

        assert stats["failedSyncsLast7Days"] == 1
        assert stats["lastSync"]["status"] == "failed"
        assert stats["lastSync"]["errorCode"] == "AliExpressAuthError"
