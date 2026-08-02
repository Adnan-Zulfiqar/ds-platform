"""Prompt management API schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.models.ai_prompt import PromptExecutionStatus
from app.schemas.base import CamelCaseModel


class AIPromptRead(CamelCaseModel):
    """One version of one named prompt.

    ``required_variables`` is derived from ``template`` at read time by
    ``PromptRenderer.extract_variables`` rather than stored — see
    ``app.ai.prompt_renderer`` for why a stored list would be a second,
    driftable source of truth.
    """

    id: uuid.UUID
    name: str
    description: str | None = None
    template: str
    target_model: str | None = None
    version: int
    active: bool
    required_variables: list[str]
    created_at: datetime
    updated_at: datetime


class PromptCreateRequest(CamelCaseModel):
    """Create a new prompt name at version 1, active immediately.

    ``name`` is a stable slug an admin UI and, eventually, generation
    services reference by string — lowercase, snake_case, matching the
    seeded defaults (``product_title_generator`` and similar).
    """

    name: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_]*$")
    description: str | None = Field(default=None, max_length=512)
    template: str = Field(min_length=1)
    target_model: str | None = Field(default=None, max_length=128)


class PromptVersionCreateRequest(CamelCaseModel):
    """Add a new, initially-inactive version under an existing name.

    This is "update" in the brief's terms — never a mutation of a stored
    template. ``description``/``target_model`` inherit the previous version's
    value when omitted, so a wording-only revision does not require
    repeating metadata that has not changed.
    """

    template: str = Field(min_length=1)
    description: str | None = Field(default=None, max_length=512)
    target_model: str | None = Field(default=None, max_length=128)


class PromptTestRenderRequest(CamelCaseModel):
    """Render the active version of a prompt, and optionally run it."""

    variables: dict[str, str] = Field(default_factory=dict)
    execute: bool = Field(
        default=False,
        description=(
            "If true, also send the rendered prompt through the configured "
            "AI provider (StubProvider unless a real one is configured — "
            "Stage 2 makes no real AI API call) and record a PromptExecution."
        ),
    )


class PromptExecutionRead(CamelCaseModel):
    """One recorded render-and-run attempt."""

    id: uuid.UUID
    prompt_name: str
    prompt_version: int
    provider: str
    model: str | None = None
    response_text: str | None = None
    is_synthetic: bool
    input_tokens: int | None = None
    output_tokens: int | None = None
    status: PromptExecutionStatus
    error_code: str | None = None
    error_message: str | None = None
    duration_ms: int | None = None
    created_at: datetime


class PromptTestRenderResponse(CamelCaseModel):
    """Result of rendering (and optionally running) a prompt."""

    rendered_prompt: str
    required_variables: list[str]
    execution: PromptExecutionRead | None = None


__all__ = [
    "AIPromptRead",
    "PromptCreateRequest",
    "PromptExecutionRead",
    "PromptTestRenderRequest",
    "PromptTestRenderResponse",
    "PromptVersionCreateRequest",
]
