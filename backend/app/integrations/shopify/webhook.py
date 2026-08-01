"""Shopify inbound webhooks — HMAC verified, replay-safe, non-fulfilling."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from redis.exceptions import RedisError
from starlette.requests import Request

from app.core.config import settings
from app.core.exceptions import AuthenticationError
from app.core.logging import get_logger
from app.core.redis import RedisPurpose, get_redis
from app.database.session import transaction
from app.integrations.shopify.auth import verify_webhook_hmac
from app.integrations.shopify.schemas import ShopifyWebhookAckResponse
from app.integrations.shopify.sync import ShopifySyncService

logger = get_logger(__name__)

_REPLAY_TTL = 7 * 24 * 3600


async def receive_shopify_webhook(request: Request, *, topic: str) -> ShopifyWebhookAckResponse:
    raw = await request.body()
    secret = settings.shopify.api_secret
    if secret is None or not secret.get_secret_value():
        logger.warning("shopify_webhook_rejected_no_secret")
        raise AuthenticationError("Shopify webhooks are not configured.")

    header_hmac = request.headers.get("x-shopify-hmac-sha256", "")
    if not verify_webhook_hmac(
        raw_body=raw,
        header_hmac=header_hmac,
        secret=secret.get_secret_value(),
    ):
        raise AuthenticationError("Shopify webhook signature is not valid.")

    shop_domain = (request.headers.get("x-shopify-shop-domain") or "").lower()
    webhook_id = request.headers.get("x-shopify-webhook-id") or hashlib.sha256(raw).hexdigest()

    redis = get_redis(RedisPurpose.CACHE)
    replay_key = f"webhooks:shopify:replay:{webhook_id}"
    try:
        inserted = await redis.set(replay_key, "1", nx=True, ex=_REPLAY_TTL)
        if not inserted:
            return ShopifyWebhookAckResponse()
    except RedisError:
        logger.warning("shopify_webhook_replay_unavailable")

    try:
        payload: dict[str, Any] = json.loads(raw.decode("utf-8")) if raw else {}
    except json.JSONDecodeError:
        payload = {}

    logger.info(
        "shopify_webhook_received",
        topic=topic,
        shop_domain=shop_domain,
        field_names=sorted(payload)[:40] if isinstance(payload, dict) else [],
        field_count=len(payload) if isinstance(payload, dict) else 0,
    )

    if not shop_domain or not isinstance(payload, dict):
        return ShopifyWebhookAckResponse()

    try:
        async with transaction() as session:
            from app.core.context import clear_context, set_tenant_id

            # Resolve tenant from shop domain via unscoped lookup then bind.
            from app.repositories.shopify import ShopifyMaintenanceRepository

            maint = ShopifyMaintenanceRepository(session)
            rows = await maint.list_connected(limit=1000)
            match = next((row for row in rows if row.shop_domain == shop_domain), None)
            if match is None:
                logger.warning("shopify_webhook_unknown_shop", shop_domain=shop_domain)
                return ShopifyWebhookAckResponse()

            set_tenant_id(match.tenant_id)
            try:
                sync = ShopifySyncService(session)
                if topic in {"orders-create", "orders-updated", "orders/create", "orders/updated"}:
                    await sync.upsert_order_from_shopify(store_id=match.store_id, raw=payload)
                # product/inventory updates are acknowledged; DropPilot remains
                # source of truth for catalogue pushes in Phase 8.
            finally:
                clear_context()
    except Exception:
        logger.exception("shopify_webhook_processing_failed", topic=topic)

    return ShopifyWebhookAckResponse()


__all__ = ["receive_shopify_webhook"]
