"""Platform operator identity (Track E5, decision D-015).

**Sign-in needs three things:** a password (Argon2id, as for tenant users), a
current TOTP code, and an address inside ``PLATFORM_ADMIN_ALLOWED_CIDRS``
(checked by the dependency before this service runs). Every failure gets the
same message, whether the address is unknown, the password wrong, the code
wrong or reused, or the account disabled.

**Failed attempts are audited in their own transaction.** The request's
transaction rolls back when sign-in fails; an audit row written there would
vanish with it, and a log of failures that records no failures is worse than
none.

**Accounts are created only by** ``scripts/create_platform_admin.py`` on the
server. There is no web path that creates one.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import totp
from app.core.encryption import (
    EncryptionNotConfiguredError,
    decrypt,
    encrypt,
    is_encryption_configured,
)
from app.core.exceptions import AuthenticationError, ConflictError, NotFoundError
from app.core.password import hash_password, validate_password_strength, verify_password
from app.core.tokens import IssuedToken, create_platform_token
from app.database.session import transaction
from app.models.platform_admin import PlatformAdmin, PlatformAdminAudit
from app.models.tenant import TenantStatus
from app.repositories.platform_admin import (
    PlatformAdminAuditRepository,
    PlatformAdminRepository,
    PlatformTenantDirectory,
    TenantDirectoryRow,
    TenantHealth,
)
from app.repositories.tenant import TenantRepository
from app.services.base import BaseService

_REFUSED = "Invalid sign-in details."


@dataclass(frozen=True, slots=True)
class NewPlatformAdmin:
    admin: PlatformAdmin
    totp_secret: str
    provisioning_uri: str


class PlatformAdminService(BaseService):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.admins = PlatformAdminRepository(session)
        self.audit = PlatformAdminAuditRepository(session)

    async def create_admin(self, *, email: str, password: str) -> NewPlatformAdmin:
        """For the CLI only. Returns the TOTP secret once; it is stored
        encrypted and never shown again."""
        if not is_encryption_configured():
            raise EncryptionNotConfiguredError()
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
        )
        await self.audit.append(action="admin_created", admin_id=admin.id, client_ip=None)
        return NewPlatformAdmin(
            admin=admin,
            totp_secret=secret,
            provisioning_uri=totp.provisioning_uri(secret, account=address),
        )

    async def authenticate(
        self, *, email: str, password: str, code: str, client_ip: str | None
    ) -> IssuedToken:
        admin = await self.admins.get_by_email(email)
        # Spend the same Argon2 work for an unknown address as for a known one.
        password_ok = verify_password(password, admin.password_hash if admin else None)
        if admin is None or not password_ok or not admin.is_active:
            await self._audit_failure(admin, client_ip, reason="credentials")
            raise AuthenticationError(_REFUSED)

        locked = await self.admins.get_for_update(admin.id)
        if locked is None:  # deleted between the two reads
            raise AuthenticationError(_REFUSED)
        step = totp.verify(decrypt(locked.encrypted_totp_secret), code)
        if step is None or (locked.totp_last_step is not None and step <= locked.totp_last_step):
            await self._audit_failure(admin, client_ip, reason="totp")
            raise AuthenticationError(_REFUSED)

        locked.totp_last_step = step
        locked.last_login_at = datetime.now(UTC)
        await self.audit.append(action="login_succeeded", admin_id=admin.id, client_ip=client_ip)
        await self.session.flush()
        self.logger.info("platform_admin_signed_in", admin_id=str(admin.id))
        return create_platform_token(admin_id=admin.id)

    async def _audit_failure(
        self, admin: PlatformAdmin | None, client_ip: str | None, *, reason: str
    ) -> None:
        """Committed independently of the failing request (see module doc)."""
        admin_id = admin.id if admin is not None else None
        try:
            async with transaction() as session:
                await PlatformAdminAuditRepository(session).append(
                    action="login_failed",
                    admin_id=admin_id,
                    client_ip=client_ip,
                    detail={"reason": reason},
                )
        except Exception:  # an audit outage must not turn a refusal into a 500
            self.logger.exception("platform_admin_audit_write_failed")
        self.logger.warning("platform_admin_sign_in_refused", reason=reason)

    # --- Workspaces (E5b) ----------------------------------------------------

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
        admin: PlatformAdmin,
        client_ip: str | None,
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
        await self.audit.append(
            action="tenant_reactivated" if active else "tenant_suspended",
            admin_id=admin.id,
            client_ip=client_ip,
            target_tenant_id=tenant.id,
            detail={"reason": reason[:500], "before": {"status": before[0], "active": before[1]}},
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

    async def active_admin(self, admin_id: uuid.UUID) -> PlatformAdmin:
        admin = await self.admins.get_by_id(admin_id)
        if admin is None or not admin.is_active:
            raise AuthenticationError("The authentication token is not valid.")
        return admin


__all__ = ["NewPlatformAdmin", "PlatformAdminService"]
