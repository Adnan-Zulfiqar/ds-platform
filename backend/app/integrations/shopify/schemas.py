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
    #: Derived from ``status`` and ``webhooks_registered_at`` by
    #: ``service.webhook_health`` — never a stored column, so it cannot drift
    #: from the timestamp it is read from. Named here so a client does not have
    #: to invent its own rule for what a null timestamp means; the previous
    #: answer was "assume connected", which hid stores that were missing every
    #: product, inventory and order subscription.
    webhook_health: str


class ShopifyStatusResponse(CamelCaseModel):
    configured: bool
    connections: list[ShopifyConnectionRead] = Field(default_factory=list)


class ShopifyWebhookAckResponse(CamelCaseModel):
    status: str = "received"


class ShopifyWebhookTopicResult(CamelCaseModel):
    """What reconciliation did about one topic.

    ``status`` is the reconciler's own vocabulary — ``already_present``,
    ``created``, ``unknown``, ``failed`` — kept rather than collapsed into a
    boolean, because "we could not tell whether that create landed" is a
    materially different thing to tell a merchant than "that create was
    refused".
    """

    topic: str
    status: str
    webhook_gid: str | None = None
    detail: str | None = None


class ShopifyWebhookReconcileResponse(CamelCaseModel):
    """The result of a deterministic webhook retry.

    Returned with 200 whether or not the outcome was healthy: the request was
    handled correctly either way, and an unhealthy reconciliation is a *result*
    to be shown, not a transport failure. ``healthy`` and ``webhook_health``
    carry the verdict, so a client can never read a 200 as "webhooks are fine".

    Carries no token, no shop secret and no raw provider payload — the fields
    here are incapable of holding one.
    """

    store_id: uuid.UUID
    healthy: bool
    webhook_health: str
    topics: list[ShopifyWebhookTopicResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    listed_count: int = 0
    created_count: int = 0
    #: Present only when the whole reconciliation was healthy; null is the
    #: honest answer for a store that is still degraded.
    webhooks_registered_at: datetime | None = None


class ShopifyPublishRequest(CamelCaseModel):
    product_id: uuid.UUID
    store_id: uuid.UUID
    #: Optimistic concurrency token from the draft the merchant just saved.
    #: When supplied, publish refuses a stale draft before any provider call.
    expected_updated_at: datetime | None = None


class ShopifyPublishCheckItem(CamelCaseModel):
    """One server-authoritative publish blocker or recommendation."""

    code: str
    message: str
    field: str | None = None
    section: str | None = None
    action: str | None = None


class ShopifyPublishReadinessRequest(CamelCaseModel):
    product_id: uuid.UUID
    store_id: uuid.UUID | None = None
    expected_updated_at: datetime | None = None


class ShopifyPublishReadinessResponse(CamelCaseModel):
    """Authoritative publish check — no readiness score, no secrets."""

    channel: str
    store_id: uuid.UUID | None = None
    draft_id: uuid.UUID
    draft_updated_at: datetime
    can_publish: bool
    blockers: list[ShopifyPublishCheckItem] = Field(default_factory=list)
    recommendations: list[ShopifyPublishCheckItem] = Field(default_factory=list)
    checked_at: datetime


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
