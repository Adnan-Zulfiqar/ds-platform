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
from app.database.session import session_factory
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
    ProductImport,
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
from app.services.import_destination import ImportDestinationService

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
        self.destinations = ImportDestinationService(session)

    # -- Import -------------------------------------------------------------

    async def import_product(
        self,
        *,
        external_id: str,
        requested_by_user_id: uuid.UUID | None = None,
        ship_to_country: str | None = None,
        currency: str | None = None,
        store_id: uuid.UUID | None = None,
    ) -> Product:
        """Import or refresh a single supplier product.

        **Idempotent.** Calling this twice with the same identifier updates the
        existing product rather than creating a second one — guaranteed by a
        unique constraint on ``(tenant_id, source, external_id)`` rather than by
        a check that a concurrent caller could race past.

        An in-flight import of the same product is rejected instead of started
        again, which is what a double-clicked button produces.

        ``ship_to_country`` is resolved via :class:`ImportDestinationService`
        when omitted — never silently forced to ``US``. ``currency`` is
        resolved the same way (M24B): a caller that omits it — every
        refresh/sync path does — gets the destination's mapped market currency
        (GB -> GBP, US -> USD) or a verified store's currency, never a
        hardcoded USD default. That default was the actual root cause of a
        live-traced bug: a GB-destined refresh silently asked AliExpress for
        USD pricing because nothing overrode it. See
        ``docs/ALIEXPRESS_LOCALIZED_PRICING.md``.
        """
        external_id = (external_id or "").strip()
        if not external_id:
            raise ValidationError("A product identifier is required.")

        destination = await self.destinations.resolve(
            ship_to_country=ship_to_country,
            store_id=store_id,
        )
        target_currency, currency_source = await self.destinations.resolve_currency(
            currency=currency,
            ship_to_country=destination,
            store_id=store_id,
        )

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
            ship_to_country=destination,
            currency=target_currency,
        )

        try:
            payload = await self._fetch_product(
                external_id, ship_to_country=destination, currency=target_currency
            )
            # Inspect rsp_code before treating an empty envelope as "not found".
            # Live: 482 SHIP_TO_COUNTRY_PROHIBITED returns {has_whole_sale:false}
            # with no product_id — previously mislabeled as product not found.
            detail = require_usable_product_detail(
                payload,
                detail=parse_product_detail(payload),
                ship_to_country=destination,
            )
        except AliExpressError as exc:
            await self._fail(
                record,
                code=exc.code,
                message=str(exc),
                result_category=exc.code,
            )
            raise

        values = map_product(detail)
        checked_at = datetime.now(UTC)
        values["import_ship_to_country"] = destination
        values["import_ship_to_checked_at"] = checked_at
        # The currency actually requested for this import/refresh -- distinct
        # from `currency` (map_product's SKU-derived selling currency) and
        # from `supplier_native_currency` (the seller's own listing currency,
        # which AliExpress never localizes regardless of what was requested).
        # Reused by the Pricing workspace to know whether a draft's stored
        # price is still asking for the currency the merchant actually wants.
        values["import_currency"] = target_currency
        # Prefer the requested destination on the product snapshot so the draft
        # editor shows "Imported for: GB" even when logistics DTO omits it.
        values["ship_to_country"] = values.get("ship_to_country") or destination
        product = await self._upsert(values)

        # Reconciled in place, not wiped and reinserted -- a variant/image's
        # id now survives a re-sync, matching by `external_variant_id`/`url`
        # (M20). Only rows the supplier no longer lists are actually deleted.
        await self.variants.sync_for_product(product.id, map_variants(detail))
        await self.images.sync_for_product(product.id, map_images(detail))

        record.status = ImportStatus.SUCCEEDED
        record.product_id = product.id
        record.result_category = "success"
        record.finished_at = checked_at
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
            ship_to_country=destination,
            target_currency=target_currency,
            currency_source=currency_source,
        )
        return product

    async def retry_import(
        self,
        import_id: uuid.UUID,
        *,
        requested_by_user_id: uuid.UUID | None = None,
    ) -> Product:
        """Resubmit a specific failed import using its own stored parameters.

        DSers-parity M1: a failed import must remain retryable without the
        merchant re-typing the product id/URL and destination from memory —
        this reads them back off the audit row instead. Deliberately narrow:
        only a ``FAILED`` attempt may be retried (an in-flight one is already
        covered by ``find_in_progress``'s duplicate-submission guard inside
        :meth:`import_product`, and a ``SUCCEEDED`` one has a product to
        refresh through ``POST /products/{id}/sync`` instead).

        Reuses :meth:`import_product` unchanged, so retrying carries the exact
        same idempotency guarantee every other import path already has: the
        `(tenant_id, source, external_id)` unique constraint means a retry
        that happens to race a still-running attempt for the same product
        updates the one row rather than creating a second draft — see
        ``ProductRepository`` / ``BaseRepository._translate_integrity_error``.
        This method creates a new ``ProductImport`` audit row for the retry
        itself, consistent with "rows are never deleted, every attempt is
        recorded" — it does not mutate the original failed row.
        """
        record = await self.imports.get_by_id_or_raise(import_id)
        if record.status is not ImportStatus.FAILED:
            raise ValidationError(
                "Only a failed import can be retried.",
                details={"status": record.status.value},
            )

        return await self.import_product(
            external_id=record.external_id,
            requested_by_user_id=requested_by_user_id,
            ship_to_country=record.ship_to_country,
            currency=record.currency,
        )

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

    async def _fail(
        self,
        record: Any,
        *,
        code: str,
        message: str,
        result_category: str | None = None,
    ) -> None:
        record.status = ImportStatus.FAILED
        record.error_code = code
        record.result_category = result_category or code
        # Bounded: an upstream message is not ours and could be arbitrarily long.
        record.error_message = message[:2048]
        record.finished_at = datetime.now(UTC)
        await self.session.flush()
        self.logger.warning(
            "product_import_failed",
            external_id=record.external_id,
            error_code=code,
            ship_to_country=getattr(record, "ship_to_country", None),
        )
        await self._persist_failure_durably(record)

    async def _persist_failure_durably(self, record: ProductImport) -> None:
        """Commit the failure row on its own connection, outside this request's transaction.

        ``import_product`` re-raises immediately after ``_fail`` sets these
        fields, so the caller (a router handler) sees the exception and never
        reaches a normal return. ``app.api.deps.get_db_session`` rolls back the
        *entire* request transaction when a handler raises — correct for every
        other write in the request, but it would silently erase the one row
        whose whole purpose (see this module's docstring and
        ``ProductImportRepository``) is to survive the failure: "why is this
        product missing" is asked long after the failure, by someone who was
        never told the failed attempt happened at all if this row never lands.
        Verified live: a real failed import (no AliExpress connection) left
        zero ``product_imports`` rows before this fix, despite ``_fail``
        flushing the update — the flush was rolled back with everything else.

        A short-lived session on an independent connection commits just this
        one row, so it survives regardless of what happens to ``self.session``.
        Deliberately narrow: this does not change commit/rollback semantics for
        any other write, in this service or elsewhere.

        Gets its own primary key rather than reusing ``record.id``. Reusing it
        deadlocked every time: ``self.session``'s transaction is still open at
        this point (nothing has raised yet), holding an uncommitted row with
        that same id, and Postgres blocks a second insert of the same primary
        key until the first transaction resolves -- which never happens,
        because that transaction cannot resolve until *this* method returns.
        Nothing needs the two ids to match: the failed request's error
        response carries no ``ProductImport`` id, and every later read (Import
        History, retry) queries by whatever id this independent commit
        actually produced.

        Best-effort, deliberately: this is a secondary write recording that
        the *real* operation failed. Letting a problem here propagate would
        replace a clean, specific error (409 not-connected, 422 ship-to
        rejected, ...) with an opaque 503 -- trading a merchant-facing error
        they can act on for one they cannot, over a write whose entire
        purpose is auxiliary. A logged failure here is a gap in the audit
        trail, not a reason to hide the original failure from the caller.
        """
        values = record.to_dict(exclude={"id", "created_at", "updated_at"})
        durable_session = session_factory()
        try:
            durable_session.add(ProductImport(**values))
            await durable_session.commit()
        except Exception:
            self.logger.error(
                "product_import_failure_not_persisted_durably",
                external_id=record.external_id,
                exc_info=True,
            )
        finally:
            await durable_session.close()

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
