"""Tenant isolation for team invitations (Track E4), by compiled SQL.

The invitation table holds live sign-up links. A leak across tenants would
let one workspace see — or accept into — another's roster.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.core.context import MissingTenantContextError, clear_context, set_tenant_id
from app.repositories.invitation import UserInvitationRepository

pytestmark = pytest.mark.unit


def _compile(query: object) -> str:
    return str(
        query.compile(  # type: ignore[attr-defined]  # Select has no typed compile()
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def test_open_invitations_are_filtered_by_the_bound_tenant(tenant_id: uuid.UUID) -> None:
    set_tenant_id(tenant_id)
    sql = _compile(UserInvitationRepository(MagicMock())._open())
    assert "user_invitations.tenant_id" in sql
    assert str(tenant_id) in sql
    assert "accepted_at IS NULL" in sql and "revoked_at IS NULL" in sql


def test_a_link_lookup_without_a_tenant_raises_rather_than_searching_every_tenant() -> None:
    clear_context()
    with pytest.raises(MissingTenantContextError):
        UserInvitationRepository(MagicMock())._open()
