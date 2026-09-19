"""Unit coverage for Stage 7 pipeline preview helpers."""

from __future__ import annotations

import pytest

from app.services.product_pipeline import (
    PIPELINE_APPROVAL_TITLE_MAX,
    PIPELINE_PUBLISH_TITLE_MAX,
    _quality_baseline,
    _title_publish_error,
    _title_storage_error,
)

pytestmark = pytest.mark.unit


class TestTitleBounds:
    def test_255_is_publishable(self) -> None:
        title = "a" * PIPELINE_PUBLISH_TITLE_MAX
        assert _title_storage_error(title) is None
        assert _title_publish_error(title) is None

    def test_256_is_approvable_but_not_publishable(self) -> None:
        title = "a" * (PIPELINE_PUBLISH_TITLE_MAX + 1)
        assert _title_storage_error(title) is None
        error = _title_publish_error(title)
        assert error is not None
        assert error.code == "candidate_title_not_publishable"

    def test_over_512_is_invalid_for_storage(self) -> None:
        title = "a" * (PIPELINE_APPROVAL_TITLE_MAX + 1)
        error = _title_storage_error(title)
        assert error is not None
        assert error.code == "candidate_content_invalid"

    def test_blank_title_is_invalid(self) -> None:
        assert _title_storage_error("   ") is not None
        assert _title_storage_error(None) is not None
        assert _title_storage_error(1) is not None


class TestQualityBaseline:
    def test_keeps_version_number_and_score(self) -> None:
        baseline = _quality_baseline({"versionNumber": 1, "score": 42})
        assert baseline is not None
        assert baseline.version_number == 1
        assert baseline.score == 42

    def test_rejects_a_bare_integer(self) -> None:
        assert _quality_baseline(42) is None
