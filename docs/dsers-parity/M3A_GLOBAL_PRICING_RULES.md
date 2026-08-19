# M3A — Global pricing and shipping rules

Backend milestone. Rules are configured once and applied in three places: at
import, in a read-only preview, and in a confirmed bulk application that runs
on the Celery queue. No frontend — that is M3A-4.

Delivered in four sessions: M3A-1 (calculation core), M3A-2 (versioning and
management API), M3A-3 (import, preview, apply), and the M3A-3 acceptance fix
(direct import tests, real background execution).

---

## 1. One calculation, three surfaces

Import, preview and confirmed application all reach
`DraftPricingService.calculate_for`, which reaches `calculate_price` in
`app/services/pricing_engine.py`. That is the whole reason the module is
shaped this way: a merchant must not see one number in the preview and a
different one after confirming.

The arithmetic lives in the engine and nowhere else. `rule_application.py`
resolves, orchestrates and records; it does not compute.

### Fail closed, always

`calculate_price` returns `price=None` plus a machine-readable reason
whenever an input is not trustworthy. It never substitutes a default for a
missing supplier figure. The vocabulary:

| Reason | Meaning |
|---|---|
| `supplier_cost_unknown` | No item cost from the supplier. |
| `shipping_cost_unknown` | No freight figure. **Expected on every AliExpress import** — see §6. |
| `supplier_currency_unknown` | The snapshot has no currency. |
| `fx_rate_unavailable` | The rule declares a currency the product is not in, and this path has no FX. |
| `no_shipping_quotes_available` | A shipping rule is configured but the supplier offered nothing priced. |
| `no_shipping_method_matches_rule` | Quotes exist; none satisfy the rule. |
| `shipping_rule_not_satisfied_fallback_used` | Fell back to the cheapest option, which satisfied nobody's constraint. |
| `shipping_destination_unknown` | Neither the rule nor the import names a destination. |
| `pricing_calculation_failed` | The rule could not be evaluated at all. |

A flagged product is still created, still visible, and never published.

### All-or-nothing per product

A product's price is the **lowest** of its variants' prices. So if any variant
cannot be priced, nothing on that product is priced. Pricing eleven of twelve
variants would advertise a "from" price derived from an incomplete set, and
the missing one could be the cheapest. The costless variant stays in the
catalogue with its reason attached rather than disappearing.

### Currency

A rule with `currency = NULL` declares no denomination and prices in the
product's currency — the ordinary single-currency case. A rule that *does*
declare one may only price a cost already in that currency; anything else is
`fx_rate_unavailable`. The check goes through `convert_currency`, which
refuses to invent a 1:1 rate.

This matters because `markup_percent` would survive a mismatch unscathed while
`min_price`, `max_price`, `markup_fixed`, `min_profit` and `fees_fixed` are
money. A GBP floor applied to a USD cost is a silent mispricing.

---

## 2. Scope precedence

`variant > product > category > store > global`, priority breaking ties,
resolved once in `app/services/rule_resolution.py` for both pricing and
shipping.

**A candidate must be fetched before it can win.** `find_candidates` on the
pricing and shipping repositories must stay mirror images of each other: they
feed one shared resolver, so a clause present in one and missing from the
other is a difference in precedence that no test of the resolver itself can
detect. That is exactly how variant-scoped pricing rules were dead across all
three surfaces until the acceptance fix — the resolver handled them correctly
and never saw one.

---

## 3. Import

`ProductImportService._apply_global_rules`, called after the supplier snapshot
is written and before the import is marked succeeded.

- No rule, or `applies_to_new_imports = false` → **exactly** pre-M3A
  behaviour. No price, no flags, no evidence columns written.
- Otherwise the outcome is stamped on the product whether or not a price was
  produced: `applied_pricing_rule_id` / `_version`, the shipping rule that
  governed, `landed_cost`, `landed_cost_fees`, `pricing_calculated_at`,
  `needs_review` and `pricing_review_reasons`.
- Pricing **never fails an import**. The snapshot is already written; letting
  a rule error escape would roll it back and leave the merchant with no draft
  and no explanation. A draft that exists and is flagged is worth more.
- Import writes no `RuleApplication` row. Those record *confirmed* bulk runs;
  creating one per refresh would fill the merchant's history with runs they
  never asked for.

Re-importing is idempotent and does not duplicate a pricing outcome.

---

## 4. Confirmed application runs on the queue

```
admin confirms  →  API creates `pending`, commits  →  message published
                                                          ↓
        results ← finalize ← batch … batch ← claim (pending → running)
```

`POST /global-rules/drafts/apply` returns **202 with a `pending`
application** and writes no price. The hand-off is a FastAPI background task
so it happens *after* the request transaction commits — a worker that picked
up the message first would look for a row that is not visible yet.

The task is `pricing.apply_rules_to_drafts`, defined in the existing
`app/tasks/pricing.py`. Extending that module rather than adding one is not
tidiness: `celery_app.conf.imports` already lists it, so the task is
registered in every worker without touching broker configuration.

**The payload is one id.** No product list, no rule snapshot, no merchant
data reaches the broker. Everything is read back from the row, which also
means a message that sat in a queue across a deploy still runs against the
rules the merchant confirmed rather than a stale copy carried in the message.

### Tenant safety

The worker resolves the tenant from the application row, via
`RuleApplicationTenantLookup` — deliberately unscoped, deliberately not a
repository, and able to answer exactly one question. Taking the tenant from
the payload would let a forged, replayed or stale message run one tenant's
rules across another tenant's catalogue.

