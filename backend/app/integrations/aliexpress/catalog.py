"""AliExpress product catalogue wire models.

Separate from ``schemas.py``, which holds the OAuth and connection contract.
This module holds the *product* contract and the parsing needed to turn it into
something the rest of the platform can use. Keeping them apart matters because
they change for different reasons and at different times: OAuth is stable,
product payloads are not.

**Every shape here was derived from a real captured response**, not from
documentation. The payloads are committed under
``tests/fixtures/aliexpress/`` and the tests parse those files, so the contract
is pinned by evidence rather than by memory.

The traps these models exist to absorb, all observed live:

* ``image_urls`` is a **semicolon-delimited string**, not an array.
* Every price is a **string** (``"3.30"``). Parsed to ``Decimal`` — never
  ``float``, which cannot represent money exactly.
* Counts and ratings are strings too (``sales_count: "112"``).
* Arrays are **double-wrapped**: ``ae_item_sku_info_dtos.ae_item_sku_info_d_t_o``.
* ``product_id`` arrives as an **integer** but must be sent as a string.
* Feed items and detail items use **different names for the same concepts**
  (``product_title`` vs ``subject``).

Every field that is not structurally guaranteed is optional. A single missing
attribute must not fail an import of an otherwise usable product — a catalogue
of 7,000 items will contain surprises, and the alternative is an import that
stops on the first one.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: AliExpress joins image URLs with this. Not a JSON array, despite the plural
#: field name.
_IMAGE_DELIMITER = ";"


def to_decimal(value: Any) -> Decimal | None:
    """Coerce an AliExpress money string to ``Decimal``.

    Money is never parsed as ``float``: 3.30 is not representable in binary
    floating point, and the error compounds across a catalogue and again across
    a margin calculation.

    Returns ``None`` rather than raising. A product whose price is unparseable
    is still worth storing — the caller decides whether it is sellable, and a
    missing price is visible while a crashed import is not.

    ``NaN`` and the infinities are rejected explicitly. ``Decimal("NaN")`` is
    perfectly valid and does *not* raise, so without this check a price of
    ``"NaN"`` would flow straight into the catalogue — and NaN compares false
    against everything, which silently breaks ``min``, ``max`` and every price
    filter downstream rather than failing anywhere near the cause.
    """
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, ArithmeticError):
        return None
    return parsed if parsed.is_finite() else None


def to_int(value: Any) -> int | None:
    """Coerce a count that may arrive as a string, an int, or nonsense."""
    if value is None or value == "":
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


class WireModel(BaseModel):
    """Base for everything AliExpress sends.

    ``extra="allow"`` throughout. AliExpress adds fields without notice, and
    rejecting unknown keys would break the integration on their schedule rather
    than ours. This is the opposite of the strictness applied to our own request
    schemas, where an unexpected field is a client error worth surfacing.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class SkuProperty(WireModel):
    """One facet of a variant — colour, size, material."""

    sku_property_id: int | None = None
    sku_property_name: str | None = None
    sku_property_value: str | None = None
    property_value_id: int | None = None
    property_value_definition_name: str | None = None
    sku_image: str | None = None


class _SkuPropertyWrapper(WireModel):
    ae_sku_property_d_t_o: list[SkuProperty] = Field(default_factory=list)


class Sku(WireModel):
    """A purchasable variant.

    ``sku_attr`` is the composite key AliExpress uses when placing an order, in
    the form ``10:529#Realme GT Neo5;14:771#tyjt white``. It is stored verbatim
    because an order must echo it back exactly; reconstructing it from the
    parsed properties would be a guess.
    """

    sku_id: str | None = None
    sku_attr: str | None = None
    currency_code: str | None = None
    sku_available_stock: int | None = None
    ae_sku_property_dtos: _SkuPropertyWrapper | None = None

    # Prices arrive as strings.
    sku_price: str | None = None
    offer_sale_price: str | None = None
    offer_bulk_sale_price: str | None = None

    @property
    def properties(self) -> list[SkuProperty]:
        return self.ae_sku_property_dtos.ae_sku_property_d_t_o if self.ae_sku_property_dtos else []

    @property
    def list_price(self) -> Decimal | None:
        """The reference price before any offer."""
        return to_decimal(self.sku_price)

    @property
    def sale_price(self) -> Decimal | None:
        """What the buyer actually pays; falls back to the list price."""
        return to_decimal(self.offer_sale_price) or to_decimal(self.sku_price)

    @property
    def variant_label(self) -> str:
        """Human-readable variant name, e.g. ``Color: Beige / Material: CANVAS``."""
        parts = [
            f"{p.sku_property_name}: {p.sku_property_value}"
            for p in self.properties
            if p.sku_property_name and p.sku_property_value
        ]
        return " / ".join(parts)

    @property
    def image_url(self) -> str | None:
        """First property image, if any variant facet carries one."""
        for prop in self.properties:
            if prop.sku_image:
                return prop.sku_image
        return None


