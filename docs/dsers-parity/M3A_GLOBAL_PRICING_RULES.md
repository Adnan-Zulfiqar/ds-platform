# M3A — Global pricing and shipping rules

Rules are configured once and applied in three places: at import, in a
read-only preview, and in a confirmed bulk application that runs on the Celery
queue.

Delivered in six sessions: M3A-1 (calculation core), M3A-2 (versioning and
management API), M3A-3 (import, preview, apply), the M3A-3 acceptance fix
(direct import tests, real background execution), **M3A-4A** (rule management
UI) and **M3A-4B** (draft impact, bulk application, and final hardening).

M3A is complete.

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

## 6a. Management UI (M3A-4A)

**Settings → Global Rules** (`/settings/global-rules`), linked from the
Settings index. Five areas on one route, because they are read together — edit
a rule, preview what it does, check what changed:

| Area | What it does |
|---|---|
| Pricing Rules | List, create, edit, activate/deactivate, open history. Fields appear only when the selected strategy uses them. |
| Shipping Rules | The same, plus carrier lists and no-match behaviour. |
| Application Behaviour | What happens automatically, and the `appliesToNewImports` switch per rule. |
| Live Preview | A read-only calculator over `POST /global-rules/preview`. |
| Rule History | Paginated, append-only, with a change-detail disclosure. |

### The backend stays the pricing authority

Not one figure on this screen is computed in TypeScript. Landed cost, price
before rounding, final price, profit, markup and margin all come from the
preview endpoint. A second implementation in the client would eventually
disagree with the one that actually prices products, and the merchant would be
shown one number and charged another.

The one piece of arithmetic in the UI is the fixed worked example — "£10
landed cost — 50% markup = £15 · 50% gross margin = £20" — which is static
text on a stated cost, not a live calculation. It exists because merchants
routinely enter one meaning the other, and the difference is expensive.

The preview debounces, and a response is discarded unless its request is still
the newest: overlapping requests do not come back in order, and a slow early
one landing last would repaint the panel with figures for input the merchant
has already changed.

### Permissions

Owners and admins manage rules; everyone else reads, previews and reads
history. Mutation controls are **absent** for a viewer rather than disabled —
a disabled control that keyboard focus lands on and does nothing is worse than
one that was never there. This is presentation only: the API rejects a
viewer's write regardless, and the backend permission tests are what prove it.

### Concurrency

Every update and activation carries `expectedUpdatedAt`, the token the server
last returned, echoed back verbatim. A 409 raises a banner offering **Reload
latest version** or **Keep my changes**; nothing resolves silently, nothing
autosaves, and "saved" is only shown after the server said so. Closing a dirty
form asks first, and a browser reload is guarded by `beforeunload`.

The principles are M2A's, reimplemented rather than imported: M2A's machinery
is built around an autosaving document with per-tab dirty tracking, and a
settings form is a different shape.

### Two supporting backend changes

M3A-4A needed two things the API did not yet offer, both additive:

* `POST /global-rules/preview` now returns **`priceBeforeRounding`**, so a
  rule that computes 20.00 and sells at 19.99 reads as the rounding mode the
  merchant chose rather than an arithmetic error. Exported from the engine
  (`compute_sell_price_before_rounding`) rather than recomputed in the client.
* `GET /global-rules/{kind}/{id}/history` is now **paginated** and returns the
  standard `Page` envelope like every other list endpoint. History is
  append-only, so a long-lived rule's trail grows without bound; this was the
  one list in the API that could not be capped.

---

## 6b. Draft impact and bulk application (M3A-4B)

**Settings → Global Rules → Preview and Impact.** What the active rules would
do to existing drafts, and the confirmed run that makes it real.

### Selection never materialises the catalogue

A page holds 25 rows. "Select all matching" sends the *filter*, not a list of
identifiers; the server expands it once, at confirmation, into a durable
snapshot capped at `MAX_APPLICATION_PRODUCTS`. Resolving at confirmation rather
than in the worker is deliberate: the merchant confirmed a count they were
shown, and resolving later would sweep in drafts imported in between.

One predicate (`draft_query`) backs both the preview and the selection, so the
set shown and the set applied cannot drift.

`safeOnly` excludes published drafts, drafts already flagged, and drafts
missing item cost, freight or currency — the three figures the engine refuses
to invent. It checks those columns directly rather than trusting
`needs_review`, which is only set once something has priced the draft: live
verification showed a selection reporting "5 ready" that included one nothing
could price.

### Confirmation, progress and results

The confirmation dialog states selected / ready / held / published / expected
changes, the governing rule and version, and that draft writes are real while
Shopify and published products are untouched. Cancelling issues no request at
all. One idempotency key is generated when the dialog opens, so a double-click,
a refresh or a network retry all reach the same run.

`202` returns a `pending` application. The screen polls until a terminal state
and then stops; the application id lives in the URL — with the open section —
so a browser refresh resumes the same run instead of losing or duplicating it.
Results are listed per item, filterable by outcome, and a partial run shows its
successes rather than hiding them behind the failures.

### Published products

