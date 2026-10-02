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
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy import event

from app.api.deps import DbSession, RequireAdmin, RequireViewer, endpoint_rate_limit
from app.integrations.aliexpress.catalog import normalise_product_id
from app.integrations.shopify.schemas import (
    ShopifyPublishCheckItem,
    ShopifyPublishReadinessResponse,
    ShopifyPublishResponse,
)
from app.models.product import Product, ProductSource
from app.repositories.product import ProductImportRepository, ProductRepository
from app.schemas.common import ListQueryParams, Page, list_query_params
from app.schemas.pipeline_bulk import (
    PipelineBulkRunCreateRequest,
    PipelineBulkRunItemRead,
    PipelineBulkRunRead,
)
from app.schemas.product import (
    FeedProductRead,
    PipelineApproveRequest,
    PipelineCheckItemRead,
    PipelineImageAnalysisEvidenceRead,
    PipelineImageAnalysisItemRead,
    PipelineImageAnalysisReportRead,
    PipelineListingViewRead,
    PipelinePreviewRequest,
    PipelinePreviewResponse,
    PipelinePublishRequest,
    ProductDetailRead,
    ProductDuplicateCheckResponse,
    ProductDuplicateMatch,
    ProductImageRead,
    ProductImportRead,
    ProductImportRequest,
    ProductOptimizeRequest,
    ProductOptimizeResponse,
    ProductRead,
    ProductUpdateRequest,
    ProductVariantRead,
    ProductVersionQualityBaselineRead,
    ProductVersionQualityBreakdownRead,
    ProductVersionRead,
    ProductWorkspaceCounts,
)
from app.services.image_analysis import ImageAnalysisReport
from app.services.pipeline_bulk import PipelineBulkRunService
from app.services.product import ProductService
from app.services.product_import import ProductImportService
from app.services.product_optimization import ProductOptimizationService
from app.services.product_pipeline import (
    PipelineCheckItem,
    PipelineListingView,
    PipelinePreview,
    ProductPipelineService,
)
from app.services.publish_readiness import PublishReadinessResult
from app.tasks.ai import publish_pipeline_bulk_run

router = APIRouter(prefix="/products", tags=["products"])

_pipeline_bulk_start_limit = endpoint_rate_limit("pipeline-bulk-start", limit=10, window_seconds=60)


def _publish_pipeline_bulk_after_commit(session: DbSession, run_id: uuid.UUID) -> None:
    def on_commit(_session: object) -> None:
        publish_pipeline_bulk_run(run_id)

    event.listen(session.sync_session, "after_commit", on_commit, once=True)


def _to_bulk_run(run: object, cancel_requested_at: datetime | None = None) -> PipelineBulkRunRead:
    read = PipelineBulkRunRead.model_validate(run)
    read.cancel_requested_at = cancel_requested_at
    return read


def _to_bulk_item(item: object) -> PipelineBulkRunItemRead:
    return PipelineBulkRunItemRead.model_validate(item)


def _to_detail(product: Product) -> ProductDetailRead:
    """Project a product and its children into the detail response.

    Fields are copied through an explicit schema rather than validating the ORM
    object wholesale, so adding a column to the model can never cause it to
    appear in a response by accident. ``description``/``supplier_description``
    are exposed explicitly here because they are sanitized before storage
    (``app.core.sanitize.sanitize_html``, Product Editor stage 1) — the raw
    ``ItemBaseInfo.description_html`` never reaches this far.
    """
    base = ProductRead.model_validate(product).model_copy(
        update={"variant_count": len(product.variants)}
    )
    return ProductDetailRead(
        **base.model_dump(),
        variants=[ProductVariantRead.model_validate(v) for v in product.variants],
        images=[ProductImageRead.model_validate(i) for i in product.live_images],
        description=product.description,
        supplier_description=product.supplier_description,
        supplier_title=product.supplier_title,
        supplier_brand=product.supplier_brand,
    )


def _to_read(product: Product, variant_count: int) -> ProductRead:
    """List-row projection with the count from the same query, not a guess."""
    return ProductRead.model_validate(product).model_copy(update={"variant_count": variant_count})


def _listing_view(view: PipelineListingView) -> PipelineListingViewRead:
    return PipelineListingViewRead(
        title=view.title,
        description=view.description,
        seo_title=view.seo_title,
        seo_description=view.seo_description,
        keywords=view.keywords,
        tags=list(view.tags),
    )


