"""Integration tests for the product catalogue endpoints.

Drives the real HTTP pipeline — auth, dependencies, repository, database — with
only the AliExpress network boundary replaced. The payload the mock returns is
the **real captured response** committed under ``tests/fixtures/aliexpress/``,
so what these tests assert about parsing is what the supplier actually sends.
"""

from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from fakeredis import aioredis as fake_aioredis
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import set_tenant_id
from app.integrations.aliexpress import client as client_module
from app.integrations.aliexpress import service as service_module
from app.models.shopify import ListingSyncStatus, StoreListing
from app.models.store import Store, StorePlatform, StoreStatus
from tests.integration.conftest import registration_payload

pytestmark = pytest.mark.integration

PRODUCTS_URL = "/api/v1/products"
IMPORT_URL = "/api/v1/products/import"
IMPORTS_URL = "/api/v1/products/imports"
CONNECT_URL = "/api/v1/integrations/aliexpress/connect"
CALLBACK_URL = "/api/v1/integrations/aliexpress/callback"

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "aliexpress"
PRODUCT_PAYLOAD = json.loads((FIXTURES / "product.json").read_text(encoding="utf-8"))
FEED_PAYLOAD = json.loads((FIXTURES / "feed_working.json").read_text(encoding="utf-8"))

#: The identifier inside the captured payload.
REAL_PRODUCT_ID = "3256806389000685"


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> fake_aioredis.FakeRedis:
    """In-process Redis for the OAuth state store."""
    redis = fake_aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(service_module, "get_redis", lambda _purpose: redis)
    return redis


