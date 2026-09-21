"""Drive the real pipeline-bulk Celery task from the integration suite.

Not collected by pytest. Same seams as the rule-application harness: the test
loop, the test transaction, and a recording broker.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import set_tenant_id
from app.tasks import ai as ai_tasks


class EnqueueRecorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail_with: Exception | None = None
        self.on_enqueue: Callable[[dict[str, Any]], None] | None = None

    def __call__(self, task: Any, *args: Any, **kwargs: Any) -> Any:
        if self.fail_with is not None:
            raise self.fail_with
        record = {"task": getattr(task, "name", None), **kwargs}
        self.calls.append(record)
        if self.on_enqueue is not None:
            self.on_enqueue(record)
        return None

    @property
    def run_ids(self) -> list[str]:
        return [str(call["run_id"]) for call in self.calls]


def bind_queue(monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession) -> EnqueueRecorder:
    @asynccontextmanager
    async def test_transaction() -> AsyncIterator[AsyncSession]:
        # Savepoint per worker unit of work so a preview exception cannot
        # roll back the durable run row sitting on the test connection.
        async with db_session.begin_nested():
            yield db_session

    monkeypatch.setattr(ai_tasks, "transaction", test_transaction)
    recorder = EnqueueRecorder()
    monkeypatch.setattr(ai_tasks, "enqueue", recorder)
    return recorder


async def run_task(
    monkeypatch: pytest.MonkeyPatch,
    *,
    run_id: str | uuid.UUID,
    task_id: str | None = None,
    retries: int = 0,
    tenant_id: uuid.UUID | None = None,
    throw: bool = True,
    extra_kwargs: dict[str, Any] | None = None,
) -> Any:
    loop = asyncio.get_running_loop()

    def bridge(coro: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, loop).result()

    monkeypatch.setattr(ai_tasks, "_run", bridge)

    def invoke() -> Any:
        return ai_tasks.process_pipeline_bulk_run.apply(
            kwargs={"run_id": str(run_id), **(extra_kwargs or {})},
            task_id=task_id or str(uuid.uuid4()),
            retries=retries,
            throw=throw,
        )

    try:
        return (await asyncio.to_thread(invoke)).get()
    finally:
        if tenant_id is not None:
            set_tenant_id(tenant_id)
