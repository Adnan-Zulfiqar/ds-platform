"""D-018 — the operator permission matrix."""

from __future__ import annotations

import pytest

from app.core.platform_permissions import (
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


def test_the_auditor_reads_audit_but_changes_nothing() -> None:
    granted = permissions_for("auditor")
    assert P.AUDIT_READ in granted
    assert not granted & {P.TENANTS_SUSPEND, P.OPERATORS_MANAGE}


@pytest.mark.parametrize("role", ["", "root", "SUPER_ADMIN", "owner"])
def test_an_unknown_role_grants_nothing(role: str) -> None:
    assert permissions_for(role) == frozenset()