class _SkuWrapper(WireModel):
    ae_item_sku_info_d_t_o: list[Sku] = Field(default_factory=list)


class ItemProperty(WireModel):
    """A catalogue attribute — brand, model, compatibility."""

    attr_name: str | None = None
    attr_value: str | None = None
    attr_name_id: int | None = None
    attr_value_id: int | None = None


class _ItemPropertyWrapper(WireModel):
    ae_item_property: list[ItemProperty] = Field(default_factory=list)


class MultimediaInfo(WireModel):
    """Images, delivered as one delimited string."""

    image_urls: str | None = None

    @property
    def urls(self) -> list[str]:
        if not self.image_urls:
            return []
        return [u.strip() for u in self.image_urls.split(_IMAGE_DELIMITER) if u.strip()]


class PackageInfo(WireModel):
    """Dimensions and weight, needed later for shipping estimates."""

    package_length: int | None = None
    package_width: int | None = None
    package_height: int | None = None
    gross_weight: str | None = None
    package_type: bool | None = None
    product_unit: int | None = None

    @property
    def weight_kg(self) -> Decimal | None:
        return to_decimal(self.gross_weight)


class LogisticsInfo(WireModel):
    delivery_time: int | None = None
    ship_to_country: str | None = None


class StoreInfo(WireModel):
    store_id: int | None = None
    store_name: str | None = None
    store_country_code: str | None = None
    communication_rating: str | None = None
    item_as_described_rating: str | None = None
    shipping_speed_rating: str | None = None


class ItemBaseInfo(WireModel):
    """Core product fields.

    ``detail`` and ``mobile_detail`` are large HTML/JSON blobs. They are part of
    the contract but are not stored: they are seller-authored markup that would
    be injected into our pages, and Phase 4 has no sanitiser. Recorded as a
    known limitation rather than stored and forgotten.
    """

    product_id: int | None = None
    subject: str | None = None
    category_id: int | None = None
    currency_code: str | None = None
    product_status_type: str | None = None
    sales_count: str | None = None
    evaluation_count: str | None = None
    avg_evaluation_rating: str | None = None

    @property
    def title(self) -> str | None:
        return self.subject

    @property
    def orders(self) -> int | None:
        return to_int(self.sales_count)

    @property
    def reviews(self) -> int | None:
        return to_int(self.evaluation_count)

    @property
    def rating(self) -> Decimal | None:
        return to_decimal(self.avg_evaluation_rating)

    @property
    def is_active(self) -> bool:
        """AliExpress reports ``onSelling`` for a listing that can be bought."""
        return (self.product_status_type or "").lower() == "onselling"


class IdConverterResult(WireModel):
    """The queried id is not always the canonical one.

    ``sub_product_id`` is a **JSON string** holding a country map, e.g.
    ``{"US":3256806389000685}``. Both ids are kept: the main id is the stable
    catalogue key, the queried id is what a later request must use.
    """

    main_product_id: int | None = None
    sub_product_id: str | None = None


