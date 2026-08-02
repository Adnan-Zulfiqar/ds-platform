"""The `AIProvider` boundary: the one abstraction every model call goes through.

A service that imports a concrete provider class is a service that cannot be
switched by configuration — so nothing outside `app.ai` should import
`StubProvider`, `OpenAIProvider`, or any future provider directly.
`app.ai.factory.get_ai_provider` is the single place a concrete class is
named; everything else depends on this Protocol and these contract types.

Request and result types are frozen dataclasses rather than provider-specific
payloads, so a generation service (Phase 9 stage 4) can be written once and
run unchanged against any provider that satisfies the Protocol.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True, slots=True, kw_only=True)
class CompletionRequest:
    """A single prompt sent to a language model."""

    prompt: str
    system: str | None = None
    max_output_tokens: int = 1024
    temperature: float = 0.7
    #: Free-form tags (e.g. tenant_id, purpose) for logging and cost
    #: attribution. Never put customer secrets here — providers may log it.
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True, kw_only=True)
class CompletionResult:
    """A model's answer to a `CompletionRequest`."""

    text: str
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    finish_reason: str = "stop"
    #: True when this result did not come from a real model. Every caller
    #: that stores or renders a completion must check this and mark the
    #: result accordingly — that is what stops a deployment that forgot its
    #: provider key from quietly publishing fabricated copy.
    is_synthetic: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class ImageAnalysisRequest:
    """A single image submitted for model-backed captioning or alt text."""

    image_url: str
    instructions: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True, kw_only=True)
class ImageAnalysisResult:
    """A model's description of an `ImageAnalysisRequest`.

    Deliberately narrow: blur, duplicate, and watermark detection are
    deterministic image processing, not language, and stage 6 gives them
    their own path that never calls a model. This type covers only the part
    that genuinely needs one — captions and alt text.
    """

    caption: str
    alt_text: str
    provider: str
    model: str
    is_synthetic: bool = False


class AIProvider(Protocol):
    """A language/vision model backend, selected by configuration.

    Concrete providers are async because every real implementation is an
    outbound HTTP call; `StubProvider` is async too so callers never branch
    on which kind of provider they were given.
    """

    #: Stable identifier persisted alongside every generation it produces.
    #: This is how a stored suggestion is traced back to what produced it
    #: after `AI_PROVIDER` changes — the same reason AliExpress imports
    #: record an `external_id` rather than trusting position in a list.
    name: str

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        """Generate text from a prompt."""
        ...

    async def analyse_image(self, request: ImageAnalysisRequest) -> ImageAnalysisResult:
        """Generate a caption and alt text for an image."""
        ...


__all__ = [
    "AIProvider",
    "CompletionRequest",
    "CompletionResult",
    "ImageAnalysisRequest",
    "ImageAnalysisResult",
]
