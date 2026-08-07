#!/usr/bin/env python
"""Compare Shopify GraphQL shop.currencyCode with persisted Store.currency.

Read-only by default. Pass ``--refresh`` to call the same sync path as the API
refresh endpoint (mutates Store.currency + currency_last_synced_at).

Requires a running DB + a connected Shopify store. Never prints access tokens.

    VERIFY_SHOPIFY_STORE_ID=<uuid>   optional; otherwise first connected Shopify store
    python scripts/verify_shopify_currency.py
    python scripts/verify_shopify_currency.py --refresh
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))


async def _main(*, store_id: uuid.UUID | None, refresh: bool) -> int:
    from sqlalchemy import select

    from app.core.context import set_tenant_id
    from app.core.encryption import decrypt, is_encryption_configured
    from app.database.session import session_factory
    from app.integrations.shopify.client import ShopifyClient
    from app.integrations.shopify.service import ShopifyService
    from app.models.integration import IntegrationStatus
    from app.models.shopify import ShopifyConnection
    from app.models.store import Store, StorePlatform

    if not is_encryption_configured():
        print("SKIP: encryption not configured.")
        return 0

    async with session_factory() as session:
        connection: ShopifyConnection | None = None
        if store_id is not None:
            result = await session.execute(
                select(ShopifyConnection).where(ShopifyConnection.store_id == store_id)
            )
            connection = result.scalar_one_or_none()
        else:
            result = await session.execute(
                select(ShopifyConnection)
                .where(ShopifyConnection.status == IntegrationStatus.CONNECTED)
                .limit(1)
            )
            connection = result.scalar_one_or_none()

        if connection is None:
            print("SKIP: no connected Shopify store found.")
            return 0

        set_tenant_id(connection.tenant_id)
        store = await session.get(Store, connection.store_id)
        if store is None or store.platform is not StorePlatform.SHOPIFY:
            print("SKIP: linked store missing or not Shopify.")
            return 0

        token = decrypt(connection.encrypted_access_token)
        client = ShopifyClient(
            shop_domain=connection.shop_domain,
            access_token=token,
            tenant_id=str(connection.tenant_id),
        )
        shopify_code = await client.fetch_shop_currency_code()
        persisted = store.currency
        synced_at = store.currency_last_synced_at
        match = (
            shopify_code == persisted and synced_at is not None
        )
        print(f"store_id={store.id}")
        print(f"shop_domain={connection.shop_domain}")
        print(f"shopify_currencyCode={shopify_code}")
        print(f"persisted_currency={persisted}")
        print(f"currency_last_synced_at={synced_at}")
        print(f"MATCH={'MATCH' if match else 'MISMATCH'}")

        if refresh:
            service = ShopifyService(session)
            updated = await service.refresh_shop_currency(store.id)
            await session.commit()
            print("--- after refresh ---")
            print(f"persisted_currency={updated.currency}")
            print(f"currency_last_synced_at={updated.currency_last_synced_at}")
            print(
                "MATCH="
                + (
                    "MATCH"
                    if updated.currency == shopify_code
                    and updated.currency_last_synced_at is not None
                    else "MISMATCH"
                )
            )
        return 0 if match or refresh else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store-id", default=os.environ.get("VERIFY_SHOPIFY_STORE_ID"))
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Mutate store currency via GraphQL sync (explicit).",
    )
    args = parser.parse_args()
    store_id = uuid.UUID(args.store_id) if args.store_id else None
    os.chdir(BACKEND_ROOT)
    raise SystemExit(asyncio.run(_main(store_id=store_id, refresh=args.refresh)))


if __name__ == "__main__":
    main()
