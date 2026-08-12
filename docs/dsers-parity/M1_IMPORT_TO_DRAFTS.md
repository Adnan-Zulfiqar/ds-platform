# M1 — AliExpress Product Import → Editable Draft

Status: **Delivered.** Branch: `feature/dsers-parity-m1-import-drafts`.
Audit date: 2026-08-12.

## Scope

The user-facing workflow required for M1: paste an AliExpress URL/ID, land in
Drafts (never Products) with normalized data, retry a failed attempt without
re-entering it, and never silently duplicate a draft. The full premium editor
is explicitly out of scope — that is M2
([MASTER_ROADMAP.md](MASTER_ROADMAP.md)).

## What already existed (audit finding, not built this pass)

A repository audit found the large majority of M1 already implemented and
tested, from earlier phases:

- **Import pipeline.** `ProductImportService.import_product` — request →
  AliExpress client → contract schema → mapper → domain → repository. Reuses
  the Phase 3/4 AliExpress client unchanged.
- **Idempotency.** A DB-level unique constraint on `(tenant_id, source,
  external_id)` plus `ProductImportRepository.find_in_progress` (blocks a
  concurrent duplicate submission) plus `BaseRepository`'s
  `IntegrityError → ConflictError` translation as a race-safety net. No
  separate idempotency-key column was needed — the natural key already
  provides an equivalent guarantee, so none was added.
- **Tenant isolation.** Enforced by `TenantScopedRepository` on every query
  and write, same as the rest of the platform.
- **Drafts vs. Products separation.** Shipped as Product Workspace V2 Stage 0
  before this milestone: a product is a Draft until it has at least one
  synced `StoreListing` row, at which point it appears under Products
  instead. `GET /api/v1/drafts`, `GET /api/v1/products` (published only),
  `GET /api/v1/products/workspace-counts`.
- **The import dialog UI** (`import-product-dialog.tsx`) — accepts an ID or a
  pasted URL, validates client-side, and translates every documented
  AliExpress error code (`not_connected`, `ship_to_prohibited`, `rate_limited`,
  `token_expired`, `invalid_response`, ...) into a specific, actionable
  message rather than a generic failure.
- **Drafts table UI** — image, title, supplier, status, cost/currency,
  actions, empty/loading/error states, all already built.

## What this milestone built

The one genuine, concrete gap the audit found: **no way to retry a failed
import**, and no visibility into failures at all from the Drafts page (a
failed attempt creates no `Product` row, by design — the draft/product
separation is exactly "no product yet").

- `ProductImportService.retry_import` — resubmits a specific failed import
  using its own stored parameters (external ID, ship-to country, currency),
  so the merchant never re-types them.
- `POST /api/v1/products/imports/{id}/retry` — admin-gated, tenant-scoped
  (cross-tenant returns 404, not 403), rejects retrying anything that isn't
  currently `failed`.
- Frontend: a Retry button in `ImportHistoryTable` (visible only on failed
  rows), wired to a `useRetryImport()` React Query mutation with loading and
  error states, and a link to the resulting draft on success.
- A lightweight duplicate-import warning in the import dialog: checks the
  entered ID against the currently-loaded Drafts page cache and, on a match,
  shows a non-blocking notice with a link to the existing draft. Deliberately
  scoped down from a full backend "does this exist" lookup — documented as a
  known limit in `FEATURE_MATRIX.md`, not hidden.

## The durability bug (found during verification, fixed in this milestone)

Building the retry feature required a failed import to actually be
*queryable* — and live browser verification against the real dev stack found
that it was not. A genuine failed import (no AliExpress connection) returned
the correct `409 aliexpress_not_connected` response, but `GET
/api/v1/products/imports` immediately afterward showed **zero rows**, and a
direct database query confirmed it: zero `product_imports` rows for that
tenant.

**Root cause.** `app/api/deps.py::get_db_session` wraps every request in one
transaction: commit on success, roll back on any exception. That is correct
for ordinary writes — but `ProductImportService._fail` sets `status=FAILED`
on the `ProductImport` row and flushes it, and then `import_product`
re-raises the AliExpress error so the router can return the correct HTTP
status. The re-raise triggers the *same* rollback that a real bug would need,
discarding the just-recorded failure along with it. Every automated test that
covered this path still passed, because the test fixture
(`tests/integration/conftest.py::db_session`) shares one session across the
whole test and rolls it back only once at teardown — it never exercises the
real per-request commit/rollback contract, so it could not have caught this.

