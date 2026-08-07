# Shopify API modernisation status (M24A)

**Date:** 2026-08-07  
**Scope:** Selling-currency authority only — not a full REST→GraphQL rewrite.

## Current configured API version

| Setting | Value |
|---|---|
| `SHOPIFY_API_VERSION` / `settings.shopify.api_version` | **2026-07** |

Shared by REST and GraphQL URL builders (`ShopifyClient._url`).

## Changed in M24A

| Path | Transport | Version |
|---|---|---|
| `POST /admin/api/2026-07/graphql.json` — `query ShopCurrency { shop { currencyCode } }` | **GraphQL (new)** | 2026-07 |
| `POST /api/v1/stores/{store_id}/currency/refresh` | App API → GraphQL | 2026-07 |

## Still on REST (unchanged behaviour, now hitting 2026-07 via shared config)

Product sync, inventory, orders, webhooks registration/cleanup, token revoke —
all continue to use `ShopifyClient` REST helpers (`/products.json`,
`/webhooks.json`, `/api_permissions/current.json`, etc.).

These previously targeted **2025-01** (retired baseline). They now use the
shared **2026-07** version string. Endpoints were **not** rewritten to GraphQL
in this tranche.

## Recommended follow-up

1. Inventory each REST call site under `app/integrations/shopify/`.
2. Migrate high-churn surfaces (products, inventory levels, orders) to Admin
   GraphQL with typed operations and Decimal-safe JSON parsing.
3. Keep OAuth token exchange on its current non-versioned endpoint.
4. Track remaining debt in this file until REST is gone for merchant data paths.

## Explicit non-goals of M24A

- Full Shopify integration rewrite
- Presentment-currency multi-market pricing
- M24B (AliExpress ship-to / GBP mapping) and M24C (fees / tax / landed cost)
