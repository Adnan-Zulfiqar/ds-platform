"""Track E7 W1 — connecting a WooCommerce store, through the API.

Real Postgres (migrations); the store's REST API is an ``httpx.MockTransport``
and DNS a fake resolver, so nothing leaves the machine.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import set_tenant_id
from app.integrations.woocommerce import client as woo
from app.models.store import Store
from app.services.data_subject_erasure import WorkspaceClosureService
from tests.integration.test_ebay_c1_api import auth_header, register, token_with_roles

pytestmark = pytest.mark.integration

CONNECT = "/api/v1/integrations/woocommerce/connect"
STORES = "/api/v1/integrations/woocommerce/stores"
KEY = "ck_" + "1" * 40
SECRET = "cs_" + "2" * 40


@pytest.fixture
def woo_site(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"currency": "GBP", "status": 200, "calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        if state["status"] != 200:
            return httpx.Response(state["status"], json={"code": "woocommerce_rest_cannot_view"})
        return httpx.Response(
            200,
            json=[
                {"id": "woocommerce_store_address", "value": "1 High St"},
                {"id": "woocommerce_currency", "value": state["currency"]},
            ],
        )

    monkeypatch.setattr(woo, "_resolver", lambda _host: ["93.184.216.34"])
    monkeypatch.setattr(woo, "_transport", httpx.MockTransport(handler))
    return state


def payload(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "Corner Shop",
        "siteUrl": "https://shop.example.com/",
        "consumerKey": KEY,
        "consumerSecret": SECRET,
    }
    body.update(overrides)
    return body


async def test_an_admin_connects_a_store_with_verified_keys_and_currency(
    client: AsyncClient, db_session: AsyncSession, woo_site: dict[str, Any]
) -> None:
    owner = await register(client)
    response = await client.post(CONNECT, json=payload(), headers=auth_header(owner))
    assert response.status_code == 201, response.text
    store = response.json()
    assert store["platform"] == "woocommerce"
    assert store["status"] == "connected"
    assert store["currency"] == "GBP"
    assert store["currencyLastSyncedAt"] is not None
    assert store["storefrontUrl"] == "https://shop.example.com"
    assert KEY not in response.text and SECRET not in response.text

    stored = await db_session.scalar(
        sa.select(Store.encrypted_credentials).where(Store.id == uuid.UUID(store["id"]))
    )
    assert stored and KEY not in stored and SECRET not in stored

    listed = await client.get(STORES, headers=auth_header(owner))
    assert [s["id"] for s in listed.json()] == [store["id"]]

    again = await client.post(CONNECT, json=payload(name="Renamed"), headers=auth_header(owner))
    assert again.json()["id"] == store["id"]  # same site, same store


async def test_rejected_keys_save_nothing(client: AsyncClient, woo_site: dict[str, Any]) -> None:
    owner = await register(client)
    woo_site["status"] = 401
    response = await client.post(CONNECT, json=payload(), headers=auth_header(owner))
    assert response.status_code == 422
    assert response.json()["code"] == "woocommerce_auth_failed"
    assert (await client.get(STORES, headers=auth_header(owner))).json() == []


async def test_malformed_keys_and_private_sites_are_refused_before_any_call(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, woo_site: dict[str, Any]
) -> None:
    owner = await register(client)
    bad_key = await client.post(
        CONNECT, json=payload(consumerKey="not-a-key"), headers=auth_header(owner)
    )
    assert bad_key.status_code == 422
    plain_http = await client.post(
        CONNECT, json=payload(siteUrl="http://shop.example.com"), headers=auth_header(owner)
    )
    assert plain_http.json()["code"] == "woocommerce_url_rejected"
    monkeypatch.setattr(woo, "_resolver", lambda _host: ["10.0.0.7"])
    internal = await client.post(CONNECT, json=payload(), headers=auth_header(owner))
    assert internal.json()["code"] == "woocommerce_url_rejected"
    assert woo_site["calls"] == 0


async def test_only_admins_connect_and_disconnect(
    client: AsyncClient, woo_site: dict[str, Any]
) -> None:
    owner = await register(client)
    member = token_with_roles(owner, "member")
    assert (await client.post(CONNECT, json=payload(), headers=member)).status_code == 403
    store = (await client.post(CONNECT, json=payload(), headers=auth_header(owner))).json()
    url = f"{STORES}/{store['id']}/disconnect"
    assert (await client.post(url, headers=member)).status_code == 403
    assert (await client.get(STORES, headers=member)).status_code == 200


async def test_disconnect_forgets_the_keys_but_keeps_the_store(
    client: AsyncClient, db_session: AsyncSession, woo_site: dict[str, Any]
) -> None:
    owner = await register(client)
    store = (await client.post(CONNECT, json=payload(), headers=auth_header(owner))).json()
    response = await client.post(f"{STORES}/{store['id']}/disconnect", headers=auth_header(owner))
    assert response.json()["status"] == "disconnected"
    stored = await db_session.scalar(
        sa.select(Store.encrypted_credentials).where(Store.id == uuid.UUID(store["id"]))
    )
    assert stored is None


async def test_another_workspace_cannot_disconnect_or_see_the_store(
    client: AsyncClient, woo_site: dict[str, Any]
) -> None:
    first = await register(client)
    second = await register(client)
    store = (await client.post(CONNECT, json=payload(), headers=auth_header(first))).json()
    response = await client.post(f"{STORES}/{store['id']}/disconnect", headers=auth_header(second))
    assert response.status_code == 404
    assert (await client.get(STORES, headers=auth_header(second))).json() == []


async def test_closing_the_workspace_clears_store_credentials(
    client: AsyncClient, db_session: AsyncSession, woo_site: dict[str, Any]
) -> None:
    owner = await register(client)
    store = (await client.post(CONNECT, json=payload(), headers=auth_header(owner))).json()
    tenant = uuid.UUID(owner["identity"]["tenant"]["id"])
    set_tenant_id(tenant)
    plan = await WorkspaceClosureService(db_session).plan(tenant)
    assert plan.counts["store_credentials"] == 1
    outcome = await WorkspaceClosureService(db_session).erase(tenant)
    assert outcome.counts["store_credentials_cleared"] == 1
    stored = await db_session.scalar(
        sa.select(Store.encrypted_credentials).where(Store.id == uuid.UUID(store["id"]))
    )
    assert stored is None
