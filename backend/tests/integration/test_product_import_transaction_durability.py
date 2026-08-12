"""A failed import must survive the request transaction that recorded it.

Every other integration test in this suite runs through the ``client``
fixture, which overrides ``get_db_session`` to yield one session shared across
the whole test and rolled back only once, at teardown
(``tests/integration/conftest.py``). That override never exercises the real
per-request contract in ``app.api.deps.get_db_session``: *commit on success,
roll back on any exception*. A handler that raises for a completely ordinary,
expected reason -- a rejected AliExpress listing, an expired token, a
disconnected supplier -- still triggers that rollback, and until this test was
added nothing in the suite could have caught a write meant to survive it being
silently discarded.

That is exactly what was happening: ``ProductImportService._fail`` set
``status=FAILED`` on the ``ProductImport`` row and flushed it, then
``import_product`` re-raised the AliExpress error so the router could return a
proper error response -- and the outer ``get_db_session`` wrapper rolled that
whole transaction back, erasing the failure record along with it. Verified
live in a browser against the real dev stack: a genuine failed import (no
AliExpress connection) left the ``product_imports`` table with zero rows for
that tenant, despite the API correctly returning a 409 and the "AliExpress not
connected" alert rendering. The fix
(``ProductImportService._persist_failure_durably``) commits the failure row on
an independent connection so it survives the request's own rollback. This test
exercises the *real* dependency, not the test override, so a regression here
is caught even though every other test in this file uses the faster shared
session.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings
from app.main import create_application
from tests.integration.conftest import registration_payload

pytestmark = pytest.mark.integration

IMPORT_URL = "/api/v1/products/import"
IMPORTS_URL = "/api/v1/products/imports"


async def test_a_failed_import_survives_the_request_transaction_rolling_back() -> None:
    """No test-fixture shortcut: real app, real `get_db_session`, real commit/rollback."""
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

            # No AliExpress connection exists for this brand-new tenant, so this
            # is guaranteed to fail with `aliexpress_not_connected` (409) --
            # exactly the ordinary, expected failure this test is about.
            failed = await client.post(
                IMPORT_URL,
                json={"externalId": "3256806389000685", "shipToCountry": "US"},
                headers=headers,
            )
            assert failed.status_code == 409, failed.text
            assert failed.json()["code"] == "aliexpress_not_connected"

            # The request that just 409'd rolled back its own transaction. If
            # the failure row lived only in that transaction, it is gone now --
            # querying through the *same* app/session dependency is what a
            # real subsequent request (e.g. loading Import History) would do.
            history = await client.get(IMPORTS_URL, headers=headers)
            assert history.status_code == 200
            items = history.json()["items"]
            assert len(items) == 1, (
                "the failed import must be visible in history — this is the "
                "exact regression: a rolled-back request silently erasing the "
                "one row whose purpose is to survive its own failure"
            )
            assert items[0]["status"] == "failed"
            assert items[0]["errorCode"] == "aliexpress_not_connected"

        # Belt and braces: confirm durability against a *fresh* connection too,
        # independent of anything the app's own connection pool might be
        # caching in memory.
        engine = create_async_engine(settings.database.async_dsn)
        try:
            async with engine.connect() as conn:
                result = await conn.execute(
                    text(
                        "SELECT status, error_code FROM product_imports "
                        "WHERE tenant_id = :tenant_id"
                    ),
                    {"tenant_id": str(tenant_id)},
                )
                rows = result.fetchall()
        finally:
            await engine.dispose()

        assert len(rows) == 1
        assert rows[0].status == "failed"
        assert rows[0].error_code == "aliexpress_not_connected"
    finally:
        if tenant_id is not None:
            # This test commits for real (that is the point), so it cannot
            # rely on the suite's usual rollback-based cleanup -- cascade
            # delete from `tenants` removes the user and the product_imports
            # row created above (`ondelete="CASCADE"` on both).
            engine = create_async_engine(settings.database.async_dsn)
            try:
                async with engine.begin() as conn:
                    await conn.execute(
                        text("DELETE FROM tenants WHERE id = :id"),
                        {"id": str(tenant_id)},
                    )
            finally:
                await engine.dispose()
