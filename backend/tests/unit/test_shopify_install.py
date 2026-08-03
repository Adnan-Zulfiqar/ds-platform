"""Unit tests for Shopify App URL install helpers (no live Shopify)."""

from __future__ import annotations

import hashlib
import hmac
import uuid
from typing import Any
from urllib.parse import parse_qsl, urlencode

import pytest
from fakeredis import aioredis as fake_aioredis
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.context import clear_context, set_tenant_id
from app.integrations.shopify.exceptions import ShopifyOAuthHmacError
from app.integrations.shopify.service import ShopifyService

pytestmark = pytest.mark.unit


def _sign_query(items: list[tuple[str, str]], secret: str) -> str:
    pairs = [(k, v) for k, v in items if k not in {"hmac", "signature"}]
    pairs.sort(key=lambda item: item[0])
    message = "&".join(f"{k}={v}" for k, v in pairs)
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


@pytest.fixture
def shopify_secrets(monkeypatch: pytest.MonkeyPatch) -> str:
    secret = "shpss_phase81_install_test"
    monkeypatch.setattr(settings.shopify, "api_key", "test_api_key_32chars____________")
    monkeypatch.setattr(settings.shopify, "api_secret", SecretStr(secret))
    monkeypatch.setattr(
        settings.shopify,
        "frontend_return_url",
        "http://localhost:3000/settings/integrations",
    )
    monkeypatch.setattr(
        "app.integrations.shopify.service.is_encryption_configured",
        lambda: True,
    )
    return secret


@pytest.mark.asyncio
async def test_app_url_install_anonymous_issues_claim_ticket(
    monkeypatch: pytest.MonkeyPatch,
    shopify_secrets: str,
) -> None:
    redis = fake_aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(
        "app.integrations.shopify.service.get_redis",
        lambda _purpose: redis,
    )

    items = [
        ("shop", "phase81-anon.myshopify.com"),
        ("timestamp", "1710000000"),
        ("host", "YWRtaW4uc2hvcGlmeS5jb20v"),
    ]
    digest = _sign_query(items, shopify_secrets)
    query = urlencode([*items, ("hmac", digest)])

    # Session is unused for the anonymous ticket path after HMAC verify.
    service = ShopifyService(session=None)  # type: ignore[arg-type]
    redirect = await service.begin_app_url_install(
        query_string=query,
        tenant_id=None,
        user_id=None,
    )
    assert "shopify=claim_needed" in redirect
    assert "install_token=" in redirect
    assert "shop=phase81-anon.myshopify.com" in redirect


@pytest.mark.asyncio
async def test_app_url_install_rejects_bad_hmac(
    monkeypatch: pytest.MonkeyPatch,
    shopify_secrets: str,
) -> None:
    monkeypatch.setattr(
        "app.integrations.shopify.service.get_redis",
        lambda _purpose: fake_aioredis.FakeRedis(decode_responses=True),
    )
    query = urlencode(
        [
            ("shop", "phase81-bad.myshopify.com"),
            ("timestamp", "1710000000"),
            ("hmac", "00" * 32),
        ]
    )
    service = ShopifyService(session=None)  # type: ignore[arg-type]
    with pytest.raises(ShopifyOAuthHmacError):
        await service.begin_app_url_install(
            query_string=query,
            tenant_id=None,
            user_id=None,
        )


@pytest.mark.asyncio
async def test_claim_install_consumes_ticket_and_starts_oauth(
    monkeypatch: pytest.MonkeyPatch,
    shopify_secrets: str,
) -> None:
    redis = fake_aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(
        "app.integrations.shopify.service.get_redis",
        lambda _purpose: redis,
    )

    class _Maint:
        async def get_by_shop_domain(self, _shop: str) -> Any:
            return None

    monkeypatch.setattr(
        "app.integrations.shopify.service.ShopifyMaintenanceRepository",
        lambda _session: _Maint(),
    )

    items = [
        ("shop", "phase81-claim.myshopify.com"),
        ("timestamp", "1710000000"),
    ]
    digest = _sign_query(items, shopify_secrets)
    query = urlencode([*items, ("hmac", digest)])

    service = ShopifyService(session=None)  # type: ignore[arg-type]
    claim_url = await service.begin_app_url_install(
        query_string=query,
        tenant_id=None,
        user_id=None,
    )
    params = dict(parse_qsl(claim_url.split("?", 1)[1]))
    token = params["install_token"]

    tenant_id = uuid.uuid4()
    set_tenant_id(tenant_id)
    try:
        authorize_url, state = await service.claim_install(
            install_token=token,
            store_name=None,
            user_id=uuid.uuid4(),
        )
    finally:
        clear_context()

    assert "phase81-claim.myshopify.com/admin/oauth/authorize" in authorize_url
    assert state
    # Ticket is one-time.
    set_tenant_id(tenant_id)
    try:
        from app.integrations.shopify.exceptions import ShopifyInstallTicketError

        with pytest.raises(ShopifyInstallTicketError):
            await service.claim_install(
                install_token=token,
                store_name=None,
                user_id=None,
            )
    finally:
        clear_context()


# Silence unused AsyncSession import warning in type checkers that scan fixtures.
_ = AsyncSession
