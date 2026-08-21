"""M3A-H1 — cancelling a bulk application is atomic.

``cancel()`` was a read-then-write: a plain ``SELECT``, a terminal-state guard
evaluated on that read, then a flush. Nothing held the row in between, and
there is no version column, so the flush emitted ``UPDATE … WHERE id = ?`` and
overwrote whatever had landed meanwhile. A cancellation racing a worker's
``finalize`` therefore turned a **completed** run into a cancelled one — the
exact outcome the guard exists to refuse.

Prices and committed result rows were never at risk; the damage was to the
record of what happened, which is the only thing a merchant has afterwards to
tell "we stopped it" from "it finished".

The race tests here use two real connections and wait on
``pg_blocking_pids()`` rather than on a sleep, so "the cancellation was
genuinely blocked by the worker's lock" is asserted rather than assumed.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import set_tenant_id
from app.core.exceptions import ConflictError
from app.models.rule_application import ApplicationStatus, RuleApplication
from app.services import rule_application as service_module
from app.services.rule_application import BatchOutcome, RuleApplicationService
from tests.integration.rule_application_harness import EnqueueRecorder, bind_queue
from tests.integration.rule_application_live import (
    backend_pid,
    count_items,
    item_targets,
    live_application,
    prices_now,
    read_application,
    wait_until_blocked,
)
from tests.integration.test_rule_application import (
    APPLY,
    BASE,
    create_rule,
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
    return dict(response.json())


def cancel_url(application_id: str) -> str:
    return f"{BASE}/applications/{application_id}/cancel"


async def row(db_session: AsyncSession, application_id: str) -> RuleApplication:
    return (
        await db_session.execute(
            sa.select(RuleApplication)
            .where(RuleApplication.id == uuid.UUID(application_id))
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def force_status(
    db_session: AsyncSession, application_id: str, status: ApplicationStatus
) -> RuleApplication:
    """Put a run in a terminal state a request cannot otherwise produce."""
    record = await row(db_session, application_id)
    record.status = status
    await db_session.flush()
    return record


class TestTerminalStateSemantics:
    async def test_a_pending_application_is_cancelled_outright(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Nothing was written, so nothing has to be preserved -- and the
        claim will refuse the message when it finally arrives."""
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")
        body = await confirm(
            client, headers, productIds=[str(product.id)], idempotencyKey="cancel-pending"
        )

        response = await client.post(cancel_url(body["id"]), headers=headers)

        assert response.status_code == 200, response.text
        assert response.json()["status"] == ApplicationStatus.CANCELLED.value
        set_tenant_id(tenant_id)
        record = await row(db_session, body["id"])
        assert record.status is ApplicationStatus.CANCELLED
        assert record.failure_reason is None, "nothing ran, so there is nothing to explain"

        # A delivery arriving afterwards cannot revive it.
        service = RuleApplicationService(db_session)
        claim = await service.claim(uuid.UUID(body["id"]), task_id="late-delivery")
        assert claim.lease is None

    async def test_a_running_application_stops_cooperatively(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        drafts = [await seed_draft(db_session, tenant_id, sell_price="30.00") for _ in range(4)]
        monkeypatch.setattr(service_module, "APPLICATION_BATCH_SIZE", 2)
        body = await confirm(
            client,
            headers,
            productIds=[str(d.id) for d in drafts],
            idempotencyKey="cancel-running",
        )
        set_tenant_id(tenant_id)
        service = RuleApplicationService(db_session)
        claim = await service.claim(uuid.UUID(body["id"]), task_id="worker-a")
        assert claim.lease is not None
        assert await service.run_next_batch(uuid.UUID(body["id"]), lease=claim.lease) is (
            BatchOutcome.MORE
        )

        response = await client.post(cancel_url(body["id"]), headers=headers)
        assert response.status_code == 200, response.text
        set_tenant_id(tenant_id)

        # The worker notices at its next boundary rather than being killed.
        assert await service.run_next_batch(uuid.UUID(body["id"]), lease=claim.lease) is (
            BatchOutcome.CANCELLED
        )
        closed = await service.finalize(uuid.UUID(body["id"]), lease=claim.lease)
        assert closed is not None
        assert closed.status is ApplicationStatus.CANCELLED
        assert closed.applied_count == 2, "the committed batch was kept"
        assert closed.failure_reason is not None
        assert "Cancelled while running" in closed.failure_reason

    async def test_cancelling_twice_changes_nothing_the_second_time(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")
        body = await confirm(
            client, headers, productIds=[str(product.id)], idempotencyKey="cancel-twice"
        )

        first = await client.post(cancel_url(body["id"]), headers=headers)
        assert first.status_code == 200, first.text
        set_tenant_id(tenant_id)
        after_first = await row(db_session, body["id"])
        finished_at, reason = after_first.finished_at, after_first.failure_reason
        items_after_first = (
            await db_session.execute(
                sa.text("SELECT count(*) FROM rule_application_items WHERE application_id = :a"),
                {"a": body["id"]},
            )
        ).scalar_one()

        second = await client.post(cancel_url(body["id"]), headers=headers)

        assert second.status_code == 200, second.text
        assert second.json()["id"] == first.json()["id"]
        set_tenant_id(tenant_id)
        after_second = await row(db_session, body["id"])
        assert after_second.finished_at == finished_at, "finished_at was rewritten"
        assert after_second.failure_reason == reason
        assert (
            await db_session.execute(
                sa.text("SELECT count(*) FROM rule_application_items WHERE application_id = :a"),
                {"a": body["id"]},
            )
        ).scalar_one() == items_after_first, "a repeat cancellation wrote audit rows"

    @pytest.mark.parametrize(
        "status",
        [ApplicationStatus.COMPLETED, ApplicationStatus.PARTIAL, ApplicationStatus.FAILED],
    )
    async def test_a_finished_application_cannot_be_cancelled(
        self, client: AsyncClient, db_session: AsyncSession, status: ApplicationStatus
    ) -> None:
        """Reporting a finished run as cancelled would misrepresent the
        catalogue, so it is refused rather than accepted quietly."""
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")
        body = await confirm(
            client,
            headers,
            productIds=[str(product.id)],
            idempotencyKey=f"cancel-{status.value}",
        )
        set_tenant_id(tenant_id)
        record = await force_status(db_session, body["id"], status)
        finished_before = record.finished_at

        response = await client.post(cancel_url(body["id"]), headers=headers)

        assert response.status_code == 409, response.text
        payload = response.json()
        assert payload["code"] == "conflict"
        assert status.value in payload["message"]
        # The envelope must not leak lock or constraint detail.
        assert payload["details"] == []
        assert "lock" not in payload["message"].lower()
        set_tenant_id(tenant_id)
        after = await row(db_session, body["id"])
        assert after.status is status
        assert after.finished_at == finished_before


class TestPermissionsAndTenancy:
    async def test_a_viewer_cannot_cancel(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")
        body = await confirm(
            client, headers, productIds=[str(product.id)], idempotencyKey="viewer-cancel"
        )

        response = await client.post(cancel_url(body["id"]), headers=viewer_header(tenant_id))

        assert response.status_code == 403, response.text
        set_tenant_id(tenant_id)
        assert (await row(db_session, body["id"])).status is ApplicationStatus.PENDING

    async def test_another_tenants_application_is_not_found(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """404, never 403 -- a 403 would confirm the identifier exists."""
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")
        body = await confirm(
            client, headers, productIds=[str(product.id)], idempotencyKey="foreign-cancel"
        )
        intruder_headers, _ = await seed_tenant(client)

        response = await client.post(cancel_url(body["id"]), headers=intruder_headers)

        assert response.status_code == 404, response.text
        assert body["id"] not in response.text, "the response echoed the identifier back"
        set_tenant_id(tenant_id)
        assert (await row(db_session, body["id"])).status is ApplicationStatus.PENDING


class TestCancellationPreservesWork:
    async def test_committed_prices_items_and_progress_all_survive(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cancelling stops the run; it never unwinds what already landed."""
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        drafts = [await seed_draft(db_session, tenant_id, sell_price="30.00") for _ in range(4)]
        monkeypatch.setattr(service_module, "APPLICATION_BATCH_SIZE", 2)
        body = await confirm(
            client,
            headers,
            productIds=[str(d.id) for d in drafts],
            idempotencyKey="cancel-preserves",
        )
        set_tenant_id(tenant_id)
        service = RuleApplicationService(db_session)
        claim = await service.claim(uuid.UUID(body["id"]), task_id="worker-a")
        assert claim.lease is not None
        await service.run_next_batch(uuid.UUID(body["id"]), lease=claim.lease)

        before = await row(db_session, body["id"])
        processed_before, applied_before = before.processed_count, before.applied_count
        items_before = (
            await db_session.execute(
                sa.text("SELECT count(*) FROM rule_application_items WHERE application_id = :a"),
                {"a": body["id"]},
            )
        ).scalar_one()
        repriced = [d for d in drafts if d.sell_price == Decimal("21.0000")]
        assert repriced, "the first batch really did write prices"
        priced_before = {d.id: d.sell_price for d in drafts}

        response = await client.post(cancel_url(body["id"]), headers=headers)
        assert response.status_code == 200, response.text
        set_tenant_id(tenant_id)

        after = await row(db_session, body["id"])
        assert after.processed_count == processed_before, "the cursor was reset"
        assert after.applied_count == applied_before
        assert (
            await db_session.execute(
                sa.text("SELECT count(*) FROM rule_application_items WHERE application_id = :a"),
                {"a": body["id"]},
            )
        ).scalar_one() == items_before, "result rows were removed"
        for draft in drafts:
            await db_session.refresh(draft)
            assert draft.sell_price == priced_before[draft.id], "a price changed on cancellation"
        assert after.lease_token == claim.lease.token, "cancellation invented or cleared a lease"


class TestCancellationRacesAcrossConnections:
    """Two real connections, real row locks, and PostgreSQL as the referee.

    Nothing here waits for a duration. Each rendezvous is a state the database
    reports -- a backend blocked on a lock -- so the interleaving is forced
    rather than hoped for.
    """

    async def test_race_a_finalize_wins_and_cancellation_is_refused(self) -> None:
        """The defect, stated as behaviour.

        The worker locks the row and writes COMPLETED. A cancellation arriving
        mid-flight must not be allowed to evaluate its guard against the row
        as it was *before* that write. Under the old read-then-write it did
        exactly that, passed the guard on a stale RUNNING, waited on the lock
        and then overwrote COMPLETED with CANCELLED.
        """
        async with live_application(drafts=2, idempotency_key="race-a") as live:
            factory = live.session_factory
            application_id = live.application_id
            set_tenant_id(live.tenant_id)

            async with factory() as owner:
                claim = await RuleApplicationService(owner).claim(
                    application_id, task_id="worker-a"
                )
                assert claim.lease is not None
                lease = claim.lease
                await owner.commit()

            finalize_locked = asyncio.Event()
            cancel_is_blocked = asyncio.Event()
            outcome: dict[str, Any] = {}
            pids: dict[str, int] = {}
            cancel_pid_known = asyncio.Event()

            async def worker() -> None:
                set_tenant_id(live.tenant_id)
                async with factory() as session:
                    service = RuleApplicationService(session)
                    while (
                        await service.run_next_batch(application_id, lease=lease)
                        is BatchOutcome.MORE
                    ):
                        pass
                    await session.commit()
                async with factory() as session:
                    # Locks the row and writes COMPLETED, then holds the
                    # transaction open until the cancellation is provably
                    # waiting on that lock.
                    finished = await RuleApplicationService(session).finalize(
                        application_id, lease=lease
                    )
                    assert finished is not None
                    outcome["worker_status"] = finished.status
                    outcome["worker_finished_at"] = finished.finished_at
                    outcome["worker_applied"] = finished.applied_count
                    finalize_locked.set()
                    await asyncio.wait_for(cancel_is_blocked.wait(), timeout=30)
                    await session.commit()

            async def canceller() -> None:
                set_tenant_id(live.tenant_id)
                async with factory() as session:
                    pids["cancel"] = await backend_pid(session)
                    cancel_pid_known.set()
                    await asyncio.wait_for(finalize_locked.wait(), timeout=30)
                    try:
                        result = await RuleApplicationService(session).cancel(application_id)
                        outcome["cancel"] = result.status
                        await session.commit()
                    except ConflictError as exc:
                        outcome["cancel"] = exc
                        await session.rollback()

            async def release_once_contended() -> None:
                """Let the worker commit only after the canceller is stuck.

                This is the whole proof: `wait_until_blocked` asks PostgreSQL
                whether the cancelling backend is waiting on somebody else's
                lock, and raises if it never is. No sleep decides the order.
                """
                await asyncio.wait_for(finalize_locked.wait(), timeout=30)
                await asyncio.wait_for(cancel_pid_known.wait(), timeout=30)
                outcome["blockers"] = await wait_until_blocked(
                    factory, pids["cancel"], what="cancellation"
                )
                cancel_is_blocked.set()

            await asyncio.gather(worker(), canceller(), release_once_contended())

            assert outcome["blockers"], "the cancellation never contended for the row lock"
            assert isinstance(outcome["cancel"], ConflictError), (
                f"cancellation should have been refused, got {outcome['cancel']!r}"
            )
            record = await read_application(factory, application_id)
            assert record.status is ApplicationStatus.COMPLETED, "a completed run was overwritten"
            assert record.finished_at == outcome["worker_finished_at"], "finished_at was rewritten"
            assert record.applied_count == outcome["worker_applied"]
            assert record.failure_reason is None, "a completed run gained a cancellation reason"
            assert len(await item_targets(factory, application_id)) == 2

    async def test_race_b_cancellation_wins_and_finalize_does_not_overwrite(self) -> None:
        """The other order. The worker still closes its run out honestly, but
        the terminal status stays the merchant's."""
        async with live_application(drafts=4, idempotency_key="race-b") as live:
            factory = live.session_factory
            application_id = live.application_id
            set_tenant_id(live.tenant_id)

            async with factory() as owner:
                service = RuleApplicationService(owner)
                claim = await service.claim(application_id, task_id="worker-a")
                assert claim.lease is not None
                lease = claim.lease
                await owner.commit()

            async with factory() as session:
                assert await RuleApplicationService(session).run_next_batch(
                    application_id, lease=lease
                ) in (BatchOutcome.MORE, BatchOutcome.DONE)
                await session.commit()

            async with factory() as session:
                cancelled = await RuleApplicationService(session).cancel(application_id)
                assert cancelled.status is ApplicationStatus.CANCELLED
                await session.commit()
            cancelled_row = await read_application(factory, application_id)
            finished_at = cancelled_row.finished_at
            items_before = await count_items(factory, application_id)

            async with factory() as session:
                closed = await RuleApplicationService(session).finalize(application_id, lease=lease)
                await session.commit()

            assert closed is not None
            assert closed.status is ApplicationStatus.CANCELLED, "finalize overwrote the status"
            after = await read_application(factory, application_id)
            assert after.status is ApplicationStatus.CANCELLED
            assert after.finished_at == finished_at, "finalize rewrote finished_at"
            assert after.applied_count == cancelled_row.applied_count
            assert await count_items(factory, application_id) == items_before

    async def test_race_c_two_cancellations_transition_once(self) -> None:
        """Both requests succeed for the caller; only one writes."""
        async with live_application(drafts=2, idempotency_key="race-c") as live:
            factory = live.session_factory
            application_id = live.application_id
            set_tenant_id(live.tenant_id)

            async with factory() as owner:
                claim = await RuleApplicationService(owner).claim(
                    application_id, task_id="worker-a"
                )
                assert claim.lease is not None
                await owner.commit()

            first_locked = asyncio.Event()
            second_is_blocked = asyncio.Event()
            results: dict[str, Any] = {}
            pids: dict[str, int] = {}
            second_pid_known = asyncio.Event()

            async def first() -> None:
                set_tenant_id(live.tenant_id)
                async with factory() as session:
                    result = await RuleApplicationService(session).cancel(application_id)
                    results["first"] = result.status
                    results["first_finished_at"] = result.finished_at
                    first_locked.set()
                    await asyncio.wait_for(second_is_blocked.wait(), timeout=30)
                    await session.commit()

            async def second() -> None:
                set_tenant_id(live.tenant_id)
                async with factory() as session:
                    pids["second"] = await backend_pid(session)
                    second_pid_known.set()
                    await asyncio.wait_for(first_locked.wait(), timeout=30)
                    result = await RuleApplicationService(session).cancel(application_id)
                    results["second"] = result.status
                    results["second_finished_at"] = result.finished_at
                    await session.commit()

            async def unblock_after_contention() -> None:
                await asyncio.wait_for(first_locked.wait(), timeout=30)
                await asyncio.wait_for(second_pid_known.wait(), timeout=30)
                await wait_until_blocked(factory, pids["second"], what="second cancellation")
                second_is_blocked.set()

            await asyncio.gather(first(), second(), unblock_after_contention())

            assert results["first"] is ApplicationStatus.CANCELLED
            assert results["second"] is ApplicationStatus.CANCELLED
            record = await read_application(factory, application_id)
            assert record.status is ApplicationStatus.CANCELLED
            assert record.finished_at == results["first_finished_at"], (
                "the second cancellation rewrote finished_at"
            )
            assert results["second_finished_at"] == results["first_finished_at"]
            targets = await item_targets(factory, application_id)
            assert len(targets) == len(set(targets)), "duplicate result rows appeared"


class TestCancellationLeavesTheCatalogueAlone:
    async def test_no_product_price_changes_when_a_pending_run_is_cancelled(self) -> None:
        async with live_application(drafts=3, idempotency_key="cancel-no-writes") as live:
            factory = live.session_factory
            set_tenant_id(live.tenant_id)
            before = await prices_now(factory, live.draft_ids)

            async with factory() as session:
                await RuleApplicationService(session).cancel(live.application_id)
                await session.commit()

            assert await prices_now(factory, live.draft_ids) == before
            assert await count_items(factory, live.application_id) == 0
            record = await read_application(factory, live.application_id)
            assert record.processed_count == 0
            assert record.lease_token is None, "cancellation invented a lease"
