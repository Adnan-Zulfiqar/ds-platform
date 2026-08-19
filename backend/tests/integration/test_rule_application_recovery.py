"""M3A-4B — crash recovery, filter selection, throttling and target lookup.

The recovery tests are about a state no request can create: an application
left `running` by a worker that died. They construct that state directly —
that is what a crash leaves behind — and then drive the production reconciler
over it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import set_tenant_id
from app.models.product import Product
from app.models.rule_application import (
    ApplicationStatus,
    RuleApplication,
    RuleApplicationItem,
)
from app.services import rule_application as service_module
from app.services.rule_application import (
    MAX_RECOVERIES,
    STALE_AFTER,
    DraftSelectionFilter,
    ImpactPreviewService,
    RuleApplicationService,
)
from tests.integration.rule_application_harness import (
    EnqueueRecorder,
    bind_queue,
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


async def row(db_session: AsyncSession, application_id: str) -> RuleApplication:
    found = (
        await db_session.execute(
            select(RuleApplication).where(RuleApplication.id == uuid.UUID(application_id))
        )
    ).scalar_one()
    await db_session.refresh(found)
    return found


async def go_stale(
    db_session: AsyncSession, application_id: str, *, minutes_ago: int = 60
) -> RuleApplication:
    """Put a run in the state a killed worker leaves: running, silent."""
    record = await row(db_session, application_id)
    record.status = ApplicationStatus.RUNNING
    record.claimed_by_task_id = "worker-that-died"
    record.started_at = datetime.now(UTC) - timedelta(minutes=minutes_ago + 5)
    record.heartbeat_at = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    await db_session.flush()
    return record


class TestHeartbeat:
    async def test_a_claim_starts_the_heartbeat(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")
        body = await confirm(
            client, headers, productIds=[str(product.id)], idempotencyKey="hb-claim"
        )
        set_tenant_id(tenant_id)

        await RuleApplicationService(db_session).claim(uuid.UUID(body["id"]), task_id="task-a")

        record = await row(db_session, body["id"])
        assert record.heartbeat_at is not None

    async def test_each_batch_refreshes_the_heartbeat(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Written in the batch's own transaction, so it cannot keep ticking
        for a worker that has stopped committing anything."""
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        drafts = [await seed_draft(db_session, tenant_id, sell_price="30.00") for _ in range(4)]
        monkeypatch.setattr(service_module, "APPLICATION_BATCH_SIZE", 2)
        body = await confirm(
            client,
            headers,
            productIds=[str(d.id) for d in drafts],
            idempotencyKey="hb-batches",
        )

        seen: list[datetime] = []
        real_batch = RuleApplicationService.run_next_batch

        async def observing(self: RuleApplicationService, application_id: uuid.UUID) -> bool:
            more = await real_batch(self, application_id)
            record = await self.get(application_id)
            assert record.heartbeat_at is not None
            seen.append(record.heartbeat_at)
            return more

        monkeypatch.setattr(RuleApplicationService, "run_next_batch", observing)
        await run_task(monkeypatch, application_id=body["id"], tenant_id=tenant_id)

        assert len(seen) == 2
        assert seen[1] >= seen[0]


