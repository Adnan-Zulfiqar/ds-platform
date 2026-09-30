"""HTTP contract, schema, tenancy, and concurrent create races for Stage 9."""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import set_tenant_id
from app.core.tokens import create_access_token
from app.main import create_application
from app.models.pipeline_bulk import (
    PipelineBulkItemState,
    PipelineBulkRun,
    PipelineBulkRunItem,
    PipelineBulkRunStatus,
)
from app.models.product import Product, ProductSource, ProductStatus
from app.models.store import Store, StorePlatform, StoreStatus
from app.services.pipeline_bulk import MAX_PIPELINE_BULK_PRODUCTS
from app.tasks import ai as ai_tasks
from tests.integration.pipeline_bulk_harness import EnqueueRecorder, bind_queue
from tests.integration.pipeline_bulk_live import live_bulk
from tests.integration.test_products import auth_header, register

pytestmark = pytest.mark.integration

RUNS = "/api/v1/products/pipeline/runs"


@pytest.fixture(autouse=True)
def queue(monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession) -> EnqueueRecorder:
    return bind_queue(monkeypatch, db_session)


async def seed_tenant(client: AsyncClient) -> tuple[dict[str, str], uuid.UUID]:
    body = await register(client)
    tenant_id = uuid.UUID(str(body["identity"]["tenant"]["id"]))
    set_tenant_id(tenant_id)
    return auth_header(body), tenant_id


async def seed_product(db_session: AsyncSession, tenant_id: uuid.UUID) -> Product:
    product = Product(
        tenant_id=tenant_id,
        source=ProductSource.MANUAL,
        external_id=f"bulk-{uuid.uuid4().hex[:10]}",
        title="Bulk product",
        status=ProductStatus.DRAFT,
        sell_price=Decimal("9.99"),
    )
    db_session.add(product)
    await db_session.flush()
    return product


async def seed_store(db_session: AsyncSession, tenant_id: uuid.UUID) -> Store:
    store = Store(
        tenant_id=tenant_id,
        name="Bulk store",
        slug=f"bulk-{uuid.uuid4().hex[:8]}",
        platform=StorePlatform.SHOPIFY,
        status=StoreStatus.CONNECTED,
        currency="USD",
    )
    db_session.add(store)
    await db_session.flush()
    return store


def role_headers(tenant_id: uuid.UUID, role: str) -> dict[str, str]:
    token = create_access_token(user_id=uuid.uuid4(), tenant_id=tenant_id, roles=(role,))
    return {"Authorization": f"Bearer {token.token}"}


