"""A manual automation run whose action fails is still recorded.

Before 2026-10-10 the service marked the run failed and re-raised, so the
request's transaction rolled the run, its notification and the rule's
failure count back: the user saw an error and the runs list showed nothing.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.automation_service import AutomationService
from tests.integration.test_products import auth_header, register

pytestmark = pytest.mark.integration

RULES = "/api/v1/automation/rules"


async def test_a_failed_manual_run_is_returned_and_listed_with_its_error(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = auth_header(await register(client))
    created = await client.post(
        RULES,
        json={"name": "Hourly inventory", "action": "sync_inventory", "schedule": "hourly"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    rule_id = created.json()["id"]
    monkeypatch.setattr(
        AutomationService, "_dispatch", AsyncMock(side_effect=RuntimeError("supplier down"))
    )

    run = await client.post(f"{RULES}/{rule_id}/run", headers=headers)
    assert run.status_code == 201, run.text
    assert run.json()["status"] == "failed"
    assert run.json()["errorMessage"] == "supplier down"

    runs = (await client.get("/api/v1/automation/runs", headers=headers)).json()
    assert [r["id"] for r in runs["items"]] == [run.json()["id"]]
    rules = (await client.get(RULES, headers=headers)).json()["items"]
    [rule] = [r for r in rules if r["id"] == rule_id]
    assert rule["consecutiveFailures"] == 1

    notifications = (await client.get("/api/v1/notifications", headers=headers)).json()
    assert any(n["kind"] == "automation_failed" for n in notifications["items"])
