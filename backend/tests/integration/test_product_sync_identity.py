"""Integration tests proving variant/image identity survives a re-sync.

This is the M20 fix (`docs/TECHNICAL_DEBT.md`): `ProductImportService`
used to delete every variant/image row and reinsert fresh ones on every
sync. It now reconciles in place, matched by `external_variant_id`/`url` —
these tests are the actual proof, not just "the same data comes back,"
which the pre-fix delete-and-recreate behaviour would also have produced.
"""

from __future__ import annotations

import copy
from typing import Any

import httpx
import pytest
from fakeredis import aioredis as fake_aioredis
from httpx import AsyncClient

from app.integrations.aliexpress import service as service_module
from tests.integration.test_products import (
    PRODUCT_PAYLOAD,
    REAL_PRODUCT_ID,
    connected_tenant,
    patch_aliexpress,
)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> fake_aioredis.FakeRedis:
    redis = fake_aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(service_module, "get_redis", lambda _purpose: redis)
    return redis


@pytest.fixture(autouse=True)
def _allow_outbound(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.integrations.rate_limiter import RateLimitDecision

    async def _allow(self: Any, tenant_id: str) -> RateLimitDecision:
        return RateLimitDecision(allowed=True, remaining=99, retry_after_seconds=0)

    monkeypatch.setattr("app.integrations.rate_limiter.OutboundRateLimiter.acquire", _allow)


def _handler_for(payload: dict[str, Any]) -> Any:
    """Build a supplier handler serving `payload` for the product-detail call."""

    def handler(request: httpx.Request) -> httpx.Response:
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
            return httpx.Response(200, json=payload)
        return httpx.Response(200, json={"error_response": {"code": "InvalidApiPath"}})

    return handler


def _skus(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return payload["aliexpress_ds_product_get_response"]["result"]["ae_item_sku_info_dtos"][
        "ae_item_sku_info_d_t_o"
    ]


def _image_urls(payload: dict[str, Any]) -> list[str]:
    return (
        payload["aliexpress_ds_product_get_response"]["result"]["ae_multimedia_info_dto"][
            "image_urls"
        ]
    ).split(";")


def _set_image_urls(payload: dict[str, Any], urls: list[str]) -> None:
    payload["aliexpress_ds_product_get_response"]["result"]["ae_multimedia_info_dto"][
        "image_urls"
    ] = ";".join(urls)


async def _fetch_detail(client: AsyncClient, headers: dict[str, str], product_id: str) -> Any:
    response = await client.get(f"/api/v1/products/{product_id}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


class TestVariantIdentitySurvivesResync:
    async def test_unchanged_variants_keep_their_ids_across_a_resync(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        patch_aliexpress(monkeypatch, _handler_for(PRODUCT_PAYLOAD))
        headers = await connected_tenant(client, monkeypatch)
        first = (
            await client.post(
                "/api/v1/products/import", json={"externalId": REAL_PRODUCT_ID}, headers=headers
            )
        ).json()
        ids_before = {v["externalVariantId"]: v["id"] for v in first["variants"]}
        assert len(ids_before) == 12

        # A plain re-sync with the identical payload -- nothing added or
        # removed on the supplier side.
        resynced = await client.post(f"/api/v1/products/{first['id']}/sync", headers=headers)
        assert resynced.status_code == 200, resynced.text
        ids_after = {v["externalVariantId"]: v["id"] for v in resynced.json()["variants"]}

        assert ids_after == ids_before, "every variant row should have kept its own id"

    async def test_a_variant_the_supplier_removes_is_deleted_and_the_rest_keep_their_ids(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        patch_aliexpress(monkeypatch, _handler_for(PRODUCT_PAYLOAD))
        headers = await connected_tenant(client, monkeypatch)
        first = (
            await client.post(
                "/api/v1/products/import", json={"externalId": REAL_PRODUCT_ID}, headers=headers
            )
        ).json()
        ids_before = {v["externalVariantId"]: v["id"] for v in first["variants"]}
        removed_sku_id = first["variants"][0]["externalVariantId"]

        reduced_payload = copy.deepcopy(PRODUCT_PAYLOAD)
        skus = _skus(reduced_payload)
        skus[:] = [s for s in skus if s["sku_id"] != removed_sku_id]
        patch_aliexpress(monkeypatch, _handler_for(reduced_payload))

        resynced = await client.post(f"/api/v1/products/{first['id']}/sync", headers=headers)
        assert resynced.status_code == 200, resynced.text
        body = resynced.json()

        assert len(body["variants"]) == 11
        remaining_ids = {v["externalVariantId"]: v["id"] for v in body["variants"]}
        assert removed_sku_id not in remaining_ids
        for external_id, row_id in remaining_ids.items():
            assert row_id == ids_before[external_id], "a surviving variant must keep its own id"

    async def test_a_variant_the_supplier_adds_gets_a_new_row_without_disturbing_the_rest(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        patch_aliexpress(monkeypatch, _handler_for(PRODUCT_PAYLOAD))
        headers = await connected_tenant(client, monkeypatch)
        first = (
            await client.post(
                "/api/v1/products/import", json={"externalId": REAL_PRODUCT_ID}, headers=headers
            )
        ).json()
        ids_before = {v["externalVariantId"]: v["id"] for v in first["variants"]}

        expanded_payload = copy.deepcopy(PRODUCT_PAYLOAD)
        skus = _skus(expanded_payload)
        new_sku = copy.deepcopy(skus[0])
        new_sku["sku_id"] = "brand-new-sku-id-999"
        skus.append(new_sku)
        patch_aliexpress(monkeypatch, _handler_for(expanded_payload))

        resynced = await client.post(f"/api/v1/products/{first['id']}/sync", headers=headers)
        assert resynced.status_code == 200, resynced.text
        body = resynced.json()

        assert len(body["variants"]) == 13
        after = {v["externalVariantId"]: v["id"] for v in body["variants"]}
        for external_id, row_id in ids_before.items():
            assert after[external_id] == row_id, "pre-existing variants must not be touched"
        assert "brand-new-sku-id-999" in after

    async def test_a_price_change_updates_the_existing_row_rather_than_replacing_it(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        patch_aliexpress(monkeypatch, _handler_for(PRODUCT_PAYLOAD))
        headers = await connected_tenant(client, monkeypatch)
        first = (
            await client.post(
                "/api/v1/products/import", json={"externalId": REAL_PRODUCT_ID}, headers=headers
            )
        ).json()
        target_id = first["variants"][0]["id"]
        target_external_id = first["variants"][0]["externalVariantId"]

        repriced_payload = copy.deepcopy(PRODUCT_PAYLOAD)
        skus = _skus(repriced_payload)
        for sku in skus:
            if sku["sku_id"] == target_external_id:
                sku["offer_sale_price"] = "999.99"
                sku["sku_price"] = "999.99"
        patch_aliexpress(monkeypatch, _handler_for(repriced_payload))

        resynced = await client.post(f"/api/v1/products/{first['id']}/sync", headers=headers)
        assert resynced.status_code == 200, resynced.text
        updated = next(v for v in resynced.json()["variants"] if v["id"] == target_id)

        assert updated["costPrice"] == "999.9900" or updated["costPrice"] == "999.99"


class TestImageIdentitySurvivesResync:
    async def test_unchanged_images_are_stable_across_a_resync(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        patch_aliexpress(monkeypatch, _handler_for(PRODUCT_PAYLOAD))
        headers = await connected_tenant(client, monkeypatch)
        first = (
            await client.post(
                "/api/v1/products/import", json={"externalId": REAL_PRODUCT_ID}, headers=headers
            )
        ).json()
        urls_before = [img["url"] for img in first["images"]]
        assert len(urls_before) == 6

        resynced = await client.post(f"/api/v1/products/{first['id']}/sync", headers=headers)
        urls_after = [img["url"] for img in resynced.json()["images"]]

        assert urls_after == urls_before

    async def test_an_image_the_supplier_removes_disappears_and_the_rest_survive(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        patch_aliexpress(monkeypatch, _handler_for(PRODUCT_PAYLOAD))
        headers = await connected_tenant(client, monkeypatch)
        first = (
            await client.post(
                "/api/v1/products/import", json={"externalId": REAL_PRODUCT_ID}, headers=headers
            )
        ).json()
        removed_url = first["images"][0]["url"]

        reduced_payload = copy.deepcopy(PRODUCT_PAYLOAD)
        remaining = [u for u in _image_urls(PRODUCT_PAYLOAD) if u != removed_url]
        _set_image_urls(reduced_payload, remaining)
        patch_aliexpress(monkeypatch, _handler_for(reduced_payload))

        resynced = await client.post(f"/api/v1/products/{first['id']}/sync", headers=headers)
        assert resynced.status_code == 200, resynced.text
        remaining_urls = [img["url"] for img in resynced.json()["images"]]

        assert len(remaining_urls) == 5
        assert removed_url not in remaining_urls
