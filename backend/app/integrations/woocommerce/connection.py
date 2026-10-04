"""Connect and disconnect WooCommerce stores (Track E7, W1).

A WooCommerce connection is a :class:`Store` row with ``platform =
woocommerce``. The consumer key and secret live in the store's existing
``encrypted_credentials`` column rather than in a new connection table: the
column was made for exactly this, and a second home for the same secret
would be two places to erase.

Unlike the generic ``POST /stores`` (which records credentials as given),
this path **proves the keys work** before saving them. It reads
``/settings/general``, which needs a valid key and also yields the store's
selling currency — so a connected WooCommerce store has a verified currency
from the first moment, as Shopify stores do.
"""

from __future__ import annotations

import json
import re
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any, Final
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.encryption import (
    EncryptionNotConfiguredError,
    decrypt,
    encrypt,
    is_encryption_configured,
)
from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.integrations.woocommerce.client import (
    WooCommerceClient,
    WooCommerceError,
    normalise_site_url,
)
from app.models.store import Store, StorePlatform, StoreStatus
from app.repositories.store import StoreRepository
from app.services.base import BaseService

logger = get_logger(__name__)

_KEY: Final = re.compile(r"^ck_[A-Za-z0-9]{20,64}$")
_SECRET: Final = re.compile(r"^cs_[A-Za-z0-9]{20,64}$")
_CURRENCY: Final = re.compile(r"^[A-Z]{3}$")
#: Track E7 W4b. Order changes the store pushes to DropPilot.
WEBHOOK_TOPICS: Final = ("order.created", "order.updated")
WEBHOOK_IDS_SETTING: Final = "woocommerceWebhookIds"


def _external_id(site_url: str) -> str:
    """``host/path`` — the public identity of the site, never a secret."""
    parsed = urlparse(site_url)
    return f"{parsed.hostname}{parsed.path}"[:128]


