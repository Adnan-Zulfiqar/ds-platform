"""Phase 6 Celery task registration and beat schedule."""

from __future__ import annotations

import pytest

from app.workers.celery_app import celery_app

pytestmark = pytest.mark.unit

EXPECTED_TASKS = {
    "inventory.sync",
    "inventory.sync_one",
    "pricing.recalculate",
    "pricing.recalculate_one",
    "automation.run",
    "automation.run_one",
    "shipment.refresh",
    "analytics.aggregate",
    "analytics.aggregate_one",
    "cleanup.old_notifications",
}

EXPECTED_BEAT = {
    "inventory-sync",
    "pricing-recalculate",
    "automation-run",
    "shipment-refresh",
    "analytics-aggregate",
    "cleanup-old-notifications",
}


def test_phase6_tasks_are_registered() -> None:
    # Importing the modules is what registers the tasks with the app.
    import app.tasks.analytics
    import app.tasks.automation
    import app.tasks.inventory
    import app.tasks.notifications
    import app.tasks.pricing
    import app.tasks.shipments  # noqa: F401

    registered = set(celery_app.tasks)
    missing = EXPECTED_TASKS - registered
    assert not missing, f"Missing tasks: {missing}"


def test_phase6_beat_entries_exist() -> None:
    schedule = set(celery_app.conf.beat_schedule)
    missing = EXPECTED_BEAT - schedule
    assert not missing, f"Missing beat entries: {missing}"
