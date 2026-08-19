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
