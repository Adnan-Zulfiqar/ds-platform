"""Catalogue client prefers the platform token over merchant OAuth."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import SecretStr

from app.core.context import set_tenant_id
from app.core.exceptions import ValidationError
from app.integrations.aliexpress.exceptions import (
    AliExpressCatalogNotConfiguredError,
    AliExpressNotConnectedError,
)
from app.integrations.aliexpress.service import AliExpressService


@pytest.fixture(autouse=True)
def _tenant() -> None:
    set_tenant_id(uuid4())


@pytest.mark.asyncio
async def test_client_for_catalog_uses_platform_token_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.integrations.aliexpress.service.settings.aliexpress.catalog_access_token",
        SecretStr("platform-catalog-token"),
    )
    monkeypatch.setattr(
        AliExpressService,
        "platform_credentials",
        staticmethod(lambda: ("app-key", "app-secret")),
    )
    service = AliExpressService(session=MagicMock())
    service.authenticated_client = AsyncMock(  # type: ignore[method-assign]
        side_effect=AssertionError("merchant OAuth must not run when catalog is set")
    )

    client = await service.client_for_catalog()

    assert client._access_token == "platform-catalog-token"
    assert client._app_key == "app-key"


@pytest.mark.asyncio
async def test_client_for_catalog_falls_back_to_merchant_oauth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.integrations.aliexpress.service.settings.aliexpress.catalog_access_token",
        None,
    )
    service = AliExpressService(session=MagicMock())
    sentinel: Any = object()
    service.authenticated_client = AsyncMock(return_value=sentinel)  # type: ignore[method-assign]

    assert await service.client_for_catalog() is sentinel


@pytest.mark.asyncio
async def test_client_for_catalog_raises_when_neither_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.integrations.aliexpress.service.settings.aliexpress.catalog_access_token",
        None,
    )
    service = AliExpressService(session=MagicMock())
    service.authenticated_client = AsyncMock(  # type: ignore[method-assign]
        side_effect=AliExpressNotConnectedError()
    )

    with pytest.raises(AliExpressCatalogNotConfiguredError):
        await service.client_for_catalog()


@pytest.mark.asyncio
async def test_client_for_catalog_maps_validation_not_connected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.integrations.aliexpress.service.settings.aliexpress.catalog_access_token",
        SecretStr("   "),
    )
    service = AliExpressService(session=MagicMock())
    service.authenticated_client = AsyncMock(  # type: ignore[method-assign]
        side_effect=ValidationError("AliExpress is not connected for this workspace.")
    )

    with pytest.raises(AliExpressCatalogNotConfiguredError):
        await service.client_for_catalog()
