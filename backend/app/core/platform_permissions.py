"""Platform operator roles and what each may do (Admin Control Center, D-018).

The matrix lives in code, not in the database: a change to who may do what
is a security change, so it goes through review like any other code. A role
stored on an operator only *names* a row of this table.

Permissions are checked on the server for every platform route (see
``app.api.deps.require_platform_permission``); the console hides what an
operator cannot do, but hiding is convenience, not security.

Operators never get a tenant-scoped write that deletes customer data or
transfers ownership (owner decision D-018, "safe operational only").
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class PlatformRole(StrEnum):
    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"
    SUPPORT = "support"
    FINANCE = "finance"
    OPERATIONS = "operations"
    AUDITOR = "auditor"


class PlatformPermission(StrEnum):
    #: Workspace list, counts, health.
    TENANTS_READ = "tenants.read"
    #: Suspend or reactivate a whole workspace.
    TENANTS_SUSPEND = "tenants.suspend"
    #: Operator accounts: list, sessions.
    OPERATORS_READ = "operators.read"
    #: Change an operator's role, deactivate, revoke their sessions.
    OPERATORS_MANAGE = "operators.manage"
    #: The operator audit trail and security events.
    AUDIT_READ = "audit.read"


_ALL: Final = frozenset(PlatformPermission)

#: What each role may do. Later phases add permissions here (users, stores,
#: jobs, billing, settings) and grant them per role in the same review.
ROLE_PERMISSIONS: Final[dict[PlatformRole, frozenset[PlatformPermission]]] = {
    PlatformRole.SUPER_ADMIN: _ALL,
    PlatformRole.ADMIN: _ALL - {PlatformPermission.OPERATORS_MANAGE},
    PlatformRole.SUPPORT: frozenset({PlatformPermission.TENANTS_READ}),
    PlatformRole.FINANCE: frozenset({PlatformPermission.TENANTS_READ}),
    PlatformRole.OPERATIONS: frozenset(
        {PlatformPermission.TENANTS_READ, PlatformPermission.TENANTS_SUSPEND}
    ),
    PlatformRole.AUDITOR: frozenset(
        {
            PlatformPermission.TENANTS_READ,
            PlatformPermission.OPERATORS_READ,
            PlatformPermission.AUDIT_READ,
        }
    ),
}

#: Actions that need a fresh password + code, not just a valid session.
REAUTH_WINDOW_MINUTES: Final = 10


def permissions_for(role: str) -> frozenset[PlatformPermission]:
    """An unknown role (a row written by hand, a role removed in a later
    version) grants nothing rather than failing open."""
    try:
        return ROLE_PERMISSIONS[PlatformRole(role)]
    except ValueError:
        return frozenset()


def has_permission(role: str, permission: PlatformPermission) -> bool:
    return permission in permissions_for(role)


__all__ = [
    "REAUTH_WINDOW_MINUTES",
    "ROLE_PERMISSIONS",
    "PlatformPermission",
    "PlatformRole",
    "has_permission",
    "permissions_for",
]
