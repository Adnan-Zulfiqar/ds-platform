"""Sign-in and account linking with Google.

Sits on top of `AuthService` rather than beside it: a Google sign-in ends with
exactly the same DropPilot session, refresh token and role binding as a password
sign-in. There is one session authority, and this is not a second one.

### The rule that matters most: no automatic linking by email

If somebody signs in with Google using an address that already has a local
DropPilot account, this **refuses** and says so. It does not helpfully attach
the Google identity to that account.

The reason is that email is a claim, not a proof of continuity. Google verifies
that the person controls the address *today*; it says nothing about whether they
are the person who registered it here. Addresses are recycled, corporate
accounts change hands, and an attacker who obtains a Google account for
`someone@company.com` should not thereby obtain the DropPilot workspace of
whoever held that address before them. Linking is therefore an act the existing
account owner performs while signed in — which proves continuity in a way an
email match never can.

The refusal is also careful about what it reveals. It is returned only when the
Google address matches a local account, so it tells the person exactly what they
need to know and nothing about any other workspace.

### What is never stored

No Google ID token, access token or refresh token. The credential is verified,
its claims are used, and it is discarded. `provider_email` is kept for display
only — nothing matches on it.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthenticationError, ConflictError, PermissionDeniedError
from app.core.logging import get_logger
from app.integrations.google import GoogleIdentity
from app.models.identity import IdentityProvider, UserIdentity
from app.models.user import User
from app.repositories.user import normalise_email
from app.services.auth import AuthResult, AuthService, LegalAcceptance

logger = get_logger(__name__)

__all__ = [
    "GoogleAccountConflictError",
    "GoogleAuthService",
    "GoogleSignInUnavailableError",
]


class GoogleAccountConflictError(ConflictError):
    """The address already has a local account that must link deliberately.

    A distinct type so the API can return a specific, actionable code — the
    person needs to be told to sign in and link, not handed a generic failure
    they cannot act on.
    """

    code = "google_account_requires_linking"


class GoogleSignInUnavailableError(AuthenticationError):
    """No linked identity. Deliberately indistinguishable from other failures."""


class GoogleAuthService:
    """Sign in, sign up, link and unlink via Google."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._auth = AuthService(session)

    # ------------------------------------------------------------- lookups

    async def _identity_for(self, subject: str) -> UserIdentity | None:
        return (
            await self._session.execute(
                select(UserIdentity).where(
                    UserIdentity.provider == IdentityProvider.GOOGLE.value,
                    UserIdentity.subject == subject,
                )
            )
        ).scalar_one_or_none()

    async def _local_user_for(self, email: str) -> User | None:
        """Unscoped by necessity: sign-in happens before a tenant is known.

        The same exception `AuthenticationUserRepository` documents. The result
        is never returned to the caller — only whether it exists influences the
        response.
        """
        return (
            await self._session.execute(
                select(User).where(
                    func.lower(User.email) == email.strip().lower(),
                    User.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()

    # ------------------------------------------------------------- sign-in
    #
    # Login and signup are separate operations, not one endpoint that guesses.
    # The combined version meant a request intended as a sign-in could silently
    # create an account and a tenant — so a typo, or a credential for an address
    # nobody here had ever seen, provisioned a workspace. Each now refuses to do
    # the other's job.

    async def login(self, identity: GoogleIdentity) -> AuthResult:
        """Authenticate an already-linked Google account.

        **Creates nothing.** No user, no tenant, no identity. An unknown subject
        is refused, whether or not a local account happens to share the address.
        """
        existing = await self._identity_for(identity.subject)
        if existing is None:
            # Identical refusal whether the address is unknown or belongs to a
            # local account: distinguishing them is an existence oracle.
            raise GoogleSignInUnavailableError(
                "No DropPilot account is connected to that Google account."
            )

        user = (
            await self._session.execute(select(User).where(User.id == existing.user_id))
        ).scalar_one()
        if not user.is_active or user.deleted_at is not None:
            raise PermissionDeniedError("This account is not available.")

        existing.last_authenticated_at = func.now()
        # The address at Google may have changed since linking. That is fine and
        # must not create a second account — the subject identifies the person.
        if identity.email != (existing.provider_email or ""):
            existing.provider_email = identity.email
        await self._session.flush()

        auth = await self._auth.authenticate_verified_user(user)
        logger.info("google_login", user_id=str(user.id))
        return auth

    async def signup(
        self,
        identity: GoogleIdentity,
        *,
        acceptance: LegalAcceptance,
        company_name: str | None = None,
    ) -> AuthResult:
        """Register a new workspace from a verified Google credential.

        **Never behaves as login.** A subject that already has an identity is
        refused rather than quietly signed in, so a signup request cannot be
        used to probe which accounts exist by observing that it "worked".
        """
        if await self._identity_for(identity.subject) is not None:
            raise ConflictError(
                "That Google account is already connected to a DropPilot account. Sign in instead."
            )

        normalised = normalise_email(identity.email)
        if await self._local_user_for(normalised) is not None:
            logger.info("google_signup_requires_linking")
            raise GoogleAccountConflictError(
                "An account already exists for this email address. Sign in with your "
                "password and connect Google from your account settings."
            )

        auth = await self._auth.register_federated_user(
            company_name=company_name or self._default_company_name(identity),
            email=normalised,
            first_name=self._first_name(identity),
            last_name=self._last_name(identity),
            acceptance=acceptance,
        )

        self._session.add(
            UserIdentity(
                user_id=auth.user.id,
                provider=IdentityProvider.GOOGLE.value,
                subject=identity.subject,
                provider_email=identity.email,
                last_authenticated_at=func.now(),
            )
        )
        try:
            await self._session.flush()
        except IntegrityError as exc:
            # Two concurrent first signups for one subject. The unique
            # constraint decides; one loses here rather than a duplicate
            # account being created.
            await self._session.rollback()
            logger.warning("google_signup_raced")
            raise ConflictError("Sign-up could not be completed. Please try again.") from exc

        logger.info("google_signup", user_id=str(auth.user.id), tenant_id=str(auth.tenant.id))
        return auth

    # ------------------------------------------------------- link / unlink

    async def link(self, *, user_id: uuid.UUID, identity: GoogleIdentity) -> UserIdentity:
        """Attach a Google account to the signed-in user."""
        clash = await self._identity_for(identity.subject)
        if clash is not None:
            if clash.user_id == user_id:
                return clash
            # Attached elsewhere. The message says nothing about where: naming
            # the other account would be a cross-tenant disclosure.
            raise ConflictError("This Google account is already linked to another user.")

        already = (
            await self._session.execute(
                select(UserIdentity).where(
                    UserIdentity.user_id == user_id,
                    UserIdentity.provider == IdentityProvider.GOOGLE.value,
                )
            )
        ).scalar_one_or_none()
        if already is not None:
            raise ConflictError("A Google account is already linked to this user.")

        link = UserIdentity(
            user_id=user_id,
            provider=IdentityProvider.GOOGLE.value,
            subject=identity.subject,
            provider_email=identity.email,
            last_authenticated_at=func.now(),
        )
        self._session.add(link)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise ConflictError("This Google account is already linked to another user.") from exc

        logger.info("google_identity_linked", user_id=str(user_id))
        return link

    async def unlink(self, *, user_id: uuid.UUID) -> bool:
        """Detach Google, refusing to leave the account unreachable.

        An account whose only sign-in method is removed is an account nobody can
        get back into, and there is no self-service recovery to fall back on.
        """
        link = (
            await self._session.execute(
                select(UserIdentity).where(
                    UserIdentity.user_id == user_id,
                    UserIdentity.provider == IdentityProvider.GOOGLE.value,
                )
            )
        ).scalar_one_or_none()
        if link is None:
            return False

        user = (await self._session.execute(select(User).where(User.id == user_id))).scalar_one()
        if user.password_hash is None:
            raise ConflictError(
                "Set a password before disconnecting Google, or you will not be able to sign in."
            )

        await self._session.delete(link)
        await self._session.flush()
        logger.info("google_identity_unlinked", user_id=str(user_id))
        return True

    # -------------------------------------------------------------- naming

    @staticmethod
    def _default_company_name(identity: GoogleIdentity) -> str:
        """A workspace name from what Google gave us, never the raw address."""
        if identity.name:
            return f"{identity.name}'s workspace"
        return "My workspace"

    @staticmethod
    def _first_name(identity: GoogleIdentity) -> str | None:
        if not identity.name:
            return None
        return identity.name.split(" ", 1)[0] or None

    @staticmethod
    def _last_name(identity: GoogleIdentity) -> str | None:
        if not identity.name or " " not in identity.name:
            return None
        return identity.name.split(" ", 1)[1] or None
