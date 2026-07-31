"""Unit tests for catalogue synchronisation tasks.

Celery workers are not started in the unit suite — these tests verify task
registration, fan-out behaviour, and failure handling without a broker.
"""

from __future__ import annotations

import uuid

import pytest

from app.integrations.aliexpress.exceptions import AliExpressError
from app.tasks import products as products_tasks

pytestmark = pytest.mark.unit


class TestSyncProductTask:
    def test_registered_under_the_expected_name(self) -> None:
        assert products_tasks.sync_product.name == "products.sync_one"

    def test_delegates_to_the_async_implementation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        product_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        calls: list[tuple[uuid.UUID, uuid.UUID]] = []

        async def fake_sync_one(pid: uuid.UUID, tid: uuid.UUID) -> bool:
            calls.append((pid, tid))
            return True

        monkeypatch.setattr(products_tasks, "_sync_one", fake_sync_one)

        result = products_tasks.sync_product.run(
            product_id=str(product_id),
            tenant_id=str(tenant_id),
        )

        assert result is True
        assert calls == [(product_id, tenant_id)]

    def test_marks_unavailable_on_a_permanent_supplier_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        product_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        marked: list[tuple[uuid.UUID, uuid.UUID, str]] = []

        async def failing_sync_one(_pid: uuid.UUID, _tid: uuid.UUID) -> bool:
            raise AliExpressError("gone", upstream_code="ITEM_ID_NOT_FOUND")

        async def fake_mark(pid: uuid.UUID, tid: uuid.UUID, reason: str) -> None:
            marked.append((pid, tid, reason))

        monkeypatch.setattr(products_tasks, "_sync_one", failing_sync_one)
        monkeypatch.setattr(products_tasks, "_mark_unavailable", fake_mark)

        result = products_tasks.sync_product.run(
            product_id=str(product_id),
            tenant_id=str(tenant_id),
        )

        assert result is False
        assert marked == [(product_id, tenant_id, "gone")]

    def test_reraises_retryable_supplier_failures(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class RetryableError(AliExpressError):
            retryable = True

        async def failing_sync_one(_pid: uuid.UUID, _tid: uuid.UUID) -> bool:
            raise RetryableError("rate limited")

        monkeypatch.setattr(products_tasks, "_sync_one", failing_sync_one)

        with pytest.raises(RetryableError):
            products_tasks.sync_product.run(
                product_id=str(uuid.uuid4()),
                tenant_id=str(uuid.uuid4()),
            )


class TestSweepStaleProductsTask:
    def test_registered_under_the_expected_name(self) -> None:
        assert products_tasks.sweep_stale_products.name == "products.sweep_stale"

    def test_queues_one_sync_task_per_stale_product(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stale = [(uuid.uuid4(), uuid.uuid4()), (uuid.uuid4(), uuid.uuid4())]
        queued: list[dict[str, str]] = []

        monkeypatch.setattr(products_tasks.asyncio, "run", lambda _: stale)

        class FakeSyncTask:
            def delay(self, **kwargs: str) -> None:
                queued.append(kwargs)

        monkeypatch.setattr(products_tasks, "sync_product", FakeSyncTask())

        count = products_tasks.sweep_stale_products.run(limit=50)

        assert count == 2
        assert len(queued) == 2
        assert all("product_id" in item and "tenant_id" in item for item in queued)

    def test_returns_zero_when_nothing_is_stale(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(products_tasks.asyncio, "run", lambda _: [])

        class FakeSyncTask:
            def delay(self, **kwargs: str) -> None:
                raise AssertionError("should not queue when nothing is stale")

        monkeypatch.setattr(products_tasks, "sync_product", FakeSyncTask())

        assert products_tasks.sweep_stale_products.run() == 0
