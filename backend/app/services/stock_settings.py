"""The workspace's listed-stock setting and the one rule that applies it.

Every channel -- Shopify, eBay, WooCommerce, at publish and on each later
stock push -- asks ``listed_quantity`` what to send, so the rule exists once
and a channel cannot drift from the others.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.inventory import StockSettingsRepository


def listed_quantity(supplier_stock: int | None, publish_quantity: int | None) -> int:
    """What a channel is told is in stock.

    No setting: the supplier's stock, unchanged. With a setting: that
    quantity -- except when the supplier has none, which is listed as 0 so a
    sold-out product stops selling instead of taking orders nobody can
    fulfil (owner decision 2026-10-12).
    """
    stock = max(int(supplier_stock or 0), 0)
    if publish_quantity is None:
        return stock
    return publish_quantity if stock > 0 else 0


class StockSettingsService:
    def __init__(self, session: AsyncSession) -> None:
        self.settings = StockSettingsRepository(session)

    async def publish_quantity(self) -> int | None:
        row = await self.settings.current()
        return row.publish_quantity if row is not None else None

    async def set_publish_quantity(self, publish_quantity: int | None) -> int | None:
        row = await self.settings.current()
        if row is None:
            row = await self.settings.create(publish_quantity=publish_quantity)
        else:
            row = await self.settings.update(row, publish_quantity=publish_quantity)
        return row.publish_quantity


__all__ = ["StockSettingsService", "listed_quantity"]
