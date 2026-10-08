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
    #: Download the audit trail as CSV.
    AUDIT_EXPORT = "audit.export"
    #: Platform-wide metrics and system health.
    DASHBOARD_READ = "dashboard.read"
    #: Everything inside one workspace, read-only (D-019): users, stores,
    #: products, listings, orders, inventory, jobs, notifications.
    WORKSPACE_DATA_READ = "workspace.data.read"
    #: Open a time-limited support session, which every workspace change
    #: needs (D-019).
    SUPPORT_SESSION = "support.session"
    #: Disable or enable a user, end their sessions, require a password
    #: reset, revoke invitations, change a member's workspace role.
    USERS_MANAGE = "users.manage"
    #: Pause or resume store sync, trigger syncs, re-register webhooks.
    STORES_MANAGE = "stores.manage"
    #: Retry imports and listing syncs.
    CATALOG_MANAGE = "catalog.manage"
    #: Refresh orders, release supplier orders.
    ORDERS_MANAGE = "orders.manage"
    #: Failed and stuck background work, across workspaces.
    JOBS_READ = "jobs.read"
    #: Retry, cancel, or close a stuck job.
    JOBS_MANAGE = "jobs.manage"
    #: Subscriptions, trials and plans.
    BILLING_READ = "billing.read"
    #: Extend a trial, override a plan, set feature flags.
    BILLING_MANAGE = "billing.manage"
    #: Maintenance mode, announcements, broadcast notifications.
    SETTINGS_MANAGE = "settings.manage"


P = PlatformPermission
_ALL: Final = frozenset(PlatformPermission)

#: What each role may do. A grant here is a security change and is reviewed
#: like one (D-018, widened by D-019).
ROLE_PERMISSIONS: Final[dict[PlatformRole, frozenset[PlatformPermission]]] = {
    PlatformRole.SUPER_ADMIN: _ALL,
    # Everything except changing who the operators are and the
    # platform-wide switches.
    PlatformRole.ADMIN: _ALL - {P.OPERATORS_MANAGE, P.SETTINGS_MANAGE},
    PlatformRole.SUPPORT: frozenset(
        {
            P.TENANTS_READ,
            P.DASHBOARD_READ,
            P.WORKSPACE_DATA_READ,
            P.SUPPORT_SESSION,
            P.USERS_MANAGE,
            P.JOBS_READ,
        }
    ),
    PlatformRole.FINANCE: frozenset(
        {P.TENANTS_READ, P.DASHBOARD_READ, P.BILLING_READ, P.BILLING_MANAGE}
    ),
    PlatformRole.OPERATIONS: frozenset(
        {
            P.TENANTS_READ,
            P.TENANTS_SUSPEND,
            P.DASHBOARD_READ,
            P.WORKSPACE_DATA_READ,
            P.SUPPORT_SESSION,
            P.STORES_MANAGE,
            P.CATALOG_MANAGE,
            P.ORDERS_MANAGE,
            P.JOBS_READ,
            P.JOBS_MANAGE,
        }
    ),
    # Sees everything, changes nothing.
    PlatformRole.AUDITOR: frozenset(
        {
            P.TENANTS_READ,
            P.OPERATORS_READ,
            P.AUDIT_READ,
            P.AUDIT_EXPORT,
            P.DASHBOARD_READ,
            P.WORKSPACE_DATA_READ,
            P.JOBS_READ,
            P.BILLING_READ,
        }
    ),
}

#: Read-only permissions. Everything else changes state.
READ_PERMISSIONS: Final = frozenset(
    {
        P.TENANTS_READ,
        P.OPERATORS_READ,
        P.AUDIT_READ,
        P.AUDIT_EXPORT,
        P.DASHBOARD_READ,
        P.WORKSPACE_DATA_READ,
        P.JOBS_READ,
        P.BILLING_READ,
    }
)

#: Actions that need a fresh password + code, not just a valid session.
REAUTH_WINDOW_MINUTES: Final = 10

#: How long a support session (D-019) may be opened for.
SUPPORT_SESSION_MIN_MINUTES: Final = 5
SUPPORT_SESSION_MAX_MINUTES: Final = 120


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
    "READ_PERMISSIONS",
    "REAUTH_WINDOW_MINUTES",
    "ROLE_PERMISSIONS",
    "SUPPORT_SESSION_MAX_MINUTES",
    "SUPPORT_SESSION_MIN_MINUTES",
    "PlatformPermission",
    "PlatformRole",
    "has_permission",
    "permissions_for",
]
