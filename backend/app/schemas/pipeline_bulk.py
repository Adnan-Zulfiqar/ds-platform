"""HTTP schemas for bulk pipeline preview runs. Stage 8 schemas stay untouched."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.models.pipeline_bulk import PipelineBulkItemState, PipelineBulkRunStatus
from app.schemas.base import CamelCaseModel


class PipelineBulkRunCreateRequest(CamelCaseModel):
    product_ids: list[uuid.UUID] = Field(min_length=1)
    idempotency_key: str = Field(min_length=1, max_length=128)
    tone: str = Field(default="professional", min_length=1, max_length=64)
    store_id: uuid.UUID | None = None


class PipelineBulkRunRead(CamelCaseModel):
    id: uuid.UUID
    status: PipelineBulkRunStatus
    idempotency_key: str
    tone: str
    store_id: uuid.UUID | None
    heartbeat_at: datetime | None
    recovery_count: int
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    total_count: int
    processed_count: int
    succeeded_count: int
    failed_count: int
    skipped_count: int
    missing_count: int
    failure_reason: str | None


class PipelineBulkRunItemRead(CamelCaseModel):
    submitted_product_id: uuid.UUID
    product_id: uuid.UUID | None
    state: PipelineBulkItemState
    candidate_version_id: uuid.UUID | None
    error_code: str | None
    error_message: str | None
    attempt_count: int
    finished_at: datetime | None


__all__ = [
    "PipelineBulkRunCreateRequest",
    "PipelineBulkRunItemRead",
    "PipelineBulkRunRead",
]
