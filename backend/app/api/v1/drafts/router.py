"""Draft product endpoints.

Drafts are not a second catalogue table. They are the same ``Product``
aggregate filtered to rows with no synced ``StoreListing`` — see
``docs/PRODUCT_WORKSPACE_V2_PLAN.md`` and ``docs/DRAFT_PRODUCT_EDITOR_PLAN.md``.

Write paths reuse :class:`ProductService` and import/sync services — no
duplicate edit logic.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path

from app.api.deps import DbSession, RequireAdmin, RequireViewer
from app.integrations.shopify.schemas import StoreListingRead
from app.models.product import Product
from app.repositories.product import ProductRepository
from app.repositories.shopify import StoreListingRepository
from app.schemas.common import ListQueryParams, Page, list_query_params
from app.schemas.draft_pricing import DraftPricingApplyRequest, DraftPricingWorkspaceRead
from app.schemas.product import (
    ProductDetailRead,
    ProductImageCreateRequest,
    ProductImageRead,
    ProductImageReorderRequest,
    ProductImageUpdateRequest,
    ProductRead,
    ProductUpdateRequest,
    ProductVariantRead,
    ProductVariantUpdateRequest,
    ProductVersionRead,
)
from app.schemas.seo import SeoScoreRead
from app.services.pricing_engine import PricingEngine
from app.services.product import ProductService
from app.services.product_import import ProductImportService
from app.services.product_optimization import ProductOptimizationService
from app.services.seo_score import seo_score_dict

router = APIRouter(prefix="/drafts", tags=["drafts"])


def _to_detail(product: Product) -> ProductDetailRead:
    return ProductDetailRead(
        **ProductRead.model_validate(product).model_dump(),
        variants=[ProductVariantRead.model_validate(v) for v in product.variants],
        images=[ProductImageRead.model_validate(i) for i in product.images],
        description=product.description,
        supplier_description=product.supplier_description,
        supplier_title=product.supplier_title,
        supplier_brand=product.supplier_brand,
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
    """Detail for the draft editor. Cross-tenant ids return 404."""
    product = await ProductRepository(session).get_by_id_or_raise(product_id)
    return _to_detail(product)


@router.patch(
    "/{product_id}",
    response_model=ProductDetailRead,
    summary="Save merchant edits on a draft",
)
async def update_draft(
    session: DbSession,
    _authorized: RequireAdmin,
    product_id: Annotated[uuid.UUID, Path()],
    payload: ProductUpdateRequest,
) -> ProductDetailRead:
    """Apply PATCH semantics via :class:`ProductService` — same as products."""
    changes = payload.model_dump(exclude_unset=True)
    product = await ProductService(session).update_product(product_id, changes)
    return _to_detail(product)


@router.post(
    "/{product_id}/refresh",
    response_model=ProductDetailRead,
    summary="Refresh supplier data for a draft",
)
async def refresh_draft(
    session: DbSession,
    principal: RequireAdmin,
    product_id: Annotated[uuid.UUID, Path()],
) -> ProductDetailRead:
    """Re-fetch supplier cost/stock/variants without overwriting merchant edits.

    Same path as ``POST /products/{id}/sync``: refresh *is* re-import by
    external id, so supplier twin protection stays in one place.
    """
    existing = await ProductRepository(session).get_by_id_or_raise(product_id)
    product = await ProductImportService(session).import_product(
        external_id=existing.external_id,
        requested_by_user_id=principal.user_id,
    )
    return _to_detail(product)


@router.get(
    "/{product_id}/versions",
    response_model=Page[ProductVersionRead],
    summary="List version history for a draft",
)
async def list_draft_versions(
    session: DbSession,
    _authorized: RequireViewer,
    product_id: Annotated[uuid.UUID, Path()],
    params: Annotated[ListQueryParams, Depends(list_query_params)],
) -> Page[ProductVersionRead]:
    versions = await ProductOptimizationService(session).list_versions(product_id)
    total = len(versions)
    page_items = versions[params.offset : params.offset + params.limit]
    return Page[ProductVersionRead].build(
        items=[ProductVersionRead.from_model(v) for v in page_items],
        page=params.page,
        size=params.size,
        total_items=total,
    )


@router.post(
    "/{product_id}/images",
    response_model=ProductDetailRead,
    summary="Add a merchant image URL to a draft",
)
async def add_draft_image(
    session: DbSession,
    _authorized: RequireAdmin,
    product_id: Annotated[uuid.UUID, Path()],
    payload: ProductImageCreateRequest,
) -> ProductDetailRead:
    product = await ProductService(session).add_image(
        product_id, url=payload.url, alt_text=payload.alt_text
    )
    return _to_detail(product)


@router.patch(
    "/{product_id}/images/reorder",
    response_model=ProductDetailRead,
    summary="Reorder draft images (index 0 is featured)",
)
async def reorder_draft_images(
    session: DbSession,
    _authorized: RequireAdmin,
    product_id: Annotated[uuid.UUID, Path()],
    payload: ProductImageReorderRequest,
) -> ProductDetailRead:
    product = await ProductService(session).reorder_images(product_id, payload.image_ids)
    return _to_detail(product)


@router.patch(
    "/{product_id}/images/{image_id}",
    response_model=ProductDetailRead,
    summary="Update draft image metadata",
)
async def update_draft_image(
    session: DbSession,
    _authorized: RequireAdmin,
    product_id: Annotated[uuid.UUID, Path()],
    image_id: Annotated[uuid.UUID, Path()],
    payload: ProductImageUpdateRequest,
) -> ProductDetailRead:
    product = await ProductService(session).update_image(
        product_id, image_id, payload.model_dump(exclude_unset=True)
    )
    return _to_detail(product)


@router.delete(
    "/{product_id}/images/{image_id}",
    response_model=ProductDetailRead,
    summary="Remove an image from the draft listing",
)
async def remove_draft_image(
    session: DbSession,
    _authorized: RequireAdmin,
    product_id: Annotated[uuid.UUID, Path()],
    image_id: Annotated[uuid.UUID, Path()],
) -> ProductDetailRead:
    product = await ProductService(session).remove_image(product_id, image_id)
    return _to_detail(product)


@router.post(
    "/{product_id}/images/{image_id}/restore",
    response_model=ProductDetailRead,
    summary="Restore a soft-deleted draft image",
)
async def restore_draft_image(
    session: DbSession,
    _authorized: RequireAdmin,
    product_id: Annotated[uuid.UUID, Path()],
    image_id: Annotated[uuid.UUID, Path()],
) -> ProductDetailRead:
    product = await ProductService(session).restore_image(product_id, image_id)
    return _to_detail(product)


@router.patch(
    "/{product_id}/variants/{variant_id}",
    response_model=ProductDetailRead,
    summary="Update a draft variant's merchant fields",
)
async def update_draft_variant(
    session: DbSession,
    _authorized: RequireAdmin,
    product_id: Annotated[uuid.UUID, Path()],
    variant_id: Annotated[uuid.UUID, Path()],
    payload: ProductVariantUpdateRequest,
) -> ProductDetailRead:
    product = await ProductService(session).update_variant(
        product_id, variant_id, payload.model_dump(exclude_unset=True)
    )
    return _to_detail(product)


@router.get(
    "/{product_id}/listings",
    response_model=list[StoreListingRead],
    summary="Channel listings for a draft/product",
)
async def list_draft_listings(
    session: DbSession,
    _authorized: RequireViewer,
    product_id: Annotated[uuid.UUID, Path()],
) -> list[StoreListingRead]:
    await ProductRepository(session).get_by_id_or_raise(product_id)
    rows = await StoreListingRepository(session).list_for_product(product_id)
    return [StoreListingRead.model_validate(row) for row in rows]


@router.get(
    "/{product_id}/seo-score",
    response_model=SeoScoreRead,
    summary="Transparent advisory SEO quality score",
)
async def get_draft_seo_score(
    session: DbSession,
    _authorized: RequireViewer,
    product_id: Annotated[uuid.UUID, Path()],
) -> SeoScoreRead:
    product = await ProductRepository(session).get_by_id_or_raise(product_id)
    data = seo_score_dict(product)
    return SeoScoreRead(**data)


@router.get(
    "/{product_id}/pricing",
    response_model=DraftPricingWorkspaceRead,
    summary="Decimal-safe draft pricing workspace",
)
async def get_draft_pricing(
    session: DbSession,
    _authorized: RequireViewer,
    product_id: Annotated[uuid.UUID, Path()],
) -> DraftPricingWorkspaceRead:
    """Profit/margin rows — authoritative math is server-side Decimal."""
    return await PricingEngine(session).draft_workspace(product_id)


@router.post(
    "/{product_id}/pricing/preview",
    response_model=DraftPricingWorkspaceRead,
    summary="Preview draft variant pricing changes without writing",
)
async def preview_draft_pricing(
    session: DbSession,
    _authorized: RequireViewer,
    product_id: Annotated[uuid.UUID, Path()],
    payload: DraftPricingApplyRequest,
) -> DraftPricingWorkspaceRead:
    return await PricingEngine(session).draft_workspace(product_id, propose=payload)


@router.post(
    "/{product_id}/pricing/apply",
    response_model=DraftPricingWorkspaceRead,
    summary="Apply bulk pricing to draft variants",
)
async def apply_draft_pricing(
    session: DbSession,
    _authorized: RequireAdmin,
    product_id: Annotated[uuid.UUID, Path()],
    payload: DraftPricingApplyRequest,
) -> DraftPricingWorkspaceRead:
    return await PricingEngine(session).apply_draft_variant_pricing(product_id, payload)
