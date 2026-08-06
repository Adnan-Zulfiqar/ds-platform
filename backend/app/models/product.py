"""Product catalogue.

The domain model for an imported product. **Nothing here imports the AliExpress
package**, and no column is named after an AliExpress field. The integration
adapter translates their payload into these columns; the catalogue does not know
where a product came from beyond a `source` value and an external identifier.

That separation is what lets a second supplier arrive without a migration: eBay
or CJ products land in the same tables with a different `source`, and everything
above — pricing, listing, orders — is unchanged.

Money is ``Numeric``, never a float column. A float column loses exactness at
the database boundary no matter how careful the application is above it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import TenantScopedBase

#: Money precision. 12 integer digits before 4 decimal places: enough for any
#: realistic price, and four decimals because supplier prices and FX conversions
#: carry sub-cent precision that must not be rounded away until display.
_MONEY = Numeric(16, 4)


class ProductSource(StrEnum):
    """Where a product came from.

    A column rather than separate tables per supplier: unlike credentials, which
    genuinely differ in shape per marketplace, an imported product has the same
    shape everywhere — title, images, variants, price, stock.
    """

    ALIEXPRESS = "aliexpress"
    MANUAL = "manual"
    SHOPIFY = "shopify"


class ProductStatus(StrEnum):
    """Where a product sits in its lifecycle.

    ``DRAFT`` is the state after import and before a human has approved it.
    Imported data is supplier-authored — titles stuffed with keywords, images of
    varying quality — so nothing goes live without review. Making that the
    default rather than an opt-in is the point.
    """

    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"
    UNAVAILABLE = "unavailable"


class ImportStatus(StrEnum):
    """Outcome of a single import attempt."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class ProductAIStatus(StrEnum):
    """Where a product sits relative to AI optimisation.

    Two terminal values plus the unstarted default — no `pending`/
    `generating`. Phase 9 stage 3's optimisation call is a single synchronous
    request; a Celery-backed asynchronous path (stage 9) is what would need an
    in-flight state, and adding one now would be a status this stage never
    writes.
    """

    NOT_OPTIMIZED = "not_optimized"
    OPTIMIZED = "optimized"
    FAILED = "failed"


class ProductVersionSource(StrEnum):
    """What produced a `ProductVersion` row."""

    ORIGINAL = "original"
    AI_GENERATED = "ai_generated"


