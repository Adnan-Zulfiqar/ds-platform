"""Unit tests for server-authoritative publish readiness."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.integration import IntegrationStatus
from app.models.store import StorePlatform
from app.services.publish_readiness import (
    CHANNEL_SHOPIFY,
    CODE_DESCRIPTION_EMPTY,
    CODE_DESTINATION_MISMATCH,
    CODE_DRAFT_VERSION_STALE,
    CODE_IMAGES_MISSING,
    CODE_PUBLISHING_DISABLED,
    CODE_SELLING_CURRENCY_MISMATCH,
    CODE_STORE_DISCONNECTED,
    CODE_STORE_PAUSED,
    CODE_STORE_REQUIRED,
    CODE_TITLE_THIN,
    CODE_UNSUPPORTED_CHANNEL,
    PublishReadinessService,
)

pytestmark = pytest.mark.unit


class _Image:
    def __init__(self, *, url: str = "https://cdn.example/a.png") -> None:
        self.url = url
        self.deleted_at = None


class _Variant:
    def __init__(
        self,
        *,
        sell_price: Decimal | None = Decimal("9.99"),
        sell_price_currency: str | None = "USD",
    ) -> None:
        self.id = uuid.uuid4()
        self.sell_price = sell_price
        self.sell_price_currency = sell_price_currency
        self.is_enabled = True
        self.deleted_at = None
        self.label = "Default"
        self.external_variant_id = "sku-1"


class _Product:
    def __init__(self, **overrides: Any) -> None:
        now = datetime.now(UTC)
        self.id = overrides.get("id", uuid.uuid4())
        self.title = overrides.get("title", "A clear product title")
        self.description = overrides.get("description", "<p>Body</p>")
        self.updated_at = overrides.get("updated_at", now)
        self.import_ship_to_country = overrides.get("import_ship_to_country", "US")
        self.variants = overrides.get("variants", [_Variant()])
        self.images = overrides.get("images", [_Image()])


class _Store:
    def __init__(self, **overrides: Any) -> None:
        self.id = overrides.get("id", uuid.uuid4())
        self.platform = overrides.get("platform", StorePlatform.SHOPIFY)
        self.settings = overrides.get("settings", {"countryCode": "US"})
        self.currency = overrides.get("currency", "USD")
        self.currency_last_synced_at = overrides.get("currency_last_synced_at", datetime.now(UTC))
        self.sync_paused_at = overrides.get("sync_paused_at")


class _Connection:
    def __init__(self, *, status: IntegrationStatus = IntegrationStatus.CONNECTED) -> None:
        self.status = status


def _service(
    *,
    product: _Product,
    store: _Store | None = None,
    connection: _Connection | None = None,
) -> PublishReadinessService:
    service = PublishReadinessService.__new__(PublishReadinessService)
    service.session = MagicMock()
    service.products = MagicMock()
    service.stores = MagicMock()
    service.flags = MagicMock()
    service.flags.is_enabled = AsyncMock(return_value=True)
    service.shopify = MagicMock()
    service._sync = MagicMock()
    service._load_product = AsyncMock(return_value=product)  # type: ignore[method-assign]
    if store is None:
        service.stores.get_by_id_or_raise = AsyncMock(side_effect=NotFoundError("Store not found."))
    else:
        service.stores.get_by_id_or_raise = AsyncMock(return_value=store)
    service.shopify.connections.get_by_store = AsyncMock(return_value=connection)
    # Real assert helpers for currency / destination — attach a real sync stub.
    from app.integrations.shopify.sync import ShopifySyncService

    service._sync = ShopifySyncService.__new__(ShopifySyncService)
    return service


@pytest.mark.asyncio
async def test_store_required_blocks_without_store() -> None:
    product = _Product()
    result = await _service(product=product).evaluate(
        channel=CHANNEL_SHOPIFY,
        product_id=product.id,
        store_id=None,
    )
    assert result.can_publish is False
    assert [item.code for item in result.blockers] == [CODE_STORE_REQUIRED]


@pytest.mark.asyncio
async def test_unsupported_channel_is_refused() -> None:
    product = _Product()
    result = await _service(product=product).evaluate(
        channel="etsy",
        product_id=product.id,
        store_id=uuid.uuid4(),
    )
    assert result.can_publish is False
    assert result.blockers[0].code == CODE_UNSUPPORTED_CHANNEL


@pytest.mark.asyncio
async def test_foreign_store_raises_not_found() -> None:
    product = _Product()
    with pytest.raises(NotFoundError):
        await _service(product=product, store=None).evaluate(
            channel=CHANNEL_SHOPIFY,
            product_id=product.id,
            store_id=uuid.uuid4(),
        )


@pytest.mark.asyncio
async def test_disconnected_store_is_a_blocker() -> None:
    product = _Product()
    store = _Store()
    result = await _service(
        product=product,
        store=store,
        connection=_Connection(status=IntegrationStatus.ERROR),
    ).evaluate(
        channel=CHANNEL_SHOPIFY,
        product_id=product.id,
        store_id=store.id,
    )
    assert result.can_publish is False
    assert any(item.code == CODE_STORE_DISCONNECTED for item in result.blockers)


@pytest.mark.asyncio
async def test_a_store_paused_by_an_operator_is_a_blocker() -> None:
    """D-019: an operator's pause stops new publishes to the store."""
    product = _Product()
    store = _Store(sync_paused_at=datetime.now(UTC))
    result = await _service(product=product, store=store, connection=_Connection()).evaluate(
        channel=CHANNEL_SHOPIFY,
        product_id=product.id,
        store_id=store.id,
    )
    assert result.can_publish is False
    assert any(item.code == CODE_STORE_PAUSED for item in result.blockers)


