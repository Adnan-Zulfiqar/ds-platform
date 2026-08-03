"""Unit coverage for `ProductOptimizationService._build_variables`.

A staticmethod operating on plain attributes — no session, no database, no
provider needed to prove it feeds the AI prompt plain text rather than raw
sanitized-but-still-tagged HTML.
"""

from __future__ import annotations

import pytest

from app.models.product import Product
from app.services.product_optimization import ProductOptimizationService

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
