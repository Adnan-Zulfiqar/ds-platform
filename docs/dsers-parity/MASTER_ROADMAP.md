# DSers-Parity Master Roadmap

A durable milestone plan toward DSers-class capability, derived from the gap
audit in [FEATURE_MATRIX.md](FEATURE_MATRIX.md). Each milestone is delivered
on its own branch, with its own migration review (if any), automated tests,
browser/local verification, documentation, and an explicit approval gate
before the next one starts — per the standing engineering rule in this
repository: implement only the current phase, never build ahead.

Milestones are ordered by dependency and by what unlocks the most value per
unit of risk — import/editor/publish is the spine every later milestone
(sync, orders, tracking) depends on, so it comes first.

| # | Milestone | Depends on | Status |
|---|---|---|---|
| M1 | AliExpress Product Import → Editable Draft | — | **Delivered** (this document's sibling: [M1_IMPORT_TO_DRAFTS.md](M1_IMPORT_TO_DRAFTS.md)) |
| M2 | Premium Product Editor | M1 | Planned |
| M3 | Publish-to-Store Hardening & Multi-Store Publish | M1, existing Shopify OAuth | Planned |
| M4 | Bulk Import & Feed-Based Sourcing | M1 | Planned |
| M5 | Order → Fulfilment Bridge (tracking push-back) | Existing order sync | Planned |
| M6 | Supplier Auto-Order (conditional on API access) | M5 | Planned — see blocker note |
| M7 | Multi-Supplier Sourcing & Price Comparison | M1, M2 | Planned |
| M8 | Multi-Channel Publishing (beyond Shopify) | M3 | Planned |
| M9 | Supplier Optimization & Repricing Intelligence | M7 | Planned |

---

## M1 — AliExpress Product Import → Editable Draft

**Status: Delivered.** See [M1_IMPORT_TO_DRAFTS.md](M1_IMPORT_TO_DRAFTS.md) for
the full report: what already existed, what was built, what was fixed, and
what remains a documented gap.

## M2 — Premium Product Editor

The full editor deliberately excluded from M1's "safe basic editable draft
view." Scope, not yet implemented:

- Rich text description editing (currently plain sanitized text only).
- Bulk variant editing — apply a price/SKU rule across every variant at once,
  rather than one `PATCH` per variant.
- Image editing beyond reorder/caption (crop, background removal) — likely
  needs a decision on whether this is client-side or a paid image API, which
  is a real cost/build trade-off to raise before implementing.
- A documented decision on the Drafts-list "variant count" column deferred
  from M1 (`FEATURE_MATRIX.md` §2): needs a correlated-subquery change to
  `ProductRepository.list_drafts`/`list_published`, reviewed for the
  read-performance impact the list schema's docstring explicitly protects
  against.

## M3 — Publish-to-Store Hardening & Multi-Store Publish

Publishing to one connected store already works end-to-end (OAuth, currency
guard, `StoreListing` creation). Scope:

- UI flow for publishing one draft to *multiple* connected stores in one
  action (the data model already supports many `StoreListing` rows per
  product — this is a UI/orchestration gap, not a schema gap).
- Un-publish / relist handling.
- Publish-failure retry, mirroring the pattern M1 just built for import
  retry (`ProductImportService.retry_import` is a template, not something to
  duplicate blindly — the publish failure surface is different).

## M4 — Bulk Import & Feed-Based Sourcing

- Multi-URL/CSV paste import (`POST /products/import` is single-product
  today).
- A sourcing UI on top of the existing `browse_feed` endpoint, which today
  has no frontend consumer.
- Keyword/category search is **blocked** on this AliExpress account
  (`NGSELECTION_SEARCH_ERROR`, live-verified) — re-check API access before
  scoping search into this milestone; do not build a UI for a call that
  returns an error.

## M5 — Order → Fulfilment Bridge

Orders sync in from Shopify today (`OrderSyncService`); nothing pushes state
back out. Scope:

- Push tracking numbers back to Shopify as fulfilments once a supplier ships.
- Manual "mark shipped + enter tracking" flow as the first, simplest version
  — before any supplier-side automation.

## M6 — Supplier Auto-Order

**Conditional milestone.** `app/integrations/aliexpress/orders.py` documents
that this AliExpress account is not a registered DS publisher and the
populated order-detail response body has never been observed live
(`docs/TECHNICAL_DEBT.md` M16). Per this platform's legal/integration
constraints, browser-automated purchasing is explicitly out of scope
regardless of API access. **Before scoping M6:** confirm whether AliExpress's
official Dropshipper API grants order-placement access to this application,
and get sign-off that placing real orders (real money, per attempt) is an
accepted cost of building and testing this milestone. If API access is
unavailable, this milestone stays blocked and should be re-evaluated per
supplier, not implemented via scraping or automation of the AliExpress
website.

## M7 — Multi-Supplier Sourcing & Price Comparison

Depends on M2 (editor needs to show more than one supplier option per
product) and a data-model extension: today a `Product` has exactly one
`(source, external_id)`. Needs its own design pass before implementation —
flagged here as a milestone, not pre-designed.

## M8 — Multi-Channel Publishing

Extend publishing beyond Shopify (Amazon, eBay, TikTok Shop, WooCommerce).
Each channel is its own OAuth integration package, following the shape
`app/integrations/shopify/` already established. Scope one channel at a time,
starting with whichever the business prioritizes — this roadmap does not
presume an order among them.

## M9 — Supplier Optimization & Repricing Intelligence

Depends on M7 (needs multiple suppliers per product to have anything to
optimize across). Automated repricing *rules* already exist
(`app/api/v1/automation/router.py`) for price/stock triggers on a single
product; this milestone is about cross-supplier comparison and reliability
scoring, which is a materially different feature.

---

## How to use this document

Before starting any milestone after M1: re-run the audit method described in
`FEATURE_MATRIX.md`'s closing section against the *then-current* repository —
this roadmap is a snapshot from 2026-08-12 and prior milestones may have
changed what's already built. Do not assume this document stays accurate
without re-verification.
