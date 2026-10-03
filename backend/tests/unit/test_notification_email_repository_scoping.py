"""Tenant isolation for the Track E3 email outbox and preferences, by
compiled SQL. The outbox sweep runs per tenant; an unscoped query here would
mail one workspace's notifications to another's admins."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.core.context import MissingTenantContextError, clear_context, set_tenant_id
from app.repositories.notification import (
    NotificationEmailPreferenceRepository,
    NotificationRepository,
)

pytestmark = pytest.mark.unit


def _compile(query: object) -> str:
    return str(
        query.compile(  # type: ignore[attr-defined]  # Select has no typed compile()
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


@pytest.mark.parametrize(
    ("repository", "table"),
    [
        (NotificationRepository, "notifications"),
        (NotificationEmailPreferenceRepository, "notification_email_preferences"),
    ],
)
def test_reads_are_filtered_by_the_bound_tenant(
    repository: type, table: str, tenant_id: uuid.UUID
) -> None:
    set_tenant_id(tenant_id)
    sql = _compile(repository(MagicMock())._base_query())
    assert f"{table}.tenant_id" in sql
    assert str(tenant_id) in sql


def test_the_sweep_without_a_tenant_raises_rather_than_reading_every_outbox() -> None:
    clear_context()
    with pytest.raises(MissingTenantContextError):
        NotificationRepository(MagicMock())._base_query()
