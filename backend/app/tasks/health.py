"""Worker liveness task.

Used by Compose/CI health checks and by ``scripts/verify_celery_broker.py``.
It must stay free of database and Redis so a broker-only outage is distinguishable
from an application-data outage.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from celery import Task

from app.workers.celery_app import celery_app


@celery_app.task(
    base=Task,
    bind=True,
    name="workers.health",
    # No BaseTask retries: a health probe that retries masks a dead worker as "slow".
    max_retries=0,
)
def worker_health(self: Any, **_: Any) -> dict[str, Any]:
    return {
        "ok": True,
        "task_id": self.request.id,
        "checked_at": datetime.now(UTC).isoformat(),
    }
