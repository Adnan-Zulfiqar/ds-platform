"""Admin Control Center phase 8 (D-019): trials, plan overrides and feature
switches, audited, with the workspace told."""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import set_tenant_id
from app.models.notification import Notification
from app.models.platform_admin import PlatformAdminAudit
from app.services.feature_flags import FeatureDisabledError, FeatureFlagService
from app.services.pipeline_bulk import PipelineBulkRunService
from tests.integration.conftest import registration_payload
from tests.integration.test_platform_admin_auth import make_admin, sign_in
from tests.integration.test_platform_admin_auth import panel as panel
from tests.integration.test_platform_workspace_users import workspace

pytestmark = pytest.mark.integration

P = "/api/v1/platform"


async def audit(db_session: AsyncSession, action: str) -> list[PlatformAdminAudit]:
    return list(
        await db_session.scalars(
            sa.select(PlatformAdminAudit).where(PlatformAdminAudit.action == action)
        )
    )


async def test_finance_extends_a_trial_without_a_support_session(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    tenant_id, _ = await workspace(client)
    headers = await sign_in(client, await make_admin(db_session, role="finance"), reauth=True)
    before = (await client.get(f"{P}/workspaces/{tenant_id}/billing", headers=headers)).json()
    assert before["onTrial"] is True and {f["key"] for f in before["flags"]} == {
        "ai_bulk_pipeline",
        "channel_publishing",
        "supplier_auto_ordering",
    }

    response = await client.post(
        f"{P}/workspaces/{tenant_id}/billing/trial",
        json={"days": 10, "reason": "onboarding delayed by us"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    old = datetime.fromisoformat(before["trialEndsAt"])
    new = datetime.fromisoformat(response.json()["trialEndsAt"])
    assert 9.9 * 86400 < (new - old).total_seconds() < 10.1 * 86400
    [row] = await audit(db_session, "workspace_trial_extended")
    assert row.detail["reason"] == "onboarding delayed by us"
    assert set(row.detail) >= {"before", "after"}
    set_tenant_id(tenant_id)
    titles = [n.title for n in await db_session.scalars(sa.select(Notification))]
    assert "Your free trial was extended" in titles


async def test_a_plan_override_decides_the_merchants_entitlement_until_cleared(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    payload = await client.post(
        "/api/v1/auth/register",
        json=registration_payload(),
    )
    owner = payload.json()
    tenant_id = owner["identity"]["tenant"]["id"]
    merchant = {"Authorization": f"Bearer {owner['tokens']['accessToken']}"}
    headers = await sign_in(client, await make_admin(db_session, role="finance"), reauth=True)

    granted = await client.post(
        f"{P}/workspaces/{tenant_id}/billing/plan-override",
        json={"plan": "pro", "ai": True, "days": 30, "reason": "partner programme"},
        headers=headers,
    )
    assert granted.status_code == 200, granted.text
    assert granted.json()["plan"] == "pro" and granted.json()["canUseAi"] is True
    status = (await client.get("/api/v1/billing", headers=merchant)).json()
    assert status["plan"] == "pro"

    cleared = await client.post(
        f"{P}/workspaces/{tenant_id}/billing/plan-override/clear",
        json={"reason": "programme ended"},
        headers=headers,
    )
    assert cleared.json()["planOverride"] is None and cleared.json()["plan"] is None
    assert len(await audit(db_session, "workspace_plan_override_set")) == 1
    assert len(await audit(db_session, "workspace_plan_override_cleared")) == 1


async def test_a_switched_off_feature_is_refused_where_it_starts(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    tenant_id, _ = await workspace(client)
    headers = await sign_in(client, await make_admin(db_session), reauth=True)
    response = await client.post(
        f"{P}/workspaces/{tenant_id}/feature-flags/ai_bulk_pipeline",
        json={"enabled": False, "reason": "abuse of AI credits"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    state = {f["key"]: f for f in response.json()["flags"]}["ai_bulk_pipeline"]
    assert (state["override"], state["effective"]) == (False, False)

    set_tenant_id(tenant_id)
    with pytest.raises(FeatureDisabledError):
        await PipelineBulkRunService(db_session).create(
            product_ids=[uuid.uuid4()],
            idempotency_key="k1",
            tone="neutral",
            store_id=None,
            actor_id=None,
        )

    cleared = await client.post(
        f"{P}/workspaces/{tenant_id}/feature-flags/ai_bulk_pipeline",
        json={"enabled": None, "reason": "resolved"},
        headers=headers,
    )
    state = {f["key"]: f for f in cleared.json()["flags"]}["ai_bulk_pipeline"]
    assert (state["override"], state["effective"]) == (None, True)
    assert len(await audit(db_session, "workspace_feature_flag_set")) == 2


async def test_only_a_super_admin_changes_a_platform_default_and_it_applies_everywhere(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    tenant_id, _ = await workspace(client)
    admin = await sign_in(client, await make_admin(db_session, role="admin"), reauth=True)
    refused = await client.post(
        f"{P}/feature-flags/channel_publishing",
        json={"enabled": False, "reason": "incident"},
        headers=admin,
    )
    assert refused.status_code == 403

    super_admin = await sign_in(
        client,
        await make_admin(db_session, email="root@droppilot.example"),
        email="root@droppilot.example",
        reauth=True,
    )
    response = await client.post(
        f"{P}/feature-flags/channel_publishing",
        json={"enabled": False, "reason": "incident"},
        headers=super_admin,
    )
    assert response.status_code == 200, response.text
    assert {f["key"]: f["enabled"] for f in response.json()}["channel_publishing"] is False
    set_tenant_id(tenant_id)
    assert await FeatureFlagService(db_session).is_enabled("channel_publishing") is False
    [row] = await audit(db_session, "feature_flag_default_set")
    assert row.detail == {"reason": "incident", "before": True, "after": False}


async def test_billing_changes_need_reauthentication_and_the_role(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    tenant_id, _ = await workspace(client)
    plain = await sign_in(client, await make_admin(db_session, role="finance"))
    refused = await client.post(
        f"{P}/workspaces/{tenant_id}/billing/trial",
        json={"days": 5, "reason": "x y z"},
        headers=plain,
    )
    assert refused.status_code == 403 and refused.json()["code"] == "reauth_required"

    support = await sign_in(
        client,
        await make_admin(db_session, role="support", email="s@droppilot.example"),
        email="s@droppilot.example",
        reauth=True,
    )
    assert (
        await client.get(f"{P}/workspaces/{tenant_id}/billing", headers=support)
    ).status_code == 403
