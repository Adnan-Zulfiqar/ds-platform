"""Team invitations (Track E4).

An owner or admin invites an address with a role. The recipient gets a link,
chooses a password, accepts the terms and becomes a user of that workspace,
signed in straight away.

**How acceptance finds the workspace without a session.** The link carries
``<tenant id>.<secret>``. The tenant id only narrows the search: the
invitation is looked up *inside* that tenant by the SHA-256 of the 256-bit
secret, so a forged or swapped tenant id finds nothing and is a 404 like any
wrong link. The alternative — an unscoped lookup by token hash — would put a
cross-tenant query on a request path, which CLAUDE.md §4 forbids. Recorded as
decision D-013.

**One address, one workspace.** Login resolves a password against every
account with the address, and cannot reach a second account that shares a
password (docs/Authentication.md). Until login is tenant-qualified, accepting
is refused when the address already has an account anywhere. That check
happens at acceptance, to the person who proved they own the address; doing
it at invite time would tell an admin whether a stranger uses DropPilot.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import require_tenant_id, set_tenant_id
from app.core.exceptions import (
    ConflictError,
    ExternalServiceError,
    NotFoundError,
    ValidationError,
)
from app.core.password import hash_password, validate_password_strength
from app.integrations.email.messages import send_team_invitation
from app.integrations.email.provider import EmailDeliveryError
from app.models.invitation import UserInvitation
from app.models.role import RoleName
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.invitation import UserInvitationRepository
from app.repositories.role import RoleRepository
from app.repositories.tenant import TenantRepository
from app.repositories.user import AuthenticationUserRepository, UserRepository, normalise_email
from app.services.auth import AuthResult, AuthService, LegalAcceptance
from app.services.base import BaseService

INVITATION_TTL: Final = timedelta(days=7)
#: Open, unexpired invitations per workspace. A cap on how much mail one
#: compromised admin account can send in the workspace's name.
MAX_OPEN_INVITATIONS: Final = 50
_INVITABLE: Final = frozenset({RoleName.ADMIN, RoleName.MEMBER, RoleName.VIEWER})
_LINK_INVALID: Final = "This invitation link is invalid, expired or already used."


class InvitationEmailError(ExternalServiceError):
    code = "invitation_email_failed"
    status_code = 503
    message = "The invitation email could not be sent. Please try again shortly."


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _split(token: str) -> tuple[uuid.UUID, str]:
    tenant_part, _, secret = token.strip().partition(".")
    try:
        tenant_id = uuid.UUID(tenant_part)
    except ValueError:
        raise NotFoundError(_LINK_INVALID) from None
    if len(secret) < 32:
        raise NotFoundError(_LINK_INVALID)
    return tenant_id, secret


@dataclass(frozen=True, slots=True)
class InvitationPreview:
    email: str
    role: str
    workspace_name: str
    expires_at: datetime


class TeamInvitationService(BaseService):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.invitations = UserInvitationRepository(session)
        self.tenants = TenantRepository(session)
        self.roles = RoleRepository(session)

    # --- admin side (authenticated, tenant from the token claim) ------------

    async def invite(self, *, email: str, role: RoleName, inviter: User) -> UserInvitation:
        """Create an invitation, or refresh the open one for this address.

        Refreshing rotates the secret, so a re-sent email makes the old link
        dead rather than leaving two live ones.
        """
        # The endpoint requires admin or above, and admin is the highest role
        # on offer, so nobody can invite above their own rank.
        if role not in _INVITABLE:
            raise ValidationError("Ownership cannot be granted by invitation.")

        address = normalise_email(email)
        if await UserRepository(self.session).email_taken(address):
            raise ConflictError("This person is already a member of the workspace.")

        existing = await self.invitations.open_for_email(address)
        if existing is None and (
            await self.invitations.count_open_unexpired() >= MAX_OPEN_INVITATIONS
        ):
            raise ConflictError("Too many open invitations. Revoke some before sending more.")

        # Send first, write second. If the send fails nothing is written, so
        # there is no invitation that nobody received. If the write fails
        # after a send, the emailed link simply does not work, which is
        # harmless. The reverse order would depend on the caller rolling back.
        secret = secrets.token_urlsafe(32)
        tenant_id = require_tenant_id()
        tenant = await self.tenants.get_by_id(tenant_id)
        try:
            await send_team_invitation(
                email=address,
                workspace_name=tenant.name if tenant is not None else "a workspace",
                inviter_name=inviter.full_name,
                token=f"{tenant_id}.{secret}",
                days_valid=INVITATION_TTL.days,
            )
        except EmailDeliveryError:
            raise InvitationEmailError(service="email") from None

        values = {
            "role": role.value,
            "token_hash": _hash(secret),
            "invited_by_user_id": inviter.id,
            "expires_at": datetime.now(UTC) + INVITATION_TTL,
        }
        if existing is None:
            invitation = await self.invitations.create(email=address, **values)
        else:
            invitation = await self.invitations.update(existing, **values)
        self.logger.info("team_invitation_sent", invitation_id=str(invitation.id), role=role.value)
        return invitation

    async def list_open(self) -> list[UserInvitation]:
        return await self.invitations.list_open(limit=MAX_OPEN_INVITATIONS * 2)

    async def revoke(self, invitation_id: uuid.UUID) -> None:
        invitation = await self.invitations.get_by_id(invitation_id)
        if invitation is None or invitation.accepted_at or invitation.revoked_at:
            raise NotFoundError.for_resource("Invitation", invitation_id)
        await self.invitations.update(invitation, revoked_at=datetime.now(UTC))

    # --- recipient side (unauthenticated, tenant from the link) -------------

    async def _resolve(self, token: str, *, lock: bool) -> tuple[UserInvitation, Tenant]:
        tenant_id, secret = _split(token)
        tenant = await self.tenants.get_by_id(tenant_id)
        if tenant is None or not tenant.is_active:
            raise NotFoundError(_LINK_INVALID)
        set_tenant_id(tenant.id)
        invitation = await self.invitations.open_by_token_hash(_hash(secret), lock=lock)
        if invitation is None or invitation.expires_at <= datetime.now(UTC):
            raise NotFoundError(_LINK_INVALID)
        return invitation, tenant

    async def preview(self, token: str) -> InvitationPreview:
        invitation, tenant = await self._resolve(token, lock=False)
        return InvitationPreview(
            email=invitation.email,
            role=invitation.role,
            workspace_name=tenant.name,
            expires_at=invitation.expires_at,
        )

    async def accept(
        self,
        *,
        token: str,
        password: str,
        acceptance: LegalAcceptance,
        first_name: str | None,
        last_name: str | None,
    ) -> AuthResult:
        acceptance.require_valid()
        invitation, _tenant = await self._resolve(token, lock=True)
        validate_password_strength(password, email=invitation.email)

        if await AuthenticationUserRepository(self.session).find_by_email(invitation.email):
            raise ConflictError(
                "This email address already has a DropPilot account. Ask the workspace "
                "admin to invite a different address."
            )

        now = datetime.now(UTC)
        user = await UserRepository(self.session).create(
            email=invitation.email,
            first_name=first_name,
            last_name=last_name,
            password_hash=hash_password(password),
            is_active=True,
            # The link reached this address, which is what verification proves.
            is_verified=True,
            terms_accepted_at=now,
            terms_version=acceptance.terms_version,
            privacy_accepted_at=now,
            privacy_version=acceptance.privacy_version,
        )
        await self.roles.assign_by_name(user_id=user.id, name=RoleName(invitation.role))
        await self.invitations.update(invitation, accepted_at=now, accepted_user_id=user.id)
        self.logger.info(
            "team_invitation_accepted", invitation_id=str(invitation.id), user_id=str(user.id)
        )
        return await AuthService(self.session).authenticate_verified_user(user)


__all__ = [
    "INVITATION_TTL",
    "MAX_OPEN_INVITATIONS",
    "InvitationEmailError",
    "InvitationPreview",
    "TeamInvitationService",
]
