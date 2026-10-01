"""Stage 7 product pipeline: preview, approve, and overlay publish.

Depends on `product_optimization` and the existing Shopify publisher.
Never imported by `product_optimization` — that direction would cycle.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.sanitize import sanitize_html
from app.integrations.shopify import sync as shopify_sync
from app.integrations.shopify.sync import ShopifyListingOverlay, ShopifySyncService
from app.models.product import Product, ProductVersion, ProductVersionSource
from app.repositories.product import (
    ProductImageRepository,
    ProductRepository,
    ProductVersionRepository,
)
from app.schemas.product import DESCRIPTION_MAX_LENGTH
from app.services.base import BaseService
from app.services.image_analysis import ImageAnalysisItem, ImageAnalysisReport, ImageAnalysisService
from app.services.product_optimization import (
    PipelineCandidateMetadata,
    ProductOptimizationService,
    parse_pipeline_candidate_metadata,
)
from app.services.publish_readiness import (
    CHANNEL_SHOPIFY,
    PublishReadinessResult,
    PublishReadinessService,
)

#: Matches Product.optimized_title and ProductUpdateRequest.title.
PIPELINE_APPROVAL_TITLE_MAX = 512
#: Shopify product-title limit documented by Stage 5 D1.
PIPELINE_PUBLISH_TITLE_MAX = 255


@dataclass(frozen=True, slots=True, kw_only=True)
class PipelineListingView:
    title: str | None
    description: str | None
    seo_title: str | None
    seo_description: str | None
    keywords: str | None
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class PipelineCheckItem:
    code: str
    message: str


@dataclass(frozen=True, slots=True, kw_only=True)
class PipelineQualityBaseline:
    version_number: int
    score: int


@dataclass(frozen=True, slots=True, kw_only=True)
class PipelinePreview:
    product_id: uuid.UUID
    candidate_version_id: uuid.UUID
    candidate_version_number: int
    candidate_active: bool
    source_updated_at: datetime
    approval_expected_updated_at: datetime
    original: PipelineListingView
    proposal: PipelineListingView
    quality_score: int | None
    quality_baseline: PipelineQualityBaseline | None
    quality_delta: int | None
    quality_score_version: int | None
    quality_breakdown: dict[str, Any] | None
    image_analysis: ImageAnalysisReport
    is_synthetic: bool
    provider: str | None
    channel_readiness: PublishReadinessResult | None
    pipeline_blockers: tuple[PipelineCheckItem, ...]
    pipeline_warnings: tuple[PipelineCheckItem, ...]
    publishable: bool


class ProductPipelineService(BaseService):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.products = ProductRepository(session)
        self.versions = ProductVersionRepository(session)
        self.images = ProductImageRepository(session)
        self.optimization = ProductOptimizationService(session)
        self.analysis = ImageAnalysisService(session)
        self.readiness = PublishReadinessService(session)
        self.shopify_sync = ShopifySyncService(session)

    async def preview(
        self,
        product_id: uuid.UUID,
        *,
        store_id: uuid.UUID | None = None,
        tone: str = "professional",
        requested_by_user_id: uuid.UUID | None,
    ) -> PipelinePreview:
        """Analyse, generate an inactive pipeline candidate, return a preview DTO.

        Does not activate. Does not publish. Does not commit.
        """
        report = await self.analysis.analyse_product_images(
            product_id, executed_by_user_id=requested_by_user_id
        )
        version = await self.optimization.generate_candidate(
            product_id,
            tone=tone,
            requested_by_user_id=requested_by_user_id,
        )
        product = await self.products.get_by_id_or_raise(product_id)
        return await self._compose_preview(
            product,
            version,
            image_analysis=report,
            store_id=store_id,
        )

    async def get_preview(
        self,
        product_id: uuid.UUID,
        *,
        version_id: uuid.UUID,
        store_id: uuid.UUID | None = None,
    ) -> PipelinePreview:
        """Compose a preview from an existing pipeline candidate.

        No analyse, no generate. Rejects non-pipeline versions.
        """
        product = await self.products.get_by_id_or_raise(product_id)
        version = await self.versions.get_by_id_for_product(
            product_id=product.id, version_id=version_id
        )
        if version is None:
            raise NotFoundError.for_resource("ProductVersion", version_id)
        parse_pipeline_candidate_metadata(version.content)
        return await self._compose_preview(
            product,
            version,
            image_analysis=await self._stored_image_report(product.id),
            store_id=store_id,
        )

    async def approve(
        self,
        product_id: uuid.UUID,
        *,
        version_id: uuid.UUID,
        expected_updated_at: datetime | None,
    ) -> Product:
        """Activate an exact pipeline candidate into the AI cache.

        Does not publish. Does not write merchant/supplier/SEO/image fields.
        `expected_updated_at` is required at runtime — None is a 422, matching
        the draft-editor posture, so a forgotten token cannot skip M2A.
        """
        product = await self.products.lock_for_update(product_id)
        if product is None:
            raise NotFoundError.for_resource("Product", product_id)
        version = await self.versions.get_by_id_for_product(
            product_id=product.id,
            version_id=version_id,
            populate_existing=True,
        )
        if version is None:
            raise NotFoundError.for_resource("ProductVersion", version_id)
        if version.source is not ProductVersionSource.AI_GENERATED:
            raise ValidationError(
                "The original snapshot cannot be approved as a pipeline candidate.",
                details={"reason": "original_not_approvable"},
            )
        metadata = parse_pipeline_candidate_metadata(version.content)
        if expected_updated_at is None:
            raise ValidationError(
                "expectedUpdatedAt is required to approve a pipeline candidate. "
                "Reload the product to get its current version, then approve again.",
            )
        if version.active:
            return product
        if expected_updated_at != product.updated_at:
            raise ConflictError(
                ("This draft changed somewhere else. Review the latest version before approving."),
                details={"reason": "draft_version_stale"},
            )
        if metadata.source_updated_at != product.updated_at:
            raise ConflictError(
                "The product changed after this preview was generated.",
                details={"reason": "stale_preview"},
            )
        title = version.content.get("title") if isinstance(version.content, dict) else None
        storage = _title_storage_error(title)
        if storage is not None:
            raise ValidationError(
                storage.message,
                details={"reason": storage.code},
            )
        activated = await self.versions.activate(product_id=product.id, version_id=version.id)
        self.optimization._apply_active_version(product, activated)
        await self.flush()
        return product

    async def publish(
        self,
        product_id: uuid.UUID,
        *,
        store_id: uuid.UUID,
        version_id: uuid.UUID,
        expected_updated_at: datetime | None,
    ) -> dict[str, Any]:
        """Publish an already-approved pipeline candidate through the overlay.

        The M2A token is required here even though merchant/Celery
        `publish_product` still allows omitting it. Check before the Product
        lock so a forgotten token cannot wait on, or skip, freshness.
        """
        if expected_updated_at is None:
            raise ValidationError(
                "expectedUpdatedAt is required to publish an approved pipeline candidate."
            )
        product = await self.products.lock_for_update(
            product_id,
            timeout_ms=shopify_sync.PUBLISH_LOCK_TIMEOUT_MS,
        )
        if product is None:
            raise NotFoundError.for_resource("Product", product_id)
        version = await self.versions.get_by_id_for_product(
            product_id=product.id,
            version_id=version_id,
            populate_existing=True,
        )
        if version is None:
            raise NotFoundError.for_resource("ProductVersion", version_id)
        if version.source is not ProductVersionSource.AI_GENERATED:
            raise ValidationError(
                "This pipeline candidate has not been approved.",
                details={"reason": "candidate_not_approved"},
            )
        metadata = parse_pipeline_candidate_metadata(version.content)
        if version.active is not True:
            raise ValidationError(
                "This pipeline candidate has not been approved.",
                details={"reason": "candidate_not_approved"},
            )
        provider = version.ai_provider
        if not isinstance(provider, str) or provider.strip() == "":
            raise ValidationError(
                "AI provider provenance is missing or unverified.",
                details={"reason": "ai_provenance_unverified"},
            )
        if metadata.is_synthetic is True or provider.strip() == "stub":
            raise ValidationError(
                "Synthetic AI content cannot be published to a sales channel.",
                details={"reason": "synthetic_publish_blocked"},
            )
        title = version.content.get("title") if isinstance(version.content, dict) else None
        publish_error = _title_publish_error(title)
        if publish_error is not None:
            raise ValidationError(
                publish_error.message,
                details={"reason": publish_error.code},
            )
        assert type(title) is str
        raw_description = (
            version.content.get("description") if isinstance(version.content, dict) else None
        )
        safe_body_html = _safe_body_html(raw_description)
        if len(safe_body_html) > DESCRIPTION_MAX_LENGTH:
            raise ValidationError(
                "The sanitized description exceeds the Shopify body length.",
                details={"reason": "candidate_description_too_long"},
            )
        overlay = ShopifyListingOverlay(
            title=title, body_html=safe_body_html, version_id=version.id
        )
        return await self.shopify_sync.publish_product(
            store_id=store_id,
            product_id=product_id,
            expected_updated_at=expected_updated_at,
            listing_overlay=overlay,
        )

    async def _compose_preview(
        self,
        product: Product,
        version: ProductVersion,
        *,
        image_analysis: ImageAnalysisReport,
        store_id: uuid.UUID | None,
    ) -> PipelinePreview:
        metadata = parse_pipeline_candidate_metadata(version.content)
        channel_readiness: PublishReadinessResult | None = None
        if store_id is not None:
            channel_readiness = await self.readiness.evaluate(
                channel=CHANNEL_SHOPIFY,
                product_id=product.id,
                store_id=store_id,
                expected_updated_at=product.updated_at,
                enforce_version=False,
            )
        blockers = _pipeline_blockers(product, version, metadata)
        warnings = _pipeline_warnings(image_analysis)
        publishable = (
            not blockers and channel_readiness is not None and channel_readiness.can_publish
        )
        content = version.content if isinstance(version.content, dict) else {}
        return PipelinePreview(
            product_id=product.id,
            candidate_version_id=version.id,
            candidate_version_number=version.version_number,
            candidate_active=version.active,
            source_updated_at=metadata.source_updated_at,
            approval_expected_updated_at=product.updated_at,
            original=_listing_from_product(product),
            proposal=_listing_from_version_content(content),
            quality_score=_optional_int(content.get("qualityScore")),
            quality_baseline=_quality_baseline(content.get("qualityBaseline")),
            quality_delta=_optional_int(content.get("qualityDelta")),
            quality_score_version=_optional_int(content.get("qualityScoreVersion")),
            quality_breakdown=_optional_dict(content.get("qualityBreakdown")),
            image_analysis=image_analysis,
            is_synthetic=metadata.is_synthetic,
            provider=version.ai_provider,
            channel_readiness=channel_readiness,
            pipeline_blockers=tuple(blockers),
            pipeline_warnings=tuple(warnings),
            publishable=publishable,
        )

    async def _stored_image_report(self, product_id: uuid.UUID) -> ImageAnalysisReport:
        live = await self.images.list_for_product(product_id)
        items: list[ImageAnalysisItem] = []
        for image in live:
            analysis = image.analysis if isinstance(image.analysis, dict) else {}
            status = analysis.get("status")
            error = analysis.get("errorCode")
            items.append(
                ImageAnalysisItem(
                    image_id=image.id,
                    position=image.position,
                    status=status if isinstance(status, str) else "unknown",
                    error_code=error if isinstance(error, str) else None,
                    analysis=analysis,
                )
            )
        return ImageAnalysisReport(product_id=product_id, images=tuple(items))


def _listing_from_product(product: Product) -> PipelineListingView:
    tags = tuple(tag for tag in (product.tags or []) if isinstance(tag, str))
    return PipelineListingView(
        title=product.title,
        description=product.description,
        seo_title=product.seo_title,
        seo_description=product.seo_description,
        keywords=product.meta_keywords,
        tags=tags,
    )


def _listing_from_version_content(content: dict[str, Any]) -> PipelineListingView:
    return PipelineListingView(
        title=_optional_str(content.get("title")),
        description=_optional_str(content.get("description")),
        seo_title=_optional_str(content.get("seoTitle")),
        seo_description=_optional_str(content.get("seoDescription")),
        keywords=_optional_str(content.get("keywords")),
        tags=(),
    )


def _pipeline_blockers(
    product: Product,
    version: ProductVersion,
    metadata: PipelineCandidateMetadata,
) -> list[PipelineCheckItem]:
    blockers: list[PipelineCheckItem] = []
    if version.source is not ProductVersionSource.AI_GENERATED or not version.active:
        blockers.append(
            PipelineCheckItem(
                code="candidate_not_approved",
                message="This pipeline candidate has not been approved.",
            )
        )
    if not version.active and metadata.source_updated_at != product.updated_at:
        blockers.append(
            PipelineCheckItem(
                code="stale_preview",
                message="The product changed after this preview was generated.",
            )
        )
    provider = version.ai_provider
    if not isinstance(provider, str) or provider.strip() == "":
        blockers.append(
            PipelineCheckItem(
                code="ai_provenance_unverified",
                message="AI provider provenance is missing or unverified.",
            )
        )
    elif metadata.is_synthetic is True or provider.strip() == "stub":
        blockers.append(
            PipelineCheckItem(
                code="synthetic_publish_blocked",
                message="Synthetic AI content cannot be published to a sales channel.",
            )
        )
    title = version.content.get("title") if isinstance(version.content, dict) else None
    storage = _title_storage_error(title)
    if storage is not None:
        blockers.append(storage)
    elif _title_publish_error(title) is not None:
        blockers.append(
            PipelineCheckItem(
                code="candidate_title_not_publishable",
                message="The candidate title exceeds the Shopify title length.",
            )
        )
    raw_description = (
        version.content.get("description") if isinstance(version.content, dict) else None
    )
    safe_body = _safe_body_html(raw_description)
    if len(safe_body) > DESCRIPTION_MAX_LENGTH:
        blockers.append(
            PipelineCheckItem(
                code="candidate_description_too_long",
                message="The sanitized description exceeds the Shopify body length.",
            )
        )
    return blockers


def _pipeline_warnings(report: ImageAnalysisReport) -> list[PipelineCheckItem]:
    warnings: list[PipelineCheckItem] = []
    for item in report.images:
        if item.status in {"fetchFailed", "decodeFailed"}:
            warnings.append(
                PipelineCheckItem(
                    code="image_analysis_incomplete",
                    message="Image analysis could not fully process one or more images.",
                )
            )
        elif item.status == "checksOnly":
            warnings.append(
                PipelineCheckItem(
                    code="image_analysis_checks_only",
                    message="Image analysis ran checks only; caption/alt were not proposed.",
                )
            )
        checks = item.analysis.get("checks") if isinstance(item.analysis, dict) else None
        if not isinstance(checks, dict):
            continue
        blur = checks.get("blur")
        if isinstance(blur, dict) and blur.get("isBlurry") is True:
            warnings.append(
                PipelineCheckItem(
                    code="image_blurry",
                    message="An image looks blurry.",
                )
            )
        duplicates = checks.get("duplicates")
        if isinstance(duplicates, dict):
            ids = duplicates.get("duplicateOfImageIds")
            if isinstance(ids, list) and ids:
                warnings.append(
                    PipelineCheckItem(
                        code="image_duplicate",
                        message="An image is a byte-identical duplicate of another.",
                    )
                )
    return warnings


def _title_storage_error(title: object) -> PipelineCheckItem | None:
    if type(title) is not str or title.strip() == "" or len(title) > PIPELINE_APPROVAL_TITLE_MAX:
        return PipelineCheckItem(
            code="candidate_content_invalid",
            message="The candidate title cannot be stored on the product.",
        )
    return None


def _title_publish_error(title: object) -> PipelineCheckItem | None:
    if type(title) is not str or title.strip() == "" or len(title) > PIPELINE_PUBLISH_TITLE_MAX:
        return PipelineCheckItem(
            code="candidate_title_not_publishable",
            message="The candidate title exceeds the Shopify title length.",
        )
    return None


def _safe_body_html(raw_description: object) -> str:
    if not isinstance(raw_description, str):
        raw_description = ""
    return sanitize_html(raw_description) or ""


def _quality_baseline(raw: object) -> PipelineQualityBaseline | None:
    if not isinstance(raw, dict):
        return None
    version_number = raw.get("versionNumber")
    score = raw.get("score")
    if type(version_number) is not int or type(score) is not int:
        return None
    return PipelineQualityBaseline(version_number=version_number, score=score)


def _optional_int(value: object) -> int | None:
    return value if type(value) is int else None


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_dict(value: object) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


__all__ = [
    "PIPELINE_APPROVAL_TITLE_MAX",
    "PIPELINE_PUBLISH_TITLE_MAX",
    "PipelineCheckItem",
    "PipelineListingView",
    "PipelinePreview",
    "PipelineQualityBaseline",
    "ProductPipelineService",
]
