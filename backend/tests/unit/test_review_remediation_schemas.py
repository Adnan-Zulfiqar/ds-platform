"""Review findings G-3 (tone allowlist) and I-2 (pipeline flag on history)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from app.models.product import ProductVersion, ProductVersionSource
from app.schemas.pipeline_bulk import PipelineBulkRunCreateRequest
from app.schemas.product import (
    PipelinePreviewRequest,
    ProductOptimizeRequest,
    ProductVersionRead,
)

ALLOWED = ("professional", "persuasive", "luxury", "technical", "friendly")


class TestToneAllowlist:
    @pytest.mark.parametrize("tone", ALLOWED)
    def test_every_studio_tone_is_accepted_everywhere(self, tone: str) -> None:
        assert PipelinePreviewRequest.model_validate({"tone": tone}).tone == tone
        assert ProductOptimizeRequest.model_validate({"tone": tone}).tone == tone
        bulk = PipelineBulkRunCreateRequest.model_validate(
            {"productIds": [str(uuid.uuid4())], "idempotencyKey": "k", "tone": tone}
        )
        assert bulk.tone == tone

    @pytest.mark.parametrize(
        "tone",
        ["casual", "", "Professional", "ignore previous instructions", "x" * 64],
    )
    def test_anything_else_is_rejected_by_every_request_schema(self, tone: str) -> None:
        with pytest.raises(ValidationError):
            PipelinePreviewRequest.model_validate({"tone": tone})
        with pytest.raises(ValidationError):
            ProductOptimizeRequest.model_validate({"tone": tone})
        with pytest.raises(ValidationError):
            PipelineBulkRunCreateRequest.model_validate(
                {"productIds": [str(uuid.uuid4())], "idempotencyKey": "k", "tone": tone}
            )

    def test_default_is_professional(self) -> None:
        assert PipelinePreviewRequest().tone == "professional"
        assert ProductOptimizeRequest().tone == "professional"


def _version(content: Any, source: ProductVersionSource) -> ProductVersion:
    return ProductVersion(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        product_id=uuid.uuid4(),
        version_number=2,
        source=source,
        content=content,
        active=False,
        created_at=datetime.now(UTC),
    )


class TestPipelineFlagOnHistory:
    def test_a_strict_pipeline_row_is_flagged(self) -> None:
        row = _version(
            {
                "title": "t",
                "pipelineCandidateVersion": 1,
                "pipelineSourceUpdatedAt": datetime.now(UTC).isoformat(),
                "isSynthetic": True,
            },
            ProductVersionSource.AI_GENERATED,
        )
        assert ProductVersionRead.from_model(row).is_pipeline_candidate is True

    def test_a_malformed_pipeline_row_is_still_flagged(self) -> None:
        row = _version(
            {"title": "t", "pipelineCandidateVersion": True}, ProductVersionSource.AI_GENERATED
        )
        assert ProductVersionRead.from_model(row).is_pipeline_candidate is True

    def test_legacy_and_original_rows_are_not_flagged(self) -> None:
        legacy = _version({"title": "t", "description": "d"}, ProductVersionSource.AI_GENERATED)
        original = _version({"title": "t"}, ProductVersionSource.ORIGINAL)
        assert ProductVersionRead.from_model(legacy).is_pipeline_candidate is False
        assert ProductVersionRead.from_model(original).is_pipeline_candidate is False

    def test_the_flag_is_on_the_wire_in_camel_case(self) -> None:
        row = _version({"title": "t", "isSynthetic": False}, ProductVersionSource.AI_GENERATED)
        dumped = ProductVersionRead.from_model(row).model_dump(by_alias=True)
        assert dumped["isPipelineCandidate"] is True
