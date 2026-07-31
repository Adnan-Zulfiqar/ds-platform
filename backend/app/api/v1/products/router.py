"""Product endpoints.

Thin, like every router here: validate, delegate, return. No SQL, no tenant
filtering, no error translation — tenant isolation is applied inside
``TenantScopedRepository`` and authorization runs as a dependency, before the
handler body, so neither can be forgotten by editing a function carelessly.

**Ordering matters in this module.** ``/products/imports`` is declared before
``/products/{product_id}`` because FastAPI matches in declaration order, and the
reverse would make "imports" parse as a product id — a 422 on a route that looks
correct.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status

from app.api.deps import DbSession, RequireAdmin, RequireViewer
from app.models.product import Product
from app.repositories.product import ProductImportRepository, ProductRepository
from app.schemas.common import ListQueryParams, Page, list_query_params
from app.schemas.product import (
    FeedProductRead,
    ProductDetailRead,
    ProductImageRead,
    ProductImportRead,
    ProductImportRequest,
    ProductRead,
    ProductVariantRead,
)
from app.services.product_import import ProductImportService

router = APIRouter(prefix="/products", tags=["products"])


def _to_detail(product: Product) -> ProductDetailRead:
    """Project a product and its children into the detail response.

    Fields are copied through an explicit schema rather than validating the ORM
    object wholesale, so adding a column to the model can never cause it to
    appear in a response by accident — which is how ``description``, the
    unsanitised supplier HTML, stays out of the API.
    """
    return ProductDetailRead(
        **ProductRead.model_validate(product).model_dump(),
        variants=[ProductVariantRead.model_validate(v) for v in product.variants],
        images=[ProductImageRead.model_validate(i) for i in product.images],
    )


@router.get(
    "",
    response_model=Page[ProductRead],
    summary="List products in the current tenant",
)
async def list_products(
    session: DbSession,
    params: Annotated[ListQueryParams, Depends(list_query_params)],
    _authorized: RequireViewer,
) -> Page[ProductRead]:
    """Return a page of the tenant's catalogue.

    Readable by every real role: knowing what is in the catalogue is operational
    information the whole team needs, even those who cannot change it.
    """
    products, total = await ProductRepository(session).list(params)
    return Page[ProductRead].build(
        items=[ProductRead.model_validate(p) for p in products],
        page=params.page,
        size=params.size,
        total_items=total,
    )


@router.get(
    "/imports",
    response_model=Page[ProductImportRead],
    summary="List recent import attempts",
)
async def list_imports(
    session: DbSession,
    params: Annotated[ListQueryParams, Depends(list_query_params)],
    _authorized: RequireViewer,
) -> Page[ProductImportRead]:
    """Return import attempts, successful and failed.

    The failures are the point. "Why is this product missing" is asked long
    after the import ran, and a view showing only successes could not answer it.
    """
    records, total = await ProductImportRepository(session).list(params)
    return Page[ProductImportRead].build(
        items=[ProductImportRead.model_validate(r) for r in records],
        page=params.page,
        size=params.size,
        total_items=total,
    )


@router.get(
    "/feeds/{feed_name}",
    response_model=list[FeedProductRead],
    summary="Browse a supplier feed",
)
async def browse_feed(
    session: DbSession,
    _authorized: RequireViewer,
    feed_name: Annotated[str, Path(min_length=1, max_length=128)],
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=50)] = 20,
    country: Annotated[str, Query(min_length=2, max_length=2)] = "US",
    currency: Annotated[str, Query(min_length=3, max_length=3)] = "USD",
) -> list[FeedProductRead]:
    """List products in a supplier feed without importing them.

    Feeds rather than keyword search: search returns ``NGSELECTION_SEARCH_ERROR``
    on this account, verified against the live gateway. Offering a search box
    that cannot work would be worse than not offering one.

    **An unknown feed name returns an empty list, not an error.** That is the
    supplier's behaviour, surfaced honestly rather than translated into a 404
    that would claim more than we know.
    """
    items = await ProductImportService(session).browse_feed(
        feed_name=feed_name,
        page=page,
        page_size=size,
        country=country,
        currency=currency,
    )
    return [FeedProductRead.model_validate(item) for item in items]


@router.get(
    "/{product_id}",
    response_model=ProductDetailRead,
    summary="Get a product with its variants and images",
)
async def get_product(
    session: DbSession,
    _authorized: RequireViewer,
    product_id: Annotated[uuid.UUID, Path()],
) -> ProductDetailRead:
    """Return one product.

    A product belonging to another tenant raises 404, not 403. A 403 would
    confirm the row exists and let an attacker enumerate other tenants'
    identifiers; the repository simply finds nothing, and "not found" is both
    safer and true from this tenant's perspective.
    """
    product = await ProductRepository(session).get_by_id_or_raise(product_id)
    return _to_detail(product)


@router.post(
    "/import",
    response_model=ProductDetailRead,
    status_code=status.HTTP_201_CREATED,
    summary="Import a product from AliExpress",
)
async def import_product(
    session: DbSession,
    payload: ProductImportRequest,
    principal: RequireAdmin,
) -> ProductDetailRead:
    """Import or refresh a supplier product into the catalogue.

    Admin or owner: importing spends the tenant's supplier rate limit and
    changes what the whole workspace sells, which is not a viewer's decision.

    **Idempotent.** Calling it twice with the same identifier refreshes the
    existing product rather than creating a second one, guaranteed by a unique
    constraint rather than a check a concurrent caller could race past. An
    import already in flight for the same product is rejected instead of
    started again.

    Runs inline rather than through a task queue. One product is one supplier
    call and finishes inside a request; deferring it would mean the caller could
    not be told whether it worked. Bulk import, where that reasoning reverses,
    is a later phase.
    """
    product = await ProductImportService(session).import_product(
        external_id=payload.external_id,
        requested_by_user_id=principal.user_id,
        ship_to_country=payload.ship_to_country,
        currency=payload.currency,
    )
    return _to_detail(product)


@router.post(
    "/{product_id}/sync",
    response_model=ProductDetailRead,
    summary="Refresh a product from its supplier",
)
async def sync_product(
    session: DbSession,
    principal: RequireAdmin,
    product_id: Annotated[uuid.UUID, Path()],
) -> ProductDetailRead:
    """Re-fetch price, stock and variants for a product already imported.

    The same code path as import — refreshing *is* importing again — so the two
    can never drift apart.

    ``status`` is preserved: a refresh must not revert something the tenant has
    activated back to draft.
    """
    existing = await ProductRepository(session).get_by_id_or_raise(product_id)

    product = await ProductImportService(session).import_product(
        external_id=existing.external_id,
        requested_by_user_id=principal.user_id,
    )
    return _to_detail(product)
