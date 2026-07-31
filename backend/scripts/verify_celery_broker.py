#!/usr/bin/env python3
"""Verify Celery against a real RabbitMQ broker (not eager / mocked .run()).

Exits non-zero on any failure. Intended for CI and for operators who have
Docker Compose (or an equivalent broker) available.

Steps:
1. Confirm the app can open the broker URL
2. Confirm expected tasks are registered
3. Confirm beat schedule entries resolve to registered task names
4. Apply ``workers.health`` and wait for a worker result
5. Apply representative Phase 6 sync tasks and wait for results

Requires a worker process already running against the same broker.
"""

from __future__ import annotations

import sys
import time
from typing import Any

# Ensure the backend package is importable when run as a script.
sys.path.insert(0, ".")

from app.workers.celery_app import celery_app

REQUIRED_TASKS = (
    "workers.health",
    "inventory.sync",
    "pricing.recalculate",
    "orders.sync_all",
    "orders.cleanup",
    "cleanup.old_notifications",
)


def _fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def _wait_result(async_result: Any, *, label: str, timeout: float = 120.0) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if async_result.ready():
            if async_result.failed():
                _fail(f"{label} failed: {async_result.result!r}")
            return async_result.result
        time.sleep(0.5)
    _fail(f"{label} timed out after {timeout:.0f}s (is a worker running?)")


def main() -> None:
    broker = celery_app.conf.broker_url
    print(f"broker={broker}")

    # Connection probe — Celery opens lazily; force a channel.
    with celery_app.connection_or_acquire() as connection:
        connection.ensure_connection(max_retries=3)
    print("broker connection: ok")

    registered = set(celery_app.tasks.keys())
    missing = [name for name in REQUIRED_TASKS if name not in registered]
    if missing:
        _fail(f"tasks not registered: {missing}")
    print(f"registered required tasks: {len(REQUIRED_TASKS)}")

    for entry_name, entry in celery_app.conf.beat_schedule.items():
        task_name = entry["task"]
        if task_name not in registered:
            _fail(f"beat entry {entry_name!r} points at unregistered {task_name!r}")
    print(f"beat schedule entries: {len(celery_app.conf.beat_schedule)}")

    health = celery_app.send_task("workers.health")
    health_body = _wait_result(health, label="workers.health")
    if not isinstance(health_body, dict) or not health_body.get("ok"):
        _fail(f"workers.health returned unexpected payload: {health_body!r}")
    print("workers.health: ok")

    for task_name in (
        "inventory.sync",
        "pricing.recalculate",
        "orders.sync_all",
        "orders.cleanup",
    ):
        result = celery_app.send_task(task_name)
        body = _wait_result(result, label=task_name, timeout=180.0)
        print(f"{task_name}: ok ({body!r})")

    print("celery broker verification: PASSED")


if __name__ == "__main__":
    main()
