"""Unit tests for order synchronisation tasks.

Celery workers are not started in the unit suite — these tests verify task
registration, fan-out behaviour, beat scheduling, and failure handling without
a broker, following the pattern of ``test_product_tasks.py``.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.integrations.aliexpress.exceptions import AliExpressError
from app.tasks import orders as orders_tasks
from app.workers.celery_app import celery_app

pytestmark = pytest.mark.unit


class TestTaskRegistration:
    @pytest.mark.parametrize(
        ("task", "name"),
        [
            (orders_tasks.sync_all, "orders.sync_all"),
            (orders_tasks.sync_one_store, "orders.sync_one_store"),
            (orders_tasks.refresh_status, "orders.refresh_status"),
            (orders_tasks.cleanup, "orders.cleanup"),
        ],
    )
    def test_registered_under_the_expected_name(self, task: Any, name: str) -> None:
        assert task.name == name

    def test_every_order_task_is_on_the_beat_schedule_or_fanned_out(self) -> None:
        """The phase brief requires periodic scheduling. `sync_one_store` is
        fanned out by `sync_all` rather than scheduled directly, so it is the
        one legitimate absence."""
        schedule = celery_app.conf.beat_schedule
        scheduled_tasks = {entry["task"] for entry in schedule.values()}

        assert "orders.sync_all" in scheduled_tasks
        assert "orders.refresh_status" in scheduled_tasks
        assert "orders.cleanup" in scheduled_tasks
        assert "orders.sync_one_store" not in scheduled_tasks


class TestSyncAllTask:
    def test_fans_out_one_task_per_connected_tenant(self, monkeypatch: pytest.MonkeyPatch) -> None:
        tenants = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
        queued: list[dict[str, Any]] = []

        monkeypatch.setattr(orders_tasks.asyncio, "run", lambda _: tenants)

        class FakeSyncTask:
            def delay(self, **kwargs: Any) -> None:
                queued.append(kwargs)

        monkeypatch.setattr(orders_tasks, "sync_one_store", FakeSyncTask())

        count = orders_tasks.sync_all.run()

        assert count == 3
        assert [item["tenant_id"] for item in queued] == [str(t) for t in tenants]

    def test_no_connected_tenants_means_no_fanout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(orders_tasks.asyncio, "run", lambda _: [])

        class FakeSyncTask:
            def delay(self, **kwargs: Any) -> None:
                raise AssertionError("nothing should be queued")

        monkeypatch.setattr(orders_tasks, "sync_one_store", FakeSyncTask())

        assert orders_tasks.sync_all.run() == 0


class TestSyncOneStoreTask:
    def test_delegates_to_the_async_implementation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        tenant_id = uuid.uuid4()
        calls: list[tuple[uuid.UUID, int]] = []

        async def fake_sync(tid: uuid.UUID, *, since_days: int) -> bool:
            calls.append((tid, since_days))
            return True

        monkeypatch.setattr(orders_tasks, "_sync_tenant", fake_sync)

        result = orders_tasks.sync_one_store.run(tenant_id=str(tenant_id), since_days=3)

        assert result is True
        assert calls == [(tenant_id, 3)]

    def test_a_permanent_supplier_failure_is_not_retried(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Retrying a rejected token three times with backoff is three wasted
        calls that end the same way; the failure is already on the run row."""

        async def failing(_tid: uuid.UUID, *, since_days: int) -> bool:
            raise AliExpressError("publisher not registered")

        monkeypatch.setattr(orders_tasks, "_sync_tenant", failing)

        result = orders_tasks.sync_one_store.run(tenant_id=str(uuid.uuid4()))

        assert result is False

    def test_retryable_supplier_failures_reach_celery_retry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class RetryableError(AliExpressError):
            retryable = True

        async def failing(_tid: uuid.UUID, *, since_days: int) -> bool:
            raise RetryableError("rate limited")

        monkeypatch.setattr(orders_tasks, "_sync_tenant", failing)

        with pytest.raises(RetryableError):
            orders_tasks.sync_one_store.run(tenant_id=str(uuid.uuid4()))


class TestRefreshStatusTask:
    def test_refreshes_every_connected_tenant(self, monkeypatch: pytest.MonkeyPatch) -> None:
        tenants = [uuid.uuid4(), uuid.uuid4()]
        refreshed_calls: list[uuid.UUID] = []

        # asyncio.run is called once for the tenant list and once per tenant;
        # dispatch on the coroutine's name to keep the fake honest.
        def fake_run(coro: Any) -> Any:
            name = coro.cr_code.co_name
            coro.close()
            if name == "_connected_tenants":
                return tenants
            refreshed_calls.append(name)
            return 5

        monkeypatch.setattr(orders_tasks.asyncio, "run", fake_run)

        total = orders_tasks.refresh_status.run(limit=10)

        assert total == 10
        assert len(refreshed_calls) == 2


class TestCleanupTask:
    def test_purges_across_tenants_and_sums_the_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tenants = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]

        def fake_run(coro: Any) -> Any:
            name = coro.cr_code.co_name
            coro.close()
            if name == "_all_tenant_ids_with_runs":
                return tenants
            return 4

        monkeypatch.setattr(orders_tasks.asyncio, "run", fake_run)

        assert orders_tasks.cleanup.run() == 12
