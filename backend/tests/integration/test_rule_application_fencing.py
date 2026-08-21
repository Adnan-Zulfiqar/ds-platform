"""M3A acceptance fix — recovery actually recovers, and one worker owns a run.

Two defects sat behind these tests, both of which every earlier test passed
straight through:

* **Recovery published a message that could never do anything.** The
  reconciler cleared the owner but left the row ``running``, so the task it
  then queued saw ``running`` under someone else, reported
  ``already_running`` and processed nothing. A stuck run stayed stuck, and the
  logs said it had been requeued.
* **Nothing re-checked ownership between batches.** A worker claimed once and
  then looped, so a worker whose run had been handed to somebody else carried
  on writing prices, results and progress into it.

So these drive the *production* reconciler and the *production* task rather
than a test-local imitation of either, and every assertion is about the row
and the catalogue afterwards.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import set_tenant_id
from app.models.rule_application import (
    ApplicationStatus,
    RuleApplication,
    RuleApplicationItem,
)
from app.services import rule_application as service_module
from app.services.rule_application import (
    ApplicationLease,
    BatchOutcome,
    ClaimResult,
    LeaseObservation,
    RuleApplicationService,
)
from app.tasks import pricing as pricing_tasks
from tests.integration import rule_application_live as live_module
from tests.integration.rule_application_harness import (
    EnqueueRecorder,
    bind_queue,
    run_task,
)
from tests.integration.rule_application_live import (
    backend_pid,
    item_targets,
    live_application,
    prices_now,
    read_application,
    wait_until_blocked,
)
from tests.integration.test_rule_application import (
    APPLY,
    create_rule,
    seed_draft,
    seed_tenant,
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


async def row(db_session: AsyncSession, application_id: str | uuid.UUID) -> RuleApplication:
    found = (
        await db_session.execute(
            select_application(application_id).execution_options(populate_existing=True)
        )
    ).scalar_one()
    return found


def select_application(application_id: str | uuid.UUID) -> sa.Select[tuple[RuleApplication]]:
    identifier = (
        application_id if isinstance(application_id, uuid.UUID) else uuid.UUID(application_id)
    )
    return sa.select(RuleApplication).where(RuleApplication.id == identifier)


async def item_count(db_session: AsyncSession, application_id: str | uuid.UUID) -> int:
    identifier = (
        application_id if isinstance(application_id, uuid.UUID) else uuid.UUID(application_id)
    )
    return int(
        (
            await db_session.execute(
                sa.select(sa.func.count())
                .select_from(RuleApplicationItem)
                .where(RuleApplicationItem.application_id == identifier)
            )
        ).scalar_one()
    )


async def go_stale(
    db_session: AsyncSession,
    application_id: str,
    *,
    minutes_ago: int = 60,
    heartbeat: bool = True,
) -> RuleApplication:
    """The state a killed worker leaves behind: `running`, owned, silent."""
    record = await row(db_session, application_id)
    record.status = ApplicationStatus.RUNNING
    record.claimed_by_task_id = "worker-that-died"
    record.lease_token = uuid.uuid4()
    record.started_at = datetime.now(UTC) - timedelta(minutes=minutes_ago + 5)
    record.heartbeat_at = datetime.now(UTC) - timedelta(minutes=minutes_ago) if heartbeat else None
    await db_session.flush()
    return record


async def sweep_for(db_session: AsyncSession, application_id: str) -> LeaseObservation | None:
    """Run the production sweep and pick out one run, as the beat task does."""
    identifier = uuid.UUID(application_id)
    for observed in await pricing_tasks._stale_applications():
        if observed.application_id == identifier:
            return observed
    return None


class TestProductionRecoveryPath:
    """The exact sequence a stuck run goes through in production."""

    async def test_the_reconciler_hands_a_stale_run_to_a_new_worker_that_finishes_it(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        queue: EnqueueRecorder,
    ) -> None:
        """Sweep, reclaim, publish, and a *new* task id claims and completes.

        Against the previous implementation this stops dead at the claim: the
        reconciler left the row `running` with a NULL owner, so the task it
        published reported `already_running` and processed nothing. The run
        was never rescued, and the only sign was a log line saying it had
        been.
        """
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        drafts = [await seed_draft(db_session, tenant_id, sell_price="30.00") for _ in range(6)]
        monkeypatch.setattr(service_module, "APPLICATION_BATCH_SIZE", 2)
        body = await confirm(
            client,
            headers,
            productIds=[str(d.id) for d in drafts],
            idempotencyKey="production-recovery",
        )

        # Worker A takes the run, commits one batch and dies.
        real_batch = RuleApplicationService.run_next_batch
        calls = {"n": 0}

        async def dies_after_one(
            self: RuleApplicationService,
            application_id: uuid.UUID,
            *,
            lease: ApplicationLease,
        ) -> BatchOutcome:
            calls["n"] += 1
            if calls["n"] > 1:
                raise RuntimeError("worker killed")
            return await real_batch(self, application_id, lease=lease)

        monkeypatch.setattr(RuleApplicationService, "run_next_batch", dies_after_one)
        with pytest.raises(Exception, match="worker killed"):
            await run_task(
                monkeypatch,
                application_id=body["id"],
                task_id=str(uuid.uuid4()),
                tenant_id=tenant_id,
            )
        monkeypatch.setattr(RuleApplicationService, "run_next_batch", real_batch)

        crashed = await row(db_session, body["id"])
        assert crashed.processed_count == 2
        assert crashed.status is ApplicationStatus.RUNNING
        dead_lease = crashed.lease_token
        assert dead_lease is not None

        # Time passes and the beat sweep finds it.
        crashed.heartbeat_at = datetime.now(UTC) - service_module.STALE_AFTER - timedelta(minutes=1)
        await db_session.flush()
        set_tenant_id(tenant_id)
        observed = await sweep_for(db_session, body["id"])
        assert observed is not None, "the sweep must find the abandoned run"

        queue.calls.clear()
        outcome = await pricing_tasks._reconcile_one(observed)
        set_tenant_id(tenant_id)

        assert outcome == "requeued"
        reclaimed = await row(db_session, body["id"])
        assert reclaimed.status is ApplicationStatus.PENDING, "claimable by the message just sent"
        assert reclaimed.claimed_by_task_id is None
        assert reclaimed.lease_token is None
        assert reclaimed.recovery_count == 1
        assert reclaimed.processed_count == 2, "the durable cursor survives recovery"
        assert queue.application_ids == [body["id"]], "one message, carrying only the id"

        # A new delivery, with its own real task id -- nothing fabricated.
        rescuer = str(uuid.uuid4())
        result = await run_task(
            monkeypatch, application_id=body["id"], task_id=rescuer, tenant_id=tenant_id
        )

        assert result["status"] == "completed"
        assert result["batches"] == 2, "it resumed from the cursor rather than restarting"
        finished = await row(db_session, body["id"])
        assert finished.claimed_by_task_id == rescuer
        assert finished.lease_token is None, "released on completion"
        assert await item_count(db_session, body["id"]) == 6, "one row per draft, not seven"
        for draft in drafts:
            await db_session.refresh(draft)
            assert draft.sell_price == Decimal("21.0000")

    async def test_a_publish_failure_leaves_the_run_claimable_rather_than_orphaned(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        queue: EnqueueRecorder,
    ) -> None:
        """A broker outage delays recovery; it must not lose it.

        The run comes to rest `pending` with no `enqueued_at`, which is
        precisely the state the pending sweep republishes -- so the next beat
        finishes what this one could not.
        """
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")
        body = await confirm(
            client, headers, productIds=[str(product.id)], idempotencyKey="publish-fails"
        )
        await go_stale(db_session, body["id"])
        set_tenant_id(tenant_id)
        observed = await sweep_for(db_session, body["id"])
        assert observed is not None

        queue.fail_with = RuntimeError("broker unreachable")
        outcome = await pricing_tasks._reconcile_one(observed)
        queue.fail_with = None
        set_tenant_id(tenant_id)

        assert outcome == "recovered_unpublished"
        stranded = await row(db_session, body["id"])
        assert stranded.status is ApplicationStatus.PENDING
        assert stranded.enqueued_at is None, "the pending sweep's signal that it needs publishing"

        # And the pending sweep does republish it.
        stranded.created_at = datetime.now(UTC) - pricing_tasks.PENDING_GRACE - timedelta(minutes=1)
        await db_session.flush()
        pending = await pricing_tasks._unpublished_applications()
        set_tenant_id(tenant_id)
        assert uuid.UUID(body["id"]) in [identifier for identifier, _ in pending]

    async def test_two_racing_reconcilers_reclaim_once_and_publish_once(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        queue: EnqueueRecorder,
    ) -> None:
        """Duplicate beat executions must not multiply the work.

        Both see the same abandoned run and both act on the same observation.
        The conditional UPDATE is what decides: the second finds a row whose
        status and recovery count have already moved and takes nothing.
        """
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")
        body = await confirm(
            client, headers, productIds=[str(product.id)], idempotencyKey="double-beat"
        )
        await go_stale(db_session, body["id"])
        set_tenant_id(tenant_id)
        observed = await sweep_for(db_session, body["id"])
        assert observed is not None

        queue.calls.clear()
        first = await pricing_tasks._reconcile_one(observed)
        set_tenant_id(tenant_id)
        second = await pricing_tasks._reconcile_one(observed)
        set_tenant_id(tenant_id)

        assert sorted([first, second]) == ["healthy", "requeued"]
        record = await row(db_session, body["id"])
        assert record.recovery_count == 1, "incremented exactly once per recovery"
        assert len(queue.calls) == 1, "one message, not one per reconciler"


class TestStaleWorkerFencing:
    """The race the whole lease exists for."""

    async def test_a_worker_that_lost_its_run_writes_nothing(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A owns it, stalls, is reclaimed, B takes over, A wakes up.

        A must be rejected *before* any write. The per-item unique constraint
        is not the safety mechanism here and could not be: the product price
        and the `PriceChange` row are written before it, so by the time it
        fired the catalogue would already have been changed twice.
        """
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        drafts = [await seed_draft(db_session, tenant_id, sell_price="30.00") for _ in range(4)]
        monkeypatch.setattr(service_module, "APPLICATION_BATCH_SIZE", 2)
        body = await confirm(
            client,
            headers,
            productIds=[str(d.id) for d in drafts],
            idempotencyKey="fenced-out",
        )
        set_tenant_id(tenant_id)
        service = RuleApplicationService(db_session)

        # Worker A claims and commits its first batch.
        claim_a = await service.claim(uuid.UUID(body["id"]), task_id="worker-a")
        assert claim_a.lease is not None
        lease_a = claim_a.lease
        assert await service.run_next_batch(uuid.UUID(body["id"]), lease=lease_a) is (
            BatchOutcome.MORE
        )

        # A goes quiet; the reconciler returns the run to the queue.
        stalled = await row(db_session, body["id"])
        stalled.heartbeat_at = datetime.now(UTC) - service_module.STALE_AFTER - timedelta(minutes=1)
        await db_session.flush()
        observed = await service.observe(uuid.UUID(body["id"]))
        assert observed is not None
        assert await service.reclaim_stale(observed=observed) is True

        # Worker B claims the recovered run under its own task id.
        claim_b = await service.claim(uuid.UUID(body["id"]), task_id="worker-b")
        assert claim_b.result is ClaimResult.CLAIMED
        assert claim_b.lease is not None
        assert claim_b.lease.token != lease_a.token

        before = await row(db_session, body["id"])
        state = (
            before.processed_count,
            before.heartbeat_at,
            before.status,
            before.applied_count,
        )
        items_before = await item_count(db_session, body["id"])
        prices_before = {}
        for draft in drafts:
            await db_session.refresh(draft)
            prices_before[draft.id] = draft.sell_price

        # A wakes up and asks for its next batch.
        assert await service.run_next_batch(uuid.UUID(body["id"]), lease=lease_a) is (
            BatchOutcome.LOST
        )

        after = await row(db_session, body["id"])
        assert (
            after.processed_count,
            after.heartbeat_at,
            after.status,
            after.applied_count,
        ) == state, "no progress, heartbeat or status write from the stale worker"
        assert await item_count(db_session, body["id"]) == items_before, "no result rows"
        for draft in drafts:
            await db_session.refresh(draft)
            assert draft.sell_price == prices_before[draft.id], "no product was repriced"

        # A cannot close the run out either, in any direction.
        assert await service.finalize(uuid.UUID(body["id"]), lease=lease_a) is None
        assert await service.fail(uuid.UUID(body["id"]), "A gave up", lease=lease_a) is None
        assert (await row(db_session, body["id"])).status is ApplicationStatus.RUNNING

        # B finishes from the durable cursor, and nothing is priced twice.
        while (
            await service.run_next_batch(uuid.UUID(body["id"]), lease=claim_b.lease)
            is BatchOutcome.MORE
        ):
            pass
        finished = await service.finalize(uuid.UUID(body["id"]), lease=claim_b.lease)
        assert finished is not None
        assert finished.status is ApplicationStatus.COMPLETED
        assert await item_count(db_session, body["id"]) == 4, "one row per draft"

    async def test_a_stale_worker_raising_does_not_fail_the_new_owners_run(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The failure handler is owner-conditional, through the real task.

        Worker A loses the run and *then* raises with its retries exhausted,
        which is the ordinary consequence of being reclaimed mid-flight. Its
        generic handler must not stamp `failed` on the run B is holding.
        """
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        drafts = [await seed_draft(db_session, tenant_id, sell_price="30.00") for _ in range(4)]
        monkeypatch.setattr(service_module, "APPLICATION_BATCH_SIZE", 2)
        body = await confirm(
            client,
            headers,
            productIds=[str(d.id) for d in drafts],
            idempotencyKey="handler-fencing",
        )

        real_batch = RuleApplicationService.run_next_batch
        taken_over: dict[str, ApplicationLease | None] = {"lease": None}

        async def loses_the_run_then_raises(
            self: RuleApplicationService,
            application_id: uuid.UUID,
            *,
            lease: ApplicationLease,
        ) -> BatchOutcome:
            if taken_over["lease"] is None:
                # Between A's claim and its first batch, the run is reclaimed
                # and handed to B.
                record = await row(self.session, application_id)
                record.heartbeat_at = (
                    datetime.now(UTC) - service_module.STALE_AFTER - timedelta(minutes=1)
                )
                await self.session.flush()
                observed = await self.observe(application_id)
                assert observed is not None
                assert await self.reclaim_stale(observed=observed) is True
                claim_b = await self.claim(application_id, task_id="worker-b")
                assert claim_b.lease is not None
                taken_over["lease"] = claim_b.lease
                # In production the reconciler and worker B are separate
                # connections and their work is already durable when A falls
                # over. Here they share A's session, so it is committed by
                # hand -- otherwise A's rollback would undo the takeover and
                # the test would prove nothing.
                await self.session.commit()
            raise RuntimeError("worker A falls over")

        monkeypatch.setattr(RuleApplicationService, "run_next_batch", loses_the_run_then_raises)
        result = await run_task(
            monkeypatch,
            application_id=body["id"],
            task_id="worker-a",
            tenant_id=tenant_id,
            retries=pricing_tasks.apply_rules_to_drafts.max_retries,
        )
        monkeypatch.setattr(RuleApplicationService, "run_next_batch", real_batch)

        assert result["status"] == "superseded", (
            "A reported that it was replaced, not that it failed"
        )
        record = await row(db_session, body["id"])
        assert record.status is ApplicationStatus.RUNNING, "B's run is untouched"
        assert record.failure_reason is None
        assert record.finished_at is None
        assert record.lease_token == taken_over["lease"].token  # type: ignore[union-attr]

        # And B goes on to finish it.
        set_tenant_id(tenant_id)
        service = RuleApplicationService(db_session)
        lease_b = taken_over["lease"]
        assert lease_b is not None
        while (
            await service.run_next_batch(uuid.UUID(body["id"]), lease=lease_b) is BatchOutcome.MORE
        ):
            pass
        finished = await service.finalize(uuid.UUID(body["id"]), lease=lease_b)
        assert finished is not None
        assert finished.status is ApplicationStatus.COMPLETED

    async def test_a_worker_with_no_lease_records_no_failure(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """ "Never owned it" and "lost it" are the same case: write nothing."""
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")
        body = await confirm(
            client, headers, productIds=[str(product.id)], idempotencyKey="no-lease"
        )
        await go_stale(db_session, body["id"])

        await pricing_tasks._mark_failed(uuid.UUID(body["id"]), "unowned failure", None)
        set_tenant_id(tenant_id)

        record = await row(db_session, body["id"])
        assert record.status is ApplicationStatus.RUNNING
        assert record.failure_reason is None


class TestSlowButLiveWorker:
    async def test_a_worker_that_checks_in_keeps_its_run_and_can_still_write(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The heartbeat moves between the sweep and the reconciler's write.

        The reclaim is conditional on the value the sweep read, so it matches
        no row -- and, crucially, the worker's lease is still the one on the
        row, so it carries on working rather than being silently fenced.
        """
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        drafts = [await seed_draft(db_session, tenant_id, sell_price="30.00") for _ in range(4)]
        monkeypatch.setattr(service_module, "APPLICATION_BATCH_SIZE", 2)
        body = await confirm(
            client,
            headers,
            productIds=[str(d.id) for d in drafts],
            idempotencyKey="slow-but-live",
        )
        set_tenant_id(tenant_id)
        service = RuleApplicationService(db_session)
        claim = await service.claim(uuid.UUID(body["id"]), task_id="slow-worker")
        assert claim.lease is not None

        # It looks abandoned when the sweep reads it.
        record = await row(db_session, body["id"])
        record.heartbeat_at = datetime.now(UTC) - service_module.STALE_AFTER - timedelta(minutes=1)
        await db_session.flush()
        observed = await sweep_for(db_session, body["id"])
        set_tenant_id(tenant_id)
        assert observed is not None

        # Then it commits a batch, which moves the heartbeat.
        assert await service.run_next_batch(uuid.UUID(body["id"]), lease=claim.lease) is (
            BatchOutcome.MORE
        )

        assert await pricing_tasks._reconcile_one(observed) == "healthy"
        set_tenant_id(tenant_id)

        kept = await row(db_session, body["id"])
        assert kept.status is ApplicationStatus.RUNNING
        assert kept.lease_token == claim.lease.token, "nothing was taken from it"
        assert kept.recovery_count == 0

        # And it really can still write.
        assert await service.run_next_batch(uuid.UUID(body["id"]), lease=claim.lease) is (
            BatchOutcome.DONE
        )
        assert await item_count(db_session, body["id"]) == 4


class TestNullHeartbeatRecovery:
    """`heartbeat_at` arrived in 0027; rows abandoned before it carry NULL."""

    async def test_a_legacy_running_row_with_no_heartbeat_is_recoverable(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """`heartbeat_at < cutoff` is NULL for these -- never true -- so they
        were invisible to the sweep and would have sat `running` forever."""
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")
        body = await confirm(
            client, headers, productIds=[str(product.id)], idempotencyKey="legacy-null"
        )
        await go_stale(db_session, body["id"], heartbeat=False)
        set_tenant_id(tenant_id)

        observed = await sweep_for(db_session, body["id"])
        assert observed is not None, "an aged NULL-heartbeat run must be visible to the sweep"
        assert observed.heartbeat_at is None

        assert await RuleApplicationService(db_session).reclaim_stale(observed=observed) is True
        db_session.expire_all()
        assert (await row(db_session, body["id"])).status is ApplicationStatus.PENDING

    async def test_a_fresh_row_with_no_heartbeat_is_not_reclaimed(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The other half: a run that has only just started must be left alone
        even if its heartbeat has not landed yet. Age is what separates the
        two, and it comes from the row rather than from a guess."""
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")
        body = await confirm(
            client, headers, productIds=[str(product.id)], idempotencyKey="fresh-null"
        )
        record = await row(db_session, body["id"])
        record.status = ApplicationStatus.RUNNING
        record.claimed_by_task_id = "just-started"
        record.lease_token = uuid.uuid4()
        record.started_at = datetime.now(UTC)
        record.heartbeat_at = None
        await db_session.flush()
        set_tenant_id(tenant_id)

        assert await sweep_for(db_session, body["id"]) is None, "too young to be abandoned"

        service = RuleApplicationService(db_session)
        observed = await service.observe(uuid.UUID(body["id"]))
        assert observed is not None
        assert await service.reclaim_stale(observed=observed) is False
        db_session.expire_all()
        assert (await row(db_session, body["id"])).status is ApplicationStatus.RUNNING


class TestTerminalStatesAreNeverRecovered:
    @pytest.mark.parametrize(
        "status",
        [
            ApplicationStatus.CANCELLED,
            ApplicationStatus.COMPLETED,
            ApplicationStatus.PARTIAL,
            ApplicationStatus.FAILED,
        ],
    )
    async def test_a_finished_run_is_not_swept_or_reclaimed(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        status: ApplicationStatus,
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")
        body = await confirm(
            client,
            headers,
            productIds=[str(product.id)],
            idempotencyKey=f"terminal-{status.value}",
        )
        record = await go_stale(db_session, body["id"])
        set_tenant_id(tenant_id)
        observed = await sweep_for(db_session, body["id"])
        assert observed is not None, "it was stale before the status changed"

        record.status = status
        await db_session.flush()

        assert await sweep_for(db_session, body["id"]) is None
        service = RuleApplicationService(db_session)
        assert await service.reclaim_stale(observed=observed) is False
        assert await service.abandon_stale(observed=observed) is False
        db_session.expire_all()
        assert (await row(db_session, body["id"])).status is status

    async def test_a_cancelled_run_keeps_the_batches_that_committed(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Cancellation stops the run; it does not erase what already landed,
        and the owner is still the one that closes it out."""
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        drafts = [await seed_draft(db_session, tenant_id, sell_price="30.00") for _ in range(4)]
        monkeypatch.setattr(service_module, "APPLICATION_BATCH_SIZE", 2)
        body = await confirm(
            client,
            headers,
            productIds=[str(d.id) for d in drafts],
            idempotencyKey="cancel-keeps",
        )
        set_tenant_id(tenant_id)
        service = RuleApplicationService(db_session)
        claim = await service.claim(uuid.UUID(body["id"]), task_id="worker-a")
        assert claim.lease is not None
        assert await service.run_next_batch(uuid.UUID(body["id"]), lease=claim.lease) is (
            BatchOutcome.MORE
        )

        await service.cancel(uuid.UUID(body["id"]))

        assert await service.run_next_batch(uuid.UUID(body["id"]), lease=claim.lease) is (
            BatchOutcome.CANCELLED
        )
        closed = await service.finalize(uuid.UUID(body["id"]), lease=claim.lease)
        assert closed is not None
        assert closed.status is ApplicationStatus.CANCELLED
        assert closed.applied_count == 2, "the committed batch was kept"
        assert await item_count(db_session, body["id"]) == 2


class TestConcurrentOwnershipAcrossConnections:
    """The fence under a genuine two-connection race (M3A-H2).

    The shared ``db_session`` fixture wraps everything in one transaction, so
    two "workers" driven through it are really one and ``FOR UPDATE`` contends
    with nothing. That is fine for the ownership *logic* above, but it cannot
    show the lock doing its job, so this commits its own data on its own
    engine and drives two real connections against it.

    **Nothing here is decided by elapsed time.** The first version of this test
    slept 0.5 s and then accepted either reclaim outcome, which meant it could
    not fail on the property it existed to prove. Every rendezvous below is a
    state PostgreSQL reports — ``pg_blocking_pids()`` — so "the reconciler was
    unable to proceed" is asserted, and a run in which contention never
    happened fails rather than passing quietly. The timeouts exist only so a
    genuine hang is a failure instead of a stall.
    """

    async def test_the_reconciler_waits_for_the_batch_lock_and_then_finds_a_live_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(service_module, "APPLICATION_BATCH_SIZE", 2)
        async with live_application(drafts=4, idempotency_key="h2-deterministic") as live:
            factory = live.session_factory
            application_id = live.application_id
            set_tenant_id(live.tenant_id)

            async with factory() as owner:
                claim = await RuleApplicationService(owner).claim(
                    application_id, task_id="worker-a"
                )
                assert claim.lease is not None
                lease = claim.lease
                # Backdate the heartbeat so the reconciler's predicate matches
                # and the only thing standing between it and the row is the
                # lock this test is about.
                await owner.execute(
                    sa.update(RuleApplication)
                    .where(RuleApplication.id == application_id)
                    .values(
                        heartbeat_at=datetime.now(UTC)
                        - service_module.STALE_AFTER
                        - timedelta(minutes=1)
                    )
                )
                await owner.commit()
                observed = await RuleApplicationService(owner).observe(application_id)
                assert observed is not None

            batch_written = asyncio.Event()
            reconciler_pid_known = asyncio.Event()
            reconciler_is_blocked = asyncio.Event()
            state: dict[str, Any] = {}
            pids: dict[str, int] = {}

            async def worker_batch() -> None:
                """Hold the row lock across the batch's writes, then commit."""
                set_tenant_id(live.tenant_id)
                async with factory() as session:
                    pids["worker"] = await backend_pid(session)
                    outcome = await RuleApplicationService(session).run_next_batch(
                        application_id, lease=lease
                    )
                    state["batch"] = outcome
                    batch_written.set()
                    # Commit only once the reconciler is provably stuck behind
                    # this transaction's lock.
                    await asyncio.wait_for(reconciler_is_blocked.wait(), timeout=30)
                    await session.commit()

            async def reconciler() -> None:
                set_tenant_id(live.tenant_id)
                async with factory() as session:
                    pids["reconciler"] = await backend_pid(session)
                    reconciler_pid_known.set()
                    await asyncio.wait_for(batch_written.wait(), timeout=30)
                    state["reclaimed"] = await RuleApplicationService(session).reclaim_stale(
                        observed=observed
                    )
                    await session.commit()

            async def prove_contention() -> None:
                await asyncio.wait_for(batch_written.wait(), timeout=30)
                await asyncio.wait_for(reconciler_pid_known.wait(), timeout=30)
                state["blockers"] = await wait_until_blocked(
                    factory, pids["reconciler"], what="reconciler"
                )
                reconciler_is_blocked.set()

            await asyncio.gather(worker_batch(), reconciler(), prove_contention())

            # 1-2. The reconciler could not touch the row while the batch held
            # it, and PostgreSQL names the worker as the backend blocking it.
            assert state["blockers"], "the reconciler never contended for the row lock"
            assert pids["worker"] in state["blockers"], (
                f"expected the worker ({pids['worker']}) to be the blocker, got {state['blockers']}"
            )
            # 3. Released only after the batch committed, the CAS re-evaluated
            # the heartbeat the batch had just moved and took nothing.
            assert state["batch"] is BatchOutcome.MORE
            assert state["reclaimed"] is False, "a live run was reclaimed out from under a batch"

            record = await read_application(factory, application_id)
            assert record.processed_count == 2, "the cursor advanced more than once"
            assert record.status is ApplicationStatus.RUNNING
            assert record.recovery_count == 0, "recovery_count moved for a healthy run"
            assert record.lease_token == lease.token, "the worker lost its lease"

            targets = await item_targets(factory, application_id)
            assert len(targets) == 2, "the batch was split or repeated"
            assert len(targets) == len(set(targets)), "a target got two result rows"

            priced = await prices_now(factory, live.draft_ids)
            repriced = [p for p in priced.values() if p == Decimal("21.0000")]
            assert len(repriced) == 2, "every product in the batch is priced exactly once"

    async def test_a_batch_that_never_contends_is_reported_rather_than_assumed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The negative control for the helper the test above relies on.

        If ``wait_until_blocked`` could pass without real contention, the whole
        deterministic claim would be worthless. Here nobody holds the row, so
        it must raise instead of returning.
        """
        monkeypatch.setattr(service_module, "APPLICATION_BATCH_SIZE", 2)
        async with live_application(drafts=2, idempotency_key="h2-control") as live:
            factory = live.session_factory
            async with factory() as idle:
                pid = await backend_pid(idle)
                monkeypatch.setattr(live_module, "HANG_GUARD_SECONDS", 0.5)
                with pytest.raises(AssertionError, match="never blocked"):
                    await wait_until_blocked(factory, pid, what="idle backend")
