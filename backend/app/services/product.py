"""General product management — merchant edits to an already-imported product.

Distinct from :class:`app.services.product_import.ProductImportService`,
which is specifically the AliExpress sync pipeline. This service owns what a
merchant can change about their own catalogue entry after import, regardless
of where the product came from — the same separation
``ProductOptimizationService`` already draws for AI-generated content.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.sanitize import sanitize_html
from app.models.product import Product, ProductImage, ProductVariant
from app.repositories.product import (
    ProductImageRepository,
    ProductRepository,
    ProductVariantRepository,
)
from app.services.base import BaseService


def _validate_image_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValidationError("Image URL must be an absolute http(s) URL.")
    if len(url) > 1024:
        raise ValidationError("Image URL is too long.")
    return url.strip()


class ProductService(BaseService):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.products = ProductRepository(session)
        self.images = ProductImageRepository(session)
        self.variants = ProductVariantRepository(session)

    async def update_product(
        self,
        product_id: uuid.UUID,
        changes: dict[str, Any],
        *,
        expected_updated_at: datetime | None = None,
    ) -> Product:
        """Apply merchant-supplied changes to a product's editable fields.

        ``changes`` is expected to already be reduced to only the fields the
        caller actually sent (``model_dump(exclude_unset=True)``) — every key
        present here is written, so an absent key must mean "no change," not
        "clear it."

        ``expected_updated_at`` (M2A), when supplied, turns this into a
        compare-and-swap: the write only lands if the row's ``updated_at``
        still equals what the caller last saw, enforced atomically by
        :meth:`ProductRepository.update_if_unmodified_since`. This is the
        platform's optimistic-concurrency mechanism — deliberately reusing
        the existing, database-generated ``updated_at`` column
        (``TimestampMixin``) instead of adding a dedicated version column,
        since it is already atomic and already present on every tenant-scoped
        table. See ``docs/dsers-parity/M2_PREMIUM_EDITOR.md``. Omitting it
        preserves the exact pre-M2A behaviour (last write wins) for any
        caller that does not yet send it.
        """
        product = await self.products.get_by_id_or_raise(product_id)

        # A true no-op — nothing in `changes` actually differs from the
        # stored row — is filtered out before any write is attempted.
        # Issuing an UPDATE anyway would still bump `updated_at` (Postgres
        # fires `onupdate` for any UPDATE statement regardless of whether
        # values changed), which would misrepresent "last edited" and could
        # spuriously invalidate a concurrent editor's still-valid version
        # token for a save that changed nothing.
        effective_changes = {
            field: value
            for field, value in changes.items()
            if getattr(product, field, object()) != value
        }

        if "description" in effective_changes:
            # Merchant-submitted text is untrusted the same way supplier text
            # is. Sanitized here, once, before storage — never at render
            # time — the same policy `ProductImportService` already applies
            # to the supplier's own description.
            effective_changes["description"] = sanitize_html(effective_changes["description"])

        new_slug = effective_changes.get("slug")
        if new_slug:
            owner = await self.products.get_by_slug(new_slug)
            if owner is not None and owner.id != product_id:
                raise ConflictError("That URL slug is already used by another product.")

        if not effective_changes:
            return product

        if expected_updated_at is not None:
            updated = await self.products.update_if_unmodified_since(
                product_id,
                expected_updated_at=expected_updated_at,
                **effective_changes,
            )
            if not updated:
                raise ConflictError(
                    "This item was changed since you loaded it. Reload to see "
                    "the latest version before saving again.",
                )
            return await self._reload_product(product_id)

        for field, value in effective_changes.items():
            setattr(product, field, value)

        await self.flush()
        return product

    async def update_draft(
        self,
        product_id: uuid.UUID,
        changes: dict[str, Any],
        *,
        expected_updated_at: datetime | None = None,
    ) -> Product:
        """Save merchant edits from the Drafts editor (M2A / M2A acceptance).

        Same write path as :meth:`update_product`, plus two rules specific
        to this surface:

        1. **`expected_updated_at` is mandatory here** (acceptance-pass
           hardening — it stayed optional on the shared schema, but this
           method is the only caller that may omit it, and this method no
           longer allows that). A draft is edited by exactly the kind of
           multi-tab, walk-away-and-come-back workflow optimistic
           concurrency exists for; letting a save silently skip the check
           because a client forgot to send the field would defeat the whole
           M2A guarantee for the one surface it was built for. Checked
           before any database lookup, so the response does not depend on
           whether the id exists or belongs to this tenant — a missing
           token gets the identical 422 either way, leaking nothing.
        2. A product that has already been published to a channel is
           edited from the Products detail view, not through this
           drafts-only endpoint — publishing is what promotes a row out of
           Drafts in the first place (``ProductRepository._synced_listing_exists``),
           so editing it back through ``/drafts/{id}`` would be editing a
           "draft" that no longer is one.

        A missing or foreign ``product_id`` is deliberately **not**
        distinguished here — it falls through to ``update_product``'s own
        ``get_by_id_or_raise``, which already returns the correct 404 for
        both "does not exist" and "belongs to another tenant". Checking
        publication first would leak "it exists but you can't see it" as a
        different response shape than "it doesn't exist" for a foreign id.

        Other candidate not-editable states from the M2A brief — an import
        still in flight, or one that failed — were audited against
        ``ProductImportService.import_product`` and found not to apply here:
        a ``Product`` row is only ever created or updated by ``_upsert``
        *after* a supplier fetch succeeds, inside the same request-scoped
        transaction that fails and rolls back entirely on any error
        (``get_db_session``). There is no reachable state where a persisted,
        navigable draft row corresponds to an in-progress or failed import —
        those exist only as ``ProductImport`` audit rows with no
        ``product_id``, surfaced in Import History, never as an editable
        draft. Inventing a gate for a state this data model cannot produce
        would be speculative, not defensive.

        **`/products/{id}`'s own PATCH deliberately keeps the token
        optional and does not gate on publication.** Audited (M2A
        acceptance pass) rather than assumed: no frontend code calls it —
        `frontend/services/products.ts` only ever `GET`s a product by id —
        and it predates M2A as a general "edit any imported product"
        endpoint (`ProductService`'s own module docstring: "merchant edits
        to an already-imported product", not "already-published"), already
        exercised by `test_product_update.py` against freshly-imported
        (unpublished) products as its normal case. Restricting it to
        published-only products would break that established, intentional,
        pre-M2A behaviour to close a path that grants no privilege a caller
        doesn't already have — reaching either endpoint requires the same
        tenant-scoped admin authentication. Left unchanged; recorded here
        as an audited, deliberate decision rather than an oversight.
        """
        if expected_updated_at is None:
            raise ValidationError(
                "expectedUpdatedAt is required to save a draft. Reload the "
                "draft to get its current version, then save again — this "
                "is what lets the server tell a stale save apart from a "
                "current one instead of silently overwriting a newer "
                "change.",
            )

        found = await self.products.get_by_id_with_publication(product_id)
        if found is not None:
            _, is_published = found
            if is_published:
                raise ConflictError(
                    "This product has already been published and is no "
                    "longer a draft. Edit it from Products instead.",
                )
        return await self.update_product(
            product_id, changes, expected_updated_at=expected_updated_at
        )

    async def add_image(
        self,
        product_id: uuid.UUID,
        *,
        url: str,
        alt_text: str | None = None,
    ) -> Product:
        await self.products.get_by_id_or_raise(product_id)
        clean_url = _validate_image_url(url)
        existing = await self.images.list_for_product(product_id)
        if any(img.url == clean_url for img in existing):
            raise ConflictError("That image URL is already on this product.")
        position = max((img.position for img in existing), default=-1) + 1
        await self.images.create(
            product_id=product_id,
            url=clean_url,
            position=position,
            alt_text=alt_text,
            is_supplier=False,
        )
        await self.flush()
        return await self._reload_product(product_id)

    async def reorder_images(self, product_id: uuid.UUID, image_ids: list[uuid.UUID]) -> Product:
        await self.products.get_by_id_or_raise(product_id)
        existing = await self.images.list_for_product(product_id)
        by_id = {img.id: img for img in existing}
        if set(image_ids) != set(by_id):
            raise ValidationError("Reorder must include every current image id exactly once.")
        for position, image_id in enumerate(image_ids):
            await self.images.update(by_id[image_id], position=position)
        await self.flush()
        return await self._reload_product(product_id)

    async def update_image(
        self,
        product_id: uuid.UUID,
        image_id: uuid.UUID,
        changes: dict[str, Any],
    ) -> Product:
        await self.products.get_by_id_or_raise(product_id)
        image = await self._get_image(product_id, image_id)
        await self.images.update(image, **changes)
        await self.flush()
        return await self._reload_product(product_id)

    async def remove_image(self, product_id: uuid.UUID, image_id: uuid.UUID) -> Product:
        """Soft-delete so a later restore can revive supplier images."""
        await self.products.get_by_id_or_raise(product_id)
        image = await self._get_image(product_id, image_id)
        await self.images.soft_delete(image)
        # Compact positions for remaining live images.
        remaining = await self.images.list_for_product(product_id)
        for position, row in enumerate(sorted(remaining, key=lambda item: item.position)):
            if row.position != position:
                await self.images.update(row, position=position)
        await self.flush()
        return await self._reload_product(product_id)

    async def restore_image(self, product_id: uuid.UUID, image_id: uuid.UUID) -> Product:
        """Restore a soft-deleted supplier image (merchant uploads too)."""
        await self.products.get_by_id_or_raise(product_id)
        # Soft-deleted rows are invisible to `_base_query` — load unscoped by
        # product ownership via a dedicated lookup that still filters tenant.
        image = await self._get_image_including_deleted(product_id, image_id)
        if image.deleted_at is None:
            return await self._reload_product(product_id)
        await self.images.restore(image)
        live = await self.images.list_for_product(product_id)
        max_pos = max((row.position for row in live if row.id != image.id), default=-1)
        await self.images.update(image, position=max_pos + 1)
        await self.flush()
        return await self._reload_product(product_id)

    async def update_variant(
        self,
        product_id: uuid.UUID,
        variant_id: uuid.UUID,
        changes: dict[str, Any],
    ) -> Product:
        await self.products.get_by_id_or_raise(product_id)
        variant = await self._get_variant(product_id, variant_id)
        for money_field in ("sell_price", "compare_at_price"):
            value = changes.get(money_field)
            if value is not None and value < Decimal("0"):
                raise ValidationError(f"{money_field} cannot be negative.")
        if changes.get("image_url"):
            changes["image_url"] = _validate_image_url(changes["image_url"])
        await self.variants.update(variant, **changes)
        await self.flush()
        return await self._reload_product(product_id)

    async def _reload_product(self, product_id: uuid.UUID) -> Product:
        """Drop cached collections so nested images/variants reflect writes."""
        product = await self.products.get_by_id_or_raise(product_id)
        await self.session.refresh(product, attribute_names=["images", "variants"])
        return product

    async def _get_image(self, product_id: uuid.UUID, image_id: uuid.UUID) -> ProductImage:
        for image in await self.images.list_for_product(product_id):
            if image.id == image_id:
                return image
        raise NotFoundError.for_resource("ProductImage", image_id)

    async def _get_image_including_deleted(
        self, product_id: uuid.UUID, image_id: uuid.UUID
    ) -> ProductImage:
        from sqlalchemy import select

        from app.core.context import require_tenant_id

        tenant_id = require_tenant_id()
        query = select(ProductImage).where(
            ProductImage.id == image_id,
            ProductImage.product_id == product_id,
            ProductImage.tenant_id == tenant_id,
        )
        image = (await self.session.execute(query)).scalar_one_or_none()
        if image is None:
            raise NotFoundError.for_resource("ProductImage", image_id)
        return image

    async def _get_variant(self, product_id: uuid.UUID, variant_id: uuid.UUID) -> ProductVariant:
        for variant in await self.variants.list_for_product(product_id):
            if variant.id == variant_id:
                return variant
        raise NotFoundError.for_resource("ProductVariant", variant_id)


__all__ = ["ProductService"]