Marked, unselectable, excluded from select-all, and refused by the service even
when their id is posted directly. No request is made to any sales channel, and
a database assertion proves the row is unchanged.

---

## 6c. Operations

### Recovering a stuck application

A worker killed mid-run leaves its application `running` forever. The worker
stamps `heartbeat_at` at every batch boundary, and
`pricing.reconcile_applications` (Celery beat, every five minutes) is what
notices.

**Correction to what this section previously claimed.** The recovery described
here at commit `c43b6c2` did not work, and the documentation said it did. The
reconciler cleared `claimed_by_task_id` but left the row `running`, so the
message it then published reached a worker that saw a `running` row owned by
somebody else, reported `already_running` and processed nothing. Every recovery
attempt logged `requeued` and rescued nothing. Reproduced against `c43b6c2`
before the fix; the sequence below is what replaced it.

#### The state machine

```
running (owner A, heartbeat gone quiet)
  → pending, unowned            conditional UPDATE, committed
  → message published            after that commit, never before
  → running (owner B, new lease) ordinary pending→running claim
  → resumes from processed_count
```

Going back through `pending` is the whole point: `pending → running` is the
only transition that mints a lease against a **real** Celery task id, and the
reconciler has no such id to appoint an owner with. Nothing fabricates one, and
no test-only task id is special-cased.

The reclaim is a single conditional UPDATE pinned to the exact state the sweep
observed — status, heartbeat, lease token and recovery count. A worker that
committed a batch in between has moved its heartbeat, so the write matches no
row and it keeps its run; a second reconciler racing the first finds the values
already changed and takes nothing. `recovery_count` therefore increments
exactly once per recovery.

If the publish fails, the run rests `pending` with `enqueued_at` NULL — exactly
the state the pending sweep repairs, so a broker outage delays recovery instead
of losing it. Duplicate beat executions are bounded: an extra message finds the
run already claimed and does nothing.

A run is parked as `failed` once `recovery_count` reaches `MAX_RECOVERIES` (3),
because a run that dies identically every time is a defect to look at rather
than work to retry forever. Committed batches keep their results and the reason
says so.

#### Per-batch ownership: how a stale worker is fenced out

Claiming once and then looping is not enough, and at `c43b6c2` it was all there
was: a worker whose run had been reclaimed carried on writing prices, result
rows, progress and heartbeats into a run that belonged to someone else, and
could then mark that run `failed` from its own error handler. Also reproduced
before the fix.

`lease_token` (migration 0028) is the fence. It is minted fresh on every grant
of ownership — first claim, recovery, and the re-lease a redelivery performs —
so it is unique per *attempt*. `claimed_by_task_id` cannot serve this purpose:
a retry carries the same task id as the attempt it replaces, so two processes
can present it at once.

Every batch begins by locking the row with `SELECT … FOR UPDATE` and checking,
in the same transaction that will perform the product and audit writes:

* status is `running` (or `cancelled`, which the owner closes out);
* `lease_token` is still this worker's.

Because the lock is held for the whole batch, a reclaim arriving mid-batch
blocks until the batch commits and then finds a heartbeat that has moved. In
the other order, the check reads a token that is no longer ours and returns
before a single write is issued. The read uses `populate_existing` so it sees
the row rather than a session's remembered copy — a fence that reads its own
cache is not a fence.

`finalize`, `fail` and the heartbeat write are owner-conditional through the
same lock. A worker that lost its run reports `superseded`; it writes no price,
no `PriceChange`, no result row, no progress, no heartbeat and no status — and
in particular does not mark the new owner's healthy run `failed`.

The per-item unique constraint is **not** the safety mechanism. Product writes
happen before it, so by the time it fired the catalogue would already have been
changed twice. It remains a backstop against duplicate result rows, nothing
more.

There is still no hard task termination anywhere.

#### NULL heartbeats

`heartbeat_at` arrived in 0027, so a run abandoned by a worker that died before
that upgrade carries NULL — and `heartbeat_at < cutoff` is NULL, never true.
Those rows were invisible to the sweep and would have sat `running` forever.
Two things fix it, deliberately both:

* migration 0028 backfills `heartbeat_at` for existing `running` rows from
  `COALESCE(started_at, updated_at, created_at)`;
* the sweep's predicate treats a NULL heartbeat as stale only once
  `COALESCE(started_at, created_at)` is itself older than `STALE_AFTER`, so a
  run that has only just started is never mistaken for an abandoned one.

**Manual check:** `SELECT id, status, heartbeat_at, recovery_count, lease_token
FROM rule_applications WHERE status = 'running' ORDER BY heartbeat_at NULLS
FIRST;`

### The historical typed-reference constraint

`python -m scripts.verify_rule_version_integrity` reports whether
`ck_global_rule_versions_one_typed_reference` is validated and whether any row
contradicts it. Read-only, never run by the application, and it prints per-tenant
counts and version ids only — no rule names or notes, because operators of
different tenants read the same output. Exit `0` clean (what a fresh install
reports), `1` unmatched rows, `2` validated needed. The remediation is in the
module docstring.

### Rate limits

