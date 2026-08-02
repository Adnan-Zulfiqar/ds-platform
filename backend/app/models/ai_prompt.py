"""AI prompt management: versioned templates and their execution audit trail.

**Prompts are platform reference data, not tenant data.** `AIPrompt` inherits
`ReferenceBase`, the same base `Role` uses: every tenant draws generation
behaviour from the same prompt library, so there is nothing to scope by
tenant. `PromptExecution` is the opposite — a record of one tenant's attempt
to render (and optionally run) a prompt — so it inherits `TenantScopedBase`
like `ProductImport`, the audit-trail model this one is patterned on.

**Versioning is append-only.** "Update" never mutates a stored template; it
inserts a new row with an incremented `version`. Rollback is "activate an
older version" rather than a separate operation — the same mechanism serves
both, and history is simply every row sharing a `name`. An audit trail that
can be rewritten is not one.

**At most one active version per name**, enforced by a partial unique index
rather than application discipline alone — the same reasoning as the
`(tenant_id, source, external_id)` constraint on `products`: a database
constraint cannot be bypassed by a code path that forgot to check first.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import ReferenceBase, TenantScopedBase


class PromptExecutionStatus(StrEnum):
    """Outcome of one render-and-run attempt.

    Only two, terminal values. Stage 2's execution path is a single
    synchronous request — render, optionally call the configured provider,
    write one row reflecting the final outcome — so there is no in-flight
    state to persist. A later stage that runs prompts through Celery (Phase 9
    stage 9) can extend this with PENDING/RUNNING when it has a caller that
    needs them; adding enum values later is a non-breaking migration.
    """

    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AIPrompt(ReferenceBase):
    """One version of one named prompt template.

    `name` is a stable slug identifying the prompt "family" (for example
    `product_title_generator`); it repeats across every version of that
    prompt. `template` holds `{{variable}}` placeholders rendered by
    `app.ai.prompt_renderer.PromptRenderer` — required variables are derived
    by parsing the template rather than stored separately, so there is no
    second copy of that information that could drift out of sync with the
    text itself.
    """

    __tablename__ = "ai_prompts"

    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_ai_prompts_name_version"),
        Index("ix_ai_prompts_name_version", "name", "version"),
        # Partial unique index: at most one row per `name` may have
        # `active = true`. This is what makes activation safe under
        # concurrent requests — the database rejects a second simultaneous
        # activation rather than trusting the service layer's read-then-write
        # to never race. `text("active")` rather than a column reference:
        # `__table_args__` is evaluated before `AIPrompt.active` exists as a
        # bound attribute, but the column exists under that name by the time
        # this index's DDL runs.
        Index(
            "uq_ai_prompts_name_active",
            "name",
            unique=True,
            postgresql_where=text("active"),
        ),
    )

    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    template: Mapped[str] = mapped_column(Text, nullable=False)

    #: Free-text hint naming the model this version was written and tuned
    #: for (e.g. "gpt-4o-mini"). Not enforced or read by any provider yet —
    #: Stage 2 does not call a real model — but recorded now so a reader
    #: auditing output quality later knows what the prompt assumed.
    target_model: Mapped[str | None] = mapped_column(String(128), nullable=True)

    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    #: Who authored this version. SET NULL so removing a user does not erase
    #: the record that they wrote it — the same reasoning as
    #: `ProductImport.requested_by_user_id`.
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


class PromptExecution(TenantScopedBase):
    """One tenant's render-and-run attempt against a prompt.

    Written by the "test prompt rendering" path in Stage 2, which is the only
    caller that exists yet. `provider` and `is_synthetic` are copied from the
    provider's own `CompletionResult` rather than assumed, so a reader can
    tell a real generation from `StubProvider` output without cross-
    referencing configuration that may have changed since.
    """

    __tablename__ = "prompt_executions"

    __table_args__ = (
        Index("ix_prompt_executions_tenant_status", "tenant_id", "status"),
        Index("ix_prompt_executions_tenant_created", "tenant_id", "created_at"),
        Index("ix_prompt_executions_tenant_prompt", "tenant_id", "prompt_id"),
    )

    #: SET NULL rather than CASCADE: a prompt has no delete path yet, but if
    #: one is added later, removing a prompt must not erase the record of
    #: what tenants ran against it — the same reasoning as
    #: `ProductImport.product_id`.
    prompt_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("ai_prompts.id", ondelete="SET NULL"),
        nullable=True,
    )

    #: Snapshot of the prompt identity at execution time, kept even if
    #: `prompt_id` is later nulled out — the same denormalisation
    #: `ProductImport` applies to `source`/`external_id` alongside its
    #: nullable `product_id`.
    prompt_name: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[int] = mapped_column(Integer, nullable=False)

    input_variables: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    #: The fully substituted prompt text. Safe to store: it is built from
    #: template text plus tenant-supplied product-catalogue values, never a
    #: credential.
    rendered_prompt: Mapped[str] = mapped_column(Text, nullable=False)

    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    response_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Copied from `CompletionResult.is_synthetic`. Every reader of this
    #: table can tell real output from `StubProvider` output without
    #: re-deriving it from `provider` or from configuration that may have
    #: since changed.
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[PromptExecutionStatus] = mapped_column(
        Enum(
            PromptExecutionStatus,
            name="prompt_execution_status",
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    executed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


__all__ = ["AIPrompt", "PromptExecution", "PromptExecutionStatus"]