class ProductDetail(WireModel):
    """The ``result`` object of ``aliexpress.ds.product.get``."""

    ae_item_base_info_dto: ItemBaseInfo | None = None
    ae_item_sku_info_dtos: _SkuWrapper | None = None
    ae_multimedia_info_dto: MultimediaInfo | None = None
    ae_item_properties: _ItemPropertyWrapper | None = None
    package_info_dto: PackageInfo | None = None
    logistics_info_dto: LogisticsInfo | None = None
    ae_store_info: StoreInfo | None = None
    product_id_converter_result: IdConverterResult | None = None
    has_whole_sale: bool | None = None

    @property
    def base(self) -> ItemBaseInfo:
        return self.ae_item_base_info_dto or ItemBaseInfo()

    @property
    def skus(self) -> list[Sku]:
        return (
            self.ae_item_sku_info_dtos.ae_item_sku_info_d_t_o if self.ae_item_sku_info_dtos else []
        )

    @property
    def image_urls(self) -> list[str]:
        return self.ae_multimedia_info_dto.urls if self.ae_multimedia_info_dto else []

    @property
    def attributes(self) -> list[ItemProperty]:
        return self.ae_item_properties.ae_item_property if self.ae_item_properties else []

    @property
    def product_id(self) -> str | None:
        """The id to use for subsequent lookups, as a string.

        Prefers the id the response was keyed on; falls back to the canonical
        main id. Returned as a string because that is what the API expects on
        the way back in, even though it arrives as an integer.
        """
        if self.base.product_id is not None:
            return str(self.base.product_id)
        converter = self.product_id_converter_result
        if converter and converter.main_product_id is not None:
            return str(converter.main_product_id)
        return None

    @property
    def price_range(self) -> tuple[Decimal | None, Decimal | None]:
        """Lowest and highest sale price across variants."""
        prices = [s.sale_price for s in self.skus if s.sale_price is not None]
        if not prices:
            return (None, None)
        return (min(prices), max(prices))

    @property
    def total_stock(self) -> int:
        return sum(s.sku_available_stock or 0 for s in self.skus)


class FeedProduct(WireModel):
    """A product as it appears in a feed.

    Deliberately a separate model from :class:`ProductDetail`. The feed uses
    different names for the same concepts — ``product_title`` not ``subject``,
    ``sale_price`` not ``offer_sale_price`` — and mapping one onto the other
    would hide a real difference in what each endpoint guarantees. A feed entry
    is a summary; only the detail call carries variants.
    """

    product_id: int | None = None
    product_title: str | None = None
    product_main_image_url: str | None = None
    product_detail_url: str | None = None
    product_video_url: str | None = None

    sale_price: str | None = None
    original_price: str | None = None
    sale_price_currency: str | None = None
    discount: str | None = None

    first_level_category_id: int | None = None
    first_level_category_name: str | None = None
    second_level_category_id: int | None = None
    second_level_category_name: str | None = None

    shop_id: int | None = None
    seller_id: int | None = None
    shop_url: str | None = None

    evaluate_rate: str | None = None
    lastest_volume: int | None = None

    @field_validator("product_id", mode="before")
    @classmethod
    def _coerce_id(cls, value: Any) -> Any:
        """Seen as both int and string depending on the feed."""
        return to_int(value) if value is not None else None

    @property
    def identifier(self) -> str | None:
        return str(self.product_id) if self.product_id is not None else None

    @property
    def price(self) -> Decimal | None:
        return to_decimal(self.sale_price)

    @property
    def list_price(self) -> Decimal | None:
        return to_decimal(self.original_price)


#: Matches the numeric id in an AliExpress listing URL.
#:
#: Their URLs take the form ``.../item/1005009558589813.html``, with optional
#: locale prefixes (``/en/``), regional hosts and tracking query strings. Only
#: the ``/item/<digits>`` segment is stable across all of them.
_ITEM_ID_IN_URL = re.compile(r"/item/(\d+)")

#: A bare identifier: digits only. AliExpress ids are numeric, and anything else
#: is a paste that needs extracting or a value that will be rejected upstream.
_BARE_ID = re.compile(r"^\d+$")


