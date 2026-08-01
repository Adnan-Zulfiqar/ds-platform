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

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
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
    title: Mapped[str] = mapped_column(String(512), nullable=False)

    #: Supplier description. **Stored as text, never rendered as HTML.**
    #: AliExpress returns seller-authored markup; rendering it unsanitised would
    #: be stored XSS. Phase 4 has no sanitiser, so this is carried but not shown.
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    category_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    category_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    brand: Mapped[str | None] = mapped_column(String(255), nullable=True)

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


__all__ = [
    "ImportStatus",
    "Product",
    "ProductImage",
    "ProductImport",
    "ProductSource",
    "ProductStatus",
    "ProductVariant",
]