def _pipeline_checks(items: tuple[PipelineCheckItem, ...]) -> list[PipelineCheckItemRead]:
    return [PipelineCheckItemRead(code=item.code, message=item.message) for item in items]


def _image_analysis_report(report: ImageAnalysisReport) -> PipelineImageAnalysisReportRead:
    """Project Stage 7 stored analysis. Empty/missing evidence is ``null``, not invented."""
    images: list[PipelineImageAnalysisItemRead] = []
    for item in report.images:
        evidence = item.analysis
        analysis = (
            None if not evidence else PipelineImageAnalysisEvidenceRead.model_validate(evidence)
        )
        images.append(
            PipelineImageAnalysisItemRead(
                image_id=item.image_id,
                position=item.position,
                status=item.status,
                error_code=item.error_code,
                analysis=analysis,
            )
        )
    return PipelineImageAnalysisReportRead(product_id=report.product_id, images=images)


def _channel_readiness(
    result: PublishReadinessResult | None,
) -> ShopifyPublishReadinessResponse | None:
    if result is None:
        return None
    return ShopifyPublishReadinessResponse(
        channel=result.channel,
        store_id=result.store_id,
        draft_id=result.draft_id,
        draft_updated_at=result.draft_updated_at,
        can_publish=result.can_publish,
        blockers=[
            ShopifyPublishCheckItem(
                code=item.code,
                message=item.message,
                field=item.field,
                section=item.section,
                action=item.action,
            )
            for item in result.blockers
        ],
        recommendations=[
            ShopifyPublishCheckItem(
                code=item.code,
                message=item.message,
                field=item.field,
                section=item.section,
                action=item.action,
            )
            for item in result.recommendations
        ],
        checked_at=result.checked_at,
    )


def _pipeline_preview_to_response(preview: PipelinePreview) -> PipelinePreviewResponse:
    """DTO → wire. No Stage 7 decisions live here."""
    quality_baseline = None
    if preview.quality_baseline is not None:
        quality_baseline = ProductVersionQualityBaselineRead(
            version_number=preview.quality_baseline.version_number,
            score=preview.quality_baseline.score,
        )
    quality_breakdown = None
    if preview.quality_breakdown is not None:
        quality_breakdown = ProductVersionQualityBreakdownRead.model_validate(
            preview.quality_breakdown
        )
    return PipelinePreviewResponse(
        product_id=preview.product_id,
        candidate_version_id=preview.candidate_version_id,
        candidate_version_number=preview.candidate_version_number,
        candidate_active=preview.candidate_active,
        source_updated_at=preview.source_updated_at,
        approval_expected_updated_at=preview.approval_expected_updated_at,
        original=_listing_view(preview.original),
        proposal=_listing_view(preview.proposal),
        quality_score=preview.quality_score,
        quality_baseline=quality_baseline,
        quality_delta=preview.quality_delta,
        quality_score_version=preview.quality_score_version,
        quality_breakdown=quality_breakdown,
        image_analysis=_image_analysis_report(preview.image_analysis),
        is_synthetic=preview.is_synthetic,
        provider=preview.provider,
        channel_readiness=_channel_readiness(preview.channel_readiness),
        pipeline_blockers=_pipeline_checks(preview.pipeline_blockers),
        pipeline_warnings=_pipeline_checks(preview.pipeline_warnings),
        publishable=preview.publishable,
    )


def _shopify_publish_response(result: dict[str, Any]) -> ShopifyPublishResponse:
    """Same mapping as ``POST /integrations/shopify/publish`` — not a second contract."""
    return ShopifyPublishResponse.from_result(result)


@router.get(
    "",
    response_model=Page[ProductRead],
    summary="List published products in the current tenant",
)
async def list_products(
    session: DbSession,
    params: Annotated[ListQueryParams, Depends(list_query_params)],
    _authorized: RequireViewer,
) -> Page[ProductRead]:
    """Return products successfully published to at least one channel.

    Imported drafts without a synced ``StoreListing`` live under
    ``GET /drafts`` (Product Workspace V2). Filtering here — not only in the
    UI — keeps pagination and badge counts honest for every client.
    """
    rows, total = await ProductRepository(session).list_published(params)
    return Page[ProductRead].build(
        items=[_to_read(product, count) for product, count in rows],
        page=params.page,
        size=params.size,
        total_items=total,
    )


