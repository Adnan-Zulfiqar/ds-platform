"""EBAY-C6 — the operator health view, over HTTP, eBay faked."""

from __future__ import annotations

import httpx
import pytest
from httpx import AsyncClient

from app.integrations.ebay.tokens import forget_application_token
from tests.integration.ebay_c1_live import ACCESS_TOKEN, SELLER_USER_ID, install
from tests.integration.test_ebay_c1_api import (
    auth_header,
    connect_fully,
    register,
    token_with_roles,
)
from tests.integration.test_ebay_c2_listing_setup import (
    fresh_redis as fresh_redis,  # autouse: a Redis client per test event loop
)
from tests.integration.test_ebay_c3_product_details import TaxonomyFakeEbay

pytestmark = pytest.mark.integration

HEALTH_URL = "/api/v1/integrations/ebay/health"


class HealthFakeEbay(TaxonomyFakeEbay):
    def __init__(self) -> None:
        super().__init__()
        self.analytics_status = 200

    async def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/developer/analytics/v1_beta/rate_limit/":
            if self.analytics_status != 200:
                return httpx.Response(self.analytics_status)
            return httpx.Response(
                200,
                json={
                    "rateLimits": [
                        {
                            "apiName": "inventory",
                            "resources": [
                                {
                                    "name": "sell.inventory",
                                    "rates": [
                                        {
                                            "limit": 2000000,
                                            "remaining": 1500000,
                                            "reset": "2026-10-04T00:00:00Z",
                                        }
                                    ],
                                }
                            ],
                        }
                    ]
                },
            )
        return await super().handler(request)


@pytest.fixture
def ebay(monkeypatch: pytest.MonkeyPatch) -> HealthFakeEbay:
    forget_application_token()
    fake = HealthFakeEbay()
    install(monkeypatch, fake)
    return fake


async def test_health_reports_connection_and_call_limits_without_secrets(
    client: AsyncClient, ebay: HealthFakeEbay
) -> None:
    body = await register(client)
    headers = auth_header(body)
    await connect_fully(client, headers)

    response = await client.get(HEALTH_URL, headers=headers)

    assert response.status_code == 200, response.text
    data = response.json()
    assert (data["configured"], data["connected"], data["environment"]) == (
        True,
        True,
        "production",
    )
    assert data["callLimits"] == [
        {
            "api": "inventory",
            "resource": "sell.inventory",
            "limit": 2000000,
            "remaining": 1500000,
            "reset": "2026-10-04T00:00:00Z",
        }
    ]
    for secret in (ACCESS_TOKEN, SELLER_USER_ID, "test-only-cert-id"):
        assert secret not in response.text


async def test_unreadable_limits_are_null_not_a_failure(
    client: AsyncClient, ebay: HealthFakeEbay
) -> None:
    ebay.analytics_status = 500
    headers = auth_header(await register(client))
    response = await client.get(HEALTH_URL, headers=headers)
    assert response.status_code == 200
    assert response.json()["callLimits"] is None
    assert response.json()["connected"] is False


async def test_health_is_for_admins(client: AsyncClient, ebay: HealthFakeEbay) -> None:
    body = await register(client)
    response = await client.get(HEALTH_URL, headers=token_with_roles(body, "member"))
    assert response.status_code == 403
