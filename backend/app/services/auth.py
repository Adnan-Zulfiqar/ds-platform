"""Authentication service.

Owns registration, sign-in, token rotation, and sign-out. Raises domain
exceptions and knows nothing about HTTP — the router translates.

Two security properties are implemented here rather than left to callers, since
both are easy to omit and neither fails visibly when omitted:

* **Uniform failure.** Every unsuccessful sign-in raises the same
  ``InvalidCredentialsError`` with the same message, whether the address is
  unknown, the password is wrong, the account is disabled, or the tenant is
  suspended. Distinguishing them would turn the login form into an account
  enumeration oracle.

* **Refresh token rotation with reuse detection.** Each refresh consumes the
  presented token and issues a new one. Presenting an already-consumed token
  means it was captured, so every session for that user is terminated.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.context import AuthenticatedUser, set_principal, set_tenant_id
from app.core.exceptions import (
    ConflictError,
    InvalidCredentialsError,
    ValidationError,
)
from app.core.password import (
    hash_password,
    needs_rehash,
    validate_password_strength,
    verify_password,
)
from app.core.tokens import (
    IssuedToken,
    TokenType,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.models.role import RoleName
from app.models.tenant import Tenant, TenantStatus
from app.models.user import User
from app.repositories.refresh_token import RefreshTokenRepository
from app.repositories.role import RoleRepository
from app.repositories.tenant import TenantRepository
from app.repositories.user import (
    AuthenticationUserRepository,
    UserRepository,
    normalise_email,
)
from app.services.base import BaseService
from app.services.login_throttle import LoginThrottle
from app.utils.strings import slugify


@dataclass(frozen=True, slots=True)
class TokenPair:
    """A freshly issued access and refresh token."""

    access_token: str
    refresh_token: str
    access_expires_at: datetime
    refresh_expires_at: datetime
    # The OAuth 2 token *type* label, not a secret — hence the suppression.
    token_type: str = "bearer"  # noqa: S105

    @property
    def expires_in_seconds(self) -> int:
        """Access token lifetime remaining, for OAuth-style clients."""
        return max(int((self.access_expires_at - datetime.now(UTC)).total_seconds()), 0)


@dataclass(frozen=True, slots=True)
class AuthResult:
    """Outcome of a successful registration or sign-in."""

    user: User
    tenant: Tenant
    roles: frozenset[str]
    tokens: TokenPair


class AuthService(BaseService):
    """Registration, sign-in, rotation, sign-out."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.tenants = TenantRepository(session)
        self.roles = RoleRepository(session)
        self.refresh_tokens = RefreshTokenRepository(session)
        self.auth_users = AuthenticationUserRepository(session)
        self.throttle = LoginThrottle()

    # -- Registration -------------------------------------------------------

    async def register(
        self,
        *,
        company_name: str,
        email: str,
        password: str,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> AuthResult:
        """Create a tenant and its first user, who becomes the owner.

        Tenant and user are created in one transaction. A half-completed
        registration — a tenant with no user — would be an account nobody can
        sign in to and nobody can clean up, so partial success is not allowed.
        """
        normalised_email = normalise_email(email)

        # Validate before hashing: Argon2 is deliberately expensive, and there
        # is no reason to spend that on a password that is about to be rejected.
        validate_password_strength(password, email=normalised_email)

        existing = await self.auth_users.find_by_email(normalised_email)
        if existing:
            # Registration cannot avoid disclosing that an address is taken —
            # the account must be creatable or not. The mitigation is that this
            # endpoint is rate limited; the real fix is to always report success
            # and send a "someone tried to register with your address" email,
            # which needs mail delivery this phase does not have.
            raise ConflictError("An account with this email address already exists.")

        tenant = await self._create_tenant(company_name)

        # Bind context so the tenant-scoped user repository can be used for the
        # insert. The alternative — an unscoped write — would mean the very
        # first row created for a customer bypasses the isolation mechanism.
        set_tenant_id(tenant.id)
        users = UserRepository(self.session)

        user = await users.create(
            email=normalised_email,
            first_name=first_name,
            last_name=last_name,
            password_hash=hash_password(password),
            is_active=True,
            # True because no mail delivery exists yet to verify against. When
            # the verification flow lands this becomes False and registration
            # sends a confirmation.
            # Verified until a mail provider exists and enforcement is enabled.
            # Flipping the default without mail would lock every signup out.
            is_verified=not settings.security.require_email_verification,
        )

        await self.roles.assign_by_name(user_id=user.id, name=RoleName.OWNER)
        role_names = frozenset({RoleName.OWNER.value})

        tokens = await self._issue_tokens(user=user, roles=role_names)
        self._bind_principal(user=user, roles=role_names)

        self.logger.info(
            "tenant_registered",
            tenant_id=str(tenant.id),
            tenant_slug=tenant.slug,
            user_id=str(user.id),
        )
        return AuthResult(user=user, tenant=tenant, roles=role_names, tokens=tokens)

    async def _create_tenant(self, company_name: str) -> Tenant:
        """Create a tenant with a unique slug derived from its name."""
        name = company_name.strip()
        if not name:
            raise ValidationError("Company name is required.")

        base_slug = slugify(name)
        if not base_slug:
            # slugify legitimately returns empty for input with no ASCII-mappable
            # characters — a name written entirely in a non-Latin script, for
            # example. Falling back keeps registration working for those
            # customers instead of rejecting a perfectly valid company name.
            base_slug = "tenant"

        slug = await self._unique_slug(base_slug)

        return await self.tenants.create(
            name=name,
            slug=slug,
            status=TenantStatus.TRIAL,
            is_active=True,
        )

    async def _unique_slug(self, base: str, *, max_attempts: int = 50) -> str:
        """Find an unused slug, appending a numeric suffix as needed.

        Two customers called "Acme" is ordinary, not exceptional. The suffix
        keeps the readable prefix rather than falling straight back to a random
        string, because the slug becomes the customer's subdomain.

        A losing race still fails safely: ``tenants.slug`` is unique in the
        database, so a concurrent insert raises ``ConflictError`` from the
        repository rather than producing a duplicate.
        """
        # 63 is the DNS label limit enforced by the CHECK constraint on the
        # column; leave room for the "-NN" suffix.
        trimmed = base[:58].rstrip("-")

        if not await self.tenants.slug_exists(trimmed):
            return trimmed

        for suffix in range(2, max_attempts + 1):
            candidate = f"{trimmed}-{suffix}"
            if not await self.tenants.slug_exists(candidate):
                return candidate

        # Fifty collisions means either a very popular company name or an abuse
        # pattern. A random suffix is guaranteed to terminate.
        return f"{trimmed}-{uuid.uuid4().hex[:8]}"

    # -- Sign-in ------------------------------------------------------------

    async def login(self, *, email: str, password: str, client_ip: str | None = None) -> AuthResult:
        """Authenticate a user and issue tokens."""
        normalised_email = normalise_email(email)

        # Before password verification, so a throttled caller never reaches the
        # expensive hash comparison.
        await self.throttle.check(email=normalised_email, client_ip=client_ip)

        user = await self._authenticate(normalised_email, password)
        if user is None:
            await self.throttle.record_failure(email=normalised_email, client_ip=client_ip)
            # Identical for every failure reason — see the module docstring.
            raise InvalidCredentialsError("Incorrect email address or password.")

        tenant = await self.tenants.get_by_id(user.tenant_id)
        if tenant is None or not tenant.is_active:
            # A disabled tenant is reported as a credential failure rather than
            # "your company account is suspended", which would confirm the
            # address is real to anyone probing it.
            await self.throttle.record_failure(email=normalised_email, client_ip=client_ip)
            self.logger.warning(
                "login_rejected_inactive_tenant",
                user_id=str(user.id),
                tenant_id=str(user.tenant_id),
            )
            raise InvalidCredentialsError("Incorrect email address or password.")

        await self.throttle.clear(email=normalised_email, client_ip=client_ip)

        set_tenant_id(tenant.id)
        role_names = await self.roles.list_role_names_for_user(user.id)

        user.last_login_at = datetime.now(UTC)
        await self.session.flush()

        tokens = await self._issue_tokens(user=user, roles=role_names)
        self._bind_principal(user=user, roles=role_names)

        self.logger.info("login_succeeded", user_id=str(user.id), tenant_id=str(tenant.id))
        return AuthResult(user=user, tenant=tenant, roles=role_names, tokens=tokens)

    async def _authenticate(self, normalised_email: str, password: str) -> User | None:
        """Return the user whose credentials match, or ``None``.

        Handles the case where one address exists in several tenants by testing
        the password against each candidate. That is correct rather than merely
        convenient: the password identifies *which* account is being signed
        into.

        **Known limitation.** If a person genuinely reuses one password across
        two tenants, the earliest-created account wins and there is no way to
        reach the other from this endpoint. The fix is tenant-qualified login
        (subdomain, or a tenant picker after the password is verified); it is
        recorded in docs/Authentication.md rather than guessed at here.
        """
        candidates = await self.auth_users.find_by_email(normalised_email)

        if not candidates:
            # Spend the same CPU as a real verification so that an unknown
            # address is not measurably faster to reject than a known one.
            verify_password(password, None)
            return None

        for candidate in candidates:
            if not verify_password(password, candidate.password_hash):
                continue
            if not candidate.is_active:
                self.logger.warning("login_rejected_inactive_user", user_id=str(candidate.id))
                return None

            # The plaintext is only in memory at this instant, so this is the
            # single opportunity to upgrade a hash written with weaker
            # parameters.
            if candidate.password_hash and needs_rehash(candidate.password_hash):
                candidate.password_hash = hash_password(password)
                await self.session.flush()
                self.logger.info("password_hash_upgraded", user_id=str(candidate.id))

            return candidate

        return None

    # -- Token rotation -----------------------------------------------------

    async def refresh(self, refresh_token: str) -> AuthResult:
        """Exchange a refresh token for a new pair, consuming the old one.

        **Rotation.** The presented token is revoked as the new one is issued,
        so a captured token is useful only until the legitimate client next
        refreshes.

        **Reuse detection.** A token that is already revoked being presented
        again means two parties hold it — the legitimate client and someone
        else. There is no way to tell which is which, so every session for the
        user is terminated and both must sign in again. Locking the real user
        out briefly is a far better outcome than leaving an attacker with a
        live session.
        """
        claims = decode_token(refresh_token, expected_type=TokenType.REFRESH)
        record = await self.refresh_tokens.get_by_token(refresh_token)

        if record is None:
            # Correctly signed and unexpired, but no stored record. Either it
            # was purged after expiry, or the signing key has leaked and someone
            # is minting tokens.
            self.logger.warning("refresh_token_not_recognised", jti=claims.jti)
            raise InvalidCredentialsError("The session is no longer valid. Please sign in again.")

        if record.is_revoked:
            revoked_count = await self.refresh_tokens.revoke_all_for_user(record.user_id)
            self.logger.error(
                "refresh_token_reuse_detected",
                user_id=str(record.user_id),
                jti=claims.jti,
                sessions_terminated=revoked_count,
            )
            raise InvalidCredentialsError("The session is no longer valid. Please sign in again.")

        if record.is_expired():
            raise InvalidCredentialsError("The session has expired. Please sign in again.")

        user = await self.auth_users.get_by_id_unscoped(record.user_id)
        if user is None or not user.is_active:
            await self.refresh_tokens.revoke_all_for_user(record.user_id)
            raise InvalidCredentialsError("The session is no longer valid. Please sign in again.")

        tenant = await self.tenants.get_by_id(user.tenant_id)
        if tenant is None or not tenant.is_active:
            await self.refresh_tokens.revoke_all_for_user(user.id)
            raise InvalidCredentialsError("The session is no longer valid. Please sign in again.")

        # Consume the old token before issuing the new one. If anything below
        # fails the transaction rolls back and the old token stays live, so a
        # failed refresh cannot strand a client with no usable session.
        record.revoke()

        set_tenant_id(tenant.id)
        # Roles are re-read rather than copied from the old token, so a role
        # change takes effect at the next refresh instead of persisting for the
        # full refresh-token lifetime.
        role_names = await self.roles.list_role_names_for_user(user.id)

        tokens = await self._issue_tokens(user=user, roles=role_names)
        self._bind_principal(user=user, roles=role_names)

        self.logger.info("token_refreshed", user_id=str(user.id))
        return AuthResult(user=user, tenant=tenant, roles=role_names, tokens=tokens)

    # -- Sign-out -----------------------------------------------------------

    async def logout(self, refresh_token: str | None) -> None:
        """Revoke the presented refresh token.

        Never raises. Logout must succeed from the client's point of view
        whatever state the token is in — a user clicking "sign out" and getting
        an error, then assuming they are still signed in, is worse than a
        no-op. An absent or unrecognised token simply has nothing to revoke.

        The access token remains valid until it expires; that is inherent to
        stateless tokens and is why the access lifetime is short.
        """
        if not refresh_token:
            return

        record = await self.refresh_tokens.get_by_token(refresh_token)
        if record is None:
            return

        record.revoke()
        await self.session.flush()
        self.logger.info("logout", user_id=str(record.user_id))

    async def logout_all_sessions(self, user_id: uuid.UUID) -> int:
        """Revoke every live session for a user. Returns the number revoked."""
        count = await self.refresh_tokens.revoke_all_for_user(user_id)
        self.logger.info("all_sessions_revoked", user_id=str(user_id), count=count)
        return count

    # -- Helpers ------------------------------------------------------------

    async def _issue_tokens(self, *, user: User, roles: frozenset[str]) -> TokenPair:
        """Mint an access and refresh token pair and persist the refresh record."""
        access: IssuedToken = create_access_token(
            user_id=user.id,
            tenant_id=user.tenant_id,
            roles=tuple(sorted(roles)),
            is_verified=user.is_verified,
        )
        # Each token carries a fresh random `jti`, so two refresh tokens issued
        # in the same second for the same user are distinct and their hashes
        # cannot collide on the unique constraint.
        refresh: IssuedToken = create_refresh_token(user_id=user.id, tenant_id=user.tenant_id)

        await self.refresh_tokens.create_for_user(
            user_id=user.id,
            token=refresh.token,
            expires_at=refresh.expires_at,
        )

        return TokenPair(
            access_token=access.token,
            refresh_token=refresh.token,
            access_expires_at=access.expires_at,
            refresh_expires_at=refresh.expires_at,
        )

    @staticmethod
    def _bind_principal(*, user: User, roles: frozenset[str]) -> None:
        """Bind the authenticated identity for the remainder of the request."""
        set_principal(
            AuthenticatedUser(
                user_id=user.id,
                tenant_id=user.tenant_id,
                email=user.email,
                roles=roles,
                is_active=user.is_active,
                is_verified=user.is_verified,
            )
        )


__all__ = ["AuthResult", "AuthService", "TokenPair"]
