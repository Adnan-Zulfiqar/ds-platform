# Changelog

All notable changes to DropPilot AI.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project uses phase tags rather than semantic versions until the first
production release.

---

## [Unreleased]

### Fixed

- **GQL-2 F-01b — a webhook confirmation can no longer go stale.** The previous
  fix made a store that had *never* reconciled visibly degraded. It did not
  cover the opposite case. `webhooks_registered_at` was only ever written, never
  cleared, and `webhook_health()` read any non-null value as healthy — so one
  successful run vouched for every failure after it. A later unhealthy
  reconciliation, a provider outage, or a reconnect that stored **brand-new
  OAuth credentials** all left the previous timestamp standing, and
  `complete_connection` rotated the access token without touching it at all. The
  card showed a healthy state for a store whose subscriptions were missing, and
  because Retry appears only while degraded, the stale timestamp also removed
  the way out.

  A non-null `webhooks_registered_at` now means *the most recent completed
  reconciliation for the current credentials was healthy* — not "some
  reconciliation once succeeded". `register_webhooks` commits the column to NULL
  in its own short transaction **before** it makes any Shopify request, so a
  provider exception, a lock timeout or a worker dying mid-reconciliation leaves
  the store degraded instead of restoring a confirmation the rollback would have
  brought back. `complete_connection` clears it in the same UPDATE that stores
  refreshed credentials, so an old confirmation can never authenticate a token
  it has never seen.

  The invalidation runs on the caller's session rather than an independent one:
  the OAuth path reaches `register_webhooks` with `complete_connection`'s UPDATE
  of that very row uncommitted, and a second connection would block on it until
  the request timed out — the deadlock
  `ProductImportService._persist_failure_durably` already documents. It is a
  deliberate, narrow departure from "handlers never commit", because here a
  trace surviving the failure is the entire point.

  Concurrency is unchanged: the same bounded row lock still serialises
  reconciliation per store, the control test proving an unlocked race duplicates
  still passes, and different stores stay independent. Invalidation takes the
  same bounded wait, so a caller arriving while another reconciliation holds the
  row is refused *before* it can clear a confirmation it is not going to
  replace. Reads never mutate — viewing the status page does not reconcile.

  The UI stops claiming more than it can know. Nothing observes a subscription
  Shopify deletes outside DropPilot, so the badge reads **Webhooks last
  confirmed** with its date rather than *Webhooks active*, and the degraded
  warning says confirmation failed *on the most recent attempt*.

  Red-before: the new suite was written first and run against `8abd9cc` —
  12 failed, 9 passed. Green after: 21 passed. Full backend suite 1741 passed
  (baseline 1720, +21); 82 Playwright tests across chromium and mobile-chrome,
  with the Shopify payloads mocked and labelled as such. No migration; Alembic
  remains a single head at `0028`.

- **GQL-2 acceptance fix — a connected Shopify store can no longer hide broken
  webhooks.** An independent review returned `GQL-2 requires fixes` on one
  blocking finding, and it was a real one: OAuth completion ran webhook
  registration inside a best-effort `try/except` and threw the `ReconcileReport`
  away, so the merchant was redirected with `shopify=connected` whatever had
  happened. `webhooks_registered_at` stayed null, the card rendered *Connected*,
  and the card's only recovery control was gated on the status *not* being
  connected — so the store that needed it was the one that could not see it. The
  amber note said "reconnect if sync stalls" next to a hidden reconnect button.
  The only way out was disconnecting a perfectly valid OAuth connection.

  Keeping the connection was right; presenting it as healthy was not. A store is
  now disconnected, connected-and-webhook-healthy, or connected-but-degraded,
  all **derived from the existing persisted authority** — `webhook_health()`
  reads `status` and `webhooks_registered_at` and there is no new column, because
  a second health field could only ever disagree with the timestamp it duplicates.
  The API returns `webhookHealth` so no client has to invent its own rule for
  what a null timestamp means; the rule the frontend had invented was "assume
  connected".

  New admin-only endpoint
  `POST /api/v1/integrations/shopify/stores/{store_id}/webhooks/reconcile`. Not
  `/shopify/webhooks/reconcile`, which shares a prefix with the unauthenticated
  HMAC webhook receiver and would be matched as a topic named "reconcile" the
  moment declaration order changed. It calls the *same* reconciler OAuth uses —
  no second implementation and no queue — lists every page before creating,
  never replays a mutation whose outcome is unknown, stamps
  `webhooks_registered_at` only on a fully healthy report, and returns 200 with
  an explicit degraded verdict rather than a fake success. A viewer gets 403; a
  foreign store id is indistinguishable from an unknown one.

  The OAuth callback now consumes the report: `shopify=connected` only when it is
  healthy, otherwise `shopify=connected_webhooks_degraded` — a distinct value
  rather than a flag, so an older frontend cannot fall back to rendering full
  success. No secret or raw error text goes in the query string.

  The Shopify card shows the degraded state, explains that product, inventory and
  order updates may be missed, and offers **Retry webhook setup** while the store
  stays connected. The outcome lands in a polite live region that takes focus
  when the retry settles; viewers see the warning and a read-only explanation but
  no control; nothing fires on render and the mutation does not self-retry.

  Lock contention (finding F-02) is now bounded: `lock_for_update` takes a
  `lock_timeout` of 10s, expressed as PostgreSQL's own setting rather than an
  application timer, scoped with `SET LOCAL` and reset immediately after the lock
  statement so it governs acquiring that row and nothing else. Expiry becomes
  `shopify_webhook_reconcile_busy` (409) instead of an unhandled 500. The
  critical section is unchanged, different stores still do not block each other,
  and `shopify_webhook_reconcile_finished` now logs `lock_wait_ms` and
  `duration_ms` on every outcome.

  Documentation corrections: the three mandatory privacy webhooks are assigned to
  a named milestone, **`SHOPIFY-COMPLIANCE-1`**, marked as blocking App Store
  submission; the claim that an automated webhook "recovery path" existed is
  withdrawn — recovery is deterministic but **manual**, and a scheduled sweep is
  recorded as `SHOPIFY-OPS-1`, unowned; and the inventory test count now
  distinguishes the 11 newly added drift tests from the file's 25 total.

  Red-before: the new backend acceptance suite was written first and run against
  the accepted candidate — 17 failed, 3 passed. Green after: 20 passed. Frontend
  coverage is 12 Playwright tests across two projects with the Shopify payloads
  **mocked**, labelled as such in the file; the same flow runs against real
  PostgreSQL in the backend suite.

### Added

