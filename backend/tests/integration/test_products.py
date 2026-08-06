"""Integration tests for the product catalogue endpoints.

Drives the real HTTP pipeline — auth, dependencies, repository, database — with
only the AliExpress network boundary replaced. The payload the mock returns is
the **real captured response** committed under ``tests/fixtures/aliexpress/``,
so what these tests assert about parsing is what the supplier actually sends.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from fakeredis import aioredis as fake_aioredis
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.aliexpress import client as client_module
from app.integrations.aliexpress import service as service_module
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
