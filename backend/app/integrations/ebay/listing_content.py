"""What a product becomes on eBay (EBAY-C3) — one place for readiness and publish.

Publish readiness and the publish itself must agree on what is sent, or a
product passes the check and fails at eBay. Both call ``offer_terms`` and
``listing_text`` here.

First cut (D-C3-5): one enabled variant (or none), condition NEW.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from app.domain.money import normalise_currency
from app.models.product import Product, ProductVariant

#: eBay's title limit.
EBAY_TITLE_MAX = 80
#: eBay accepts up to 24 pictures per listing.
EBAY_MAX_IMAGES = 24


@dataclass(frozen=True, slots=True)
class OfferTerms:
    price: Decimal | None
    currency: str | None
    quantity: int
    variant: ProductVariant | None
    #: More than one enabled variant: not supported in the first cut.
    multiple_variants: bool


def ebay_sku(product_id: uuid.UUID) -> str:
    """Deterministic, so a retried publish adopts the inventory item and offer
    it already created instead of making a second listing. 39 of eBay's 50."""
    return f"dp-{product_id}"


def _live_variants(product: Product) -> list[ProductVariant]:
    return [
        v
        for v in product.variants
        if getattr(v, "deleted_at", None) is None and getattr(v, "is_enabled", True)
    ]


def offer_terms(product: Product) -> OfferTerms:
    """Price, currency and quantity eBay would be sent.

    Only ``sell_price`` counts — the merchant's price from the Pricing tab —
    never the supplier ``list_price`` fallback Shopify publish tolerates:
    eBay must not receive a supplier's reference price as the listing price.
    """
    variants = _live_variants(product)
    if len(variants) > 1:
        return OfferTerms(None, None, 0, None, multiple_variants=True)
    if variants:
        variant = variants[0]
        currency = variant.sell_price_currency
        return OfferTerms(
            price=variant.sell_price,
            currency=normalise_currency(currency) if currency else None,
            quantity=int(variant.stock_quantity or 0),
            variant=variant,
            multiple_variants=False,
        )
    return OfferTerms(
        price=product.sell_price,
        currency=normalise_currency(product.currency) if product.currency else None,
        quantity=int(product.stock_quantity or 0),
        variant=None,
        multiple_variants=False,
    )


def image_urls(product: Product) -> list[str]:
    """Live images in position order; eBay fetches them, so only https."""
    images = sorted(
        (
            image
            for image in product.images
            if image.url and getattr(image, "deleted_at", None) is None
        ),
        key=lambda image: image.position,
    )
    return [image.url for image in images if image.url.startswith("https://")][:EBAY_MAX_IMAGES]


def listing_text(product: Product) -> tuple[str, str]:
    """``(title, description)`` as eBay receives them: the draft's own text."""
    title = (product.title or "").strip()
    description = (product.description or "").strip() or title
    return title, description


__all__ = [
    "EBAY_MAX_IMAGES",
    "EBAY_TITLE_MAX",
    "OfferTerms",
    "ebay_sku",
    "image_urls",
    "listing_text",
    "offer_terms",
]