- **GQL-2 — shop currency authority and webhook reconciliation on GraphQL.**
  The first three inventory rows migrate onto the GQL-1 foundation: `GQL-000`
  (`shop.currencyCode`), `REST-008` (webhook list) and `REST-009` (webhook
  create). Same topics, same delivery URIs, same shop-scoped model, same
  `Store.currency` semantics — only the transport changed. Ten of twelve
  versioned Admin REST calls remain.

  A new operation layer (`graphql_operations.py`) holds the three static
  documents; `webhook_reconciliation.py` holds the compare-and-create logic.
  Both were written against the official 2026-07 reference, which corrected
  GQL-1's own proposal: `WebhookSubscription.uri` is the current endpoint field,
  and `callbackUrl` and the `endpoint` union are deprecated. A document written
  from the unverified proposal would have compared endpoints by `__typename` and
  never matched anything.

  **Concurrent registration can no longer duplicate.** OAuth completion,
  reconnect and recovery all call `register_webhooks`; two of them overlapping
  would each list an empty shop, each decide a topic was missing, and each
  create it, leaving the merchant receiving every event twice. Reconciliation is
  now serialised per store with `SELECT … FOR UPDATE` on the `ShopifyConnection`
  row — the same coordination mechanism the bulk-pricing work uses, not a second
  locking architecture, and not an in-process lock that would protect neither of
  two workers. Proven by two reconcilers on two real PostgreSQL connections with
  a `pg_blocking_pids()` rendezvous, alongside a control test showing the same
  race without the lock does duplicate.

  The create mutation is **never retried automatically** — the old REST path
  inherited a generic retry, so a lost response could create a second
  subscription. An unknown outcome is reported as `unknown` and resolved by
  re-listing on the next run, and `webhooks_registered_at` is stamped only when
  every desired subscription is confirmed present with no warnings.

  Shop currency still fails closed with no USD fallback, and a failed refresh
  still retains a previously trusted currency rather than clearing it.

  **No migration was required** — no webhook identifier is persisted anywhere,
  so Shopify's list stays the only authority and `alembic heads` is unchanged at
  `0028`.

  Deliberately not done, and recorded rather than assumed safe: `shopify.app.toml`
  subscriptions are **not** introduced, because `webhookSubscriptions` returns
  only shop-scoped subscriptions and running both modes for one topic would
  duplicate every event with no way for this codebase to detect it; that move
  needs `webhookSubscriptionDelete`, which is GQL-6's. Nothing is deleted in this
  phase — duplicates and mismatches are reported as warnings.

  The inventory drift test now checks the document against the **code**, not just
  against its sibling document: a row may only claim `migrated`/`removed` if the
  call site is genuinely gone, and a cited test file must actually exist. That
  guard immediately caught a pre-existing defect — both `OAUTH-*` rows cited a
  test file that has never existed in this repository.

  Known gap, unowned by any GQL phase: the three mandatory privacy webhooks
  (`customers/data_request`, `customers/redact`, `shop/redact`) have no handler
  anywhere in the backend. They are configured outside this API, so they are not
  a REST-migration row, but they are an App Store submission blocker. See
  `docs/shopify-graphql/GQL2_SHOP_WEBHOOKS.md`.

  No live Shopify request was made; live verification is recorded as not run.

- **GQL-1 — Shopify Admin GraphQL client foundation and complete REST
  inventory.** The first Shopify App Store launch-readiness phase. Shopify
  requires new public apps to use GraphQL exclusively, and this platform makes
  twelve versioned Admin REST calls; every one is a submission blocker.

  `ShopifyGraphQLClient` is the single surface future operations go through:
  pinned to `2026-07` via its own setting (separate from the REST version, since
  the two are on different clocks, and validated so `latest` is refused at
  configuration load rather than at the first request); canonical-domain only,
  normalising nothing, rejecting schemes, ports, paths, IPs and suffix
  lookalikes through the *existing* OAuth domain authority rather than a second
  validator; redirects not followed; the token absent from `repr`, logs and
  exceptions, and absent from the shared connection pool, which carries no
  credentials at all.

  Responses are typed and carry Shopify's request id and the full
  `extensions.cost` block, parsed defensively so malformed telemetry can never
  fail a good response. Top-level `errors` **fail closed** even when `data` is
  partially populated — `allow_partial_data` exists to make the alternative
  explicit and is unused. Mutation `userErrors` are a typed failure through a
  contract that takes the mutation field name from the caller, and a document
  that forgot to select them is refused rather than read as success. Queries
  retry only timeouts, 429, 502/503/504 and `THROTTLED`, with `Retry-After`,
  cost-derived and exponential waits all bounded and jittered; `MAX_COST_EXCEEDED`
  is never retried, and **mutations never retry automatically**.

  Also: a `ShopifyGid` value object with no API that accepts an array index (a
  previous generation derived variant identity from position and repriced the
  wrong variant), and bounded cursor-pagination helpers that refuse a repeated
  cursor or `hasNextPage` with no `endCursor`.

  `docs/shopify-graphql/` gains the inventory in both Markdown and JSON — twins
  guarded by a test so they cannot drift — plus the foundation decision record
  and the six-phase roadmap. Ten of fifteen proposed replacements are marked
  `unverified` on purpose: the exact 2026-07 fields have not been read yet, and
  guessing would be worse than the gap.

  Acceptance fixes on the same phase, each with a recorded red-before result:
  parameterized Shopify GIDs (`gid://shopify/InventoryLevel/123?inventory_item_id=456`)
  were rejected outright because the parser terminated the id at `?`; retry
  eligibility was taken from a caller-declared `OperationType`, so a mutation
  declared as a query got three HTTP attempts at a `productCreate`; and the
  foundation document wrongly called `2026-10` the latest stable release when it
  is the release candidate until 1 October 2026. GIDs now support both documented
  shapes with the complete opaque string as the persistence authority, and the
  document is parsed with `graphql-core` (MIT, no runtime dependencies, declared
  explicitly) so the operation's kind is a fact rather than an assertion — a
  mislabelled mutation now makes zero HTTP requests.

  **No REST call was migrated, no behaviour changed, no migration added, and no
  live Shopify request was made** — every test runs through
  `httpx.MockTransport`.


- **M3A-4B — Draft impact, bulk application UI, and final M3A hardening** —
  **Settings → Global Rules → Preview and Impact**: what the active rules would
  do to existing drafts, with search, filters, per-product and per-variant
  figures, and a confirmation stating selected / ready / held / published /
  expected changes before anything is written. Missing figures read
  "Unavailable", never zero. Selection never enumerates the catalogue: "select
  all matching" sends the *filter*, and the server expands it once into a
  durable snapshot capped at 5000. `202` returns a `pending` run; the screen
  polls to a terminal state and stops, and the application id and open section
  live in the URL so a refresh resumes the same run rather than starting
  another. Results are per item, filterable by outcome, and a partial run shows
  its successes. Published products are marked, unselectable, excluded from
  select-all and refused by the service.

  Rules are now scoped **by name**: `GET /global-rules/targets/{kind}` returns
  labels and ids for products, variants, categories and stores, behind a
  keyboard-operable combobox, with raw identifier entry kept as an explicit
  advanced fallback.

  Hardening: a heartbeat plus `pricing.reconcile_applications` recovers runs
  abandoned by a crashed worker without ever stealing a healthy one; concurrent
  rule-version writes return `409` with a merchant-facing message; preview,
  impact and apply carry named per-tenant quotas through the *same* limiter as
  the global middleware; and `scripts/verify_rule_version_integrity.py` reports
  on the deferred typed-reference constraint without touching data. Migration
  `0027`, additive. **M3A is complete.**