@router.get(
    "/workspace-counts",
    response_model=ProductWorkspaceCounts,
    summary="Draft and published product counts for workspace navigation",
)
async def workspace_counts(
    session: DbSession,
    _authorized: RequireViewer,
) -> ProductWorkspaceCounts:
    """Sidebar badge totals. Declared before ``/{product_id}`` so the path
    cannot be parsed as a product UUID.
    """
    counts = await ProductRepository(session).count_workspace()
    return ProductWorkspaceCounts.model_validate(counts)


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
    "/import/check",
    response_model=ProductDuplicateCheckResponse,
    summary="Check whether a supplier product is already imported",
)
async def check_duplicate_import(
    session: DbSession,
    _authorized: RequireViewer,
    external_id: Annotated[str, Query(min_length=1, max_length=2048)],
) -> ProductDuplicateCheckResponse:
    """Authoritative, tenant-scoped answer to "have we already got this one".

    Accepts a bare id or a full listing URL — the same
    ``normalise_product_id`` the import endpoint itself uses as a validator
    (`ProductImportRequest`), so a URL and its bare id always resolve to the
    same answer here and at import time; the two paths cannot silently
    disagree.

    Declared before ``/{product_id}`` was already unnecessary here — a
    two-segment path never matches that one-segment route — but it is kept in
    this file's early, specific-routes-first order for readability, matching
    every route around it.

    Read-only, viewer-level: this only tells the caller whether a row exists
    and, if so, its id/title/publication state — nothing about another
    tenant, and nothing that isn't already visible on the drafts/products
    list this same principal can already read.

    An identifier that fails to normalise (still-being-typed, decoration
    that cannot be resolved) answers ``exists: false`` rather than a
    validation error — this endpoint is called on every keystroke as the
    merchant types or pastes, and "nothing to compare yet" is not a client
    mistake worth surfacing as one.
    """
    identifier = normalise_product_id(external_id)
    if identifier is None:
        return ProductDuplicateCheckResponse(exists=False)

    match = await ProductRepository(session).find_duplicate(
        source=ProductSource.ALIEXPRESS, external_id=identifier
    )
    if match is None:
        return ProductDuplicateCheckResponse(exists=False)

    product, is_published = match
    return ProductDuplicateCheckResponse(
        exists=True,
        product=ProductDuplicateMatch(
            id=product.id,
            title=product.title,
            status=product.status,
            is_published=is_published,
        ),
    )


@router.post(
    "/imports/{import_id}/retry",
    response_model=ProductDetailRead,
    summary="Retry a failed import",
)
async def retry_import(
    session: DbSession,
    principal: RequireAdmin,
    import_id: Annotated[uuid.UUID, Path()],
) -> ProductDetailRead:
    """Resubmit a specific failed import attempt without re-entering it.

    DSers-parity M1 (`docs/dsers-parity/M1_IMPORT_TO_DRAFTS.md`). Declared
    before ``/{product_id}`` for the same reason ``/imports`` is: FastAPI
    matches routes in declaration order, and ``/{product_id}`` would
    otherwise swallow ``/imports/<uuid>/retry`` as an attempt to parse
    ``imports`` itself as a product id.
    """
    product = await ProductImportService(session).retry_import(
        import_id, requested_by_user_id=principal.user_id
    )
    return _to_detail(product)


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


@router.post(
    "/pipeline/runs",
    response_model=PipelineBulkRunRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start a bulk pipeline preview run",
)
async def start_pipeline_bulk_run(
    session: DbSession,
    payload: PipelineBulkRunCreateRequest,
    principal: RequireAdmin,
    _throttle: None = Depends(_pipeline_bulk_start_limit),
) -> PipelineBulkRunRead:
    """Accept work; generate inactive preview candidates on Celery.

    Declared with the other static paths so ``pipeline`` cannot be parsed as a
    product id. Publish happens on ``after_commit``; progress is PostgreSQL.
    """
    run = await PipelineBulkRunService(session).create(
        product_ids=payload.product_ids,
        idempotency_key=payload.idempotency_key,
        tone=payload.tone,
        store_id=payload.store_id,
        actor_id=principal.user_id,
    )
    _publish_pipeline_bulk_after_commit(session, run.id)
    return _to_bulk_run(run)


@router.get(
    "/pipeline/runs/{run_id}",
    response_model=PipelineBulkRunRead,
    summary="Read a bulk pipeline run",
)
async def get_pipeline_bulk_run(
    session: DbSession,
    _authorized: RequireAdmin,
    run_id: Annotated[uuid.UUID, Path()],
) -> PipelineBulkRunRead:
    service = PipelineBulkRunService(session)
    run = await service.get(run_id)
    return _to_bulk_run(run, await service.cancel_requested_at(run_id))


