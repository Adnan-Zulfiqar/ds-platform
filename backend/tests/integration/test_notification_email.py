"""Track E3 — notifications by email, from the outbox.

Real database, real repositories, the stub email provider (which records
instead of sending). Asserts who is mailed, about what, and what is recorded.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import set_tenant_id
from app.integrations.email import provider as email_provider
from app.integrations.email.provider import EmailDeliveryError, StubEmailProvider
from app.models.notification import Notification, NotificationKind
from app.services.notification_email import NotificationEmailService
from app.services.notification_service import NotificationService
from tests.integration.test_ebay_c1_api import auth_header, register

pytestmark = pytest.mark.integration

PREFS_URL = "/api/v1/notifications/email-preferences"


@pytest.fixture
def mailbox() -> Iterator[StubEmailProvider]:
    email_provider.reset_email_provider()
    stub = email_provider.get_email_provider()
    assert isinstance(stub, StubEmailProvider)
    yield stub
    email_provider.reset_email_provider()


async def workspace(client: AsyncClient) -> tuple[dict[str, Any], uuid.UUID, str]:
    body = await register(client)
    tenant_id = uuid.UUID(str(body["identity"]["tenant"]["id"]))
    return body, tenant_id, str(body["identity"]["user"]["email"])


async def test_failures_reach_admins_by_default_routine_news_does_not(
    client: AsyncClient, db_session: AsyncSession, mailbox: StubEmailProvider
) -> None:
    _, tenant_id, owner_email = await workspace(client)
    set_tenant_id(tenant_id)
    service = NotificationService(db_session)
    failure = await service.notify(
        kind=NotificationKind.SYNC_FAILED,
        title="Inventory sync failed",
        body="Boom",
        href="/inventory",
    )
    routine = await service.notify(kind=NotificationKind.IMPORT_COMPLETED, title="Import done")
    info = await service.notify(kind=NotificationKind.INFO, title="FYI")
    assert (failure.email_status, routine.email_status, info.email_status) == (
        "pending",
        "pending",
        None,
    )

    outcome = await NotificationEmailService(db_session).deliver_pending()

    assert (outcome.sent, outcome.skipped, outcome.failed) == (1, 1, 0)
    assert [m.to for m in mailbox.sent] == [owner_email]
    message = mailbox.sent[0]
    assert message.subject == "DropPilot: Inventory sync failed"
    assert "http://localhost:3000/inventory" in message.text
    assert (failure.email_status, routine.email_status) == ("sent", "skipped")
    assert failure.emailed_at is not None

    again = await NotificationEmailService(db_session).deliver_pending()
    assert again.sent == 0 and len(mailbox.sent) == 1  # never twice


async def test_preferences_change_what_is_sent(
    client: AsyncClient, db_session: AsyncSession, mailbox: StubEmailProvider
) -> None:
    body, tenant_id, _ = await workspace(client)
    headers = auth_header(body)

    default = await client.get(PREFS_URL, headers=headers)
    assert default.status_code == 200
    assert "sync_failed" in default.json()["kinds"]
    assert "info" not in default.json()["available"]

    saved = await client.put(PREFS_URL, json={"kinds": ["import_completed"]}, headers=headers)
    assert saved.json()["kinds"] == ["import_completed"]
    assert (
        await client.put(PREFS_URL, json={"kinds": ["nonsense"]}, headers=headers)
    ).status_code == 422

    set_tenant_id(tenant_id)
    service = NotificationService(db_session)
    await service.notify(kind=NotificationKind.SYNC_FAILED, title="Failed")
    await service.notify(kind=NotificationKind.IMPORT_COMPLETED, title="Imported")
    outcome = await NotificationEmailService(db_session).deliver_pending()
    assert (outcome.sent, outcome.skipped) == (1, 1)
    assert [m.subject for m in mailbox.sent] == ["DropPilot: Imported"]


async def test_a_user_targeted_notification_goes_to_that_user_only(
    client: AsyncClient, db_session: AsyncSession, mailbox: StubEmailProvider
) -> None:
    body, tenant_id, owner_email = await workspace(client)
    set_tenant_id(tenant_id)
    owner_id = uuid.UUID(str(body["identity"]["user"]["id"]))
    await NotificationService(db_session).notify(
        kind=NotificationKind.TASK_FAILURE, title="Yours", user_id=owner_id
    )
    await NotificationEmailService(db_session).deliver_pending()
    assert [m.to for m in mailbox.sent] == [owner_email]


async def test_a_provider_failure_is_recorded_not_retried_forever(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    mailbox: StubEmailProvider,
) -> None:
    _, tenant_id, _ = await workspace(client)
    set_tenant_id(tenant_id)
    note = await NotificationService(db_session).notify(
        kind=NotificationKind.SYNC_FAILED, title="x"
    )

    async def down(_message: Any) -> str:
        raise EmailDeliveryError()

    monkeypatch.setattr(mailbox, "send", down)
    outcome = await NotificationEmailService(db_session).deliver_pending()
    assert outcome.failed == 1
    assert note.email_status == "failed"


async def test_another_workspace_never_sees_this_ones_outbox(
    client: AsyncClient, db_session: AsyncSession, mailbox: StubEmailProvider
) -> None:
    _, first, _ = await workspace(client)
    _, second, _ = await workspace(client)
    set_tenant_id(first)
    await NotificationService(db_session).notify(kind=NotificationKind.SYNC_FAILED, title="first")
    set_tenant_id(second)
    outcome = await NotificationEmailService(db_session).deliver_pending()
    assert outcome.sent == 0 and mailbox.sent == []
    pending = await db_session.scalar(
        sa.select(sa.func.count())
        .select_from(Notification)
        .where(Notification.email_status == "pending")
    )
    assert pending == 1
