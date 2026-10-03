# EBAY-C4 — Inventory and price synchronisation

Status: **implemented; verified against a mocked eBay transport only.**

## What it does

Keeps each published eBay listing's **price and available quantity** equal to
the product's in DropPilot. Title, description and pictures change only
through a publish (C3).

| Trigger | How |
|---|---|
| Product edited (`PATCH /products/{id}`) | `after_commit` hook queues `ebay.push_price_quantity` |
| Supplier refresh (`POST /products/{id}/sync`) | same hook |
| Repricing (`POST /pricing/apply`, `POST /drafts/{id}/pricing/apply`) | same hook (added after review) |
| Inventory sync (scheduled task or `POST /inventory/sync`) | products whose stock moved are queued after the sync commits |
| Merchant, on the published product page | "Send price and stock to eBay now" — the same push, run in the request |

One eBay call per product: `bulkUpdatePriceQuantity`, with the inventory
item's quantity and the offer's quantity and price. Values are absolute, so
a task delivered twice sends the same numbers twice.

## Decisions

- **After commit, never before.** A task queued inside the request could run
  before the change is visible and send the old values. The platform's
  existing `after_commit` pattern (global rules, bulk pipeline) is reused.
- **Refusals are recorded, outages are retried.** A line eBay refuses (or a
  local reason the update cannot be sent: several variants, no price, a price
  in another currency) marks that listing `error` with the reason, and the
  task completes so the record is kept. eBay unreachable or 5xx raises, and
  `BaseTask` retries with backoff without touching the listing.
- **A broker outage does not fail the edit.** Enqueue failures are logged; the
  merchant's change is saved and can be sent from the product page.
- **No cross-tenant sweep.** A periodic "push everything" would need a new
  unscoped repository, which CLAUDE.md §4 reserves for explicit approval.
  The event triggers plus the manual send cover the cases; a sweep can be
  added with that approval.

## Verification

- Integration (mocked transport): absolute values sent to the right offer;
  a refused line recorded on the listing; a wrong currency never sent; a
  product without an eBay listing makes no call; another workspace's product
  is 404.
- Unit: the enqueue helper queues each product once and survives a broker
  outage.
- **Not verified:** the `after_commit` hooks firing inside a real request
  (the test client's transaction never commits), and any real eBay call.
