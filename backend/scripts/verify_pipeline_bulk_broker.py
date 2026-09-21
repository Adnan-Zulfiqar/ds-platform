#!/usr/bin/env python3
"""Verify durable pipeline bulk execution against a real RabbitMQ worker.

Polls PostgreSQL, not Celery AsyncResult. Requires a worker already running
against the same broker and the test database at Alembic head.

Exits non-zero on any failure. Intended for CI ``celery-broker``.
"""

from __future__ import annotations

import asyncio
import sys
import time
import uuid

sys.path.insert(0, ".")

from sqlalchemy import select

from app.core.context import clear_context, set_tenant_id
from app.database.session import dispose_engine, transaction
from app.models.pipeline_bulk import PipelineBulkItemState, PipelineBulkRun, PipelineBulkRunItem
from app.models.product import (
    Product,
    ProductSource,
    ProductStatus,
    ProductVersion,
    ProductVersionSource,
)
from app.models.tenant import Tenant, TenantStatus
from app.services.pipeline_bulk import PipelineBulkRunService
from app.workers.celery_app import celery_app

_POLL_SECONDS = 180.0


def _fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


async def _pipeline_candidates(product_id: uuid.UUID) -> list[ProductVersion]:
    async with transaction() as session:
        rows = (
            (
                await session.execute(
                    select(ProductVersion).where(ProductVersion.product_id == product_id)
                )
            )
            .scalars()
            .all()
        )
    return [
        version
        for version in rows
        if version.source is ProductVersionSource.AI_GENERATED
        and isinstance(version.content, dict)
        and version.content.get("pipelineCandidateVersion") == 1
    ]


async def _seed() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    suffix = uuid.uuid4().hex[:10]
    async with transaction() as session:
        tenant = Tenant(
            name="Pipeline bulk broker",
            slug=f"pbbulk-{suffix}",
            status=TenantStatus.ACTIVE,
        )
        session.add(tenant)
        await session.flush()
        product = Product(
            tenant_id=tenant.id,
            source=ProductSource.MANUAL,
            external_id=f"pbbulk-{suffix}",
            title="Broker bulk product",
            status=ProductStatus.DRAFT,
        )
        session.add(product)
        await session.flush()
        set_tenant_id(tenant.id)
        try:
            run = await PipelineBulkRunService(session).create(
                product_ids=[product.id],
                idempotency_key=f"broker-{suffix}",
                tone="professional",
                store_id=None,
                actor_id=None,
            )
        finally:
            clear_context()
        return run.id, product.id, tenant.id


async def _read_run(run_id: uuid.UUID) -> PipelineBulkRun | None:
    async with transaction() as session:
        return (
            await session.execute(select(PipelineBulkRun).where(PipelineBulkRun.id == run_id))
        ).scalar_one_or_none()


async def _read_item(run_id: uuid.UUID) -> PipelineBulkRunItem | None:
    async with transaction() as session:
        return (
            await session.execute(
                select(PipelineBulkRunItem).where(PipelineBulkRunItem.run_id == run_id)
            )
        ).scalar_one_or_none()


async def _poll_completed(run_id: uuid.UUID) -> PipelineBulkRun:
    deadline = time.monotonic() + _POLL_SECONDS
    while time.monotonic() < deadline:
        run = await _read_run(run_id)
        if run is not None and run.status.value == "completed":
            return run
        await asyncio.sleep(0.5)
    run = await _read_run(run_id)
    _fail(f"run {run_id} did not reach completed (last status={getattr(run, 'status', None)})")
    raise AssertionError("unreachable")


async def _async_main() -> None:
    run_id, product_id, tenant_id = await _seed()
    print(f"seeded run={run_id} product={product_id} tenant={tenant_id}")

    celery_app.loader.import_default_modules()
    celery_app.send_task("ai.process_pipeline_bulk_run", kwargs={"run_id": str(run_id)})
    print("published ai.process_pipeline_bulk_run")

    run = await _poll_completed(run_id)
    item = await _read_item(run_id)
    if item is None:
        _fail("item row missing")
    assert item is not None
    if item.state is not PipelineBulkItemState.SUCCEEDED:
        _fail(f"item state={item.state.value!r}, expected succeeded")
    if item.candidate_version_id is None:
        _fail("candidateVersionId was not stored")
    if run.processed_count != 1 or run.succeeded_count != 1:
        _fail(f"counts processed={run.processed_count} succeeded={run.succeeded_count}")

    candidates = await _pipeline_candidates(product_id)
    if len(candidates) != 1:
        _fail(f"expected exactly one pipeline-marked candidate, found {len(candidates)}")
    candidate = candidates[0]
    if candidate.id != item.candidate_version_id:
        _fail("candidate id does not match item.candidate_version_id")
    if candidate.active is not False:
        _fail("candidate was activated")
    if candidate.tenant_id != tenant_id:
        _fail("candidate tenant does not match the run")

    first_processed = run.processed_count
    first_succeeded = run.succeeded_count
    first_candidate = item.candidate_version_id

    celery_app.send_task("ai.process_pipeline_bulk_run", kwargs={"run_id": str(run_id)})
    print("published duplicate ai.process_pipeline_bulk_run")
    await asyncio.sleep(5)

    run = await _read_run(run_id)
    item = await _read_item(run_id)
    candidates = await _pipeline_candidates(product_id)
    if run is None or item is None:
        _fail("run or item missing after duplicate delivery")
    if len(candidates) != 1:
        _fail(f"duplicate delivery created extra candidates: {len(candidates)}")
    if item.candidate_version_id != first_candidate:
        _fail("candidateVersionId changed after duplicate delivery")
    if run.processed_count != first_processed or run.succeeded_count != first_succeeded:
        _fail("counters changed after duplicate delivery")
    print("pipeline bulk broker verification: PASSED")


def main() -> None:
    try:
        asyncio.run(_async_main())
    finally:
        asyncio.run(dispose_engine())


if __name__ == "__main__":
    main()
