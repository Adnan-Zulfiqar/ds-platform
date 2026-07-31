"""AliExpress connection service.

Orchestrates the OAuth flow and connection lifecycle. Knows nothing about HTTP —
the router translates — so the same service can be driven by a Celery task,
which is exactly what the health check does.

**Where the security properties of this phase live:**

* Credentials are encrypted before they reach the database and decrypted only at
  the moment a request is signed.
* The OAuth ``state`` is verified server-side against a Redis record. Without
  it, an attacker could complete a consent flow with their own AliExpress
  account and deliver the code to a victim's browser, binding the attacker's
  supplier account to the victim's workspace.
* Disconnecting deletes the row rather than soft-deleting it. A customer who
  disconnects has asked us to forget their credentials.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.context import require_tenant_id
from app.core.encryption import (
    EncryptionNotConfiguredError,
    decrypt,
    encrypt,
    is_encryption_configured,
)
from app.core.exceptions import ValidationError
from app.core.redis import RedisPurpose, get_redis
from app.integrations.aliexpress.auth import (
    OAuthState,
    build_authorization_url,
    resolve_token_expiry,
)
from app.integrations.aliexpress.client import AliExpressClient
from app.integrations.aliexpress.exceptions import (
    AliExpressAuthError,
    AliExpressError,
    AliExpressNotConnectedError,
    AliExpressOAuthStateError,
)
from app.integrations.aliexpress.schemas import AliExpressTokenResponse
from app.models.integration import AliExpressConnection, IntegrationStatus
from app.repositories.integration import AliExpressConnectionRepository
from app.services.base import BaseService

_STATE_KEY_PREFIX = "aliexpress:oauth:state:"


class AliExpressService(BaseService):
    """Connection lifecycle: connect, complete, inspect, disconnect, refresh."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.connections = AliExpressConnectionRepository(session)

    # -- Connect ------------------------------------------------------------

    async def begin_connection(
        self, *, app_key: str, app_secret: str, user_id: uuid.UUID | None
    ) -> tuple[str, str]:
        """Store credentials and return an authorization URL and state token.

        The app secret is encrypted and persisted **before** the user is sent to
        AliExpress, because the callback arrives on a different request with no
        access to it — signing the token exchange requires the secret, and there
        is nowhere else to keep it in the meantime.

        The connection is created in ``PENDING`` and only becomes ``CONNECTED``
        once a token comes back, so an abandoned consent screen is
        distinguishable from a failure.
        """
        if not is_encryption_configured():
            # Refusing beats storing a customer's supplier secret in plaintext
            # because a key was missing.
            raise EncryptionNotConfiguredError()

        app_key = app_key.strip()
        if not app_key:
            raise ValidationError("An AliExpress app key is required.")
        if not app_secret.strip():
            raise ValidationError("An AliExpress app secret is required.")

        tenant_id = require_tenant_id()
        existing = await self.connections.get_for_tenant()

        if existing is not None:
            # Reconnecting replaces the credentials in place. The unique
            # constraint permits one connection per tenant, and deleting then
            # recreating would lose `created_at`.
            await self.connections.update(
                existing,
                app_key=app_key,
                encrypted_app_secret=encrypt(app_secret),
                encrypted_access_token=None,
                encrypted_refresh_token=None,
                token_expiry=None,
                status=IntegrationStatus.PENDING,
                last_error=None,
                user_id=user_id,
            )
        else:
            await self.connections.create(
                app_key=app_key,
                encrypted_app_secret=encrypt(app_secret),
                status=IntegrationStatus.PENDING,
                user_id=user_id,
            )

        state = OAuthState.create(
            tenant_id=str(tenant_id), user_id=str(user_id) if user_id else None
        )
        await self._store_state(state)

        self.logger.info("aliexpress_connection_started", tenant_id=str(tenant_id))
        return build_authorization_url(app_key=app_key, state=state.token), state.token

    async def complete_connection(self, *, code: str, state_token: str) -> AliExpressConnection:
        """Exchange the authorization code for tokens and mark the connection live.

        The state is consumed on lookup, so a replayed callback fails. That is
        deliberate: a code can only be exchanged once, and a second callback
        carrying the same state is either a duplicate submission or an attack.
        """
        state = await self._consume_state(state_token)

        tenant_id = require_tenant_id()
        if state.tenant_id != str(tenant_id):
            # The state was issued for a different workspace. Either a stale
            # session or an attempt to attach an account across tenants.
            self.logger.error(
                "aliexpress_state_tenant_mismatch",
                expected=state.tenant_id,
                actual=str(tenant_id),
            )
            raise AliExpressOAuthStateError()

        connection = await self.connections.get_for_tenant()
        if connection is None:
            raise AliExpressNotConnectedError(
                "No pending AliExpress connection was found. Please start again."
            )

        app_secret = decrypt(connection.encrypted_app_secret)
        client = AliExpressClient(
            app_key=connection.app_key,
            app_secret=app_secret,
            tenant_id=str(tenant_id),
        )

        try:
            payload = await client.exchange_token(
                settings.aliexpress.token_url,
                {
                    "code": code,
                    "grant_type": "authorization_code",
                    "need_refresh_token": "true",
                    "redirect_uri": settings.aliexpress.redirect_uri,
                },
            )
        except AliExpressError as exc:
            await self._mark_error(connection, exc.message)
            raise

        token = self._parse_token(payload)
        await self._store_token(connection, token)

        self.logger.info(
            "aliexpress_connected",
            tenant_id=str(tenant_id),
            connection_id=str(connection.id),
        )
        return connection

    # -- Inspect ------------------------------------------------------------

    async def get_connection(self) -> AliExpressConnection | None:
        """The current tenant's connection, or ``None``."""
        return await self.connections.get_for_tenant()

    async def require_connection(self) -> AliExpressConnection:
        """The current tenant's connection, raising if absent or unusable."""
        connection = await self.connections.get_for_tenant()
        if connection is None:
            raise AliExpressNotConnectedError()
        return connection

    # -- Disconnect ---------------------------------------------------------

    async def disconnect(self) -> bool:
        """Remove the connection. Returns whether one existed.

        A hard delete. The customer asked us to forget their credentials, and
        retaining an encrypted copy is retention they did not request — it
        enlarges the blast radius of any future key compromise for no benefit.
        The *event* is recorded here in the log; the secret is not kept.

        Idempotent: disconnecting when nothing is connected is a no-op, so a
        double click cannot produce an error.
        """
        connection = await self.connections.get_for_tenant()
        if connection is None:
            return False

        connection_id = connection.id
        await self.connections.hard_delete(connection)

        self.logger.info(
            "aliexpress_disconnected",
            tenant_id=str(require_tenant_id()),
            connection_id=str(connection_id),
        )
        return True

    # -- Tokens -------------------------------------------------------------

    async def refresh_if_needed(self, connection: AliExpressConnection) -> AliExpressConnection:
        """Refresh the access token when it is close to expiry.

        Proactive rather than waiting for a 401, so a long-running sync is not
        interrupted partway through by a token that lapsed mid-run.

        A connection with no refresh token cannot be recovered without the user
        re-authorising, so it is marked ``EXPIRED`` rather than left looking
        healthy.
        """
        margin = settings.aliexpress.token_refresh_margin_seconds
        if not connection.expires_within(margin):
            return connection

        if not connection.encrypted_refresh_token:
            await self._mark_status(
                connection,
                IntegrationStatus.EXPIRED,
                "The access token expired and no refresh token is available.",
            )
            return connection

        app_secret = decrypt(connection.encrypted_app_secret)
        refresh_token = decrypt(connection.encrypted_refresh_token)

        client = AliExpressClient(
            app_key=connection.app_key,
            app_secret=app_secret,
            tenant_id=str(connection.tenant_id),
        )

        try:
            payload = await client.exchange_token(
                settings.aliexpress.refresh_url,
                {"refresh_token": refresh_token, "grant_type": "refresh_token"},
            )
        except AliExpressAuthError as exc:
            # The grant was revoked or the credentials changed. Not recoverable
            # automatically — the user must reconnect.
            await self._mark_status(connection, IntegrationStatus.EXPIRED, exc.message)
            raise
        except AliExpressError as exc:
            # Transient. Leave the status alone so a passing outage does not
            # present as a broken connection.
            await self._mark_error(connection, exc.message)
            raise

        await self._store_token(connection, self._parse_token(payload))
        self.logger.info("aliexpress_token_refreshed", connection_id=str(connection.id))
        return connection

    async def check_health(self, connection: AliExpressConnection) -> bool:
        """Verify a connection is usable, refreshing the token if needed.

        Returns a boolean rather than raising: the caller is a scheduled sweep
        across many tenants, and one broken connection must not abort the rest.
        """
        try:
            await self.refresh_if_needed(connection)
        except AliExpressError as exc:
            self.logger.warning(
                "aliexpress_health_check_failed",
                connection_id=str(connection.id),
                reason=exc.code,
            )
            return False

        healthy = connection.is_usable
        if healthy:
            await self.connections.update(
                connection, last_sync_at=datetime.now(UTC), last_error=None
            )
        return healthy

    # -- Internals ----------------------------------------------------------

    @staticmethod
    def _parse_token(payload: dict[str, Any]) -> AliExpressTokenResponse:
        """Extract the token from a response.

        AliExpress nests the payload under a result key on some gateways and
        returns it flat on others, so both shapes are accepted rather than
        assuming one.
        """
        candidate = payload
        for key in ("data", "result", "aliexpress_system_oauth_token_create_response"):
            nested = payload.get(key)
            if isinstance(nested, dict):
                candidate = nested
                break

        try:
            return AliExpressTokenResponse.model_validate(candidate)
        except Exception as exc:
            # The payload is not logged — it contains the token.
            raise AliExpressAuthError("AliExpress did not return a usable access token.") from exc

    async def _store_token(
        self, connection: AliExpressConnection, token: AliExpressTokenResponse
    ) -> None:
        expiry = resolve_token_expiry(expires_in=token.expires_in, expire_time=token.expire_time)

        await self.connections.update(
            connection,
            encrypted_access_token=encrypt(token.access_token),
            encrypted_refresh_token=(encrypt(token.refresh_token) if token.refresh_token else None),
            token_expiry=expiry,
            status=IntegrationStatus.CONNECTED,
            last_error=None,
        )

    async def _mark_status(
        self, connection: AliExpressConnection, status: IntegrationStatus, error: str | None
    ) -> None:
        await self.connections.update(
            connection, status=status, last_error=(error or "")[:512] or None
        )

    async def _mark_error(self, connection: AliExpressConnection, error: str | None) -> None:
        await self.connections.update(
            connection, status=IntegrationStatus.ERROR, last_error=(error or "")[:512] or None
        )

    async def _store_state(self, state: OAuthState) -> None:
        """Persist the OAuth state with a TTL.

        Redis rather than the database: the value is short-lived, single-use,
        and read on a request that has no transaction of its own. The TTL is the
        expiry mechanism, so nothing has to clean it up.
        """
        try:
            await get_redis(RedisPurpose.SESSION).set(
                f"{_STATE_KEY_PREFIX}{state.token}",
                json.dumps(state.to_dict()),
                ex=settings.aliexpress.oauth_state_ttl_seconds,
            )
        except RedisError as exc:
            # Fails closed. Proceeding without a stored state would mean the
            # callback could not verify anything, which removes the CSRF
            # defence entirely — worse than refusing to start the flow.
            self.logger.error("aliexpress_state_store_failed", error=str(exc))
            raise AliExpressOAuthStateError(
                "The authorization flow could not be started. Please try again."
            ) from exc

    async def _consume_state(self, token: str) -> OAuthState:
        """Look up and delete a state token.

        Deleting on read makes it single-use, so a replayed callback fails.
        """
        if not token:
            raise AliExpressOAuthStateError()

        key = f"{_STATE_KEY_PREFIX}{token}"
        try:
            client = get_redis(RedisPurpose.SESSION)
            raw = await client.get(key)
            if raw:
                await client.delete(key)
        except RedisError as exc:
            self.logger.error("aliexpress_state_lookup_failed", error=str(exc))
            raise AliExpressOAuthStateError() from exc

        if not raw:
            # Expired, already used, or never issued. All three are equally
            # non-actionable for the caller and equally suspicious.
            self.logger.warning("aliexpress_state_not_found")
            raise AliExpressOAuthStateError()

        try:
            return OAuthState.from_dict(token, json.loads(raw))
        except (ValueError, KeyError) as exc:
            self.logger.error("aliexpress_state_malformed")
            raise AliExpressOAuthStateError() from exc


__all__ = ["AliExpressService"]
