# Track E7 — additional channels: WooCommerce plan

Status: **plan.** Etsy and TikTok Shop are blocked (B-015). WooCommerce
proceeds in the stages below.

## Why WooCommerce first

- **Etsy (Open API v3)** needs an app that Etsy approves for commercial
  access.
- **TikTok Shop** needs Partner Center registration and app review.

Both are owner registrations with the platform, and both have OAuth consent
that only a real account can click through. B-015 holds the owner checklist.

WooCommerce needs nothing from a platform owner. Each merchant creates REST
API keys in their own WordPress admin, or approves the standard
`/wc-auth/v1/authorize` flow. That makes it buildable and testable with a
faked transport, like eBay C0–C6 before it.

## What already exists (do not duplicate)

- `StorePlatform.WOOCOMMERCE` and the Postgres `store_platform` value (0006).
  No enum migration is needed for stores.
- `StoreListing` is shared by channels. It records external product id,
  variant map, status and content source.
- `app/core/encryption.py` provides `encrypt`/`decrypt` for credentials.
- The eBay after-commit price/quantity push
  (`push_price_quantity_after_commit`) and its six-hourly sweep.
- Data-subject erasure: `USER_REFERENCES` and
  `WorkspaceClosureService.CONNECTION_MODELS`.

## Architecture change, stated before code (hard stop 6)

`publish_readiness.evaluate()` treats **every non-eBay platform as Shopify**.
It has an `else` branch where Shopify should be an explicit case. If
WooCommerce were simply added to the channel map, it would be checked
against Shopify's rules. That is a silent correctness bug.

**W0 changes this:**

- Each channel gets its own explicit branch.
- An unknown platform raises.
- No behaviour changes for Shopify or eBay, and their existing tests must
  pass unchanged.

This is the only change to existing architecture in E7. Other platform
branches with the same pattern are listed in W0 and made explicit in the
same way:

- the store-currency trust in `pricing_engine` and `import_destination`;
- the `product_pipeline` channel.

## Stages (one PR each)

| Stage | Scope | Migration |
|---|---|---|
| **W0** | Make the platform branches explicit; refuse unknown platforms. Pure refactor, existing tests prove no change | none |
| **W1** | Connect and disconnect. A store URL plus consumer key/secret pasted by the merchant, or the `/wc-auth` approval flow. The URL is validated: https only, no private/loopback addresses (SSRF guard) and a `system_status` check. Keys are encrypted. `woocommerce_connections` (one per store, tenant-scoped). Settings card. Joins erasure and workspace closure | new table |
| **W2** | Publish a draft as a simple or variable product (`/wp-json/wc/v3/products`). Adopt-by-SKU so a retry never duplicates. Readiness rules for WooCommerce (price, currency = store currency from `/settings/general`, images by URL) | none |
| **W3** | Price and stock push: generalise the eBay after-commit hook into a per-channel dispatcher rather than adding a third copy. Plus the sweep (the B-011 pattern needs an ids-only sweep class, which needs **owner approval** like B-011) | none |
| **W4** | Order import. Webhooks (`order.created/updated`, HMAC-SHA256 with the per-connection secret) plus a polling backstop. `ALTER TYPE order_source ADD VALUE 'woocommerce'` | enum value |
| **W5** | Mark shipped: an order note plus status `completed`. WooCommerce core has no tracking field; the tracking plugins differ, so core only is supported, with that limitation stated | none |

## Security notes

- **The merchant supplies the URL.** Every outbound call goes through one
  client that resolves the host and refuses private, loopback and link-local
  ranges. It refuses redirects to them as well. Otherwise the connect form is
  an SSRF probe into our network.
- **Keys** are encrypted at rest and never returned, logged or included in
  error details.
- **Webhooks** are verified by the HMAC before parsing. An unknown or
  unverifiable delivery gets 401 and no database work.

## Owner items

- **B-015:** Etsy and TikTok Shop app registrations (checklist in BLOCKERS).
- A real WooCommerce test store (any WordPress host with WooCommerce) for
  the live check after W2. The agent tests against a faked transport only.
- **W3 sweep class approval:** the same question as B-011, asked when W3
  starts.
