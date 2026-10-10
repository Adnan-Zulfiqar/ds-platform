"""The worker image must consume every queue that ``task_routes`` sends to.

A queue with a producer and no consumer fails silently: the task is accepted,
acknowledged by the broker, and never runs. That is how every Shopify sync
went unexecuted on a local stack until 2026-10-10.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core.config import settings
from app.workers.celery_app import celery_app

pytestmark = pytest.mark.unit

DOCKERFILE = Path(__file__).resolve().parents[3] / "docker" / "worker.Dockerfile"


def _routed_queues() -> set[str]:
    queues = {settings.celery.task_default_queue}
    for route in celery_app.conf.task_routes.values():
        if isinstance(route, dict) and "queue" in route:
            queues.add(str(route["queue"]))
    return queues


def _dockerfile_queues() -> set[str]:
    text = DOCKERFILE.read_text(encoding="utf-8")
    match = re.search(r'"--queues=([^"]+)"', text)
    assert match, "worker CMD must pass --queues explicitly"
    return set(match.group(1).split(","))


def test_the_worker_cmd_consumes_every_routed_queue() -> None:
    missing = _routed_queues() - _dockerfile_queues()
    assert not missing, f"queues with tasks but no consumer: {sorted(missing)}"


def test_ci_starts_the_worker_on_the_same_queues() -> None:
    ci = (DOCKERFILE.parents[1] / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "--queues=default,integrations" in ci
