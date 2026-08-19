"""M3A-3 acceptance fix — the rule-application task is genuinely wired.

Registration is not a formality here: a task the worker never imports fails
at call time with "unregistered task", which looks nothing like a wiring
problem. These assert the two things that make it reachable in production --
it is registered under its name, and its module is in the worker's import
list -- neither of which an in-process test of the function would catch.
"""

from __future__ import annotations

import pytest

from app.tasks import pricing as pricing_tasks
from app.workers.celery_app import celery_app

pytestmark = pytest.mark.unit

TASK_NAME = "pricing.apply_rules_to_drafts"


def test_the_task_is_registered_under_its_name() -> None:
    assert pricing_tasks.apply_rules_to_drafts.name == TASK_NAME
    assert TASK_NAME in celery_app.tasks


def test_the_worker_imports_the_module_that_defines_it() -> None:
    """Extending `app.tasks.pricing` rather than adding a module is what
    makes this true without touching the broker configuration."""
    assert "app.tasks.pricing" in celery_app.conf.imports


def test_the_task_retries_like_every_other_task() -> None:
    """At-least-once delivery is the contract; the task is idempotent, and
    the retry policy is the shared one rather than a bespoke copy."""
    task = pricing_tasks.apply_rules_to_drafts
    assert task.max_retries == 3
    assert task.retry_backoff is True
    assert task.retry_jitter is True


def test_it_is_not_on_the_beat_schedule() -> None:
    """A confirmed reprice is merchant-initiated. A scheduled one would
    reprice a catalogue nobody asked to reprice."""
    scheduled = {entry["task"] for entry in celery_app.conf.beat_schedule.values()}
    assert TASK_NAME not in scheduled


class TestReconcilerTask:
    """The sweep that recovers runs abandoned by a crashed worker."""

    def test_the_reconciler_is_registered(self) -> None:
        assert pricing_tasks.reconcile_applications.name == "pricing.reconcile_applications"
        assert "pricing.reconcile_applications" in celery_app.tasks

    def test_it_runs_on_the_beat_schedule(self) -> None:
        """A merchant watching a stalled progress bar needs it to resume in
        minutes, and nothing else would ever notice the run."""
        entry = celery_app.conf.beat_schedule["pricing-reconcile-applications"]
        assert entry["task"] == "pricing.reconcile_applications"
        assert entry["schedule"] <= 60 * 10

    def test_a_quiet_sweep_touches_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def nothing(coro: object) -> list[object]:
            coro.close()  # type: ignore[attr-defined]
            return []

        monkeypatch.setattr(pricing_tasks, "_run", nothing)

        assert pricing_tasks.reconcile_applications.run() == {
            "requeued": 0,
            "abandoned": 0,
            "healthy": 0,
            "skipped": 0,
            "republished": 0,
        }

    def test_each_stale_run_is_handled_under_its_own_tenant(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The tenant comes from the row, never from a caller."""
        import uuid as uuid_module

        stale = [
            (uuid_module.uuid4(), uuid_module.uuid4(), 0),
            (uuid_module.uuid4(), uuid_module.uuid4(), 1),
        ]
        seen: list[tuple[object, object, int]] = []
        calls = {"n": 0}

        def fake_run(coro: object) -> object:
            calls["n"] += 1
            coro.close()  # type: ignore[attr-defined]
            # First the stale sweep, then one call per application, then the
            # unpublished sweep -- which finds nothing in this test.
            if calls["n"] == 1:
                return stale
            if calls["n"] <= 1 + len(stale):
                return "requeued"
            return []

        async def recording(application_id: object, tenant_id: object, recoveries: int) -> str:
            seen.append((application_id, tenant_id, recoveries))
            return "requeued"

        monkeypatch.setattr(pricing_tasks, "_reconcile_one", recording)
        monkeypatch.setattr(pricing_tasks, "_run", fake_run)

        result = pricing_tasks.reconcile_applications.run()

        assert result["requeued"] == 2
        assert result["republished"] == 0

    def test_it_also_republishes_runs_the_broker_never_took(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The row and the message cannot be written atomically, so a run the
        broker never accepted is picked up here rather than waiting forever."""
        import uuid as uuid_module

        unpublished = [(uuid_module.uuid4(), uuid_module.uuid4())]
        calls = {"n": 0}

        def fake_run(coro: object) -> object:
            calls["n"] += 1
            coro.close()  # type: ignore[attr-defined]
            if calls["n"] == 1:
                return []  # nothing stale
            if calls["n"] == 2:
                return unpublished
            return "republished"

        monkeypatch.setattr(pricing_tasks, "_run", fake_run)

        result = pricing_tasks.reconcile_applications.run()

        assert result["republished"] == 1
        assert result["requeued"] == 0