> **Architectural note.** This is a third unscoped accessor alongside
> `TenantRepository` and `AuthenticationUserRepository`. It is not a
> repository and exposes no row reads, which is the "separate,
> explicitly-named class" CLAUDE.md §4 prescribes — but it is a deliberate
> addition to a short list and is flagged here for the owner's awareness.

### Idempotency and retries

At-least-once delivery is the contract (`task_acks_late`), so:

- The `pending → running` transition **is** the lock — one conditional
  `UPDATE`, so of two workers handed the same message exactly one proceeds.
- A **duplicate delivery** finds the run no longer `pending` and does nothing.
- A **retry of the same task** is recognised by `claimed_by_task_id` and
  *resumes* from `processed_count` rather than restarting.
- `processed_count` is a cursor, not a derived figure: an item whose product
  id no longer resolves is recorded with a NULL `product_id`, so progress
  cannot be recovered by diffing recorded rows against the selection. The
  cursor and the batch it accounts for commit together.
- Counts are recomputed from the recorded items rather than tallied in
  memory, because a run spans transactions and may resume.
- A run that exhausts its retries is `failed` **with the results of every
  batch that did commit** — reporting zero would send the merchant looking
  for changes the catalogue already has.

If the broker refuses the message the run is marked `failed` with the reason.
The row is already committed and cannot be rolled back, and leaving it
`pending` forever is indistinguishable from "queued behind a busy worker".

---

## 5. Cancellation policy

Explicit, because "cancel" means different things at different points.

| State | Behaviour |
|---|---|
| `pending` | Stopped outright. The claim refuses the message when it arrives, so a task already in flight cannot revive it. |
| `running` | **Cooperative.** Marked cancelled; the worker stops at its next batch boundary. Drafts repriced by committed batches keep those prices — they are real writes with real result rows, and quietly reverting them would be a second unreviewed reprice. |
| `completed` / `partial` / `failed` | Refused with a conflict. Reporting a finished run as cancelled would misrepresent the catalogue. |

Cancelling an already-cancelled run returns it unchanged. There is
deliberately **no hard task termination** — killing a worker mid-transaction
is precisely the failure this design exists to avoid.

Cross-tenant cancellation returns **404, not 403**; a 403 would confirm the
application exists.

---

## 6. Limits, and where they are published

| Limit | Value | Where |
|---|---|---|
| Preview page size | `MAX_PREVIEW_PAGE = 200` | Rejected at the endpoint with 422 |
| Products per application | `MAX_APPLICATION_PRODUCTS = 5000` | `maxApplicationProducts` on the preview response |
| Products per worker transaction | `APPLICATION_BATCH_SIZE = 50` | `applicationBatchSize` on the preview response |

A selection above the ceiling is **refused with the number named**, never
silently truncated — a merchant who selected 6,000 drafts and got 5,000
repriced with no warning would have no way to find the other thousand.

Duplicate ids in a selection are collapsed at creation, because the run walks
the stored list by index.

---

## 7. Known limitations

1. **AliExpress supplies no freight quotes.** `ds.product.get` carries none,
   and `mapper.map_product` returns `shipping_cost: None` rather than
   inventing one. Every AliExpress import under a pricing rule therefore
   lands in `needs_review` with `shipping_cost_unknown`, and no price is
   produced. This is the fail-closed design working, not a defect — but it
   means rule-driven import pricing is not yet useful in practice for
   AliExpress. It becomes useful when a freight source exists. Tested
   unstubbed in `TestAliExpressReportsNoShipping`; the tests covering a
   *priced* import stub the mapper to simulate a supplier that reports
   freight, and say so.
2. **No published-product impact preview endpoint.** Optional in the brief.
   Published drafts already appear in the draft preview marked `published`
   with `canApply: false`.
3. **No frontend.** M3A-4.
4. **No FX conversion on the M3A path.** A cross-currency rule fails closed
   rather than converting. `PricingEngine.propose_calculation` (the draft
   workspace) has its own conversion; unifying the two is not M3A work.
5. **Filter-based selection is not offered.** A confirmed application names
   concrete product ids, which is what makes the selection snapshot
   meaningful after the drafts change. The preview is the filtering surface.

---

## 8. Migrations

| Revision | Contents |
|---|---|
| `0023` | Rule model: variant scope, target-margin/hybrid strategies, rounding, shipping rules, version history, price-change provenance. |
| `0024` | Composite FKs `(tenant_id, rule_id)`; `NOT VALID` typed-reference CHECK; partial unique indexes for one active global rule per kind. |
| `0025` | Pricing-outcome columns on `products`; `rule_applications` / `rule_application_items`; re-runs 0024's backfill and validates its constraint only if no row contradicts it. |
| `0026` | `claimed_by_task_id`, `processed_count`, `enqueued_at` on `rule_applications`. |

All additive. `0023`–`0025` are not modified.

---

## 9. Verification

254 M3A-focused tests; full backend suite 1286 passing. `ruff check`,
`ruff format --check` and strict `mypy` clean. Single head `0026`; fresh
upgrade from empty and a `0026 → 0025 → 0026` round-trip both exit 0.

The queue tests drive the **registered task** through `Task.apply` on a
worker thread, not the service beneath it. No broker is contacted.

**No frontend or Playwright gates were run** — M3A is backend-only.
