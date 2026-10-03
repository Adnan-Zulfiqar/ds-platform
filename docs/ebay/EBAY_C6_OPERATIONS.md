# EBAY-C6 — Production readiness and operations (runbook)

Status: **agent-side hardening implemented; the eBay-side steps are owner
actions and are open.** Nothing here has run against real eBay.

## What changed in the code

| Area | Change |
|---|---|
| Call limits | A 429 from any Sell or Taxonomy call is `ebay_rate_limited` (not "eBay did not answer"), so a merchant reads the right cause and background tasks retry with backoff |
| Health view | `GET /api/v1/integrations/ebay/health` (admin): configured, environment, this workspace's connection state, token expiry, reconnect reason, and the application's remaining Sell API quota from eBay's Developer Analytics API (`null` if that read fails — never a guess) |
| Readiness CLI | New rule `EBAY_SELLER_CHANNEL` (required at activation): SKIPPED when eBay is off; MISSING when partly configured (client secret, RuName, deletion verification token, deletion endpoint), naming what is missing and never a value |

## Before going live on eBay (owner checklist — all OPEN)

1. **Production keyset activation.** eBay keeps a production keyset inactive
   until the marketplace-account-deletion endpoint validates (EBAY-C0, done in
   production per the master roadmap). Confirm in the eBay developer portal
   that the keyset shows *active*.
2. **Readiness CLI.** On the production host:
   `python backend/scripts/verify_production_config.py --env-file <prod env>`
   — `EBAY_SELLER_CHANNEL` must be PASS.
3. **Application Growth Check.** eBay's default daily call limits suit a pilot,
   not "thousands of paying customers". Apply for the Growth Check in the
   developer portal once real traffic exists; it asks for the app's call
   patterns (C4 sends one `bulkUpdatePriceQuantity` per changed product; C5
   imports on demand).
4. **First live seller.** Connect a seller (C1), save listing setup (C2),
   publish one single-variant draft (C3), change its price (C4), import and
   ship one order (C5). Record each with the health view's output — no token,
   no eBay user id.
5. **Approval needed for scheduled jobs.** A periodic eBay order import and a
   periodic price/stock sweep need one new unscoped maintenance repository
   (CLAUDE.md §4 requires explicit approval). Until approved, both run on
   events and on demand.

## Signals to watch (structured log events)

| Event | Meaning | First response |
|---|---|---|
| `ebay_seller_api_rate_limited`, `ebay_taxonomy_rate_limited` | Daily or burst quota spent | Check `/ebay/health` call limits; Growth Check if persistent |
| `ebay_seller_api_unauthorized` | A seller's grant was refused | Connection is marked *reconnect*; the merchant reconnects |
| `ebay_listing_rejected` | eBay refused a listing or update | The merchant sees eBay's reason on the listing |
| `ebay_price_quantity_enqueue_failed` | Broker unreachable when queuing a push | Fix RabbitMQ; the merchant can send from the product page |
| `ebay_call_limits_unavailable` | Analytics API unreadable | Health view shows `null`; nothing else is affected |
| `ebay_store_listings_erased`, `ebay_order_buyers_anonymised` | Deletion-contract erasure ran | Counts only; the compliance ledger records the notice |

## Known limitations

- The application token and category tree ids are cached per process; a
  restart re-fetches them.
- No background token refresh: user tokens refresh on use (C1 design).
- Health view call limits cover the Sell APIs (inventory, fulfillment,
  account), not Taxonomy.
