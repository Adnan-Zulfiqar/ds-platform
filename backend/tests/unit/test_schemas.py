"""Tests for shared schemas: pagination, error envelopes, serialisation."""

from __future__ import annotations

import pytest
from app.core.config import settings
from app.schemas.common import ErrorResponse, Page, PageMeta, PaginationParams
from app.schemas.user import UserCreate
from pydantic import ValidationError as PydanticValidationError

pytestmark = pytest.mark.unit


class TestPagination:
    def test_offset_is_derived_from_page_and_size(self) -> None:
        assert PaginationParams(page=1, size=25).offset == 0
        assert PaginationParams(page=3, size=25).offset == 50

    def test_size_is_clamped_rather_than_rejected(self) -> None:
        """An oversized page returns fewer rows instead of a 422."""
        params = PaginationParams(page=1, size=settings.max_page_size + 1000)
        assert params.size == settings.max_page_size

    def test_page_below_one_is_rejected(self) -> None:
        with pytest.raises(PydanticValidationError):
            PaginationParams(page=0, size=10)

    @pytest.mark.parametrize(
        ("total", "size", "expected_pages"),
        [(0, 25, 0), (1, 25, 1), (25, 25, 1), (26, 25, 2), (100, 10, 10)],
    )
    def test_total_pages_rounds_up(self, total: int, size: int, expected_pages: int) -> None:
        assert PageMeta.build(page=1, size=size, total_items=total).total_pages == expected_pages

    def test_navigation_flags_on_a_middle_page(self) -> None:
        meta = PageMeta.build(page=2, size=10, total_items=100)
        assert meta.has_next is True
        assert meta.has_previous is True

    def test_navigation_flags_on_an_empty_result(self) -> None:
        meta = PageMeta.build(page=1, size=10, total_items=0)
        assert meta.has_next is False
        assert meta.has_previous is False

    def test_page_build_wraps_items_with_metadata(self) -> None:
        page = Page[str].build(items=["a", "b"], page=1, size=2, total_items=5)
        assert list(page.items) == ["a", "b"]
        assert page.meta.total_pages == 3


class TestSerialisation:
    def test_responses_serialise_to_camel_case(self) -> None:
        """The API speaks camelCase to its TypeScript consumer."""
        payload = ErrorResponse(code="not_found", message="Nope").model_dump(
            mode="json", by_alias=True
        )
        assert "requestId" in payload
        assert "request_id" not in payload

    def test_unknown_fields_are_rejected(self) -> None:
        """A typo'd field must 422 rather than being silently dropped."""
        with pytest.raises(PydanticValidationError):
            UserCreate(email="a@example.com", nonexistent_field="x")  # type: ignore[call-arg]

    def test_invalid_email_is_rejected(self) -> None:
        with pytest.raises(PydanticValidationError):
            UserCreate(email="not-an-email")

    def test_user_create_has_no_tenant_field(self) -> None:
        """Tenant must come from context, never from the request body.

        Accepting it here would let a caller create a user inside another
        tenant.
        """
        assert "tenant_id" not in UserCreate.model_fields
