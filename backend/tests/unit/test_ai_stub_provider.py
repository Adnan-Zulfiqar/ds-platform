"""Tests for `StubProvider`.

The property that matters most: output is deterministic and unmistakably
synthetic. A real provider is not testable without a live key; the stub is
the provider every other stage tests against until one is added, so its
behaviour has to be exactly assertable.
"""

from __future__ import annotations

import pytest

from app.ai.provider import CompletionRequest, ImageAnalysisRequest
from app.ai.stub_provider import StubProvider

pytestmark = pytest.mark.unit


class TestComplete:
    async def test_marks_output_as_synthetic(self) -> None:
        provider = StubProvider()

        result = await provider.complete(CompletionRequest(prompt="Write a title for a mug"))

        assert result.is_synthetic is True
        assert result.provider == "stub"

    async def test_output_is_visibly_fake(self) -> None:
        provider = StubProvider()

        result = await provider.complete(CompletionRequest(prompt="Write a title for a mug"))

        assert "[STUB-AI]" in result.text

    async def test_same_prompt_produces_the_same_output(self) -> None:
        provider = StubProvider()
        request = CompletionRequest(prompt="Write a title for a mug")

        first = await provider.complete(request)
        second = await provider.complete(request)

        assert first.text == second.text

    async def test_different_prompts_produce_different_output(self) -> None:
        provider = StubProvider()

        first = await provider.complete(CompletionRequest(prompt="Write a title for a mug"))
        second = await provider.complete(CompletionRequest(prompt="Write a title for a lamp"))

        assert first.text != second.text

    async def test_token_counts_are_derived_from_the_text(self) -> None:
        provider = StubProvider()
        request = CompletionRequest(prompt="one two three")

        result = await provider.complete(request)

        assert result.input_tokens == 3
        assert result.output_tokens == len(result.text.split())


class TestAnalyseImage:
    async def test_marks_output_as_synthetic(self) -> None:
        provider = StubProvider()

        result = await provider.analyse_image(
            ImageAnalysisRequest(image_url="https://example.invalid/mug.jpg")
        )

        assert result.is_synthetic is True
        assert "[STUB-AI]" in result.caption
        assert "[STUB-AI]" in result.alt_text

    async def test_same_url_produces_the_same_output(self) -> None:
        provider = StubProvider()
        request = ImageAnalysisRequest(image_url="https://example.invalid/mug.jpg")

        first = await provider.analyse_image(request)
        second = await provider.analyse_image(request)

        assert first.caption == second.caption
        assert first.alt_text == second.alt_text

    async def test_different_urls_produce_different_output(self) -> None:
        provider = StubProvider()

        first = await provider.analyse_image(
            ImageAnalysisRequest(image_url="https://example.invalid/mug.jpg")
        )
        second = await provider.analyse_image(
            ImageAnalysisRequest(image_url="https://example.invalid/lamp.jpg")
        )

        assert first.caption != second.caption