class TestStuckRunRecovery:
    async def test_a_stale_run_is_reclaimed(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")
        body = await confirm(
            client, headers, productIds=[str(product.id)], idempotencyKey="stale-1"
        )
        await go_stale(db_session, body["id"])
        set_tenant_id(tenant_id)

        reclaimed = await RuleApplicationService(db_session).reclaim_stale(
            uuid.UUID(body["id"]), task_id="rescuer"
        )

        assert reclaimed is True
        record = await row(db_session, body["id"])
        assert record.claimed_by_task_id == "rescuer"
        assert record.recovery_count == 1
        assert record.status is ApplicationStatus.RUNNING

    async def test_a_healthy_run_is_never_stolen(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The reclaim is conditional on the heartbeat at the moment it
        writes, so a worker that is merely slow keeps its run."""
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")
        body = await confirm(
            client, headers, productIds=[str(product.id)], idempotencyKey="healthy"
        )
        record = await go_stale(db_session, body["id"])
        # The worker checks in a moment before the reconciler writes.
        record.heartbeat_at = datetime.now(UTC)
        await db_session.flush()
        set_tenant_id(tenant_id)

        reclaimed = await RuleApplicationService(db_session).reclaim_stale(
            uuid.UUID(body["id"]), task_id="rescuer"
        )

        assert reclaimed is False
        refreshed = await row(db_session, body["id"])
        assert refreshed.claimed_by_task_id == "worker-that-died"
        assert refreshed.recovery_count == 0

    async def test_a_cancelled_run_is_never_resumed(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")
        body = await confirm(
            client, headers, productIds=[str(product.id)], idempotencyKey="cancelled-stale"
        )
        record = await go_stale(db_session, body["id"])
        record.status = ApplicationStatus.CANCELLED
        await db_session.flush()
        set_tenant_id(tenant_id)

        reclaimed = await RuleApplicationService(db_session).reclaim_stale(
            uuid.UUID(body["id"]), task_id="rescuer"
        )

        assert reclaimed is False
        assert (await row(db_session, body["id"])).status is ApplicationStatus.CANCELLED

    async def test_a_completed_run_is_never_resumed(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")
        body = await confirm(
            client, headers, productIds=[str(product.id)], idempotencyKey="done-stale"
        )
        await run_task(monkeypatch, application_id=body["id"], tenant_id=tenant_id)
        record = await row(db_session, body["id"])
        record.heartbeat_at = datetime.now(UTC) - STALE_AFTER - timedelta(minutes=5)
        await db_session.flush()
        set_tenant_id(tenant_id)

        reclaimed = await RuleApplicationService(db_session).reclaim_stale(
            uuid.UUID(body["id"]), task_id="rescuer"
        )

        assert reclaimed is False
        assert (await row(db_session, body["id"])).status is ApplicationStatus.COMPLETED

    async def test_recovery_resumes_without_repricing_anything_twice(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The crash mid-run, then the reconciler, then the resumed worker."""
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        drafts = [await seed_draft(db_session, tenant_id, sell_price="30.00") for _ in range(6)]
        monkeypatch.setattr(service_module, "APPLICATION_BATCH_SIZE", 2)
        body = await confirm(
            client,
            headers,
            productIds=[str(d.id) for d in drafts],
            idempotencyKey="resume-after-crash",
        )

        # One batch commits, then the worker dies.
        real_batch = RuleApplicationService.run_next_batch
        calls = {"n": 0}

        async def dies_after_one(self: RuleApplicationService, application_id: uuid.UUID) -> bool:
            calls["n"] += 1
            if calls["n"] > 1:
                raise RuntimeError("worker killed")
            return await real_batch(self, application_id)

        monkeypatch.setattr(RuleApplicationService, "run_next_batch", dies_after_one)
        with pytest.raises(Exception, match="worker killed"):
            await run_task(
                monkeypatch, application_id=body["id"], task_id="dead", tenant_id=tenant_id
            )

        after_crash = await row(db_session, body["id"])
        assert after_crash.processed_count == 2
        assert after_crash.status is ApplicationStatus.RUNNING

        # Time passes; the reconciler reclaims it.
        after_crash.heartbeat_at = datetime.now(UTC) - STALE_AFTER - timedelta(minutes=1)
        await db_session.flush()
        set_tenant_id(tenant_id)
        assert await RuleApplicationService(db_session).reclaim_stale(
            uuid.UUID(body["id"]), task_id="rescuer"
        )
        # The reclaim is a bulk UPDATE, so the identity map still holds the
        # pre-reclaim row. In production the reconciler and the worker are
        # separate sessions and never see each other's stale copy; the harness
        # shares one, so it is expired by hand.
        db_session.expire_all()

        monkeypatch.setattr(RuleApplicationService, "run_next_batch", real_batch)
        result = await run_task(
            monkeypatch, application_id=body["id"], task_id="rescuer", tenant_id=tenant_id
        )

        assert result["status"] == "completed"
        items = (
            await db_session.execute(
                select(func.count())
                .select_from(RuleApplicationItem)
                .where(RuleApplicationItem.application_id == uuid.UUID(body["id"]))
            )
        ).scalar_one()
        assert items == 6, "one row per draft — the first batch was not redone"
        for draft in drafts:
            await db_session.refresh(draft)
            assert draft.sell_price == Decimal("21.0000")

    async def test_a_run_reclaimed_too_often_is_parked(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A run that dies identically every time is a defect to look at, not
        work to retry forever."""
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")
        body = await confirm(
            client, headers, productIds=[str(product.id)], idempotencyKey="hopeless"
        )
        record = await go_stale(db_session, body["id"])
        record.recovery_count = MAX_RECOVERIES
        await db_session.flush()
        set_tenant_id(tenant_id)

        service = RuleApplicationService(db_session)
        assert await service.reclaim_stale(uuid.UUID(body["id"]), task_id="rescuer") is False
        assert await service.abandon_stale(uuid.UUID(body["id"])) is True

        parked = await row(db_session, body["id"])
        assert parked.status is ApplicationStatus.FAILED
        assert parked.failure_reason is not None
        assert "recovery attempts" in parked.failure_reason


class TestVersionConflict:
    async def test_two_writers_racing_a_version_get_a_conflict(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Both readers see the same latest version and both insert N+1. The
        loser must get a 409 explaining it, not a 500 naming an index."""
        from app.core.exceptions import ConflictError
        from app.models.pricing import GlobalRuleKind
        from app.services.global_rules import GlobalRuleService

        headers, tenant_id = await seed_tenant(client)
        rule = await create_rule(client, headers)
        set_tenant_id(tenant_id)

        service = GlobalRuleService(db_session)
        loaded = await service.get_pricing_rule(uuid.UUID(rule["id"]))

        # The first writer's version lands.
        await service._record(
            loaded, kind=GlobalRuleKind.PRICING, previous={}, actor_id=None, note="first"
        )
        await db_session.flush()

        # The second writer read the same "latest" a moment earlier, so it
        # tries to claim the number that has just been taken.
        from app.repositories.global_rules import GlobalRuleVersionRepository

        stale_latest = GlobalRuleVersionRepository.latest_version_number

        async def frozen(self: Any, **kwargs: Any) -> int:
            return await stale_latest(self, **kwargs) - 1

        GlobalRuleVersionRepository.latest_version_number = frozen  # type: ignore[method-assign]
        try:
            with pytest.raises(ConflictError) as caught:
                await service._record(
                    loaded,
                    kind=GlobalRuleKind.PRICING,
                    previous={},
                    actor_id=None,
                    note="second",
                )
                await db_session.flush()
        finally:
            GlobalRuleVersionRepository.latest_version_number = stale_latest  # type: ignore[method-assign]

        assert "same moment" in str(caught.value)
        # Nothing about the schema reaches the merchant.
        assert "uq_" not in str(caught.value)
        assert "constraint" not in str(caught.value).lower()


class TestFilterSelection:
    async def test_a_filter_is_expanded_and_stored(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The browser sends a filter; the server snapshots concrete ids."""
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        for index in range(3):
            await seed_draft(db_session, tenant_id, sell_price="30.00", title=f"Widget {index}")
        await seed_draft(db_session, tenant_id, sell_price="30.00", title="Gadget")

        body = await confirm(
            client,
            headers,
            selectionFilter={"search": "Widget", "safeOnly": True},
            idempotencyKey="by-filter",
        )

        assert body["totalCount"] == 3
        record = await row(db_session, body["id"])
        assert len(record.selection["productIds"]) == 3
        assert record.selection_filter == {
            "search": "Widget",
            "needsReviewOnly": False,
            "safeOnly": True,
            "productIds": [],
        }

    async def test_select_all_excludes_published_drafts(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        safe = await seed_draft(db_session, tenant_id, sell_price="30.00", title="Selectable")
        live = await seed_draft(db_session, tenant_id, sell_price="30.00", title="Selectable live")
        await publish(db_session, live)

        body = await confirm(
            client,
            headers,
            selectionFilter={"search": "Selectable", "safeOnly": True},
            idempotencyKey="skip-published",
        )

        record = await row(db_session, body["id"])
        assert record.selection["productIds"] == [str(safe.id)]

    async def test_select_all_excludes_drafts_already_flagged(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        safe = await seed_draft(db_session, tenant_id, sell_price="30.00", title="Flag test ok")
        flagged = await seed_draft(
            db_session, tenant_id, sell_price="30.00", title="Flag test held"
        )
        flagged.needs_review = True
        await db_session.flush()

        body = await confirm(
            client,
            headers,
            selectionFilter={"search": "Flag test", "safeOnly": True},
            idempotencyKey="skip-flagged",
        )

        record = await row(db_session, body["id"])
        assert record.selection["productIds"] == [str(safe.id)]

    async def test_a_filter_matching_nothing_is_refused(self, client: AsyncClient) -> None:
        """Better than accepting a run that would do nothing and reporting it
        as completed."""
        headers, _ = await seed_tenant(client)
        await create_rule(client, headers)

        response = await client.post(
            APPLY,
            json={
                "selectionFilter": {"search": "nothing matches this", "safeOnly": True},
                "idempotencyKey": "empty-filter",
            },
            headers=headers,
        )

        assert response.status_code == 422
        assert "Nothing matches" in response.text

    async def test_the_snapshot_caps_at_the_documented_maximum(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        for index in range(5):
            await seed_draft(db_session, tenant_id, sell_price="30.00", title=f"Capped {index}")
        monkeypatch.setattr(service_module, "MAX_APPLICATION_PRODUCTS", 3)

        body = await confirm(
            client,
            headers,
            selectionFilter={"search": "Capped", "safeOnly": True},
            idempotencyKey="capped",
        )

        assert body["totalCount"] == 3

    async def test_the_preview_reports_how_many_select_all_would_cover(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Counted in the database, not inferred from the visible page."""
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        for index in range(5):
            await seed_draft(db_session, tenant_id, sell_price="30.00", title=f"Counted {index}")
        live = await seed_draft(db_session, tenant_id, sell_price="30.00", title="Counted live")
        await publish(db_session, live)

        response = await client.get(
            f"{BASE}/drafts/impact",
            params={"search": "Counted", "size": 2},
            headers=headers,
        )

        body = response.json()
        assert len(body["items"]) == 2, "one page"
        assert body["total"] == 6
        assert body["selectableTotal"] == 5, "the published one is not selectable"

    async def test_the_filter_survives_for_audit(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The record still explains itself after the drafts have moved on."""
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00", title="Audited")
        body = await confirm(
            client,
            headers,
            selectionFilter={"search": "Audited", "safeOnly": True},
            idempotencyKey="audit-filter",
        )
        await run_task(monkeypatch, application_id=body["id"], tenant_id=tenant_id)

        product.deleted_at = datetime.now(UTC)
        await db_session.flush()

        record = await row(db_session, body["id"])
        assert record.selection_filter is not None
        assert record.selection_filter["search"] == "Audited"


class TestPreviewSafeFilter:
    async def test_safe_only_hides_published_and_flagged(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        await seed_draft(db_session, tenant_id, sell_price="30.00", title="Safe one")
        live = await seed_draft(db_session, tenant_id, sell_price="30.00", title="Safe live")
        await publish(db_session, live)

        response = await client.get(
            f"{BASE}/drafts/impact", params={"safeOnly": True}, headers=headers
        )

        titles = {item["title"] for item in response.json()["items"]}
        assert "Safe one" in titles
        assert "Safe live" not in titles

    async def test_the_preview_and_the_selection_agree(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Both go through one predicate, so what is shown is what runs."""
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        for index in range(4):
            await seed_draft(db_session, tenant_id, sell_price="30.00", title=f"Agree {index}")
        live = await seed_draft(db_session, tenant_id, sell_price="30.00", title="Agree live")
        await publish(db_session, live)
        set_tenant_id(tenant_id)

        selection = DraftSelectionFilter(search="Agree", safe_only=True)
        counted = await ImpactPreviewService(db_session).count_matching(selection)
        resolved = await RuleApplicationService(db_session)._resolve_filter(selection)

        assert counted == len(resolved) == 4


class TestTargetLookup:
    async def test_products_are_searchable_by_name(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await seed_draft(db_session, tenant_id, title="Blue running shoe")
        await seed_draft(db_session, tenant_id, title="Red kettle")

        response = await client.get(
            f"{BASE}/targets/product", params={"search": "shoe"}, headers=headers
        )

        body = response.json()
        assert body["meta"]["totalItems"] == 1
        assert body["items"][0]["label"] == "Blue running shoe"
        # The id is a separate field from the label, so a client stores one and
        # shows the other rather than parsing a combined string.
        assert uuid.UUID(body["items"][0]["id"])

    async def test_a_target_search_never_crosses_tenants(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, owner_tenant = await seed_tenant(client)
        await seed_draft(db_session, owner_tenant, title="Private inventory item")

        intruder, _ = await seed_tenant(client)
        response = await client.get(
            f"{BASE}/targets/product", params={"search": "Private"}, headers=intruder
        )

        assert response.status_code == 200
        assert response.json()["items"] == []

    async def test_target_results_are_paginated(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        for index in range(5):
            await seed_draft(db_session, tenant_id, title=f"Paged item {index}")

        response = await client.get(
            f"{BASE}/targets/product",
            params={"search": "Paged", "page": 1, "size": 2},
            headers=headers,
        )

        body = response.json()
        assert len(body["items"]) == 2
        assert body["meta"]["totalItems"] == 5
        assert body["meta"]["hasNext"] is True

    async def test_a_target_page_cannot_be_unbounded(self, client: AsyncClient) -> None:
        headers, _ = await seed_tenant(client)
        response = await client.get(
            f"{BASE}/targets/product", params={"size": 5000}, headers=headers
        )
        assert response.status_code == 422

    async def test_categories_report_the_values_actually_in_use(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        first = await seed_draft(db_session, tenant_id, title="Categorised one")
        second = await seed_draft(db_session, tenant_id, title="Categorised two")
        first.category_id = "380230"
        second.category_id = "380230"
        await db_session.flush()

        response = await client.get(f"{BASE}/targets/category", headers=headers)

        items = response.json()["items"]
        assert any(item["id"] == "380230" and "2 products" in item["sublabel"] for item in items)

    async def test_variants_can_be_narrowed_to_one_product(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        from app.models.product import ProductVariant

        headers, tenant_id = await seed_tenant(client)
        product = await seed_draft(db_session, tenant_id, title="Has variants")
        other = await seed_draft(db_session, tenant_id, title="Other product")
        for parent, label in ((product, "Small"), (product, "Large"), (other, "Unrelated")):
            db_session.add(
                ProductVariant(
                    tenant_id=tenant_id,
                    product_id=parent.id,
                    external_variant_id=f"sku-{uuid.uuid4().hex[:6]}",
                    label=label,
                    currency="USD",
                )
            )
        await db_session.flush()

        response = await client.get(
            f"{BASE}/targets/variant",
            params={"productId": str(product.id)},
            headers=headers,
        )

        labels = {item["label"] for item in response.json()["items"]}
        assert labels == {"Small", "Large"}

    async def test_a_viewer_may_search_targets(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, tenant_id = await seed_tenant(client)
        await seed_draft(db_session, tenant_id, title="Viewer visible")

        response = await client.get(f"{BASE}/targets/product", headers=viewer_header(tenant_id))

        assert response.status_code == 200


class TestPreviewThrottling:
    async def test_the_preview_is_throttled_per_tenant(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A tight quota, exercised through the real dependency and the real
        limiter — only the Redis round trip is replaced."""
        from app.core import rate_limit as rate_limit_module
        from app.core.config import settings

        counters: dict[str, int] = {}

        async def counting(
            self: Any, key: str, *, limit: int, window: int
        ) -> rate_limit_module.LimitDecision:
            counters[key] = counters.get(key, 0) + 1
            used = counters[key]
            # Only the endpoint quota is tightened. The broad middleware runs
            # through this same limiter, and capping it too would produce a 429
            # from the wrong layer -- which is what this test would then be
            # proving.
            if not key.startswith("ratelimit:global-rules-"):
                return rate_limit_module.LimitDecision(True, limit, limit, 0)
            return rate_limit_module.LimitDecision(used <= 2, limit, limit - used, 30)

        monkeypatch.setattr(rate_limit_module.FixedWindowLimiter, "consume", counting)
        monkeypatch.setattr(settings.security, "rate_limit_enabled", True)

        headers, _tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        payload = {"itemCost": "10", "shippingCost": "4", "currency": "USD"}

        first = await client.post(f"{BASE}/preview", json=payload, headers=headers)
        second = await client.post(f"{BASE}/preview", json=payload, headers=headers)
        third = await client.post(f"{BASE}/preview", json=payload, headers=headers)

        assert first.status_code == 200
        assert second.status_code == 200
        assert third.status_code == 429
        body = third.json()
        assert body["code"] == "rate_limit_exceeded"
        assert body["requestId"]
        assert third.headers["Retry-After"] == "30"

    async def test_one_tenant_cannot_consume_anothers_quota(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.core import rate_limit as rate_limit_module
        from app.core.config import settings

        counters: dict[str, int] = {}

        async def counting(
            self: Any, key: str, *, limit: int, window: int
        ) -> rate_limit_module.LimitDecision:
            counters[key] = counters.get(key, 0) + 1
            used = counters[key]
            if not key.startswith("ratelimit:global-rules-"):
                return rate_limit_module.LimitDecision(True, limit, limit, 0)
            return rate_limit_module.LimitDecision(used <= 1, limit, limit - used, 30)

        monkeypatch.setattr(rate_limit_module.FixedWindowLimiter, "consume", counting)
        monkeypatch.setattr(settings.security, "rate_limit_enabled", True)

        first_headers, _ = await seed_tenant(client)
        payload = {"itemCost": "10", "shippingCost": "4", "currency": "USD"}
        assert (
            await client.post(f"{BASE}/preview", json=payload, headers=first_headers)
        ).status_code == 200
        assert (
            await client.post(f"{BASE}/preview", json=payload, headers=first_headers)
        ).status_code == 429

        # A different tenant starts with its own allowance.
        second_headers, _ = await seed_tenant(client)
        assert (
            await client.post(f"{BASE}/preview", json=payload, headers=second_headers)
        ).status_code == 200

        keys = [key for key in counters if key.startswith("ratelimit:global-rules-preview")]
        assert len(keys) == 2, "counted against two distinct tenant keys"


class TestPublishedSafetyUnderFilters:
    async def test_a_published_draft_submitted_directly_is_still_refused(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Even when the id is posted explicitly rather than selected."""
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")
        await publish(db_session, product)

        body = await confirm(
            client, headers, productIds=[str(product.id)], idempotencyKey="direct-published"
        )
        await run_task(monkeypatch, application_id=body["id"], tenant_id=tenant_id)

        await db_session.refresh(product)
        assert product.sell_price == Decimal("30.0000")
        assert product.pricing_calculated_at is None, "nothing was even stamped on it"
        stored = (
            await db_session.execute(select(Product.sell_price).where(Product.id == product.id))
        ).scalar_one()
        assert stored == Decimal("30.0000")
