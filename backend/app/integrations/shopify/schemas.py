"""Shopify API request/response schemas — no credential fields."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.schemas.base import CamelCaseModel


class ShopifyConnectRequest(CamelCaseModel):
    """Begin OAuth for a shop domain."""

    shop: str = Field(min_length=3, max_length=255)
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


class ShopifySyncRequest(CamelCaseModel):
    store_id: uuid.UUID | None = None