class Product(TenantScopedBase):
    """A product in a tenant's catalogue."""

    __tablename__ = "products"

    __table_args__ = (
        # The idempotency guarantee. Importing the same supplier product twice
        # updates rather than duplicates, and the database enforces it rather
        # than trusting every call site to check first.
        #
        # Scoped to the tenant: two tenants importing the same AliExpress
        # product must each get their own row, because they will price and
        # describe it differently.
        UniqueConstraint(
            "tenant_id",
            "source",
            "external_id",
            name="uq_products_tenant_source_external",
        ),
        # Leading with tenant_id on every index: a query that forgets the tenant
        # filter then cannot use the index, so the mistake shows up as a slow
        # query in testing rather than as silently correct-looking output.
        Index("ix_products_tenant_status", "tenant_id", "status"),
        Index("ix_products_tenant_created", "tenant_id", "created_at"),
        Index("ix_products_tenant_category", "tenant_id", "category_id"),
        # Postgres treats multiple NULLs as distinct under a unique
        # constraint, so unoptimised products (no slug yet) never collide —
        # this only starts enforcing once a tenant actually sets one.
        UniqueConstraint("tenant_id", "slug", name="uq_products_tenant_slug"),
    )

    # --- Provenance ---------------------------------------------------------
    source: Mapped[ProductSource] = mapped_column(
        Enum(
            ProductSource,
            name="product_source",
            # Without this SQLAlchemy persists member *names* ("ALIEXPRESS"),
            # not values, and every insert fails against the value-based type.
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=ProductSource.ALIEXPRESS,
    )

    #: The supplier's identifier, as a string. AliExpress returns an integer but
    #: expects a string on the way back in, and other suppliers use opaque
    #: strings — so the widest representation wins.
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)

    #: The supplier's canonical id where it differs from the one queried.
    #: AliExpress returns both, and they are not interchangeable.
    external_canonical_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    external_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # --- Catalogue ----------------------------------------------------------
    #
    # `title`, `brand`, and `description` share one pattern (Product Editor
    # stages 1-2): each has a `supplier_*` twin that always reflects the
    # supplier's current value, while the bare column is merchant-editable
    # and only refreshed by a sync while it still equals its twin — see
    # `ProductImportService._upsert`. `category_name`/`vendor`/`tags`/SEO
    # fields below need no such protection: `map_product` never sets them,
    # so a sync never touches them regardless.
    title: Mapped[str] = mapped_column(String(512), nullable=False)

    #: The supplier's title as of the last sync. Always overwritten on
    #: import/refresh; not itself editable.
    supplier_title: Mapped[str | None] = mapped_column(String(512), nullable=True)

    #: The merchant-editable description. Sanitized HTML
    #: (``app.core.sanitize.sanitize_html``) — never raw supplier markup.
    #: Seeded from ``supplier_description`` on first import. A sync only
    #: refreshes it while it still equals ``supplier_description`` (see
    #: ``ProductImportService._upsert``); once a merchant edit diverges the
    #: two, sync stops touching this column so the edit is never silently
    #: overwritten.
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: The supplier's own description, as of the last sync. Always
    #: overwritten on every import/refresh — this is deliberately *not*
    #: editable, so there is always an answer to "what does the supplier
    #: currently say" independent of whatever the merchant has changed
    #: ``description`` to. Sanitized the same way as ``description``.
    supplier_description: Mapped[str | None] = mapped_column(Text, nullable=True)

    category_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    category_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    brand: Mapped[str | None] = mapped_column(String(255), nullable=True)

    #: The supplier's brand attribute as of the last sync. Same protection
    #: as `supplier_title`.
    supplier_brand: Mapped[str | None] = mapped_column(String(255), nullable=True)

    status: Mapped[ProductStatus] = mapped_column(
        Enum(
            ProductStatus,
            name="product_status",
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=ProductStatus.DRAFT,
    )

    # --- Supplier pricing ---------------------------------------------------
    #
    # What the supplier charges. Sell price is derived by the pricing engine and
    # audited in ``price_changes``; it is stored here so list views do not join.
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    cost_price_min: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    cost_price_max: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    sell_price: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)

    #: Optional sales-channel assignment. Null means the product is in the
    #: catalogue but not mapped to a store yet.
    store_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # --- Inventory ----------------------------------------------------------
    stock_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # --- Supplier reputation ------------------------------------------------
    supplier_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    supplier_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    rating: Mapped[Decimal | None] = mapped_column(Numeric(3, 2), nullable=True)
    review_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    order_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- Sync state ---------------------------------------------------------
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_error: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # --- SEO (Phase 9 stage 3) -----------------------------------------------
    seo_title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    seo_description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    #: Comma-separated, matching how a `<meta name="keywords">` tag is
    #: actually rendered — not an array for a value that is one string on the
    #: way out.
    meta_keywords: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Marketplace (Phase 9 stage 3) ---------------------------------------
    #
    # `brand` already exists above (Phase 4, supplier-populated) and is reused
    # rather than duplicated.
    slug: Mapped[str | None] = mapped_column(String(255), nullable=True)
    vendor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: `server_default` alongside the Python-side `default`, unlike most
    #: columns in this file — this one adds a NOT NULL column to a table the
    #: migration expects to already hold rows (existing imported products),
    #: and `ALTER TABLE ... ADD COLUMN ... NOT NULL` needs a database-side
    #: default to backfill them or the migration fails outright.
    tags: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )

    # --- AI optimisation status (Phase 9 stage 3) ----------------------------
    #
    # These five columns are a cache of the currently active `ProductVersion`,
    # not independent state. Activating a version (see `ProductVersion`
    # below) always rewrites all five together from that version's data;
    # nothing else in the application assigns to them — which is what
    # guarantees `title`/`description` above can never be overwritten by AI
    # content, since no code path does so.
    ai_status: Mapped[ProductAIStatus] = mapped_column(
        Enum(
            ProductAIStatus,
            name="product_ai_status",
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=ProductAIStatus.NOT_OPTIMIZED,
        # Same reasoning as `tags.server_default` above.
        server_default=text("'not_optimized'"),
    )
    ai_last_generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ai_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: The active `ProductVersion.version_number`, denormalised so a list or
    #: detail view never needs to join `product_versions` to show it.
    ai_version: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- AI-optimised content (Phase 9 stage 3) ------------------------------
    #
    # Populated only when the active version's source is AI_GENERATED; a
    # rollback to the ORIGINAL version clears both back to `None`.
    optimized_title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    optimized_description: Mapped[str | None] = mapped_column(Text, nullable=True)

    variants: Mapped[list[ProductVariant]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    images: Mapped[list[ProductImage]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ProductImage.position",
    )

    @property
    def is_in_stock(self) -> bool:
        return self.stock_quantity > 0


class ProductVariant(TenantScopedBase):
    """A purchasable variation — a size, a colour, a combination.

    Carries ``tenant_id`` of its own rather than relying on the parent's. It is
    denormalised, but it means a variant query is tenant-filtered by the same
    base repository as everything else instead of depending on a join being
    written correctly every time.
    """

    __tablename__ = "product_variants"

    __table_args__ = (
        UniqueConstraint("product_id", "external_variant_id", name="uq_variants_product_external"),
        Index("ix_variants_tenant_product", "tenant_id", "product_id"),
    )

    product_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    external_variant_id: Mapped[str] = mapped_column(String(128), nullable=False)

    #: The supplier's composite variant key, stored **verbatim**. AliExpress
    #: requires this exact string when an order is placed; rebuilding it from
    #: the parsed attributes would be a guess, and a wrong guess buys the wrong
    #: item.
    external_attributes: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    #: Human-readable label, e.g. "Color: Beige / Material: CANVAS".
    label: Mapped[str | None] = mapped_column(String(512), nullable=True)

    cost_price: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    list_price: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    stock_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    image_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    #: Merchant listing SKU — never overwrite ``external_variant_id``.
    merchant_sku: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: Channel selling price. Distinct from ``list_price`` (supplier reference).
    sell_price: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    compare_at_price: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    product: Mapped[Product] = relationship(back_populates="variants")


class ProductImage(TenantScopedBase):
    """A product image, kept as a URL rather than a copied file.

    Copying supplier images into our own storage would mean holding
    seller-owned media and paying to serve it. A URL is enough until a supplier
    rotates one, at which point the next sync replaces it.
    """

    __tablename__ = "product_images"

    __table_args__ = (
        UniqueConstraint("product_id", "url", name="uq_images_product_url"),
        Index("ix_images_tenant_product", "tenant_id", "product_id"),
    )

    product_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    url: Mapped[str] = mapped_column(String(1024), nullable=False)

    #: Ordering is meaningful: position 0 is the image a listing leads with.
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    alt_text: Mapped[str | None] = mapped_column(String(512), nullable=True)
    #: Supplier-imported images may be refreshed; merchant-added URLs are not
    #: deleted or reordered by sync.
    is_supplier: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    product: Mapped[Product] = relationship(back_populates="images")


class ProductImport(TenantScopedBase):
    """One import attempt, successful or not.

    A separate table rather than columns on ``Product`` because a failed import
    has no product to hang off — and the failures are the rows worth reading.
    "Which imports failed last night and why" is unanswerable if only successes
    leave a trace.

    This is also the audit trail: it records who asked, for what, when, and what
    happened.
    """

    __tablename__ = "product_imports"

    __table_args__ = (
        Index("ix_imports_tenant_status", "tenant_id", "status"),
        Index("ix_imports_tenant_created", "tenant_id", "created_at"),
    )

    source: Mapped[ProductSource] = mapped_column(
        Enum(
            ProductSource,
            name="product_source",
            values_callable=lambda enum: [member.value for member in enum],
            # The type is created by the products table in the same migration;
            # emitting the CREATE TYPE twice would fail.
            create_type=False,
        ),
        nullable=False,
        default=ProductSource.ALIEXPRESS,
    )

    external_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)

    status: Mapped[ImportStatus] = mapped_column(
        Enum(
            ImportStatus,
            name="import_status",
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=ImportStatus.PENDING,
    )

    #: Set once the import produces a product. Nullable because a failure never
    #: will. SET NULL rather than CASCADE so deleting a product does not erase
    #: the record that it was imported.
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("products.id", ondelete="SET NULL"),
        nullable=True,
    )

    #: Who triggered it. SET NULL so removing a user does not destroy the audit
    #: trail of what they did.
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    #: A stable machine-readable reason, so a caller can branch on it. The
    #: message below is for humans and may change wording freely.
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def is_finished(self) -> bool:
        return self.status in (
            ImportStatus.SUCCEEDED,
            ImportStatus.FAILED,
            ImportStatus.SKIPPED,
        )


class ProductVersion(TenantScopedBase):
    """One version of a product's optimisable content.

    Versions are rows, not a nested history table — the same shape Phase 9
    stage 2 uses for `AIPrompt`. Version 1 is always a snapshot of the
    product exactly as its supplier described it (`source=ORIGINAL`),
    created lazily on first optimisation rather than at import time — see
    `ProductOptimizationService`. Versions 2 and up are AI-generated. History
    is every row sharing a `product_id`; rollback is activating an older
    version through the same mechanism that activates a new one.

    Immutable once written: nothing in this codebase updates `content` after
    creation. A correction is a new version, never an edit to an old one —
    an audit trail that can be rewritten is not one.
    """

    __tablename__ = "product_versions"

    __table_args__ = (
        UniqueConstraint("product_id", "version_number", name="uq_product_versions_product_number"),
        Index("ix_product_versions_tenant_product", "tenant_id", "product_id"),
        # Partial unique index: at most one row per product may have
        # `active = true`, enforced by the database rather than a
        # read-then-write check — identical mechanism to
        # `uq_ai_prompts_name_active` in migration 0010.
        Index(
            "uq_product_versions_product_active",
            "product_id",
            unique=True,
            postgresql_where=text("active"),
        ),
    )

    #: Tenant-owned data, unlike `PromptExecution.prompt_id` which points at
    #: a reference table — so this cascades like `ProductVariant`/
    #: `ProductImage` already do, rather than SET NULL.
    product_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[ProductVersionSource] = mapped_column(
        Enum(
            ProductVersionSource,
            name="product_version_source",
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
    )

    #: `{"title": ..., "description": ...}` today. JSONB rather than discrete
    #: columns so a later stage can add `seoTitle`/`seoDescription`/`tags`
    #: to the shape without a migration — the same reasoning
    #: `AutomationRule.config` already established in this codebase.
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    #: Null for ORIGINAL. Copied from the provider that produced this
    #: version, the same denormalisation `PromptExecution.provider` uses.
    ai_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)

    #: Links to the `PromptExecution` that produced this version, so the
    #: full record (prompt, rendered text, provider, tokens, status) is
    #: reachable without duplicating any of those columns here. Two
    #: executions (title, description) currently produce one version; this
    #: points at one of them as the representative link.
    prompt_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("prompt_executions.id", ondelete="SET NULL"),
        nullable=True,
    )

    #: Null for the lazily-created original snapshot, which no user
    #: explicitly requested. SET NULL so removing a user does not erase the
    #: record that they triggered an optimisation.
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


__all__ = [
    "ImportStatus",
    "Product",
    "ProductAIStatus",
    "ProductImage",
    "ProductImport",
    "ProductSource",
    "ProductStatus",
    "ProductVariant",
    "ProductVersion",
    "ProductVersionSource",
]