class TestMigration0034:
    async def test_schema_objects_exist(self, db_session: AsyncSession) -> None:
        version = (
            await db_session.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar()
        # 0034's objects must exist at any later head; revisions are 4-digit.
        assert version is not None and version >= "0034"

        indexes = (
            (
                await db_session.execute(
                    text(
                        "SELECT indexname FROM pg_indexes WHERE tablename IN "
                        "('pipeline_bulk_runs','pipeline_bulk_run_items','products','product_versions')"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert "uq_products_tenant_id_id" in indexes
        assert "uq_product_versions_tenant_id_id" in indexes
        assert "uq_pipeline_bulk_runs_tenant_idempotency" in indexes
        assert "uq_pipeline_bulk_runs_tenant_id_id" in indexes
        assert "uq_pipeline_bulk_runs_tenant_active" in indexes
        assert "uq_pipeline_bulk_run_items_run_product" in indexes
        assert "ix_pipeline_bulk_runs_pending_created" in indexes
        assert "ix_pipeline_bulk_runs_running_heartbeat" in indexes
        assert "ix_pipeline_bulk_run_items_tenant_run" in indexes
        assert "ix_pipeline_bulk_run_items_run_state" in indexes

        constraints = (
            (
                await db_session.execute(
                    text(
                        "SELECT conname FROM pg_constraint WHERE conname IN ("
                        "'ck_pipeline_bulk_run_items_succeeded_version',"
                        "'fk_pipeline_bulk_run_items_run_tenant',"
                        "'fk_pipeline_bulk_run_items_product_tenant',"
                        "'fk_pipeline_bulk_run_items_version_tenant'"
                        ")"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert "ck_pipeline_bulk_run_items_succeeded_version" in constraints
        assert "fk_pipeline_bulk_run_items_run_tenant" in constraints
        assert "fk_pipeline_bulk_run_items_product_tenant" in constraints
        assert "fk_pipeline_bulk_run_items_version_tenant" in constraints

    async def test_enum_values_not_names(self, db_session: AsyncSession) -> None:
        run_values = (
            (
                await db_session.execute(
                    text(
                        "SELECT e.enumlabel FROM pg_enum e "
                        "JOIN pg_type t ON t.oid = e.enumtypid "
                        "WHERE t.typname = 'pipeline_bulk_run_status' "
                        "ORDER BY e.enumsortorder"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert list(run_values) == [
            "pending",
            "running",
            "completed",
            "partial",
            "failed",
            "cancelled",
        ]

    async def test_cross_tenant_product_fk_is_rejected(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        own = await seed_product(db_session, tenant_id)
        other = await register(client)
        other_id = uuid.UUID(str(other["identity"]["tenant"]["id"]))
        foreign = await seed_product(db_session, other_id)
        set_tenant_id(tenant_id)
        created = await client.post(
            RUNS,
            json={"productIds": [str(own.id)], "idempotencyKey": "fk-cross"},
            headers=headers,
        )
        assert created.status_code == 202, created.text
        item = (
            await db_session.execute(
                select(PipelineBulkRunItem).where(
                    PipelineBulkRunItem.run_id == uuid.UUID(created.json()["id"])
                )
            )
        ).scalar_one()
        item.product_id = foreign.id
        with pytest.raises(IntegrityError):
            await db_session.flush()
        await db_session.rollback()
        set_tenant_id(tenant_id)

    async def test_check_succeeded_requires_candidate(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        product = await seed_product(db_session, tenant_id)
        created = await client.post(
            RUNS,
            json={"productIds": [str(product.id)], "idempotencyKey": "check-ok"},
            headers=headers,
        )
        item = (
            await db_session.execute(
                select(PipelineBulkRunItem).where(
                    PipelineBulkRunItem.run_id == uuid.UUID(created.json()["id"])
                )
            )
        ).scalar_one()
        item.state = PipelineBulkItemState.SUCCEEDED
        with pytest.raises(IntegrityError):
            await db_session.flush()
        await db_session.rollback()
        set_tenant_id(tenant_id)

    async def test_attached_product_cannot_be_hard_deleted(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        product = await seed_product(db_session, tenant_id)
        created = await client.post(
            RUNS,
            json={"productIds": [str(product.id)], "idempotencyKey": "restrict"},
            headers=headers,
        )
        item = (
            await db_session.execute(
                select(PipelineBulkRunItem).where(
                    PipelineBulkRunItem.run_id == uuid.UUID(created.json()["id"])
                )
            )
        ).scalar_one()
        item.product_id = product.id
        await db_session.flush()
        with pytest.raises(IntegrityError):
            await db_session.execute(
                text("DELETE FROM products WHERE id = :id"), {"id": product.id}
            )
            await db_session.flush()
        await db_session.rollback()
        set_tenant_id(tenant_id)


class TestStart:
    async def test_admin_starts_a_run(
        self, client: AsyncClient, db_session: AsyncSession, queue: EnqueueRecorder
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        product = await seed_product(db_session, tenant_id)
        duplicate = uuid.uuid4()
        response = await client.post(
            RUNS,
            json={
                "productIds": [str(product.id), str(product.id), str(duplicate)],
                "idempotencyKey": "start-1",
            },
            headers=headers,
        )
        assert response.status_code == 202, response.text
        body = response.json()
        assert body["status"] == "pending"
        assert body["totalCount"] == 2
        assert body["processedCount"] == 0
        assert "items" not in body
        assert queue.calls == [], "published before the row was durable"
        await db_session.commit()
        assert queue.run_ids == [body["id"]]
        items = (
            (
                await db_session.execute(
                    select(PipelineBulkRunItem).where(
                        PipelineBulkRunItem.run_id == uuid.UUID(body["id"])
                    )
                )
            )
            .scalars()
            .all()
        )
        assert {item.submitted_product_id for item in items} == {product.id, duplicate}
        assert all(item.product_id is None for item in items)
        assert all(item.state is PipelineBulkItemState.PENDING for item in items)

    async def test_viewer_and_member_are_forbidden(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, tenant_id = await seed_tenant(client)
        product = await seed_product(db_session, tenant_id)
        payload = {"productIds": [str(product.id)], "idempotencyKey": "role"}
        for role in ("viewer", "member"):
            response = await client.post(RUNS, json=payload, headers=role_headers(tenant_id, role))
            assert response.status_code == 403, response.text

    async def test_unauthenticated_is_401(self, client: AsyncClient) -> None:
        response = await client.post(
            RUNS, json={"productIds": [str(uuid.uuid4())], "idempotencyKey": "anon"}
        )
        assert response.status_code == 401

    async def test_empty_and_oversized_lists_are_422(self, client: AsyncClient) -> None:
        headers, _ = await seed_tenant(client)
        empty = await client.post(
            RUNS, json={"productIds": [], "idempotencyKey": "empty"}, headers=headers
        )
        assert empty.status_code == 422
        too_many = await client.post(
            RUNS,
            json={
                "productIds": [str(uuid.uuid4()) for _ in range(MAX_PIPELINE_BULK_PRODUCTS + 1)],
                "idempotencyKey": "too-many",
            },
            headers=headers,
        )
        assert too_many.status_code == 422
        assert str(MAX_PIPELINE_BULK_PRODUCTS) in too_many.text

    async def test_invalid_tone_is_422(self, client: AsyncClient) -> None:
        headers, _ = await seed_tenant(client)
        blank = await client.post(
            RUNS,
            json={"productIds": [str(uuid.uuid4())], "idempotencyKey": "tone", "tone": ""},
            headers=headers,
        )
        assert blank.status_code == 422

    async def test_same_key_same_fingerprint_returns_original(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        product = await seed_product(db_session, tenant_id)
        payload = {"productIds": [str(product.id)], "idempotencyKey": "retry-me"}
        first = await client.post(RUNS, json=payload, headers=headers)
        second = await client.post(RUNS, json=payload, headers=headers)
        assert first.status_code == second.status_code == 202
        assert first.json()["id"] == second.json()["id"]
        count = (
            (
                await db_session.execute(
                    select(PipelineBulkRun).where(PipelineBulkRun.tenant_id == tenant_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(count) == 1

    async def test_same_key_different_fingerprint_is_409(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        product = await seed_product(db_session, tenant_id)
        first = await client.post(
            RUNS,
            json={
                "productIds": [str(product.id)],
                "idempotencyKey": "clash",
                "tone": "professional",
            },
            headers=headers,
        )
        assert first.status_code == 202
        second = await client.post(
            RUNS,
            json={"productIds": [str(product.id)], "idempotencyKey": "clash", "tone": "casual"},
            headers=headers,
        )
        assert second.status_code == 409
        assert second.json()["code"] == "conflict"

    async def test_second_key_while_active_is_pipeline_bulk_run_active(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        product = await seed_product(db_session, tenant_id)
        first = await client.post(
            RUNS,
            json={"productIds": [str(product.id)], "idempotencyKey": "active-a"},
            headers=headers,
        )
        assert first.status_code == 202
        second = await client.post(
            RUNS,
            json={"productIds": [str(product.id)], "idempotencyKey": "active-b"},
            headers=headers,
        )
        assert second.status_code == 409
        assert second.json()["code"] == "pipeline_bulk_run_active"

    async def test_foreign_store_is_404_and_creates_no_run(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        product = await seed_product(db_session, tenant_id)
        other = await register(client)
        other_id = uuid.UUID(str(other["identity"]["tenant"]["id"]))
        foreign_store = await seed_store(db_session, other_id)
        set_tenant_id(tenant_id)
        response = await client.post(
            RUNS,
            json={
                "productIds": [str(product.id)],
                "idempotencyKey": "foreign-store",
                "storeId": str(foreign_store.id),
            },
            headers=headers,
        )
        assert response.status_code == 404
        runs = (
            (
                await db_session.execute(
                    select(PipelineBulkRun).where(PipelineBulkRun.tenant_id == tenant_id)
                )
            )
            .scalars()
            .all()
        )
        assert runs == []

    async def test_missing_store_is_404(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        product = await seed_product(db_session, tenant_id)
        response = await client.post(
            RUNS,
            json={
                "productIds": [str(product.id)],
                "idempotencyKey": "missing-store",
                "storeId": str(uuid.uuid4()),
            },
            headers=headers,
        )
        assert response.status_code == 404


class TestReadAndCancel:
    async def test_get_and_items_and_cancel(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        product = await seed_product(db_session, tenant_id)
        created = await client.post(
            RUNS,
            json={"productIds": [str(product.id)], "idempotencyKey": "read-1"},
            headers=headers,
        )
        run_id = created.json()["id"]
        got = await client.get(f"{RUNS}/{run_id}", headers=headers)
        assert got.status_code == 200
        items = await client.get(f"{RUNS}/{run_id}/items", headers=headers)
        assert items.status_code == 200
        page = items.json()
        assert page["meta"]["totalItems"] == 1
        assert page["items"][0]["submittedProductId"] == str(product.id)
        assert page["items"][0]["productId"] is None
        cancelled = await client.post(f"{RUNS}/{run_id}/cancel", headers=headers)
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
        again = await client.post(f"{RUNS}/{run_id}/cancel", headers=headers)
        assert again.status_code == 200

    async def test_foreign_run_is_404(self, client: AsyncClient, db_session: AsyncSession) -> None:
        owner, tenant_id = await seed_tenant(client)
        product = await seed_product(db_session, tenant_id)
        created = await client.post(
            RUNS,
            json={"productIds": [str(product.id)], "idempotencyKey": "secret"},
            headers=owner,
        )
        other = await register(client)
        run_id = created.json()["id"]
        for method, path in (
            ("GET", f"{RUNS}/{run_id}"),
            ("GET", f"{RUNS}/{run_id}/items"),
            ("POST", f"{RUNS}/{run_id}/cancel"),
        ):
            response = await client.request(method, path, headers=auth_header(other))
            assert response.status_code == 404, response.text

    async def test_cancel_of_completed_is_409(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        product = await seed_product(db_session, tenant_id)
        created = await client.post(
            RUNS,
            json={"productIds": [str(product.id)], "idempotencyKey": "done"},
            headers=headers,
        )
        run = (
            await db_session.execute(
                select(PipelineBulkRun).where(PipelineBulkRun.id == uuid.UUID(created.json()["id"]))
            )
        ).scalar_one()
        run.status = PipelineBulkRunStatus.COMPLETED
        await db_session.flush()
        response = await client.post(f"{RUNS}/{run.id}/cancel", headers=headers)
        assert response.status_code == 409


class TestConcurrentCreate:
    async def test_same_key_same_payload_resolves_to_one_row(self) -> None:
        async with live_bulk(create_run=False, products=1) as live:
            payload = {
                "productIds": [str(live.product_ids[0])],
                "idempotencyKey": "same-key",
            }
            recorder = EnqueueRecorder()
            original = ai_tasks.enqueue
            ai_tasks.enqueue = recorder  # type: ignore[method-assign]
            try:
                app = create_application()
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://testserver"
                ) as http:
                    first, second = await asyncio.gather(
                        http.post(RUNS, json=payload, headers=live.headers),
                        http.post(RUNS, json=payload, headers=live.headers),
                    )
            finally:
                ai_tasks.enqueue = original  # type: ignore[method-assign]
            assert sorted([first.status_code, second.status_code]) == [202, 202]
            assert first.json()["id"] == second.json()["id"]
            async with live.session_factory() as session:
                rows = (
                    (
                        await session.execute(
                            select(PipelineBulkRun).where(
                                PipelineBulkRun.tenant_id == live.tenant_id
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            assert len(rows) == 1

    async def test_different_keys_yield_one_active_run(self) -> None:
        async with live_bulk(create_run=False, products=1) as live:
            recorder = EnqueueRecorder()
            original = ai_tasks.enqueue
            ai_tasks.enqueue = recorder  # type: ignore[method-assign]
            try:
                app = create_application()
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://testserver"
                ) as http:
                    first, second = await asyncio.gather(
                        http.post(
                            RUNS,
                            json={
                                "productIds": [str(live.product_ids[0])],
                                "idempotencyKey": "key-a",
                            },
                            headers=live.headers,
                        ),
                        http.post(
                            RUNS,
                            json={
                                "productIds": [str(live.product_ids[0])],
                                "idempotencyKey": "key-b",
                            },
                            headers=live.headers,
                        ),
                    )
            finally:
                ai_tasks.enqueue = original  # type: ignore[method-assign]
            codes = sorted([first.status_code, second.status_code])
            assert codes == [202, 409]
            conflict = first if first.status_code == 409 else second
            assert conflict.json()["code"] == "pipeline_bulk_run_active"
            async with live.session_factory() as session:
                active = (
                    (
                        await session.execute(
                            select(PipelineBulkRun)
                            .where(PipelineBulkRun.tenant_id == live.tenant_id)
                            .where(
                                PipelineBulkRun.status.in_(
                                    (PipelineBulkRunStatus.PENDING, PipelineBulkRunStatus.RUNNING)
                                )
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            assert len(active) == 1

    async def test_terminal_run_allows_a_new_key(self) -> None:
        async with live_bulk(create_run=True, products=1, idempotency_key="first") as live:
            assert live.run_id is not None
            async with live.session_factory() as session:
                run = (
                    await session.execute(
                        select(PipelineBulkRun).where(PipelineBulkRun.id == live.run_id)
                    )
                ).scalar_one()
                run.status = PipelineBulkRunStatus.COMPLETED
                await session.commit()
            recorder = EnqueueRecorder()
            original = ai_tasks.enqueue
            ai_tasks.enqueue = recorder  # type: ignore[method-assign]
            try:
                app = create_application()
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://testserver"
                ) as http:
                    created = await http.post(
                        RUNS,
                        json={
                            "productIds": [str(live.product_ids[0])],
                            "idempotencyKey": "second",
                        },
                        headers=live.headers,
                    )
            finally:
                ai_tasks.enqueue = original  # type: ignore[method-assign]
            assert created.status_code == 202, created.text
            assert created.json()["id"] != str(live.run_id)
