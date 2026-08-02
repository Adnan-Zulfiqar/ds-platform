"""AI provider errors.

Mirrors `app.integrations.aliexpress.exceptions`: every failure carries a
stable machine-readable `code` and inherits `InfrastructureError` (503) — from
a caller's perspective, "the configured AI provider cannot answer" is the same
kind of failure as "the database is unreachable", not a client mistake.

Kept to what stage 1 actually raises. Timeout, rate-limit, and malformed-
response variants belong to whichever stage first makes a real outbound model
call and can therefore raise them — adding them now would be exception classes
with no caller, which is the thing CLAUDE.md's KISS rule warns against.
"""

from __future__ import annotations

from app.core.exceptions import InfrastructureError

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


__all__ = [
    "SERVICE_NAME",
    "AIError",
    "AIProviderNotConfiguredError",
]
