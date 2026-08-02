"""AI product-optimisation integration (Phase 9).

Stage 1 delivers the provider boundary only: the `AIProvider` protocol, the
request/result contract types, the deterministic `StubProvider`, and the
`get_ai_provider` resolver. Prompt management, product optimisation, and
concrete cloud providers land in later stages — see docs/PHASE_9_PLAN.md.

Sits beside `app.integrations.aliexpress` in the dependency graph: this
package may import `app.models`; nothing in `app.models` may import it.
"""

from app.ai.exceptions import AIError, AIProviderNotConfiguredError
from app.ai.factory import get_ai_provider
from app.ai.provider import (
    AIProvider,
    CompletionRequest,
    CompletionResult,
    ImageAnalysisRequest,
    ImageAnalysisResult,
)
from app.ai.stub_provider import StubProvider

__all__ = [
    "AIError",
    "AIProvider",
    "AIProviderNotConfiguredError",
    "CompletionRequest",
    "CompletionResult",
    "ImageAnalysisRequest",
    "ImageAnalysisResult",
    "StubProvider",
    "get_ai_provider",
]