**Fix.** `ProductImportService._persist_failure_durably` commits the failure
row through a short-lived session on an independent connection, so it
survives the request's own rollback. Two things had to be true for this to be
safe rather than a second bug:

1. **A fresh primary key**, not the doomed in-transaction row's id. Reusing
   it deadlocked on every failure: the outer transaction still holds an
   uncommitted row with that id, and Postgres blocks a second insert of the
   same primary key until the first transaction resolves — which cannot
   happen until this call returns. Nothing needs the two ids to match; the
   error response never carries a `ProductImport` id, and every later read
   (Import History, retry) queries by whatever id the durable insert actually
   produced.
2. **Best-effort, not fatal.** If the durable write itself fails (logged, not
   raised), the original clean error response — 409, 422, whatever the real
   failure was — still reaches the caller. A secondary audit write must never
   turn a specific, actionable error into an opaque 503.

This is a narrow, targeted fix: it changes commit/rollback behaviour for
exactly one write (a failed import's own row), not for the general request
transaction contract used by the rest of the application.

## Data model

No new tables. `ProductImport` (`backend/app/models/product.py`) already
carried every field M1 needed: tenant isolation, lifecycle status
(`PENDING`/`RUNNING`/`SUCCEEDED`/`FAILED`/`SKIPPED`), `error_code`/
`error_message`, the request parameters (`ship_to_country`, `currency`) used
to power retry, `requested_by_user_id`, and timestamps. No migration was
required for this milestone.

## Importable fields

Source platform, source product ID, canonical source URL, supplier name,
original title/description (kept separately from merchant edits —
`supplier_title`/`supplier_description` vs. `title`/`description`), images,
variants with supplier SKU and attributes, supplier cost, source currency,
available inventory, shipping info, product status, import timestamp
(`created_at`), last source refresh timestamp (`last_synced_at`), and the
import record itself as the provenance/snapshot reference. Fields AliExpress
does not return are left `null`/absent — never fabricated.

## Reliability

- URL/ID validation client- and server-side (`extractProductId`,
  server-side normalization).
- Tenant-scoped idempotency and duplicate-submission blocking (above).
- Provider error normalization — every AliExpress error maps to a stable
  `code` the frontend branches on, never a raw upstream message shown as the
  primary explanation.
- Structured logging with request-correlation ids; no credentials, tokens, or
  full supplier payloads logged.
- Background processing: import runs synchronously within the request today
  (unchanged from before this milestone) — genuinely long-running import is
  not yet on Celery. Noted as a gap, not fixed in this pass since it was not
  part of the identified M1 blocker.

## Test coverage

**Backend** (`backend/tests/integration/test_products.py`,
`test_product_import_transaction_durability.py`):

- Valid product-ID import, idempotent re-import, variant/image round-trip.
- Delisted product, ship-to-prohibited, no-connection failures each recorded
  correctly and distinctly.
- Tenant isolation on products, imports, and retry (`TestTenantIsolation`,
  `TestRetryImport::test_retrying_another_tenants_import_returns_404_not_403`).
- Retry: succeeds with original parameters, does not duplicate an existing
  draft, rejects a non-failed import, 404s an unknown import.
- **New:** a dedicated regression test
  (`test_a_failed_import_survives_the_request_transaction_rolling_back`) that
  builds the real app and drives it through the real, non-overridden
  `get_db_session` dependency — the only test in the suite that exercises
  genuine per-request commit/rollback semantics for this path.

**Frontend** (`frontend/tests/e2e/products.spec.ts`,
`import-history.spec.ts`):

- Import form validation (empty ID rejected without a network call), URL
  parsing, ship-to-prohibited retry-with-new-country flow, dialog dismissal.
- Draft appears in Drafts, does not appear in Products, after a real import.
- **New:** duplicate-warning shown/hidden correctly against the cached
  Drafts list; retry button visible only on failed rows, shows the resulting
  draft link on success, shows a specific error and stays enabled on retry
  failure; keyboard-only dialog flow (focus trap, labelled fields, Escape
  closes and returns focus to the trigger); no console errors during a full
  open/fill/cancel cycle.

## Verification status

Stated precisely, because the two halves of this used different methods and
it matters which is which:

- **The bug — verified live, in the browser, against the real dev stack.**
  Registered a real tenant, submitted a real import with no AliExpress
  connection, got the correct `409 aliexpress_not_connected`, then loaded
  Import History and saw **zero rows** — confirmed a second time with a
  direct SQL query against the real database. This live reproduction is what
  the fix responds to.
- **The fix — verified with automated tests and against the real running
  application object, not mocked, but *not* re-confirmed through the live
  browser.** After implementing the fix: (1) a standalone script constructing
  the real FastAPI app fresh (`create_application()`) and driving it through
  `httpx.ASGITransport` — a real in-process request/response cycle, not a
  stub — confirmed import → 409 → history shows 1 failed row → retry → still
  fails correctly (still not connected) → history shows 2 rows; (2) a
  dedicated pytest regression test doing the same thing, passing in 3.4s
  (`test_a_failed_import_survives_the_request_transaction_rolling_back`);
  (3) the full backend suite — 956 tests, `ruff check`, `ruff format --check`,
  `mypy` on 173 files — all green after the change.
- **What was *not* achieved:** re-running the same live-browser reproduction
  above, after the fix, to see it pass. Two attempts against this machine's
  already-running dev backend processes (ports 8000 and 8002) both still
  showed the pre-fix behaviour (0 rows) after the fix was written — traced to
  those specific long-running processes not reflecting current source on
  disk (confirmed by hitting port 8002 directly with the same script that
  passes against a freshly-constructed app object: it still returns 0 rows,
  while the freshly-constructed object returns 1). This is a stale-process
  problem with this session's already-running dev servers, not a fix that
  only works "in a script." See "Known limitations" for what was tried.
- **Frontend build/lint/typecheck:** see the final delivery report for
  results — run after this document, not before, per the standard sequence.

## Known limitations

- **Variant count column** on the Drafts list is not implemented. The list
  response schema (`ProductRead`) deliberately omits variants for payload
  size; adding a count needs a correlated-subquery change to the repository
  query, reviewed on its own rather than folded into this fix. Documented in
  `FEATURE_MATRIX.md`.
- **Duplicate-import warning** checks only the currently-cached Drafts page
  (25 most recent rows), not the full tenant catalogue. A real duplicate
  outside that window is still caught server-side by the natural-key
  constraint — nothing is silently broken — but the *warning* will not fire
  for it.
- **Background processing.** Import still runs synchronously in the request;
  a very large or slow supplier response holds the request open rather than
  handing off to a Celery task. Pre-existing, not changed by this milestone.
- **Live browser re-verification of the fix specifically could not be
  completed this session.** Two locally-running backend processes were
  investigated as candidates: the one behind `127.0.0.1:8000` (this
  environment's configured `NEXT_PUBLIC_API_URL`) has an owning process id
  that does not resolve through normal process enumeration
  (`Get-CimInstance`) and was found to have active established TCP
  connections from external, non-local IP addresses — outside what this
  session could safely restart or investigate further, and outside this
  milestone's scope. A second candidate, a `uvicorn --reload` process
  positively identified on port 8002 running from this same checkout's
  virtualenv, was hit directly with the same before/after script used to
  verify the fix in-process — and it *also* returned the pre-fix result (0
  rows) even after the fix landed on disk, meaning that process is serving
  code from before this change despite `--reload` being enabled, for a
  reason not diagnosed further (most likely a stuck or crashed file-watcher
  in a long-running process, not something specific to this fix). Neither
  process was restarted, since the first could not be identified safely and
  the second's ownership/purpose beyond this session was not confirmed.
  **This is flagged as a separate operational concern for the repository
  owner — investigate what is actually serving `127.0.0.1:8000` before
  trusting it for manual testing — not as a defect in the M1 code**, whose
  correctness is established by the in-process and automated-test evidence
  above.
