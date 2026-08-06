"""SEO quality score — transparent, advisory, no meta-keywords export."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.seo_score import score_product_seo

pytestmark = pytest.mark.unit


def _product(**kwargs: object) -> SimpleNamespace:
    defaults = {
        "title": "USB-C Hub Multiport Adapter",
        "seo_title": "USB-C Hub for Laptops | Multiport Adapter",
        "seo_description": (
            "Connect HDMI, USB, and charging with a compact USB-C hub built for "
            "travel and desk setups."
        ),
        "description": "<p>" + ("Useful product details. " * 40) + "</p>",
        "supplier_description": "<p>Supplier filler text only.</p>",
        "slug": "usb-c-hub-multiport",
        "search_topics": ["usb-c hub", "laptop adapter"],
        "seo_planning": {
            "primary_topic": "USB-C hub",
            "primary_search_intent": "transactional",
        },
        "images": [SimpleNamespace(alt_text="USB-C hub", deleted_at=None)],
        "variants": [
            SimpleNamespace(
                is_enabled=True,
                sell_price="19.99",
                list_price="14.00",
                merchant_sku="HUB-1",
                external_variant_id="1",
            )
        ],
        "sell_price": "19.99",
        "currency": "USD",
        "status": SimpleNamespace(value="draft"),
        "requires_shipping": True,
        "package_weight_kg": "0.2",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class TestSeoScore:
    def test_strong_listing_scores_well(self) -> None:
        result = score_product_seo(_product())  # type: ignore[arg-type]
        assert result.score >= 70
        assert result.status in {"Good", "Excellent"}

    def test_keyword_stuffing_is_flagged(self) -> None:
        result = score_product_seo(
            _product(  # type: ignore[arg-type]
                seo_title="hub hub hub hub hub hub adapter hub hub"
            )
        )
        assert any("stuffing" in warning.lower() for warning in result.warnings)

    def test_meta_keywords_never_exported_flag(self) -> None:
        from app.services.seo_score import seo_score_dict

        data = seo_score_dict(_product())  # type: ignore[arg-type]
        assert data["meta_keywords_exported"] is False