- **M3A-4A — Global Rules management UI** — **Settings → Global Rules**
  (`/settings/global-rules`): pricing rules, shipping rules, application
  behaviour, a live calculator and append-only rule history, on one route.
  Strategy fields appear only when the selected strategy uses them; scope
  controls show only the identifier that scope needs; markup versus gross
  margin is explained with a worked example. **Every live figure comes from
  `POST /global-rules/preview`** — no pricing formula is duplicated in
  TypeScript. Updates and activations carry `expectedUpdatedAt`, and a 409
  raises a banner offering *Reload latest version* or *Keep my changes*; there
  is no autosave and no mutation on page load. Owners and admins manage;
  everyone else reads, previews and reads history, with mutation controls
  absent rather than disabled. Two supporting API additions: the preview now
  returns `priceBeforeRounding`, and rule history is paginated in the standard
  `Page` envelope. The draft impact and bulk-application screen is **M3A-4B**
  and is not included. See
  `docs/dsers-parity/M3A_GLOBAL_PRICING_RULES.md`.

- **M3A — Global pricing and shipping rules** (backend only; UI is M3A-4) —
  one calculation shared by import, a read-only impact preview and a confirmed
  bulk application. Scope precedence
  `variant > product > category > store > global`, versioned rules with an
  append-only history, and a fail-closed engine that returns a
  machine-readable reason instead of a price whenever a supplier figure is
  missing. Confirmed applications run on the **existing Celery queue**: the
  API returns `202` with a `pending` run, the worker claims it atomically,
  processes bounded batches that resume correctly after a retry, and records
  one result row per item including the ones nothing happened to. The broker
  payload is a single application id, and the tenant is read from the row
  rather than the message. Migrations `0023`-`0026`, all additive. See
  `docs/dsers-parity/M3A_GLOBAL_PRICING_RULES.md`.

- **M2B — Rich-text product description** — TipTap 3 editor
  (`rich-text-description-editor.tsx`) replaces the raw-HTML `<textarea>` on
  the draft editor's Description tab. **Storage format unchanged** (sanitized
  HTML in `products.description`), so no migration and no change to the
  Shopify publish path. `DESCRIPTION_MAX_LENGTH = 64_000` is now enforced at
  the API boundary and shown as a live counter. No image insertion or upload
  (that is M2D-A); existing supplier images and tables are preserved. See
  `docs/dsers-parity/M2B_RICH_TEXT_DESCRIPTION.md`.

- **M24A — Shopify selling-currency authority + production FX** — GraphQL
  `shop.currencyCode` sync (Admin API **2026-07**); migration `0020`
  `stores.currency_last_synced_at` (nullable, never backfilled); Open Exchange
  Rates provider; freshness vs Redis retention windows; explicit
  `POST /stores/{id}/currency/refresh`. See `docs/FX_RATE_PROVIDER.md`,
  `docs/SHOPIFY_API_MODERNISATION.md`. **Not** production pricing readiness
  (M24B/M24C remain).

### Fixed

- **A cancellation racing a finishing run could rewrite it as cancelled
  (M3A-H1)** — `cancel()` was a read-then-write: a plain `SELECT`, the
  terminal-state guard evaluated against it, then a flush. Nothing held the row
  in between and there is no version column, so the flush emitted
  `UPDATE … WHERE id = ?` and overwrote whatever had landed meanwhile. A
  cancellation arriving while a worker was in `finalize` read `running`, passed
  the guard, waited on the worker's lock and then rewrote a **completed** run as
  cancelled — the one outcome the guard exists to refuse. Product prices,
  result rows and progress were never affected; the record of what happened was.
  Cancellation now takes the same tenant-scoped `SELECT … FOR UPDATE` the worker
  paths use, so inspecting the status and mutating it are one atomic step: a
  finished run is refused with `409`, an already-cancelled run is returned
  without rewriting `finished_at`, the reason, the counters or any audit row,
  and a `pending`/`running` run is cancelled cooperatively with committed
  batches intact. Cross-tenant identifiers still find nothing and return `404`.
  No schema change: the existing status column plus row locking is the whole
  mechanism.

- **The mid-batch concurrency test could not fail (M3A-H2)** — it slept 0.5 s
  and accepted either reclaim outcome. Replaced with deterministic
  two-connection tests that wait on PostgreSQL's own `pg_blocking_pids()`,
  assert the worker is the blocking backend, and prove the reclaim CAS
  re-evaluates the moved heartbeat and takes nothing — plus a negative control
  proving the helper raises when there is no contention.

- **Recovery of a stuck bulk application never actually resumed it (M3A)** —
  the reconciler cleared `claimed_by_task_id` but left the row `running`, so
  the message it published reached a worker that saw a `running` row owned by
  someone else, reported `already_running` and processed nothing. Every
  recovery logged `requeued` and rescued nothing. Recovery now returns the run
  to `pending` and unowned in one conditional UPDATE, commits, and *then*
  publishes; the new delivery takes it through the ordinary `pending → running`
  claim under its own real Celery task id, and resumes from the durable cursor.
  The reclaim is pinned to the exact status, heartbeat, lease and recovery
  count the sweep observed, so a worker that checked in keeps its run and two
  racing reconcilers reclaim exactly once. A failed publish leaves the run
  `pending` with `enqueued_at` NULL, which the pending sweep repairs.

- **A worker whose run had been taken over kept writing to it (M3A)** — the
  worker claimed once and then looped over batches with nothing re-checking
  ownership, so a reclaimed worker carried on writing prices, `PriceChange`
  rows, result rows, progress and heartbeats into a run that belonged to
  another worker, and could mark that healthy run `failed` from its own error
  handler. A durable lease (`lease_token`, migration `0028`) is now minted on
  every grant of ownership and verified under `SELECT … FOR UPDATE` at the
  start of every batch, in the same transaction as the writes. `finalize`,
  `fail` and the heartbeat are owner-conditional through the same lock; a
  replaced worker reports `superseded` and writes nothing at all. The per-item
  unique constraint was never the safety mechanism here — product writes happen
  before it.

- **Runs abandoned before migration `0027` were unrecoverable (M3A)** —
  `heartbeat_at < cutoff` is NULL, never true, for rows that predate the
  column, so the sweep could not see them and they sat `running` forever.
  `0028` backfills them, and the sweep now treats a NULL heartbeat as stale
  once the row itself is older than `STALE_AFTER` — so a legitimately
  just-started run is still never reclaimed early.

- **One member could exhaust an endpoint quota for a whole workspace (M3A)** —
  the named endpoint limiter keyed on `tenant` alone. It is now
  `ratelimit:{name}:tenant:{tenant_id}:user:{user_id}`, taken from verified
  token claims through a principal the dependency requires, so authentication
  resolves first by construction and no caller-supplied header can choose a
  bucket. Two tenants stay isolated, endpoints keep separate names,
  `Retry-After` is unchanged, there is still one `FixedWindowLimiter`, and the
  documented fail-open-on-Redis-outage policy is preserved and now tested.

- **A `202` could be returned for an application that was then rolled back
  (M3A)** — the queue hand-off ran as a FastAPI background task, on the
  assumption that yield-dependency teardown (and therefore the commit) happened
  first. It does not: Starlette awaits background tasks inside the response
  call, still within the session's scope. The worker looked up a row its own
  request had not written, the `NotFoundError` escaped after the response had
  started, and the whole transaction rolled back. The message is now published
  from an `after_commit` hook, and `reconcile_applications` republishes any
  `pending` run the broker never accepted. Found by running a real worker.
- **Every Celery task shared one database engine across `asyncio.run` loops
  (M3A)** — the engine is a module-level singleton whose pooled connections
  belong to the loop that opened them, so the *second* task in a worker failed
  with `'NoneType' object has no attribute 'send'`. Fixed for the pricing tasks
  by disposing the engine before the loop closes; **the same latent bug remains
  in the other task modules** and is recorded in
  `docs/dsers-parity/M3A_GLOBAL_PRICING_RULES.md` §7.
