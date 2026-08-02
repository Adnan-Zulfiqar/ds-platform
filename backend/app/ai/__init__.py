"""AI product-optimisation integration (Phase 9).

Stage 1 delivered the provider boundary: the `AIProvider` protocol, the
request/result contract types, the deterministic `StubProvider`, and the
`get_ai_provider` resolver. Stage 2 adds prompt management: safe
`{{variable}}` rendering via `PromptRenderer`. Product optimisation and
concrete cloud providers land in later stages — see docs/PHASE_9_PLAN.md.

Sits beside `app.integrations.aliexpress` in the dependency graph: this
package may import `app.models`; nothing in `app.models` may import it.
"""

from app.ai.exceptions import (
    AIError,
    AIProviderNotConfiguredError,
    MissingPromptVariablesError,
)
from app.ai.factory import get_ai_provider
from app.ai.prompt_renderer import PromptRenderer
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
    "MissingPromptVariablesError",
    "PromptRenderer",
    "StubProvider",
    "get_ai_provider",
]
