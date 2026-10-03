"""EBAY-C2 — listing setup over HTTP, with only eBay's wire faked.

Real database, encryption, authorization and Redis, as in the C1 API tests.
``SellerFakeEbay`` extends C1's ``FakeEbay`` with the Account and Inventory
endpoints; an unexpected URL still raises rather than reaching the network.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import RedisPurpose, close_redis_clients, get_redis
from app.integrations.ebay.deletion import DeletionSubject, EbayListingDefaultsOwner
from app.models.ebay import EbayConnection, EbayConnectionStatus, EbayListingDefaults
from tests.integration.ebay_c1_live import ACCESS_TOKEN, SELLER_USER_ID, FakeEbay, install
from tests.integration.test_ebay_c1_api import (
    auth_header,
    connect_fully,
    register,
    token_with_roles,
)

pytestmark = pytest.mark.integration

SETUP_URL = "/api/v1/integrations/ebay/listing-setup"
DEFAULTS_URL = "/api/v1/integrations/ebay/listing-defaults"
LOCATIONS_URL = "/api/v1/integrations/ebay/locations"
DISCONNECT_URL = "/api/v1/integrations/ebay/disconnect"

CHOICE = {
    "marketplaceId": "EBAY_US",
    "fulfillmentPolicyId": "6000001",
    "paymentPolicyId": "6000002",
    "returnPolicyId": "6000003",
    "merchantLocationKey": "warehouse-1",
}


class SellerFakeEbay(FakeEbay):
    def __init__(self) -> None:
        super().__init__()
        self.opted_in = True
        self.seller_status = 200
        self.locations: list[dict[str, Any]] = [
            {
                "merchantLocationKey": "warehouse-1",
                "name": "Main warehouse",
                "merchantLocationStatus": "ENABLED",
                "location": {"address": {"city": "Austin", "postalCode": "78701", "country": "US"}},
            },
            {"merchantLocationKey": "old-shed", "merchantLocationStatus": "DISABLED"},
        ]
        self.seller_requests: list[httpx.Request] = []

    async def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if not path.startswith("/sell/"):
            return await super().handler(request)
        self.seller_requests.append(request)
        assert request.headers["Authorization"] == f"Bearer {ACCESS_TOKEN}"
        if self.seller_status != 200:
            return httpx.Response(self.seller_status, json={"errors": [{"errorId": 1}]})
        if path == "/sell/account/v1/program/get_opted_in_programs":
            programs = [{"programType": "SELLING_POLICY_MANAGEMENT"}] if self.opted_in else []
            return httpx.Response(200, json={"programs": programs})
        if path == "/sell/account/v1/fulfillment_policy":
            assert request.url.params["marketplace_id"] == "EBAY_US"
            return httpx.Response(
                200,
                json={"fulfillmentPolicies": [{"fulfillmentPolicyId": "6000001", "name": "Free"}]},
            )
        if path == "/sell/account/v1/payment_policy":
            return httpx.Response(
                200, json={"paymentPolicies": [{"paymentPolicyId": "6000002", "name": "Managed"}]}
            )
        if path == "/sell/account/v1/return_policy":
            return httpx.Response(
                200, json={"returnPolicies": [{"returnPolicyId": "6000003", "name": "30 days"}]}
            )
        if path == "/sell/inventory/v1/location" and request.method == "GET":
            return httpx.Response(200, json={"locations": self.locations})
        if path.startswith("/sell/inventory/v1/location/") and request.method == "POST":
            key = path.rsplit("/", 1)[1]
            body = json.loads(request.content)
            self.locations.append(
                {"merchantLocationKey": key, "merchantLocationStatus": "ENABLED", **body}
            )
            return httpx.Response(204)
        raise AssertionError(f"unexpected outbound eBay request: {request.url}")


@pytest.fixture(autouse=True)
async def fresh_redis() -> AsyncIterator[None]:
    await close_redis_clients()
    client = get_redis(RedisPurpose.CACHE)
    stale = await client.keys("ebay:oauth:state:*")
    if stale:
        await client.delete(*stale)
    yield
    await close_redis_clients()


@pytest.fixture
def ebay(monkeypatch: pytest.MonkeyPatch) -> SellerFakeEbay:
    fake = SellerFakeEbay()
    install(monkeypatch, fake)
    return fake


async def connected(client: AsyncClient) -> dict[str, str]:
    body = await register(client)
    headers = auth_header(body)
    await connect_fully(client, headers)
    return headers


class TestRead:
    async def test_policies_locations_and_no_defaults_yet(
        self, client: AsyncClient, ebay: SellerFakeEbay
    ) -> None:
        headers = await connected(client)

        response = await client.get(SETUP_URL, params={"marketplaceId": "EBAY_US"}, headers=headers)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["businessPoliciesEnabled"] is True
        assert body["fulfillmentPolicies"] == [{"id": "6000001", "name": "Free"}]
        assert body["paymentPolicies"] == [{"id": "6000002", "name": "Managed"}]
        assert body["returnPolicies"] == [{"id": "6000003", "name": "30 days"}]
        assert [loc["key"] for loc in body["locations"]] == ["warehouse-1", "old-shed"]
        assert body["locations"][0]["city"] == "Austin"
        assert body["locations"][1]["enabled"] is False
        assert body["defaults"] is None
        assert "EBAY_GB" in body["supportedMarketplaces"]
        for secret in (ACCESS_TOKEN, SELLER_USER_ID):
            assert secret not in response.text

    async def test_not_opted_in_reports_null_policies_without_calling_them(
        self, client: AsyncClient, ebay: SellerFakeEbay
    ) -> None:
        ebay.opted_in = False
        headers = await connected(client)

        body = (await client.get(SETUP_URL, headers=headers)).json()

        assert body["businessPoliciesEnabled"] is False
        assert body["fulfillmentPolicies"] is None
        assert not any("_policy" in r.url.path for r in ebay.seller_requests)

    async def test_a_viewer_may_not_read_setup(
        self, client: AsyncClient, ebay: SellerFakeEbay
    ) -> None:
        body = await register(client)
        await connect_fully(client, auth_header(body))
        response = await client.get(SETUP_URL, headers=token_with_roles(body, "viewer"))
        assert response.status_code == 403

    async def test_without_a_connection_the_answer_is_a_clear_409(
        self, client: AsyncClient, ebay: SellerFakeEbay
    ) -> None:
        headers = auth_header(await register(client))
        response = await client.get(SETUP_URL, headers=headers)
        assert response.status_code == 409
        assert response.json()["code"] == "ebay_not_connected"

    async def test_an_unsupported_marketplace_is_refused_before_calling_ebay(
        self, client: AsyncClient, ebay: SellerFakeEbay
    ) -> None:
        headers = await connected(client)
        response = await client.get(SETUP_URL, params={"marketplaceId": "EBAY_XX"}, headers=headers)
        assert response.status_code == 422
        assert ebay.seller_requests == []

    async def test_a_401_from_ebay_marks_the_connection_for_reconnect(
        self, client: AsyncClient, ebay: SellerFakeEbay
    ) -> None:
        headers = await connected(client)
        ebay.seller_status = 401
        response = await client.get(SETUP_URL, headers=headers)
        assert response.status_code == 422
        assert response.json()["code"] == "ebay_reconnect_required"

    async def test_a_5xx_from_ebay_is_a_retryable_upstream_error(
        self, client: AsyncClient, ebay: SellerFakeEbay
    ) -> None:
        headers = await connected(client)
        ebay.seller_status = 500
        response = await client.get(SETUP_URL, headers=headers)
        assert response.status_code >= 500
        assert response.json()["code"] == "ebay_seller_api_unavailable"


class TestSaveDefaults:
    async def test_save_then_read_back(self, client: AsyncClient, ebay: SellerFakeEbay) -> None:
        headers = await connected(client)

        saved = await client.put(DEFAULTS_URL, json=CHOICE, headers=headers)
        assert saved.status_code == 200, saved.text
        assert saved.json()["merchantLocationKey"] == "warehouse-1"

        again = await client.put(
            DEFAULTS_URL, json={**CHOICE, "fulfillmentPolicyId": "6000001"}, headers=headers
        )
        assert again.status_code == 200

        body = (await client.get(SETUP_URL, headers=headers)).json()
        assert body["defaults"]["paymentPolicyId"] == "6000002"

    @pytest.mark.parametrize(
        "override",
        [
            {"fulfillmentPolicyId": "not-the-sellers"},
            {"returnPolicyId": "deleted-in-seller-hub"},
            {"merchantLocationKey": "old-shed"},  # disabled
        ],
    )
    async def test_a_stale_or_foreign_id_is_refused(
        self, client: AsyncClient, ebay: SellerFakeEbay, override: dict[str, str]
    ) -> None:
        headers = await connected(client)
        response = await client.put(DEFAULTS_URL, json={**CHOICE, **override}, headers=headers)
        assert response.status_code == 422
        assert response.json()["code"] == "ebay_policy_not_found"

    async def test_a_member_may_not_save(self, client: AsyncClient, ebay: SellerFakeEbay) -> None:
        body = await register(client)
        await connect_fully(client, auth_header(body))
        response = await client.put(
            DEFAULTS_URL, json=CHOICE, headers=token_with_roles(body, "member")
        )
        assert response.status_code == 403

    async def test_defaults_are_not_visible_to_another_workspace(
        self, client: AsyncClient, ebay: SellerFakeEbay
    ) -> None:
        first = await connected(client)
        assert (await client.put(DEFAULTS_URL, json=CHOICE, headers=first)).status_code == 200

        other = auth_header(await register(client))
        # A second workspace cannot connect the same seller (C1), so it has no
        # connection — and therefore no way to see the first one's defaults.
        response = await client.get(SETUP_URL, headers=other)
        assert response.status_code == 409


class TestCreateLocation:
    async def test_creates_a_generated_key_and_it_becomes_choosable(
        self, client: AsyncClient, ebay: SellerFakeEbay
    ) -> None:
        headers = await connected(client)

        response = await client.post(
            LOCATIONS_URL,
            json={"name": "Second warehouse", "postalCode": "10001", "country": "US"},
            headers=headers,
        )

        assert response.status_code == 201, response.text
        key = response.json()["key"]
        assert key.startswith("droppilot-") and len(key) <= 36
        sent = json.loads(ebay.seller_requests[-1].content)
        assert sent["location"]["address"] == {"country": "US", "postalCode": "10001"}
        assert sent["locationTypes"] == ["WAREHOUSE"]

        saved = await client.put(
            DEFAULTS_URL, json={**CHOICE, "merchantLocationKey": key}, headers=headers
        )
        assert saved.status_code == 200, saved.text

    async def test_an_address_eBay_would_reject_is_refused_first(
        self, client: AsyncClient, ebay: SellerFakeEbay
    ) -> None:
        headers = await connected(client)
        response = await client.post(
            LOCATIONS_URL, json={"name": "No postcode", "country": "US"}, headers=headers
        )
        assert response.status_code == 422
        assert not any(r.method == "POST" for r in ebay.seller_requests)


class TestErasure:
    async def test_disconnect_removes_the_defaults(
        self, client: AsyncClient, ebay: SellerFakeEbay, db_session: AsyncSession
    ) -> None:
        headers = await connected(client)
        assert (await client.put(DEFAULTS_URL, json=CHOICE, headers=headers)).status_code == 200

        assert (await client.delete(DISCONNECT_URL, headers=headers)).status_code == 200

        remaining = await db_session.scalar(
            sa.select(sa.func.count()).select_from(EbayListingDefaults)
        )
        assert remaining == 0

    async def test_the_deletion_owner_erases_by_immutable_id_and_is_idempotent(
        self, client: AsyncClient, ebay: SellerFakeEbay, db_session: AsyncSession
    ) -> None:
        headers = await connected(client)
        assert (await client.put(DEFAULTS_URL, json=CHOICE, headers=headers)).status_code == 200

        owner = EbayListingDefaultsOwner()
        nobody = await owner.erase(
            db_session, DeletionSubject(user_id="someone-else", username=None, eias_token=None)
        )
        first = await owner.erase(
            db_session, DeletionSubject(user_id=SELLER_USER_ID, username=None, eias_token=None)
        )
        second = await owner.erase(
            db_session, DeletionSubject(user_id=SELLER_USER_ID, username=None, eias_token=None)
        )

        assert (nobody, first, second) == (0, 1, 0)
        connection = await db_session.scalar(
            sa.select(EbayConnection).where(EbayConnection.ebay_user_id == SELLER_USER_ID)
        )
        assert connection is not None
        assert connection.status is EbayConnectionStatus.CONNECTED