The live preview, the impact preview and the apply endpoint carry named quotas
(120/120/20 per minute) through the *same* `FixedWindowLimiter` the global
middleware uses. There is one limiter implementation, not two, and the broad
middleware quota still applies underneath: a request passes through both.

The key is `ratelimit:{name}:tenant:{tenant_id}:user:{user_id}`. It was
`ratelimit:{name}:tenant:{tenant_id}` at `c43b6c2`, which meant any one seat
could spend the whole workspace's allowance and lock out every colleague — a
limiter added to blunt abuse, turned into a way for one member to deny service
to the rest. Both halves of the key are needed: user ids are only unique within
a tenant here, so the tenant half is what keeps two workspaces apart.

The identity comes from the verified token claims, and the dependency *asks
FastAPI for the principal*, so authentication and tenant resolution are
guaranteed to have happened before the counter is touched — by construction
rather than by parameter order. No header, query parameter or context value a
caller controls can choose a bucket; an unauthenticated request has no
principal and is counted by address. The identifiers appear in server logs as
structured fields and never in a response payload.

Fail-open on a Redis outage is unchanged, and tested: the documented trade is
that a cache outage degrades to "no quota" rather than taking pricing down.

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
3. **No `preferred_carrier` shipping strategy.** "Prefer this carrier" is the
   `preferredCarriers` list, which filters the quotes every strategy chooses
   from, so it composes with all four rather than being a mutually exclusive
   fifth. A strategy the backend cannot persist would save and then behave as
   something else.
4. **No FX conversion on the M3A path.** A cross-currency rule fails closed
   rather than converting. `PricingEngine.propose_calculation` (the draft
   workspace) has its own conversion; unifying the two is not M3A work.
5. **Products cannot be created through the API**, only imported. The
   Playwright impact tests therefore seed drafts from the test process rather
   than through an endpoint — adding production API surface that exists only
   for tests would be surface an attacker gets too.
6. **RabbitMQ remains unverified.** The M3A-4B verification worker ran on a
   SQLAlchemy broker: this machine has no RabbitMQ, and its Redis (3.0.504)
   predates the `HELLO` command kombu needs. The message flow, the claim,
   batching, resume and cancellation were exercised through a real
   out-of-process worker and a real broker — but **not** the transport
   production is configured for, and nothing since has changed that. Treat
   "works on RabbitMQ" as untested until somebody runs it there.
7. **`safeOnly` predicts, it does not promise.** It excludes what the recorded
   columns can rule out. A rule denominated in another currency, or a shipping
   rule with no matching quote, can still hold a draft back when the run
   reaches it — recorded as `needs_review` in the results rather than written.
8. **The recovery and fencing figures below predate the acceptance fix.**
   Section 6c has been corrected; §9's figures come from the M3A-4B report and
   the acceptance-fix report supersedes them for anything touching recovery,
   ownership or endpoint quotas.
9. **Every Celery task in this repository shares one database engine across
   `asyncio.run` calls.** `_run` in `app/tasks/pricing.py` now disposes the
   engine before its loop closes, because without it the *second* task in a
   worker picks a pooled connection belonging to a dead loop and fails with
   `'NoneType' object has no attribute 'send'` — found by running a real worker
   against a real broker, where the first message succeeded and every one after
   it retried. **The same latent bug affects `app/tasks/products.py`,
   `orders.py`, `inventory.py` and the rest**, which were not touched here.
   They need the same fix; it is out of M3A's scope and is recorded as
   outstanding work rather than quietly left unmentioned.

---

## 8. Migrations

| Revision | Contents |
|---|---|
| `0023` | Rule model: variant scope, target-margin/hybrid strategies, rounding, shipping rules, version history, price-change provenance. |
| `0024` | Composite FKs `(tenant_id, rule_id)`; `NOT VALID` typed-reference CHECK; partial unique indexes for one active global rule per kind. |
| `0025` | Pricing-outcome columns on `products`; `rule_applications` / `rule_application_items`; re-runs 0024's backfill and validates its constraint only if no row contradicts it. |
| `0026` | `claimed_by_task_id`, `processed_count`, `enqueued_at` on `rule_applications`. |
| `0027` | `heartbeat_at`, `recovery_count`, `selection_filter`, and a partial index for the reconciler sweep. |
| `0028` | `lease_token` — the per-attempt ownership fence — and a backfill of `heartbeat_at` for `running` rows left NULL by 0027. |

All additive. Earlier migrations are never modified. 0028's backfill is
deliberately not reversed by its `downgrade()`: it only ever filled NULLs on
`running` rows, and restoring them would put back the defect it exists to fix.

---

## 9. Verification

See the M3A-4B report for the final figures. In summary: the full backend
suite passes with `ruff`, `ruff format --check` and strict `mypy` clean at a
single head `0027`, with a fresh upgrade and a `0027 → 0026 → 0027` round-trip
both exiting 0; the frontend lints, typechecks and builds; and the Playwright
suites for M3A-4A and M3A-4B pass, including the apply path against a **real
out-of-process Celery worker consuming from a real broker**.

The queue tests drive the **registered task** through `Task.apply`, not the
service beneath it.