- **Database constraint text was returned to API clients (all repositories)** —
  `TenantScopedRepository._translate_integrity_error` put the raw driver
  message into `details`, which is serialised into the error envelope, so any
  client able to provoke a duplicate received index names, column names and the
  offending values. The text now goes to the log, and the response carries the
  correlation id that ties the two together.

- **Rule history was an unbounded result set (M3A)** —
  `GET /global-rules/{kind}/{id}/history` returned every version of a rule as a
  bare array. History is append-only, so a long-lived rule's trail grows
  without limit; it is now paginated and returns the same `Page` envelope as
  every other list endpoint in the API.

- **Variant-scoped pricing rules never applied anywhere (M3A)** —
  `PricingRuleRepository.find_candidates` had no `variant_id` clause, unlike
  its shipping twin, so a variant-scoped rule was never in the candidate set
  the resolver chose from. The resolver ranked variant highest and simply
  never saw one, which is why unit tests of the precedence logic passed
  throughout. The narrowest scope in the model was silently dead at import, in
  preview and in bulk apply.
- **A misconfigured pricing rule could fail an entire import (M3A)** —
  `compute_sell_price` raises when a strategy is missing the field it needs,
  and `_apply_global_rules` did not catch it, so the exception rolled back the
  transaction that had just written the supplier snapshot and the merchant got
  no draft at all. Pricing failures now flag the draft
  (`pricing_calculation_failed`) and let the import complete.
- **A rule denominated in another currency priced silently (M3A)** — the M3A
  path did no currency check, so a GBP rule's `min_price`, `max_price`,
  `markup_fixed`, `min_profit` and `fees_fixed` were applied to a USD cost as
  though the numbers were comparable. It now fails closed with
  `fx_rate_unavailable`.

- **Draft description saves no longer write on every autosave (M2B)** —
  `ProductService.update_product` sanitized the description *after* the
  no-op comparison, so a rich-text client's re-serialised markup (`<br />`
  vs `<br>`, a stripped `class`, a rewritten `rel`) read as a change and
  wrote an identical value, moving `updated_at` and spuriously invalidating
  other editors' version tokens every 1.8s. Sanitization now runs first.
- **Opening a draft no longer rewrites it (M2B)** — the rich-text editor
  treated ProseMirror's own normalisation transactions as merchant edits,
  marking the form dirty on load and autosaving a draft nobody had touched.
  Edits are now recognised from actual input (DOM events and toolbar
  commands), not from any document change.
- **Supplier images and tables are no longer destroyed by the description
  editor (M2B)** — the editor's initial schema had no image or table node,
  so ProseMirror discarded them on parse and the autosave above persisted
  the loss. The schema now covers everything the sanitizer allows; note
  that "can exist in the document" and "can be created from the toolbar"
  are deliberately different sets.

- **Pricing currency integrity** — Prohibit 1:1 cross-currency conversion;
  block calculated profit/proposed prices when FX is required and unavailable;
  Shopify selling currency requires a verified sync timestamp — no tenant /
  supplier / USD fallback for unsynced stores. See
  `docs/PRICING_CURRENCY_DEFECT_AUDIT.md`.
- **Pricing currency integrity, closing the gap the above left open** —
  `ProductVariant.sell_price` had no record of which currency it was
  actually computed in (`variant.currency` is the *supplier's* currency, a
  different field); a price set for a GBP store would have silently reached
  Shopify as an unlabelled number after a switch to a USD store. Added
  `sell_price_currency` (migration `0021`), stamped whenever pricing writes
  a price; a mismatch — or a price predating this column — surfaces as
  `needsRecalculation` rather than being trusted; Shopify publish now blocks
  outright when an enabled variant's price currency doesn't match the
  store's verified currency, never sending a mislabelled amount. See M23/M26
  in `docs/TECHNICAL_DEBT.md`.
- **M24B — AliExpress `target_currency` derived from destination, not
  hardcoded** — every refresh/sync path (`POST /products/{id}/sync`,
  `POST /drafts/{id}/refresh`, the scheduled resync task) previously omitted
  `currency` and silently defaulted to `"USD"` regardless of the product's
  real destination — a GB-destined refresh asked AliExpress for USD pricing.
  `ImportDestinationService.resolve_currency()` now derives it (explicit
  request → verified store currency → destination's mapped market currency,
  GB→GBP/US→USD → tenant default), mirroring the existing ship-to-country
  resolver. Also fixed a live-reproduced mislabeling: `Product.currency` was
  set from AliExpress's unlocalized native currency
  (`ae_item_base_info_dto.currency_code`, always the seller's own currency
  regardless of what was requested) while `cost_price_min`/`cost_price_max`
  are computed from the correctly-localized SKU prices — now derived from
  the SKUs themselves; the native currency is preserved separately as
  `supplier_native_currency` (migration `0022`, alongside `import_currency`
  recording what was actually requested). Live-traced against a real
  product; shipping/tax/landed-cost remain out of scope — see
  `docs/ALIEXPRESS_LOCALIZED_PRICING.md` and M27 in `docs/TECHNICAL_DEBT.md`.

### Changed

- **Money / FX domain** — `Money` value object, `FxService` as sole FX
  entrypoint (`unavailable` / `stub` / `openexchangerates`), Decimal-safe JSON
  rate parsing. See `docs/MONEY_AND_CURRENCY_ARCHITECTURE.md`.

### Changed

- **Premium draft editor header** — Three-layer sticky chrome with thumbnail,
  breadcrumb, supplier-sync/readiness/SEO badges, Publish-primary action
  hierarchy, More menu for tertiary tools, Draft Preview sheet (not a tab
  switch), tablet wrap + mobile bottom action bar. Unsupported Duplicate /
  Archive / Delete actions are hidden until APIs exist. See
  `docs/PREMIUM_PRODUCT_EDITOR_UI.md`.

### Added

- **AliExpress import destinations** — Import as Draft records `ship_to_country`
  on each import job and `import_ship_to_country` / `import_ship_to_checked_at`
  on the product (migration `0019`). Destination resolution prefers the dialog
  selection, then store `settings.countryCode`, then optional
  `DEFAULT_SHIP_TO_COUNTRY`, then the last successful import — never a silent
  US default. Publish warns/blocks when the store market differs from the
  import destination.
- **Premium Product Editor** — Sticky header/inspector, autosave, SEO workspace
  + advisory score (no meta-keywords export), editable shipping/customs,
  target-margin pricing, post-publish View in Store / Manage in Shopify when
  URLs are verified. Migration `0018` persists listing handle/admin/storefront
  fields. See `docs/PREMIUM_PRODUCT_EDITOR_UI.md`.
- **Draft Product Editor Stage 5** — Pricing workspace (`GET/POST …/pricing`)
  with Decimal profit/margin/break-even; inventory freshness + supplier stock
  labels; shipping package/logistics columns (migration `0017`). Missing
  freight is never coerced to zero.
- **Draft Product Editor Stage 4** — Media reorder/featured/alt text/add-by-URL/
  remove; variant merchant SKU, sell/compare-at prices, enable/disable.
  Migration `0016`. Supplier sync preserves merchant image order/alt and
  merchant variant pricing fields.
