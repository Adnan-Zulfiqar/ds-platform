# Track E7 W3 — WooCommerce price and stock follow the draft

Status: **event-driven push implemented; scheduled sweep not built
(needs approval).** Verified against a faked WooCommerce API only.

## What it does

When a committed change can move a product's price or stock, the published
WooCommerce product is updated with **absolute** values: `regular_price`,
`stock_quantity` and `manage_stock`. The changes that trigger it are:

- a product or draft edit;
- a pricing apply;
- an inventory or supplier sync.

Because the values are absolute, a task that runs twice (at-least-once
delivery) sends the same numbers twice and nothing drifts.

- **Recorded on the listing (ERROR, with the reason):**
  - several variants;
  - no price;
  - the wrong currency;
  - the store's own refusal;
  - a disconnected, unverified or key-rejected store ("reconnect").
- **Raised, so the Celery task retries with backoff:** the store is
  unreachable or returns a 5xx. The listing is left as it was.

## Decision: one dispatcher, not a third copy

The after-commit hook `push_price_quantity_after_commit` lived in the eBay
task module and queued only eBay pushes. It moved to
`app/tasks/integrations/channels.py` and now queues **both** channels' tasks
on commit. The five call sites changed one import line each.

Each channel's task finds that channel's listings for the product. A product
with no WooCommerce listing costs one indexed query and makes no call.
Shopify keeps its own push path, which is unchanged.

## Not built: the six-hourly backstop sweep

eBay has a sweep (B-011) that re-sends every listing's values in case an
event push was lost. A WooCommerce sweep would need the same thing:

- an unscoped, ids-only class listing the workspaces that have WooCommerce
  stores;
- an entry on CLAUDE.md §4's closed list;
- explicit owner approval.

It is recorded as an open question (B-016) and is not built. Until then, a
lost event push is corrected by the next change to that product, or by
publishing again.

## Verified

- `tests/integration/test_woocommerce_price_quantity.py` covers:
  - absolute values sent, and idempotent on a repeat;
  - a wrong currency recorded with nothing sent;
  - a disconnected store asking for a reconnect;
  - the store's refusal recorded on the listing;
  - an unreachable store raising, with the listing untouched;
  - a product with no listing making no call.
- `tests/unit/test_channel_price_quantity_dispatch.py` checks that one
  commit queues eBay and WooCommerce once each, that nothing is queued before
  the commit or for an empty list, and that a broker outage is swallowed.
