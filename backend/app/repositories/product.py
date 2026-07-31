"""Product catalogue data access.

Every class here extends :class:`TenantScopedRepository`, so the tenant
predicate is inherited rather than written per query. That is the whole point:
a cross-tenant leak in this layer is the worst failure mode the platform has,
and the defence is that the correct behaviour is the default one.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product import (
    ImportStatus,
    Product,
    ProductImage,
    ProductImport,
    ProductSource,
    ProductStatus,
    ProductVariant,
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

    async def delete_for_product(self, product_id: uuid.UUID) -> None:
        """Remove every variant of a product.

        A hard delete, unlike most of this codebase. A variant the supplier has
        withdrawn is not history worth keeping - it is a purchasable option that
        no longer exists, and a soft-deleted row risks being offered for sale by
        a query that forgets the filter.
        """
        for variant in await self.list_for_product(product_id):
            await self.session.delete(variant)
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

    async def delete_for_product(self, product_id: uuid.UUID) -> None:
        """Replace rather than merge on re-import.

        Supplier image sets are reordered and rotated between syncs. Diffing
        them would mean guessing which remote URL corresponds to which stored
        row; replacing is both simpler and correct.
        """
        for image in await self.list_for_product(product_id):
            await self.session.delete(image)
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


__all__ = [
    "ProductImageRepository",
    "ProductImportRepository",
    "ProductRepository",
    "ProductVariantRepository",
]
