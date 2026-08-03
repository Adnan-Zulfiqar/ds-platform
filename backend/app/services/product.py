"""General product management — merchant edits to an already-imported product.

Distinct from :class:`app.services.product_import.ProductImportService`,
which is specifically the AliExpress sync pipeline. This service owns what a
merchant can change about their own catalogue entry after import, regardless
of where the product came from — the same separation
``ProductOptimizationService`` already draws for AI-generated content.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError
from app.core.sanitize import sanitize_html
from app.models.product import Product
from app.repositories.product import ProductRepository
from app.services.base import BaseService


class ProductService(BaseService):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.products = ProductRepository(session)

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


__all__ = ["ProductService"]
