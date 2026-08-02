"""Tests for `PromptRenderer`.

Pure logic, no database, no provider — the properties that matter are that
required variables come only from the template text, and that a missing
variable fails loudly rather than sending a gap to a model.
"""

from __future__ import annotations

import pytest

from app.ai.exceptions import MissingPromptVariablesError
from app.ai.prompt_renderer import PromptRenderer

pytestmark = pytest.mark.unit


class TestExtractVariables:
    def test_finds_every_distinct_variable(self) -> None:
        template = "Title: {{product_title}}. Category: {{category}}. Tone: {{tone}}."
        assert PromptRenderer.extract_variables(template) == frozenset(
            {"product_title", "category", "tone"}
        )

    def test_a_repeated_variable_counts_once(self) -> None:
        template = "{{name}} and {{name}} again"
        assert PromptRenderer.extract_variables(template) == frozenset({"name"})

    def test_a_template_with_no_variables_is_empty(self) -> None:
        assert PromptRenderer.extract_variables("Static text only.") == frozenset()

    def test_tolerates_internal_whitespace(self) -> None:
        assert PromptRenderer.extract_variables("{{  padded  }}") == frozenset({"padded"})

    def test_ignores_braces_that_are_not_a_valid_identifier(self) -> None:
        """`{{ "literal" }}` is not `{{identifier}}` — left untouched rather
        than matched, so text that merely resembles the syntax cannot be
        mistaken for a variable reference."""
        assert PromptRenderer.extract_variables('{{ "not an identifier" }}') == frozenset()


class TestRender:
    def test_substitutes_every_variable(self) -> None:
        rendered = PromptRenderer.render(
            "Title: {{title}}. Brand: {{brand}}.",
            {"title": "Mirror lip gloss set", "brand": "Generic"},
        )
        assert rendered == "Title: Mirror lip gloss set. Brand: Generic."

    def test_extra_supplied_variables_are_ignored(self) -> None:
        """A caller may pass one shared context dict to several templates
        without trimming it per template."""
        rendered = PromptRenderer.render("{{a}}", {"a": "1", "b": "2"})
        assert rendered == "1"

    def test_missing_variable_raises_rather_than_sending_a_gap(self) -> None:
        with pytest.raises(MissingPromptVariablesError) as exc_info:
            PromptRenderer.render("{{a}} and {{b}}", {"a": "1"})
        assert exc_info.value.missing_variables == ["b"]

    def test_reports_every_missing_variable_at_once(self) -> None:
        with pytest.raises(MissingPromptVariablesError) as exc_info:
            PromptRenderer.render("{{a}} {{b}} {{c}}", {})
        assert exc_info.value.missing_variables == ["a", "b", "c"]

    def test_a_value_containing_braces_is_not_re_processed(self) -> None:
        """Single-pass substitution: a variable *value* that happens to look
        like `{{another}}` must not trigger a second round of substitution."""
        rendered = PromptRenderer.render("{{a}}", {"a": "{{b}} literally"})
        assert rendered == "{{b}} literally"

    def test_no_variables_returns_the_template_unchanged(self) -> None:
        assert PromptRenderer.render("Static text.", {}) == "Static text."

    def test_the_error_is_a_422_validation_error(self) -> None:
        with pytest.raises(MissingPromptVariablesError) as exc_info:
            PromptRenderer.render("{{a}}", {})
        assert exc_info.value.status_code == 422
        assert exc_info.value.code == "missing_prompt_variables"
