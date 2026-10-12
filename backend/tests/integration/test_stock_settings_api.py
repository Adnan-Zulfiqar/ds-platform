"""The workspace's listed-stock setting, through the API, and what a channel
is then sent.

Real Postgres (migrations). WooCommerce stands in for every channel: it
shares ``offer_terms`` with eBay, and Shopify reads the same rule.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.woocommerce import client as woo
from tests.integration.test_ebay_c1_api import auth_header, register, token_with_roles
from tests.integration.test_woocommerce_publish import PUBLISH, FakeWoo, connected, draft

pytestmark = pytest.mark.integration

STOCK = "/api/v1/global-rules/stock"


@pytest.fixture
def woo_shop(monkeypatch: pytest.MonkeyPatch) -> FakeWoo:
    fake = FakeWoo()
    monkeypatch.setattr(woo, "_resolver", lambda _host: ["93.184.216.34"])
    monkeypatch.setattr(woo, "_transport", httpx.MockTransport(fake.handle))
    return fake


async def _set(client: AsyncClient, owner: dict[str, Any], quantity: int | None) -> Any:
    return await client.put(STOCK, json={"publishQuantity": quantity}, headers=auth_header(owner))


async def test_the_setting_starts_empty_saves_and_clears(client: AsyncClient) -> None:
    owner = await register(client)
    first = await client.get(STOCK, headers=auth_header(owner))
    assert first.status_code == 200 and first.json() == {"publishQuantity": None}

    saved = await _set(client, owner, 14)
    assert saved.status_code == 200 and saved.json() == {"publishQuantity": 14}
    assert (await client.get(STOCK, headers=auth_header(owner))).json()["publishQuantity"] == 14

    cleared = await _set(client, owner, None)
    assert cleared.json() == {"publishQuantity": None}


@pytest.mark.parametrize("quantity", [0, -1, 10001])
async def test_an_impossible_quantity_is_refused(client: AsyncClient, quantity: int) -> None:
    owner = await register(client)
    response = await _set(client, owner, quantity)
    assert response.status_code == 422


async def test_another_workspace_never_sees_the_setting(client: AsyncClient) -> None:
    owner = await register(client)
    other = await register(client)
    await _set(client, owner, 14)
    response = await client.get(STOCK, headers=auth_header(other))
    assert response.json() == {"publishQuantity": None}


async def test_a_member_may_read_but_not_change_it(client: AsyncClient) -> None:
    owner = await register(client)
    member = token_with_roles(owner, "member")
    assert (await client.get(STOCK, headers=member)).status_code == 200
    response = await client.put(STOCK, json={"publishQuantity": 14}, headers=member)
    assert response.status_code == 403


async def test_publishing_lists_the_set_quantity_instead_of_the_suppliers(
    client: AsyncClient, db_session: AsyncSession, woo_shop: FakeWoo
) -> None:
    owner, store_id = await connected(client)
    product_id = await draft(db_session, owner, stock=9000)
    await _set(client, owner, 14)

    response = await client.post(
        PUBLISH,
        json={"productId": str(product_id), "storeId": store_id},
        headers=auth_header(owner),
    )
    assert response.status_code == 200, response.text
    [remote] = woo_shop.products.values()
    assert remote["stock_quantity"] == 14