@router.get(
    "/pipeline/runs/{run_id}/items",
    response_model=Page[PipelineBulkRunItemRead],
    summary="List items of a bulk pipeline run",
)
async def list_pipeline_bulk_run_items(
    session: DbSession,
    _authorized: RequireAdmin,
    run_id: Annotated[uuid.UUID, Path()],
    params: Annotated[ListQueryParams, Depends(list_query_params)],
) -> Page[PipelineBulkRunItemRead]:
    rows, total = await PipelineBulkRunService(session).list_items(run_id, params)
    return Page[PipelineBulkRunItemRead].build(
        items=[_to_bulk_item(item) for item in rows],
        page=params.page,
        size=params.size,
        total_items=total,
    )


@router.post(
    "/pipeline/runs/{run_id}/cancel",
    response_model=PipelineBulkRunRead,
    summary="Cancel a bulk pipeline run",
)
async def cancel_pipeline_bulk_run(
    session: DbSession,
    principal: RequireAdmin,
    run_id: Annotated[uuid.UUID, Path()],
) -> PipelineBulkRunRead:
    """Never waits on the worker. Mid-item, the response is still ``running``
    with ``cancelRequestedAt`` set; poll until ``cancelled``."""
    outcome = await PipelineBulkRunService(session).cancel(
        run_id, requested_by_user_id=principal.user_id
    )
    return _to_bulk_run(outcome.run, outcome.cancel_requested_at)


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


@router.patch(
    "/{product_id}",
    response_model=ProductDetailRead,
    summary="Update a product's editable fields",
)
async def update_product(
    session: DbSession,
    _authorized: RequireAdmin,
    product_id: Annotated[uuid.UUID, Path()],
    payload: ProductUpdateRequest,
) -> ProductDetailRead:
    """Apply merchant edits to a product.

    Admin or owner: editing catalogue content that customers see is the same
    weight of decision as importing or optimising it.

    PATCH semantics: only fields present in the request body change. A
    field the merchant never mentions is left exactly as it was — sending
    `{"tags": [...]}` does not touch `title`, and vice versa.

    Optimistic concurrency (M2A) applies here too when the caller sends
    `expectedUpdatedAt` — see `ProductService.update_product` — but is
    opt-in: existing callers that omit it keep the pre-M2A last-write-wins
    behaviour.
    """
    changes = payload.model_dump(exclude_unset=True)
    expected_updated_at = changes.pop("expected_updated_at", None)
    product = await ProductService(session).update_product(
        product_id, changes, expected_updated_at=expected_updated_at
    )
    return _to_detail(product)


@router.get(
    "/{product_id}/versions",
    response_model=Page[ProductVersionRead],
    summary="List a product's optimisation version history",
)
async def list_product_versions(
    session: DbSession,
    _authorized: RequireViewer,
    product_id: Annotated[uuid.UUID, Path()],
    params: Annotated[ListQueryParams, Depends(list_query_params)],
) -> Page[ProductVersionRead]:
    """Return every version of a product, newest first.

    Empty for a product that has never been optimised — version 1 (the
    original snapshot) is created lazily on first optimisation, not at
    import time, so "no versions yet" is the true state rather than a gap.

    Paginated in Python rather than SQL, the same simplification Phase 9
    stage 2's prompt-history endpoint makes: per-product version counts are
    small, and real OFFSET/LIMIT for a list that never reaches page 2 in
    practice would be complexity without a caller who needs it.
    """
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
        store_id=payload.store_id,
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

    # Refresh for the destination last used successfully — never force US.
    product = await ProductImportService(session).import_product(
        external_id=existing.external_id,
        requested_by_user_id=principal.user_id,
        ship_to_country=existing.import_ship_to_country or existing.ship_to_country,
    )
    return _to_detail(product)


