"""Product catalogue data access.

Every class here extends :class:`TenantScopedRepository`, so the tenant
predicate is inherited rather than written per query. That is the whole point:
a cross-tenant leak in this layer is the worst failure mode the platform has,
and the defence is that the correct behaviour is the default one.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any, Literal

from sqlalchemy import ColumnElement, Exists, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import require_tenant_id
from app.core.exceptions import NotFoundError, ShopifyPublishBusyError
from app.models.product import (
    ImportStatus,
    Product,
    ProductImage,
    ProductImport,
    ProductMarketplaceAttributes,
    ProductSource,
    ProductStatus,
    ProductVariant,
    ProductVersion,
)
from app.models.shopify import ListingSyncStatus, StoreListing
from app.repositories.base import TenantScopedRepository
from app.schemas.common import ListQueryParams

#: PostgreSQL SQLSTATE for ``lock_timeout`` expiring on a row lock.
_LOCK_NOT_AVAILABLE = "55P03"


def _is_lock_timeout(exc: DBAPIError) -> bool:
    """Whether this driver error is PostgreSQL refusing to keep waiting.

    Matched on SQLSTATE rather than message wording. Same check as the
    Shopify connection row lock — duplicated here so the product repository
    does not import from the Shopify repository package.
    """
    original = getattr(exc, "orig", None)
    for attribute in ("sqlstate", "pgcode"):
        if getattr(original, attribute, None) == _LOCK_NOT_AVAILABLE:
            return True
    return False


class ProductRepository(TenantScopedRepository[Product]):
    """Reads and writes for the catalogue."""

    #: An allowlist, not a denylist. ``sort_by`` arrives from the query string,
    #: and resolving an arbitrary client string to a column is how injection
    #: happens.
    sortable_fields = frozenset(
        {
            "created_at",
            "updated_at",
            "title",
            "status",
            "cost_price_min",
            "sell_price",
            "stock_quantity",
            "last_synced_at",
        }
    )

    searchable_fields = frozenset({"title", "external_id", "supplier_name"})

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Product)

    def _synced_listing_exists(self) -> Exists:
        """Whether this product has at least one successfully published listing.

        Drafts vs Products are lifecycle projections over the same product row
        (Product Workspace V2). Publication truth is channel-specific
        ``StoreListing`` state — not ``Product.status``, which import leaves as
        ``draft`` and Shopify publish does not flip.
        """
        return (
            select(StoreListing.id)
            .where(
                StoreListing.product_id == Product.id,
                StoreListing.tenant_id == Product.tenant_id,
                StoreListing.status == ListingSyncStatus.SYNCED,
                StoreListing.deleted_at.is_(None),
            )
            .exists()
        )

    def _publication_predicate(
        self, publication: Literal["draft", "published"]
    ) -> ColumnElement[bool]:
        exists = self._synced_listing_exists()
        return exists if publication == "published" else ~exists

    def _variant_count_column(self) -> ColumnElement[int]:
        """Correlated ``COUNT`` of a product's variants, as a scalar column.

        Rides along in the same list query as its own selected column —
        one query for a page, not one query per row. ``product_variants``
        is indexed on ``product_id`` (``ix_variants_tenant_product`` leads
        with ``tenant_id``, but a correlated subquery scoped to one already-
        tenant-filtered ``Product.id`` uses it as the second key), so this
        adds a cheap indexed lookup per returned row, not a table scan.
        """
        return (
            select(func.count(ProductVariant.id))
            .where(ProductVariant.product_id == Product.id)
            .correlate(Product)
            .scalar_subquery()
        )

    async def list_by_publication(
        self,
        params: ListQueryParams,
        *,
        publication: Literal["draft", "published"],
    ) -> tuple[Sequence[tuple[Product, int]], int]:
        """Page of drafts or published products for the workspace lists.

        Each row pairs a ``Product`` with its variant count — accurate always,
        never a placeholder: a ``Product`` row only exists after a successful
        import, and that same transaction syncs its variants
        (``ProductImportService._upsert`` -> ``variants.sync_for_product``),
        so a committed row's variant count is never "unknown", only
        legitimately zero for a single-SKU listing. There is nothing here for
        a failed import to be inaccurate about — a failure never creates a
        ``Product`` row, so it never reaches this query at all.
        """
        query = self._base_query().where(self._publication_predicate(publication))
        query = self._apply_search(query, params)

        count_query = select(func.count()).select_from(query.subquery())
        total = (await self.session.execute(count_query)).scalar_one()

        query = self._apply_sorting(query, params)
        query = query.offset(params.offset).limit(params.limit)
        query = query.add_columns(self._variant_count_column().label("variant_count"))
        rows = (await self.session.execute(query)).all()
        return [(row[0], row[1]) for row in rows], total

    async def list_drafts(
        self, params: ListQueryParams
    ) -> tuple[Sequence[tuple[Product, int]], int]:
        """Products with no synced channel listing — the Drafts inbox."""
        return await self.list_by_publication(params, publication="draft")

    async def list_published(
        self, params: ListQueryParams
    ) -> tuple[Sequence[tuple[Product, int]], int]:
        """Products with at least one synced StoreListing — the Products page."""
        return await self.list_by_publication(params, publication="published")

    async def count_workspace(self) -> dict[str, int]:
        """Draft and published totals for sidebar badges."""
        draft_q = select(func.count()).select_from(
            self._base_query().where(self._publication_predicate("draft")).subquery()
        )
        published_q = select(func.count()).select_from(
            self._base_query().where(self._publication_predicate("published")).subquery()
        )
        draft_count = int((await self.session.execute(draft_q)).scalar_one())
        published_count = int((await self.session.execute(published_q)).scalar_one())
        return {"drafts": draft_count, "products": published_count}

    async def get_by_external_id(
        self, *, source: ProductSource, external_id: str
    ) -> Product | None:
        """Find a product by its supplier identifier.

        The lookup that makes import idempotent. Tenant-filtered through
        ``_base_query``, so one tenant's import can never find - or overwrite -
        another tenant's row for the same supplier product.
        """
        query = self._base_query().where(
            Product.source == source,
            Product.external_id == external_id,
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def find_duplicate(
        self, *, source: ProductSource, external_id: str
    ) -> tuple[Product, bool] | None:
        """Same lookup as ``get_by_external_id``, plus whether it's published.

        Backs the authoritative duplicate-import check
        (``GET /products/import/check``): the frontend asks this instead of
        scanning whatever page of Drafts happens to be cached, so a match
        outside the first page is still found. One query, not two — the
        publication flag rides along as a correlated subquery, the same
        ``_synced_listing_exists()`` that ``list_by_publication`` uses, so
        this can never disagree with what Drafts vs Products actually shows.
        """
        query = self._base_query().where(
            Product.source == source,
            Product.external_id == external_id,
        )
        query = query.add_columns(self._synced_listing_exists().label("is_published"))
        result = await self.session.execute(query)
        row = result.first()
        if row is None:
            return None
        product, is_published = row
        return product, bool(is_published)

    async def get_by_id_with_publication(
        self, product_id: uuid.UUID
    ) -> tuple[Product, bool] | None:
        """Fetch a tenant-owned product together with whether it is published.

        Same shape as :meth:`find_duplicate`, reusing the identical
        ``_synced_listing_exists()`` predicate so "is this published" can
        never disagree between the two lookup paths. Backs the drafts
        editor's publish-state edit gate (M2A) — a product already pushed
        to a channel is edited from Products, not through ``/drafts/{id}``.
        """
        query = self._base_query().where(Product.id == product_id)
        query = query.add_columns(self._synced_listing_exists().label("is_published"))
        result = await self.session.execute(query)
        row = result.first()
        if row is None:
            return None
        product, is_published = row
        return product, bool(is_published)

    async def get_by_slug(self, slug: str) -> Product | None:
        """Find a product by its merchant-set URL slug, within this tenant.

        Used only to pre-check uniqueness before a `PATCH` writes one —
        `uq_products_tenant_slug` is the real backstop, this just turns a raw
        constraint violation into a clean, specific error before the insert.
        """
        query = self._base_query().where(Product.slug == slug)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def lock_for_update(
        self, product_id: uuid.UUID, *, timeout_ms: int | None = None
    ) -> Product | None:
        """Take this tenant's product row for the rest of the transaction.

        Shopify publish is list-decide-create: two concurrent publishes of the
        same draft can both miss ``StoreListing``, both miss the remote handle,
        and both create a Shopify product. Serialising on the product row closes
        that window across workers and processes — an in-process lock would not.

        Tenant-scoped via ``_base_query``: a foreign id finds nothing (404 at
        the service layer), never a 403. ``populate_existing`` forces a fresh
        read so a guard does not inspect a stale identity-map copy.

        ``timeout_ms`` bounds the wait with PostgreSQL ``SET LOCAL lock_timeout``
        (transaction-scoped, reset immediately after acquire) so a wedged
        publish cannot hold HTTP workers forever. Expiry becomes
        ``ShopifyPublishBusyError`` rather than a raw 500.
        """
        query = (
            self._base_query()
            .where(Product.id == product_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if timeout_ms is None:
            result = await self.session.execute(query)
            return result.scalar_one_or_none()

        await self.session.execute(text(f"SET LOCAL lock_timeout = '{int(timeout_ms)}ms'"))
        try:
            result = await self.session.execute(query)
        except DBAPIError as exc:
            if _is_lock_timeout(exc):
                raise ShopifyPublishBusyError(details={"timeout_ms": timeout_ms}) from exc
            raise
        finally:
            try:
                await self.session.execute(text("SET LOCAL lock_timeout = DEFAULT"))
            except DBAPIError:  # pragma: no cover - transaction already aborted
                pass
        return result.scalar_one_or_none()

    async def count_by_status(self) -> dict[str, int]:
        """Catalogue totals per status, for the products page header.

        Builds the tenant predicate from ``_base_query`` rather than restating
        it. An aggregate cannot use that query directly because it selects whole
        entities, and re-typing the filter is exactly how one gets omitted.
        """
        where_clause = self._base_query().whereclause
        query = select(Product.status, func.count(Product.id)).select_from(Product)
        if where_clause is not None:
            query = query.where(where_clause)
        result = await self.session.execute(query.group_by(Product.status))
        return {str(status.value): count for status, count in result.all()}

    async def list_for_inventory(
        self,
        *,
        store_id: uuid.UUID | None = None,
        limit: int = 500,
    ) -> list[Product]:
        """Catalogue rows eligible for an inventory sweep."""
        query = self._base_query().where(
            Product.status.in_((ProductStatus.ACTIVE, ProductStatus.DRAFT))
        )
        if store_id is not None:
            query = query.where(Product.store_id == store_id)
        query = query.order_by(Product.last_synced_at.asc().nulls_first()).limit(limit)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def count_for_store(self, store_id: uuid.UUID) -> int:
        where_clause = self._base_query().whereclause
        query = (
            select(func.count(Product.id)).select_from(Product).where(Product.store_id == store_id)
        )
        if where_clause is not None:
            query = query.where(where_clause)
        return int((await self.session.execute(query)).scalar_one())


class ProductVariantRepository(TenantScopedRepository[ProductVariant]):
    """Variants. Written by the importer, read with their parent."""

    sortable_fields = frozenset({"created_at", "updated_at", "cost_price", "stock_quantity"})

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, ProductVariant)

    async def list_for_product(self, product_id: uuid.UUID) -> list[ProductVariant]:
        query = self._base_query().where(ProductVariant.product_id == product_id)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def sync_for_product(self, product_id: uuid.UUID, mapped: list[dict[str, Any]]) -> None:
        """Reconcile a product's variants with the supplier's current list.

        Matches existing rows by `external_variant_id` (unique per product —
        `uq_variants_product_external`) and updates in place, rather than
        deleting every row and reinserting fresh ones on every sync (the
        pre-Product-Editor behaviour). A variant's id now stays stable across
        syncs, which is what lets anything come to reference one by id — a
        future `PATCH`, an order line item — without that reference dangling
        the next time the product refreshes.

        A variant the supplier no longer lists is still **hard deleted**,
        exactly as before: it is not history worth keeping, it is a
        purchasable option that no longer exists, and a soft-deleted row
        risks being offered for sale by a query that forgets the filter.
        """
        existing = {v.external_variant_id: v for v in await self.list_for_product(product_id)}
        seen: set[str] = set()

        # Merchant listing fields are never present in the supplier map and must
        # not be wiped if a caller accidentally includes them — strip to the
        # supplier-owned keys only on update.
        supplier_keys = (
            "external_variant_id",
            "external_attributes",
            "label",
            "cost_price",
            "list_price",
            "currency",
            "stock_quantity",
            "image_url",
        )

        for values in mapped:
            external_variant_id = values["external_variant_id"]
            seen.add(external_variant_id)
            current = existing.get(external_variant_id)
            supplier_values = {key: values[key] for key in supplier_keys if key in values}
            if current is None:
                await self.create(product_id=product_id, **supplier_values)
            else:
                await self.update(current, **supplier_values)

        for external_variant_id, row in existing.items():
            if external_variant_id not in seen:
                await self.session.delete(row)
        await self.session.flush()


class ProductImageRepository(TenantScopedRepository[ProductImage]):
    """Images, ordered by position."""

    sortable_fields = frozenset({"created_at", "position"})

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, ProductImage)

    async def list_for_product(self, product_id: uuid.UUID) -> list[ProductImage]:
        query = (
            self._base_query()
            .where(ProductImage.product_id == product_id)
            .order_by(ProductImage.position)
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def sync_for_product(self, product_id: uuid.UUID, mapped: list[dict[str, Any]]) -> None:
        """Reconcile a product's images with the supplier's current list.

        Matches existing rows by `url` (unique per product —
        `uq_images_product_url`) and updates only `position` in place, rather
        than deleting every row and reinserting fresh ones on every sync (the
        pre-Product-Editor behaviour, whose docstring here used to argue that
        matching by URL was "guessing which remote URL corresponds to which
        stored row" — it is not a guess: the URL *is* the identity the
        unique constraint already enforces, not a heuristic this method
        invents). An image's id now stays stable across syncs, the same
        reasoning `ProductVariantRepository.sync_for_product` documents.

        An image the supplier no longer lists is deleted, same as before.
        """
        existing = {img.url: img for img in await self.list_for_product(product_id)}
        seen: set[str] = set()
        next_position = max((img.position for img in existing.values()), default=-1) + 1

        for values in mapped:
            url = values["url"]
            seen.add(url)
            current = existing.get(url)
            if current is None:
                # Append new supplier images after the merchant's current order
                # rather than resetting every position from the supplier feed.
                await self.create(
                    product_id=product_id,
                    url=url,
                    position=next_position,
                    is_supplier=True,
                )
                next_position += 1
            # Existing rows: leave position and alt_text alone — merchant edit
            # protection for the Media tab (Stage 4).

        for url, row in existing.items():
            if url not in seen and row.is_supplier:
                await self.session.delete(row)
            # Merchant-added URLs are never removed by a supplier refresh.
        await self.session.flush()


class ProductImportRepository(TenantScopedRepository[ProductImport]):
    """Import attempts - the audit trail.

    Rows are never deleted. A failed import is the record that answers "why is
    this product missing", and that question is asked long after the failure.
    """

    sortable_fields = frozenset({"created_at", "updated_at", "status", "finished_at"})
    searchable_fields = frozenset({"external_id", "error_code"})

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, ProductImport)

    async def list_recent(self, *, limit: int = 50) -> list[ProductImport]:
        query = self._base_query().order_by(ProductImport.created_at.desc()).limit(limit)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def find_in_progress(
        self, *, source: ProductSource, external_id: str
    ) -> ProductImport | None:
        """Find an import of this product that has not finished.

        Guards against a double submission starting the same import twice - a
        double-clicked button, or a retried request whose first attempt is still
        running.
        """
        query = self._base_query().where(
            ProductImport.source == source,
            ProductImport.external_id == external_id,
            ProductImport.status.in_((ImportStatus.PENDING, ImportStatus.RUNNING)),
        )
        result = await self.session.execute(query)
        return result.scalars().first()


class ProductVersionRepository(TenantScopedRepository[ProductVersion]):
    """Versions — the optimisation history. Rows are never deleted or edited.

    Activation mirrors `PromptRepository.activate` in
    `app.repositories.ai_prompt`: two sequential flushes, deactivate then
    activate, so the database is never asked to hold two active rows for the
    same product at once — the partial unique index on `product_versions`
    would reject that.
    """

    sortable_fields = frozenset({"created_at", "version_number"})

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, ProductVersion)

    async def list_for_product(self, product_id: uuid.UUID) -> list[ProductVersion]:
        """Every version of a product, newest first — the history view."""
        query = (
            self._base_query()
            .where(ProductVersion.product_id == product_id)
            .order_by(ProductVersion.version_number.desc())
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_active(self, product_id: uuid.UUID) -> ProductVersion | None:
        query = self._base_query().where(
            ProductVersion.product_id == product_id, ProductVersion.active.is_(True)
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    async def get_by_id_for_product(
        self,
        *,
        product_id: uuid.UUID,
        version_id: uuid.UUID,
        populate_existing: bool = False,
    ) -> ProductVersion | None:
        query = self._base_query().where(
            ProductVersion.product_id == product_id, ProductVersion.id == version_id
        )
        if populate_existing:
            # Pipeline approve/publish must not trust a stale identity-map copy
            # of `active` after another transaction committed.
            query = query.execution_options(populate_existing=True)
        return (await self.session.execute(query)).scalar_one_or_none()

    async def next_version_number(self, product_id: uuid.UUID) -> int:
        # Tenant predicate stated here (review finding B-1) rather than relying
        # on the caller having already checked the product. Deliberately *not*
        # the soft-delete filter of `_base_query`: the unique constraint on
        # (product_id, version_number) counts every row, so must this.
        query = select(func.max(ProductVersion.version_number)).where(
            ProductVersion.product_id == product_id,
            ProductVersion.tenant_id == require_tenant_id(),
        )
        current = (await self.session.execute(query)).scalar_one_or_none()
        return (current or 0) + 1

    async def activate(self, *, product_id: uuid.UUID, version_id: uuid.UUID) -> ProductVersion:
        """Make `version_id` the active version of `product_id`.

        The same operation serves both "activate a newly-created version" and
        "roll back to an older one" — there is no separate rollback mechanism.
        """
        target = await self.get_by_id_for_product(product_id=product_id, version_id=version_id)
        if target is None:
            raise NotFoundError.for_resource("ProductVersion", version_id)
        if target.active:
            return target

        current = await self.get_active(product_id)
        if current is not None:
            current.active = False
            await self.session.flush()

        target.active = True
        await self.session.flush()
        return target


class ProductMarketplaceAttributesRepository(TenantScopedRepository[ProductMarketplaceAttributes]):
    """A product's category and item specifics per marketplace (EBAY-C3)."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, ProductMarketplaceAttributes)

    async def get_for(
        self, product_id: uuid.UUID, marketplace_id: str
    ) -> ProductMarketplaceAttributes | None:
        result = await self.session.execute(
            self._base_query().where(
                ProductMarketplaceAttributes.product_id == product_id,
                ProductMarketplaceAttributes.marketplace_id == marketplace_id,
            )
        )
        return result.scalar_one_or_none()


__all__ = [
    "ProductImageRepository",
    "ProductImportRepository",
    "ProductMarketplaceAttributesRepository",
    "ProductRepository",
    "ProductVariantRepository",
    "ProductVersionRepository",
]
