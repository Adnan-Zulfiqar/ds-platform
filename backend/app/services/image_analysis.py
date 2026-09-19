"""Product-scoped image analysis. Stage 6 service; no HTTP, no commit."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.image_checks import (
    BLUR_THRESHOLD,
    WATERMARK_NA,
    WORKING_SIZE,
    blur_score,
    content_sha256,
    duplicate_of_image_ids,
    is_blurry,
    normalize_working_canvas,
)
from app.ai.image_decode import decode_image, persisted_format
from app.ai.image_fetch import (
    ImageFetchDecodeFailed,
    ImageFetcher,
    ImageFetchError,
    ImageFetchPixelLimit,
)
from app.ai.provider import ImageAnalysisResult
from app.models.ai_prompt import PromptExecution
from app.models.product import Product, ProductImage
from app.repositories.product import ProductImageRepository, ProductRepository
from app.services.base import BaseService
from app.services.prompt import PromptService

IMAGE_ANALYSIS_VERSION: Final[int] = 1
_PROMPT_NAME: Final[str] = "image_analyzer"


@dataclass(frozen=True, slots=True, kw_only=True)
class ImageAnalysisItem:
    image_id: uuid.UUID
    position: int
    status: str
    error_code: str | None
    analysis: dict[str, Any]


@dataclass(frozen=True, slots=True, kw_only=True)
class ImageAnalysisReport:
    product_id: uuid.UUID
    images: tuple[ImageAnalysisItem, ...]


@dataclass
class _PhaseASuccess:
    image: ProductImage
    raw_bytes: bytes
    digest: str
    decoded_width: int
    decoded_height: int
    decoded_format: str
    score: int
    duplicate_of: list[str]


@dataclass
class _PhaseAFailure:
    image: ProductImage
    status: str
    error_code: str


class ImageAnalysisService(BaseService):
    """Fetch, check, caption, and persist analysis on live product images."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        fetcher: ImageFetcher | None = None,
    ) -> None:
        super().__init__(session)
        self.products = ProductRepository(session)
        self.images = ProductImageRepository(session)
        self.prompts = PromptService(session)
        self.fetcher = fetcher or ImageFetcher()

    async def analyse_product_images(
        self,
        product_id: uuid.UUID,
        *,
        executed_by_user_id: uuid.UUID | None = None,
    ) -> ImageAnalysisReport:
        product = await self.products.get_by_id_or_raise(product_id)
        live = await self.images.list_for_product(product.id)
        if not live:
            return ImageAnalysisReport(product_id=product.id, images=())

        acquired: list[_PhaseASuccess | _PhaseAFailure] = []
        for image in live:
            acquired.append(await self._phase_a(image))

        successes = [row for row in acquired if isinstance(row, _PhaseASuccess)]
        grouped = duplicate_of_image_ids(
            tuple((item.image.id, item.digest) for item in successes)
        )
        for item in successes:
            item.duplicate_of = grouped[item.image.id]

        items: list[ImageAnalysisItem] = []
        for acquired_row in acquired:
            if isinstance(acquired_row, _PhaseAFailure):
                analysis = _failure_payload(
                    acquired_row.image.url,
                    status=acquired_row.status,
                    error_code=acquired_row.error_code,
                )
                acquired_row.image.analysis = analysis
                items.append(
                    ImageAnalysisItem(
                        image_id=acquired_row.image.id,
                        position=acquired_row.image.position,
                        status=acquired_row.status,
                        error_code=acquired_row.error_code,
                        analysis=analysis,
                    )
                )
                continue
            analysis = await self._phase_c(
                product,
                acquired_row,
                executed_by_user_id=executed_by_user_id,
            )
            acquired_row.image.analysis = analysis
            items.append(
                ImageAnalysisItem(
                    image_id=acquired_row.image.id,
                    position=acquired_row.image.position,
                    status=str(analysis["status"]),
                    error_code=analysis["errorCode"]
                    if isinstance(analysis["errorCode"], str)
                    else None,
                    analysis=analysis,
                )
            )

        await self.flush()
        return ImageAnalysisReport(product_id=product.id, images=tuple(items))

    async def _phase_a(self, image: ProductImage) -> _PhaseASuccess | _PhaseAFailure:
        try:
            raw = await self.fetcher.fetch(image.url)
            decoded = decode_image(raw)
            canvas, _new_w, _new_h, _left, _top = normalize_working_canvas(decoded)
            score = blur_score(canvas)
        except ImageFetchError as exc:
            status = (
                "decodeFailed"
                if isinstance(exc, ImageFetchPixelLimit | ImageFetchDecodeFailed)
                else "fetchFailed"
            )
            return _PhaseAFailure(image=image, status=status, error_code=type(exc).__name__)
        return _PhaseASuccess(
            image=image,
            raw_bytes=raw,
            digest=content_sha256(raw),
            decoded_width=decoded.size[0],
            decoded_height=decoded.size[1],
            decoded_format=persisted_format(decoded),
            score=score,
            duplicate_of=[],
        )

    async def _phase_c(
        self,
        product: Product,
        row: _PhaseASuccess,
        *,
        executed_by_user_id: uuid.UUID | None,
    ) -> dict[str, Any]:
        _rendered, result, execution = await self.prompts.execute_image_analysis(
            name=_PROMPT_NAME,
            variables={"image_url": row.image.url, "product_title": product.title},
            executed_by_user_id=executed_by_user_id,
        )
        checks = _checks_block(row)
        if result is None:
            return _evidence(
                url=row.image.url,
                row=row,
                checks=checks,
                status="checksOnly",
                error_code=execution.error_code,
                result=None,
                execution=execution,
            )
        return _evidence(
            url=row.image.url,
            row=row,
            checks=checks,
            status="succeeded",
            error_code=None,
            result=result,
            execution=execution,
        )


