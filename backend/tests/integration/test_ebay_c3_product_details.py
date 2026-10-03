"""EBAY-C3a — a product's eBay category and item specifics, over HTTP.

eBay's Taxonomy API is faked on the wire; it is called with an *application*
token (client-credentials grant), which the fake records so the tests can
prove no seller token is used and the token is minted once and reused.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.ebay.taxonomy import forget_category_trees
from app.integrations.ebay.tokens import forget_application_token
from tests.integration.ebay_c1_live import ACCESS_TOKEN, FakeEbay, install
from tests.integration.test_ebay_c1_api import auth_header, register, token_with_roles
from tests.integration.test_ebay_c2_listing_setup import (
    fresh_redis as fresh_redis,  # autouse: a Redis client per test event loop
)
from tests.integration.test_global_rules_api import seed_product

pytestmark = pytest.mark.integration

APP_TOKEN = "v^1.1#i^1#APPLICATION-NOT-A-REAL-TOKEN"


def details_url(product_id: uuid.UUID) -> str:
    return f"/api/v1/integrations/ebay/products/{product_id}/details"


def suggestions_url(product_id: uuid.UUID) -> str:
    return f"/api/v1/integrations/ebay/products/{product_id}/category-suggestions"


class TaxonomyFakeEbay(FakeEbay):
    def __init__(self) -> None:
        super().__init__()
        self.taxonomy_requests: list[httpx.Request] = []
        self.taxonomy_status = 200
        self.app_token_status = 200

    async def _token(self, request: httpx.Request) -> httpx.Response:
        body = dict(httpx.QueryParams(request.content.decode()))
        if body.get("grant_type") == "client_credentials":
            self.token_requests.append(body)
            if self.app_token_status != 200:
                return httpx.Response(self.app_token_status, json={"error": "invalid_client"})
            return httpx.Response(
                200,
                json={
                    "access_token": APP_TOKEN,
                    "expires_in": 7200,
                    "token_type": "Application Access Token",
                },
            )
        return await super()._token(request)

    async def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if not path.startswith("/commerce/taxonomy/"):
            return await super().handler(request)
        self.taxonomy_requests.append(request)
        assert request.headers["Authorization"] == f"Bearer {APP_TOKEN}"
        if self.taxonomy_status != 200:
            return httpx.Response(self.taxonomy_status, json={"errors": []})
        if path.endswith("/get_default_category_tree_id"):
            return httpx.Response(200, json={"categoryTreeId": "0"})
        if path.endswith("/get_category_suggestions"):
            return httpx.Response(
                200,
                json={
                    "categorySuggestions": [
                        {
                            "category": {"categoryId": "20625", "categoryName": "Mugs"},
                            "categoryTreeNodeAncestors": [
                                {"categoryName": "Kitchen, Dining & Bar"},
                                {"categoryName": "Home & Garden"},
                            ],
                        }
                    ]
                },
            )
        if path.endswith("/get_item_aspects_for_category"):
            return httpx.Response(
                200,
                json={
                    "aspects": [
                        {
                            "localizedAspectName": "Colour",
                            "aspectConstraint": {
                                "aspectRequired": False,
                                "aspectMode": "FREE_TEXT",
                            },
                            "aspectValues": [{"localizedValue": "Red"}],
                        },
                        {
                            "localizedAspectName": "Brand",
                            "aspectConstraint": {
                                "aspectRequired": True,
                                "aspectMode": "FREE_TEXT",
                                "itemToAspectCardinality": "SINGLE",
                            },
                        },
                    ]
                },
            )
        raise AssertionError(f"unexpected taxonomy request: {request.url}")

    @property
    def app_token_mints(self) -> int:
        return len(self.grants("client_credentials"))


@pytest.fixture(autouse=True)
def fresh_caches() -> Iterator[None]:
    forget_application_token()
    forget_category_trees()
    yield
    forget_application_token()
    forget_category_trees()


@pytest.fixture
def ebay(monkeypatch: pytest.MonkeyPatch) -> TaxonomyFakeEbay:
    fake = TaxonomyFakeEbay()
    install(monkeypatch, fake)
    return fake


@pytest.fixture
async def owner(client: AsyncClient) -> AsyncIterator[dict[str, Any]]:
    yield await register(client)


async def test_suggestions_come_from_the_title_with_an_application_token(
    client: AsyncClient, db_session: AsyncSession, ebay: TaxonomyFakeEbay, owner: dict[str, Any]
) -> None:
    product_id = await seed_product(db_session, owner)

    response = await client.get(
        suggestions_url(product_id), params={"marketplaceId": "EBAY_GB"}, headers=auth_header(owner)
    )

    assert response.status_code == 200, response.text
    assert response.json() == [
        {
            "categoryId": "20625",
            "name": "Mugs",
            "path": "Home & Garden > Kitchen, Dining & Bar > Mugs",
        }
    ]
    query = next(
        r for r in ebay.taxonomy_requests if r.url.path.endswith("get_category_suggestions")
    )
    assert query.url.params["q"] == "Scope target"
    assert all(
        r.headers["Authorization"] != f"Bearer {ACCESS_TOKEN}" for r in ebay.taxonomy_requests
    )


async def test_save_then_read_reports_missing_required_aspects(
    client: AsyncClient, db_session: AsyncSession, ebay: TaxonomyFakeEbay, owner: dict[str, Any]
) -> None:
    product_id = await seed_product(db_session, owner)
    headers = auth_header(owner)

    saved = await client.put(
        details_url(product_id),
        json={
            "marketplaceId": "EBAY_GB",
            "categoryId": "20625",
            "categoryName": "Mugs",
            "aspects": {"Colour": ["Red", " "]},
        },
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["aspects"] == {"Colour": ["Red"]}
    assert body["missingRequired"] == ["Brand"]
    assert [a["name"] for a in body["categoryAspects"]] == ["Brand", "Colour"]  # required first

    filled = await client.put(
        details_url(product_id),
        json={"marketplaceId": "EBAY_GB", "categoryId": "20625", "aspects": {"Brand": ["Acme"]}},
        headers=headers,
    )
    assert filled.json()["missingRequired"] == []

    read = await client.get(
        details_url(product_id), params={"marketplaceId": "EBAY_GB"}, headers=headers
    )
    assert read.json()["aspects"] == {"Brand": ["Acme"]}
    # One application token for the whole sequence: it is cached, not re-minted.
    assert ebay.app_token_mints == 1


async def test_nothing_chosen_yet_needs_no_ebay_call(
    client: AsyncClient, db_session: AsyncSession, ebay: TaxonomyFakeEbay, owner: dict[str, Any]
) -> None:
    product_id = await seed_product(db_session, owner)
    response = await client.get(details_url(product_id), headers=auth_header(owner))
    assert response.status_code == 200
    assert response.json()["categoryId"] is None
    assert ebay.taxonomy_requests == []


@pytest.mark.parametrize(
    "payload",
    [
        {"marketplaceId": "EBAY_GB", "categoryId": "../x", "aspects": {}},
        {"marketplaceId": "EBAY_XX", "categoryId": "20625", "aspects": {}},
        {"marketplaceId": "EBAY_GB", "categoryId": "20625", "aspects": {"Brand": ["x" * 66]}},
    ],
)
async def test_invalid_details_are_refused_before_ebay(
    client: AsyncClient,
    db_session: AsyncSession,
    ebay: TaxonomyFakeEbay,
    owner: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    product_id = await seed_product(db_session, owner)
    response = await client.put(details_url(product_id), json=payload, headers=auth_header(owner))
    assert response.status_code == 422
    assert ebay.taxonomy_requests == []


async def test_a_member_may_read_but_not_save(
    client: AsyncClient, db_session: AsyncSession, ebay: TaxonomyFakeEbay, owner: dict[str, Any]
) -> None:
    product_id = await seed_product(db_session, owner)
    member = token_with_roles(owner, "member")
    assert (await client.get(details_url(product_id), headers=member)).status_code == 200
    response = await client.put(
        details_url(product_id),
        json={"marketplaceId": "EBAY_GB", "categoryId": "20625", "aspects": {}},
        headers=member,
    )
    assert response.status_code == 403


async def test_another_workspace_gets_404_for_the_product(
    client: AsyncClient, db_session: AsyncSession, ebay: TaxonomyFakeEbay, owner: dict[str, Any]
) -> None:
    product_id = await seed_product(db_session, owner)
    other = auth_header(await register(client))
    assert (await client.get(details_url(product_id), headers=other)).status_code == 404
    response = await client.put(
        details_url(product_id),
        json={"marketplaceId": "EBAY_GB", "categoryId": "20625", "aspects": {}},
        headers=other,
    )
    assert response.status_code == 404


async def test_a_taxonomy_401_drops_the_application_token(
    client: AsyncClient, db_session: AsyncSession, ebay: TaxonomyFakeEbay, owner: dict[str, Any]
) -> None:
    product_id = await seed_product(db_session, owner)
    headers = auth_header(owner)
    ebay.taxonomy_status = 401
    failed = await client.get(suggestions_url(product_id), headers=headers)
    assert failed.status_code >= 500
    assert failed.json()["code"] == "ebay_seller_api_unavailable"

    ebay.taxonomy_status = 200
    ok = await client.get(suggestions_url(product_id), headers=headers)
    assert ok.status_code == 200
    assert ebay.app_token_mints == 2
