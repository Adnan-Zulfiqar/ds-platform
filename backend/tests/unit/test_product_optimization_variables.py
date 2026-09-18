"""Unit coverage for `ProductOptimizationService._build_variables`.

A staticmethod operating on plain attributes — no session, no database, no
provider needed to prove it feeds the AI prompt plain text rather than raw
sanitized-but-still-tagged HTML.
"""

from __future__ import annotations

import pytest

from app.models.product import Product
from app.services.product_optimization import ProductOptimizationService, _keywords_for_prompt

pytestmark = pytest.mark.unit


def _product(**overrides: object) -> Product:
    product = Product(title="Realme GT Neo5 Case", brand="NoEnName_Null")
    for key, value in overrides.items():
        setattr(product, key, value)
    return product


class TestBuildVariables:
    def test_description_becomes_non_empty_plain_text_features(self) -> None:
        """The gap this stage closes: before description import existed,
        `product.description` was always `None` and every real optimisation
        ran on an empty `features` string."""
        product = _product(description="<p>Durable canvas case with camera protection.</p>")
        variables = ProductOptimizationService._build_variables(product, tone="professional")

        assert variables["features"] != ""
        assert "Durable canvas case with camera protection." in variables["features"]

    def test_features_contains_no_html_tags(self) -> None:
        """`description` is sanitized HTML, not plain text -- feeding it to a
        prompt verbatim would put literal `<p>`/`<img>` markup into model
        input."""
        product = _product(description='<p>Great fit.</p><img src="https://x/a.jpg" alt="photo"/>')
        variables = ProductOptimizationService._build_variables(product, tone="professional")

        assert "<" not in variables["features"]
        assert ">" not in variables["features"]
        assert "Great fit." in variables["features"]

    def test_a_product_with_no_description_has_empty_features(self) -> None:
        """Still true today for a product whose supplier sent none at all --
        distinct from the old bug, where *every* product had empty features
        regardless of what the supplier actually sent."""
        product = _product(description=None)
        variables = ProductOptimizationService._build_variables(product, tone="professional")

        assert variables["features"] == ""

    def test_other_variables_are_unaffected(self) -> None:
        product = _product(description="<p>x</p>", category_name="Phone Cases", brand="Acme")
        variables = ProductOptimizationService._build_variables(product, tone="playful")

        assert variables["product_title"] == "Realme GT Neo5 Case"
        assert variables["category"] == "Phone Cases"
        assert variables["brand"] == "Acme"
        assert variables["tone"] == "playful"


class TestKeywordsVariable:
    """Phase 9 stage 4: the `{{keywords}}` input to `seo_optimizer`.

    Every case proves the value comes from something the merchant already
    recorded on the product — the builder never invents a keyword.
    """

    def test_keywords_is_always_present(self) -> None:
        """A product with nothing recorded still supplies the variable, so
        `seo_optimizer` renders rather than raising
        `MissingPromptVariablesError` — that error is reserved for a
        template variable the caller genuinely forgot."""
        product = _product(search_topics=None, tags=None, meta_keywords=None)
        variables = ProductOptimizationService._build_variables(product, tone="professional")

        assert "keywords" in variables
        assert isinstance(variables["keywords"], str)

    def test_search_topics_win_over_every_other_source(self) -> None:
        product = _product(
            search_topics=["canvas case", "camera protection"],
            tags=["realme", "case"],
            meta_keywords="legacy, keywords",
        )
        variables = ProductOptimizationService._build_variables(product, tone="professional")

        assert variables["keywords"] == "canvas case, camera protection"

    def test_tags_are_used_when_search_topics_are_empty(self) -> None:
        product = _product(search_topics=[], tags=["realme", "case"], meta_keywords="legacy")
        variables = ProductOptimizationService._build_variables(product, tone="professional")

        assert variables["keywords"] == "realme, case"

    def test_meta_keywords_are_used_when_both_lists_are_empty(self) -> None:
        product = _product(search_topics=[], tags=[], meta_keywords="  legacy, keywords  ")
        variables = ProductOptimizationService._build_variables(product, tone="professional")

        assert variables["keywords"] == "legacy, keywords"

    def test_falls_back_to_an_empty_string_not_a_fabricated_keyword(self) -> None:
        """No source, no keyword. Filling the gap from the title or category
        would be the builder inventing input the merchant never gave."""
        product = _product(search_topics=[], tags=[], meta_keywords="   ")
        variables = ProductOptimizationService._build_variables(product, tone="professional")

        assert variables["keywords"] == ""

    def test_blank_list_entries_do_not_count_as_a_source(self) -> None:
        """A list holding only whitespace is treated as empty and falls
        through, rather than producing a keywords value of `", "`."""
        product = _product(search_topics=["", "  "], tags=["  realme  "], meta_keywords=None)
        variables = ProductOptimizationService._build_variables(product, tone="professional")

        assert variables["keywords"] == "realme"


class TestStage4PromptRenderingIsPinned:
    """Phase 9 stage 5 regression pins (docs/PHASE_9_STAGE_5_PLAN.md §5.2).

    Stage 5 parses merchant keyword terms for scoring in its own private
    helper. Stage 4's `_keywords_for_prompt` must keep producing exactly
    what it produced when it was accepted, because that string is rendered
    into the `seo_optimizer` prompt and hashed into every recorded
    execution. These assertions passed on `develop` before any stage 5
    code existed; they must pass unchanged afterwards.
    """

    def test_meta_keywords_are_passed_through_unsplit_and_unnormalised(self) -> None:
        product = _product(search_topics=None, tags=None, meta_keywords="a,b")
        assert _keywords_for_prompt(product) == "a,b"

    def test_duplicate_list_entries_are_not_collapsed(self) -> None:
        product = _product(search_topics=["x", "x"], tags=None, meta_keywords=None)
        assert _keywords_for_prompt(product) == "x, x"

    def test_build_variables_shape_is_exactly_the_stage_4_dict(self) -> None:
        product = _product(
            description="<p>Durable canvas.</p>",
            category_name="Phone Cases",
            brand="Acme",
            search_topics=None,
            tags=None,
            meta_keywords="a,b",
        )
        assert ProductOptimizationService._build_variables(product, tone="playful") == {
            "product_title": "Realme GT Neo5 Case",
            "category": "Phone Cases",
            "brand": "Acme",
            "features": "Durable canvas.",
            "tone": "playful",
            "keywords": "a,b",
        }
