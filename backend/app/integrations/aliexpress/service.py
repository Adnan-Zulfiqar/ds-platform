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
from app.core.context import require_tenant_id, set_tenant_id, set_user_id
from app.core.encryption import (
    EncryptionNotConfiguredError,
    decrypt,
    encrypt,
    is_encryption_configured,
)
from app.core.exceptions import ValidationError
from app.core.redis import RedisPurpose, get_redis, take_once
from app.integrations.aliexpress.auth import (
    OAuthState,
    build_authorization_url,
    resolve_token_expiry,
)
from app.integrations.aliexpress.client import AliExpressClient
from app.integrations.aliexpress.exceptions import (
    AliExpressAuthError,
    AliExpressCatalogNotConfiguredError,
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

    @staticmethod
    def platform_credentials() -> tuple[str, str]:
        """Return DropPilot's AliExpress application credentials from settings.

        Merchants never supply an app key/secret. One platform application is
        authorized by each tenant seller; the secret stays in the environment
        and is never written to tenant connection rows.
        """
        platform_key = settings.aliexpress.app_key.strip()
        platform_secret = settings.aliexpress.app_secret

        if not platform_key or not platform_secret:
            raise ValidationError(
                "AliExpress is not configured on this server. Set "
                "ALIEXPRESS_APP_KEY and ALIEXPRESS_APP_SECRET."
            )

        return platform_key, platform_secret.get_secret_value()

    # Kept as a thin alias so older call sites/tests that imported the name
    # still resolve; always platform-owned.
    @staticmethod
    def resolve_credentials(
        app_key: str | None = None,
        app_secret: str | None = None,
    ) -> tuple[str, str]:
        if (app_key or "").strip() or (app_secret or "").strip():
            raise ValidationError(
                "AliExpress app credentials are owned by the platform. "
                "Do not supply an app key or app secret per workspace."
            )
        return AliExpressService.platform_credentials()

    async def begin_connection(
        self,
        *,
        user_id: uuid.UUID | None,
    ) -> tuple[str, str]:
        """Open a pending connection and return the AliExpress consent URL.

        Uses platform ``ALIEXPRESS_APP_*`` credentials only. Tenant rows store
        the public app key for display and, after callback, encrypted seller
        tokens — never the application secret.
        """
        if not is_encryption_configured():
            # Refusing beats storing seller tokens in plaintext because a key
            # was missing.
            raise EncryptionNotConfiguredError()

        app_key, _app_secret = self.platform_credentials()

        tenant_id = require_tenant_id()
        existing = await self.connections.get_for_tenant()

        if existing is not None:
            # Reconnecting clears seller tokens in place. The unique constraint
            # permits one connection per tenant, and deleting then recreating
            # would lose `created_at`.
            await self.connections.update(
                existing,
                app_key=app_key,
                encrypted_app_secret=None,
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
                encrypted_app_secret=None,
                status=IntegrationStatus.PENDING,
                user_id=user_id,
            )

        state = OAuthState.create(
            tenant_id=str(tenant_id), user_id=str(user_id) if user_id else None
        )
        await self._store_state(state)

        self.logger.info("aliexpress_connection_started", tenant_id=str(tenant_id))
        return build_authorization_url(app_key=app_key, state=state.token), state.token

    def build_client(
        self,
        connection: AliExpressConnection,
        *,
        access_token: str | None = None,
    ) -> AliExpressClient:
        """Build a client with platform app credentials and optional seller token."""
        app_key, app_secret = self.platform_credentials()
        return AliExpressClient(
            app_key=app_key,
            app_secret=app_secret,
            tenant_id=str(connection.tenant_id),
            access_token=access_token,
        )

    async def authenticated_client(self) -> AliExpressClient:
        """Client for the current tenant with a usable seller access token.

        Required for orders, tracking, and any call that acts as the merchant's
        AliExpress dropshipper account. Product import uses
        :meth:`client_for_catalog` instead.
        """
        connection = await self.require_connection()
        connection = await self.refresh_if_needed(connection)
        if not connection.encrypted_access_token:
            raise ValidationError("AliExpress is not connected for this workspace.")
        return self.build_client(
            connection,
            access_token=decrypt(connection.encrypted_access_token),
        )

    def catalog_client(self) -> AliExpressClient:
        """Client signed with platform app credentials and the catalog token.

        Used for link → draft import so merchants are not forced through OAuth
        before they can edit a product. AliExpress still requires an access
        token on ``ds.product.get``; that token is the platform dropshipper
        grant in ``ALIEXPRESS_CATALOG_ACCESS_TOKEN``.
        """
        raw = settings.aliexpress.catalog_access_token
        token = raw.get_secret_value().strip() if raw is not None else ""
        if not token:
            raise AliExpressCatalogNotConfiguredError()
        app_key, app_secret = self.platform_credentials()
        # Rate-limit and log under the calling tenant so one workspace cannot
        # silently consume another tenant's outbound budget.
        return AliExpressClient(
            app_key=app_key,
            app_secret=app_secret,
            tenant_id=str(require_tenant_id()),
            access_token=token,
        )

    async def client_for_catalog(self) -> AliExpressClient:
        """Resolve a client for product fetch / feed / catalogue sync.

        Preference: platform catalog token (no merchant OAuth). Fallback: the
        merchant's connected seller token, so existing workspaces keep working
        until the operator configures ``ALIEXPRESS_CATALOG_ACCESS_TOKEN``.
        """
        raw = settings.aliexpress.catalog_access_token
        if raw is not None and raw.get_secret_value().strip():
            return self.catalog_client()
        try:
            return await self.authenticated_client()
        except (AliExpressNotConnectedError, ValidationError) as exc:
            raise AliExpressCatalogNotConfiguredError() from exc

    async def complete_connection(self, *, code: str, state_token: str) -> AliExpressConnection:
        """Exchange the authorization code for tokens and mark the connection live.

        The state is consumed on lookup, so a replayed callback fails. That is
        deliberate: a code can only be exchanged once, and a second callback
        carrying the same state is either a duplicate submission or an attack.
        """
        state = await self._consume_state(state_token)

        # The browser arrives from AliExpress without a Bearer token. The OAuth
        # state issued during an authenticated /connect call is the authority
        # on which tenant owns this callback — binding from it is what makes
        # the CSRF defence work without requiring a session cookie on return.
        tenant_id = uuid.UUID(state.tenant_id)
        set_tenant_id(tenant_id)
        if state.user_id:
            set_user_id(uuid.UUID(state.user_id))

        connection = await self.connections.get_for_tenant()
        if connection is None:
            raise AliExpressNotConnectedError(
                "No pending AliExpress connection was found. Please start again."
            )

        client = self.build_client(connection)

        try:
            payload = await client.exchange_token(
                settings.aliexpress.token_url,
                {
                    "code": code,
                    "grant_type": "authorization_code",
                    "need_refresh_token": "true",
                    "redirect_uri": settings.aliexpress.callback_url,
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

        refresh_token = decrypt(connection.encrypted_refresh_token)

        client = self.build_client(connection)

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
        `take_once` does both atomically (review finding C-1): with a separate
        GET and DELETE, two concurrent callbacks could both read the state
        before either deleted it.
        """
        if not token:
            raise AliExpressOAuthStateError()

        key = f"{_STATE_KEY_PREFIX}{token}"
        try:
            client = get_redis(RedisPurpose.SESSION)
            raw = await take_once(client, key)
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