- **Draft Product Editor (Stages 1–3)** — Clickable Drafts rows open
  `/drafts/{id}` with Overview/Description/SEO editing, Save Draft
  (`PATCH /api/v1/drafts/{id}`), Refresh Supplier Data, readiness sidebar, and
  Publish to Store panel. Reuses Product Editor write/sanitize APIs. See
  [DRAFT_PRODUCT_EDITOR_PLAN.md](docs/DRAFT_PRODUCT_EDITOR_PLAN.md).
  Merged to `develop` (`da83d52`).
- **Product Workspace V2 Stage 0** — Drafts vs Products are publication
  projections over the same `Product` aggregate. `GET /api/v1/drafts` lists
  products with no synced `StoreListing`; `GET /api/v1/products` lists only
  successfully published products; `GET /api/v1/products/workspace-counts`
  feeds sidebar badges. Frontend routes `/drafts`, `/products`, and
  `/imports/history`; supplier ingestion is labelled **Import as Draft**.
  Migration `0015` indexes `(tenant_id, product_id, status)` on
  `store_listings`. See [PRODUCT_WORKSPACE_V2_PLAN.md](docs/PRODUCT_WORKSPACE_V2_PLAN.md).

### Fixed

- **AliExpress ship-to errors** — `rsp_code 482 SHIP_TO_COUNTRY_PROHIBITED` maps
  to `aliexpress_ship_to_prohibited` (422) with an actionable destination
  message; `605` maps to `aliexpress_product_unavailable` (404). Empty product
  envelopes are no longer labelled “Product not found.” Integration tests use
  isolated `droppilot_test` so Alembic head mismatches on the shared developer
  DB do not block the suite. Premium-editor migration `0018` is preserved on
  develop for stamp compatibility.
- **A-06** — CI `frontend-e2e` job runs Playwright chromium against a live API
  with `SECURITY_RATE_LIMIT_REQUESTS=1000`; `.env.example` documents the e2e
  ceiling; Playwright serves the standalone build via `npm run start:e2e`
  (also closes A-13 `next start` mismatch).
- **A-16 / A-09** — Finished the uncommitted Shopify webhook-tunnel workaround
  (was broken — a referenced function was never defined), added the two
  missing webhook topics (`products/create`, `app/uninstalled`), handled
  `app/uninstalled` by marking the connection `ERROR` immediately, and made
  replay-dedup fail closed (503) on a Redis outage for mutating topics
  (`orders/create`, `orders/updated`, `app/uninstalled`) while non-mutating
  topics still acknowledge.
- **A-15 (Shopify)** — Disconnecting a store now catches failures and shows a
  per-store error message instead of failing silently; the connected stores
  card also now shows when each store was originally connected, not just its
  last sync time.
- **A-02** — Logout (and mid-session token clear) call `queryClient.clear()` so
  a shared browser cannot show the previous tenant's React Query cache;
  `router.refresh()` alone was insufficient.
- **A-05** — Compose frontend defaults to `NEXT_PUBLIC_API_URL=http://localhost`
  so the SPA uses nginx same-origin `/api`; CORS includes the nginx origin; CI
  smoke asserts the bundle does not embed `:8000`.
- **A-04** — Shopify product publish is Celery-safe: create uses deterministic
  handle `droppilot-{product_id}` and adopts an existing Shopify product on
  redelivery instead of posting a duplicate.
- **A-03** — Platform-global AI prompt create / version / activate are refused
  unless `AI_ALLOW_PROMPT_MUTATION=true` (default off). Tenant admins can still
  list, history, and test-render. Tests opt in via conftest.
- **A-01** — Shopify `shop_domain` is globally unique (migration `0012`);
  webhooks resolve the owning tenant by indexed domain lookup instead of a
  capped table scan; connect rejects a shop already bound to another workspace.
- AliExpress connect is platform-credential only: merchants no longer enter App
  Key / App Secret. OAuth uses `ALIEXPRESS_APP_*` from the environment; tenant
  rows store encrypted seller tokens only (migration `0009`).
- Shopify connect rejects custom storefront domains; OAuth callback failures are
  classified (`hmac` / `state` / `exchange`) without logging secrets. See
  [SHOPIFY_CONNECTION_DEBUG_REPORT.md](docs/SHOPIFY_CONNECTION_DEBUG_REPORT.md).

### Added

- **Phase 8.1 Cloudflare wildcard verify** — public `*` route makes
  `/install`, `/webhook`, and `/health/live` reach FastAPI; path-specific
  tunnel rules 1–4 redundant; canonical webhook base set to
  `…/shopify/webhook`; live merchant consent still pending (M17). See
  [PHASE_8_1_LIVE_DEPLOY.md](docs/PHASE_8_1_LIVE_DEPLOY.md).
- **Phase 8.1 public routing re-verify** — Cloudflare Tunnel still path-scoped;
  public `/install` and `/webhook` remain 404; canonical webhook documented as
  `…/shopify/webhook`; Product Editor stashes left untouched; M17 still open.
  See [PHASE_8_1_LIVE_DEPLOY.md](docs/PHASE_8_1_LIVE_DEPLOY.md).
- **Phase 8.1 live deploy notes** — local Phase 8.1 routes confirmed after
  uvicorn restart; public `/install` still 404 due to path-scoped Cloudflare
  Tunnel; webhook base aligned to public POST callback; Partner/Cloudflare
  checklist. See [PHASE_8_1_LIVE_DEPLOY.md](docs/PHASE_8_1_LIVE_DEPLOY.md).
  M17 remains open pending merchant consent + tunnel path updates.
- **Phase 8.1 — Shopify OAuth production quality** — App URL install
  (`GET …/shopify/install` with HMAC), claim-install for anonymous App Store
  entry, Connect dialog (domain only), webhook base validation + singular
  `/webhook` receiver, idempotent webhook registration, disconnect revoke,
  uninstall releases shop claim, reconnect store reuse. Plan/completion:
  [PHASE_8_1_PLAN.md](docs/PHASE_8_1_PLAN.md),
  [PHASE_8_1_COMPLETION.md](docs/PHASE_8_1_COMPLETION.md). Live Partner OAuth
  consent remains open (M17).
- **Phase 8.1 verification follow-up** — documents that `phase-8-1-complete`
  was tagged before build/Playwright/live OAuth were fully verified; records
  subsequent green `next build`, Playwright Shopify (26) + integrations/shell
  (62), production `/install` 404, and remaining M17. Tag **not** moved. See
  [PHASE_8_1_VERIFICATION.md](docs/PHASE_8_1_VERIFICATION.md).
- **Shopify production install plan** — Partner checklist, AutoDS migration
  steps, webhook base mismatch findings. See
  [SHOPIFY_PRODUCTION_INSTALL_PLAN.md](docs/SHOPIFY_PRODUCTION_INSTALL_PLAN.md).
- **Shopify install-flow audit** — why shop domain is required for
  authorization-code grant and why AutoDS-like UX needs App URL architecture.
  See [SHOPIFY_INSTALL_FLOW_AUDIT.md](docs/SHOPIFY_INSTALL_FLOW_AUDIT.md).
