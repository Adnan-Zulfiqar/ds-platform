"""The default `AIProvider` when no real one is configured.

**Not a test double.** `app.ai.factory.get_ai_provider` selects this whenever
`AI_PROVIDER=stub` — which is the default, so it is also what runs in local
development and in any deployment that never set `AI_PROVIDER` at all.

Its output is deterministic and unmistakably synthetic. That is the point: a
deployment that forgot to configure a real provider gets visibly fake copy
that nobody would mistake for a real listing, rather than silently plausible
text someone publishes without noticing the key was never set.
"""

from __future__ import annotations

import hashlib

from app.ai.provider import (
    CompletionRequest,
    CompletionResult,
    ImageAnalysisRequest,
    ImageAnalysisResult,
)

#: Prefixes every piece of stub output, so it is recognisable in a database
#: row, a log line, or a screenshot without inspecting `is_synthetic`.
_MARKER = "[STUB-AI]"


def _short_digest(value: str) -> str:
    """A short, deterministic fingerprint of the input.

    Not a security use of the hash — only used so that the same input always
    produces the same stub output, which is what makes the stub's behaviour
    exactly assertable in tests.
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


class StubProvider:
    """Deterministic, keyless implementation of `AIProvider`."""

    name = "stub"
    _model = "stub-1"

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        digest = _short_digest(request.prompt)
        text = f"{_MARKER} synthetic completion (prompt {digest})."
        return CompletionResult(
            text=text,
            provider=self.name,
            model=self._model,
            input_tokens=len(request.prompt.split()),
            output_tokens=len(text.split()),
            finish_reason="stop",
            is_synthetic=True,
        )

    async def analyse_image(self, request: ImageAnalysisRequest) -> ImageAnalysisResult:
        digest = _short_digest(request.image_url)
        return ImageAnalysisResult(
            caption=f"{_MARKER} synthetic caption (image {digest}).",
            alt_text=f"{_MARKER} synthetic alt text (image {digest}).",
            provider=self.name,
            model=self._model,
            is_synthetic=True,
        )


__all__ = ["StubProvider"]
