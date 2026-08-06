"""Product catalogue data access.

Every class here extends :class:`TenantScopedRepository`, so the tenant
predicate is inherited rather than written per query. That is the whole point:
a cross-tenant leak in this layer is the worst failure mode the platform has,
and the defence is that the correct behaviour is the default one.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.product import (
    ImportStatus,
    Product,
    ProductImage,
    ProductImport,
    ProductSource,
    ProductStatus,
    ProductVariant,
    ProductVersion,
)
from app.repositories.base import TenantScopedRepository


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

    async def get_by_slug(self, slug: str) -> Product | None:
        """Find a product by its merchant-set URL slug, within this tenant.

        Used only to pre-check uniqueness before a `PATCH` writes one —
        `uq_products_tenant_slug` is the real backstop, this just turns a raw
        constraint violation into a clean, specific error before the insert.
        """
        query = self._base_query().where(Product.slug == slug)
        result = await self.session.execute(query)
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

        for values in mapped:
            external_variant_id = values["external_variant_id"]
            seen.add(external_variant_id)
            current = existing.get(external_variant_id)
            if current is None:
                await self.create(product_id=product_id, **values)
            else:
                await self.update(current, **values)

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

        for values in mapped:
            url = values["url"]
            seen.add(url)
            current = existing.get(url)
            if current is None:
                await self.create(product_id=product_id, **values)
            else:
                await self.update(current, position=values["position"])

        for url, row in existing.items():
            if url not in seen:
                await self.session.delete(row)
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
        self, *, product_id: uuid.UUID, version_id: uuid.UUID
    ) -> ProductVersion | None:
        query = self._base_query().where(
            ProductVersion.product_id == product_id, ProductVersion.id == version_id
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    async def next_version_number(self, product_id: uuid.UUID) -> int:
        query = select(func.max(ProductVersion.version_number)).where(
            ProductVersion.product_id == product_id
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


__all__ = [
    "ProductImageRepository",
    "ProductImportRepository",
    "ProductRepository",
    "ProductVariantRepository",
    "ProductVersionRepository",
]
