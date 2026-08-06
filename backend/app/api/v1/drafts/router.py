"""Draft product endpoints.

Drafts are not a second catalogue table. They are the same ``Product``
aggregate filtered to rows with no synced ``StoreListing`` — see
``docs/PRODUCT_WORKSPACE_V2_PLAN.md``. Handlers stay thin: validate, delegate,
return.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path

from app.api.deps import DbSession, RequireViewer
from app.models.product import Product
from app.repositories.product import ProductRepository
from app.schemas.common import ListQueryParams, Page, list_query_params
from app.schemas.product import (
    ProductDetailRead,
    ProductImageRead,
    ProductRead,
    ProductVariantRead,
)

router = APIRouter(prefix="/drafts", tags=["drafts"])


def _to_detail(product: Product) -> ProductDetailRead:
    return ProductDetailRead(
        **ProductRead.model_validate(product).model_dump(),
        variants=[ProductVariantRead.model_validate(v) for v in product.variants],
        images=[ProductImageRead.model_validate(i) for i in product.images],
        description=product.description,
        supplier_description=product.supplier_description,
    )


@router.get(
    "",
    response_model=Page[ProductRead],
    summary="List draft products in the current tenant",
)
async def list_drafts(
    session: DbSession,
    params: Annotated[ListQueryParams, Depends(list_query_params)],
    _authorized: RequireViewer,
) -> Page[ProductRead]:
    """Return imported products that are not yet published to any channel."""
    products, total = await ProductRepository(session).list_drafts(params)
    return Page[ProductRead].build(
        items=[ProductRead.model_validate(p) for p in products],
        page=params.page,
        size=params.size,
        total_items=total,
    )


@router.get(
    "/{product_id}",
    response_model=ProductDetailRead,
    summary="Get a draft product by id",
)
async def get_draft(
    session: DbSession,
    _authorized: RequireViewer,
    product_id: Annotated[uuid.UUID, Path()],
) -> ProductDetailRead:
    """Detail for a draft. Cross-tenant ids return 404 via the repository.

    A product that already has a synced listing is still readable here by id —
    the list projection is exclusive; deep-links must not 404 after publish
    while the editor routes converge in later stages.
    """
    product = await ProductRepository(session).get_by_id_or_raise(product_id)
    return _to_detail(product)
