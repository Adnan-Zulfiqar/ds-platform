"""Contract tests for the AliExpress catalogue parsers.

**These parse real captured payloads**, committed under
``tests/fixtures/aliexpress/``. That is the point: Phase 3 closed with M10
recording that no response schema had ever seen a real payload, and every
surprise pinned below was found by capturing one rather than by reading
documentation.

Each trap gets its own named test, because when AliExpress changes the shape the
failure should say which assumption broke.
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
    Sku,
    parse_categories,
    parse_feed_names,
    parse_feed_products,
    parse_product_detail,
    product_detail_envelope_status,
    require_usable_product_detail,
    to_decimal,
    to_int,
)
from app.integrations.aliexpress.exceptions import (
    AliExpressProductUnavailableError,
    AliExpressShipToProhibitedError,
)

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "aliexpress"


def load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def product_payload() -> dict[str, Any]:
    return load("product.json")


@pytest.fixture(scope="module")
def product(product_payload: dict[str, Any]) -> ProductDetail:
    detail = parse_product_detail(product_payload)
    assert detail is not None
    return detail


class TestFixturesAreReal:
    """Guards the premise of every other test in this file."""

    def test_the_captured_payloads_exist(self) -> None:
        for name in ("product.json", "feed_working.json", "category.json", "feednames.json"):
            assert (FIXTURES / name).exists(), f"{name} missing — contract tests are meaningless"

    def test_the_product_payload_is_a_real_response(self, product_payload: dict[str, Any]) -> None:
        body = product_payload["aliexpress_ds_product_get_response"]
        assert body["rsp_code"] == 200
        assert body["rsp_msg"] == "Call succeeds"
        assert "request_id" in body

    def test_no_credential_is_present_in_a_fixture(self) -> None:
        """Fixtures are committed; a captured token would be a live leak."""
        for path in FIXTURES.glob("*.json"):
            blob = path.read_text(encoding="utf-8")
            assert "access_token" not in blob
            assert "refresh_token" not in blob
            assert "app_secret" not in blob


class TestProductDetail:
    def test_parses_the_real_payload(self, product: ProductDetail) -> None:
        assert product.product_id == "3256806389000685"
        assert product.base.title is not None
        assert "Realme GT Neo5" in product.base.title

    def test_product_id_is_returned_as_a_string(self, product: ProductDetail) -> None:
        """It arrives as an int but must be sent back as a string.

        Leaving it an int meant every call site had to remember to convert, and
        one that forgot would fail at the gateway rather than here.
        """
        assert isinstance(product.product_id, str)

    def test_category_and_currency_are_read(self, product: ProductDetail) -> None:
        assert product.base.category_id == 380230
        assert product.base.currency_code == "USD"

    def test_listing_status_is_interpreted(self, product: ProductDetail) -> None:
        """`onSelling` is the only value that means buyable."""
        assert product.base.product_status_type == "onSelling"
        assert product.base.is_active is True

    def test_string_counts_are_converted_to_integers(self, product: ProductDetail) -> None:
        """`sales_count` and `evaluation_count` arrive as strings."""
        assert product.base.sales_count == "112"
        assert product.base.orders == 112
        assert product.base.reviews == 40

    def test_rating_is_a_decimal_not_a_float(self, product: ProductDetail) -> None:
        assert product.base.rating == Decimal("4.9")
        assert isinstance(product.base.rating, Decimal)

    def test_store_information_is_read(self, product: ProductDetail) -> None:
        store = product.ae_store_info
        assert store is not None
        assert store.store_id == 1101460887
        assert store.store_name == "szsxjtkj Store"
        assert store.store_country_code == "CN"

    def test_package_weight_is_a_decimal(self, product: ProductDetail) -> None:
        pkg = product.package_info_dto
        assert pkg is not None
        assert pkg.weight_kg == Decimal("0.040")

    def test_attributes_are_unwrapped(self, product: ProductDetail) -> None:
        """`ae_item_properties.ae_item_property` — a wrapper around a list."""
        attrs = product.attributes
        assert len(attrs) == 10
        assert any(a.attr_name == "Brand Name" for a in attrs)


class TestImageParsing:
    """The single most surprising field in the payload."""

    def test_images_are_a_delimited_string_not_an_array(
        self, product_payload: dict[str, Any]
    ) -> None:
        raw = product_payload["aliexpress_ds_product_get_response"]["result"][
            "ae_multimedia_info_dto"
        ]["image_urls"]
        assert isinstance(raw, str), "if this becomes a list, the parser must change"
        assert ";" in raw

    def test_the_delimited_string_is_split_into_urls(self, product: ProductDetail) -> None:
        urls = product.image_urls
        assert len(urls) == 6
        assert all(u.startswith("https://") for u in urls)
        assert not any(";" in u for u in urls)

    def test_missing_images_yield_an_empty_list(self) -> None:
        detail = ProductDetail.model_validate({})
        assert detail.image_urls == []


class TestSkuParsing:
    def test_all_variants_are_parsed(self, product: ProductDetail) -> None:
        assert len(product.skus) == 12

    def test_sku_identity_and_stock(self, product: ProductDetail) -> None:
        sku = product.skus[0]
        assert sku.sku_id == "12000037711871337"
        assert sku.sku_available_stock == 2994

    def test_sku_attr_is_preserved_verbatim(self, product: ProductDetail) -> None:
        """An order must echo this exact composite key back.

        Rebuilding it from the parsed properties would be a guess, and a wrong
        guess buys the wrong variant.
        """
        sku = product.skus[0]
        assert sku.sku_attr == "10:529#Realme GT Neo5;14:771#tyjt white"

    def test_prices_are_decimals_parsed_from_strings(self, product: ProductDetail) -> None:
        sku = product.skus[0]
        assert sku.sku_price == "3.30"
        assert sku.sale_price == Decimal("3.30")
        assert isinstance(sku.sale_price, Decimal)

    def test_sale_price_falls_back_to_list_price(self) -> None:
        sku = Sku.model_validate({"sku_price": "9.99"})
        assert sku.sale_price == Decimal("9.99")

    def test_variant_properties_are_double_unwrapped(self, product: ProductDetail) -> None:
        """`ae_sku_property_dtos.ae_sku_property_d_t_o` — two levels."""
        props = product.skus[0].properties
        assert len(props) == 2
        assert {p.sku_property_name for p in props} == {"Color", "Material"}

    def test_variant_label_is_human_readable(self, product: ProductDetail) -> None:
        assert product.skus[0].variant_label == "Color: Beige / Material: CANVAS"

    def test_variant_image_is_found_on_a_property(self, product: ProductDetail) -> None:
        assert product.skus[0].image_url is not None
        assert product.skus[0].image_url.startswith("https://")

    def test_price_range_spans_the_variants(self, product: ProductDetail) -> None:
        low, high = product.price_range
        assert low is not None and high is not None
        assert low <= high

    def test_total_stock_sums_the_variants(self, product: ProductDetail) -> None:
        assert product.total_stock >= 2994


class TestIdConversion:
    def test_the_queried_id_differs_from_the_canonical_id(self, product: ProductDetail) -> None:
        """Undocumented, and it matters: they are not interchangeable."""
        converter = product.product_id_converter_result
        assert converter is not None
        assert converter.main_product_id == 1005006575315437
        assert product.base.product_id == 3256806389000685

    def test_sub_product_id_is_json_inside_a_string(self, product: ProductDetail) -> None:
        """A JSON country map delivered as a string, not as an object."""
        converter = product.product_id_converter_result
        assert converter is not None
        assert converter.sub_product_id is not None
        decoded = json.loads(converter.sub_product_id)
        assert decoded == {"US": 3256806389000685}


class TestDescriptionParsing:
    """``detail``/``mobile_detail`` — the product-editor stage 1 gap.

    Both fields were previously undeclared on `ItemBaseInfo`, so nothing ever
    read them even though the real gateway sends both on every
    `product.get` call — confirmed here against the same committed fixture
    the rest of this file already trusts.
    """

    def test_detail_is_present_on_the_real_fixture(self, product: ProductDetail) -> None:
        assert product.base.detail is not None
        assert "<" in product.base.detail

    def test_mobile_detail_is_present_on_the_real_fixture(self, product: ProductDetail) -> None:
        assert product.base.mobile_detail is not None
        parsed = json.loads(product.base.mobile_detail)
        assert "moduleList" in parsed

    def test_description_html_prefers_detail(self, product: ProductDetail) -> None:
        assert product.base.description_html == product.base.detail

    def test_description_html_falls_back_to_mobile_detail_when_detail_is_absent(self) -> None:
        detail = ProductDetail.model_validate(
            {
                "ae_item_base_info_dto": {
                    "product_id": 1,
                    "mobile_detail": json.dumps(
                        {
                            "moduleList": [
                                {"type": "text", "data": {"text": "Soft and durable."}},
                                {"type": "image", "data": {"url": "https://ae01.example/a.jpg"}},
                            ]
                        }
                    ),
                }
            }
        )
        html = detail.base.description_html
        assert html is not None
        assert "<p>Soft and durable.</p>" in html
        assert '<img src="https://ae01.example/a.jpg"/>' in html

    def test_description_html_is_none_when_neither_field_is_present(self) -> None:
        detail = ProductDetail.model_validate({"ae_item_base_info_dto": {"product_id": 1}})
        assert detail.base.description_html is None

    def test_mobile_detail_fallback_ignores_non_text_non_image_modules(self) -> None:
        detail = ProductDetail.model_validate(
            {
                "ae_item_base_info_dto": {
                    "product_id": 1,
                    "mobile_detail": json.dumps(
                        {"moduleList": [{"type": "video", "data": {"url": "https://x/v.mp4"}}]}
                    ),
                }
            }
        )
        assert detail.base.description_html is None

    def test_mobile_detail_fallback_escapes_text_content(self) -> None:
        """A module's `text` is still untrusted supplier content — even the
        fallback path must not let it inject markup before sanitisation ever
        runs."""
        detail = ProductDetail.model_validate(
            {
                "ae_item_base_info_dto": {
                    "product_id": 1,
                    "mobile_detail": json.dumps(
                        {"moduleList": [{"type": "text", "data": {"text": "<script>x</script>"}}]}
                    ),
                }
            }
        )
        html = detail.base.description_html
        assert html is not None
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    @pytest.mark.parametrize(
        "raw",
        [
            "not json at all",
            "[]",
            "{}",
            json.dumps({"moduleList": "not-a-list"}),
            json.dumps({"moduleList": [1, 2, 3]}),
            json.dumps({"moduleList": [{"type": "text", "data": "not-a-dict"}]}),
        ],
    )
    def test_malformed_mobile_detail_never_raises(self, raw: str) -> None:
        """A malformed fallback field must not fail an otherwise-good import —
        the same tolerance every other field in this module already gets."""
        detail = ProductDetail.model_validate(
            {"ae_item_base_info_dto": {"product_id": 1, "mobile_detail": raw}}
        )
        assert detail.base.description_html is None


class TestFeedParsing:
    def test_feed_products_are_parsed(self) -> None:
        products = parse_feed_products(load("feed_working.json"))
        assert len(products) > 0
        assert all(isinstance(p, FeedProduct) for p in products)

    def test_feed_uses_different_field_names_from_detail(self) -> None:
        """`product_title` here, `subject` in detail. Same concept, two names."""
        first = parse_feed_products(load("feed_working.json"))[0]
        assert first.product_title is not None
        assert first.identifier is not None
        assert first.price is not None

    def test_feed_prices_are_decimals(self) -> None:
        first = parse_feed_products(load("feed_working.json"))[0]
        assert isinstance(first.price, Decimal)
        assert isinstance(first.list_price, Decimal)

    def test_an_empty_feed_is_not_an_error(self) -> None:
        """An invented feed name returns zero records rather than failing.

        This is how a wrong feed name goes unnoticed: `DS_bestseller` looked
        plausible, returned `total_record_count: 0`, and produced no error at
        all.
        """
        empty = {
            "aliexpress_ds_recommend_feed_get_response": {
                "result": {"total_record_count": 0, "products": {}},
                "rsp_code": 200,
            }
        }
        assert parse_feed_products(empty) == []

    def test_a_malformed_envelope_yields_no_products(self) -> None:
        assert parse_feed_products({}) == []
        assert parse_feed_products({"unexpected": "shape"}) == []


class TestFeedNames:
    def test_real_feed_names_are_extracted(self) -> None:
        names = parse_feed_names(load("feednames.json"))
        assert len(names) == 124
        assert all(isinstance(n, str) and n for n in names)

    def test_the_invented_feed_name_is_not_among_them(self) -> None:
        """Pins the discovery that `DS_bestseller` was never real."""
        assert "DS_bestseller" not in parse_feed_names(load("feednames.json"))


class TestCategoryParsing:
    def test_categories_are_parsed_from_a_different_envelope(self) -> None:
        """Categories nest under `resp_result.result`; products under `result`."""
        cats = parse_categories(load("category.json"))
        assert len(cats) == 549

    def test_root_categories_have_no_parent_field(self) -> None:
        cats = parse_categories(load("category.json"))
        roots = [c for c in cats if c.is_root]
        children = [c for c in cats if not c.is_root]
        assert roots and children
        assert any(c.category_name == "Food" for c in roots)

    def test_a_malformed_envelope_yields_no_categories(self) -> None:
        assert parse_categories({}) == []


class TestDefensiveParsing:
    """A catalogue of thousands will contain surprises.

    One unusable field must not fail the import of an otherwise usable product.
    """

    def test_unknown_fields_are_accepted(self) -> None:
        detail = ProductDetail.model_validate(
            {"ae_item_base_info_dto": {"product_id": 1, "brand_new_field_2027": "x"}}
        )
        assert detail.base.product_id == 1

    def test_a_missing_result_is_not_an_error(self) -> None:
        """`ITEM_ID_NOT_FOUND` returns a well-formed envelope with no result.

        Normal during a refresh: products get delisted.
        """
        payload = {
            "aliexpress_ds_product_get_response": {
                "rsp_code": 605,
                "rsp_msg": "ITEM_ID_NOT_FOUND",
            }
        }
        assert parse_product_detail(payload) is None

    def test_envelope_status_reads_ship_to_prohibited(self) -> None:
        payload = {
            "aliexpress_ds_product_get_response": {
                "rsp_code": 482,
                "rsp_msg": "SHIP_TO_COUNTRY_PROHIBITED",
                "result": {"has_whole_sale": False},
            }
        }
        assert product_detail_envelope_status(payload) == (
            482,
            "SHIP_TO_COUNTRY_PROHIBITED",
        )
        detail = parse_product_detail(payload)
        assert detail is not None
        assert detail.product_id is None
        with pytest.raises(AliExpressShipToProhibitedError) as exc_info:
            require_usable_product_detail(payload, detail=detail, ship_to_country="US")
        assert exc_info.value.code == "aliexpress_ship_to_prohibited"

    def test_require_usable_maps_item_not_found(self) -> None:
        payload = {
            "aliexpress_ds_product_get_response": {
                "rsp_code": 605,
                "rsp_msg": "ITEM_ID_NOT_FOUND",
            }
        }
        with pytest.raises(AliExpressProductUnavailableError) as exc_info:
            require_usable_product_detail(
                payload,
                detail=parse_product_detail(payload),
                ship_to_country="US",
            )
        assert exc_info.value.code == "aliexpress_product_unavailable"

    def test_require_usable_returns_a_valid_detail(self, product_payload: dict[str, Any]) -> None:
        detail = require_usable_product_detail(
            product_payload,
            detail=parse_product_detail(product_payload),
            ship_to_country="US",
        )
        assert detail.product_id is not None

    def test_an_empty_product_parses_without_raising(self) -> None:
        detail = ProductDetail.model_validate({})
        assert detail.product_id is None
        assert detail.skus == []
        assert detail.price_range == (None, None)
        assert detail.total_stock == 0

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("3.30", Decimal("3.30")),
            (" 3.30 ", Decimal("3.30")),
            (3.3, Decimal("3.3")),
            (None, None),
            ("", None),
            ("not-a-price", None),
        ],
    )
    def test_decimal_coercion_is_total(self, raw: Any, expected: Decimal | None) -> None:
        """Never raises. A bad price is recorded as absent, not as a crash."""
        assert to_decimal(raw) == expected

    @pytest.mark.parametrize("raw", ["NaN", "nan", "Infinity", "-Infinity"])
    def test_non_finite_prices_are_rejected(self, raw: str) -> None:
        """`Decimal("NaN")` is valid and does not raise.

        Without an explicit check it reaches the catalogue, and NaN compares
        false against everything — quietly breaking `min`, `max` and every
        price filter far from the cause. Found by this test, not in production.
        """
        assert to_decimal(raw) is None

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("112", 112), (112, 112), (None, None), ("", None), ("many", None)],
    )
    def test_int_coercion_is_total(self, raw: Any, expected: int | None) -> None:
        assert to_int(raw) == expected

    def test_money_never_becomes_a_float(self) -> None:
        """0.1 + 0.2 is the reason. Money is Decimal end to end."""
        value = to_decimal("0.1")
        assert value is not None
        assert not isinstance(value, float)
        assert value + to_decimal("0.2") == Decimal("0.3")  # type: ignore[operator]
