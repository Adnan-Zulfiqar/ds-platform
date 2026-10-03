"""Composition point for `AIProvider`.

The only module allowed to name a concrete provider class. A service that
imports `StubProvider` directly — instead of calling `get_ai_provider` — is a
service that cannot be switched by configuration, which defeats the entire
point of drawing the boundary in `app.ai.provider`.
"""

from __future__ import annotations

from app.ai.exceptions import AIProviderNotConfiguredError
from app.ai.openai_provider import OpenAIProvider
from app.ai.provider import AIProvider
from app.ai.stub_provider import StubProvider
from app.core.config import AIProviderName, Settings


def get_ai_provider(settings: Settings) -> AIProvider:
    """Resolve the `AIProvider` this deployment is configured to use.

    `stub` and `openai` are implemented. The other names in `AIProviderName`
    stay a configuration error until each has a caller-driven implementation:
    a deployment that set `AI_PROVIDER=anthropic` believing it works must
    fail loudly at the point of use, not publish stub copy under a real
    provider's name. The same holds for `openai` with its key or model
    missing — the error names the missing setting, never a value.
    """
    ai = settings.ai
    if ai.provider is AIProviderName.STUB:
        return StubProvider()

    if ai.provider is AIProviderName.OPENAI:
        api_key = ai.openai_api_key.get_secret_value().strip() if ai.openai_api_key else ""
        missing = [
            name
            for name, value in (
                ("AI_OPENAI_API_KEY", api_key),
                ("AI_OPENAI_MODEL", ai.openai_model.strip()),
            )
            if not value
        ]
        if missing:
            raise AIProviderNotConfiguredError(f"AI_PROVIDER=openai needs {' and '.join(missing)}.")
        return OpenAIProvider(
            api_key=api_key,
            model=ai.openai_model.strip(),
            base_url=ai.openai_base_url,
            request_timeout_seconds=ai.request_timeout_seconds,
            connect_timeout_seconds=ai.connect_timeout_seconds,
            max_retries=ai.max_retries,
        )

    raise AIProviderNotConfiguredError(
        f"AI_PROVIDER={ai.provider.value} has no implementation yet. "
        "Use AI_PROVIDER=openai or stub until this provider ships."
    )


__all__ = ["get_ai_provider"]
