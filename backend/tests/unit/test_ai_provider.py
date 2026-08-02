"""Tests for the AI contract types in `app.ai.provider`.

These pin two properties: the request/result types are immutable (a provider
must not be able to mutate the request it was handed), and every result
defaults `is_synthetic` to `False` — a real provider that forgets to set it
would otherwise silently look fake, which is the safe direction to fail in,
but still worth pinning so a future edit cannot flip the default the other way
by accident.
"""

from __future__ import annotations

import dataclasses

import pytest

from app.ai.provider import (
    CompletionRequest,
    CompletionResult,
    ImageAnalysisRequest,
    ImageAnalysisResult,
)

pytestmark = pytest.mark.unit


class TestCompletionRequest:
    def test_is_frozen(self) -> None:
        request = CompletionRequest(prompt="hello")
        with pytest.raises(dataclasses.FrozenInstanceError):
            request.prompt = "changed"  # type: ignore[misc]

    def test_defaults(self) -> None:
        request = CompletionRequest(prompt="hello")

        assert request.system is None
        assert request.max_output_tokens == 1024
        assert request.temperature == 0.7
        assert dict(request.metadata) == {}

    def test_requires_keyword_arguments(self) -> None:
        with pytest.raises(TypeError):
            CompletionRequest("hello")  # type: ignore[misc]


class TestCompletionResult:
    def test_is_synthetic_defaults_false(self) -> None:
        result = CompletionResult(text="hi", provider="openai", model="gpt-x")
        assert result.is_synthetic is False

    def test_is_frozen(self) -> None:
        result = CompletionResult(text="hi", provider="openai", model="gpt-x")
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.text = "changed"  # type: ignore[misc]


class TestImageAnalysisTypes:
    def test_request_defaults(self) -> None:
        request = ImageAnalysisRequest(image_url="https://example.invalid/a.jpg")
        assert request.instructions is None
        assert dict(request.metadata) == {}

    def test_result_is_synthetic_defaults_false(self) -> None:
        result = ImageAnalysisResult(
            caption="a mug",
            alt_text="a white mug",
            provider="openai",
            model="gpt-x",
        )
        assert result.is_synthetic is False
