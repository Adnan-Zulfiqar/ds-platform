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

from app.core.exceptions import ValidationError
from app.integrations.aliexpress.catalog import (
    parse_feed_products,
    parse_product_detail,
    require_usable_product_detail,
)
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
            # Inspect rsp_code before treating an empty envelope as "not found".
            # Live: 482 SHIP_TO_COUNTRY_PROHIBITED returns {has_whole_sale:false}
            # with no product_id — previously mislabeled as product not found.
            detail = require_usable_product_detail(
                payload,
                detail=parse_product_detail(payload),
                ship_to_country=ship_to_country,
            )
        except AliExpressError as exc:
            await self._fail(record, code=exc.code, message=str(exc))
            raise

        values = map_product(detail)
        product = await self._upsert(values)

        # Reconciled in place, not wiped and reinserted -- a variant/image's
        # id now survives a re-sync, matching by `external_variant_id`/`url`
        # (M20). Only rows the supplier no longer lists are actually deleted.
        await self.variants.sync_for_product(product.id, map_variants(detail))
        await self.images.sync_for_product(product.id, map_images(detail))

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

    #: Fields with a merchant-editable column and a `supplier_{field}` twin
    #: that always tracks the supplier (Product Editor stages 1-2: `title`/
    #: `brand` joined `description`). Every one of these is popped out of
    #: `values` in `_upsert` and handled by the loop there instead of the
    #: blanket `setattr` — the same reasoning that already keeps `status`
    #: out of a blanket update.
    _SYNCED_FIELDS = ("title", "brand", "description")

    async def _upsert(self, values: dict[str, Any]) -> Product:
        """Create the product, or update it if this tenant already has it.

        ``status`` is preserved on update. A re-sync must not revert a product
        the tenant has activated back to draft — that would silently unpublish
        their catalogue every time prices refreshed.

        ``title``/``brand``/``description`` get the same protection, by the
        same mechanism. Each ``supplier_{field}`` value (popped out of
        ``values`` here) always refreshes to what the supplier currently
        says. The merchant-editable twin only refreshes *while it still
        equals the previous supplier snapshot* — the moment a merchant edit
        (via ``PATCH /products/{id}``) diverges the two, a sync stops
        touching that field so the edit is never silently overwritten,
        while its ``supplier_*`` twin keeps tracking the supplier regardless.
        """
        existing = await self.products.get_by_external_id(
            source=ProductSource.ALIEXPRESS, external_id=values["external_id"]
        )

        supplier_values = {
            field: values.pop(f"supplier_{field}", None) for field in self._SYNCED_FIELDS
        }
        values = {**values, "last_synced_at": datetime.now(UTC)}

        if existing is None:
            synced = {field: supplier_values[field] for field in self._SYNCED_FIELDS}
            supplier_synced = {f"supplier_{field}": val for field, val in synced.items()}
            return await self.products.create(
                status=ProductStatus.DRAFT,
                **synced,
                **supplier_synced,
                **values,
            )

        for field in self._SYNCED_FIELDS:
            supplier_field = f"supplier_{field}"
            if getattr(existing, field) == getattr(existing, supplier_field):
                setattr(existing, field, supplier_values[field])
            setattr(existing, supplier_field, supplier_values[field])

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
        client = await self.integration.authenticated_client()
        return await client.call(method, params)


__all__ = ["ProductImportService"]
