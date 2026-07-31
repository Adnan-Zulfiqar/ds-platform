"""Product import and catalogue synchronisation.

The flow the phase brief describes, in one place:

    request → AliExpress client → contract schema → mapper → domain → repository

**Reuses the Phase 3 client unchanged.** No signing, no token handling and no
HTTP appears here — that logic exists once, was verified against the live
gateway, and duplicating it would mean two implementations to keep correct.

Every import writes a ``ProductImport`` row whether it succeeds or not. The
failures are the rows worth reading: "why is this product missing" is asked long
after the failure, and a system that only records successes cannot answer it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import require_tenant_id
from app.core.encryption import decrypt
from app.core.exceptions import NotFoundError, ValidationError
from app.integrations.aliexpress.catalog import parse_feed_products, parse_product_detail
from app.integrations.aliexpress.client import AliExpressClient
from app.integrations.aliexpress.exceptions import AliExpressError
from app.integrations.aliexpress.mapper import map_images, map_product, map_variants
from app.integrations.aliexpress.service import AliExpressService
from app.models.product import (
    ImportStatus,
    Product,
    ProductSource,
    ProductStatus,
)
from app.repositories.product import (
    ProductImageRepository,
    ProductImportRepository,
    ProductRepository,
    ProductVariantRepository,
)
from app.services.base import BaseService

#: AliExpress method names. Named constants because a typo in a method string
#: returns `InvalidApiPath` — an error that says nothing about which call site
#: was wrong.
_PRODUCT_DETAIL_METHOD = "aliexpress.ds.product.get"
_FEED_METHOD = "aliexpress.ds.recommend.feed.get"


class ProductImportService(BaseService):
    """Imports products from a connected supplier into a tenant's catalogue."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.products = ProductRepository(session)
        self.variants = ProductVariantRepository(session)
        self.images = ProductImageRepository(session)
        self.imports = ProductImportRepository(session)
        self.integration = AliExpressService(session)

    # -- Import -------------------------------------------------------------

    async def import_product(
        self,
        *,
        external_id: str,
        requested_by_user_id: uuid.UUID | None = None,
        ship_to_country: str = "US",
        currency: str = "USD",
    ) -> Product:
        """Import or refresh a single supplier product.

        **Idempotent.** Calling this twice with the same identifier updates the
        existing product rather than creating a second one — guaranteed by a
        unique constraint on ``(tenant_id, source, external_id)`` rather than by
        a check that a concurrent caller could race past.

        An in-flight import of the same product is rejected instead of started
        again, which is what a double-clicked button produces.
        """
        external_id = (external_id or "").strip()
        if not external_id:
            raise ValidationError("A product identifier is required.")

        in_progress = await self.imports.find_in_progress(
            source=ProductSource.ALIEXPRESS, external_id=external_id
        )
        if in_progress is not None:
            raise ValidationError(
                "An import for this product is already in progress.",
            )

        record = await self.imports.create(
            source=ProductSource.ALIEXPRESS,
            external_id=external_id,
            status=ImportStatus.RUNNING,
            requested_by_user_id=requested_by_user_id,
            started_at=datetime.now(UTC),
        )

        try:
            payload = await self._fetch_product(
                external_id, ship_to_country=ship_to_country, currency=currency
            )
        except AliExpressError as exc:
            await self._fail(record, code=type(exc).__name__, message=str(exc))
            raise

        detail = parse_product_detail(payload)
        if detail is None or not detail.product_id:
            # A well-formed envelope with no result. Normal during a refresh:
            # products get delisted, and that is information rather than a fault.
            await self._fail(
                record,
                code="product_not_found",
                message="AliExpress returned no product for this identifier.",
            )
            raise NotFoundError("Product not found on AliExpress.")

        values = map_product(detail)
        product = await self._upsert(values)

        await self.variants.delete_for_product(product.id)
        for variant in map_variants(detail):
            await self.variants.create(product_id=product.id, **variant)

        await self.images.delete_for_product(product.id)
        for image in map_images(detail):
            await self.images.create(product_id=product.id, **image)

        record.status = ImportStatus.SUCCEEDED
        record.product_id = product.id
        record.finished_at = datetime.now(UTC)
        await self.session.flush()

        # Load the children explicitly before returning.
        #
        # `selectin` eager loading applies when a product is *queried*, not to
        # one just built in this session — and the variants and images were
        # written through their own repositories, so the parent's collections
        # are stale. Touching them afterwards would trigger a lazy load, which
        # is implicit IO, which raises `MissingGreenlet` in async SQLAlchemy
        # rather than awaiting. Refreshing here is that load, made explicit.
        await self.session.refresh(product, attribute_names=["variants", "images"])

        self.logger.info(
            "product_imported",
            product_id=str(product.id),
            external_id=external_id,
            variants=len(detail.skus),
            images=len(detail.image_urls),
        )
        return product

    async def _upsert(self, values: dict[str, Any]) -> Product:
        """Create the product, or update it if this tenant already has it.

        ``status`` is preserved on update. A re-sync must not revert a product
        the tenant has activated back to draft — that would silently unpublish
        their catalogue every time prices refreshed.
        """
        existing = await self.products.get_by_external_id(
            source=ProductSource.ALIEXPRESS, external_id=values["external_id"]
        )

        values = {**values, "last_synced_at": datetime.now(UTC)}

        if existing is None:
            return await self.products.create(status=ProductStatus.DRAFT, **values)

        for field, value in values.items():
            setattr(existing, field, value)
        await self.session.flush()
        return existing

    async def _fail(self, record: Any, *, code: str, message: str) -> None:
        record.status = ImportStatus.FAILED
        record.error_code = code
        # Bounded: an upstream message is not ours and could be arbitrarily long.
        record.error_message = message[:2048]
        record.finished_at = datetime.now(UTC)
        await self.session.flush()
        self.logger.warning(
            "product_import_failed",
            external_id=record.external_id,
            error_code=code,
        )

    # -- Discovery ----------------------------------------------------------

    async def browse_feed(
        self,
        *,
        feed_name: str,
        page: int = 1,
        page_size: int = 20,
        country: str = "US",
        currency: str = "USD",
    ) -> list[dict[str, Any]]:
        """List products in a supplier feed, without importing them.

        Feeds rather than a search box: keyword search returns
        ``NGSELECTION_SEARCH_ERROR`` on this account, verified live in Phase 4
        contract discovery. Shipping a search field that cannot work would be
        worse than not shipping one.

        An unknown feed name returns an **empty list, not an error** — that is
        AliExpress's behaviour, and it is exactly how a wrong feed name goes
        unnoticed. Callers should treat empty as "check the name".
        """
        payload = await self._call(
            _FEED_METHOD,
            {
                "feed_name": feed_name,
                "page_no": str(page),
                "page_size": str(page_size),
                "country": country,
                "target_currency": currency,
                "target_language": "en",
            },
        )
        items = parse_feed_products(payload)
        return [
            {
                "external_id": item.identifier,
                "title": item.product_title,
                "image_url": item.product_main_image_url,
                "price": item.price,
                "currency": item.sale_price_currency,
                "orders": item.lastest_volume,
                "category_name": item.second_level_category_name,
            }
            for item in items
            if item.identifier
        ]

    # -- Supplier access ----------------------------------------------------

    async def _fetch_product(
        self, external_id: str, *, ship_to_country: str, currency: str
    ) -> dict[str, Any]:
        return await self._call(
            _PRODUCT_DETAIL_METHOD,
            {
                "product_id": external_id,
                "ship_to_country": ship_to_country,
                "target_currency": currency,
                "target_language": "en",
            },
        )

    async def _call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Issue a supplier call using the tenant's stored credentials.

        Refreshes the token first when it is near expiry, so a long import is
        not interrupted partway through by a lapse. That logic lives in
        :class:`AliExpressService` and is reused rather than repeated.
        """
        connection = await self.integration.require_connection()
        connection = await self.integration.refresh_if_needed(connection)

        if not connection.encrypted_access_token:
            raise ValidationError("AliExpress is not connected for this workspace.")

        client = AliExpressClient(
            app_key=connection.app_key,
            app_secret=decrypt(connection.encrypted_app_secret),
            tenant_id=str(require_tenant_id()),
            access_token=decrypt(connection.encrypted_access_token),
        )
        return await client.call(method, params)


__all__ = ["ProductImportService"]
