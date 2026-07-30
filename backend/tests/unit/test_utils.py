"""Tests for pure utilities."""

from __future__ import annotations

import re

import pytest
from app.utils.strings import slugify, truncate

pytestmark = pytest.mark.unit

# The CHECK constraint on tenants.slug. Every slugify result must satisfy it.
DNS_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")


class TestSlugify:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("Acme Corporation", "acme-corporation"),
            ("Café Münster GmbH", "cafe-munster-gmbh"),
            ("  --Hello, World!--  ", "hello-world"),
            ("multiple???separators", "multiple-separators"),
            ("ALLCAPS", "allcaps"),
            ("already-a-slug", "already-a-slug"),
            ("123 Numbers 456", "123-numbers-456"),
        ],
    )
    def test_produces_expected_slug(self, value: str, expected: str) -> None:
        assert slugify(value) == expected

    @pytest.mark.parametrize("value", ["", "***", "   ", "!!!"])
    def test_returns_empty_when_nothing_usable_remains(self, value: str) -> None:
        """Callers must handle this rather than assume a valid slug."""
        assert slugify(value) == ""

    def test_respects_max_length(self) -> None:
        assert len(slugify("a" * 200)) <= 63

    def test_does_not_end_in_a_hyphen_after_truncation(self) -> None:
        """Truncation must not leave a trailing hyphen.

        A trailing hyphen violates the DNS label constraint, so this would fail
        at insert time rather than here.
        """
        result = slugify("x" * 62 + " word")
        assert not result.endswith("-")

    @pytest.mark.parametrize(
        "value",
        ["Acme Corporation", "Café Münster", "a" * 200, "x" * 62 + " word", "A-B-C"],
    )
    def test_output_always_satisfies_the_database_constraint(self, value: str) -> None:
        result = slugify(value)
        assert result == "" or DNS_LABEL.match(result)


class TestTruncate:
    def test_shortens_and_appends_suffix(self) -> None:
        assert truncate("abcdefghij", 8) == "abcde..."

    def test_leaves_short_strings_untouched(self) -> None:
        assert truncate("short", 10) == "short"

    def test_result_never_exceeds_max_length(self) -> None:
        for length in range(1, 12):
            assert len(truncate("abcdefghij", length)) <= length
