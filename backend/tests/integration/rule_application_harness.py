"""Drive the real rule-application task from the integration suite.

Not a test module -- deliberately named so pytest does not collect it.

Two mismatches have to be bridged to exercise the *production* task rather
than the service underneath it:

* **The loop.** A Celery worker is a synchronous process with no event loop,
  so the task calls ``asyncio.run``. An integration test already has a
  running loop. The task's ``_run`` seam is redirected to hand the coroutine
  back to the test's loop, and the task itself is invoked on a worker thread,
  so the real synchronous entry point runs exactly as it would in a worker.
* **The transaction.** The task opens its own sessions through
  ``transaction()``, which would connect outside the test's rolled-back
  transaction and see none of its data. That symbol is redirected to the test
  session, whose ``commit()`` releases a savepoint -- so batch-by-batch
  commits, and therefore resume behaviour, still really happen.

The broker is never contacted. ``enqueue`` is replaced by a recorder, which
is also what lets a test assert *when* the message was published relative to
the application being created.
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
from app.tasks import pricing as pricing_tasks


class EnqueueRecorder:
    """Stands in for the broker. Records, and can be made to fail."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail_with: Exception | None = None
        #: Called with the recorded kwargs just before returning. Lets a test
        #: observe the exact moment the message would have been published --
        #: which is how "enqueued only after the row exists" is checked.
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
    def application_ids(self) -> list[str]:
        return [str(call["application_id"]) for call in self.calls]


def bind_queue(monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession) -> EnqueueRecorder:
    """Bind the task layer to the test session and a recording broker.

    A plain function, wrapped as a fixture by each test module that wants it.
    Fixtures are only discovered in conftest or in the collected module, and
    this belongs to two modules rather than to every integration test.
    """

    @asynccontextmanager
    async def test_transaction() -> AsyncIterator[AsyncSession]:
        try:
            yield db_session
            await db_session.commit()
        except Exception:
            await db_session.rollback()
            raise

    monkeypatch.setattr(pricing_tasks, "transaction", test_transaction)

    recorder = EnqueueRecorder()
    monkeypatch.setattr(pricing_tasks, "enqueue", recorder)
    return recorder


async def run_task(
    monkeypatch: pytest.MonkeyPatch,
    *,
    application_id: str | uuid.UUID,
    task_id: str | None = None,
    retries: int = 0,
    tenant_id: uuid.UUID | None = None,
) -> Any:
    """Invoke the registered task through Celery, on a worker thread.

    ``Task.apply`` is Celery's own local execution path, so the task runs with
    a real request context -- including the task id the claim depends on --
    rather than being called as a plain function.
    """
    loop = asyncio.get_running_loop()

    def bridge(coro: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, loop).result()

    monkeypatch.setattr(pricing_tasks, "_run", bridge)

    def invoke() -> Any:
        return pricing_tasks.apply_rules_to_drafts.apply(
            kwargs={"application_id": str(application_id)},
            task_id=task_id or str(uuid.uuid4()),
            retries=retries,
            throw=True,
        )

    try:
        return (await asyncio.to_thread(invoke)).get()
    finally:
        # The task clears tenant context when it finishes, which is correct in
        # a worker and inconvenient here -- the test still has assertions to
        # make against tenant-scoped reads.
        if tenant_id is not None:
            set_tenant_id(tenant_id)


async def run_dispatch(application_id: uuid.UUID, tenant_id: uuid.UUID) -> bool:
    """Run the post-commit hand-off the API schedules as a background task."""
    try:
        return await pricing_tasks.dispatch_rule_application(application_id, tenant_id)
    finally:
        set_tenant_id(tenant_id)