def _slug_for(site_url: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", _external_id(site_url).lower()).strip("-")
    return f"woo-{base}"[:56].rstrip("-")


def _currency_from(settings: Any) -> str | None:
    if not isinstance(settings, list):
        return None
    for item in settings:
        if isinstance(item, dict) and item.get("id") == "woocommerce_currency":
            value = str(item.get("value") or "").upper()
            return value if _CURRENCY.match(value) else None
    return None


def is_usable(store: Store) -> bool:
    """Connected through the verified path: keys present, and the currency
    stamp only :meth:`WooCommerceConnectionService.connect` writes."""
    return (
        store.platform is StorePlatform.WOOCOMMERCE
        and store.status is StoreStatus.CONNECTED
        and bool(store.encrypted_credentials)
        and store.currency_last_synced_at is not None
        and bool(store.storefront_url)
    )


class WooCommerceConnectionService(BaseService):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.stores = StoreRepository(session)

    async def connect(
        self,
        *,
        name: str,
        site_url: str,
        consumer_key: str,
        consumer_secret: str,
        user_id: uuid.UUID | None,
    ) -> Store:
        if not is_encryption_configured():
            raise EncryptionNotConfiguredError()
        key, secret = consumer_key.strip(), consumer_secret.strip()
        if not _KEY.match(key) or not _SECRET.match(secret):
            raise ValidationError(
                "Paste the consumer key (starts ck_) and secret (starts cs_) from "
                "WooCommerce → Settings → Advanced → REST API."
            )
        site = normalise_site_url(site_url)

        client = WooCommerceClient(site_url=site, consumer_key=key, consumer_secret=secret)
        currency = _currency_from(await client.get("/settings/general"))
        if currency is None:
            raise ValidationError(
                "The store did not report a currency. Check WooCommerce → Settings → General."
            )

        now = datetime.now(UTC)
        values: dict[str, Any] = {
            "name": name.strip() or _external_id(site),
            "status": StoreStatus.CONNECTED,
            "storefront_url": site,
            "external_store_id": _external_id(site),
            "encrypted_credentials": encrypt(
                json.dumps({"consumer_key": key, "consumer_secret": secret})
            ),
            "currency": currency,
            "currency_last_synced_at": now,
            "last_error": None,
            "last_activity_at": now,
            "connected_by_user_id": user_id,
        }
        # Reconnecting the same site reuses its store, so listings made from
        # it stay attached.
        existing = next(
            (
                s
                for s in await self.stores.list_by_platform(StorePlatform.WOOCOMMERCE)
                if s.external_store_id == values["external_store_id"]
            ),
            None,
        )
        if existing is not None:
            store = await self.stores.update(existing, **values)
        else:
            slug = await self._free_slug(_slug_for(site))
            store = await self.stores.create(
                slug=slug, platform=StorePlatform.WOOCOMMERCE, health_score=100, **values
            )
        if existing is not None:
            await self._remove_webhooks(store, client)
        await self._register_webhooks(store, client, key=key, secret=secret)
        # Track E6b: one free trial per store, whichever account connects it.
        from app.tasks.billing import claim_trial_after_commit

        claim_trial_after_commit(
            self.session, platform="woocommerce", identity=str(values["external_store_id"])
        )
        logger.info("woocommerce_connected", store_id=str(store.id))
        return store

    async def _register_webhooks(
        self, store: Store, client: WooCommerceClient, *, key: str, secret: str
    ) -> None:
        """Track E7 W4b: ask the store to POST order changes to DropPilot.

        Best effort, and only when a public https base is configured. A store
        that refuses (an older WooCommerce, a key without webhook permission)
        still connects; orders then arrive through "Import recent orders",
        and the store row says why.

        The delivery URL names the tenant and the store; the per-store secret
        signs every delivery. See ``webhook.py`` for why naming the tenant is
        safe (decision D-013).
        """
        if not settings.woocommerce.registers_webhooks:
            return
        webhook_secret = secrets.token_urlsafe(32)
        base = settings.woocommerce.webhook_callback_base.rstrip("/")
        delivery_url = f"{base}/{store.tenant_id}/{store.id}"
        ids: list[int] = []
        try:
            for topic in WEBHOOK_TOPICS:
                created = await client.post(
                    "/webhooks",
                    {
                        "name": f"DropPilot {topic}",
                        "topic": topic,
                        "delivery_url": delivery_url,
                        "secret": webhook_secret,
                        "status": "active",
                    },
                )
                if isinstance(created, dict) and isinstance(created.get("id"), int):
                    ids.append(created["id"])
        except WooCommerceError as exc:
            logger.warning("woocommerce_webhook_registration_failed", store_id=str(store.id))
            await self.stores.update(
                store,
                last_error=(
                    "Live order updates could not be set up on this store "
                    f"({exc.code}). Use Import recent orders instead."
                ),
            )
            return
        await self.stores.update(
            store,
            settings={**(store.settings or {}), WEBHOOK_IDS_SETTING: ids},
            encrypted_credentials=encrypt(
                json.dumps(
                    {
                        "consumer_key": key,
                        "consumer_secret": secret,
                        "webhook_secret": webhook_secret,
                    }
                )
            ),
        )

    async def _remove_webhooks(self, store: Store, client: WooCommerceClient) -> None:
        """Best effort: delete the webhooks a previous connection registered,
        so a reconnect or disconnect leaves no stale deliveries behind."""
        ids = (store.settings or {}).get(WEBHOOK_IDS_SETTING) or []
        for webhook_id in ids:
            try:
                await client.delete(f"/webhooks/{int(webhook_id)}", {"force": "true"})
            except (WooCommerceError, ValueError):
                logger.warning("woocommerce_webhook_removal_failed", store_id=str(store.id))
        if ids:
            settings_copy = dict(store.settings or {})
            settings_copy.pop(WEBHOOK_IDS_SETTING, None)
            await self.stores.update(store, settings=settings_copy)

    async def _free_slug(self, base: str) -> str:
        candidate, n = base, 1
        while await self.stores.get_by_slug(candidate) is not None:
            n += 1
            candidate = f"{base}-{n}"
        return candidate

    def client_for(self, store: Store) -> WooCommerceClient:
        """A client for a store connected through :meth:`connect` (W2).

        Requires the verified currency stamp as well as keys: a WooCommerce
        store whose keys came from the generic store endpoint was never
        checked, so publishing does not trust it.
        """
        if not is_usable(store):
            raise ValidationError("This WooCommerce store is not connected.")
        raw = json.loads(decrypt(store.encrypted_credentials or ""))
        return WooCommerceClient(
            site_url=str(store.storefront_url),
            consumer_key=str(raw["consumer_key"]),
            consumer_secret=str(raw["consumer_secret"]),
        )

    async def list_stores(self) -> list[Store]:
        return await self.stores.list_by_platform(StorePlatform.WOOCOMMERCE)

    async def _woo_store(self, store_id: uuid.UUID) -> Store:
        store = await self.stores.get_by_id(store_id)
        if store is None or store.platform is not StorePlatform.WOOCOMMERCE:
            raise NotFoundError.for_resource("Store", store_id)
        return store

    async def disconnect(self, store_id: uuid.UUID) -> Store:
        """Forget the keys. The store row and its listings stay, so a later
        reconnect picks up where it left off. Revoking the key itself is done
        in WordPress; DropPilot has no right to do it."""
        store = await self._woo_store(store_id)
        if is_usable(store):
            await self._remove_webhooks(store, self.client_for(store))
        await self.stores.update(store, status=StoreStatus.DISCONNECTED, encrypted_credentials=None)
        logger.info("woocommerce_disconnected", store_id=str(store.id))
        return store


def webhook_secret_for(store: Store) -> str | None:
    """The per-store secret deliveries are signed with, if webhooks exist."""
    if not store.encrypted_credentials:
        return None
    raw = json.loads(decrypt(store.encrypted_credentials))
    value = raw.get("webhook_secret") if isinstance(raw, dict) else None
    return str(value) if value else None


__all__ = ["WooCommerceConnectionService", "is_usable", "webhook_secret_for"]
