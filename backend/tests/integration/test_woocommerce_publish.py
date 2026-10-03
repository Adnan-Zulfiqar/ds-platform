"""Track E7 W2 — publishing a draft to WooCommerce, through the API.

Real Postgres (migrations). The WooCommerce store is a small in-memory fake
behind ``httpx.MockTransport`` that keeps products by id and answers SKU
searches, so retries and adoption behave as they would on a real site.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import set_tenant_id
from app.integrations.woocommerce import client as woo
from app.models.product import Product, ProductImage, ProductSource, ProductStatus
from app.models.shopify import StoreListing
from app.models.store import Store
from tests.integration.test_ebay_c1_api import auth_header, register, token_with_roles

pytestmark = pytest.mark.integration

CONNECT = "/api/v1/integrations/woocommerce/connect"
READINESS = "/api/v1/integrations/woocommerce/publish-readiness"
PUBLISH = "/api/v1/integrations/woocommerce/publish"


class FakeWoo:
    def __init__(self) -> None:
        self.products: dict[int, dict[str, Any]] = {}
        self.calls: list[tuple[str, str]] = []
        self.next_id = 100
        self.reject_writes: str | None = None

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/wp-json/wc/v3")
        self.calls.append((request.method, path))
        if path == "/settings/general":
            return httpx.Response(200, json=[{"id": "woocommerce_currency", "value": "GBP"}])
        if path == "/products" and request.method == "GET":
            sku = request.url.params.get("sku")
            return httpx.Response(200, json=[p for p in self.products.values() if p["sku"] == sku])
        if request.method in ("POST", "PUT") and self.reject_writes:
            return httpx.Response(
                400, json={"code": "woocommerce_rest_invalid", "message": self.reject_writes}
            )
        if path == "/products" and request.method == "POST":
            body = json.loads(request.content)
            product = {
                **body,
                "id": self.next_id,
                "permalink": f"https://shop.example.com/p/{self.next_id}",
            }
            self.products[self.next_id] = product
            self.next_id += 1
            return httpx.Response(201, json=product)
        if path.startswith("/products/"):
            pid = int(path.rsplit("/", 1)[1])
            if pid not in self.products:
                return httpx.Response(
                    404,
                    json={"code": "woocommerce_rest_product_invalid_id", "message": "Invalid ID."},
                )
            if request.method == "PUT":
                self.products[pid].update(json.loads(request.content))
            return httpx.Response(200, json=self.products[pid])
        return httpx.Response(404, text="<html>no route</html>")


@pytest.fixture
def shop(monkeypatch: pytest.MonkeyPatch) -> FakeWoo:
    fake = FakeWoo()
    monkeypatch.setattr(woo, "_resolver", lambda _host: ["93.184.216.34"])
    monkeypatch.setattr(woo, "_transport", httpx.MockTransport(fake.handle))
    return fake


async def connected(client: AsyncClient) -> tuple[dict[str, Any], str]:
    owner = await register(client)
    response = await client.post(
        CONNECT,
        json={
            "name": "Corner Shop",
            "siteUrl": "https://shop.example.com",
            "consumerKey": "ck_" + "1" * 40,
            "consumerSecret": "cs_" + "2" * 40,
        },
        headers=auth_header(owner),
    )
    assert response.status_code == 201, response.text
    return owner, str(response.json()["id"])


async def draft(
    db_session: AsyncSession, owner: dict[str, Any], *, currency: str = "GBP", stock: int = 7
) -> uuid.UUID:
    tenant_id = uuid.UUID(str(owner["identity"]["tenant"]["id"]))
    set_tenant_id(tenant_id)
    product = Product(
        tenant_id=tenant_id,
        source=ProductSource.MANUAL,
        external_id=f"w2-{uuid.uuid4().hex[:10]}",
        title="Red ceramic mug, 350 ml",
        description="<p>A sturdy mug.</p>",
        status=ProductStatus.DRAFT,
        sell_price=Decimal("12.50"),
        currency=currency,
        stock_quantity=stock,
    )
    db_session.add(product)
    await db_session.flush()
    db_session.add(
        ProductImage(
            tenant_id=tenant_id,
            product_id=product.id,
            url="https://cdn.example/mug.jpg",
            position=0,
        )
    )
    await db_session.flush()
    return product.id


async def test_publish_creates_a_simple_product_and_records_the_listing(
    client: AsyncClient, db_session: AsyncSession, shop: FakeWoo
) -> None:
    owner, store_id = await connected(client)
    product_id = await draft(db_session, owner)
    body = {"productId": str(product_id), "storeId": store_id}

    ready = await client.post(READINESS, json=body, headers=auth_header(owner))
    assert ready.json()["canPublish"] is True, ready.text

    response = await client.post(PUBLISH, json=body, headers=auth_header(owner))
    assert response.status_code == 200, response.text
    assert response.json()["updated"] is False
    [remote] = shop.products.values()
    assert remote["type"] == "simple"
    assert remote["sku"] == f"dp-{product_id}"
    assert remote["regular_price"] == "12.50"
    assert remote["stock_quantity"] == 7 and remote["manage_stock"] is True
    assert remote["images"] == [{"src": "https://cdn.example/mug.jpg"}]

    listing = await db_session.scalar(
        sa.select(StoreListing).where(StoreListing.product_id == product_id)
    )
    assert listing is not None and listing.external_product_id == str(remote["id"])


async def test_a_second_publish_updates_and_never_resends_images(
    client: AsyncClient, db_session: AsyncSession, shop: FakeWoo
) -> None:
    owner, store_id = await connected(client)
    product_id = await draft(db_session, owner)
    body = {"productId": str(product_id), "storeId": store_id}
    await client.post(PUBLISH, json=body, headers=auth_header(owner))
    shop.calls.clear()
    again = await client.post(PUBLISH, json=body, headers=auth_header(owner))
    assert again.json()["updated"] is True
    assert len(shop.products) == 1
    writes = [c for c in shop.calls if c[0] in ("POST", "PUT")]
    assert writes == [("PUT", f"/products/{next(iter(shop.products))}")]


async def test_a_lost_response_is_adopted_by_sku_not_duplicated(
    client: AsyncClient, db_session: AsyncSession, shop: FakeWoo
) -> None:
    owner, store_id = await connected(client)
    product_id = await draft(db_session, owner)
    # The store already has the product (a first attempt whose answer was lost).
    shop.products[55] = {"id": 55, "sku": f"dp-{product_id}", "status": "publish"}
    response = await client.post(
        PUBLISH,
        json={"productId": str(product_id), "storeId": store_id},
        headers=auth_header(owner),
    )
    assert response.json()["externalProductId"] == "55"
    assert list(shop.products) == [55]


async def test_a_product_deleted_in_wordpress_is_recreated(
    client: AsyncClient, db_session: AsyncSession, shop: FakeWoo
) -> None:
    owner, store_id = await connected(client)
    product_id = await draft(db_session, owner)
    body = {"productId": str(product_id), "storeId": store_id}
    await client.post(PUBLISH, json=body, headers=auth_header(owner))
    shop.products.clear()
    again = await client.post(PUBLISH, json=body, headers=auth_header(owner))
    assert again.status_code == 200, again.text
    assert len(shop.products) == 1


async def test_readiness_blocks_a_wrong_currency_and_no_stock_before_any_write(
    client: AsyncClient, db_session: AsyncSession, shop: FakeWoo
) -> None:
    owner, store_id = await connected(client)
    product_id = await draft(db_session, owner, currency="USD", stock=0)
    body = {"productId": str(product_id), "storeId": store_id}
    ready = await client.post(READINESS, json=body, headers=auth_header(owner))
    codes = {b["code"] for b in ready.json()["blockers"]}
    assert {"selling_currency_mismatch", "quantity_missing"} <= codes
    refused = await client.post(PUBLISH, json=body, headers=auth_header(owner))
    assert refused.status_code in (409, 422)
    assert shop.products == {}


async def test_the_stores_refusal_reaches_the_merchant(
    client: AsyncClient, db_session: AsyncSession, shop: FakeWoo
) -> None:
    owner, store_id = await connected(client)
    product_id = await draft(db_session, owner)
    shop.reject_writes = "Invalid or duplicated SKU."
    response = await client.post(
        PUBLISH,
        json={"productId": str(product_id), "storeId": store_id},
        headers=auth_header(owner),
    )
    assert response.status_code == 422
    assert response.json()["code"] == "woocommerce_rejected"
    assert "duplicated SKU" in response.json()["message"]


async def test_an_unverified_or_disconnected_store_cannot_publish(
    client: AsyncClient, db_session: AsyncSession, shop: FakeWoo
) -> None:
    owner, store_id = await connected(client)
    product_id = await draft(db_session, owner)
    await client.post(
        f"/api/v1/integrations/woocommerce/stores/{store_id}/disconnect", headers=auth_header(owner)
    )
    body = {"productId": str(product_id), "storeId": store_id}
    ready = await client.post(READINESS, json=body, headers=auth_header(owner))
    assert "store_disconnected" in {b["code"] for b in ready.json()["blockers"]}

    # Keys recorded through the generic store endpoint were never verified.
    await db_session.execute(
        sa.update(Store)
        .where(Store.id == uuid.UUID(store_id))
        .values(status="connected", encrypted_credentials="x", currency_last_synced_at=None)
    )
    ready = await client.post(READINESS, json=body, headers=auth_header(owner))
    assert "store_disconnected" in {b["code"] for b in ready.json()["blockers"]}


async def test_a_member_may_not_publish(
    client: AsyncClient, db_session: AsyncSession, shop: FakeWoo
) -> None:
    owner, store_id = await connected(client)
    product_id = await draft(db_session, owner)
    response = await client.post(
        PUBLISH,
        json={"productId": str(product_id), "storeId": store_id},
        headers=token_with_roles(owner, "member"),
    )
    assert response.status_code == 403
    assert shop.products == {}
