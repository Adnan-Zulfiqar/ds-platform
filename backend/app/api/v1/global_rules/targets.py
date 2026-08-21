"""Human-readable target lookup for rule scoping (M3A-4B).

A rule scoped to a product, variant, category or store needs an identifier,
and asking a merchant to paste a UUID is not a workflow. This turns each of
those into a searchable list of labels.

**Not a second catalogue API.** It returns `{id, label, sublabel}` and nothing
else — no prices, no supplier snapshot, no variants-of-a-product payload — so
it cannot grow into one. The existing `/products` list is a full catalogue
read with its own filters and response shape, and reusing it here would mean
shipping whole product bodies to populate a combobox; more to the point,
*variants and categories have no list endpoint at all*, so two of the four
kinds could not be served by reuse under any reading. One narrow read-only
endpoint is smaller than the alternative.

Tenant scoping comes from the repositories' own base query, not from anything
in the request. Viewers may read it: choosing what a rule points at is part of
understanding the rule, and it exposes nothing a viewer cannot already list.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, or_, select

from app.api.deps import DbSession, RequireViewer
from app.core.context import require_tenant_id
from app.models.product import Product, ProductVariant
from app.models.store import Store
from app.schemas.base import CamelCaseModel
from app.schemas.common import Page, PageMeta

router = APIRouter(prefix="/global-rules", tags=["global-rules"])

#: A hard ceiling regardless of what a client asks for. A picker shows a
#: handful of options; anything larger is a catalogue dump wearing a search box.
MAX_TARGET_PAGE = 50


class TargetKind(StrEnum):
    PRODUCT = "product"
    VARIANT = "variant"
    CATEGORY = "category"
    STORE = "store"


class TargetRead(CamelCaseModel):
    """One selectable option.

    ``id`` and ``label`` are separate fields and stay that way. The client
    stores the id and shows the label; a combined string would eventually be
    parsed back apart, and a product named after a UUID would break it.
    """

    id: str
    label: str
    #: Secondary line — a SKU, a supplier, a platform. Never required to
    #: identify the row, only to tell two similar ones apart.
    sublabel: str | None = None


async def _products(
    session: DbSession, search: str | None, page: int, size: int
) -> tuple[list[TargetRead], int]:
    tenant_id = require_tenant_id()
    base = select(Product).where(Product.tenant_id == tenant_id).where(Product.deleted_at.is_(None))
    if search:
        term = f"%{search.strip()}%"
        base = base.where(or_(Product.title.ilike(term), Product.external_id.ilike(term)))

    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (
        (
            await session.execute(
                base.order_by(Product.title.asc()).offset((page - 1) * size).limit(size)
            )
        )
        .scalars()
        .all()
    )
    return (
        [
            TargetRead(
                id=str(product.id),
                label=product.title,
                sublabel=product.external_id,
            )
            for product in rows
        ],
        int(total),
    )


async def _variants(
    session: DbSession, search: str | None, page: int, size: int, product_id: uuid.UUID | None
) -> tuple[list[TargetRead], int]:
    tenant_id = require_tenant_id()
    base = (
        select(ProductVariant, Product.title)
        .join(Product, Product.id == ProductVariant.product_id)
        .where(ProductVariant.tenant_id == tenant_id)
        .where(ProductVariant.deleted_at.is_(None))
    )
    if product_id is not None:
        base = base.where(ProductVariant.product_id == product_id)
    if search:
        term = f"%{search.strip()}%"
        base = base.where(
            or_(
                ProductVariant.label.ilike(term),
                ProductVariant.merchant_sku.ilike(term),
                ProductVariant.external_variant_id.ilike(term),
                Product.title.ilike(term),
            )
        )

    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (
        await session.execute(
            base.order_by(Product.title.asc(), ProductVariant.created_at.asc())
            .offset((page - 1) * size)
            .limit(size)
        )
    ).all()
    return (
        [
            TargetRead(
                id=str(variant.id),
                # A variant with no options is still a real row; naming it
                # after its product beats showing an empty line.
                label=variant.label or title,
                sublabel=variant.merchant_sku or variant.external_variant_id or title,
            )
            for variant, title in rows
        ],
        int(total),
    )


async def _categories(
    session: DbSession, search: str | None, page: int, size: int
) -> tuple[list[TargetRead], int]:
    """Distinct supplier categories actually present in this catalogue.

    There is no category table — the identifier arrives on the product from the
    supplier — so the options are exactly the values in use. That is the honest
    set: offering a category no product carries would produce a rule that
    matches nothing, with no clue why.
    """
    tenant_id = require_tenant_id()
    base = (
        select(Product.category_id, func.count().label("uses"))
        .where(Product.tenant_id == tenant_id)
        .where(Product.deleted_at.is_(None))
        .where(Product.category_id.is_not(None))
        .group_by(Product.category_id)
    )
    if search:
        base = base.having(func.max(Product.category_id).ilike(f"%{search.strip()}%"))

    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (
        await session.execute(
            base.order_by(func.count().desc()).offset((page - 1) * size).limit(size)
        )
    ).all()
    return (
        [
            TargetRead(
                id=str(category_id),
                label=str(category_id),
                sublabel=f"{uses} product{'' if uses == 1 else 's'}",
            )
            for category_id, uses in rows
        ],
        int(total),
    )


async def _stores(
    session: DbSession, search: str | None, page: int, size: int
) -> tuple[list[TargetRead], int]:
    tenant_id = require_tenant_id()
    base = select(Store).where(Store.tenant_id == tenant_id).where(Store.deleted_at.is_(None))
    if search:
        base = base.where(Store.name.ilike(f"%{search.strip()}%"))

    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (
        (
            await session.execute(
                base.order_by(Store.name.asc()).offset((page - 1) * size).limit(size)
            )
        )
        .scalars()
        .all()
    )
    return (
        [
            TargetRead(id=str(store.id), label=store.name, sublabel=store.platform.value)
            for store in rows
        ],
        int(total),
    )


@router.get(
    "/targets/{kind}",
    response_model=Page[TargetRead],
    summary="Search selectable rule targets by name",
)
async def search_targets(
    session: DbSession,
    kind: TargetKind,
    _authorized: RequireViewer,
    search: Annotated[str | None, Query(max_length=200)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=MAX_TARGET_PAGE)] = 20,
    product_id: Annotated[uuid.UUID | None, Query(alias="productId")] = None,
) -> Page[TargetRead]:
    """Labels and ids for a scope picker, paginated and tenant-scoped.

    ``productId`` narrows a variant search to one product, which is how the
    form asks the question a merchant actually has: "which variant *of this
    product*".
    """
    if kind is TargetKind.PRODUCT:
        items, total = await _products(session, search, page, size)
    elif kind is TargetKind.VARIANT:
        items, total = await _variants(session, search, page, size, product_id)
    elif kind is TargetKind.CATEGORY:
        items, total = await _categories(session, search, page, size)
    else:
        items, total = await _stores(session, search, page, size)

    return Page(items=items, meta=PageMeta.build(page=page, size=size, total_items=total))


__all__ = ["MAX_TARGET_PAGE", "TargetKind", "TargetRead", "router"]
