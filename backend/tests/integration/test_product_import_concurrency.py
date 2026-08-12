"""Genuinely concurrent import requests must not create a second product.

Like ``test_product_import_transaction_durability.py``, this does not use the
``client``/``db_session`` fixtures: they share one SQLAlchemy session across
the whole test, and a single ``AsyncSession`` is not safe for two coroutines
to drive at once (``asyncio.gather`` against it raises
``IllegalStateChangeError``/``InvalidRequestError`` — a fixture problem, not
evidence about the real endpoint). This builds the real app and gives each
concurrent request its own connection via the real, non-overridden
``get_db_session``, which is what two simultaneous browser tabs actually do.

The AliExpress network boundary is still mocked, the same way the rest of the
product-import suite does it (``patch_aliexpress`` / ``supplier_handler`` from
``test_products.py``) — ``monkeypatch`` is a plain function-scoped fixture,
independent of which database-session wiring is in play. This is not a live
AliExpress verification; it verifies the platform's own concurrency
guarantee, with the supplier response held constant.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fakeredis import aioredis as fake_aioredis
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings
from app.integrations.aliexpress import service as service_module
from app.main import create_application
from tests.integration.conftest import registration_payload
from tests.integration.test_products import (
    REAL_PRODUCT_ID,
    patch_aliexpress,
    supplier_handler,
)

pytestmark = pytest.mark.integration

IMPORT_URL = "/api/v1/products/import"
DRAFTS_URL = "/api/v1/drafts"
CONNECT_URL = "/api/v1/integrations/aliexpress/connect"
CALLBACK_URL = "/api/v1/integrations/aliexpress/callback"


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> fake_aioredis.FakeRedis:
    """In-process Redis for the OAuth state store — same as `test_products.py`."""
    redis = fake_aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(service_module, "get_redis", lambda _purpose: redis)
    return redis


@pytest.fixture(autouse=True)
def _allow_outbound(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bypass the outbound rate limiter — same as `test_products.py`."""
    from app.integrations.rate_limiter import RateLimitDecision

    async def _allow(self: object, tenant_id: str) -> RateLimitDecision:
        return RateLimitDecision(allowed=True, remaining=99, retry_after_seconds=0)

    monkeypatch.setattr("app.integrations.rate_limiter.OutboundRateLimiter.acquire", _allow)


async def test_two_concurrent_imports_of_the_same_product_create_one_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_aliexpress(monkeypatch, supplier_handler)
    app = create_application()
    tenant_id: uuid.UUID | None = None

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            registered = await client.post("/api/v1/auth/register", json=registration_payload())
            assert registered.status_code == 201, registered.text
            body = registered.json()
            tenant_id = uuid.UUID(body["identity"]["tenant"]["id"])
            headers = {"Authorization": f"Bearer {body['tokens']['accessToken']}"}

            connect = await client.post(CONNECT_URL, json={}, headers=headers)
            assert connect.status_code == 201, connect.text
            state = connect.json()["state"]
            callback = await client.get(
                CALLBACK_URL,
                params={"code": "auth-code", "state": state},
                follow_redirects=False,
            )
            assert callback.status_code == 303
            assert "aliexpress=connected" in callback.headers["location"]

            # Two genuinely concurrent requests, each getting its own
            # connection from the real (non-overridden) get_db_session —
            # what two simultaneous browser tabs actually produce.
            results = await asyncio.gather(
                client.post(IMPORT_URL, json={"externalId": REAL_PRODUCT_ID}, headers=headers),
                client.post(IMPORT_URL, json={"externalId": REAL_PRODUCT_ID}, headers=headers),
            )

            statuses = sorted(r.status_code for r in results)
            # One creates (201); the other lands on whichever safety net
            # actually won the race — 201 (same row, updated), 422
            # ("already in progress", `find_in_progress`), or 409 (the DB
            # unique constraint itself, via `_translate_integrity_error`,
            # when both requests get past `find_in_progress` before either
            # has committed). All three are correct outcomes of a real race.
            # What must never happen is two distinct products — the actual
            # assertion is below.
            assert all(s in (201, 422, 409) for s in statuses), statuses

            drafts = await client.get(DRAFTS_URL, headers=headers)
            assert drafts.json()["meta"]["totalItems"] == 1
    finally:
        if tenant_id is not None:
            engine = create_async_engine(settings.database.async_dsn)
            try:
                async with engine.begin() as conn:
                    await conn.execute(
                        text("DELETE FROM tenants WHERE id = :id"), {"id": str(tenant_id)}
                    )
            finally:
                await engine.dispose()