- **Phase 9 Stage 3** — product optimisation data architecture: migration
  `0011` (SEO/marketplace/AI columns + `product_versions`),
  `ProductOptimizationService` (StubProvider via Stage 2 prompts), versions /
  optimize / activate APIs, products-table AI status + Optimize + History UI.
  See [PHASE_9_STAGE_3_COMPLETION.md](docs/PHASE_9_STAGE_3_COMPLETION.md).

### Changed

- **Phase 8.1** — `app/uninstalled` now deletes the Shopify connection (releases
  the global shop claim) instead of leaving an ERROR row; mutating webhook
  processing failures no longer ACK silently.

---

## [phase-8] — 2026-08-01

Shopify as the first sales channel. See
[PHASE_8_COMPLETION.md](docs/PHASE_8_COMPLETION.md) and
[SHOPIFY_INTEGRATION.md](docs/SHOPIFY_INTEGRATION.md).

### Added

- Migration `0008`: `shopify_connections`, `store_listings`, `orders.store_id`
- OAuth connect/callback/status/disconnect; Fernet-encrypted access tokens
- Product publish, inventory/price push, order import (poll + webhooks)
- Celery `shopify.*` tasks and beat entry
- Integrations UI Shopify card

### Verified

- Backend: ruff, mypy strict, **592** pytest
- Frontend: lint, typecheck, build
- Playwright integrations (chromium): **11** passed
- Live Shopify Admin/OAuth: **not run** (no Partner credentials)

---

## [phase-7] — 2026-07-31

Production hardening and operational readiness. See
[PHASE_7_COMPLETION.md](docs/PHASE_7_COMPLETION.md).

### Added

- Compose **beat** service and worker healthcheck
- CI on `develop`: image builds, `docker compose config`, Celery broker job,
  best-effort compose smoke
- `workers.health` task + `scripts/verify_celery_broker.py`
- Webhook HMAC (opt-in) and shed-without-429 limiter
- Email verification foundation: migration `0007`, logging mailer,
  `/auth/verify-email/*`, `RequireVerified` (flag-gated)
- `docs/STORE_CHANNEL_DECISION.md`, `docs/PHASE_7_PLAN.md`

### Changed

- Access tokens carry `email_verified`
- Frontend: removed unused notification Zustand store; removed stale Import nav

### Verified (local)

- Backend: ruff, mypy strict, pytest (583+)
- Frontend: lint, typecheck, build
- Live AliExpress and local Docker/Celery: **not** available on this machine —
  CI path documented

---

## [phase-6] — 2026-07-31

Inventory synchronisation, dynamic pricing, multi-store management, automation,
notifications, real analytics, and shipment tracking extensions.

Closed with migration `0006`, integration tests against real PostgreSQL, live
`AliExpressClient.call()` for the inventory dependency, and frontend pages for
every new module. See [PHASE_6_COMPLETION.md](docs/PHASE_6_COMPLETION.md).

### Added

**Domain** (`migration 0006`)
- `stores`, inventory sync runs/changes, pricing rules/changes, automation
  rules/runs, notifications, analytics daily rollups
- Product `sell_price` and optional `store_id`

**Services & APIs**
- Inventory sync (idempotent, reuses product import)
- Pricing engine (percentage / fixed / tiered; min profit / max price guards;
  preview before apply; audit trail)
- Store management with health and statistics
- Automation dispatcher (background-oriented)
- Notification centre
- Analytics dashboard aggregates

**Celery**
- `inventory.sync`, `pricing.recalculate`, `automation.run`,
  `shipment.refresh`, `analytics.aggregate`, `cleanup.old_notifications`

**Frontend**
- `/inventory`, `/pricing`, `/stores`, `/automation`, `/notifications`,
  `/analytics`, `/shipments`
- Dashboard and analytics consume live `/analytics/dashboard` — `MOCK_*` removed

### Verified

- **566 backend tests** — ruff, mypy strict, pytest all green
- **Live `AliExpressClient.call()`** — `product.get` success for inventory path;
  category / order error path re-checked
- Frontend lint, typecheck, build
- Tenant isolation tests for new repositories
- Playwright: Phase 6 ops smoke + updated shell dashboard tests (chromium)

### Not verified

- Celery under a live RabbitMQ broker (M15)
- Webhook signature verification (M11)
- Docker deployment (C1)
- Marketplace OAuth for sales channels (manual stores only)

---

## [phase-5] — 2026-07-31

Order management, fulfilment, and synchronisation from AliExpress.

Closed with live `AliExpressClient.call()` verification, migration `0005`,
integration tests against real PostgreSQL, and an Orders UI backed by the real
API. See [PHASE_5_COMPLETION.md](docs/PHASE_5_COMPLETION.md).

### Added

**Contract layer** (`app/integrations/aliexpress/orders.py`)
- Wire models and parsers for order detail, commission list, and logistics
- Captured live fixtures for error envelopes and list responses
- Client fix: unwrap `error_response` envelopes before error mapping

**Domain model** (`app/models/order.py`, migration `0005`)
- `orders`, `order_items`, `shipments`, `tracking_events`, `order_events`,
  `order_sync_runs`
- Validated fulfilment transition map

**Sync service** (`app/services/order_sync.py`)
- Idempotent incremental import; timeline merge; statistics

**Order API** (`app/api/v1/orders/router.py`)
- List (filters), statistics, sync, detail, timeline

**Background sync** (`app/tasks/orders.py`)
- `orders.sync_all`, `orders.sync_one_store`, `orders.refresh_status`,
  `orders.cleanup` with Celery beat entries

**Webhook processing**
- Redis replay protection, classification, delivery counters; unsigned payloads
  never mutate order state directly

**Frontend orders module**
- `/orders` list with filters, search, pagination, sync dialog, live statistics
- `/orders/[orderId]` detail with items, shipments, tracking, timeline
- Dashboard live order-synchronisation row

### Verified

- **523 backend tests** — ruff, mypy strict, pytest all green
- **Live `AliExpressClient.call()`** — category success; order get error path;
  commission order list capture
- **Tenant isolation** — SQL compile tests on order repositories; integration
  cross-tenant 404
- **Frontend lint, typecheck, build** — all pass
- **Playwright orders suite** — 16 passed against real backend

### Not verified

- Populated order-detail success body from live API (M16)
- Celery order tasks under a live broker/worker (M15)
- Webhook signature verification (M11 — unsigned)
- Docker deployment (C1 — unchanged)

---

## [phase-4] — 2026-07-31

Product import and catalogue synchronisation from AliExpress.

Closed with contract discovery from live payloads, integration tests against
real PostgreSQL, and a products UI backed by the real API. See
[PHASE_4_COMPLETION.md](docs/PHASE_4_COMPLETION.md).

### Added

**Contract layer** (`app/integrations/aliexpress/catalog.py`)
- Pydantic models parsing real `aliexpress.ds.product.get` and feed payloads
- Captured fixtures committed under `tests/fixtures/aliexpress/`

**Domain model** (`app/models/product.py`, migration `0004`)
- `products`, `product_variants`, `product_images`, `product_imports`
- Unique constraint on `(tenant_id, source, external_id)` for idempotent import

**Import service** (`app/services/product_import.py`)
- Import by supplier product id; feed browse without importing
- Idempotent upsert preserving tenant-set status on refresh

**Product API** (`app/api/v1/products/router.py`)
- List, detail, import, sync, import history, feed browse

**Background sync foundation** (`app/tasks/products.py`)
- `products.sync_one` — refresh one product from its supplier
- `products.sweep_stale` — fan out refresh for stale catalogue rows