@router.post(
    "/{product_id}/optimize",
    response_model=ProductOptimizeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generate an AI-optimised title and description",
)
async def optimize_product(
    session: DbSession,
    principal: RequireAdmin,
    product_id: Annotated[uuid.UUID, Path()],
    payload: ProductOptimizeRequest | None = None,
) -> ProductOptimizeResponse:
    """Generate a new title and description and make them active.

    Admin or owner: this changes what the whole workspace's listing says,
    the same reasoning `import`/`sync` already apply.

    **Uses `StubProvider`, not a real model.** No `AI_PROVIDER` is configured
    in this deployment (Phase 9 stages 1 and 2), so every generated result is
    deterministic, obviously-synthetic text — see `app.ai.stub_provider`. The
    response's `version.aiProvider` will read `"stub"` until a real provider
    is wired in a later stage.

    Never overwrites `title`/`description` — those stay exactly as the
    supplier described the product. Only the dedicated `optimizedTitle`/
    `optimizedDescription` fields change.
    """
    tone = payload.tone if payload is not None else ProductOptimizeRequest().tone
    product, version = await ProductOptimizationService(session).optimize_product(
        product_id,
        tone=tone,
        requested_by_user_id=principal.user_id,
    )
    return ProductOptimizeResponse(
        product=_to_detail(product), version=ProductVersionRead.from_model(version)
    )


@router.post(
    "/{product_id}/versions/{version_id}/activate",
    response_model=ProductDetailRead,
    summary="Activate a version — also how rollback works",
)
async def activate_product_version(
    session: DbSession,
    _authorized: RequireAdmin,
    product_id: Annotated[uuid.UUID, Path()],
    version_id: Annotated[uuid.UUID, Path()],
) -> ProductDetailRead:
    """Make `version_id` the active version of a product.

    The same operation serves both "activate a newly-created optimisation"
    and "roll back to an older one, including the original supplier
    content" — there is no separate rollback endpoint because there is no
    separate mechanism.
    """
    product = await ProductOptimizationService(session).activate_version(product_id, version_id)
    return _to_detail(product)


@router.post(
    "/{product_id}/pipeline/preview",
    response_model=PipelinePreviewResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generate an inactive pipeline preview candidate",
)
async def generate_pipeline_preview(
    session: DbSession,
    principal: RequireAdmin,
    product_id: Annotated[uuid.UUID, Path()],
    payload: PipelinePreviewRequest | None = None,
) -> PipelinePreviewResponse:
    """Analyse and generate one inactive pipeline candidate. Does not activate or publish."""
    request = payload if payload is not None else PipelinePreviewRequest()
    preview = await ProductPipelineService(session).preview(
        product_id,
        store_id=request.store_id,
        tone=request.tone,
        requested_by_user_id=principal.user_id,
    )
    return _pipeline_preview_to_response(preview)


@router.get(
    "/{product_id}/pipeline/versions/{version_id}/preview",
    response_model=PipelinePreviewResponse,
    summary="Read an existing pipeline candidate preview",
)
async def get_pipeline_preview(
    session: DbSession,
    _authorized: RequireAdmin,
    product_id: Annotated[uuid.UUID, Path()],
    version_id: Annotated[uuid.UUID, Path()],
    store_id: Annotated[uuid.UUID | None, Query(alias="storeId")] = None,
) -> PipelinePreviewResponse:
    """Compose a preview from a stored candidate. Does not analyse or generate."""
    preview = await ProductPipelineService(session).get_preview(
        product_id,
        version_id=version_id,
        store_id=store_id,
    )
    return _pipeline_preview_to_response(preview)


@router.post(
    "/{product_id}/pipeline/versions/{version_id}/approve",
    response_model=ProductDetailRead,
    summary="Approve an exact pipeline candidate",
)
async def approve_pipeline_candidate(
    session: DbSession,
    _authorized: RequireAdmin,
    product_id: Annotated[uuid.UUID, Path()],
    version_id: Annotated[uuid.UUID, Path()],
    payload: PipelineApproveRequest,
) -> ProductDetailRead:
    """Activate the exact candidate. Token required. Does not publish."""
    product = await ProductPipelineService(session).approve(
        product_id,
        version_id=version_id,
        expected_updated_at=payload.expected_updated_at,
    )
    return _to_detail(product)


@router.post(
    "/{product_id}/pipeline/versions/{version_id}/publish",
    response_model=ShopifyPublishResponse,
    summary="Publish an already-approved pipeline candidate to Shopify",
)
async def publish_pipeline_candidate(
    session: DbSession,
    _authorized: RequireAdmin,
    product_id: Annotated[uuid.UUID, Path()],
    version_id: Annotated[uuid.UUID, Path()],
    payload: PipelinePublishRequest,
) -> ShopifyPublishResponse:
    """Overlay title/body through the existing Shopify publisher. Token required."""
    result = await ProductPipelineService(session).publish(
        product_id,
        store_id=payload.store_id,
        version_id=version_id,
        expected_updated_at=payload.expected_updated_at,
    )
    return _shopify_publish_response(result)