def normalise_product_id(value: str) -> str | None:
    """Extract an AliExpress product id from an id or a listing URL.

    **Exists because the gateway's error for a malformed id is actively
    misleading.** Sending a URL where a product id belongs returns
    ``MissingParameter: The input parameter "product_id" ... is not supplied``
    — it reports the value as *absent* rather than *wrong*, which sends whoever
    is debugging it looking for a serialisation bug that is not there. Verified
    live against the real gateway.

    Accepting a pasted URL is also just what a user does: the id lives in the
    address bar, so that is what gets copied. Refusing it would be technically
    defensible and practically useless.

    Returns ``None`` when no id can be recovered, so the caller can say
    something specific instead of forwarding a value the supplier will reject.
    """
    candidate = (value or "").strip()
    if not candidate:
        return None

    if _BARE_ID.match(candidate):
        return candidate

    match = _ITEM_ID_IN_URL.search(candidate)
    if match:
        return str(match.group(1))

    # A bare id with decoration — quotes, a stray trailing character. Recover
    # the digits only when they are unambiguous: exactly one run in the string.
    runs = re.findall(r"\d{6,}", candidate)
    if len(runs) == 1:
        return str(runs[0])

    return None


def parse_product_detail(payload: dict[str, Any]) -> ProductDetail | None:
    """Extract the product from a raw ``ds.product.get`` envelope.

    Returns ``None`` when the envelope carries no result — a product id that no
    longer exists answers ``rsp_code 605 ITEM_ID_NOT_FOUND`` with a well-formed
    envelope and no payload, which is a normal outcome during a catalogue
    refresh rather than an error.
    """
    body = payload.get("aliexpress_ds_product_get_response") or payload
    result = body.get("result")
    if not isinstance(result, dict):
        return None
    return ProductDetail.model_validate(result)


def parse_feed_products(payload: dict[str, Any]) -> list[FeedProduct]:
    """Extract products from a raw ``ds.recommend.feed.get`` envelope.

    Tolerates an empty feed, which is a real outcome: an invented feed name
    returns ``total_record_count: 0`` rather than an error, and that is exactly
    how a wrong feed name goes unnoticed.
    """
    body = payload.get("aliexpress_ds_recommend_feed_get_response") or payload
    result = body.get("result")
    if not isinstance(result, dict):
        return []
    products = result.get("products")
    if not isinstance(products, dict):
        return []
    items = products.get("traffic_product_d_t_o")
    if not isinstance(items, list):
        return []
    return [FeedProduct.model_validate(i) for i in items if isinstance(i, dict)]


class Category(WireModel):
    """A catalogue category.

    ``parent_category_id`` is **absent** on top-level categories rather than
    null, so its optionality is what distinguishes a root from a child.
    """

    category_id: int | None = None
    category_name: str | None = None
    parent_category_id: int | None = None

    @property
    def is_root(self) -> bool:
        return self.parent_category_id is None


def parse_categories(payload: dict[str, Any]) -> list[Category]:
    """Extract categories from a raw ``ds.category.get`` envelope.

    Note the envelope differs from the product one: categories nest under
    ``resp_result.result`` where products use a bare ``result``. That
    inconsistency is AliExpress's, and it is the reason each endpoint gets its
    own parser rather than a shared unwrapper.
    """
    body = payload.get("aliexpress_ds_category_get_response") or payload
    result = (body.get("resp_result") or {}).get("result") or {}
    items = (result.get("categories") or {}).get("category")
    if not isinstance(items, list):
        return []
    return [Category.model_validate(i) for i in items if isinstance(i, dict)]


def parse_feed_names(payload: dict[str, Any]) -> list[str]:
    """Extract the promotion feed names available to this account."""
    body = payload.get("aliexpress_ds_feedname_get_response") or payload
    result = (body.get("resp_result") or {}).get("result") or {}
    promos = (result.get("promos") or {}).get("promo") or []
    return [str(p["promo_name"]) for p in promos if isinstance(p, dict) and p.get("promo_name")]


__all__ = [
    "Category",
    "FeedProduct",
    "ItemBaseInfo",
    "ItemProperty",
    "LogisticsInfo",
    "MultimediaInfo",
    "PackageInfo",
    "ProductDetail",
    "Sku",
    "SkuProperty",
    "StoreInfo",
    "normalise_product_id",
    "parse_categories",
    "parse_feed_names",
    "parse_feed_products",
    "parse_product_detail",
    "to_decimal",
    "to_int",
]
