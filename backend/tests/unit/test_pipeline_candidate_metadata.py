"""Unit coverage for Stage 7 pipeline-candidate metadata parsing.

No session, no database, no provider: the parser is a fail-closed gate on
JSONB shapes, and `True == 1` is the whole reason it exists.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.core.exceptions import ValidationError
from app.services.product_optimization import (
    content_has_any_pipeline_metadata_key,
    parse_pipeline_candidate_metadata,
)

pytestmark = pytest.mark.unit

_AWARE = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


def _valid(**overrides: object) -> dict[str, Any]:
    content: dict[str, Any] = {
        "title": "Candidate",
        "pipelineCandidateVersion": 1,
        "pipelineSourceUpdatedAt": _AWARE.isoformat(),
        "isSynthetic": False,
    }
    content.update(overrides)
    return content


def _reason(exc: ValidationError) -> str | None:
    reason = exc.details.get("reason")
    return reason if isinstance(reason, str) else None


class TestParsePipelineCandidateMetadata:
    def test_valid_row(self) -> None:
        metadata = parse_pipeline_candidate_metadata(_valid(isSynthetic=True))
        assert metadata.source_updated_at == _AWARE
        assert metadata.is_synthetic is True

    def test_marker_true_is_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            parse_pipeline_candidate_metadata(_valid(pipelineCandidateVersion=True))
        assert _reason(exc_info.value) == "not_a_pipeline_candidate"

    def test_marker_false_is_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            parse_pipeline_candidate_metadata(_valid(pipelineCandidateVersion=False))
        assert _reason(exc_info.value) == "not_a_pipeline_candidate"

    def test_marker_missing_is_rejected(self) -> None:
        content = _valid()
        del content["pipelineCandidateVersion"]
        with pytest.raises(ValidationError) as exc_info:
            parse_pipeline_candidate_metadata(content)
        assert _reason(exc_info.value) == "not_a_pipeline_candidate"

    def test_malformed_source_timestamp_is_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            parse_pipeline_candidate_metadata(_valid(pipelineSourceUpdatedAt="not-a-date"))
        assert _reason(exc_info.value) == "not_a_pipeline_candidate"

    def test_naive_timestamp_is_rejected(self) -> None:
        naive = datetime(2026, 9, 19, 12, 0).isoformat()
        with pytest.raises(ValidationError) as exc_info:
            parse_pipeline_candidate_metadata(_valid(pipelineSourceUpdatedAt=naive))
        assert _reason(exc_info.value) == "not_a_pipeline_candidate"

    def test_is_synthetic_missing_is_rejected(self) -> None:
        content = _valid()
        del content["isSynthetic"]
        with pytest.raises(ValidationError) as exc_info:
            parse_pipeline_candidate_metadata(content)
        assert _reason(exc_info.value) == "not_a_pipeline_candidate"

    def test_is_synthetic_string_false_is_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            parse_pipeline_candidate_metadata(_valid(isSynthetic="false"))
        assert _reason(exc_info.value) == "not_a_pipeline_candidate"

    @pytest.mark.parametrize("value", [0, 1])
    def test_is_synthetic_integers_are_rejected(self, value: int) -> None:
        with pytest.raises(ValidationError) as exc_info:
            parse_pipeline_candidate_metadata(_valid(isSynthetic=value))
        assert _reason(exc_info.value) == "not_a_pipeline_candidate"

    def test_non_mapping_content_is_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            parse_pipeline_candidate_metadata("not-json")
        assert _reason(exc_info.value) == "not_a_pipeline_candidate"


class TestContentHasAnyPipelineMetadataKey:
    def test_unmarked_legacy_content(self) -> None:
        assert content_has_any_pipeline_metadata_key({"title": "Legacy"}) is False

    def test_any_key_counts_even_when_malformed(self) -> None:
        assert content_has_any_pipeline_metadata_key({"pipelineCandidateVersion": True}) is True
        assert content_has_any_pipeline_metadata_key({"isSynthetic": "false"}) is True
