# StoreListing sync field authority (UX-L2C-R2)

This document records which backend paths write `StoreListing` sync fields and
whether `lastSyncedAt` can authorize “Up to date on Shopify” in the frontend.

## Writer table

| Operation | File / function | `last_synced_at` | `published_at` | `online_store_published` | `status` | Full product body to Shopify? |
|---|---|---|---|---|---|---|
| Initial publish | `backend/app/integrations/shopify/sync.py` → `publish_product()` success | Set to `now` | Set when visibility signal is active | Set from Shopify response | `SYNCED` | Yes |
| Re-publish / update | Same | Set to `now` | Updated on active visibility | Updated from response | `SYNCED` | Yes |
| Publish failure | `publish_product()` except block | Unchanged | Unchanged | Unchanged | `ERROR` + `last_failed_sync_at` | No |
| Inventory-only sync | `push_inventory()` | Set to `now` | Unchanged | Unchanged | `SYNCED` | **No** — inventory levels only |
| Price-only sync | `push_price()` | Set to `now` | Unchanged | Unchanged | `SYNCED` | **No** — variant prices only |
| Missing listing on partial sync | `push_inventory()` / `push_price()` when no listing | Via `publish_product()` fallback | Via publish path | Via publish path | Via publish path | Yes (fallback publish) |
| Adoption retry | `_create_or_adopt()` → `publish_product()` | Via publish success path | Via publish path | Via publish path | Via publish path | Yes when adopted through publish |
| Celery background | `backend/app/tasks/integrations/shopify.py` | Delegates to sync service above | Same | Same | Same | Depends on task |
| API read | `backend/app/api/v1/drafts/router.py` → listings GET | Read only | Read only | Read only | Read only | N/A |
| Repository CRUD | `backend/app/repositories/shopify.py` → `StoreListingRepository` | Only via sync service callers | Same | Same | Same | N/A |

Paths that **do not** write `StoreListing`:

- Disconnect / reconnect (`ShopifyConnection` only; listings remain until explicit publish/sync).
- Supplier refresh (`Product.last_synced_at` in `product_import.py` / `tasks/products.py` — different model field; advances draft `updatedAt` but not listing sync timestamp).
- Order import (`import_orders()`).

## Authority conclusion

**`lastSyncedAt` does not prove the complete current DropPilot draft was sent
through the full Shopify product publish/update path.** Inventory and price
pushes advance `lastSyncedAt` without sending title, description, media, or
other product-body fields.

Therefore the frontend must **not** use timestamp comparison alone to show
“Up to date on Shopify”. Use:

- **“Visible on your shop”** only when `onlineStorePublished === true` (server-confirmed visibility).
- **“Added to Shopify”** + conservative copy when a synced listing exists but full draft sync cannot be proven.
- **“Changes saved in DropPilot — not sent to Shopify”** when `draftUpdatedAt > lastSyncedAt`.

Review changes / Update Shopify remain available whenever a synced listing exists.
