"""Shopify API request/response schemas — no credential fields."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.schemas.base import CamelCaseModel


class ShopifyConnectRequest(CamelCaseModel):
    """Begin OAuth for a shop domain (typed Connect — no merchant credentials)."""

    shop: str = Field(min_length=3, max_length=255)
    store_name: str | None = Field(default=None, max_length=255)


class ShopifyClaimInstallRequest(CamelCaseModel):
    """Bind a HMAC-verified App URL install ticket to the current tenant."""

    install_token: str = Field(min_length=16, max_length=128)
    store_name: str | None = Field(default=None, max_length=255)


class ShopifyAuthorizationResponse(CamelCaseModel):
    authorization_url: str
    state: str
    expires_in_seconds: int


class ShopifyConnectionRead(CamelCaseModel):
    id: uuid.UUID
    store_id: uuid.UUID
    shop_domain: str
    status: str
    scopes: str
    connected_at: datetime
    last_sync_at: datetime | None
    last_error: str | None
    webhooks_registered_at: datetime | None


class ShopifyStatusResponse(CamelCaseModel):
    configured: bool
    connections: list[ShopifyConnectionRead] = Field(default_factory=list)


class ShopifyWebhookAckResponse(CamelCaseModel):
    status: str = "received"


class ShopifyPublishRequest(CamelCaseModel):
    product_id: uuid.UUID
    store_id: uuid.UUID


class ShopifyPublishResponse(CamelCaseModel):
    """Structured publish result — includes verified storefront/admin links."""

    message: str
    listing_id: uuid.UUID
    external_product_id: str
    external_handle: str | None = None
    external_graphql_id: str | None = None
    shop_domain: str | None = None
    storefront_url: str | None = None
    admin_url: str | None = None
    online_store_published: bool | None = None
    updated: bool = True


class StoreListingRead(CamelCaseModel):
    id: uuid.UUID
    store_id: uuid.UUID
    product_id: uuid.UUID
    external_product_id: str
    external_handle: str | None = None
    external_graphql_id: str | None = None
    shop_domain: str | None = None
    storefront_url: str | None = None
    admin_url: str | None = None
    online_store_published: bool | None = None
    status: str
    last_synced_at: datetime | None = None
    last_error: str | None = None
    published_at: datetime | None = None
    last_failed_sync_at: datetime | None = None


class ShopifySyncRequest(CamelCaseModel):
    store_id: uuid.UUID | None = None
