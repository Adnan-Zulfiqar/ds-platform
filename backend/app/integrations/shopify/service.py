"""Shopify OAuth connection lifecycle."""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl

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
from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.core.redis import RedisPurpose, get_redis
from app.integrations.shopify.auth import (
    OAuthState,
    build_authorization_url,
    normalise_shop_domain,
    verify_oauth_hmac,
)
from app.integrations.shopify.client import ShopifyClient
from app.integrations.shopify.exceptions import (
    ShopifyAuthError,
    ShopifyConfigError,
    ShopifyInstallTicketError,
    ShopifyNotConnectedError,
    ShopifyOAuthExchangeError,
    ShopifyOAuthHmacError,
    ShopifyOAuthStateError,
    ShopifyShopTakenError,
    ShopifyWebhookConfigError,
)
from app.models.integration import IntegrationStatus
from app.models.shopify import ShopifyConnection
from app.models.store import StorePlatform, StoreStatus
from app.repositories.shopify import ShopifyConnectionRepository, ShopifyMaintenanceRepository
from app.repositories.store import StoreRepository
from app.services.base import BaseService

logger = get_logger(__name__)

_STATE_KEY_PREFIX = "shopify:oauth:state:"
_INSTALL_TICKET_PREFIX = "shopify:install:ticket:"

#: Topics registered after every successful OAuth. Keep in sync with
#: ``docs/PHASE_8_1_PLAN.md`` Part 5 and the webhook receiver.
WEBHOOK_TOPICS: tuple[str, ...] = (
    "products/create",
    "products/update",
    "inventory_levels/update",
    "orders/create",
    "orders/updated",
    "app/uninstalled",
)


def validate_webhook_callback_base(base: str) -> str:
    """Return a normalised base that maps to a real DropPilot receiver."""
    trimmed = base.strip().rstrip("/")
    if not trimmed:
        raise ShopifyWebhookConfigError()
    if trimmed.endswith(("/webhooks", "/callback", "/webhook")):
        return trimmed
    raise ShopifyWebhookConfigError()


def webhook_delivery_address(*, base: str, topic: str) -> str:
    """Build the address a webhook topic should be registered against.

    Normally each topic gets its own path under ``.../webhooks/{topic}``. A
    path-scoped tunnel (e.g. a Cloudflare tunnel that only forwards the OAuth
    callback path in local development) cannot reach that — so when
    ``base`` ends with ``/callback`` or singular ``/webhook``, every topic
    shares that one URL, and the receiver tells topics apart using
    the ``X-Shopify-Topic`` header. See
    ``docs/SHOPIFY_INTEGRATION.md``.
    """
    trimmed = validate_webhook_callback_base(base)
    if trimmed.endswith("/webhooks"):
        return f"{trimmed}/{topic.replace('/', '-')}"
    return trimmed


