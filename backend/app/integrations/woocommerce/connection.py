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
import uuid
from datetime import UTC, datetime
from typing import Any, Final
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import (
    EncryptionNotConfiguredError,
    encrypt,
    is_encryption_configured,
)
from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.integrations.woocommerce.client import WooCommerceClient, normalise_site_url
from app.models.store import Store, StorePlatform, StoreStatus
from app.repositories.store import StoreRepository
from app.services.base import BaseService

logger = get_logger(__name__)

_KEY: Final = re.compile(r"^ck_[A-Za-z0-9]{20,64}$")
_SECRET: Final = re.compile(r"^cs_[A-Za-z0-9]{20,64}$")
_CURRENCY: Final = re.compile(r"^[A-Z]{3}$")


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
        logger.info("woocommerce_connected", store_id=str(store.id))
        return store

    async def _free_slug(self, base: str) -> str:
        candidate, n = base, 1
        while await self.stores.get_by_slug(candidate) is not None:
            n += 1
            candidate = f"{base}-{n}"
        return candidate

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
        await self.stores.update(store, status=StoreStatus.DISCONNECTED, encrypted_credentials=None)
        logger.info("woocommerce_disconnected", store_id=str(store.id))
        return store


__all__ = ["WooCommerceConnectionService"]
