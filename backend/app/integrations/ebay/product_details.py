"""EBAY-C3: a product's eBay category and item specifics (D-C3-3).

The merchant picks a leaf category (suggested from the title by eBay's
Taxonomy API) and fills the aspects eBay requires for it. Stored per product
and marketplace in ``product_marketplace_attributes``. Publish readiness asks
``missing_required_aspects`` before an offer is ever sent.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.integrations.ebay import taxonomy
from app.integrations.ebay.seller_setup import EBAY_SUPPORTED_MARKETPLACES
from app.integrations.ebay.taxonomy import EbayAspect, EbayCategorySuggestion
from app.models.product import ProductMarketplaceAttributes
from app.repositories.product import (
    ProductMarketplaceAttributesRepository,
    ProductRepository,
)

#: eBay category ids are numeric strings.
_CATEGORY_ID = re.compile(r"^\d{1,10}$")
# eBay's own limits: aspect names 40 characters, values 65, at most 45 aspects.
_MAX_ASPECTS = 45
_MAX_NAME = 40
_MAX_VALUE = 65
_MAX_VALUES_PER_ASPECT = 30


@dataclass(frozen=True, slots=True)
class EbayProductDetails:
    marketplace_id: str
    category_id: str | None
    category_name: str | None
    aspects: dict[str, list[str]]
    #: eBay's aspects for the chosen category; empty when none is chosen.
    category_aspects: tuple[EbayAspect, ...]
    missing_required: tuple[str, ...]


def _require_marketplace(marketplace_id: str) -> None:
    if marketplace_id not in EBAY_SUPPORTED_MARKETPLACES:
        raise ValidationError("That eBay marketplace is not supported.")


def normalise_aspects(raw: dict[str, Any]) -> dict[str, list[str]]:
    """Trim, drop empties, enforce eBay's size limits. Raises on a breach
    rather than truncating: a silently shortened brand is a wrong listing."""
    if len(raw) > _MAX_ASPECTS:
        raise ValidationError(f"eBay accepts at most {_MAX_ASPECTS} item specifics.")
    out: dict[str, list[str]] = {}
    for name, values in raw.items():
        clean_name = name.strip()
        if not clean_name:
            continue
        if len(clean_name) > _MAX_NAME:
            raise ValidationError(f"Item specific name '{clean_name[:20]}…' is too long for eBay.")
        listed = values if isinstance(values, list) else [values]
        clean_values = [str(v).strip() for v in listed if str(v).strip()]
        if len(clean_values) > _MAX_VALUES_PER_ASPECT:
            raise ValidationError(f"Too many values for '{clean_name}'.")
        if any(len(v) > _MAX_VALUE for v in clean_values):
            raise ValidationError(f"A value for '{clean_name}' is longer than eBay allows.")
        if clean_values:
            out[clean_name] = clean_values
    return out


def missing_required(
    aspects: dict[str, list[str]], category_aspects: tuple[EbayAspect, ...]
) -> tuple[str, ...]:
    filled = {name.casefold() for name, values in aspects.items() if values}
    return tuple(a.name for a in category_aspects if a.required and a.name.casefold() not in filled)


class EbayProductDetailsService:
    def __init__(self, session: AsyncSession) -> None:
        self.products = ProductRepository(session)
        self.attributes = ProductMarketplaceAttributesRepository(session)

    async def get(self, product_id: uuid.UUID, marketplace_id: str) -> EbayProductDetails:
        _require_marketplace(marketplace_id)
        await self.products.get_by_id_or_raise(product_id)
        row = await self.attributes.get_for(product_id, marketplace_id)
        return await self._details(marketplace_id, row)

    async def suggest_categories(
        self, product_id: uuid.UUID, marketplace_id: str, query: str | None
    ) -> tuple[EbayCategorySuggestion, ...]:
        _require_marketplace(marketplace_id)
        product = await self.products.get_by_id_or_raise(product_id)
        text = (query or product.title or "").strip()
        if not text:
            raise ValidationError("Give the product a title, or type what it is.")
        return await taxonomy.category_suggestions(marketplace_id, text)

    async def save(
        self,
        product_id: uuid.UUID,
        marketplace_id: str,
        *,
        category_id: str,
        category_name: str | None,
        aspects: dict[str, Any],
    ) -> EbayProductDetails:
        _require_marketplace(marketplace_id)
        if not _CATEGORY_ID.match(category_id):
            raise ValidationError("That is not an eBay category id.")
        await self.products.get_by_id_or_raise(product_id)
        clean = normalise_aspects(aspects)
        row = await self.attributes.get_for(product_id, marketplace_id)
        values: dict[str, Any] = {
            "category_id": category_id,
            "category_name": (category_name or "").strip()[:255] or None,
            "aspects": clean,
        }
        if row is None:
            row = await self.attributes.create(
                product_id=product_id, marketplace_id=marketplace_id, **values
            )
        else:
            row = await self.attributes.update(row, **values)
        return await self._details(marketplace_id, row)

    async def missing_required_aspects(
        self, product_id: uuid.UUID, marketplace_id: str
    ) -> tuple[bool, tuple[str, ...]]:
        """``(has_category, missing aspect names)`` for publish readiness.
        Asks eBay for the category's current requirements, so a category that
        gained a required aspect since the merchant saved is caught here."""
        row = await self.attributes.get_for(product_id, marketplace_id)
        if row is None or not row.category_id:
            return False, ()
        category_aspects = await taxonomy.item_aspects(marketplace_id, row.category_id)
        return True, missing_required(row.aspects or {}, category_aspects)

    async def _details(
        self, marketplace_id: str, row: ProductMarketplaceAttributes | None
    ) -> EbayProductDetails:
        aspects: dict[str, list[str]] = dict(row.aspects or {}) if row else {}
        category_id = row.category_id if row else None
        category_aspects = (
            await taxonomy.item_aspects(marketplace_id, category_id) if category_id else ()
        )
        return EbayProductDetails(
            marketplace_id=marketplace_id,
            category_id=category_id,
            category_name=row.category_name if row else None,
            aspects=aspects,
            category_aspects=category_aspects,
            missing_required=missing_required(aspects, category_aspects),
        )


__all__ = [
    "EbayProductDetails",
    "EbayProductDetailsService",
    "missing_required",
    "normalise_aspects",
]
