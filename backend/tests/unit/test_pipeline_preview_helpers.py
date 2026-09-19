"""Unit coverage for Stage 7 pipeline preview helpers.

Named distinctly from `tests/integration/test_product_pipeline.py` because
pytest's default prepend import mode cannot collect two files with the same
basename.
"""

from __future__ import annotations

import pytest

from app.services.product_pipeline import (
    PIPELINE_APPROVAL_TITLE_MAX,
    PIPELINE_PUBLISH_TITLE_MAX,
    _quality_baseline,
    _safe_body_html,
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


class TestSafeBodyHtml:
    def test_strips_script_and_event_handlers(self) -> None:
        raw = (
            "<p>Keep</p><script>alert(1)</script>"
            '<img src="https://cdn.example/a.jpg" onerror="alert(1)">'
            '<a href="javascript:alert(1)">x</a>'
            '<a href="data:text/html,hi">y</a>'
            "<strong>bold</strong>"
        )
        cleaned = _safe_body_html(raw)
        assert "<script>" not in cleaned
        assert "alert(1)" not in cleaned
        assert "onerror" not in cleaned
        assert "javascript:" not in cleaned
        assert "data:" not in cleaned
        assert "<p>Keep</p>" in cleaned
        assert "<strong>bold</strong>" in cleaned

    def test_non_string_description_becomes_empty(self) -> None:
        assert _safe_body_html(None) == ""
        assert _safe_body_html(123) == ""
