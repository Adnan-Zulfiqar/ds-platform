"""Composition point for `AIProvider`.

The only module allowed to name a concrete provider class. A service that
imports `StubProvider` directly — instead of calling `get_ai_provider` — is a
service that cannot be switched by configuration, which defeats the entire
point of drawing the boundary in `app.ai.provider`.
"""

from __future__ import annotations

from app.ai.exceptions import AIProviderNotConfiguredError
from app.ai.provider import AIProvider
from app.ai.stub_provider import StubProvider
from app.core.config import AIProviderName, Settings


def get_ai_provider(settings: Settings) -> AIProvider:
    """Resolve the `AIProvider` this deployment is configured to use.

    Concrete cloud providers are named in `AIProviderName` now so the
    operator-facing configuration surface is complete, but none has an
    implementation yet — each lands in the Phase 9 stage that first calls it
    (see docs/PHASE_9_PLAN.md §3), rather than being written ahead of a
    caller that could exercise it.

    Selecting one of them today is therefore a configuration error, not a
    silent fallback to the stub: a deployment that set `AI_PROVIDER=openai`
    believing it works must fail loudly at the point of use, not publish
    stub copy under a real provider's name.
    """
    if settings.ai.provider is AIProviderName.STUB:
        return StubProvider()

    raise AIProviderNotConfiguredError(
        f"AI_PROVIDER={settings.ai.provider.value} has no implementation yet. "
        "Set AI_PROVIDER=stub, or leave it unset, until this provider ships."
    )


__all__ = ["get_ai_provider"]
