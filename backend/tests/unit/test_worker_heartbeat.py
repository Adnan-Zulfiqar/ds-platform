"""The worker's container health check reads a heartbeat file instead of
running ``celery inspect ping`` (which imports the whole application)."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from app.workers.celery_app import celery_app, touch_worker_heartbeat

# The package attribute `app.workers.celery_app` is the Celery object, not the
# module, so the module is fetched by name.
worker_module = importlib.import_module("app.workers.celery_app")

pytestmark = pytest.mark.unit


def test_the_heartbeat_file_is_written(tmp_path: Path) -> None:
    target = tmp_path / "heartbeat"
    assert touch_worker_heartbeat(target) is True
    assert target.exists()


def test_an_unwritable_heartbeat_path_does_not_raise(tmp_path: Path) -> None:
    """A health file that cannot be written must never fail a task."""
    assert touch_worker_heartbeat(tmp_path / "missing-dir" / "heartbeat") is False


def test_beat_keeps_an_idle_worker_heartbeating() -> None:
    """The health check allows 3 minutes; beat must schedule the broker-only
    health task well inside that."""
    entry = celery_app.conf.beat_schedule["workers-heartbeat"]
    assert entry["task"] == "workers.health"
    assert entry["schedule"] <= 60


def test_the_default_file_name_matches_the_health_check() -> None:
    """The compose and Dockerfile health checks grep for this exact name."""
    assert worker_module.WORKER_HEARTBEAT_FILE.name == "celery-worker-heartbeat"
