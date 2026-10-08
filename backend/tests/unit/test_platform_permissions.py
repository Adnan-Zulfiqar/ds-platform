"""D-018 — the operator permission matrix."""

from __future__ import annotations

import pytest

from app.core.platform_permissions import (
    READ_PERMISSIONS,
    ROLE_PERMISSIONS,
    PlatformPermission,
    PlatformRole,
    has_permission,
    permissions_for,
)

pytestmark = pytest.mark.unit

P = PlatformPermission


def test_every_role_has_a_row() -> None:
    assert set(ROLE_PERMISSIONS) == set(PlatformRole)


def test_only_a_super_admin_manages_operators() -> None:
    holders = {r for r in PlatformRole if has_permission(r.value, P.OPERATORS_MANAGE)}
    assert holders == {PlatformRole.SUPER_ADMIN}


def test_the_super_admin_holds_every_permission() -> None:
    assert permissions_for("super_admin") == frozenset(PlatformPermission)


@pytest.mark.parametrize("role", ["support", "finance", "auditor"])
def test_read_roles_cannot_suspend_a_workspace(role: str) -> None:
    assert not has_permission(role, P.TENANTS_SUSPEND)


def test_the_auditor_reads_everything_but_changes_nothing() -> None:
    granted = permissions_for("auditor")
    assert {P.AUDIT_READ, P.WORKSPACE_DATA_READ, P.BILLING_READ} <= granted
    assert granted <= READ_PERMISSIONS


def test_only_a_super_admin_changes_platform_settings() -> None:
    holders = {r for r in PlatformRole if has_permission(r.value, P.SETTINGS_MANAGE)}
    assert holders == {PlatformRole.SUPER_ADMIN}


def test_finance_sees_billing_but_not_workspace_data() -> None:
    granted = permissions_for("finance")
    assert {P.BILLING_READ, P.BILLING_MANAGE} <= granted
    assert P.WORKSPACE_DATA_READ not in granted


def test_every_role_that_changes_a_workspace_can_open_a_support_session() -> None:
    """Workspace changes need a support session (D-019), so a role granted
    one without the other would hold a permission it can never use."""
    workspace_writes = {P.USERS_MANAGE, P.STORES_MANAGE, P.CATALOG_MANAGE, P.ORDERS_MANAGE}
    for role in PlatformRole:
        granted = permissions_for(role.value)
        if granted & workspace_writes:
            assert P.SUPPORT_SESSION in granted, role
            assert P.WORKSPACE_DATA_READ in granted, role


@pytest.mark.parametrize("role", ["", "root", "SUPER_ADMIN", "owner"])
def test_an_unknown_role_grants_nothing(role: str) -> None:
    assert permissions_for(role) == frozenset()
