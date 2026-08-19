"""M3A-3 acceptance fix — confirmed applications run on the Celery queue.

Everything here goes through the production entry points: the HTTP endpoint
that accepts the work, the post-commit hand-off that publishes the message,
and the registered task invoked through ``Task.apply``. Nothing calls
``RuleApplicationService`` directly except where the subject *is* the service
contract the worker depends on (the atomic claim).

The broker is never contacted; see ``rule_application_harness``.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import set_tenant_id
from app.models.pricing import PriceChange
from app.models.product import Product
from app.models.rule_application import (
    ApplicationStatus,
    RuleApplication,
    RuleApplicationItem,
)
from app.services import rule_application as service_module
from app.services.rule_application import (
    APPLICATION_BATCH_SIZE,
    MAX_APPLICATION_PRODUCTS,
    ApplyRequest,
    ClaimResult,
    RuleApplicationService,
)
from app.tasks import pricing as pricing_tasks
from tests.integration.rule_application_harness import (
    EnqueueRecorder,
    bind_queue,
    run_dispatch,
    run_task,
)
from tests.integration.test_rule_application import (
    APPLY,
    BASE,
    create_rule,
    publish,
    seed_draft,
    seed_tenant,
    viewer_header,
)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def queue(monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession) -> EnqueueRecorder:
    return bind_queue(monkeypatch, db_session)


async def confirm(
    client: AsyncClient, headers: dict[str, str], **payload: object
) -> dict[str, Any]:
    response = await client.post(APPLY, json=payload, headers=headers)
    assert response.status_code == 202, response.text
    body: dict[str, Any] = response.json()
    return body


async def read(client: AsyncClient, headers: dict[str, str], application_id: str) -> dict[str, Any]:
    response = await client.get(f"{BASE}/applications/{application_id}", headers=headers)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def row(db_session: AsyncSession, application_id: str) -> RuleApplication:
    found = (
        await db_session.execute(
            select(RuleApplication).where(RuleApplication.id == uuid.UUID(application_id))
        )
    ).scalar_one()
    await db_session.refresh(found)
    return found


async def item_count(db_session: AsyncSession, application_id: str) -> int:
    return int(
        (
            await db_session.execute(
                select(func.count())
                .select_from(RuleApplicationItem)
                .where(RuleApplicationItem.application_id == uuid.UUID(application_id))
            )
        ).scalar_one()
    )


class TestAcceptance:
    async def test_the_endpoint_returns_a_pending_application(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """202 means accepted, not done. Nothing has been priced yet."""
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")

        body = await confirm(
            client, headers, productIds=[str(product.id)], idempotencyKey="accepted"
        )

        assert body["status"] == "pending"
        assert body["items"] == []
        assert body["totalCount"] == 1
        assert body["processedCount"] == 0
        await db_session.refresh(product)
        assert product.sell_price == Decimal("30.0000")

    async def test_the_enqueued_payload_carries_only_the_application_id(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        queue: EnqueueRecorder,
    ) -> None:
        """No product list, no rule snapshot, no merchant data on the broker."""
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")

        body = await confirm(
            client, headers, productIds=[str(product.id)], idempotencyKey="payload"
        )

        assert queue.application_ids == [body["id"]]
        published = queue.calls[0]
        assert published["task"] == "pricing.apply_rules_to_drafts"
        assert set(published) == {"task", "application_id"}
        assert str(product.id) not in str(published)

    async def test_nothing_is_enqueued_when_the_request_is_rejected(
        self, client: AsyncClient, queue: EnqueueRecorder
    ) -> None:
        """The hand-off is downstream of a successful creation, so a refused
        request cannot leave a message pointing at no row."""
        headers, _ = await seed_tenant(client)

        response = await client.post(
            APPLY, json={"productIds": [], "idempotencyKey": "nope"}, headers=headers
        )

        assert response.status_code == 422
        assert queue.calls == []

    async def test_a_repeat_of_the_same_key_is_not_enqueued_twice(
        self, client: AsyncClient, db_session: AsyncSession, queue: EnqueueRecorder
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")
        payload = {"productIds": [str(product.id)], "idempotencyKey": "once"}

        first = await client.post(APPLY, json=payload, headers=headers)
        second = await client.post(APPLY, json=payload, headers=headers)

        assert first.json()["id"] == second.json()["id"]
        assert len(queue.calls) == 1

    async def test_the_same_key_with_a_different_payload_is_a_conflict(
        self, client: AsyncClient, db_session: AsyncSession, queue: EnqueueRecorder
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        first_product = await seed_draft(db_session, tenant_id)
        second_product = await seed_draft(db_session, tenant_id)

        await client.post(
            APPLY,
            json={"productIds": [str(first_product.id)], "idempotencyKey": "shared"},
            headers=headers,
        )
        clash = await client.post(
            APPLY,
            json={"productIds": [str(second_product.id)], "idempotencyKey": "shared"},
            headers=headers,
        )

        assert clash.status_code == 409, clash.text
        assert len(queue.calls) == 1

    async def test_a_selection_above_the_documented_ceiling_is_refused(
        self, client: AsyncClient
    ) -> None:
        """Refused with the limit named -- never silently truncated."""
        headers, _ = await seed_tenant(client)

        response = await client.post(
            APPLY,
            json={
                "productIds": [str(uuid.uuid4()) for _ in range(MAX_APPLICATION_PRODUCTS + 1)],
                "idempotencyKey": "too-many",
            },
            headers=headers,
        )

        assert response.status_code == 422
        assert str(MAX_APPLICATION_PRODUCTS) in response.text

    async def test_the_preview_publishes_the_limits(self, client: AsyncClient) -> None:
        """The screen that builds a selection is told what it may submit."""
        headers, _ = await seed_tenant(client)

        body = (await client.get(f"{BASE}/drafts/impact", headers=headers)).json()

        assert body["maxApplicationProducts"] == MAX_APPLICATION_PRODUCTS
        assert body["applicationBatchSize"] == APPLICATION_BATCH_SIZE


class TestWorkerExecution:
    async def test_the_worker_takes_a_pending_run_to_completed(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")
        body = await confirm(
            client, headers, productIds=[str(product.id)], idempotencyKey="lifecycle"
        )

        result = await run_task(monkeypatch, application_id=body["id"], tenant_id=tenant_id)

        assert result["status"] == "completed"
        record = await row(db_session, body["id"])
        assert record.status is ApplicationStatus.COMPLETED
        assert record.claimed_by_task_id is not None
        assert record.started_at is not None and record.finished_at is not None
        assert record.processed_count == 1
        await db_session.refresh(product)
        assert product.sell_price == Decimal("21.0000")

    async def test_a_partial_run_is_reported_as_partial(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        good = await seed_draft(db_session, tenant_id, sell_price="30.00")
        bad = await seed_draft(db_session, tenant_id, cost=None)
        body = await confirm(
            client,
            headers,
            productIds=[str(good.id), str(bad.id)],
            idempotencyKey="partial-run",
        )

        result = await run_task(monkeypatch, application_id=body["id"], tenant_id=tenant_id)

        assert result["status"] == "partial"
        assert result["applied"] == 1
        assert result["review"] == 1
        await db_session.refresh(good)
        assert good.sell_price == Decimal("21.0000")

    async def test_the_tenant_comes_from_the_row_not_the_caller(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A message cannot steer the worker at another tenant's catalogue.

        The task is invoked with a *different* tenant bound in the ambient
        context, which is the closest a forged or stale payload could get. The
        run still executes against the tenant recorded on the application.
        """
        owner, owner_tenant = await seed_tenant(client)
        await create_rule(client, owner)
        product = await seed_draft(db_session, owner_tenant, sell_price="30.00")
        body = await confirm(
            client, owner, productIds=[str(product.id)], idempotencyKey="tenant-safe"
        )

        _, intruder_tenant = await seed_tenant(client)  # binds the other tenant
        assert intruder_tenant != owner_tenant

        result = await run_task(monkeypatch, application_id=body["id"], tenant_id=owner_tenant)

        assert result["status"] == "completed"
        await db_session.refresh(product)
        assert product.sell_price == Decimal("21.0000")

    async def test_an_application_that_no_longer_exists_is_a_no_op(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A message can outlive its row; that is not a failure to retry."""
        result = await run_task(monkeypatch, application_id=uuid.uuid4())

        assert result["status"] == ClaimResult.UNKNOWN.value

    async def test_a_published_product_is_never_repriced_by_the_worker(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")
        await publish(db_session, product)
        body = await confirm(client, headers, productIds=[str(product.id)], idempotencyKey="live")

        await run_task(monkeypatch, application_id=body["id"], tenant_id=tenant_id)

        await db_session.refresh(product)
        assert product.sell_price == Decimal("30.0000")
        results = await read(client, headers, body["id"])
        assert {i["outcome"] for i in results["items"]} == {"published"}

    async def test_supplier_snapshots_survive_a_worker_run(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")
        product.supplier_title = "Supplier original"
        product.supplier_description = "<p>Supplier copy</p>"
        await db_session.flush()
        body = await confirm(
            client, headers, productIds=[str(product.id)], idempotencyKey="snapshot-queue"
        )

        await run_task(monkeypatch, application_id=body["id"], tenant_id=tenant_id)

        await db_session.refresh(product)
        assert product.supplier_title == "Supplier original"
        assert product.supplier_description == "<p>Supplier copy</p>"
        assert product.cost_price_min == Decimal("10.0000")


class TestClaimAndDelivery:
    async def test_two_workers_cannot_both_claim_one_run(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The `pending -> running` transition is the lock."""
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")
        body = await confirm(
            client, headers, productIds=[str(product.id)], idempotencyKey="claim-race"
        )

        # The HTTP request that created this cleared tenant context on its
        # way out; a worker binds its own from the row, and here the test is
        # standing in for that worker.
        set_tenant_id(tenant_id)
        service = RuleApplicationService(db_session)
        first = await service.claim(uuid.UUID(body["id"]), task_id="task-a")
        second = await service.claim(uuid.UUID(body["id"]), task_id="task-b")

        assert first is ClaimResult.CLAIMED
        assert second is ClaimResult.ALREADY_RUNNING

    async def test_the_same_task_id_resumes_rather_than_restarts(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")
        body = await confirm(client, headers, productIds=[str(product.id)], idempotencyKey="resume")

        set_tenant_id(tenant_id)
        service = RuleApplicationService(db_session)
        assert await service.claim(uuid.UUID(body["id"]), task_id="task-a") is ClaimResult.CLAIMED
        again = await service.claim(uuid.UUID(body["id"]), task_id="task-a")

        assert again is ClaimResult.RESUMED

    async def test_a_duplicate_delivery_executes_once(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """At-least-once delivery is the contract; twice must not double-write."""
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")
        body = await confirm(
            client, headers, productIds=[str(product.id)], idempotencyKey="delivered-twice"
        )

        first = await run_task(monkeypatch, application_id=body["id"], tenant_id=tenant_id)
        after_first = await item_count(db_session, body["id"])
        second = await run_task(monkeypatch, application_id=body["id"], tenant_id=tenant_id)

        assert first["status"] == "completed"
        assert second["status"] == ClaimResult.NOT_CLAIMABLE.value
        assert second["batches"] == 0
        assert await item_count(db_session, body["id"]) == after_first
        await db_session.refresh(product)
        assert product.sell_price == Decimal("21.0000")

    async def test_a_run_another_worker_holds_is_left_alone(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")
        body = await confirm(client, headers, productIds=[str(product.id)], idempotencyKey="held")
        record = await row(db_session, body["id"])
        record.status = ApplicationStatus.RUNNING
        record.claimed_by_task_id = "someone-else"
        await db_session.flush()

        result = await run_task(monkeypatch, application_id=body["id"], tenant_id=tenant_id)

        assert result["status"] == ClaimResult.ALREADY_RUNNING.value
        assert await item_count(db_session, body["id"]) == 0
        await db_session.refresh(product)
        assert product.sell_price == Decimal("30.0000")


class TestRetryAndFailure:
    async def test_a_retry_resumes_without_duplicating_outcomes(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A transient failure mid-run, then the same task delivered again.

        The batch that failed rolled back with its cursor, so the retry redoes
        exactly that batch and nothing else -- no product is priced twice and
        no result row is written twice.
        """
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        drafts = [await seed_draft(db_session, tenant_id, sell_price="30.00") for _ in range(6)]
        monkeypatch.setattr(service_module, "APPLICATION_BATCH_SIZE", 2)
        body = await confirm(
            client,
            headers,
            productIds=[str(d.id) for d in drafts],
            idempotencyKey="transient",
        )
        task_id = str(uuid.uuid4())

        calls = {"n": 0}
        real_batch = RuleApplicationService.run_next_batch

        async def flaky(self: RuleApplicationService, application_id: uuid.UUID) -> bool:
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("broker hiccup")
            return await real_batch(self, application_id)

        monkeypatch.setattr(RuleApplicationService, "run_next_batch", flaky)
        with pytest.raises(Exception, match="broker hiccup"):
            await run_task(
                monkeypatch,
                application_id=body["id"],
                task_id=task_id,
                tenant_id=tenant_id,
            )

        partial = await row(db_session, body["id"])
        assert partial.status is ApplicationStatus.RUNNING
        assert partial.processed_count == 2, "only the committed batch counts"
        assert await item_count(db_session, body["id"]) == 2

        monkeypatch.setattr(RuleApplicationService, "run_next_batch", real_batch)
        result = await run_task(
            monkeypatch, application_id=body["id"], task_id=task_id, tenant_id=tenant_id
        )

        assert result["status"] == "completed"
        assert await item_count(db_session, body["id"]) == 6, "one row per draft, not more"
        assert result["applied"] == 6
        for draft in drafts:
            await db_session.refresh(draft)
            assert draft.sell_price == Decimal("21.0000")

    async def test_a_retry_writes_no_extra_price_change_rows(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """This workflow records its history as application items, not as
        ``PriceChange`` rows -- so a retry cannot add any, and the count is
        asserted rather than assumed."""
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")
        body = await confirm(
            client, headers, productIds=[str(product.id)], idempotencyKey="no-price-change"
        )
        task_id = str(uuid.uuid4())

        await run_task(monkeypatch, application_id=body["id"], task_id=task_id, tenant_id=tenant_id)
        await run_task(monkeypatch, application_id=body["id"], task_id=task_id, tenant_id=tenant_id)

        changes = (
            await db_session.execute(select(func.count()).select_from(PriceChange))
        ).scalar_one()
        assert changes == 0
        assert await item_count(db_session, body["id"]) == 1

    async def test_an_exhausted_retry_records_failed_and_keeps_committed_work(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        drafts = [await seed_draft(db_session, tenant_id, sell_price="30.00") for _ in range(4)]
        monkeypatch.setattr(service_module, "APPLICATION_BATCH_SIZE", 2)
        body = await confirm(
            client,
            headers,
            productIds=[str(d.id) for d in drafts],
            idempotencyKey="exhausted",
        )

        calls = {"n": 0}
        real_batch = RuleApplicationService.run_next_batch

        async def flaky(self: RuleApplicationService, application_id: uuid.UUID) -> bool:
            calls["n"] += 1
            if calls["n"] >= 2:
                raise RuntimeError("still broken")
            return await real_batch(self, application_id)

        monkeypatch.setattr(RuleApplicationService, "run_next_batch", flaky)

        result = await run_task(
            monkeypatch,
            application_id=body["id"],
            tenant_id=tenant_id,
            retries=pricing_tasks.apply_rules_to_drafts.max_retries,
        )

        assert result["status"] == "failed"
        record = await row(db_session, body["id"])
        assert record.status is ApplicationStatus.FAILED
        assert record.failure_reason is not None and "still broken" in record.failure_reason
        # The first batch really did reprice two drafts. Reporting zero would
        # send the merchant looking for changes the catalogue already has.
        assert record.applied_count == 2
        assert await item_count(db_session, body["id"]) == 2

    async def test_a_broker_failure_leaves_an_explicit_state(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        queue: EnqueueRecorder,
    ) -> None:
        """`pending` forever is indistinguishable from "queued behind a busy
        worker"; the merchant has to be able to tell nothing will happen."""
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")
        queue.fail_with = ConnectionRefusedError("broker down")

        body = await confirm(
            client, headers, productIds=[str(product.id)], idempotencyKey="no-broker"
        )

        record = await row(db_session, body["id"])
        assert record.status is ApplicationStatus.FAILED
        assert record.failure_reason is not None
        assert "Could not be queued" in record.failure_reason
        assert record.enqueued_at is None
        await db_session.refresh(product)
        assert product.sell_price == Decimal("30.0000"), "no price was touched"

    async def test_a_successful_hand_off_records_when_it_was_queued(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")

        body = await confirm(
            client, headers, productIds=[str(product.id)], idempotencyKey="queued-at"
        )

        record = await row(db_session, body["id"])
        assert record.status is ApplicationStatus.PENDING
        assert record.enqueued_at is not None


class TestCancellation:
    async def test_a_pending_application_can_be_cancelled(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")
        body = await confirm(
            client, headers, productIds=[str(product.id)], idempotencyKey="cancel-me"
        )

        response = await client.post(f"{BASE}/applications/{body['id']}/cancel", headers=headers)

        assert response.status_code == 200, response.text
        assert response.json()["status"] == "cancelled"

    async def test_a_delayed_task_does_not_revive_a_cancelled_run(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The message is already on the broker when the merchant cancels."""
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")
        body = await confirm(
            client, headers, productIds=[str(product.id)], idempotencyKey="cancel-race"
        )
        await client.post(f"{BASE}/applications/{body['id']}/cancel", headers=headers)

        result = await run_task(monkeypatch, application_id=body["id"], tenant_id=tenant_id)

        assert result["status"] == ClaimResult.NOT_CLAIMABLE.value
        assert await item_count(db_session, body["id"]) == 0
        await db_session.refresh(product)
        assert product.sell_price == Decimal("30.0000")

    async def test_cancelling_a_running_run_stops_it_at_a_batch_boundary(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The documented safe policy: cooperative, never a mid-transaction kill.

        Drafts already repriced by committed batches keep their prices -- they
        are real writes with real result rows, and quietly reverting them would
        be a second unreviewed reprice.
        """
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        drafts = [await seed_draft(db_session, tenant_id, sell_price="30.00") for _ in range(6)]
        monkeypatch.setattr(service_module, "APPLICATION_BATCH_SIZE", 2)
        body = await confirm(
            client,
            headers,
            productIds=[str(d.id) for d in drafts],
            idempotencyKey="stop-midway",
        )

        real_batch = RuleApplicationService.run_next_batch
        cancelled = {"done": False}

        async def cancel_after_first(
            self: RuleApplicationService, application_id: uuid.UUID
        ) -> bool:
            more = await real_batch(self, application_id)
            if not cancelled["done"]:
                cancelled["done"] = True
                await client.post(f"{BASE}/applications/{body['id']}/cancel", headers=headers)
                # That request cleared tenant context on its way out. A real
                # cancellation arrives on a different connection entirely; here
                # it shares one, so the worker's context is put back by hand.
                set_tenant_id(tenant_id)
            return more

        monkeypatch.setattr(RuleApplicationService, "run_next_batch", cancel_after_first)
        result = await run_task(monkeypatch, application_id=body["id"], tenant_id=tenant_id)

        assert result["status"] == "cancelled"
        record = await row(db_session, body["id"])
        assert record.status is ApplicationStatus.CANCELLED
        assert record.processed_count < len(drafts), "it stopped early"
        assert record.applied_count == 2, "the committed batch was kept"
        assert record.failure_reason is not None
        assert "Cancelled while running" in record.failure_reason

    async def test_cancellation_is_idempotent(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")
        body = await confirm(
            client, headers, productIds=[str(product.id)], idempotencyKey="cancel-twice"
        )

        first = await client.post(f"{BASE}/applications/{body['id']}/cancel", headers=headers)
        second = await client.post(f"{BASE}/applications/{body['id']}/cancel", headers=headers)

        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json()["status"] == "cancelled"

    async def test_a_failed_application_cannot_be_cancelled(
        self, client: AsyncClient, db_session: AsyncSession, queue: EnqueueRecorder
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")
        queue.fail_with = ConnectionRefusedError("broker down")
        body = await confirm(
            client, headers, productIds=[str(product.id)], idempotencyKey="failed-cancel"
        )

        response = await client.post(f"{BASE}/applications/{body['id']}/cancel", headers=headers)

        assert response.status_code == 409, response.text

    async def test_a_viewer_cannot_cancel(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")
        body = await confirm(
            client, headers, productIds=[str(product.id)], idempotencyKey="viewer-cancel"
        )

        response = await client.post(
            f"{BASE}/applications/{body['id']}/cancel", headers=viewer_header(tenant_id)
        )

        assert response.status_code == 403

    async def test_another_tenant_cancelling_gets_a_non_enumerating_404(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """404, not 403 -- a 403 would confirm the application exists."""
        owner, owner_tenant = await seed_tenant(client)
        await create_rule(client, owner)
        product = await seed_draft(db_session, owner_tenant, sell_price="30.00")
        body = await confirm(
            client, owner, productIds=[str(product.id)], idempotencyKey="private-cancel"
        )

        intruder, _ = await seed_tenant(client)
        response = await client.post(f"{BASE}/applications/{body['id']}/cancel", headers=intruder)

        assert response.status_code == 404


class TestBatchScalability:
    async def test_a_large_selection_runs_in_bounded_batches(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """More drafts than one batch holds; every one still gets a result."""
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        count = APPLICATION_BATCH_SIZE * 2 + 3
        drafts = [
            await seed_draft(db_session, tenant_id, sell_price="30.00", title=f"Draft {i}")
            for i in range(count)
        ]
        body = await confirm(
            client,
            headers,
            productIds=[str(d.id) for d in drafts],
            idempotencyKey="big-run",
        )

        result = await run_task(monkeypatch, application_id=body["id"], tenant_id=tenant_id)

        assert result["batches"] == 3, "50 + 50 + 3, not one long transaction"
        assert result["status"] == "completed"
        assert result["applied"] == count
        record = await row(db_session, body["id"])
        assert record.processed_count == count
        assert record.total_count == count
        assert await item_count(db_session, body["id"]) == count

    async def test_progress_advances_batch_by_batch(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        drafts = [await seed_draft(db_session, tenant_id, sell_price="30.00") for _ in range(6)]
        monkeypatch.setattr(service_module, "APPLICATION_BATCH_SIZE", 2)
        body = await confirm(
            client,
            headers,
            productIds=[str(d.id) for d in drafts],
            idempotencyKey="progress",
        )

        seen: list[tuple[int, int]] = []
        real_batch = RuleApplicationService.run_next_batch

        async def observing(self: RuleApplicationService, application_id: uuid.UUID) -> bool:
            more = await real_batch(self, application_id)
            record = await self.get(application_id)
            seen.append((record.processed_count, record.applied_count))
            return more

        monkeypatch.setattr(RuleApplicationService, "run_next_batch", observing)
        await run_task(monkeypatch, application_id=body["id"], tenant_id=tenant_id)

        assert seen == [(2, 2), (4, 4), (6, 6)]

    async def test_the_selection_snapshot_is_durable(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The worker gets one id; what was confirmed has to survive on the row."""
        headers, tenant_id = await seed_tenant(client)
        rule = await create_rule(client, headers)
        drafts = [await seed_draft(db_session, tenant_id) for _ in range(3)]
        body = await confirm(
            client,
            headers,
            productIds=[str(d.id) for d in drafts],
            idempotencyKey="durable",
            expectedRuleId=rule["id"],
            expectedRuleVersion=1,
        )

        record = await row(db_session, body["id"])

        assert record.selection["productIds"] == [str(d.id) for d in drafts]
        assert str(record.pricing_rule_id) == rule["id"]
        assert record.pricing_rule_version == 1

    async def test_a_duplicated_id_in_the_selection_is_collapsed(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The run walks the stored list by index, so a repeat would otherwise
        be processed twice and collide on its own uniqueness constraint."""
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")
        body = await confirm(
            client,
            headers,
            productIds=[str(product.id), str(product.id)],
            idempotencyKey="duplicated",
        )

        assert body["totalCount"] == 1
        result = await run_task(monkeypatch, application_id=body["id"], tenant_id=tenant_id)

        assert result["status"] == "completed"
        assert await item_count(db_session, body["id"]) == 1

    async def test_the_result_history_survives_for_audit(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Including the drafts nothing happened to -- "why is this one still
        at the old price" is the question merchants actually ask."""
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        applied = await seed_draft(db_session, tenant_id, sell_price="30.00")
        unchanged = await seed_draft(db_session, tenant_id, sell_price="21.00")
        held = await seed_draft(db_session, tenant_id, cost=None)
        body = await confirm(
            client,
            headers,
            productIds=[str(applied.id), str(unchanged.id), str(held.id)],
            idempotencyKey="audit",
        )

        await run_task(monkeypatch, application_id=body["id"], tenant_id=tenant_id)
        results = await read(client, headers, body["id"])

        outcomes = {i["productId"]: i["outcome"] for i in results["items"]}
        assert outcomes[str(applied.id)] == "applied"
        assert outcomes[str(unchanged.id)] == "skipped"
        assert outcomes[str(held.id)] == "needs_review"
        held_item = next(i for i in results["items"] if i["productId"] == str(held.id))
        assert "supplier_cost_unknown" in held_item["reviewReasons"]


class TestDispatchHandOff:
    async def test_the_hand_off_marks_the_run_enqueued(
        self, client: AsyncClient, db_session: AsyncSession, queue: EnqueueRecorder
    ) -> None:
        """Exercised directly, as the API schedules it after its commit."""
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")
        set_tenant_id(tenant_id)
        service = RuleApplicationService(db_session)
        application = await service.create(
            ApplyRequest(product_ids=(product.id,), idempotency_key="direct-dispatch"),
            actor_id=None,
        )
        await db_session.flush()
        queue.calls.clear()

        queued = await run_dispatch(application.id, tenant_id)

        assert queued is True
        assert queue.application_ids == [str(application.id)]
        record = await row(db_session, str(application.id))
        assert record.enqueued_at is not None
        assert record.status is ApplicationStatus.PENDING

    async def test_the_hand_off_reports_a_broker_failure(
        self, client: AsyncClient, db_session: AsyncSession, queue: EnqueueRecorder
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")
        set_tenant_id(tenant_id)
        service = RuleApplicationService(db_session)
        application = await service.create(
            ApplyRequest(product_ids=(product.id,), idempotency_key="dispatch-fails"),
            actor_id=None,
        )
        await db_session.flush()
        queue.fail_with = ConnectionRefusedError("broker down")

        queued = await run_dispatch(application.id, tenant_id)

        assert queued is False
        record = await row(db_session, str(application.id))
        assert record.status is ApplicationStatus.FAILED


class TestQueuedAccessControl:
    async def test_a_viewer_cannot_confirm_an_application(
        self, client: AsyncClient, db_session: AsyncSession, queue: EnqueueRecorder
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id)

        response = await client.post(
            APPLY,
            json={"productIds": [str(product.id)], "idempotencyKey": "viewer-confirm"},
            headers=viewer_header(tenant_id),
        )

        assert response.status_code == 403
        assert queue.calls == []

    async def test_a_viewer_may_watch_progress(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")
        body = await confirm(client, headers, productIds=[str(product.id)], idempotencyKey="watch")
        await run_task(monkeypatch, application_id=body["id"], tenant_id=tenant_id)

        response = await client.get(
            f"{BASE}/applications/{body['id']}", headers=viewer_header(tenant_id)
        )

        assert response.status_code == 200
        assert response.json()["status"] == "completed"

    async def test_another_tenants_run_is_not_exposed(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        owner, owner_tenant = await seed_tenant(client)
        await create_rule(client, owner)
        product = await seed_draft(db_session, owner_tenant, sell_price="30.00")
        body = await confirm(client, owner, productIds=[str(product.id)], idempotencyKey="hidden")

        intruder, _ = await seed_tenant(client)
        response = await client.get(f"{BASE}/applications/{body['id']}", headers=intruder)

        assert response.status_code == 404

    async def test_another_tenants_drafts_are_not_repriced(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A foreign id in the selection is recorded and skipped, not applied."""
        owner, owner_tenant = await seed_tenant(client)
        await create_rule(client, owner)
        mine = await seed_draft(db_session, owner_tenant, sell_price="30.00")

        _, other_tenant = await seed_tenant(client)
        theirs = await seed_draft(db_session, other_tenant, sell_price="30.00")

        body = await confirm(
            client,
            owner,
            productIds=[str(mine.id), str(theirs.id)],
            idempotencyKey="cross-tenant",
        )
        await run_task(monkeypatch, application_id=body["id"], tenant_id=owner_tenant)

        await db_session.refresh(mine)
        await db_session.refresh(theirs)
        assert mine.sell_price == Decimal("21.0000")
        assert theirs.sell_price == Decimal("30.0000"), "another tenant's draft is untouched"
        results = await read(client, owner, body["id"])
        failed = [i for i in results["items"] if i["outcome"] == "failed"]
        assert len(failed) == 1
        assert failed[0]["productId"] is None, "no reference to a foreign row is invented"


class TestUnchangedProductsAreSafe:
    async def test_a_draft_outside_the_selection_is_never_touched(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        selected = await seed_draft(db_session, tenant_id, sell_price="30.00")
        bystander = await seed_draft(db_session, tenant_id, sell_price="99.00")
        body = await confirm(
            client, headers, productIds=[str(selected.id)], idempotencyKey="scoped"
        )

        await run_task(monkeypatch, application_id=body["id"], tenant_id=tenant_id)

        await db_session.refresh(bystander)
        assert bystander.sell_price == Decimal("99.0000")
        assert bystander.applied_pricing_rule_id is None

    async def test_the_worker_writes_no_product_rows_it_did_not_select(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        selected = await seed_draft(db_session, tenant_id, sell_price="30.00")
        for _ in range(3):
            await seed_draft(db_session, tenant_id, sell_price="99.00")
        body = await confirm(
            client, headers, productIds=[str(selected.id)], idempotencyKey="narrow"
        )

        await run_task(monkeypatch, application_id=body["id"], tenant_id=tenant_id)

        stamped = (
            await db_session.execute(
                select(func.count())
                .select_from(Product)
                .where(Product.tenant_id == tenant_id)
                .where(Product.pricing_calculated_at.is_not(None))
            )
        ).scalar_one()
        assert stamped == 1
