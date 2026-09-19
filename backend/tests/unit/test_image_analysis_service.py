"""ImageAnalysisService Phase A/B/C without a live network or database."""

from __future__ import annotations

import hashlib
import uuid
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image

from app.ai.image_fetch import ImageFetcher, ImageFetchNotGlobalAddress
from app.ai.provider import ImageAnalysisResult
from app.core.exceptions import NotFoundError
from app.models.ai_prompt import PromptExecutionStatus
from app.services.image_analysis import ImageAnalysisService

pytestmark = pytest.mark.unit


def _png(color: tuple[int, int, int] = (40, 40, 40)) -> bytes:
    image = Image.new("RGB", (32, 32), color)
    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _image(*, url: str, position: int = 0, alt_text: str | None = None) -> MagicMock:
    row = MagicMock()
    row.id = uuid.uuid4()
    row.url = url
    row.position = position
    row.alt_text = alt_text
    row.is_supplier = True
    row.analysis = None
    return row


def _service() -> ImageAnalysisService:
    session = MagicMock()
    session.flush = AsyncMock()
    service = ImageAnalysisService(session, fetcher=ImageFetcher())
    service.products.get_by_id_or_raise = AsyncMock()  # type: ignore[method-assign]
    service.images.list_for_product = AsyncMock()  # type: ignore[method-assign]
    service.prompts.execute_image_analysis = AsyncMock()  # type: ignore[method-assign]
    service.flush = AsyncMock()  # type: ignore[method-assign]
    return service


