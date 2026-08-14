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
| M2A | Premium Editor Foundation — concurrency & edit-gating hardening | M1 | **Delivered, acceptance-fix pass applied 2026-08-13** (see [M2_PREMIUM_EDITOR.md](M2_PREMIUM_EDITOR.md)) |
| M2B–E | Premium Product Editor — remaining scope (see below) | M2A | Planned |
| M3 | Publish-to-Store Hardening & Multi-Store Publish | M1, existing Shopify OAuth | Planned |
| M4 | Bulk Import & Feed-Based Sourcing | M1 | Planned |
| M5 | Order → Fulfilment Bridge (tracking push-back) | Existing order sync | Planned |
| M6 | Supplier Auto-Order (conditional on API access) | M5 | Planned — see blocker note |
| M7 | Multi-Supplier Sourcing & Price Comparison | M1, M2B (variant editing) | Planned |
| M8 | Multi-Channel Publishing (beyond Shopify) | M3 | Planned |
| M9 | Supplier Optimization & Repricing Intelligence | M7 | Planned |

---

## M1 — AliExpress Product Import → Editable Draft

**Status: Delivered.** See [M1_IMPORT_TO_DRAFTS.md](M1_IMPORT_TO_DRAFTS.md) for
the full report: what already existed, what was built, what was fixed, and
what remains a documented gap.

## M2A — Premium Editor Foundation

**Status: Delivered, acceptance-fix pass applied 2026-08-13.** See
[M2_PREMIUM_EDITOR.md](M2_PREMIUM_EDITOR.md) for the full report.

**Scope correction, discovered during M2A's own mandatory pre-implementation
audit:** M2A was originally framed as "build the safe foundation of the
premium editor" — title/description editing, a dedicated route, a save-state
UI — as if none of it existed yet. It already did, built separately (and
mostly earlier) via `cursor/draft-product-editor`, `cursor/premium-product-editor`,
and `cursor/product-workspace-v2`, merged to `develop` before the M1
dsers-parity branch even started. `FEATURE_MATRIX.md` §3 previously
understated this as a "basic safe draft view" and has been corrected.

What M2A actually delivered, once the real gap was identified by reading the
existing code rather than assuming the brief's framing: **optimistic
concurrency** (a stale save is now rejected with a 409 instead of silently
overwriting a newer change — nothing enforced this before) and **one
edit-gating rule** (a product already published to a channel can no longer be
edited back through the drafts-only endpoint). Both are hardening on the
existing `PATCH /drafts/{id}` write path, not a new editor.

**Acceptance-fix pass (2026-08-13):** the initial delivery's version token
was optional on the drafts path (a request that omitted it fell back to
unguarded last-write-wins), and its conflict-recovery UX ("Keep my changes")
silently refreshed the token and left autosave free to overwrite a newer
change moments later. Both closed — the token is now mandatory on the
drafts path specifically, and conflict recovery requires an explicit second
action (confirm-to-reload, or review-then-explicitly-overwrite) before
anything is discarded or saved. See [M2_PREMIUM_EDITOR.md](M2_PREMIUM_EDITOR.md)
for the full account.

## M2B–E — Premium Product Editor, remaining scope

**Rebaselined 2026-08-13** against a fresh capability-by-capability audit
(`FEATURE_MATRIX.md` §3) rather than carried forward — several items below
replace or narrow what the pre-acceptance-fix version of this document
scoped, now that each capability's actual state (not its intended state) is
confirmed with code and test evidence. Nothing here rebuilds SEO, variants,
pricing, inventory, shipping, or media — all of those are already delivered
(see §3) and stay untouched by M2B–E.

- **M2B — Rich text description editing.** Description is edited as plain
  sanitized text (a `<textarea>`); no rich editor component exists anywhere
  in the codebase. Scope: a rich-text or Markdown editor for the existing
  `description` field, through the existing sanitize-on-save path — the
  sanitizer already strips executable content, so this is an editor-UI
  addition, not a new server-side trust boundary.
- **M2C — Bulk variant editing.** `PATCH /drafts/{id}/variants/{variantId}`
  is single-variant only; `draft-variants-panel.tsx` has no selection or
  apply-to-all mechanism. Scope: a bulk price/SKU rule applied across some
  or all variants in one action, backed by a new bulk endpoint (or a
  variant-array extension of the existing one) rather than N sequential
  `PATCH` calls from the client.
- **M2D — Image editing beyond reorder/caption (crop, background removal).**
  Confirmed still not built (`draft-media-panel.tsx`'s own docstring: *"File
  upload / crop land when S3 storage is wired"*). Needs a decision on
  client-side vs. a paid image-processing API before implementation — a
  real cost/build trade-off to raise, not to decide unilaterally in this
  milestone's own scoping.
- **M2E — Publish-readiness hardening.** New this rebaseline, found during
  the audit rather than carried over from the original brief: the
  Publish-readiness score/checklist (`readinessFor()`,
  `editor-header/readiness.ts`) that gates the "Fix N issues to publish"
  button is client-side only, with **zero automated test coverage** of its
  own, and the backend publish path enforces only the currency-mismatch
  guard — a direct API call bypasses every readiness check the UI shows.
  Scope: unit tests for `readinessFor()`, Playwright coverage of the
  Publish-button gating, and a decision on whether any readiness checks
  (missing images, no variants) should move server-side as an actual publish
  precondition rather than staying advisory-only.
- **AI Studio side-by-side proposal review — deferred, not yet its own
  numbered milestone.** The "AI Studio" tab exists and is reachable but its
  body is a placeholder (*"Use Optimize with AI from More actions for now.
  Side-by-side proposal studio is Stage 6"*, `draft-product-editor.tsx`).
  The underlying AI optimization it points at (`ProductOptimizationService`
  + version-activate flow) is already implemented and verified — this item
  is specifically the missing in-editor comparison view, not the AI feature
  itself. Left unnumbered pending a decision on where it sits relative to
  M2B–E's priority order.
- ~~A documented decision on the Drafts-list "variant count" column~~ —
  **delivered by M1's acceptance pass**
  (`ProductRepository._variant_count_column`); removed from this list rather
  than re-scoping already-shipped work.

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

Depends on M2B (variant editing -- the editor needs to show more than one supplier option per
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
this roadmap was last rebaselined 2026-08-13 (M2A acceptance-fix pass) and
prior milestones may have changed what's already built since. Do not assume
this document stays accurate without re-verification.
