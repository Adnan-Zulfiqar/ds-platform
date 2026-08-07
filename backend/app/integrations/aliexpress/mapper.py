"""Translate AliExpress payloads into catalogue values.

**The only place the two vocabularies meet.** Everything above this module works
in domain terms; everything below it works in AliExpress terms. The dependency
runs one way — this imports the domain, the domain never imports this — which is
what allows a second supplier to be added by writing another mapper rather than
by changing the catalogue.

Returns plain dictionaries of column values rather than ORM instances. The
repository owns persistence, and a mapper that constructed entities would need a
session, which would drag data access into a translation layer.
"""

from __future__ import annotations

from typing import Any

from app.core.sanitize import sanitize_html
from app.integrations.aliexpress.catalog import FeedProduct, ProductDetail
from app.models.product import ProductSource, ProductStatus

#: AliExpress product pages follow a stable pattern, and the payload does not
#: carry a canonical URL for the detail call. Built here so that a support
#: question — "show me the original listing" — has an answer.
_ITEM_URL = "https://www.aliexpress.com/item/{product_id}.html"

#: Supplier titles routinely exceed the column. Truncation is deliberate and
#: happens once, here, rather than as a database error at insert time.
_TITLE_LIMIT = 512


def _truncate(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed[:limit] if len(trimmed) > limit else trimmed


def _skus_currency(detail: ProductDetail) -> str | None:
    """The currency ``cost_price_min``/``cost_price_max`` are actually in.

    Those two figures are ``min``/``max`` of each SKU's own ``sale_price``
    (see ``ProductDetail.price_range``) -- so the currency that belongs next
    to them is whatever the SKUs themselves report, not
    ``ae_item_base_info_dto.currency_code``. A live-traced AliExpress
    response for a GB/GBP request proved the two can genuinely differ: base
    info stays CNY (the seller's native listing currency, never localized)
    while every SKU reports GBP (the requested target, honored at SKU
    level). Pairing the min/max numbers with the base currency was a real,
    live-reproduced mislabeling bug -- see
    docs/ALIEXPRESS_LOCALIZED_PRICING.md.
    """
    codes = {sku.currency_code for sku in detail.skus if sku.currency_code}
    if len(codes) == 1:
        return next(iter(codes))
    return None


def map_product(detail: ProductDetail) -> dict[str, Any]:
    """Map a product detail response to ``Product`` column values.

    Deliberately tolerant. A product missing a rating, a package weight or a
    store name is still worth importing; only the identifier and a title are
    required, and a product without a title gets a placeholder rather than
    failing the import. Across a catalogue of thousands, refusing to store
    anything imperfect means storing very little.

    ``status`` is not set here. Whether an import creates a draft or updates an
    existing product's status is the service's decision, and encoding it here
    would mean a re-sync silently reverting a product the tenant had activated.

    ``supplier_title``/``supplier_brand``/``supplier_description`` are
    likewise not applied directly to ``Product.title``/``Product.brand``/
    ``Product.description`` here. Each is the always-fresh supplier
    snapshot; whether it is safe to also refresh the merchant-editable twin
    is a sync-policy decision belonging to
    :meth:`ProductImportService._upsert`, the same reasoning that already
    keeps ``status`` out of this function.

    Package weight/dimensions and logistics delivery hints are stored for the
    Draft Shipping workspace. Freight quotes are still usually absent from
    ``ds.product.get`` — ``shipping_cost`` stays null rather than inventing zero.
    """
    base = detail.base
    store = detail.ae_store_info
    package = detail.package_info_dto
    logistics = detail.logistics_info_dto
    low, high = detail.price_range

    external_id = detail.product_id
    canonical = detail.product_id_converter_result

    brand = next(
        (a.attr_value for a in detail.attributes if (a.attr_name or "").lower() == "brand name"),
        None,
    )

    return {
        "source": ProductSource.ALIEXPRESS,
        "external_id": external_id,
        "external_canonical_id": (
            str(canonical.main_product_id)
            if canonical and canonical.main_product_id is not None
            else None
        ),
        "external_url": _ITEM_URL.format(product_id=external_id) if external_id else None,
        # A product with no title is still a product. The placeholder is visible
        # in the UI, which is better than an import that fails on one field.
        "supplier_title": _truncate(base.title, _TITLE_LIMIT) or f"Untitled product {external_id}",
        # Sanitized here, once, before the value ever reaches the mapper's
        # caller — never at render time, and never left as raw supplier HTML
        # for something downstream to forget to sanitize.
        "supplier_description": sanitize_html(base.description_html),
        "category_id": str(base.category_id) if base.category_id is not None else None,
        "supplier_brand": _truncate(brand, 255),
        # Paired with cost_price_min/max below -- see `_skus_currency`. Falls
        # back to the base/native currency only when SKUs disagree or are
        # absent, which is the same "no data to be confident about" case
        # `PricingEngine._resolve_selling_currency` treats as unresolved
        # rather than guessing.
        "currency": _skus_currency(detail) or base.currency_code,
        "supplier_native_currency": base.currency_code,
        "cost_price_min": low,
        "cost_price_max": high,
        "stock_quantity": detail.total_stock,
        "package_weight_kg": package.weight_kg if package else None,
        "package_length_cm": package.package_length if package else None,
        "package_width_cm": package.package_width if package else None,
        "package_height_cm": package.package_height if package else None,
        "delivery_time_days": logistics.delivery_time if logistics else None,
        "ship_to_country": (
            _truncate(logistics.ship_to_country, 8)
            if logistics and logistics.ship_to_country
            else None
        ),
        # Freight is not on the product detail contract we use today.
        "shipping_cost": None,
        "warehouse_origin": (
            _truncate(store.store_country_code, 64) if store and store.store_country_code else None
        ),
        "supplier_name": _truncate(store.store_name, 255) if store else None,
        "supplier_id": str(store.store_id) if store and store.store_id is not None else None,
        "rating": base.rating,
        "review_count": base.reviews,
        "order_count": base.orders,
        "last_sync_error": None,
    }


def map_variants(detail: ProductDetail) -> list[dict[str, Any]]:
    """Map SKUs to ``ProductVariant`` column values.

    Variants without an identifier are dropped. A variant that cannot be named
    in an order request is not orderable, so storing it would mean showing a
    customer an option that cannot be bought.
    """
    variants: list[dict[str, Any]] = []
    for sku in detail.skus:
        if not sku.sku_id:
            continue
        variants.append(
            {
                "external_variant_id": str(sku.sku_id),
                # Stored verbatim: an order must echo this exact composite key.
                "external_attributes": _truncate(sku.sku_attr, 1024),
                "label": _truncate(sku.variant_label, 512) or None,
                "cost_price": sku.sale_price,
                "list_price": sku.list_price,
                "currency": sku.currency_code or detail.base.currency_code,
                "stock_quantity": sku.sku_available_stock or 0,
                "image_url": _truncate(sku.image_url, 1024),
            }
        )
    return variants


def map_images(detail: ProductDetail) -> list[dict[str, Any]]:
    """Map the delimited image string to ``ProductImage`` rows.

    Order is preserved and stored as ``position``: position 0 is the image a
    listing leads with, and losing that ordering means every imported product
    leads with an arbitrary photo.

    Duplicates are removed. The same URL appearing twice would violate the
    unique constraint on ``(product_id, url)`` and fail an otherwise good
    import.
    """
    seen: set[str] = set()
    images: list[dict[str, Any]] = []
    for url in detail.image_urls:
        trimmed = _truncate(url, 1024)
        if not trimmed or trimmed in seen:
            continue
        seen.add(trimmed)
        images.append({"url": trimmed, "position": len(images)})
    return images


def map_feed_product(item: FeedProduct) -> dict[str, Any] | None:
    """Map a feed entry to ``Product`` column values.

    A feed entry is a *summary*: it has no variants and no stock. It is enough
    to list a product for selection, not to sell one — which is why an import
    always follows up with a detail call rather than trusting the feed.

    Returns ``None`` for an entry with no identifier, which cannot be imported
    later and is therefore not worth storing.
    """
    if not item.identifier:
        return None
    return {
        "source": ProductSource.ALIEXPRESS,
        "external_id": item.identifier,
        "external_url": item.product_detail_url or _ITEM_URL.format(product_id=item.identifier),
        "title": _truncate(item.product_title, _TITLE_LIMIT)
        or f"Untitled product {item.identifier}",
        "category_id": (
            str(item.second_level_category_id)
            if item.second_level_category_id is not None
            else None
        ),
        "category_name": _truncate(item.second_level_category_name, 255),
        "currency": item.sale_price_currency,
        "cost_price_min": item.price,
        "cost_price_max": item.price,
        "supplier_id": str(item.shop_id) if item.shop_id is not None else None,
        "order_count": item.lastest_volume,
        "status": ProductStatus.DRAFT,
    }


__all__ = ["map_feed_product", "map_images", "map_product", "map_variants"]
