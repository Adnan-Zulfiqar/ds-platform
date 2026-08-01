# Shopify integration

| | |
|---|---|
| Phase | 8 |
| Status | Implemented (live OAuth unverified without Partner app credentials) |

## Overview

Shopify is the first **sales channel**. AliExpress remains the supplier. A tenant
can connect one or more Shopify shops; each shop creates a `Store` row
(`platform=shopify`) and a `shopify_connections` row holding the encrypted
access token.

## Configuration

```
SHOPIFY_API_KEY=
SHOPIFY_API_SECRET=
SHOPIFY_SCOPES=read_products,write_products,read_inventory,write_inventory,read_orders,read_locations
SHOPIFY_API_VERSION=2025-01
SHOPIFY_CALLBACK_URL=https://api.example.com/api/v1/integrations/shopify/callback
SHOPIFY_FRONTEND_RETURN_URL=https://app.example.com/settings/integrations
SHOPIFY_WEBHOOK_CALLBACK_BASE=https://api.example.com/api/v1/integrations/shopify/webhooks
```

Never put secrets in `NEXT_PUBLIC_*` variables.

## OAuth

1. `POST /api/v1/integrations/shopify/connect` `{ "shop": "mystore.myshopify.com" }`
2. Browser visits Shopify consent URL (state stored in Redis).
3. `GET /api/v1/integrations/shopify/callback` verifies HMAC, exchanges code,
   encrypts token, creates/updates Store + connection, registers webhooks.
4. Redirect to frontend `?shopify=connected|failed|denied`.

## Sync

| Direction | Mechanism |
|---|---|
| Product publish | `POST /integrations/shopify/publish` — idempotent via `store_listings` |
| Inventory push | Celery `shopify.push_inventory` / sync service |
| Price push | Celery `shopify.push_price` — records previous/new/reason in logs |
| Order import | Poll `shopify.sync_orders_*` + webhooks `orders/create|updated` |

Fulfilment push is **out of scope** for Phase 8.

## Webhooks

HMAC (`X-Shopify-Hmac-SHA256`) required. Replay protection via Redis NX.
Topics registered on connect: products/update, inventory_levels/update,
orders/create, orders/updated.

## Security

- Tokens Fernet-encrypted at rest
- No credential fields on response schemas
- Tenant isolation on connection and listing repositories
- Disconnect hard-deletes the connection row
