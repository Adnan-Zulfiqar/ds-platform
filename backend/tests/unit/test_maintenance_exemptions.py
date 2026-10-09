"""D-019: maintenance mode never refuses inbound provider traffic or OAuth
returns. A dropped webhook can be a lost order or a missed compliance
request."""

from __future__ import annotations

import pytest

from app.services.platform_settings import maintenance_exempt

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/billing/webhooks/stripe",
        "/api/v1/integrations/aliexpress/webhook",
        "/api/v1/integrations/aliexpress/callback",
        "/api/v1/integrations/shopify/webhook",
        "/api/v1/integrations/shopify/webhooks/orders-create",
        "/api/v1/integrations/shopify/callback",
        "/api/v1/integrations/shopify/claim-install",
        "/api/v1/integrations/ebay/callback",
        "/api/v1/integrations/ebay/marketplace-account-deletion",
        "/api/v1/integrations/woocommerce/webhooks/2b6f0c1e-0000-4000-8000-000000000001/"
        "3c7a1d2f-0000-4000-8000-000000000002",
    ],
)
def test_provider_traffic_is_exempt(path: str) -> None:
    assert maintenance_exempt(path)


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/products",
        "/api/v1/orders/sync",
        "/api/v1/integrations/shopify/publish",
        "/api/v1/integrations/shopify/stores/abc/webhooks/reconcile",
        "/api/v1/notifications/read-all",
    ],
)
def test_merchant_changes_are_not_exempt(path: str) -> None:
    assert not maintenance_exempt(path)
