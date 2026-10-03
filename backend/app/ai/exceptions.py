"""AI provider and prompt-management errors.

Mirrors `app.integrations.aliexpress.exceptions`: every failure carries a
stable machine-readable `code`. Provider failures inherit
`InfrastructureError` (503) — from a caller's perspective, "the configured AI
provider cannot answer" is the same kind of failure as "the database is
unreachable", not a client mistake. `MissingPromptVariablesError` inherits
`ValidationError` (422) instead: a caller who forgot a variable can fix their
own request, which is exactly what distinguishes a 4xx from a 5xx here.

The unavailable / rejected / malformed variants arrived with the first real
outbound provider (`OpenAIProvider`), which is their caller.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.core.exceptions import InfrastructureError, ValidationError

SERVICE_NAME = "ai"


class AIError(InfrastructureError):
    """Base for every AI-provider failure."""

    code = "ai_error"
    message = "The AI provider encountered an error."

    #: Whether retrying the identical request could plausibly succeed.
    retryable: bool = False


class AIProviderNotConfiguredError(AIError):
    """The configured provider has no implementation yet, or is unreachable.

    Not retryable: this is a deployment/configuration state, not a transient
    failure, so retrying the identical request cannot help. Raised by
    `app.ai.factory.get_ai_provider` rather than left to fail deeper in a
    generation service, so a misconfiguration surfaces where it was made.
    """

    code = "ai_provider_not_configured"
    message = "The configured AI provider is not available."
    retryable = False


class AIProviderUnavailableError(AIError):
    """A timeout, a connection failure, a 5xx or a rate limit.

    Retryable: the identical request can succeed a moment later. The provider
    retries these itself, up to `AI_MAX_RETRIES`; reaching a caller means the
    retries ran out.
    """

    code = "ai_provider_unavailable"
    message = "The AI provider is temporarily unavailable."
    retryable = True

    def __init__(
        self, message: str | None = None, *, retry_after_seconds: float | None = None
    ) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class AIProviderRejectedError(AIError):
    """The provider refused this request (a 4xx other than auth or 429).

    Not retryable: an invalid model name or an oversized prompt fails the same
    way every time.
    """

    code = "ai_provider_rejected"
    message = "The AI provider rejected the request."
    retryable = False


class AIResponseMalformedError(AIError):
    """The provider answered, but with nothing usable — no text, or not the
    structure asked for.

    Not retryable by the provider itself; a caller may run the whole
    generation again.
    """

    code = "ai_response_malformed"
    message = "The AI provider returned an unusable response."
    retryable = False


class MissingPromptVariablesError(ValidationError):
    """A prompt template could not be rendered — one or more `{{variables}}`
    it declares were not supplied.

    Raised at render time, before anything is sent anywhere. The alternative
    — rendering with gaps and sending `{title}` to a model — would pay for an
    answer nobody can use; failing here is strictly cheaper and clearer.
    """

    code = "missing_prompt_variables"
    message = "The prompt is missing one or more required variables."

    def __init__(self, missing: Sequence[str]) -> None:
        ordered = sorted(missing)
        super().__init__(
            f"Missing required prompt variables: {', '.join(ordered)}.",
            details={"missing_variables": ordered},
        )
        self.missing_variables = ordered


__all__ = [
    "SERVICE_NAME",
    "AIError",
    "AIProviderNotConfiguredError",
    "AIProviderRejectedError",
    "AIProviderUnavailableError",
    "AIResponseMalformedError",
    "MissingPromptVariablesError",
]
