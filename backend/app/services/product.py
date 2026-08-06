"""General product management — merchant edits to an already-imported product.

Distinct from :class:`app.services.product_import.ProductImportService`,
which is specifically the AliExpress sync pipeline. This service owns what a
merchant can change about their own catalogue entry after import, regardless
of where the product came from — the same separation
``ProductOptimizationService`` already draws for AI-generated content.
"""

from __future__ import annotations

import uuid
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

    async def update_product(self, product_id: uuid.UUID, changes: dict[str, Any]) -> Product:
        """Apply merchant-supplied changes to a product's editable fields.

        ``changes`` is expected to already be reduced to only the fields the
        caller actually sent (``model_dump(exclude_unset=True)``) — every key
        present here is written, so an absent key must mean "no change," not
        "clear it."
        """
        product = await self.products.get_by_id_or_raise(product_id)

        if "description" in changes:
            # Merchant-submitted text is untrusted the same way supplier text
            # is. Sanitized here, once, before storage — never at render
            # time — the same policy `ProductImportService` already applies
            # to the supplier's own description.
            changes["description"] = sanitize_html(changes["description"])

        new_slug = changes.get("slug")
        if new_slug:
            owner = await self.products.get_by_slug(new_slug)
            if owner is not None and owner.id != product_id:
                raise ConflictError("That URL slug is already used by another product.")

        for field, value in changes.items():
            setattr(product, field, value)

        await self.flush()
        return product

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
