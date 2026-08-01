"""Product API schemas.

What the platform returns to its own clients, as distinct from the wire models
in ``integrations.aliexpress.catalog`` that describe what a supplier sends.

Two things are deliberately absent from every response here:

* **The raw supplier description.** It is seller-authored HTML, and there is no
  sanitiser yet. A field that carried it would eventually be rendered, and that
  is stored XSS. The column exists; nothing exposes it.
* **Any credential.** As elsewhere in this codebase, the guarantee is structural
  rather than a matter of care — there is no field capable of holding one.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import Field, field_validator

from app.integrations.aliexpress.catalog import normalise_product_id
from app.models.product import ImportStatus, ProductSource, ProductStatus
from app.schemas.base import CamelCaseModel


class ProductImageRead(CamelCaseModel):
    """An image, with the ordering that decides which one leads a listing."""

    url: str
    position: int


class ProductVariantRead(CamelCaseModel):
    """A purchasable variation.

    ``external_attributes`` is exposed because support and debugging need it —
    it is the exact key an order must carry, and a mismatch there buys the wrong
    item. It is an opaque supplier string, not a credential.
    """

    id: uuid.UUID
    external_variant_id: str
    external_attributes: str | None = None
    label: str | None = None
    cost_price: Decimal | None = None
    list_price: Decimal | None = None
    currency: str | None = None
    stock_quantity: int
    image_url: str | None = None


class ProductRead(CamelCaseModel):
    """A product in list form.

    Variants and images are omitted here and returned only by the detail
    endpoint. A page of 50 products would otherwise carry several hundred rows
    that a list view never renders.
    """

    id: uuid.UUID
    source: ProductSource
    external_id: str
    external_url: str | None = None
    title: str
    category_id: str | None = None
    category_name: str | None = None
    brand: str | None = None
    status: ProductStatus
    currency: str | None = None
    cost_price_min: Decimal | None = None
    cost_price_max: Decimal | None = None
    stock_quantity: int
    supplier_name: str | None = None
    rating: Decimal | None = None
    review_count: int | None = None
    order_count: int | None = None
    last_synced_at: datetime | None = None
    last_sync_error: str | None = None
    created_at: datetime


class ProductDetailRead(ProductRead):
    """A single product, with everything needed to render its page."""

    variants: list[ProductVariantRead] = Field(default_factory=list)
    images: list[ProductImageRead] = Field(default_factory=list)


class ProductImportRead(CamelCaseModel):
    """One import attempt — the audit record.

    ``error_code`` is stable and safe to branch on; ``error_message`` is for
    humans and may be reworded at any time.
    """

    id: uuid.UUID
    source: ProductSource
    external_id: str
    status: ImportStatus
    product_id: uuid.UUID | None = None
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime


class ProductImportRequest(CamelCaseModel):
    """Ask for one supplier product to be imported.

    ``external_id`` only. The client does not send a title or a price: those
    come from the supplier, and accepting them here would let a caller write
    arbitrary catalogue data through an endpoint named "import".

    A full listing URL is accepted and reduced to its id — see the validator.
    """

    external_id: str = Field(min_length=1, max_length=2048)

    @field_validator("external_id")
    @classmethod
    def _normalise_identifier(cls, value: str) -> str:
        """Accept a listing URL as well as a bare id.

        Normalising here rather than in the UI means every caller benefits —
        the API, a future bulk import, a script — instead of the rule living in
        one React component and being re-invented by the next client.

        The alternative is worse than it looks. Forwarding a URL as a product id
        returns ``MissingParameter: ... "product_id" ... is not supplied`` from
        AliExpress, which describes the value as *missing* rather than
        *malformed* and sends whoever is debugging it hunting a serialisation
        bug that does not exist. Rejecting it here, with a message that names
        the real problem, is the difference between a five-second fix and an
        afternoon.
        """
        identifier = normalise_product_id(value)
        if identifier is None:
            raise ValueError(
                "Enter an AliExpress product ID (digits only) or paste the "
                "full listing URL, for example "
                "https://www.aliexpress.com/item/1005009558589813.html"
            )
        return identifier

    #: Destination country and currency shape the prices the supplier quotes,
    #: so they are import parameters rather than display preferences.
    ship_to_country: str = Field(default="US", min_length=2, max_length=2)
    currency: str = Field(default="USD", min_length=3, max_length=3)


class FeedProductRead(CamelCaseModel):
    """A product as it appears in a supplier feed, before import.

    A summary: no variants and no stock, because a feed entry does not carry
    them. Enough to choose what to import, not enough to sell.
    """

    external_id: str
    title: str | None = None
    image_url: str | None = None
    price: Decimal | None = None
    currency: str | None = None
    orders: int | None = None
    category_name: str | None = None


__all__ = [
    "FeedProductRead",
    "ProductDetailRead",
    "ProductImageRead",
    "ProductImportRead",
    "ProductImportRequest",
    "ProductRead",
    "ProductVariantRead",
]