**Frontend products module**
- `/products` page with table, empty state, and import dialog
- Real API fetchers in `services/products.ts`

### Verified

- **419 backend tests** — ruff, mypy strict, pytest all green
- **Tenant isolation** — SQL compile tests on four repositories; integration
  tests confirm cross-tenant access returns 404 not 403
- **Real payload parsing** — integration tests use committed capture, not
  invented JSON
- **Frontend lint, typecheck, build** — all pass
- **Playwright** — UI paths pass; import-flow tests skip when live OAuth
  callback cannot complete (documented)

### Not verified

- Celery product sync tasks under a live broker/worker
- Celery beat scheduling for stale-product sweeps
- Live import through production `AliExpressClient.call` (fixture transport in tests)
- Docker deployment (C1 — unchanged)

---

## [phase-3] — 2026-07-31

AliExpress integration foundation. Connection, credentials, client and inbound
webhook only — no product, price, inventory, or order functionality.

Closed with live verification against the real AliExpress gateway (sub-phases
3.5–3.7). See [PHASE_3_COMPLETION.md](docs/PHASE_3_COMPLETION.md).

### Verified live

- **OAuth round trip** completed end to end against `api-sg.aliexpress.com`,
  storing encrypted access and refresh tokens
- **Replay protection** — an OAuth state is deleted on first use and a second
  presentation is refused
- **API permissions** — product, category, search, order and freight endpoints
  reachable; affiliate correctly denied, verified against a known-good denial
  control rather than by absence of evidence
- **Webhook** reachable through `https://api.whiteto.com` and answering 200 for
  every malformed input tried

### Added

**Inbound webhook** (`app/integrations/aliexpress/webhook.py`)
- `POST /api/v1/integrations/aliexpress/webhook`, separate from the OAuth
  callback — different method, caller, contract and response
- Always answers 200, including on an unreadable body, because a delivery agent
  reads the status as a retry instruction
- Logs field names and counts, never payload values: an order notification
  carries buyer names and addresses
- Signature verification is **not** implemented; recorded as M11

### Fixed (during live verification)

- `sign_method` was excluded from the signature base string, which made every
  token exchange fail with `IncompleteSignature` (`fa02dd2`)
- Only the root `Settings` read `.env`, so every nested settings group silently
  ignored the file and ran on defaults (`ba752c9`)
- The OAuth callback required a Bearer token that a browser redirect from
  AliExpress can never carry (`74c6653`)
- Redis connections failed on every request because redis-py negotiates RESP3
  with `HELLO`, which the local server rejects (`74c6653`)

### Added

**Credential encryption** (`app/core/encryption.py`)
- Fernet (AES-128-CBC + HMAC-SHA256, random IV) for third-party credentials
- Key rotation via `MultiFernet`: keys newest-first, decryption tries each, so
  rotation needs no downtime
- Fails closed when no key is configured — refusing beats storing a customer's
  supplier secret in plaintext

**Database** — migration `0003`
- `aliexpress_connections` with encrypted secret and token columns, status,
  expiry, and last-sync tracking. Unique per tenant

**Integration package** (`app/integrations/aliexpress/`)
- Signed HTTP client with timeouts, jittered exponential backoff, and typed
  error mapping. Retries only failures that could resolve themselves
- Inspects the response body regardless of status, because AliExpress reports
  failure inside HTTP 200 as often as through a status code
- OAuth signing and a single-use, server-side, random `state` token

**Outbound rate limiting** (`app/integrations/rate_limiter.py`)
- Per tenant and provider. **Fails closed**, the opposite of the inbound
  limiter: exceeding a provider's quota can suspend the application key for
  every tenant

**Endpoints**
- `POST /api/v1/integrations/aliexpress/connect` (admin or owner)
- `GET /api/v1/integrations/aliexpress/callback`
- `GET /api/v1/integrations/aliexpress/status`
- `DELETE /api/v1/integrations/aliexpress/disconnect` (admin or owner)

**Background tasks** — `health_check` and `sweep_health_checks`, with tenant
context bound per connection; integration work routed to its own queue

**Frontend** — `/settings/integrations` with real server-driven connection
state, and a settings index that is now a genuine hub rather than a placeholder

**Documentation** — `docs/ALIEXPRESS_INTEGRATION.md`

### Changed

- Disconnect **hard-deletes** the connection, unlike everything else in the
  platform. A customer who disconnects has asked us to forget their credentials
- Celery `task_routes` sends `integrations.*` to a dedicated queue

### Fixed

- **Docker images installed a stub `app` package into site-packages** (debt item
  M1). The real code only won by `sys.path` ordering, so any command run from a
  different directory resolved to the empty stub. The builder now installs
  dependencies only, extracted from `pyproject.toml`

---

## [phase-2-complete] — 2026-07-31

Application shell, navigation, and SaaS UI foundation. Frontend only; no
business functionality.

### Added

**Application shell**
- `AppShell` — sidebar, top bar, and a main region that owns scrolling so the
  chrome stays put without `position: fixed`
- Collapsible desktop sidebar with six navigation sections, tooltips when
  collapsed, and a persisted collapse preference
- Responsive drawer below `md`, sharing the same navigation component as the
  desktop rail so the two cannot diverge; closes on navigation and when the
  viewport grows past the breakpoint
- Top bar with notification centre, theme toggle, and a user menu showing name,
  email, tenant, and role

**Navigation**
- `lib/navigation.ts` — one manifest feeding sidebar, drawer, and top bar. Each
  entry declares whether its destination exists; unbuilt ones render as
  non-interactive items, so primary navigation can never reach a 404

**Dashboard**
- Six stat cards with trend indicators that decouple direction from sentiment,
  so a metric where down is good is not painted red
- Three reusable charts — sales area, stacked orders, horizontal product
  performance — on a shared chart theme that reads design tokens at runtime, so
  a theme switch recolours them with no JavaScript
- `ChartContainer` owning all four chart states: loading, error, empty, populated

**Design system** — nine primitives: `avatar`, `tooltip`, `sheet`, `separator`,
`empty-state`, `error-state`, `page-header`, `coming-soon`, plus `stat-card` and
`chart-container` under `components/dashboard/`

**Routes** — `/products`, `/stores`, `/orders`, `/analytics`, `/settings`,
`/unauthorized`, plus loading and error boundaries scoped to the protected group

**State and services** — `notification-store`; `services/dashboard.ts`,
`products.ts`, `stores.ts` as query keys and types with no fetchers, because
those endpoints do not exist

**Testing** — 47 Playwright tests covering sidebar, mobile navigation, theme
switching, protected routes, dashboard, and user menu at 320px, 768px, and
1440px. They register real accounts through the API rather than stubbing it, so
they exercise the actual token and cookie handling

### Changed

- `layouts/sidebar.tsx` and `layouts/top-nav.tsx` moved into
  `components/navigation/` and split into focused components
- `middleware.ts` treats `/unauthorized` as public — it reports a permission
  failure, not an authentication one

### Fixed

- **`CORS_ORIGINS` could not be set in the documented format.**
  pydantic-settings runs `json.loads` on list-typed fields before validators
  execute, so the comma-separated form in `.env.example` raised
  `JSONDecodeError` during boot. The application could not start with its own
  example configuration. Fixed with `NoDecode` plus a validator accepting both
  forms, and covered by regression tests