def append_frontend_query(return_url: str, *, shopify: str, **extra: str) -> str:
    """Append ``shopify=…`` (and optional extras) without breaking an existing query."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    parts = urlsplit(return_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["shopify"] = shopify
    for key, value in extra.items():
        if value:
            query[key] = value
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


class ShopifyService(BaseService):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.connections = ShopifyConnectionRepository(session)
        self.stores = StoreRepository(session)

    def _require_app_credentials(self) -> tuple[str, str]:
        key = settings.shopify.api_key.strip()
        secret = settings.shopify.api_secret
        if not key or secret is None or not secret.get_secret_value():
            raise ShopifyConfigError()
        return key, secret.get_secret_value()

    async def _store_state(self, state: str, payload: dict[str, Any]) -> None:
        client = get_redis(RedisPurpose.CACHE)
        try:
            await client.set(
                f"{_STATE_KEY_PREFIX}{state}",
                json.dumps(payload),
                ex=settings.shopify.oauth_state_ttl_seconds,
            )
        except RedisError as exc:
            raise ShopifyOAuthStateError() from exc

    async def _consume_state(self, state: str) -> dict[str, Any]:
        client = get_redis(RedisPurpose.CACHE)
        key = f"{_STATE_KEY_PREFIX}{state}"
        try:
            raw = await client.get(key)
            if raw is None:
                raise ShopifyOAuthStateError()
            await client.delete(key)
        except RedisError as exc:
            raise ShopifyOAuthStateError() from exc
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ShopifyOAuthStateError()
        return data

    @staticmethod
    def _slug_for_shop(shop_domain: str) -> str:
        base = shop_domain.removesuffix(".myshopify.com")
        slug = re.sub(r"[^a-z0-9-]+", "-", base.lower()).strip("-")[:48]
        return slug or "shopify-store"

    async def _store_install_ticket(self, token: str, payload: dict[str, Any]) -> None:
        client = get_redis(RedisPurpose.CACHE)
        try:
            await client.set(
                f"{_INSTALL_TICKET_PREFIX}{token}",
                json.dumps(payload),
                ex=settings.shopify.oauth_state_ttl_seconds,
            )
        except RedisError as exc:
            raise ShopifyInstallTicketError() from exc

    async def _consume_install_ticket(self, token: str) -> dict[str, Any]:
        client = get_redis(RedisPurpose.CACHE)
        key = f"{_INSTALL_TICKET_PREFIX}{token}"
        try:
            raw = await client.get(key)
            if raw is None:
                raise ShopifyInstallTicketError()
            await client.delete(key)
        except RedisError as exc:
            raise ShopifyInstallTicketError() from exc
        data = json.loads(raw)
        if not isinstance(data, dict) or not data.get("shop_domain"):
            raise ShopifyInstallTicketError()
        return data

    async def begin_app_url_install(
        self,
        *,
        query_string: str,
        tenant_id: uuid.UUID | None,
        user_id: uuid.UUID | None,
    ) -> str:
        """Handle Shopify App URL GET — verify HMAC, then authorize or claim.

        Returns a redirect URL (Shopify authorize or DropPilot frontend).
        """
        if not is_encryption_configured():
            raise EncryptionNotConfiguredError()
        _, api_secret = self._require_app_credentials()

        items = parse_qsl(query_string, keep_blank_values=True)
        params = dict(items)
        shop = params.get("shop")
        hmac_value = params.get("hmac")
        timestamp = params.get("timestamp")
        if not shop or not hmac_value or not timestamp:
            from app.core.exceptions import ValidationError

            raise ValidationError(
                "Shopify App URL installs require shop, hmac, and timestamp query parameters."
            )
        if not verify_oauth_hmac(query_items=items, secret=api_secret):
            logger.warning(
                "shopify_install_hmac_invalid",
                param_names=sorted(k for k, _ in items if k),
            )
            raise ShopifyOAuthHmacError()

        shop_domain = normalise_shop_domain(shop)
        return_url = settings.shopify.frontend_return_url

        if tenant_id is None:
            ticket = OAuthState.issue().token
            await self._store_install_ticket(
                ticket,
                {
                    "shop_domain": shop_domain,
                    "host": params.get("host"),
                    "verified": True,
                },
            )
            logger.info(
                "shopify_install_claim_needed",
                shop_domain=shop_domain,
            )
            return append_frontend_query(
                return_url,
                shopify="claim_needed",
                shop=shop_domain,
                install_token=ticket,
            )

        from app.core.context import set_tenant_id, set_user_id

        set_tenant_id(tenant_id)
        if user_id is not None:
            set_user_id(user_id)

        authorization_url, _state = await self.begin_connection(
            shop=shop_domain,
            store_name=None,
            user_id=user_id,
        )
        return authorization_url

    async def claim_install(
        self,
        *,
        install_token: str,
        store_name: str | None,
        user_id: uuid.UUID | None,
    ) -> tuple[str, str]:
        """Consume a verified App URL ticket and start OAuth for this tenant."""
        saved = await self._consume_install_ticket(install_token)
        shop_domain = normalise_shop_domain(str(saved["shop_domain"]))
        return await self.begin_connection(
            shop=shop_domain,
            store_name=store_name,
            user_id=user_id,
        )

    async def begin_connection(
        self,
        *,
        shop: str,
        store_name: str | None,
        user_id: uuid.UUID | None,
    ) -> tuple[str, str]:
        if not is_encryption_configured():
            raise EncryptionNotConfiguredError()
        self._require_app_credentials()

        shop_domain = normalise_shop_domain(shop)
        tenant_id = require_tenant_id()

        # Reject before sending the merchant to Shopify consent: a second
        # tenant connecting the same shop would make webhook routing ambiguous.
        owner = await ShopifyMaintenanceRepository(self.session).get_by_shop_domain(shop_domain)
        if owner is not None and owner.tenant_id != tenant_id:
            raise ShopifyShopTakenError()

        state = OAuthState.issue().token

        await self._store_state(
            state,
            {
                "tenant_id": str(tenant_id),
                "user_id": str(user_id) if user_id else None,
                "shop_domain": shop_domain,
                "store_name": store_name or shop_domain.split(".")[0],
            },
        )
        logger.info(
            "shopify_oauth_begun",
            shop_domain=shop_domain,
            tenant_id=str(tenant_id),
        )
        return build_authorization_url(shop_domain=shop_domain, state=state), state

    async def complete_connection(
        self,
        *,
        query_string: str,
    ) -> ShopifyConnection:
        """Exchange the OAuth code. Tenant context is restored from Redis state."""
        from app.core.context import set_tenant_id, set_user_id

        if not is_encryption_configured():
            raise EncryptionNotConfiguredError()
        api_key, api_secret = self._require_app_credentials()

        items = parse_qsl(query_string, keep_blank_values=True)
        params = dict(items)
        if not verify_oauth_hmac(query_items=items, secret=api_secret):
            logger.warning(
                "shopify_oauth_hmac_invalid",
                param_names=sorted(k for k, _ in items if k),
            )
            raise ShopifyOAuthHmacError()

        state = params.get("state")
        code = params.get("code")
        shop = params.get("shop")
        if not state or not code or not shop:
            logger.warning(
                "shopify_oauth_callback_missing_fields",
                has_state=bool(state),
                has_code=bool(code),
                has_shop=bool(shop),
            )
            raise ShopifyOAuthStateError()

        try:
            saved = await self._consume_state(state)
        except ShopifyOAuthStateError:
            logger.warning("shopify_oauth_state_missing_or_expired")
            raise

        shop_domain = normalise_shop_domain(shop)
        if shop_domain != saved.get("shop_domain"):
            logger.warning(
                "shopify_oauth_shop_mismatch",
                callback_shop=shop_domain,
                started_shop=saved.get("shop_domain"),
            )
            raise ShopifyOAuthStateError()

        tenant_id = uuid.UUID(str(saved["tenant_id"]))
        set_tenant_id(tenant_id)
        user_raw = saved.get("user_id")
        user_id = uuid.UUID(str(user_raw)) if user_raw else None
        if user_id is not None:
            set_user_id(user_id)

        try:
            token_payload = await ShopifyClient.exchange_token(
                shop_domain=shop_domain,
                code=code,
                api_key=api_key,
                api_secret=api_secret,
            )
        except ShopifyAuthError as exc:
            logger.warning(
                "shopify_oauth_token_exchange_failed",
                shop_domain=shop_domain,
            )
            raise ShopifyOAuthExchangeError() from exc
        access_token = str(token_payload["access_token"])
        scopes = str(token_payload.get("scope") or settings.shopify.scopes)

        # Race-safe ownership check after token exchange (begin_connection also
        # checked; the unique constraint is the final backstop).
        owner = await ShopifyMaintenanceRepository(self.session).get_by_shop_domain(shop_domain)
        if owner is not None and owner.tenant_id != tenant_id:
            raise ShopifyShopTakenError()

        existing = await self.connections.get_by_shop_domain(shop_domain)
        store_name = str(saved.get("store_name") or shop_domain)

        if existing is not None:
            store = await self.stores.get_by_id(existing.store_id)
            if store is None:
                raise NotFoundError("Linked store was not found.")
            await self.stores.update(
                store,
                status=StoreStatus.CONNECTED,
                storefront_url=f"https://{shop_domain}",
                external_store_id=shop_domain,
                last_error=None,
                connected_by_user_id=user_id,
            )
            connection = await self.connections.update(
                existing,
                encrypted_access_token=encrypt(access_token),
                scopes=scopes,
                status=IntegrationStatus.CONNECTED,
                last_error=None,
                user_id=user_id,
            )
        else:
            # Reconnect after disconnect leaves the Store row; reusing it
            # avoids uq_stores_tenant_slug collisions (Phase 8.1 H1).
            slug = self._slug_for_shop(shop_domain)
            store = await self.stores.get_by_slug(slug)
            if store is None:
                store = await self.stores.create(
                    name=store_name,
                    slug=slug,
                    platform=StorePlatform.SHOPIFY,
                    status=StoreStatus.CONNECTED,
                    storefront_url=f"https://{shop_domain}",
                    external_store_id=shop_domain,
                    connected_by_user_id=user_id,
                )
            else:
                await self.stores.update(
                    store,
                    name=store_name or store.name,
                    status=StoreStatus.CONNECTED,
                    storefront_url=f"https://{shop_domain}",
                    external_store_id=shop_domain,
                    last_error=None,
                    connected_by_user_id=user_id,
                )
            connection = await self.connections.create(
                store_id=store.id,
                shop_domain=shop_domain,
                encrypted_access_token=encrypt(access_token),
                scopes=scopes,
                status=IntegrationStatus.CONNECTED,
                user_id=user_id,
            )

        await self.session.flush()
        logger.info(
            "shopify_oauth_completed",
            shop_domain=shop_domain,
            store_id=str(connection.store_id),
            tenant_id=str(tenant_id),
        )
        return connection

    async def list_status(self) -> tuple[bool, list[ShopifyConnection]]:
        configured = bool(
            settings.shopify.api_key.strip()
            and settings.shopify.api_secret
            and settings.shopify.api_secret.get_secret_value()
            and is_encryption_configured()
        )
        return configured, list(await self.connections.list_all())

    async def release_shop(
        self,
        connection: ShopifyConnection,
        *,
        reason: str,
        revoke_remote: bool,
    ) -> None:
        """Delete the connection row and mark the store disconnected.

        When ``revoke_remote`` is true, best-effort delete webhooks and revoke
        the offline token at Shopify before wiping local ciphertext. Failures
        must not block local cleanup — the merchant asked to disconnect (or
        Shopify already uninstalled the app).
        """
        shop_domain = connection.shop_domain
        store_id = connection.store_id
        if revoke_remote and connection.encrypted_access_token:
            try:
                token = decrypt(connection.encrypted_access_token)
                client = ShopifyClient(
                    shop_domain=shop_domain,
                    access_token=token,
                    tenant_id=str(connection.tenant_id),
                )
                await client.delete_registered_webhooks()
                await client.revoke_access_token()
            except Exception:
                logger.exception(
                    "shopify_remote_cleanup_failed",
                    shop_domain=shop_domain,
                    store_id=str(store_id),
                )

        store = await self.stores.get_by_id(store_id)
        if store is not None:
            await self.stores.update(
                store,
                status=StoreStatus.DISCONNECTED,
                last_error=reason[:512] if reason else None,
            )
        await self.session.delete(connection)
        await self.session.flush()
        logger.info(
            "shopify_shop_released",
            store_id=str(store_id),
            shop_domain=shop_domain,
            reason=reason[:128] if reason else None,
        )

    async def disconnect(self, *, store_id: uuid.UUID) -> None:
        connection = await self.connections.get_by_store(store_id)
        if connection is None:
            raise ShopifyNotConnectedError()
        await self.release_shop(
            connection,
            reason="Disconnected from DropPilot.",
            revoke_remote=True,
        )

    async def handle_app_uninstalled(self, connection: ShopifyConnection) -> None:
        """Release the global shop claim after Shopify uninstalls the app."""
        await self.release_shop(
            connection,
            reason="The app was uninstalled from the Shopify admin.",
            revoke_remote=False,
        )

    async def client_for_store(
        self, store_id: uuid.UUID
    ) -> tuple[ShopifyClient, ShopifyConnection]:
        connection = await self.connections.get_by_store(store_id)
        if connection is None or connection.status is not IntegrationStatus.CONNECTED:
            raise ShopifyNotConnectedError()
        token = decrypt(connection.encrypted_access_token)
        client = ShopifyClient(
            shop_domain=connection.shop_domain,
            access_token=token,
            tenant_id=str(require_tenant_id()),
        )
        return client, connection

    async def mark_synced(self, connection: ShopifyConnection) -> None:
        await self.connections.update(
            connection,
            last_sync_at=datetime.now(UTC),
            last_error=None,
        )
        store = await self.stores.get_by_id(connection.store_id)
        if store is not None:
            await self.stores.update(
                store,
                last_sync_at=datetime.now(UTC),
                status=StoreStatus.CONNECTED,
                last_error=None,
            )

    async def mark_error(self, connection: ShopifyConnection, message: str) -> None:
        trimmed = message[:512]
        await self.connections.update(
            connection,
            last_error=trimmed,
            status=IntegrationStatus.ERROR,
        )
        store = await self.stores.get_by_id(connection.store_id)
        if store is not None:
            await self.stores.update(
                store,
                last_error=trimmed,
                status=StoreStatus.ERROR,
            )

    async def register_webhooks(self, store_id: uuid.UUID) -> None:
        client, connection = await self.client_for_store(store_id)
        base = validate_webhook_callback_base(settings.shopify.webhook_callback_base)

        existing_payload = await client.get("/webhooks.json")
        existing_rows = existing_payload.get("webhooks")
        existing: dict[str, str] = {}
        if isinstance(existing_rows, list):
            for row in existing_rows:
                if not isinstance(row, dict):
                    continue
                topic = row.get("topic")
                address = row.get("address")
                if isinstance(topic, str) and isinstance(address, str):
                    existing[topic] = address

        for topic in WEBHOOK_TOPICS:
            address = webhook_delivery_address(base=base, topic=topic)
            if existing.get(topic) == address:
                continue
            await client.post(
                "/webhooks.json",
                json_body={
                    "webhook": {
                        "topic": topic,
                        "address": address,
                        "format": "json",
                    }
                },
            )
        await self.connections.update(
            connection,
            webhooks_registered_at=datetime.now(UTC),
        )