class TestImageAnalysisService:
    async def test_empty_image_list_does_not_call_provider_or_flush(self) -> None:
        service = _service()
        product = MagicMock(id=uuid.uuid4(), title="Mug")
        service.products.get_by_id_or_raise.return_value = product
        service.images.list_for_product.return_value = []

        report = await service.analyse_product_images(product.id)

        assert report.images == ()
        service.prompts.execute_image_analysis.assert_not_called()
        service.flush.assert_not_called()

    async def test_missing_product_is_not_found(self) -> None:
        service = _service()
        service.products.get_by_id_or_raise.side_effect = NotFoundError.for_resource(
            "Product", uuid.uuid4()
        )
        with pytest.raises(NotFoundError):
            await service.analyse_product_images(uuid.uuid4())

    async def test_fetch_failure_skips_provider_and_persists_fetch_failed(self) -> None:
        service = _service()
        product = MagicMock(id=uuid.uuid4(), title="Mug")
        image = _image(url="https://127.0.0.1/x.jpg")
        service.products.get_by_id_or_raise.return_value = product
        service.images.list_for_product.return_value = [image]
        service.fetcher.fetch = AsyncMock(side_effect=ImageFetchNotGlobalAddress())

        report = await service.analyse_product_images(product.id)

        service.prompts.execute_image_analysis.assert_not_called()
        assert report.images[0].status == "fetchFailed"
        assert report.images[0].error_code == "ImageFetchNotGlobalAddress"
        assert image.analysis["status"] == "fetchFailed"
        assert image.alt_text is None
        service.flush.assert_awaited_once()

    async def test_success_and_duplicate_symmetry(self) -> None:
        service = _service()
        product = MagicMock(id=uuid.uuid4(), title="Mug")
        body = _png()
        first = _image(url="https://cdn.example/a.png", position=0)
        second = _image(url="https://cdn.example/b.png", position=1)
        service.products.get_by_id_or_raise.return_value = product
        service.images.list_for_product.return_value = [first, second]
        service.fetcher.fetch = AsyncMock(return_value=body)
        execution = MagicMock(
            prompt_name="image_analyzer",
            prompt_version=1,
            status=PromptExecutionStatus.SUCCEEDED,
            error_code=None,
        )
        result = ImageAnalysisResult(
            caption="[STUB-AI] synthetic caption (image deadbeef).",
            alt_text="[STUB-AI] synthetic alt text (image deadbeef).",
            provider="stub",
            model="stub-1",
            is_synthetic=True,
        )
        service.prompts.execute_image_analysis.return_value = ("rendered", result, execution)

        report = await service.analyse_product_images(product.id)

        assert report.images[0].status == "succeeded"
        assert first.analysis["isSynthetic"] is True
        assert first.analysis["checks"]["watermark"] == {
            "applicable": False,
            "reason": "genericWatermarkDetectionNotImplemented",
        }
        assert first.analysis["checks"]["duplicates"]["duplicateOfImageIds"] == [str(second.id)]
        assert second.analysis["checks"]["duplicates"]["duplicateOfImageIds"] == [str(first.id)]
        assert first.alt_text is None
        assert service.prompts.execute_image_analysis.await_count == 2

    async def test_ai_error_keeps_checks_only(self) -> None:
        service = _service()
        product = MagicMock(id=uuid.uuid4(), title="Mug")
        image = _image(url="https://cdn.example/a.png")
        service.products.get_by_id_or_raise.return_value = product
        service.images.list_for_product.return_value = [image]
        service.fetcher.fetch = AsyncMock(return_value=_png())
        execution = MagicMock(
            prompt_name="image_analyzer",
            prompt_version=1,
            status=PromptExecutionStatus.FAILED,
            error_code="AIProviderNotConfiguredError",
        )
        service.prompts.execute_image_analysis.return_value = ("rendered", None, execution)

        report = await service.analyse_product_images(product.id)

        assert report.images[0].status == "checksOnly"
        assert image.analysis["errorCode"] == "AIProviderNotConfiguredError"
        assert image.analysis["checks"]["blur"]["applicable"] is True
        assert image.analysis["captionProposal"] is None

    async def test_programming_error_in_checks_propagates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service = _service()
        product = MagicMock(id=uuid.uuid4(), title="Mug")
        image = _image(url="https://cdn.example/a.png")
        service.products.get_by_id_or_raise.return_value = product
        service.images.list_for_product.return_value = [image]
        service.fetcher.fetch = AsyncMock(return_value=_png())

        def explode(*_args: object, **_kwargs: object) -> None:
            raise TypeError("bug")

        monkeypatch.setattr("app.services.image_analysis.blur_score", explode)

        with pytest.raises(TypeError):
            await service.analyse_product_images(product.id)
        service.prompts.execute_image_analysis.assert_not_called()
        service.flush.assert_not_called()

    async def test_merchant_fields_and_duplicates_with_a_failed_sibling(self) -> None:
        service = _service()
        product = MagicMock(id=uuid.uuid4(), title="Mug")
        body = _png()
        live = _image(url="https://cdn.example/a.png", position=0, alt_text="Keep me")
        live.is_supplier = False
        failed = _image(url="https://127.0.0.1/x.jpg", position=1)
        service.products.get_by_id_or_raise.return_value = product
        service.images.list_for_product.return_value = [live, failed]

        async def _fetch(url: str) -> bytes:
            if url == live.url:
                return body
            raise ImageFetchNotGlobalAddress()

        service.fetcher.fetch = AsyncMock(side_effect=_fetch)
        execution = MagicMock(
            prompt_name="image_analyzer",
            prompt_version=1,
            status=PromptExecutionStatus.SUCCEEDED,
            error_code=None,
        )
        digest = hashlib.sha256(live.url.encode()).hexdigest()[:8]
        result = ImageAnalysisResult(
            caption=f"[STUB-AI] synthetic caption (image {digest}).",
            alt_text=f"[STUB-AI] synthetic alt text (image {digest}).",
            provider="stub",
            model="stub-1",
            is_synthetic=True,
        )
        service.prompts.execute_image_analysis.return_value = ("rendered", result, execution)
        service.session.commit = AsyncMock()

        report = await service.analyse_product_images(product.id)

        assert report.images[0].status == "succeeded"
        assert report.images[1].status == "fetchFailed"
        assert live.analysis["checks"]["duplicates"]["duplicateOfImageIds"] == []
        assert live.alt_text == "Keep me"
        assert live.url == "https://cdn.example/a.png"
        assert live.position == 0
        assert live.is_supplier is False
        assert failed.analysis["captionProposal"] is None
        assert failed.analysis["promptName"] is None
        service.session.commit.assert_not_called()
        service.prompts.execute_image_analysis.assert_awaited_once()

    async def test_reanalysis_replaces_content_hash(self) -> None:
        service = _service()
        product = MagicMock(id=uuid.uuid4(), title="Mug")
        image = _image(url="https://cdn.example/a.png")
        service.products.get_by_id_or_raise.return_value = product
        service.images.list_for_product.return_value = [image]
        first = _png((10, 10, 10))
        second = _png((200, 10, 10))
        service.fetcher.fetch = AsyncMock(side_effect=[first, second])
        execution = MagicMock(
            prompt_name="image_analyzer",
            prompt_version=1,
            status=PromptExecutionStatus.SUCCEEDED,
            error_code=None,
        )
        result = ImageAnalysisResult(
            caption="x",
            alt_text="y",
            provider="stub",
            model="stub-1",
            is_synthetic=True,
        )
        service.prompts.execute_image_analysis.return_value = ("rendered", result, execution)

        await service.analyse_product_images(product.id)
        first_hash = image.analysis["contentSha256"]
        await service.analyse_product_images(product.id)
        assert image.analysis["contentSha256"] != first_hash
        assert image.analysis["contentSha256"] == hashlib.sha256(second).hexdigest()

    async def test_decode_failure_skips_provider(self) -> None:
        service = _service()
        product = MagicMock(id=uuid.uuid4(), title="Mug")
        image = _image(url="https://cdn.example/a.png")
        service.products.get_by_id_or_raise.return_value = product
        service.images.list_for_product.return_value = [image]
        service.fetcher.fetch = AsyncMock(return_value=b"not-an-image")

        report = await service.analyse_product_images(product.id)

        service.prompts.execute_image_analysis.assert_not_called()
        assert report.images[0].status == "decodeFailed"
        assert report.images[0].error_code == "ImageFetchDecodeFailed"