@pytest.fixture(autouse=True)
def _allow_outbound(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bypass the outbound rate limiter, which has its own tests."""
    from app.integrations.rate_limiter import RateLimitDecision

    async def _allow(self: Any, tenant_id: str) -> RateLimitDecision:
        return RateLimitDecision(allowed=True, remaining=99, retry_after_seconds=0)

    monkeypatch.setattr("app.integrations.rate_limiter.OutboundRateLimiter.acquire", _allow)


#: The handler currently answering supplier calls.
#:
#: A mutable holder rather than re-patching, because patching twice makes the
#: second stub inherit from the first and the parent's ``__init__`` overwrites
#: the transport — so the *first* handler silently keeps answering and a test
#: that swapped in a failure stub would quietly assert against success.
_CURRENT_HANDLER: dict[str, Any] = {}


def patch_aliexpress(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    """Route supplier calls to `handler`. Safe to call repeatedly."""
    _CURRENT_HANDLER["fn"] = handler

    def dispatch(request: httpx.Request) -> httpx.Response:
        return _CURRENT_HANDLER["fn"](request)

    if getattr(client_module.httpx.AsyncClient, "_droppilot_stub", False):
        return

    class _Patched(httpx.AsyncClient):
        _droppilot_stub = True

        def __init__(self, **kwargs: Any) -> None:
            kwargs["transport"] = httpx.MockTransport(dispatch)
            super().__init__(**kwargs)

    monkeypatch.setattr(client_module.httpx, "AsyncClient", _Patched)


def supplier_handler(request: httpx.Request) -> httpx.Response:
    """Answer both the token exchange and the catalogue calls.

    One handler because a single import touches both: the service refreshes the
    token if needed before calling the product endpoint.
    """
    body = request.content.decode()

    if "/auth/token" in str(request.url):
        return httpx.Response(
            200,
            json={
                "access_token": "issued-access-token",
                "refresh_token": "issued-refresh-token",
                "expires_in": 86400,
                "user_id": "seller-1",
            },
        )
    if "aliexpress.ds.product.get" in body:
        return httpx.Response(200, json=PRODUCT_PAYLOAD)
    if "aliexpress.ds.recommend.feed.get" in body:
        return httpx.Response(200, json=FEED_PAYLOAD)
    return httpx.Response(200, json={"error_response": {"code": "InvalidApiPath"}})


def not_found_handler(request: httpx.Request) -> httpx.Response:
    """A delisted product: a well-formed envelope carrying no result."""
    if "/auth/token" in str(request.url):
        return httpx.Response(
            200, json={"access_token": "t", "refresh_token": "r", "expires_in": 8000}
        )
    return httpx.Response(
        200,
        json={
            "aliexpress_ds_product_get_response": {"rsp_code": 605, "rsp_msg": "ITEM_ID_NOT_FOUND"}
        },
    )


def ship_to_prohibited_handler(request: httpx.Request) -> httpx.Response:
    """Live shape for rsp_code 482: empty-ish result, no product_id."""
    if "/auth/token" in str(request.url):
        return httpx.Response(
            200, json={"access_token": "t", "refresh_token": "r", "expires_in": 8000}
        )
    return httpx.Response(
        200,
        json={
            "aliexpress_ds_product_get_response": {
                "rsp_code": 482,
                "rsp_msg": "SHIP_TO_COUNTRY_PROHIBITED",
                "result": {"has_whole_sale": False},
            }
        },
    )


async def register(client: AsyncClient, **overrides: Any) -> dict[str, Any]:
    response = await client.post("/api/v1/auth/register", json=registration_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


def auth_header(body: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {body['tokens']['accessToken']}"}


async def connect_aliexpress(client: AsyncClient, headers: dict[str, str]) -> None:
    """Take a tenant through OAuth so imports have credentials to use."""
    response = await client.post(CONNECT_URL, json={}, headers=headers)
    assert response.status_code == 201, response.text
    state = response.json()["state"]

    callback = await client.get(
        CALLBACK_URL,
        params={"code": "auth-code", "state": state},
        follow_redirects=False,
    )
    assert callback.status_code == 303
    assert "aliexpress=connected" in callback.headers["location"]


async def connected_tenant(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, **overrides: Any
) -> dict[str, str]:
    patch_aliexpress(monkeypatch, supplier_handler)
    body = await register(client, **overrides)
    headers = auth_header(body)
    await connect_aliexpress(client, headers)
    return headers


class TestAuthorization:
    async def test_listing_requires_authentication(self, client: AsyncClient) -> None:
        assert (await client.get(PRODUCTS_URL)).status_code == 401

    async def test_import_requires_authentication(self, client: AsyncClient) -> None:
        response = await client.post(IMPORT_URL, json={"externalId": REAL_PRODUCT_ID})
        assert response.status_code == 401

    async def test_listing_is_readable_by_any_role(self, client: AsyncClient) -> None:
        body = await register(client)
        assert (await client.get(PRODUCTS_URL, headers=auth_header(body))).status_code == 200

    async def test_imports_history_requires_authentication(self, client: AsyncClient) -> None:
        assert (await client.get(IMPORTS_URL)).status_code == 401


class TestImport:
    async def test_imports_a_product_from_a_real_payload(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers = await connected_tenant(client, monkeypatch)

        response = await client.post(
            IMPORT_URL, json={"externalId": REAL_PRODUCT_ID}, headers=headers
        )

        assert response.status_code == 201, response.text
        product = response.json()
        assert product["externalId"] == REAL_PRODUCT_ID
        assert "Realme GT Neo5" in product["title"]
        assert product["status"] == "draft"

    async def test_variants_and_images_are_stored(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers = await connected_tenant(client, monkeypatch)
        product = (
            await client.post(IMPORT_URL, json={"externalId": REAL_PRODUCT_ID}, headers=headers)
        ).json()

        assert len(product["variants"]) == 12
        assert len(product["images"]) == 6
        assert product["images"][0]["position"] == 0

    async def test_the_composite_variant_key_survives_the_round_trip(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An order must echo it back exactly, so it must survive storage."""
        headers = await connected_tenant(client, monkeypatch)
        product = (
            await client.post(IMPORT_URL, json={"externalId": REAL_PRODUCT_ID}, headers=headers)
        ).json()

        keys = [v["externalAttributes"] for v in product["variants"]]
        assert "10:529#Realme GT Neo5;14:771#tyjt white" in keys

    async def test_import_is_idempotent(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Importing twice refreshes rather than duplicating."""
        headers = await connected_tenant(client, monkeypatch)

        first = await client.post(IMPORT_URL, json={"externalId": REAL_PRODUCT_ID}, headers=headers)
        second = await client.post(
            IMPORT_URL, json={"externalId": REAL_PRODUCT_ID}, headers=headers
        )

        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json()["id"] == second.json()["id"]

        # Imports land in Drafts, not Products (publication projection).
        drafts = (await client.get("/api/v1/drafts", headers=headers)).json()
        assert drafts["meta"]["totalItems"] == 1
        published = (await client.get(PRODUCTS_URL, headers=headers)).json()
        assert published["meta"]["totalItems"] == 0

    async def test_reimport_does_not_duplicate_variants_or_images(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Children are replaced, not appended.

        Appending would double them on every sync, which is the kind of bug that
        only shows up in production a week later.
        """
        headers = await connected_tenant(client, monkeypatch)
        await client.post(IMPORT_URL, json={"externalId": REAL_PRODUCT_ID}, headers=headers)
        product = (
            await client.post(IMPORT_URL, json={"externalId": REAL_PRODUCT_ID}, headers=headers)
        ).json()

        assert len(product["variants"]) == 12
        assert len(product["images"]) == 6

    async def test_a_delisted_product_fails_without_creating_a_product(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers = await connected_tenant(client, monkeypatch)
        patch_aliexpress(monkeypatch, not_found_handler)

        response = await client.post(IMPORT_URL, json={"externalId": "999"}, headers=headers)

        assert response.status_code == 404
        assert response.json()["code"] == "aliexpress_product_unavailable"
        listing = (await client.get(PRODUCTS_URL, headers=headers)).json()
        assert listing["meta"]["totalItems"] == 0

    async def test_ship_to_prohibited_is_not_mislabeled_as_not_found(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """482 must surface as ship-to, not a generic product-not-found."""
        headers = await connected_tenant(client, monkeypatch)
        patch_aliexpress(monkeypatch, ship_to_prohibited_handler)

        response = await client.post(
            IMPORT_URL,
            json={"externalId": "1005010486653604", "shipToCountry": "US"},
            headers=headers,
        )

        assert response.status_code == 422
        body = response.json()
        assert body["code"] == "aliexpress_ship_to_prohibited"
        assert "united states" in body["message"].lower()
        assert "shipped" in body["message"].lower()
        history = (await client.get(IMPORTS_URL, headers=headers)).json()
        failed = [r for r in history["items"] if r["status"] == "failed"]
        assert failed
        assert failed[0]["shipToCountry"] == "US"
        assert failed[0]["resultCategory"] == "aliexpress_ship_to_prohibited"
        listing = (await client.get(PRODUCTS_URL, headers=headers)).json()
        assert listing["meta"]["totalItems"] == 0

    async def test_a_failure_is_recorded_in_the_import_history(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The failures are the rows worth reading."""
        headers = await connected_tenant(client, monkeypatch)
        patch_aliexpress(monkeypatch, not_found_handler)

        await client.post(IMPORT_URL, json={"externalId": "999"}, headers=headers)

        history = (await client.get(IMPORTS_URL, headers=headers)).json()
        failed = [r for r in history["items"] if r["status"] == "failed"]
        assert failed
        assert failed[0]["errorCode"] == "aliexpress_product_unavailable"
        assert failed[0]["externalId"] == "999"

    async def test_import_without_a_connection_is_rejected(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        patch_aliexpress(monkeypatch, supplier_handler)
        body = await register(client)

        response = await client.post(
            IMPORT_URL, json={"externalId": REAL_PRODUCT_ID}, headers=auth_header(body)
        )

        # 409: the request is well formed, but the workspace is in the wrong
        # state to satisfy it. Not 404 — the endpoint exists — and not 400,
        # which would suggest the client sent something wrong.
        assert response.status_code == 409
        assert response.json()["code"] == "aliexpress_not_connected"

    async def test_a_blank_identifier_is_rejected(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers = await connected_tenant(client, monkeypatch)
        assert (
            await client.post(IMPORT_URL, json={"externalId": ""}, headers=headers)
        ).status_code == 422


class TestImportHistory:
    async def test_a_successful_import_is_recorded(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers = await connected_tenant(client, monkeypatch)
        product = (
            await client.post(IMPORT_URL, json={"externalId": REAL_PRODUCT_ID}, headers=headers)
        ).json()

        history = (await client.get(IMPORTS_URL, headers=headers)).json()
        succeeded = [r for r in history["items"] if r["status"] == "succeeded"]

        assert succeeded
        assert succeeded[0]["productId"] == product["id"]
        assert succeeded[0]["finishedAt"] is not None


class TestRetryImport:
    """DSers-parity M1 — a failed import must remain retryable without the
    merchant re-entering the product id/URL and destination from memory."""

    async def test_retrying_a_failed_import_succeeds_with_the_original_parameters(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers = await connected_tenant(client, monkeypatch)
        patch_aliexpress(monkeypatch, ship_to_prohibited_handler)
        failed = await client.post(
            IMPORT_URL,
            json={"externalId": REAL_PRODUCT_ID, "shipToCountry": "US"},
            headers=headers,
        )
        assert failed.status_code == 422

        history = (await client.get(IMPORTS_URL, headers=headers)).json()
        failed_record = next(r for r in history["items"] if r["status"] == "failed")
        assert failed_record["shipToCountry"] == "US"

        # The supplier is healthy now -- the retry should succeed using the
        # ship-to country stored on the failed attempt, not a fresh guess.
        patch_aliexpress(monkeypatch, supplier_handler)
        retried = await client.post(f"{IMPORTS_URL}/{failed_record['id']}/retry", headers=headers)

        assert retried.status_code == 200, retried.text
        product = retried.json()
        assert product["externalId"] == REAL_PRODUCT_ID

        listing = (await client.get(PRODUCTS_URL, headers=headers)).json()
        drafts = (await client.get("/api/v1/drafts", headers=headers)).json()
        assert listing["meta"]["totalItems"] == 0
        assert any(d["id"] == product["id"] for d in drafts["items"])

    async def test_retry_does_not_duplicate_an_existing_draft(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A retry for a product already successfully imported updates the
        same row -- the natural-key uniqueness on `(tenant, source,
        external_id)` that already backs every import path, unchanged here."""
        headers = await connected_tenant(client, monkeypatch)
        first = await client.post(IMPORT_URL, json={"externalId": REAL_PRODUCT_ID}, headers=headers)
        first_product_id = first.json()["id"]

        patch_aliexpress(monkeypatch, ship_to_prohibited_handler)
        failed = await client.post(
            IMPORT_URL,
            json={"externalId": REAL_PRODUCT_ID, "shipToCountry": "US"},
            headers=headers,
        )
        assert failed.status_code == 422

        history = (await client.get(IMPORTS_URL, headers=headers)).json()
        failed_record = next(
            r
            for r in history["items"]
            if r["status"] == "failed" and r["externalId"] == REAL_PRODUCT_ID
        )

        patch_aliexpress(monkeypatch, supplier_handler)
        retried = await client.post(f"{IMPORTS_URL}/{failed_record['id']}/retry", headers=headers)

        assert retried.status_code == 200, retried.text
        assert retried.json()["id"] == first_product_id

        drafts = (await client.get("/api/v1/drafts", headers=headers)).json()
        assert drafts["meta"]["totalItems"] == 1

    async def test_retrying_another_tenants_import_returns_404_not_403(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        acme = await connected_tenant(
            client, monkeypatch, companyName="Acme", email="retry-a@example.com"
        )
        patch_aliexpress(monkeypatch, ship_to_prohibited_handler)
        failed = await client.post(
            IMPORT_URL,
            json={"externalId": "1005010486653604", "shipToCountry": "US"},
            headers=acme,
        )
        assert failed.status_code == 422
        history = (await client.get(IMPORTS_URL, headers=acme)).json()
        failed_record = next(r for r in history["items"] if r["status"] == "failed")

        globex = await connected_tenant(
            client, monkeypatch, companyName="Globex", email="retry-b@example.com"
        )

        response = await client.post(f"{IMPORTS_URL}/{failed_record['id']}/retry", headers=globex)

        assert response.status_code == 404

    async def test_retrying_a_successful_import_is_rejected(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers = await connected_tenant(client, monkeypatch)
        product = (
            await client.post(IMPORT_URL, json={"externalId": REAL_PRODUCT_ID}, headers=headers)
        ).json()
        history = (await client.get(IMPORTS_URL, headers=headers)).json()
        succeeded_record = next(r for r in history["items"] if r["status"] == "succeeded")
        assert succeeded_record["productId"] == product["id"]

        response = await client.post(
            f"{IMPORTS_URL}/{succeeded_record['id']}/retry", headers=headers
        )

        assert response.status_code == 422
        assert response.json()["code"] == "validation_error"

    async def test_retrying_an_unknown_import_returns_404(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers = await connected_tenant(client, monkeypatch)

        response = await client.post(f"{IMPORTS_URL}/{uuid.uuid4()}/retry", headers=headers)

        assert response.status_code == 404


def id_echoing_handler(request: httpx.Request) -> httpx.Response:
    """Answer with a product whose id is whatever was actually requested.

    ``supplier_handler`` always returns the same captured fixture regardless
    of the id in the request — fine for most tests, useless for anything that
    needs several *distinct* products, like proving the duplicate-check
    endpoint doesn't depend on which page a client happens to have cached.
    Parses ``product_id`` out of the outbound form-encoded body and patches it
    into a copy of the real payload, so the mapped product actually gets that
    external id.
    """
    if "/auth/token" in str(request.url):
        return httpx.Response(
            200, json={"access_token": "t", "refresh_token": "r", "expires_in": 8000}
        )
    body = request.content.decode()
    if "aliexpress.ds.product.get" in body:
        requested_id = parse_qs(body).get("product_id", [REAL_PRODUCT_ID])[0]
        payload = copy.deepcopy(PRODUCT_PAYLOAD)
        payload["aliexpress_ds_product_get_response"]["result"]["ae_item_base_info_dto"][
            "product_id"
        ] = requested_id
        return httpx.Response(200, json=payload)
    return httpx.Response(200, json={"error_response": {"code": "InvalidApiPath"}})


CHECK_URL = "/api/v1/products/import/check"


class TestVariantCount:
    """M1 acceptance: the Drafts list needs an accurate variant count per row.

    `ProductRepository._variant_count_column` rides a correlated `COUNT`
    alongside the same list query — these tests are less about the count
    being *correct* in isolation (that's ordinary aggregation) and more about
    it staying correct once several products are on the same page at once,
    which is exactly where a naive per-row query would either explode (N+1)
    or silently mix counts up across rows.
    """

    async def test_the_drafts_list_reports_the_real_variant_count(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers = await connected_tenant(client, monkeypatch)
        created = (
            await client.post(IMPORT_URL, json={"externalId": REAL_PRODUCT_ID}, headers=headers)
        ).json()
        assert len(created["variants"]) == 12  # the fixture's real count

        drafts = (await client.get("/api/v1/drafts", headers=headers)).json()
        row = next(d for d in drafts["items"] if d["id"] == created["id"])

        assert row["variantCount"] == 12

    async def test_the_products_list_reports_the_real_variant_count(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession
    ) -> None:
        """Same query path (`list_by_publication`) backs both Drafts and
        Products — worth its own test rather than assuming the published
        branch behaves like the draft one just proved."""
        body = await register(
            client, companyName="Variant Count Co", email="variant-count@example.com"
        )
        tenant_id = uuid.UUID(body["identity"]["tenant"]["id"])
        headers = auth_header(body)
        patch_aliexpress(monkeypatch, supplier_handler)
        await connect_aliexpress(client, headers)
        created = (
            await client.post(IMPORT_URL, json={"externalId": REAL_PRODUCT_ID}, headers=headers)
        ).json()

        # Publish it (same no-OAuth StoreListing pattern as
        # TestDuplicateImportCheck.test_a_published_match_is_flagged_as_published)
        # so it shows up on GET /products, not GET /drafts.
        set_tenant_id(tenant_id)
        store = Store(
            tenant_id=tenant_id,
            name="Variant-count test store",
            slug=f"vc-{uuid.uuid4().hex[:12]}",
            platform=StorePlatform.SHOPIFY,
            status=StoreStatus.CONNECTED,
        )
        db_session.add(store)
        await db_session.flush()
        db_session.add(
            StoreListing(
                tenant_id=tenant_id,
                store_id=store.id,
                product_id=uuid.UUID(created["id"]),
                external_product_id=f"gid://shopify/Product/{uuid.uuid4().hex[:8]}",
                external_variant_map={},
                inventory_item_map={},
                status=ListingSyncStatus.SYNCED,
            )
        )
        await db_session.flush()

        products = (await client.get(PRODUCTS_URL, headers=headers)).json()
        row = next(p for p in products["items"] if p["id"] == created["id"])

        assert row["variantCount"] == 12

    async def test_variant_counts_stay_correct_with_several_products_on_one_page(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Guards against the count column mixing up rows once the list query
        returns more than one product — a real risk for a naively-written
        correlated subquery, not just a theoretical one."""
        headers = await connected_tenant(client, monkeypatch)
        patch_aliexpress(monkeypatch, id_echoing_handler)

        created_ids: list[str] = []
        for i in range(5):
            product = (
                await client.post(
                    IMPORT_URL, json={"externalId": f"300000000{i:04d}"}, headers=headers
                )
            ).json()
            created_ids.append(product["id"])
            # id_echoing_handler reuses the real fixture body, so every one of
            # these genuinely has 12 variants too — the assertion below is
            # real, not coincidentally always the same because nothing varies.
            assert len(product["variants"]) == 12

        drafts = (await client.get("/api/v1/drafts", params={"size": 25}, headers=headers)).json()
        rows_by_id = {d["id"]: d for d in drafts["items"]}
        for product_id in created_ids:
            assert rows_by_id[product_id]["variantCount"] == 12


class TestDuplicateImportCheck:
    """Authoritative, server-side duplicate detection (M1 acceptance).

    The frontend's own cache of the Drafts page cannot see a match outside
    whatever page happens to be loaded. This endpoint can, because it is a
    direct tenant-scoped lookup by natural key, not a scan of a list.
    """

    async def test_duplicate_within_the_first_page_is_found(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers = await connected_tenant(client, monkeypatch)
        created = (
            await client.post(IMPORT_URL, json={"externalId": REAL_PRODUCT_ID}, headers=headers)
        ).json()

        response = await client.get(
            CHECK_URL, params={"external_id": REAL_PRODUCT_ID}, headers=headers
        )

        assert response.status_code == 200
        body = response.json()
        assert body["exists"] is True
        assert body["product"]["id"] == created["id"]
        assert body["product"]["isPublished"] is False

    async def test_a_url_and_its_bare_id_resolve_to_the_same_duplicate(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The same normalisation the import endpoint itself uses."""
        headers = await connected_tenant(client, monkeypatch)
        created = (
            await client.post(IMPORT_URL, json={"externalId": REAL_PRODUCT_ID}, headers=headers)
        ).json()

        as_url = await client.get(
            CHECK_URL,
            params={"external_id": f"https://www.aliexpress.com/item/{REAL_PRODUCT_ID}.html"},
            headers=headers,
        )
        as_id = await client.get(
            CHECK_URL, params={"external_id": REAL_PRODUCT_ID}, headers=headers
        )

        assert as_url.json() == as_id.json()
        assert as_url.json()["product"]["id"] == created["id"]

    async def test_no_match_answers_exists_false_not_an_error(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers = await connected_tenant(client, monkeypatch)

        response = await client.get(
            CHECK_URL, params={"external_id": "9999999999999"}, headers=headers
        )

        assert response.status_code == 200
        assert response.json() == {"exists": False, "product": None}

    async def test_an_identifier_that_cannot_be_parsed_answers_false_not_a_422(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Called on every keystroke while typing — a not-yet-valid value is
        not a client error, it's "nothing to compare yet"."""
        headers = await connected_tenant(client, monkeypatch)

        response = await client.get(CHECK_URL, params={"external_id": "abc"}, headers=headers)

        assert response.status_code == 200
        assert response.json()["exists"] is False

    async def test_duplicate_outside_the_first_25_rows_is_still_found(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Proves the check is a direct lookup, not a scan of a cached list.

        Imports 26 distinct products for the tenant — one more than a single
        Drafts page (`size=25`) could ever hold at once, so no client-side
        cache of "the current page" could possibly contain all of them. The
        duplicate check still finds the 26th, because it is a tenant-scoped
        lookup by natural key against the database, not a scan of whatever a
        client happened to load.

        (Not asserting *which* specific row a page-1 fetch would omit: within
        this test's shared-transaction fixture every row's ``created_at`` is
        the same transaction-start instant — Postgres ``now()`` is
        transaction-scoped, not statement-scoped — so sort order among rows
        created in the same test is not meaningfully distinguishable here.
        That is a property of the test fixture, not of the real per-request
        transactions this endpoint runs under.)
        """
        headers = await connected_tenant(client, monkeypatch)
        patch_aliexpress(monkeypatch, id_echoing_handler)

        target_id = "1000000000001"
        target = (
            await client.post(IMPORT_URL, json={"externalId": target_id}, headers=headers)
        ).json()
        for i in range(25):
            await client.post(IMPORT_URL, json={"externalId": f"200000000{i:04d}"}, headers=headers)

        drafts = (await client.get("/api/v1/drafts", params={"size": 25}, headers=headers)).json()
        assert drafts["meta"]["totalItems"] == 26

        response = await client.get(CHECK_URL, params={"external_id": target_id}, headers=headers)

        assert response.status_code == 200
        assert response.json()["exists"] is True
        assert response.json()["product"]["id"] == target["id"]

    async def test_another_tenants_matching_product_is_not_exposed(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        acme = await connected_tenant(
            client, monkeypatch, companyName="Acme Dup", email="dup-a@example.com"
        )
        await client.post(IMPORT_URL, json={"externalId": REAL_PRODUCT_ID}, headers=acme)

        globex = await connected_tenant(
            client, monkeypatch, companyName="Globex Dup", email="dup-b@example.com"
        )

        response = await client.get(
            CHECK_URL, params={"external_id": REAL_PRODUCT_ID}, headers=globex
        )

        assert response.status_code == 200
        assert response.json() == {"exists": False, "product": None}

    async def test_a_published_match_is_flagged_as_published(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession
    ) -> None:
        """`isPublished` decides the frontend's link target
        (`/drafts/{id}` vs `/products/{id}`) — worth its own test.

        Attaches the `StoreListing` directly through the test session, the
        same no-OAuth pattern `test_product_workspace.py` uses — publication
        membership is entirely `StoreListing` state, so this does not need a
        real Shopify connection to be a faithful test.
        """
        body = await register(client, companyName="Dup Publish Co", email="dup-publish@example.com")
        tenant_id = uuid.UUID(body["identity"]["tenant"]["id"])
        headers = auth_header(body)
        patch_aliexpress(monkeypatch, supplier_handler)
        await connect_aliexpress(client, headers)
        created = (
            await client.post(IMPORT_URL, json={"externalId": REAL_PRODUCT_ID}, headers=headers)
        ).json()

        set_tenant_id(tenant_id)
        store = Store(
            tenant_id=tenant_id,
            name="Duplicate-check test store",
            slug=f"dup-check-{uuid.uuid4().hex[:12]}",
            platform=StorePlatform.SHOPIFY,
            status=StoreStatus.CONNECTED,
        )
        db_session.add(store)
        await db_session.flush()
        db_session.add(
            StoreListing(
                tenant_id=tenant_id,
                store_id=store.id,
                product_id=uuid.UUID(created["id"]),
                external_product_id=f"gid://shopify/Product/{uuid.uuid4().hex[:8]}",
                external_variant_map={},
                inventory_item_map={},
                status=ListingSyncStatus.SYNCED,
            )
        )
        await db_session.flush()

        response = await client.get(
            CHECK_URL, params={"external_id": REAL_PRODUCT_ID}, headers=headers
        )
        assert response.json()["product"]["isPublished"] is True

    async def test_repeated_submissions_remain_idempotent(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sequential repeats, on the shared fast fixture. Genuine concurrent
        requests need two independent database sessions racing for real —
        this fixture's session is not safe for that — so that case is
        `test_product_import_concurrency.py`, which builds its own app
        instance per request instead."""
        headers = await connected_tenant(client, monkeypatch)

        first = await client.post(IMPORT_URL, json={"externalId": REAL_PRODUCT_ID}, headers=headers)
        second = await client.post(
            IMPORT_URL, json={"externalId": REAL_PRODUCT_ID}, headers=headers
        )

        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json()["id"] == second.json()["id"]

        drafts = (await client.get("/api/v1/drafts", headers=headers)).json()
        assert drafts["meta"]["totalItems"] == 1


class TestProductDetail:
    async def test_returns_the_product_with_children(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers = await connected_tenant(client, monkeypatch)
        created = (
            await client.post(IMPORT_URL, json={"externalId": REAL_PRODUCT_ID}, headers=headers)
        ).json()

        response = await client.get(f"{PRODUCTS_URL}/{created['id']}", headers=headers)

        assert response.status_code == 200
        assert response.json()["id"] == created["id"]
        assert len(response.json()["variants"]) == 12

    async def test_the_raw_supplier_html_is_never_exposed_only_the_sanitized_form(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`description` is now exposed (Product Editor stage 1) -- but only
        ever the sanitized result (`app.core.sanitize.sanitize_html`), never
        the raw seller-authored markup AliExpress actually sent. The real
        fixture's `detail` wraps everything in
        `<div class="detailmodule_html">...`, which is exactly what must not
        survive: `div` is not in the sanitizer's allowlist.
        """
        headers = await connected_tenant(client, monkeypatch)
        created = (
            await client.post(IMPORT_URL, json={"externalId": REAL_PRODUCT_ID}, headers=headers)
        ).json()

        response = await client.get(f"{PRODUCTS_URL}/{created['id']}", headers=headers)

        assert response.json()["description"], "sanitized description should be present"
        assert "detailmodule_html" not in response.text
        assert "<div" not in response.text


class TestTenantIsolation:
    """The property that matters most.

    Two tenants importing the *same* supplier product must each get their own
    row, and neither may see or overwrite the other's.
    """

    async def test_one_tenant_cannot_see_another_tenants_products(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        acme = await connected_tenant(
            client, monkeypatch, companyName="Acme", email="a@example.com"
        )
        await client.post(IMPORT_URL, json={"externalId": REAL_PRODUCT_ID}, headers=acme)

        globex = await connected_tenant(
            client, monkeypatch, companyName="Globex", email="g@example.com"
        )
        listing = (await client.get(PRODUCTS_URL, headers=globex)).json()

        assert listing["meta"]["totalItems"] == 0

    async def test_fetching_another_tenants_product_returns_404_not_403(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """403 would confirm the row exists and enable enumeration."""
        acme = await connected_tenant(
            client, monkeypatch, companyName="Acme", email="a2@example.com"
        )
        created = (
            await client.post(IMPORT_URL, json={"externalId": REAL_PRODUCT_ID}, headers=acme)
        ).json()

        globex = await connected_tenant(
            client, monkeypatch, companyName="Globex", email="g2@example.com"
        )
        response = await client.get(f"{PRODUCTS_URL}/{created['id']}", headers=globex)

        assert response.status_code == 404

    async def test_both_tenants_may_import_the_same_supplier_product(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Uniqueness is per tenant, not global.

        A global constraint would mean the first tenant to import a product
        locked every other tenant out of it.
        """
        acme = await connected_tenant(
            client, monkeypatch, companyName="Acme", email="a3@example.com"
        )
        first = await client.post(IMPORT_URL, json={"externalId": REAL_PRODUCT_ID}, headers=acme)

        globex = await connected_tenant(
            client, monkeypatch, companyName="Globex", email="g3@example.com"
        )
        second = await client.post(IMPORT_URL, json={"externalId": REAL_PRODUCT_ID}, headers=globex)

        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json()["id"] != second.json()["id"]

    async def test_import_history_is_tenant_scoped(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        acme = await connected_tenant(
            client, monkeypatch, companyName="Acme", email="a4@example.com"
        )
        await client.post(IMPORT_URL, json={"externalId": REAL_PRODUCT_ID}, headers=acme)

        globex = await connected_tenant(
            client, monkeypatch, companyName="Globex", email="g4@example.com"
        )
        history = (await client.get(IMPORTS_URL, headers=globex)).json()

        assert history["meta"]["totalItems"] == 0


class TestFeedBrowsing:
    async def test_lists_products_without_importing_them(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers = await connected_tenant(client, monkeypatch)

        response = await client.get(
            f"{PRODUCTS_URL}/feeds/AEB_%20ComputerAccessories_EG", headers=headers
        )

        assert response.status_code == 200
        assert response.json()
        assert (await client.get(PRODUCTS_URL, headers=headers)).json()["meta"]["totalItems"] == 0


class TestSync:
    async def test_refresh_reuses_the_import_path_without_duplicating(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sync is import again — the same id, the same row, refreshed data."""
        headers = await connected_tenant(client, monkeypatch)
        created = (
            await client.post(IMPORT_URL, json={"externalId": REAL_PRODUCT_ID}, headers=headers)
        ).json()

        response = await client.post(f"{PRODUCTS_URL}/{created['id']}/sync", headers=headers)

        assert response.status_code == 200, response.text
        refreshed = response.json()
        assert refreshed["id"] == created["id"]
        assert refreshed["externalId"] == REAL_PRODUCT_ID
        assert len(refreshed["variants"]) == 12

    async def test_sync_preserves_status_rather_than_reverting_to_draft(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        patch_aliexpress(monkeypatch, supplier_handler)
        body = await register(client, email="status@example.com")
        headers = auth_header(body)
        await connect_aliexpress(client, headers)
        tenant_id = uuid.UUID(body["identity"]["tenant"]["id"])

        created = (
            await client.post(IMPORT_URL, json={"externalId": REAL_PRODUCT_ID}, headers=headers)
        ).json()

        from app.core.context import set_tenant_id
        from app.models.product import ProductStatus
        from app.repositories.product import ProductRepository

        set_tenant_id(tenant_id)
        product = await ProductRepository(db_session).get_by_id_or_raise(uuid.UUID(created["id"]))
        product.status = ProductStatus.ACTIVE
        await db_session.flush()

        response = await client.post(f"{PRODUCTS_URL}/{created['id']}/sync", headers=headers)

        assert response.status_code == 200
        assert response.json()["status"] == "active"

    async def test_syncing_another_tenants_product_returns_404_not_403(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        acme = await connected_tenant(
            client, monkeypatch, companyName="Acme", email="sync-a@example.com"
        )
        created = (
            await client.post(IMPORT_URL, json={"externalId": REAL_PRODUCT_ID}, headers=acme)
        ).json()

        globex = await connected_tenant(
            client, monkeypatch, companyName="Globex", email="sync-g@example.com"
        )
        response = await client.post(f"{PRODUCTS_URL}/{created['id']}/sync", headers=globex)

        assert response.status_code == 404
