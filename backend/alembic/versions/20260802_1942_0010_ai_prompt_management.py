"""AI prompt management: ai_prompts and prompt_executions, with seeded defaults.

Revision ID: 0010
Revises: 0009
Created: 2026-08-02 19:42:53+00:00

Two tables. ``ai_prompts`` is platform reference data (no ``tenant_id``),
mirroring how ``roles`` was seeded in migration ``0002``: the application
cannot function without the five default prompts, so they are part of the
schema contract rather than an application-startup seed that would race
between replicas. ``prompt_executions`` is tenant-scoped, patterned on
``product_imports``.

``uq_ai_prompts_name_active`` is a partial unique index (``WHERE active``)
rather than an ordinary unique constraint: multiple inactive versions may
share a ``name``, but at most one of them may be active at a time, and only
a database constraint can guarantee that against a concurrent activation
race.

Autogenerate also proposed rewriting unique constraints into unique indexes
on ``email_verification_tokens``, ``refresh_tokens``, ``roles``,
``shopify_connections``, and ``tenants``. Removed by hand, matching the
precedent set in migration ``0004``: these are drift between the live schema
and how earlier migrations declared those constraints, not a change this
migration needs, and rewriting five unrelated tables' constraints is not
something to do as a side effect of adding a prompt library.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Deterministic per prompt name, the same reasoning as migration 0002's role
# ids: a given default prompt has the same id in every environment, which
# makes fixtures portable and lets a support query compare ids across
# environments without a lookup. Distinct from `_ROLE_NAMESPACE` — different
# entity kind, different namespace.
_PROMPT_NAMESPACE = uuid.UUID("2f6e9c1a-8b3d-4e5a-9c7f-1a2b3c4d5e6f")

_DEFAULT_PROMPTS: list[dict[str, str | None]] = [
    {
        "name": "product_title_generator",
        "description": "Rewrite a supplier product title into a clear, sellable listing title.",
        "template": (
            "Rewrite the following product title for an online store listing. "
            "Keep it concise, remove keyword stuffing, and do not invent "
            "features not mentioned.\n\n"
            "Original title: {{product_title}}\n"
            "Category: {{category}}\n"
            "Brand: {{brand}}\n"
            "Tone: {{tone}}"
        ),
        "target_model": None,
    },
    {
        "name": "product_description_generator",
        "description": "Write a product description from supplier-provided facts.",
        "template": (
            "Write a product description for an online store listing using only "
            "the facts below. Do not invent claims, certifications, or "
            "specifications that are not listed.\n\n"
            "Title: {{product_title}}\n"
            "Category: {{category}}\n"
            "Key features: {{features}}\n"
            "Brand: {{brand}}\n"
            "Tone: {{tone}}"
        ),
        "target_model": None,
    },
    {
        "name": "seo_optimizer",
        "description": "Suggest an SEO title, meta description, and keywords for a listing.",
        "template": (
            "Suggest an SEO-optimised page title (under 60 characters), a meta "
            "description (under 155 characters), and up to 10 search keywords "
            "for the product below. Base suggestions only on the information "
            "given.\n\n"
            "Title: {{product_title}}\n"
            "Category: {{category}}\n"
            "Keywords to consider: {{keywords}}"
        ),
        "target_model": None,
    },
    {
        "name": "quality_scorer",
        "description": (
            "Reserved. Phase 9 stage 5's quality score is deliberately "
            "model-free (see docs/PHASE_9_PLAN.md) and does not call this "
            "prompt. Seeded so `quality_scorer` is a valid name in the same "
            "library as the generation prompts, should a future stage add a "
            "model-assisted explanation of a deterministic score."
        ),
        "template": (
            "Given the product listing below, explain in one sentence what most "
            "limits its quality score.\n\n"
            "Title: {{product_title}}\n"
            "Key features: {{features}}"
        ),
        "target_model": None,
    },
    {
        "name": "image_analyzer",
        "description": "Generate a caption and alt text for a product image.",
        "template": (
            "Describe the product image at the URL below in one sentence "
            "suitable as accessible alt text. Do not describe anything not "
            "visible in the image.\n\n"
            "Image URL: {{image_url}}\n"
            "Product title: {{product_title}}"
        ),
        "target_model": None,
    },
]


def upgrade() -> None:
    ai_prompts_table = op.create_table(
        "ai_prompts",
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=True),
        sa.Column("template", sa.Text(), nullable=False),
        sa.Column("target_model", sa.String(length=128), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_ai_prompts_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_prompts")),
        sa.UniqueConstraint("name", "version", name="uq_ai_prompts_name_version"),
    )
    op.create_index(op.f("ix_ai_prompts_created_at"), "ai_prompts", ["created_at"], unique=False)
    op.create_index(op.f("ix_ai_prompts_name"), "ai_prompts", ["name"], unique=False)
    op.create_index("ix_ai_prompts_name_version", "ai_prompts", ["name", "version"], unique=False)
    op.create_index(
        "uq_ai_prompts_name_active",
        "ai_prompts",
        ["name"],
        unique=True,
        postgresql_where=sa.text("active"),
    )

    op.create_table(
        "prompt_executions",
        sa.Column("prompt_id", sa.UUID(), nullable=True),
        sa.Column("prompt_name", sa.String(length=128), nullable=False),
        sa.Column("prompt_version", sa.Integer(), nullable=False),
        sa.Column("input_variables", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rendered_prompt", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("response_text", sa.Text(), nullable=True),
        sa.Column("is_synthetic", sa.Boolean(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("succeeded", "failed", name="prompt_execution_status"),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=2048), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("executed_by_user_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["executed_by_user_id"],
            ["users.id"],
            name=op.f("fk_prompt_executions_executed_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["prompt_id"],
            ["ai_prompts.id"],
            name=op.f("fk_prompt_executions_prompt_id_ai_prompts"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_prompt_executions_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prompt_executions")),
    )
    op.create_index(
        op.f("ix_prompt_executions_created_at"), "prompt_executions", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_prompt_executions_deleted_at"), "prompt_executions", ["deleted_at"], unique=False
    )
    op.create_index(
        "ix_prompt_executions_tenant_created",
        "prompt_executions",
        ["tenant_id", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_prompt_executions_tenant_id"), "prompt_executions", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_prompt_executions_tenant_prompt",
        "prompt_executions",
        ["tenant_id", "prompt_id"],
        unique=False,
    )
    op.create_index(
        "ix_prompt_executions_tenant_status",
        "prompt_executions",
        ["tenant_id", "status"],
        unique=False,
    )

    # Seeded in the migration rather than by application startup code, for
    # the same reason migration 0002 seeds roles: the schema contract
    # includes these five rows, and a startup seed would race between
    # replicas. Each starts at version 1 and active.
    op.bulk_insert(
        ai_prompts_table,
        [
            {
                "id": uuid.uuid5(_PROMPT_NAMESPACE, prompt["name"]),  # type: ignore[arg-type]
                "name": prompt["name"],
                "description": prompt["description"],
                "template": prompt["template"],
                "target_model": prompt["target_model"],
                "version": 1,
                "active": True,
                "created_by_user_id": None,
            }
            for prompt in _DEFAULT_PROMPTS
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_prompt_executions_tenant_status", table_name="prompt_executions")
    op.drop_index("ix_prompt_executions_tenant_prompt", table_name="prompt_executions")
    op.drop_index(op.f("ix_prompt_executions_tenant_id"), table_name="prompt_executions")
    op.drop_index("ix_prompt_executions_tenant_created", table_name="prompt_executions")
    op.drop_index(op.f("ix_prompt_executions_deleted_at"), table_name="prompt_executions")
    op.drop_index(op.f("ix_prompt_executions_created_at"), table_name="prompt_executions")
    op.drop_table("prompt_executions")

    op.drop_index(
        "uq_ai_prompts_name_active", table_name="ai_prompts", postgresql_where=sa.text("active")
    )
    op.drop_index("ix_ai_prompts_name_version", table_name="ai_prompts")
    op.drop_index(op.f("ix_ai_prompts_name"), table_name="ai_prompts")
    op.drop_index(op.f("ix_ai_prompts_created_at"), table_name="ai_prompts")
    op.drop_table("ai_prompts")

    # Postgres enum types outlive the tables that use them. Without this the
    # downgrade "succeeds" and the next upgrade fails with "type
    # prompt_execution_status already exists" — a broken downgrade that only
    # reveals itself on the way back up. See migration 0004 for the same fix
    # applied to product_source/product_status/import_status.
    sa.Enum(name="prompt_execution_status").drop(op.get_bind(), checkfirst=True)