def _checks_block(row: _PhaseASuccess) -> dict[str, Any]:
    return {
        "blur": {
            "applicable": True,
            "blurScore": row.score,
            "isBlurry": is_blurry(row.score),
            "threshold": BLUR_THRESHOLD,
            "workingSize": WORKING_SIZE,
        },
        "duplicates": {
            "applicable": True,
            "contentSha256": row.digest,
            "duplicateOfImageIds": list(row.duplicate_of),
        },
        "watermark": dict(WATERMARK_NA),
    }


def _failure_payload(url: str, *, status: str, error_code: str) -> dict[str, Any]:
    return {
        "imageAnalysisVersion": IMAGE_ANALYSIS_VERSION,
        "sourceUrl": url,
        "contentSha256": None,
        "byteLength": None,
        "decodedWidth": None,
        "decodedHeight": None,
        "decodedFormat": None,
        "status": status,
        "errorCode": error_code,
        "checks": None,
        "captionProposal": None,
        "altTextProposal": None,
        "isSynthetic": None,
        "provider": None,
        "model": None,
        "promptName": None,
        "promptVersion": None,
    }


def _evidence(
    *,
    url: str,
    row: _PhaseASuccess,
    checks: dict[str, Any],
    status: str,
    error_code: str | None,
    result: ImageAnalysisResult | None,
    execution: PromptExecution,
) -> dict[str, Any]:
    return {
        "imageAnalysisVersion": IMAGE_ANALYSIS_VERSION,
        "sourceUrl": url,
        "contentSha256": row.digest,
        "byteLength": len(row.raw_bytes),
        "decodedWidth": row.decoded_width,
        "decodedHeight": row.decoded_height,
        "decodedFormat": row.decoded_format,
        "status": status,
        "errorCode": error_code,
        "checks": checks,
        "captionProposal": None if result is None else result.caption,
        "altTextProposal": None if result is None else result.alt_text,
        "isSynthetic": None if result is None else result.is_synthetic,
        "provider": None if result is None else result.provider,
        "model": None if result is None else result.model,
        "promptName": execution.prompt_name,
        "promptVersion": execution.prompt_version,
    }


__all__ = [
    "IMAGE_ANALYSIS_VERSION",
    "ImageAnalysisItem",
    "ImageAnalysisReport",
    "ImageAnalysisService",
]
