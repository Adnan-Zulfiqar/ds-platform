"""Platform operator identity (Track E5, D-015; Admin Control Center, D-018).

**Sign-in needs three things:** a password (Argon2id, as for tenant users), a
current TOTP code, and an address inside ``PLATFORM_ADMIN_ALLOWED_CIDRS``
(checked by the dependency before this service runs). Every failure gets the
same message, whether the address is unknown, the password wrong, the code
wrong or reused, or the account disabled.

**Every sign-in opens a session** (D-018). The token carries its id, and each
request checks the session is still open, so revoking it ends access at
once. Sensitive actions also need the operator to have re-entered password
and code in this session within ``REAUTH_WINDOW_MINUTES``.

**Failures are audited in their own transaction.** The request's transaction
rolls back when it fails; an audit row written there would vanish with it,
and a log of failures that records no failures is worse than none.

**Accounts are created only by** ``scripts/create_platform_admin.py`` on the
server. There is no web path that creates one.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import totp
from app.core.encryption import (
    EncryptionNotConfiguredError,
    decrypt,
    encrypt,
    is_encryption_configured,
)
from app.core.exceptions import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from app.core.password import hash_password, validate_password_strength, verify_password
from app.core.platform_permissions import (
    REAUTH_WINDOW_MINUTES,
    SUPPORT_SESSION_MAX_MINUTES,
    SUPPORT_SESSION_MIN_MINUTES,
    PlatformPermission,
    PlatformRole,
    has_permission,
    permissions_for,
)
from app.core.tokens import IssuedToken, create_platform_token, platform_token_expiry
from app.database.session import transaction
from app.models.notification import NotificationKind
from app.models.platform_admin import (
    PlatformAdmin,
    PlatformAdminAudit,
    PlatformAdminSession,
    PlatformSupportSession,
)
from app.models.tenant import TenantStatus
from app.repositories.notification import NotificationRepository
from app.repositories.platform_admin import (
    PlatformAdminAuditRepository,
    PlatformAdminRepository,
    PlatformAdminSessionRepository,
    PlatformSupportSessionRepository,
    PlatformTenantDirectory,
    TenantDirectoryRow,
    TenantHealth,
)
from app.repositories.tenant import TenantRepository
from app.services.base import BaseService

_REFUSED = "Invalid sign-in details."
_TOKEN_INVALID = "The authentication token is not valid."  # noqa: S105 — a message, not a credential
#: ``last_seen_at`` is written at most this often, not on every request.
_SEEN_RESOLUTION = timedelta(seconds=60)


@dataclass(frozen=True, slots=True)
class NewPlatformAdmin:
    admin: PlatformAdmin
    totp_secret: str
    provisioning_uri: str


@dataclass(frozen=True, slots=True)
class AuditContext:
    """Who is asking from where, for the audit trail. Built by the router
    from the request; never trusted for authorization."""

    client_ip: str | None = None
    user_agent: str | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class PlatformPrincipal:
    """A verified operator and the session their token belongs to."""

    admin: PlatformAdmin
    session: PlatformAdminSession

    @property
    def permissions(self) -> frozenset[PlatformPermission]:
        return permissions_for(self.admin.role)

    def can(self, permission: PlatformPermission) -> bool:
        return has_permission(self.admin.role, permission)

    def reauthenticated_recently(self, now: datetime | None = None) -> bool:
        at = self.session.reauthenticated_at
        if at is None:
            return False
        return (now or datetime.now(UTC)) - at <= timedelta(minutes=REAUTH_WINDOW_MINUTES)


class PlatformAdminService(BaseService):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.admins = PlatformAdminRepository(session)
        self.audit = PlatformAdminAuditRepository(session)
        self.sessions = PlatformAdminSessionRepository(session)

    # --- accounts (CLI) ----------------------------------------------------

    async def create_admin(
        self, *, email: str, password: str, role: str = PlatformRole.SUPER_ADMIN.value
    ) -> NewPlatformAdmin:
        """For the CLI only. Returns the TOTP secret once; it is stored
        encrypted and never shown again."""
        if not is_encryption_configured():
            raise EncryptionNotConfiguredError()
        try:
            role = PlatformRole(role).value
        except ValueError:
            raise ValidationError(f"Unknown role: {role}.") from None
        address = email.strip().lower()
        validate_password_strength(password, email=address)
        if await self.admins.get_by_email(address) is not None:
            raise ConflictError("A platform admin with this email already exists.")
        secret = totp.generate_secret()
        admin = await self.admins.create(
            email=address,
            password_hash=hash_password(password),
            encrypted_totp_secret=encrypt(secret),
            is_active=True,
            role=role,
        )
        await self.audit.append(
            action="admin_created",
            admin_id=admin.id,
            client_ip=None,
            actor_role=role,
            target_type="operator",
            target_id=str(admin.id),
            detail={"role": role, "via": "cli"},
        )
        return NewPlatformAdmin(
            admin=admin,
            totp_secret=secret,
            provisioning_uri=totp.provisioning_uri(secret, account=address),
        )

    # --- sign-in, sessions, re-authentication ------------------------------

    async def authenticate(
        self, *, email: str, password: str, code: str, ctx: AuditContext
    ) -> IssuedToken:
        admin = await self.admins.get_by_email(email)
        # Spend the same Argon2 work for an unknown address as for a known one.
        password_ok = verify_password(password, admin.password_hash if admin else None)
        if admin is None or not password_ok or not admin.is_active:
            await self._audit_failure(
                action="login_failed", admin=admin, ctx=ctx, detail={"reason": "credentials"}
            )
            raise AuthenticationError(_REFUSED)

        locked = await self.admins.get_for_update(admin.id)
        if locked is None:  # deleted between the two reads
            raise AuthenticationError(_REFUSED)
        if not self._accept_code(locked, code):
            await self._audit_failure(
                action="login_failed", admin=admin, ctx=ctx, detail={"reason": "totp"}
            )
            raise AuthenticationError(_REFUSED)

        locked.last_login_at = datetime.now(UTC)
        expires_at = platform_token_expiry()
        opened = await self.sessions.open(
            admin_id=admin.id,
            expires_at=expires_at,
            client_ip=ctx.client_ip,
            user_agent=ctx.user_agent,
        )
        await self._audit(
            "login_succeeded",
            admin=locked,
            ctx=ctx,
            target_type="session",
            target_id=str(opened.id),
        )
        await self.session.flush()
        self.logger.info("platform_admin_signed_in", admin_id=str(admin.id))
        return create_platform_token(admin_id=admin.id, session_id=opened.id, expires_at=expires_at)

    def _accept_code(self, locked: PlatformAdmin, code: str) -> bool:
        """A TOTP code is good once (RFC 6238 §5.2): the step must be newer
        than the last one accepted. The caller holds the row lock."""
        step = totp.verify(decrypt(locked.encrypted_totp_secret), code)
        if step is None or (locked.totp_last_step is not None and step <= locked.totp_last_step):
            return False
        locked.totp_last_step = step
        return True

    async def principal(self, *, admin_id: uuid.UUID, session_id: uuid.UUID) -> PlatformPrincipal:
        """The operator behind a verified token, if the account is active and
        the session is open. Every failure is the same 401."""
        admin = await self.admins.get_by_id(admin_id)
        row = await self.sessions.get(session_id)
        now = datetime.now(UTC)
        if (
            admin is None
            or not admin.is_active
            or row is None
            or row.admin_id != admin.id
            or row.revoked_at is not None
            or row.expires_at <= now
        ):
            raise AuthenticationError(_TOKEN_INVALID)
        if row.last_seen_at is None or now - row.last_seen_at > _SEEN_RESOLUTION:
            row.last_seen_at = now
            await self.session.flush()
        return PlatformPrincipal(admin=admin, session=row)

    async def logout(self, principal: PlatformPrincipal, ctx: AuditContext) -> None:
        await self.sessions.revoke(principal.session, reason="signed_out")
        await self._audit(
            "logout",
            admin=principal.admin,
            ctx=ctx,
            target_type="session",
            target_id=str(principal.session.id),
        )

    async def reauthenticate(
        self, principal: PlatformPrincipal, *, password: str, code: str, ctx: AuditContext
    ) -> datetime:
        """Password and a fresh code again, inside the current session. The
        code is consumed like a sign-in code, so it cannot be replayed."""
        admin = principal.admin
        if not verify_password(password, admin.password_hash):
            await self._audit_failure(
                action="reauth_failed", admin=admin, ctx=ctx, detail={"reason": "credentials"}
            )
            raise AuthenticationError(_REFUSED)
        locked = await self.admins.get_for_update(admin.id)
        if locked is None or not self._accept_code(locked, code):
            await self._audit_failure(
                action="reauth_failed", admin=admin, ctx=ctx, detail={"reason": "totp"}
            )
            raise AuthenticationError(_REFUSED)
        now = datetime.now(UTC)
        principal.session.reauthenticated_at = now
        await self._audit(
            "reauth_succeeded",
            admin=admin,
            ctx=ctx,
            target_type="session",
            target_id=str(principal.session.id),
        )
        await self.session.flush()
        return now

    async def my_sessions(self, principal: PlatformPrincipal) -> list[PlatformAdminSession]:
        return await self.sessions.open_for(principal.admin.id)

    async def revoke_own_session(
        self, principal: PlatformPrincipal, session_id: uuid.UUID, ctx: AuditContext
    ) -> None:
        row = await self.sessions.get(session_id)
        if row is None or row.admin_id != principal.admin.id:
            # Someone else's session id: indistinguishable from no session.
            raise NotFoundError.for_resource("Session", session_id)
        await self.sessions.revoke(row, reason="revoked_by_self")
        await self._audit(
            "session_revoked",
            admin=principal.admin,
            ctx=ctx,
            target_type="session",
            target_id=str(row.id),
        )

    # --- operators (D-018) -------------------------------------------------

    async def operators(self) -> list[tuple[PlatformAdmin, int]]:
        """Every operator with their number of open sessions."""
        rows = await self.admins.all_operators()
        counts = await self.sessions.open_counts()
        return [(row, counts.get(row.id, 0)) for row in rows]

    async def operator_sessions(self, operator_id: uuid.UUID) -> list[PlatformAdminSession]:
        await self._operator(operator_id)
        return await self.sessions.open_for(operator_id)

    async def set_operator_role(
        self,
        principal: PlatformPrincipal,
        operator_id: uuid.UUID,
        *,
        role: str,
        reason: str,
        ctx: AuditContext,
    ) -> PlatformAdmin:
        try:
            new_role = PlatformRole(role).value
        except ValueError:
            raise ValidationError(f"Unknown role: {role}.") from None
        supers = await self._lock_super_admins()
        target = await self._operator(operator_id, lock=True)
        self._refuse_self(principal, target, "change your own role")
        before = target.role
        if before == new_role:
            return target
        if before == PlatformRole.SUPER_ADMIN.value:
            self._keep_one_super_admin(target, supers)
        target.role = new_role
        # A role change takes effect now, not at token expiry: their open
        # sessions end and they sign in again under the new role.
        ended = await self.sessions.revoke_all_for(target.id, reason="role_changed")
        await self._audit(
            "operator_role_changed",
            admin=principal.admin,
            ctx=ctx,
            target_type="operator",
            target_id=str(target.id),
            detail={
                "reason": reason[:500],
                "before": {"role": before},
                "after": {"role": new_role},
                "sessions_ended": ended,
            },
        )
        await self.session.flush()
        return target

    async def set_operator_active(
        self,
        principal: PlatformPrincipal,
        operator_id: uuid.UUID,
        *,
        active: bool,
        reason: str,
        ctx: AuditContext,
    ) -> PlatformAdmin:
        supers = await self._lock_super_admins()
        target = await self._operator(operator_id, lock=True)
        self._refuse_self(principal, target, "deactivate your own account")
        if target.is_active == active:
            return target
        if not active and target.role == PlatformRole.SUPER_ADMIN.value:
            self._keep_one_super_admin(target, supers)
        target.is_active = active
        ended = (
            0
            if active
            else await self.sessions.revoke_all_for(target.id, reason="operator_deactivated")
        )
        await self._audit(
            "operator_reactivated" if active else "operator_deactivated",
            admin=principal.admin,
            ctx=ctx,
            target_type="operator",
            target_id=str(target.id),
            detail={"reason": reason[:500], "sessions_ended": ended},
        )
        await self.session.flush()
        return target

    async def revoke_operator_sessions(
        self,
        principal: PlatformPrincipal,
        operator_id: uuid.UUID,
        *,
        reason: str,
        ctx: AuditContext,
    ) -> int:
        target = await self._operator(operator_id)
        ended = await self.sessions.revoke_all_for(target.id, reason="revoked_by_admin")
        await self._audit(
            "operator_sessions_revoked",
            admin=principal.admin,
            ctx=ctx,
            target_type="operator",
            target_id=str(target.id),
            detail={"reason": reason[:500], "sessions_ended": ended},
        )
        return ended

    async def _operator(self, operator_id: uuid.UUID, *, lock: bool = False) -> PlatformAdmin:
        row = (
            await self.admins.get_for_update(operator_id)
            if lock
            else await self.admins.get_by_id(operator_id)
        )
        if row is None:
            raise NotFoundError.for_resource("Operator", operator_id)
        return row

    @staticmethod
    def _refuse_self(principal: PlatformPrincipal, target: PlatformAdmin, what: str) -> None:
        """Privilege changes always need a second operator: nobody raises,
        lowers or removes their own access."""
        if target.id == principal.admin.id:
            raise PermissionDeniedError(f"You cannot {what}.")

    async def _lock_super_admins(self) -> int:
        """Taken before the target's own lock, in every privilege change, so
        two concurrent changes queue in the same order instead of
        deadlocking."""
        return await self.admins.lock_active_with_role(PlatformRole.SUPER_ADMIN.value)

    @staticmethod
    def _keep_one_super_admin(target: PlatformAdmin, active_supers: int) -> None:
        if target.is_active and active_supers <= 1:
            raise ConflictError("The last active super admin cannot be demoted or deactivated.")

    # --- audit -----------------------------------------------------------

    async def _audit(
        self,
        action: str,
        *,
        admin: PlatformAdmin | None,
        ctx: AuditContext,
        outcome: str = "success",
        target_tenant_id: uuid.UUID | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> PlatformAdminAudit:
        return await self.audit.append(
            action=action,
            admin_id=admin.id if admin else None,
            actor_role=admin.role if admin else None,
            client_ip=ctx.client_ip,
            user_agent=ctx.user_agent,
            request_id=ctx.request_id,
            outcome=outcome,
            target_tenant_id=target_tenant_id,
            target_type=target_type,
            target_id=target_id,
            detail=detail,
        )

    async def _audit_failure(
        self,
        *,
        action: str,
        admin: PlatformAdmin | None,
        ctx: AuditContext,
        detail: dict[str, Any],
    ) -> None:
        """Committed independently of the failing request (see module doc)."""
        try:
            async with transaction() as session:
                await PlatformAdminAuditRepository(session).append(
                    action=action,
                    admin_id=admin.id if admin else None,
                    actor_role=admin.role if admin else None,
                    client_ip=ctx.client_ip,
                    user_agent=ctx.user_agent,
                    request_id=ctx.request_id,
                    outcome="failure",
                    detail=detail,
                )
        except Exception:  # an audit outage must not turn a refusal into a 500
            self.logger.exception("platform_admin_audit_write_failed")
        self.logger.warning("platform_admin_refused", action=action, **detail)

    # --- support sessions (D-019) -------------------------------------------

    async def open_support_session(
        self,
        principal: PlatformPrincipal,
        tenant_id: uuid.UUID,
        *,
        minutes: int,
        reason: str,
        ctx: AuditContext,
    ) -> PlatformSupportSession:
        """Opens (or extends, by replacing) this operator's window on one
        workspace. The caller has re-authenticated, and is inside the
        workspace's tenant context so the notice lands in its feed."""
        if not SUPPORT_SESSION_MIN_MINUTES <= minutes <= SUPPORT_SESSION_MAX_MINUTES:
            raise ValidationError(
                f"A support session lasts {SUPPORT_SESSION_MIN_MINUTES}"
                f"-{SUPPORT_SESSION_MAX_MINUTES} minutes."
            )
        repo = PlatformSupportSessionRepository(self.session)
        current = await repo.active(admin_id=principal.admin.id, tenant_id=tenant_id)
        if current is not None:
            await repo.end(current, reason="replaced")
        row = await repo.open(
            admin_id=principal.admin.id,
            tenant_id=tenant_id,
            reason=reason,
            expires_at=datetime.now(UTC) + timedelta(minutes=minutes),
        )
        await NotificationRepository(self.session).create(
            kind=NotificationKind.INFO,
            title="DropPilot support is working in your workspace",
            body=(
                f"A DropPilot operator opened a support session for {minutes} minutes. "
                f"Reason: {reason[:300]}"
            ),
            payload={"support_session_id": str(row.id)},
        )
        await self._audit(
            "support_session_opened",
            admin=principal.admin,
            ctx=ctx,
            target_tenant_id=tenant_id,
            target_type="support_session",
            target_id=str(row.id),
            detail={"reason": reason[:500], "minutes": minutes},
        )
        return row

    async def end_support_session(
        self, principal: PlatformPrincipal, tenant_id: uuid.UUID, *, ctx: AuditContext
    ) -> None:
        repo = PlatformSupportSessionRepository(self.session)
        current = await repo.active(admin_id=principal.admin.id, tenant_id=tenant_id)
        if current is None:
            raise NotFoundError("No open support session for this workspace.")
        await repo.end(current, reason="ended_by_operator")
        await self._audit(
            "support_session_ended",
            admin=principal.admin,
            ctx=ctx,
            target_tenant_id=tenant_id,
            target_type="support_session",
            target_id=str(current.id),
        )

    async def active_support_session(
        self, principal: PlatformPrincipal, tenant_id: uuid.UUID
    ) -> PlatformSupportSession | None:
        return await PlatformSupportSessionRepository(self.session).active(
            admin_id=principal.admin.id, tenant_id=tenant_id
        )

    async def record_workspace_action(
        self,
        principal: PlatformPrincipal,
        tenant_id: uuid.UUID,
        action: str,
        *,
        target_type: str,
        target_id: str,
        reason: str,
        ctx: AuditContext,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """A change inside a workspace (D-019). Written in the same
        transaction as the change: either both happen or neither does."""
        await self._audit(
            action,
            admin=principal.admin,
            ctx=ctx,
            target_tenant_id=tenant_id,
            target_type=target_type,
            target_id=target_id,
            detail={"reason": reason[:500], **(detail or {})},
        )

    async def record_workspace_view(
        self,
        principal: PlatformPrincipal,
        tenant_id: uuid.UUID,
        *,
        route: str,
        ctx: AuditContext,
    ) -> None:
        """Every look inside a workspace leaves a row (D-019). Written in the
        request's transaction: a view that fails is not a view."""
        await self._audit(
            "workspace_viewed",
            admin=principal.admin,
            ctx=ctx,
            target_tenant_id=tenant_id,
            target_type="workspace",
            target_id=str(tenant_id),
            detail={"route": route[:200]},
        )

    async def record_workspace_export(
        self,
        principal: PlatformPrincipal,
        tenant_id: uuid.UUID,
        *,
        dataset: str,
        rows: int,
        ctx: AuditContext,
    ) -> None:
        """A bulk copy of customer data leaving the platform: its own row,
        with what and how much."""
        await self._audit(
            "workspace_data_exported",
            admin=principal.admin,
            ctx=ctx,
            target_tenant_id=tenant_id,
            target_type="workspace",
            target_id=str(tenant_id),
            detail={"dataset": dataset, "rows": rows},
        )

    async def record_workspace_failure(
        self,
        principal: PlatformPrincipal,
        tenant_id: uuid.UUID,
        action: str,
        *,
        reason: str,
        error: str,
        ctx: AuditContext,
    ) -> None:
        """An operator's change that failed part-way. The request rolls back,
        so this is written in its own transaction: an attempt on a workspace
        leaves a trace even when it changed nothing."""
        try:
            async with transaction() as session:
                await PlatformAdminAuditRepository(session).append(
                    action=action,
                    admin_id=principal.admin.id,
                    actor_role=principal.admin.role,
                    client_ip=ctx.client_ip,
                    user_agent=ctx.user_agent,
                    request_id=ctx.request_id,
                    outcome="failure",
                    target_tenant_id=tenant_id,
                    target_type="workspace",
                    target_id=str(tenant_id),
                    detail={"reason": reason[:500], "error": error[:300]},
                )
        except Exception:  # an audit outage must not mask the original error
            self.logger.exception("platform_admin_audit_write_failed")

    async def audit_permission_denied(
        self, admin: PlatformAdmin, *, permission: str, ctx: AuditContext
    ) -> None:
        """A security event: an operator tried something outside their role."""
        await self._audit_failure(
            action="permission_denied",
            admin=admin,
            ctx=ctx,
            detail={"permission": permission},
        )

    # --- workspaces (E5b) --------------------------------------------------

    async def list_tenants(
        self, *, page: int, size: int, q: str | None
    ) -> tuple[list[TenantDirectoryRow], int]:
        return await PlatformTenantDirectory(self.session).page(page=page, size=size, q=q)

    async def set_tenant_active(
        self,
        tenant_id: uuid.UUID,
        *,
        active: bool,
        reason: str,
        principal: PlatformPrincipal,
        ctx: AuditContext,
    ) -> TenantDirectoryRow:
        """Suspend or reactivate a workspace. Sign-in and token refresh already
        refuse an inactive tenant uniformly, so a suspension takes effect at
        the next refresh: within one access-token lifetime (15 minutes).

        The audit row is written in the same transaction as the change: if
        either fails, neither happens.
        """
        tenants = TenantRepository(self.session)
        tenant = await tenants.get_by_id(tenant_id)
        if tenant is None:
            raise NotFoundError.for_resource("Workspace", tenant_id)
        before = (tenant.status.value, tenant.is_active)
        await tenants.update(
            tenant,
            is_active=active,
            status=TenantStatus.ACTIVE if active else TenantStatus.SUSPENDED,
        )
        await self._audit(
            "tenant_reactivated" if active else "tenant_suspended",
            admin=principal.admin,
            ctx=ctx,
            target_tenant_id=tenant.id,
            target_type="workspace",
            target_id=str(tenant.id),
            detail={
                "reason": reason[:500],
                "before": {"status": before[0], "active": before[1]},
                "after": {"status": tenant.status.value, "active": active},
            },
        )
        self.logger.info("platform_tenant_state_changed", tenant_id=str(tenant.id), active=active)
        rows, _ = await PlatformTenantDirectory(self.session).page(
            page=1, size=1, tenant_id=tenant.id
        )
        return rows[0]

    async def tenant_health(self, tenant_id: uuid.UUID) -> TenantHealth:
        if await TenantRepository(self.session).get_by_id(tenant_id) is None:
            raise NotFoundError.for_resource("Workspace", tenant_id)
        return await PlatformTenantDirectory(self.session).health(tenant_id)

    async def recent_audit(self, *, limit: int) -> list[PlatformAdminAudit]:
        return await self.audit.recent(limit=limit)


__all__ = [
    "AuditContext",
    "NewPlatformAdmin",
    "PlatformAdminService",
    "PlatformPrincipal",
]
