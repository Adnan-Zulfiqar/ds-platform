"""The eBay seller connection: consent, callback, refresh, disconnect.

One service owns the whole lifecycle so there is exactly one answer to "is this
token usable, and what do we do if not".

Three decisions worth stating up front, because each of them is a place this
kind of flow usually goes wrong:

**The callback trusts the state record, never the query string.** eBay's
redirect carries only ``code`` and ``state``. Which workspace this belongs to,
which admin started it and which eBay estate it targets all come from the Redis
record that ``state`` unlocks. A tenant id read from a URL is a tenant id an
attacker can type.

**State is consumed before the code is spent.** The delete is what makes the
state single-use, so it happens first. Exchanging first and deleting afterwards
leaves a window in which a replayed callback runs the exchange twice.

**Refresh is serialised by a database row lock.** Two requests that both notice
an expiring token would otherwise both call eBay, and the loser would overwrite
the winner's token with an older one.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Final

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
from app.core.logging import get_logger
from app.core.redis import RedisPurpose, get_redis
from app.integrations.ebay.exceptions import (
    EbayNotConfiguredError,
    EbayOAuthStateError,
    EbayTokenRevokedError,
)
from app.integrations.ebay.identity import EbaySellerIdentity, fetch_seller_identity
from app.integrations.ebay.oauth import (
    EbayOAuthState,
    build_authorization_url,
    scope_parameter,
)
from app.integrations.ebay.tokens import (
    EbayTokenSet,
    exchange_authorization_code,
    refresh_access_token,
)
from app.models.ebay import EbayConnection, EbayConnectionStatus
from app.models.store import StorePlatform, StoreStatus
from app.repositories.ebay import EbayConnectionRepository, EbayListingDefaultsRepository
from app.repositories.shopify import StoreListingRepository
from app.repositories.store import StoreRepository

logger = get_logger(__name__)

#: Only a hash of the state token is used as the key. A leaked Redis snapshot
#: then contains no usable state credential — the raw token exists solely in the
#: seller's redirect and in memory for the length of one request.
_STATE_KEY_PREFIX: Final = "ebay:oauth:state:"

#: Machine codes for ``reconnect_reason``. Stable, and never upstream text.
RECONNECT_REVOKED: Final = "refresh_token_revoked"
RECONNECT_MISSING_REFRESH: Final = "no_refresh_token"


class EbayConnectionService:
    """Seller OAuth for one tenant."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.connections = EbayConnectionRepository(session)

    # --- configuration -----------------------------------------------------

    @staticmethod
    def _require_oauth_configured() -> None:
        if not settings.ebay.is_oauth_configured:
            raise EbayNotConfiguredError("eBay seller connection is not configured on this server.")

    # --- state -------------------------------------------------------------

    @staticmethod
    def _state_key(token: str) -> str:
        return f"{_STATE_KEY_PREFIX}{hashlib.sha256(token.encode()).hexdigest()}"

    async def _store_state(self, state: EbayOAuthState) -> None:
        client = get_redis(RedisPurpose.CACHE)
        try:
            await client.set(
                self._state_key(state.token),
                json.dumps(state.to_dict()),
                ex=settings.ebay.oauth_state_ttl_seconds,
            )
        except RedisError as exc:
            # Without durable state there is no CSRF defence, so refuse to
            # start rather than issuing a consent URL that cannot be validated.
            raise EbayOAuthStateError() from exc

    async def _consume_state(self, token: str) -> EbayOAuthState:
        """Atomically read-and-delete. The delete is what makes it single-use.

        Atomic rather than get-then-delete: two simultaneous replays of the same
        callback would both pass a non-atomic check, and only one of them should
        ever proceed.

        **``MULTI``/``EXEC`` rather than ``GETDEL``**, which would be the obvious
        choice and is wrong here. ``GETDEL`` was added in Redis 6.2, and this
        platform's Windows deployments run the 3.0.504 build — where it does not
        exist, and the resulting ``unknown command`` error would make *every*
        eBay consent fail with "invalid state", indistinguishable from a real
        CSRF rejection. A transaction gives exactly the same guarantee (Redis
        runs a queued block with no other client's command interleaved, so only
        one caller can see a non-nil value) on every server version from 1.2
        onward. One round trip either way.
        """
        client = get_redis(RedisPurpose.CACHE)
        key = self._state_key(token)
        try:
            async with client.pipeline(transaction=True) as pipe:
                pipe.get(key)
                pipe.delete(key)
                raw, _ = await pipe.execute()
        except RedisError as exc:
            raise EbayOAuthStateError() from exc
        if raw is None:
            # Missing, expired or already used — one error for all three, so
            # nothing tells a caller whether a guessed token was ever real.
            raise EbayOAuthStateError()
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise EbayOAuthStateError() from exc
        if not isinstance(data, dict):
            raise EbayOAuthStateError()
        try:
            return EbayOAuthState.from_dict(token, data)
        except (KeyError, ValueError) as exc:
            raise EbayOAuthStateError() from exc

    # --- read --------------------------------------------------------------

    async def get_connection(self) -> EbayConnection | None:
        return await self.connections.get_for_tenant()

    # --- connect -----------------------------------------------------------

    async def begin_connection(self, *, user_id: uuid.UUID | None) -> tuple[str, str]:
        """Issue a consent URL bound to this tenant. Returns ``(url, state)``.

        Encryption is checked here rather than at the callback: discovering the
        platform cannot store a token *after* a seller has granted consent
        wastes their time and leaves a live credential with nowhere safe to go.
        """
        self._require_oauth_configured()
        if not is_encryption_configured():
            raise EncryptionNotConfiguredError()

        tenant_id = require_tenant_id()
        state = EbayOAuthState.create(
            tenant_id=str(tenant_id),
            user_id=str(user_id) if user_id else None,
            environment=settings.ebay.environment.value,
            return_to=settings.ebay.frontend_return_url,
        )
        await self._store_state(state)

        logger.info("ebay_oauth_begun", tenant_id=str(tenant_id))
        return build_authorization_url(state=state.token), state.token

    async def complete_connection(self, *, code: str, state_token: str) -> EbayConnection:
        """Exchange the consent code and persist the connection.

        Tenant context is restored from the state record, so this runs with the
        identity of whoever started the flow rather than of whoever's browser
        arrived at the callback.
        """
        from app.core.context import set_tenant_id, set_user_id

        self._require_oauth_configured()
        if not is_encryption_configured():
            raise EncryptionNotConfiguredError()

        # Consume first: the code must not be spent on a state that could be
        # replayed.
        state = await self._consume_state(state_token)

        if state.environment != settings.ebay.environment.value:
            # The server was reconfigured mid-flow. The resulting token would
            # belong to a different eBay estate from the one now configured.
            logger.warning("ebay_oauth_environment_changed")
            raise EbayOAuthStateError()

        tenant_id = uuid.UUID(state.tenant_id)
        set_tenant_id(tenant_id)
        user_id = uuid.UUID(state.user_id) if state.user_id else None
        if user_id is not None:
            set_user_id(user_id)

        # `code` arrives already URL-decoded from the query string, and httpx
        # encodes the form body. Exactly one encoding — see `tokens.py`.
        tokens = await exchange_authorization_code(code=code)
        identity = await fetch_seller_identity(access_token=tokens.access_token)

        connection = await self._persist(
            tokens=tokens,
            identity=identity,
            user_id=user_id,
        )
        logger.info(
            "ebay_oauth_completed",
            tenant_id=str(tenant_id),
            connection_id=str(connection.id),
        )
        return connection

    async def _persist(
        self,
        *,
        tokens: EbayTokenSet,
        identity: EbaySellerIdentity,
        user_id: uuid.UUID | None,
    ) -> EbayConnection:
        """Create or update this tenant's connection, transactionally.

        Reconnecting the *same* seller updates in place, which is what makes a
        re-consent safe: the row keeps its id, and the global unique constraint
        is never challenged. A *different* seller replaces the identity on the
        same row — one account per workspace — and the constraint refuses it if
        that account belongs elsewhere.
        """
        now = datetime.now(UTC)
        values: dict[str, Any] = {
            "environment": settings.ebay.environment.value,
            "ebay_user_id": identity.user_id,
            "ebay_username": identity.username,
            "marketplace_id": identity.registration_marketplace_id,
            "account_type": identity.account_type,
            "encrypted_access_token": encrypt(tokens.access_token),
            "access_token_expires_at": now + timedelta(seconds=tokens.expires_in),
            "granted_scopes": tokens.scope or scope_parameter(),
            "status": EbayConnectionStatus.CONNECTED,
            "connected_at": now,
            "last_verified_at": now,
            "reconnect_reason": None,
            "last_error": None,
            "user_id": user_id,
        }
        if tokens.refresh_token:
            values["encrypted_refresh_token"] = encrypt(tokens.refresh_token)
            if tokens.refresh_token_expires_in:
                values["refresh_token_expires_at"] = now + timedelta(
                    seconds=tokens.refresh_token_expires_in
                )

        existing = await self.connections.get_for_tenant()
        if existing is not None:
            if existing.ebay_user_id and existing.ebay_user_id != identity.user_id:
                # A different seller on the same row: the previous seller's
                # policy ids, location keys and listing ids mean nothing for
                # the new one, and leaving them would both publish with the
                # wrong policies and hide them from the old seller's eBay
                # deletion notice (which matches through this row's user id).
                await self._erase_seller_artifacts(existing)
            return await self.connections.update(existing, **values)
        return await self.connections.create(**values)

    async def _erase_seller_artifacts(self, connection: EbayConnection) -> int:
        """Remove everything held for the connected seller except the row itself:
        listing defaults (C2) and eBay listing rows (C3), and park the eBay
        stores. Used by disconnect and by a reconnect as another seller."""
        await EbayListingDefaultsRepository(self.session).delete_for_connection(connection.id)
        stores = await StoreRepository(self.session).list_by_platform(StorePlatform.EBAY)
        for store in stores:
            if store.status is not StoreStatus.DISCONNECTED:
                await StoreRepository(self.session).update(store, status=StoreStatus.DISCONNECTED)
        return await StoreListingRepository(self.session).erase_for_stores(
            [store.id for store in stores]
        )

    # --- token authority ---------------------------------------------------

    async def access_token_for(self, connection: EbayConnection) -> str:
        """The one way to obtain a usable access token.

        Every future eBay call goes through here, so "is it still valid, and
        who refreshes it" is answered once rather than at each call site.
        """
        if connection.status is EbayConnectionStatus.RECONNECT_REQUIRED:
            raise EbayTokenRevokedError()
        if connection.encrypted_access_token is None:
            raise EbayTokenRevokedError()

        if not connection.access_token_expires_within(settings.ebay.token_refresh_margin_seconds):
            return decrypt(connection.encrypted_access_token)

        refreshed = await self.refresh(connection)
        if refreshed.encrypted_access_token is None:  # pragma: no cover - defensive
            raise EbayTokenRevokedError()
        return decrypt(refreshed.encrypted_access_token)

    async def refresh(self, connection: EbayConnection) -> EbayConnection:
        """Renew the access token, serialised per connection.

        The row lock is taken first and the expiry re-read afterwards: whichever
        request loses the race finds the token already renewed and returns it
        rather than spending a second refresh on the same connection.
        """
        locked = await self.connections.lock_for_update(connection.id)
        if locked is None:  # pragma: no cover - the row was removed concurrently
            raise EbayTokenRevokedError()

        if not locked.access_token_expires_within(settings.ebay.token_refresh_margin_seconds):
            return locked

        if locked.encrypted_refresh_token is None:
            await self._mark_reconnect_required(locked, RECONNECT_MISSING_REFRESH)
            raise EbayTokenRevokedError()

        try:
            tokens = await refresh_access_token(
                refresh_token=decrypt(locked.encrypted_refresh_token),
                scope=locked.granted_scopes or scope_parameter(),
            )
        except EbayTokenRevokedError:
            # Not retryable, ever: the seller changed their password or login
            # name, or revoked consent. Marked so the card asks for reconnect
            # instead of the platform hammering the token service.
            await self._mark_reconnect_required(locked, RECONNECT_REVOKED)
            raise

        now = datetime.now(UTC)
        values: dict[str, Any] = {
            "encrypted_access_token": encrypt(tokens.access_token),
            "access_token_expires_at": now + timedelta(seconds=tokens.expires_in),
            "last_refreshed_at": now,
            "status": EbayConnectionStatus.CONNECTED,
            "reconnect_reason": None,
            "last_error": None,
        }
        # eBay's documented refresh response carries no new refresh token, but
        # storing one when it appears means a future rotation is honoured rather
        # than silently dropped — the failure mode that leaves the platform
        # authenticating with a credential the provider has already retired.
        if tokens.refresh_token:
            values["encrypted_refresh_token"] = encrypt(tokens.refresh_token)
            if tokens.refresh_token_expires_in:
                values["refresh_token_expires_at"] = now + timedelta(
                    seconds=tokens.refresh_token_expires_in
                )
        if tokens.scope:
            values["granted_scopes"] = tokens.scope

        logger.info("ebay_token_refreshed", connection_id=str(locked.id))
        return await self.connections.update(locked, **values)

    async def _mark_reconnect_required(self, connection: EbayConnection, reason: str) -> None:
        """Drop the dead credentials and record why.

        The tokens are cleared rather than kept: a refresh token eBay has
        revoked cannot be used again, and retaining the ciphertext is retention
        with no purpose.
        """
        await self.connections.update(
            connection,
            status=EbayConnectionStatus.RECONNECT_REQUIRED,
            reconnect_reason=reason,
            encrypted_access_token=None,
            encrypted_refresh_token=None,
            access_token_expires_at=None,
        )
        logger.warning(
            "ebay_reconnect_required",
            connection_id=str(connection.id),
            reason=reason,
        )

    async def verify(self) -> EbayConnection | None:
        """Re-check the connection against eBay and refresh display metadata.

        Also the honest way to answer "is this still working?" — the stored
        status is only ever as fresh as the last call that used it.
        """
        connection = await self.connections.get_for_tenant()
        if connection is None:
            return None

        token = await self.access_token_for(connection)
        identity = await fetch_seller_identity(access_token=token)

        if identity.user_id != connection.ebay_user_id:
            # The token now belongs to a different eBay account than the one on
            # record. Refusing is the only safe response: silently rewriting the
            # identity would move a connection between accounts without anyone
            # consenting to it.
            logger.warning("ebay_identity_changed", connection_id=str(connection.id))
            raise EbayTokenRevokedError()

        return await self.connections.update(
            connection,
            ebay_username=identity.username,
            marketplace_id=identity.registration_marketplace_id,
            account_type=identity.account_type,
            last_verified_at=datetime.now(UTC),
        )

    # --- disconnect --------------------------------------------------------

    async def disconnect(self) -> bool:
        """Remove this tenant's connection and its stored credentials.

        Idempotent: disconnecting nothing reports success rather than erroring,
        so a double click cannot produce a confusing failure.

        **No provider-side revocation call is made, and that is a deliberate,
        documented choice rather than an omission.** eBay's published OAuth
        documentation describes minting and refreshing tokens, and states that
        sellers revoke consent through their own eBay account pages; it does not
        document a revocation endpoint for this grant that an application can
        call on the seller's behalf. Rather than invent a plausible-looking URL
        and report "revoked" on the strength of a guess, the local credentials
        are destroyed — which is what actually stops this platform acting — and
        the limitation is written down in the eBay integration guide so the
        merchant is told to revoke at eBay if they want the grant itself gone.

        Compliance history is untouched: the ledger holds no personal data and
        is not this row's to delete.
        """
        connection = await self.connections.get_for_tenant()
        if connection is None:
            return False

        # EBAY-C3: the marketplace stores stay (marked disconnected, so the
        # editor stops offering them); their listing rows go, because they
        # hold the seller's eBay ids and mean nothing without the grant.
        # Listings already live on eBay are not touched.
        erased = await self._erase_seller_artifacts(connection)

        await self.connections.hard_delete(connection)
        logger.info("ebay_disconnected", connection_id=str(connection.id), listings_erased=erased)
        return True


__all__ = [
    "RECONNECT_MISSING_REFRESH",
    "RECONNECT_REVOKED",
    "EbayConnectionService",
]
