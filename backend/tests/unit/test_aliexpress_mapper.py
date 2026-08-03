"""Tests for the AliExpress-to-catalogue mapper.

Driven by the same real captured payload as the contract tests, so what is
asserted here is what the supplier actually sends.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.integrations.aliexpress.catalog import (
    FeedProduct,
    ProductDetail,
    parse_feed_products,
    parse_product_detail,
)
from app.integrations.aliexpress.mapper import (
    map_feed_product,
    map_images,
    map_product,
    map_variants,
)
from app.models.product import ProductSource

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "aliexpress"


def load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def product() -> ProductDetail:
    detail = parse_product_detail(load("product.json"))
    assert detail is not None
    return detail


class TestProductMapping:
    def test_maps_the_real_payload_to_column_values(self, product: ProductDetail) -> None:
        values = map_product(product)

        assert values["source"] is ProductSource.ALIEXPRESS
        assert values["external_id"] == "3256806389000685"
        assert "Realme GT Neo5" in values["supplier_title"]
        assert values["currency"] == "USD"

    def test_keeps_both_supplier_identifiers(self, product: ProductDetail) -> None:
        """They are not interchangeable, so discarding either loses information."""
        values = map_product(product)

        assert values["external_id"] == "3256806389000685"
        assert values["external_canonical_id"] == "1005006575315437"

    def test_price_range_comes_from_the_variants(self, product: ProductDetail) -> None:
        values = map_product(product)

        assert isinstance(values["cost_price_min"], Decimal)
        assert values["cost_price_min"] <= values["cost_price_max"]

    def test_stock_is_summed_across_variants(self, product: ProductDetail) -> None:
        values = map_product(product)
        assert values["stock_quantity"] == sum(sku.sku_available_stock or 0 for sku in product.skus)

    def test_supplier_details_are_carried(self, product: ProductDetail) -> None:
        values = map_product(product)

        assert values["supplier_name"] == "szsxjtkj Store"
        assert values["supplier_id"] == "1101460887"
        assert values["rating"] == Decimal("4.9")
        assert values["review_count"] == 40
        assert values["order_count"] == 112

    def test_brand_is_lifted_from_the_attributes(self, product: ProductDetail) -> None:
        """Brand is an attribute row, not a top-level field."""
        assert map_product(product)["supplier_brand"] == "NoEnName_Null"

    def test_supplier_description_is_sanitized_html_from_the_real_fixture(
        self, product: ProductDetail
    ) -> None:
        values = map_product(product)
        assert values["supplier_description"] is not None
        assert "<img" in values["supplier_description"]
        # The real fixture's `detail` wraps everything in
        # `<div class="detailmodule_html">...` -- div is not in the
        # sanitizer's allowlist and must not survive, even though its
        # content (the image) does.
        assert "<div" not in values["supplier_description"]

    def test_synced_fields_are_not_set_by_the_mapper_onto_their_editable_twin(
        self, product: ProductDetail
    ) -> None:
        """`title`/`brand`/`description` (the merchant-editable fields) are a
        sync-policy decision for `ProductImportService._upsert`, not the
        mapper -- the same reasoning that already keeps `status` out of this
        function."""
        values = map_product(product)
        assert "title" not in values
        assert "brand" not in values
        assert "description" not in values

    def test_a_product_with_no_description_maps_to_none(self) -> None:
        detail = ProductDetail.model_validate({"ae_item_base_info_dto": {"product_id": 1}})
        assert map_product(detail)["supplier_description"] is None

    def test_status_is_not_set_by_the_mapper(self, product: ProductDetail) -> None:
        """The service decides.

        If the mapper set it, a re-sync would revert a product the tenant had
        activated back to draft — silently unpublishing their catalogue every
        time prices refreshed.
        """
        assert "status" not in map_product(product)

    def test_a_product_without_a_title_still_maps(self) -> None:
        """A placeholder is visible in the UI; a failed import is not."""
        detail = ProductDetail.model_validate({"ae_item_base_info_dto": {"product_id": 42}})
        values = map_product(detail)

        assert values["supplier_title"] == "Untitled product 42"
        assert values["external_id"] == "42"

    def test_an_over_long_title_is_truncated_not_rejected(self) -> None:
        """Supplier titles routinely exceed the column.

        Truncating once here beats a database error at insert time.
        """
        detail = ProductDetail.model_validate(
            {"ae_item_base_info_dto": {"product_id": 1, "subject": "x" * 900}}
        )
        assert len(map_product(detail)["supplier_title"]) == 512

    def test_an_empty_product_maps_without_raising(self) -> None:
        values = map_product(ProductDetail.model_validate({}))
        assert values["stock_quantity"] == 0
        assert values["cost_price_min"] is None


class TestVariantMapping:
    def test_every_identified_variant_is_mapped(self, product: ProductDetail) -> None:
        assert len(map_variants(product)) == 12

    def test_the_composite_key_is_preserved_verbatim(self, product: ProductDetail) -> None:
        """An order must echo this exact string back."""
        first = map_variants(product)[0]
        assert first["external_attributes"] == "10:529#Realme GT Neo5;14:771#tyjt white"

    def test_variant_prices_are_decimals(self, product: ProductDetail) -> None:
        first = map_variants(product)[0]
        assert first["cost_price"] == Decimal("3.30")
        assert isinstance(first["cost_price"], Decimal)

    def test_variant_label_is_readable(self, product: ProductDetail) -> None:
        assert map_variants(product)[0]["label"] == "Color: Beige / Material: CANVAS"

    def test_a_variant_without_an_id_is_dropped(self) -> None:
        """It cannot be named in an order, so offering it would be a dead end."""
        detail = ProductDetail.model_validate(
            {"ae_item_sku_info_dtos": {"ae_item_sku_info_d_t_o": [{"sku_price": "1.00"}]}}
        )
        assert map_variants(detail) == []


class TestImageMapping:
    def test_the_delimited_string_becomes_ordered_rows(self, product: ProductDetail) -> None:
        images = map_images(product)

        assert len(images) == 6
        assert [i["position"] for i in images] == [0, 1, 2, 3, 4, 5]

    def test_ordering_is_preserved(self, product: ProductDetail) -> None:
        """Position 0 is the image a listing leads with."""
        images = map_images(product)
        assert images[0]["url"] == product.image_urls[0]

    def test_duplicate_urls_are_removed(self) -> None:
        """A repeated URL would violate the unique constraint and fail an
        otherwise good import."""
        detail = ProductDetail.model_validate(
            {
                "ae_multimedia_info_dto": {
                    "image_urls": "https://a.example/1.jpg;https://a.example/1.jpg;https://a.example/2.jpg"
                }
            }
        )
        images = map_images(detail)

        assert len(images) == 2
        assert [i["position"] for i in images] == [0, 1]

    def test_no_images_is_not_an_error(self) -> None:
        assert map_images(ProductDetail.model_validate({})) == []


class TestFeedMapping:
    def test_a_feed_entry_maps_to_a_product(self) -> None:
        items = parse_feed_products(load("feed_working.json"))
        values = map_feed_product(items[0])

        assert values is not None
        assert values["external_id"] == items[0].identifier
        assert values["title"]
        assert isinstance(values["cost_price_min"], Decimal)

    def test_an_entry_without_an_identifier_is_skipped(self) -> None:
        """It could never be imported later, so storing it helps nobody."""
        assert map_feed_product(FeedProduct.model_validate({"product_title": "x"})) is None

    def test_feed_mapping_uses_the_feed_field_names(self) -> None:
        """`product_title`, not `subject`. Mapping one onto the other would hide
        that a feed entry carries less than a detail response."""
        items = parse_feed_products(load("feed_working.json"))
        values = map_feed_product(items[0])

        assert values is not None
        assert values["title"] == items[0].product_title
