"""Product API schemas.

What the platform returns to its own clients, as distinct from the wire models
in ``integrations.aliexpress.catalog`` that describe what a supplier sends.

Two things are deliberately absent from every response here:

* **The raw, unsanitized supplier description.** ``ItemBaseInfo.description_html``
  is seller-authored HTML and never reaches a schema directly — it is
  sanitized once, at import (``app.core.sanitize.sanitize_html``), before it
  is even stored. ``ProductDetailRead.description``/``supplier_description``
  carry the sanitized result, which is why they are safe to expose (Product
  Editor stage 1) where the raw field never was.
* **Any credential.** As elsewhere in this codebase, the guarantee is structural
  rather than a matter of care — there is no field capable of holding one.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import Field, field_validator

from app.integrations.aliexpress.catalog import normalise_product_id
from app.models.product import (
    ImportStatus,
    ProductAIStatus,
    ProductSource,
    ProductStatus,
    ProductVersion,
    ProductVersionSource,
)
from app.schemas.base import CamelCaseModel


class ProductImageRead(CamelCaseModel):
    """An image, with the ordering that decides which one leads a listing."""

    id: uuid.UUID
    url: str
    position: int
    alt_text: str | None = None
    is_supplier: bool = True


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
    merchant_sku: str | None = None
    sell_price: Decimal | None = None
    compare_at_price: Decimal | None = None
    is_enabled: bool = True


class ProductImageCreateRequest(CamelCaseModel):
    """Add a merchant image by URL (S3 upload lands later)."""

    url: str = Field(min_length=8, max_length=1024)
    alt_text: str | None = Field(default=None, max_length=512)


class ProductImageUpdateRequest(CamelCaseModel):
    alt_text: str | None = Field(default=None, max_length=512)


class ProductImageReorderRequest(CamelCaseModel):
    """Ordered image ids — index 0 becomes the featured image."""

    image_ids: list[uuid.UUID] = Field(min_length=1)


class ProductVariantUpdateRequest(CamelCaseModel):
    """Merchant edits to a single variant (supplier ids stay read-only)."""

    label: str | None = Field(default=None, max_length=512)
    merchant_sku: str | None = Field(default=None, max_length=128)
    sell_price: Decimal | None = None
    compare_at_price: Decimal | None = None
    image_url: str | None = Field(default=None, max_length=1024)
    is_enabled: bool | None = None


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
    sell_price: Decimal | None = None
    stock_quantity: int
    package_weight_kg: Decimal | None = None
    package_length_cm: int | None = None
    package_width_cm: int | None = None
    package_height_cm: int | None = None
    delivery_time_days: int | None = None
    ship_to_country: str | None = None
    shipping_cost: Decimal | None = None
    warehouse_origin: str | None = None
    requires_shipping: bool = True
    hs_code: str | None = None
    country_of_origin: str | None = None
    customs_description: str | None = None
    handling_time_days: int | None = None
    weight_unit: str | None = None
    dimension_unit: str | None = None
    supplier_name: str | None = None
    rating: Decimal | None = None
    review_count: int | None = None
    order_count: int | None = None
    last_synced_at: datetime | None = None
    last_sync_error: str | None = None
    created_at: datetime

    # --- SEO / marketplace (Phase 9 stage 3) ---------------------------------
    seo_title: str | None = None
    seo_description: str | None = None
    #: Legacy; not exported to Shopify. Prefer ``search_topics``.
    meta_keywords: str | None = None
    search_topics: list[str] = Field(default_factory=list)
    seo_planning: dict[str, Any] = Field(default_factory=dict)
    og_title: str | None = None
    og_description: str | None = None
    redirect_old_handle: bool = True
    slug: str | None = None
    vendor: str | None = None
    tags: list[str] = Field(default_factory=list)

    # --- AI optimisation (Phase 9 stage 3) -----------------------------------
    #
    # ``optimized_title``/``optimized_description`` are AI-generated text —
    # currently always ``StubProvider`` output — and, like every other
    # AI-produced field in this platform, must be rendered as plain text,
    # never as HTML: `docs/PHASE_9_PLAN.md`'s risk table applies the same
    # rule to model output that already applies to the raw supplier
    # ``description`` this schema deliberately omits.
    ai_status: ProductAIStatus = ProductAIStatus.NOT_OPTIMIZED
    ai_last_generated_at: datetime | None = None
    ai_provider: str | None = None
    ai_version: int | None = None
    optimized_title: str | None = None
    optimized_description: str | None = None


class ProductDetailRead(ProductRead):
    """A single product, with everything needed to render its page."""

    variants: list[ProductVariantRead] = Field(default_factory=list)
    images: list[ProductImageRead] = Field(default_factory=list)

    # --- Description (Product Editor stage 1) --------------------------------
    #
    # Both already sanitized (`app.core.sanitize.sanitize_html`) before
    # storage -- never the raw supplier `detail`/`mobile_detail`.
    # `description` is the merchant-editable field, seeded from the supplier
    # on first import. `supplier_description` is the always-current supplier
    # snapshot, kept separately so a future edit can never be silently
    # overwritten by the next sync -- see `ProductImportService._upsert`.
    # Detail-only, like `variants`/`images` above: a list page for dozens of
    # products should not carry a full description body per row.
    description: str | None = None
    supplier_description: str | None = None

    # Supplier twins for title/brand (Product Editor stage 2 / draft editor).
    # Read-only provenance for the merchant Overview tab.
    supplier_title: str | None = None
    supplier_brand: str | None = None


class ProductUpdateRequest(CamelCaseModel):
    """Merchant edits to a product's editable fields (Product Editor stage 2).

    Every field is optional and PATCH semantics apply — only fields actually
    present in the request body are changed; the router reads
    ``model_dump(exclude_unset=True)`` rather than treating an absent field
    the same as an explicit ``null``.

    ``title``/``brand``/``description`` are supplier-sourced fields that are
    now safe to edit because they have a ``supplier_*`` twin
    (`ProductImportService._upsert`) protecting them from being silently
    reverted by the next sync. The rest — ``category_name``, ``vendor``,
    ``tags``, and the SEO fields — have no supplier equivalent at all and
    were never at risk.
    """

    title: str | None = Field(default=None, min_length=1, max_length=512)
    description: str | None = None
    brand: str | None = Field(default=None, max_length=255)
    category_name: str | None = Field(default=None, max_length=255)
    vendor: str | None = Field(default=None, max_length=255)
    tags: list[str] | None = None
    seo_title: str | None = Field(default=None, max_length=512)
    seo_description: str | None = Field(default=None, max_length=512)
    meta_keywords: str | None = None
    search_topics: list[str] | None = None
    seo_planning: dict[str, Any] | None = None
    og_title: str | None = Field(default=None, max_length=512)
    og_description: str | None = Field(default=None, max_length=1024)
    redirect_old_handle: bool | None = None
    slug: str | None = Field(default=None, max_length=255)
    status: ProductStatus | None = None
    requires_shipping: bool | None = None
    hs_code: str | None = Field(default=None, max_length=32)
    country_of_origin: str | None = Field(default=None, max_length=8)
    customs_description: str | None = Field(default=None, max_length=255)
    handling_time_days: int | None = Field(default=None, ge=0, le=365)
    package_weight_kg: Decimal | None = None
    package_length_cm: int | None = Field(default=None, ge=0)
    package_width_cm: int | None = Field(default=None, ge=0)
    package_height_cm: int | None = Field(default=None, ge=0)
    weight_unit: str | None = Field(default=None, max_length=8)
    dimension_unit: str | None = Field(default=None, max_length=8)

    @field_validator("title", "brand", "category_name", "vendor", "seo_title", "slug")
    @classmethod
    def _reject_blank_when_provided(cls, value: str | None) -> str | None:
        """`str_strip_whitespace` already trims; an explicitly-sent empty
        string here almost always means "the field was cleared" from a form,
        which is ambiguous with "no change" for `exclude_unset`-style PATCH
        semantics on a field where blank isn't a meaningful value. `title` in
        particular is `NOT NULL` — silently writing `""` would pass
        validation here and then fail (or worse, succeed and corrupt display)
        at the database.
        """
        if value is not None and value == "":
            raise ValueError("This field cannot be set to an empty string.")
        return value


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


class ProductVersionRead(CamelCaseModel):
    """One version of a product's optimisable content — one row of history.

    ``title``/``description`` are read out of the stored ``content`` JSONB
    rather than exposed as a raw dict, keeping the wire contract typed even
    though storage stays flexible for fields a later stage may add.
    """

    id: uuid.UUID
    version_number: int
    source: ProductVersionSource
    title: str | None = None
    description: str | None = None
    active: bool
    ai_provider: str | None = None
    prompt_execution_id: uuid.UUID | None = None
    created_by_user_id: uuid.UUID | None = None
    created_at: datetime

    @classmethod
    def from_model(cls, version: ProductVersion) -> ProductVersionRead:
        """Build from a `ProductVersion` ORM instance.

        A small `model_validate` wrapper rather than a bare
        ``from_attributes`` mapping: ``content`` is a dict on the model but
        two typed top-level fields on the schema, so the two do not line up
        automatically.
        """
        content = version.content or {}
        return cls(
            id=version.id,
            version_number=version.version_number,
            source=version.source,
            title=content.get("title"),
            description=content.get("description"),
            active=version.active,
            ai_provider=version.ai_provider,
            prompt_execution_id=version.prompt_execution_id,
            created_by_user_id=version.created_by_user_id,
            created_at=version.created_at,
        )


class ProductOptimizeRequest(CamelCaseModel):
    """Ask for a new AI-generated title and description.

    No product data is accepted here — only tone, which the seeded prompts
    cannot derive from the product itself. Everything else (title, category,
    brand, features) is read from the product's own current fields.
    """

    tone: str = Field(default="professional", min_length=1, max_length=64)


class ProductOptimizeResponse(CamelCaseModel):
    """The product after optimisation, and the version that produced it."""

    product: ProductDetailRead
    version: ProductVersionRead


class ProductWorkspaceCounts(CamelCaseModel):
    """Sidebar badge totals for the Drafts / Products workspace split."""

    drafts: int
    products: int


__all__ = [
    "FeedProductRead",
    "ProductDetailRead",
    "ProductImageCreateRequest",
    "ProductImageRead",
    "ProductImageReorderRequest",
    "ProductImageUpdateRequest",
    "ProductImportRead",
    "ProductImportRequest",
    "ProductOptimizeRequest",
    "ProductOptimizeResponse",
    "ProductRead",
    "ProductVariantRead",
    "ProductVariantUpdateRequest",
    "ProductVersionRead",
    "ProductWorkspaceCounts",
]
