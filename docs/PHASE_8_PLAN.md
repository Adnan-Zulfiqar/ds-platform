# Phase 8 plan — Shopify sales-channel integration

| | |
|---|---|
| Date | 2026-08-01 |
| Branch | `develop` |
| Base tag | `phase-7-complete` (`1ed5477`) |
| Decision | Option A from `STORE_CHANNEL_DECISION.md` |

---

## Goal

Connect Shopify as the first external sales channel: OAuth, product publish,
inventory/price push, order import foundation, webhooks, Celery jobs, and UI —
without fulfilling orders yet and without claiming live Shopify verification
unless credentials exist.

---

## Architecture

```
api/integrations/shopify/*  →  ShopifyService  →  ShopifyClient
                              ↘ Store / StoreListing / Order repos
tasks/integrations/shopify.py → same services (Celery)
```

| Concern | Approach |
|---|---|
| Credentials | Dedicated `shopify_connections` table; Fernet ciphertext; never in responses |
| Store row | OAuth creates/updates a `Store` (`platform=shopify`) bound 1:1 to the connection |
| Product channel IDs | `store_listings` — keeps supplier `products.external_id` clean |
| Orders | `OrderSource.SHOPIFY` + `orders.store_id`; import only, no fulfilment |
| Webhooks | HMAC-SHA256 (`X-Shopify-Hmac-SHA256`); Redis replay; no mutation without verify |
| Multi-tenancy | Connection and listing repos are tenant-scoped; callback state carries tenant in Redis |

Mirror AliExpress layering under `app/integrations/shopify/`. Do **not**
genericize into one integrations bag.

---

## Delivery slices

1. **Schema** — migration `0008`: `shopify_connections`, `store_listings`,
   `orders.store_id`, enum values `shopify` on order/product sources as needed
2. **OAuth + client** — install URL, callback HMAC, token exchange, disconnect,
   status, Admin API client with retries/timeouts
3. **Sync services** — publish product (idempotent via listing), push inventory,
   push price, import orders (webhook + poll)
4. **Webhooks** — register on connect; verify; product/inventory/order topics
5. **Celery** — sync/publish/inventory/price/orders/health tasks + beat entries
6. **Frontend** — Shopify card on integrations; store status; Playwright
7. **Gates + docs + tag**

---

## Risks and honesty

| Risk | Mitigation |
|---|---|
| No Shopify Partner app credentials locally | Unit/integration tests with mocked HTTP; document live gap |
| GraphQL vs REST | REST Admin API for Phase 8 foundation (version setting); GraphQL later if needed |
| Duplicate products | Unique `(tenant_id, store_id, product_id)` on listings; update-in-place |
| Webhook storms | Verify HMAC first; replay NX; shed if needed |
| C1/M15 still partial | Do not block Shopify on Docker; reuse existing Celery patterns |

---

## Out of scope

- Shopify order fulfilment / tracking push
- WooCommerce / other channels
- Billing / Shopify app charges
- Live Partner App Review submission