- The user menu showed the email address twice for accounts with no name set

---

## [phase-1.1] — 2026-07-31

Pre-Phase-2 housekeeping.

### Added

- `CLAUDE.md` — the engineering constitution for this repository
- `docs/TECHNICAL_DEBT.md` — ranked debt register, each item with a trigger
- **Login throttle test coverage** (16 tests). The throttle previously had none,
  while a fixture docstring claimed otherwise. Runs against `fakeredis` so it
  executes everywhere rather than skipping without a Redis server
- **Authorization integration tests** (9 tests) covering each role, a token with
  no roles, a token with only unrecognised roles, and that authorization is
  decided before resource lookup so a 403 does not leak existence

### Changed

- Both `/api/v1/users` endpoints now require a recognised role via
  `RequireViewer`. `require_minimum_role` was previously unit-tested but wired
  to no endpoint
- Sidebar entries for unbuilt destinations render as disabled "Soon" items
  instead of linking to routes that returned 404
- Development branch renamed from `phase-0-foundation` to `develop`

### Fixed

- `frontend/tsconfig.tsbuildinfo` is no longer tracked in git
- Corrected a docstring in `tests/integration/conftest.py` that claimed test
  coverage which did not exist

---

## [phase-1-complete] — 2026-07-31

Authentication and multi-tenant identity. Commit `a21f7b8`.

### Added

**Authentication**
- Argon2id password hashing, with NFKC normalisation so the same password typed
  on different platforms verifies, and transparent rehashing when parameters are
  raised
- Password strength validation: minimum 12 characters, common-password denylist,
  and rejection of passwords containing the user's own email local part
- JWT access tokens (15 minutes) and refresh tokens (30 days), with the `typ`
  claim verified on every decode so a refresh token cannot be used as a bearer
  credential
- Refresh token rotation with reuse detection — replaying a consumed token
  terminates every session for that user
- `AuthenticatedUser` principal bound to context variables, feeding tenant
  filtering in every repository
- Role-based authorization: `require_roles` for exact membership and
  `require_minimum_role` for hierarchical checks
- Login throttling counted against email **and** client IP independently,
  checked before password verification
- Minimum length enforced on the JWT signing key (32 characters, RFC 7518 §3.2)
  in every environment

**API endpoints**
- `POST /api/v1/auth/register` — creates tenant, first user, and owner role
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/refresh`
- `POST /api/v1/auth/logout` — requires no access token
- `POST /api/v1/auth/logout-all`
- `GET /api/v1/auth/me` — roles read from the database, not the token

**Database** — migration `0002`
- `roles` table, seeded with four roles using deterministic UUIDv5 identifiers
  so a role has the same id in every environment
- `user_roles` association table with a composite primary key
- `refresh_tokens` table storing only a SHA-256 hash of each token

**Frontend**
- Sign-in, registration, and password-reset request pages
- `AuthProvider` session management; access token held in memory only, refresh
  token in an httpOnly path-scoped cookie
- Single-flight token refresh in the API client — without it, concurrent 401s
  each rotate the token, the second presents a consumed one, and reuse detection
  signs the user out
- `AuthGuard` route protection distinguishing *loading* from *unauthenticated*,
  so a page refresh does not eject an authenticated user
- Form primitives wiring react-hook-form and Zod to the design system with
  correct `aria-describedby` / `aria-invalid` handling

**Testing**
- 25 integration tests running against real PostgreSQL, building the schema by
  applying the Alembic migrations rather than `create_all`
- 53 new unit tests covering token verification, password handling, and
  authorization

**Documentation**
- `docs/Authentication.md`, `docs/Database.md`

### Changed

- **Tenant identity now comes from a verified JWT claim** instead of the
  `X-Tenant-ID` header. Confined to `app/api/deps.py`; no endpoint, service, or
  repository signature moved
- `users` gains `first_name`, `last_name`, `is_verified`
- `Base.__mapper_args__` sets `eager_defaults=True`, required in an async
  codebase — see Fixed below
- Enum columns pass `values_callable` so member values are persisted, not names
- `docs/DevelopmentSetup.md` rewritten; its `X-Tenant-ID` instructions had
  become actively wrong

### Removed

Each required by the Phase 1 specification. Safe because migration `0001` had
never been applied to any live database.

- `users.full_name` — replaced by `first_name` and `last_name`, with a read-only
  property preserving the display form
- `users.email_verified_at` — replaced by `is_verified`
- `users.role` enum column and the `user_role` type — replaced by `user_roles`.
  Keeping both would give a user's role two sources of truth, and reading the
  stale one is a privilege-escalation bug
- The Phase 0 `UserRole` **enum** was renamed to `RoleName` so the `UserRole`
  **table** could take the name

### Fixed

Three bugs found by running against real PostgreSQL. None was reachable without
a live database, and all three would have reached production.

- **Enum persistence.** SQLAlchemy sent member *names* (`"TRIAL"`) where the
  database expected values (`"trial"`), failing every tenant insert
- **`MissingGreenlet` on login.** Server-side `onupdate` columns are expired
  after a flush; serialising a just-updated user triggered lazy IO, which raises
  in async SQLAlchemy. This broke *every* login
- **No minimum JWT signing key length.** A short key signs and verifies
  normally and is only weaker, so the weakness was entirely silent

---

## [phase-0-complete] — 2026-07-30

Foundation. Commit `b513e4b`.

### Added

- Clean architecture with a strictly one-way dependency direction:
  `api → services → repositories → models`, with `core` depending on nothing
- Multi-tenancy enforced in a single base repository, reading the tenant from
  context so it cannot be forgotten at a call site
- UUID primary keys, UTC timestamps, soft deletes, deterministic constraint
  names, connection pooling
- Global exception handling producing one error envelope for every failure, with
  a request id on every response
- Structured logging with correlation across API and workers
- Redis caching with tenant-namespaced keys; distributed rate limiting with a
  circuit breaker
- Celery and RabbitMQ configured with context propagation and no jobs
- Next.js 15 / React 19 frontend, design system on CSS-variable tokens, light
  and dark themes
- Multi-stage non-root Dockerfiles, Compose stack, Nginx reverse proxy, GitHub
  Actions
- 54 tests, 11 of them targeting tenant isolation directly
- `README`, `Architecture`, `FolderStructure`, `CodingStandards`,
  `DevelopmentSetup`, `Contributing`

### Changed

- `app.workers.tasks` moved to `app.tasks` during finalisation. Tasks are entry
  points, architecturally symmetric with `api/`; `workers/` retains the
  infrastructure that runs them

### Fixed

- Pydantic `ClassVar` error that made the application unimportable
- Missing `email-validator` dependency
- Rate limiter paying a full Redis connect timeout on every request during an
  outage, adding ~4 seconds of latency per request

---

[Unreleased]: https://github.com/Adnan-Zulfiqar/ds-platform/compare/phase-2-complete...HEAD
[phase-2-complete]: https://github.com/Adnan-Zulfiqar/ds-platform/compare/phase-1-complete...phase-2-complete
[phase-1-complete]: https://github.com/Adnan-Zulfiqar/ds-platform/compare/phase-0-complete...phase-1-complete
[phase-0-complete]: https://github.com/Adnan-Zulfiqar/ds-platform/releases/tag/phase-0-complete