@pytest.mark.asyncio
async def test_publishing_switched_off_for_the_workspace_is_a_blocker() -> None:
    """D-019: the ``channel_publishing`` switch stops every channel publish."""
    product = _Product()
    store = _Store()
    service = _service(product=product, store=store, connection=_Connection())
    service.flags.is_enabled = AsyncMock(return_value=False)
    result = await service.evaluate(
        channel=CHANNEL_SHOPIFY, product_id=product.id, store_id=store.id
    )
    assert result.can_publish is False
    assert any(item.code == CODE_PUBLISHING_DISABLED for item in result.blockers)


@pytest.mark.asyncio
async def test_destination_mismatch_is_a_blocker() -> None:
    product = _Product(import_ship_to_country="GB")
    store = _Store(settings={"countryCode": "US"})
    result = await _service(
        product=product,
        store=store,
        connection=_Connection(),
    ).evaluate(
        channel=CHANNEL_SHOPIFY,
        product_id=product.id,
        store_id=store.id,
    )
    assert result.can_publish is False
    assert any(item.code == CODE_DESTINATION_MISMATCH for item in result.blockers)


@pytest.mark.asyncio
async def test_currency_mismatch_is_a_blocker() -> None:
    product = _Product(variants=[_Variant(sell_price=Decimal("6.60"), sell_price_currency="GBP")])
    store = _Store(currency="USD")
    result = await _service(
        product=product,
        store=store,
        connection=_Connection(),
    ).evaluate(
        channel=CHANNEL_SHOPIFY,
        product_id=product.id,
        store_id=store.id,
    )
    assert result.can_publish is False
    assert any(item.code == CODE_SELLING_CURRENCY_MISMATCH for item in result.blockers)


@pytest.mark.asyncio
async def test_recommendations_do_not_set_can_publish_false() -> None:
    product = _Product(title="x", description="", images=[])
    store = _Store()
    result = await _service(
        product=product,
        store=store,
        connection=_Connection(),
    ).evaluate(
        channel=CHANNEL_SHOPIFY,
        product_id=product.id,
        store_id=store.id,
    )
    assert result.can_publish is True
    codes = [item.code for item in result.recommendations]
    assert CODE_TITLE_THIN in codes
    assert CODE_DESCRIPTION_EMPTY in codes
    assert CODE_IMAGES_MISSING in codes
    assert result.blockers == ()


@pytest.mark.asyncio
async def test_blocker_and_recommendation_order_is_deterministic() -> None:
    product = _Product(title="x", description="", images=[], import_ship_to_country="GB")
    store = _Store(settings={"countryCode": "US"})
    service = _service(product=product, store=store, connection=_Connection())
    first = await service.evaluate(
        channel=CHANNEL_SHOPIFY, product_id=product.id, store_id=store.id
    )
    second = await service.evaluate(
        channel=CHANNEL_SHOPIFY, product_id=product.id, store_id=store.id
    )
    assert [item.code for item in first.blockers] == [item.code for item in second.blockers]
    assert [item.code for item in first.recommendations] == [
        item.code for item in second.recommendations
    ]
    assert [item.code for item in first.blockers] == sorted(item.code for item in first.blockers)
    assert [item.code for item in first.recommendations] == sorted(
        item.code for item in first.recommendations
    )


@pytest.mark.asyncio
async def test_stale_version_enforced_on_require_publishable() -> None:
    now = datetime.now(UTC)
    product = _Product(updated_at=now)
    store = _Store()
    service = _service(product=product, store=store, connection=_Connection())
    with pytest.raises(ConflictError) as exc:
        await service.require_publishable(
            channel=CHANNEL_SHOPIFY,
            product_id=product.id,
            store_id=store.id,
            expected_updated_at=datetime(2020, 1, 1, tzinfo=UTC),
        )
    assert CODE_DRAFT_VERSION_STALE in str(exc.value.details)


@pytest.mark.asyncio
async def test_require_publishable_raises_validation_when_blocked() -> None:
    product = _Product()
    store = _Store()
    service = _service(
        product=product,
        store=store,
        connection=_Connection(status=IntegrationStatus.ERROR),
    )
    with pytest.raises(ValidationError) as exc:
        await service.require_publishable(
            channel=CHANNEL_SHOPIFY,
            product_id=product.id,
            store_id=store.id,
        )
    assert exc.value.details.get("reason") == "publish_blocked"
    assert CODE_STORE_DISCONNECTED in str(exc.value.details.get("blocker_codes"))


@pytest.mark.asyncio
async def test_publish_product_skips_provider_when_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.integrations.shopify.sync import ShopifySyncService

    service = ShopifySyncService.__new__(ShopifySyncService)
    service.session = MagicMock()
    service.shopify = MagicMock()
    service.shopify.client_for_store = AsyncMock()
    service._load_product = AsyncMock()  # type: ignore[method-assign]
    service.listings = MagicMock()

    async def _blocked(**_: Any) -> Any:
        raise ValidationError(
            "Store disconnected",
            details={"reason": "publish_blocked", "blocker_codes": CODE_STORE_DISCONNECTED},
        )

    fake = MagicMock()
    fake.require_publishable = AsyncMock(side_effect=_blocked)
    monkeypatch.setattr(
        "app.services.publish_readiness.PublishReadinessService",
        lambda _session: fake,
    )

    with pytest.raises(ValidationError):
        await service.publish_product(store_id=uuid.uuid4(), product_id=uuid.uuid4())

    service.shopify.client_for_store.assert_not_awaited()
    service._load_product.assert_not_awaited()
