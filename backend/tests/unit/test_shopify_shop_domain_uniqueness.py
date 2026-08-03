"""Shopify shop-domain uniqueness and webhook lookup (audit A-01)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from app.core.context import set_tenant_id
from app.integrations.shopify.exceptions import ShopifyShopTakenError
from app.integrations.shopify.service import ShopifyService
from app.models.integration import IntegrationStatus
from app.models.shopify import ShopifyConnection
from app.repositories.shopify import ShopifyMaintenanceRepository

pytestmark = pytest.mark.unit


def _compile(query: object) -> str:
    return str(
        query.compile(  # type: ignore[attr-defined]
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


class TestMaintenanceShopDomainLookup:
    @pytest.mark.asyncio
    async def test_connected_lookup_filters_by_domain_and_status_without_a_row_cap(
        self,
    ) -> None:
        """Webhook resolution must be an indexed equality, not ``LIMIT 1000``."""
        session = MagicMock()
        repo = ShopifyMaintenanceRepository(session)
        captured: list[object] = []

        async def _capture(query: object) -> MagicMock:
            captured.append(query)
            result = MagicMock()
            result.scalar_one_or_none.return_value = None
            return result

        session.execute = _capture
        await repo.get_connected_by_shop_domain("mriy3s-zv.myshopify.com")

        assert len(captured) == 1
        sql = _compile(captured[0])
        assert "shopify_connections.shop_domain" in sql
        assert "mriy3s-zv.myshopify.com" in sql
        assert "connected" in sql
        assert "LIMIT" not in sql.upper()


class TestBeginConnectionRejectsForeignShop:
    @pytest.mark.asyncio
    async def test_raises_when_another_tenant_owns_the_shop(
        self, tenant_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        set_tenant_id(tenant_id)
        session = MagicMock()
        service = ShopifyService(session)

        monkeypatch.setattr(
            "app.integrations.shopify.service.is_encryption_configured",
            lambda: True,
        )
        monkeypatch.setattr(
            service,
            "_require_app_credentials",
            lambda: ("key", "secret"),
        )

        foreign = ShopifyConnection()
        foreign.tenant_id = uuid.uuid4()
        foreign.shop_domain = "taken-shop.myshopify.com"

        with patch("app.integrations.shopify.service.ShopifyMaintenanceRepository") as maint_cls:
            maint_cls.return_value.get_by_shop_domain = AsyncMock(return_value=foreign)
            with pytest.raises(ShopifyShopTakenError):
                await service.begin_connection(
                    shop="taken-shop.myshopify.com",
                    store_name=None,
                    user_id=None,
                )

    @pytest.mark.asyncio
    async def test_allows_reconnect_for_the_same_tenant(
        self, tenant_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        set_tenant_id(tenant_id)
        session = MagicMock()
        service = ShopifyService(session)

        monkeypatch.setattr(
            "app.integrations.shopify.service.is_encryption_configured",
            lambda: True,
        )
        monkeypatch.setattr(
            service,
            "_require_app_credentials",
            lambda: ("key", "secret"),
        )
        monkeypatch.setattr(service, "_store_state", AsyncMock())

        own = ShopifyConnection()
        own.tenant_id = tenant_id
        own.shop_domain = "mine.myshopify.com"
        own.status = IntegrationStatus.CONNECTED

        with patch("app.integrations.shopify.service.ShopifyMaintenanceRepository") as maint_cls:
            maint_cls.return_value.get_by_shop_domain = AsyncMock(return_value=own)
            with patch(
                "app.integrations.shopify.service.build_authorization_url",
                return_value="https://mine.myshopify.com/admin/oauth/authorize",
            ):
                url, state = await service.begin_connection(
                    shop="mine.myshopify.com",
                    store_name=None,
                    user_id=None,
                )

        assert "authorize" in url
        assert state
