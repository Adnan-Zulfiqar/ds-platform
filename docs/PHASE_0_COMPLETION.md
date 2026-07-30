# Phase 0 — Completion Report

**Project:** DropPilot AI — multi-tenant dropshipping automation platform
**Phase:** 0 — Foundation
**Completed:** 2026-07-30
**Commit:** `857dacf8cf02f2133ae93b90d6cb008e52c658a2`
**Branch:** `phase-0-foundation` (local only — not pushed)

Phase 0 delivered the architecture that later phases build on. It contains **no
business features**: no marketplace integrations, no product import, no orders,
no billing, and no authentication. Those are deliberate boundaries, not gaps in
delivery.

---

## 1. Architecture decisions

Each decision below is settled. Reversing one is a cross-cutting change, so the
reasoning is recorded here and in [Architecture.md](Architecture.md).

### Layering

Clean architecture with a strictly one-way dependency direction:

```
api → services → repositories → models,  with core at the bottom
```

Nothing below the API layer imports `app.api`, and services never import
`fastapi`. Domain code raises `AppError` subclasses rather than `HTTPException`.
This is what allows the same service to be driven by an HTTP request, a Celery
task, or a CLI command without modification.

### Multi-tenancy: shared schema with a `tenant_id` discriminator

Database-per-tenant and schema-per-tenant were both rejected — at tens of
thousands of tenants they make every migration an operational project and
collapse connection pooling.

The cost is that isolation becomes a property of application code. Three
mitigations, all in place:

1. **One choke point.** All tenant-scoped access goes through
   `TenantScopedRepository._base_query()`.
2. **Context, not arguments.** The tenant is read from a `contextvar` bound once
   per request. `require_tenant_id()` raises rather than returning `None`, so a
   missing tenant is a loud failure, never an unfiltered query.
3. **Index shape.** Every tenant-scoped index leads with `tenant_id`, making a
   forgotten filter a visible performance cliff rather than something that looks
   correct in testing.

Cross-tenant access returns **404, not 403** — a 403 confirms the resource
exists and enables identifier enumeration.

### Data model conventions

| Decision | Rationale |
|---|---|
| UUID primary keys | Sequential integers leak business volume and enable enumeration; UUIDs exist before the INSERT |
| UTC timestamps, database-generated | App servers drift and may sit in different regions; the database is the single authority on time |
| Soft deletes (`deleted_at IS NULL`) | Customers delete by mistake; financial records must stay auditable. Hard deletion is reserved for GDPR erasure |
| Deterministic constraint names | Alembic otherwise generates names that vary between versions, producing migrations that cannot be reliably downgraded |

The soft-delete filter is applied by the repository, **not** by a global
SQLAlchemy event — implicit query rewriting is very hard to reason about when a
query legitimately needs to see deleted rows.

### Error handling

Domain exceptions carry a stable `code` and an HTTP `status_code`. The status
lives on the exception because the mapping from failure to status is a property
of the failure; this keeps the HTTP layer a dumb translator instead of a growing
if/elif chain.

Two invariants: **nothing internal leaks** (driver messages and tracebacks go to
the log, not the client), and **every response carries `requestId`**.

### Background processing

RabbitMQ brokers, Redis stores results. Redis alone is not a durable broker — a
restart can lose queued work, which for order fulfilment means a customer's
order is silently never placed.

`task_acks_late=True` gives at-least-once delivery, which makes **task
idempotency a hard requirement**. Request context is serialised into the task
payload explicitly, because `contextvars` do not survive a process hop, and
cleared in `task_postrun` — worker threads are reused, so a leaked tenant id
would scope the next task to the wrong customer.

### Rate limiting fails open

A Redis outage must not take the API down with it. A circuit breaker prevents
the limiter from retrying Redis on every request during an outage. This is an
explicit availability-over-enforcement trade, and it means **Redis needs its own
alerting** — while it is down there is no quota enforcement at all.

### Frontend

Server Components by default. React Query owns server state; Zustand owns UI
state only. The `QueryClient` is created per component instance, not at module
scope — a module-level client is shared across every server request, which in a
multi-tenant application means one customer's cached data served to another.

---

## 2. Verified components

Every check below was executed on this machine and passed.

| Check | Command | Result |
|---|---|---|
| Python lint | `ruff check .` | All checks passed |
| Python format | `ruff format --check .` | 60 files already formatted |
| Python types | `mypy app` (strict) | No issues in **53 source files** |
| Python tests | `pytest` | **54 passed** |
| TS lint | `npm run lint` | Clean |
| TS types | `npm run typecheck` | Clean |
| Frontend build | `npm run build` | Compiled successfully, 3 routes, 105 kB shared JS |
| App boots | ASGI import + route table | 5 routes registered under `/api/v1` and `/health` |
| Live request behaviour | Raw ASGI calls | `/health/live` → 200 (0.69 ms); `/api/v1/users` → 401 (1.22 ms); unknown route → 404 |

Test coverage is concentrated where it matters most: **11 of the 54 tests target
tenant isolation directly**, asserting on generated SQL so they run without a
database and cannot be skipped for convenience.

### Defects found and fixed during verification

Running the code — rather than assuming it worked — caught three real problems:

1. **`PydanticUserError` on import.** A bare class attribute in `SecuritySettings`
   was treated as a settable field. Fixed with `ClassVar`, which also prevents
   the placeholder-key sentinel being overridden from the environment.
2. **Missing `email-validator`.** Pydantic ships `EmailStr` support as an
   optional extra; without it, schema construction failed at import. Added as an
   explicit dependency.
3. **Rate limiter added ~4 s latency per request when Redis was down.** The
   fail-open path paid a full 5-second connect timeout on every request. Fixed
   with a 0.25 s fast-path timeout plus a circuit breaker.

A fourth investigation — "API routes are missing from the application" — turned
out to be a **measurement error on my part**, not a defect. FastAPI 0.141 no
longer flattens `include_router()` children into `app.routes`; it keeps them as
composite `_IncludedRouter` objects. The routes were registered correctly all
along, as the OpenAPI document and live requests confirmed.

---

## 3. Unverified components

**These are written but have never been executed.** Treat each as unproven.

| Component | Why unverified | How to verify |
|---|---|---|
| Initial Alembic migration | No PostgreSQL available locally | `alembic upgrade head` against a throwaway database, then `alembic downgrade base` to prove reversibility |
| Docker images (backend, worker, frontend) | Docker not installed | `docker compose build` |
| Docker Compose stack | Same | `docker compose up` |
| Nginx configuration | Same | `docker compose up nginx`, then `curl http://localhost/nginx-health` |
| GitHub Actions pipeline | Never pushed | First CI run after push |
| Playwright E2E specs | Browser binaries not installed | `npx playwright install && npm run test:e2e` |
| Celery worker startup | No RabbitMQ available | `celery -A app.workers.celery_app.celery_app worker --loglevel=info` |

The migration is the highest-risk item: it was written by hand rather than
autogenerated against a live database, so column types, constraint names, and
enum handling are unconfirmed.

---

## 4. Known limitations

Deliberate Phase 0 boundaries. None is a defect; all are recorded so none is
discovered late.

1. **No authentication.** Tenant identity comes from an `X-Tenant-ID` header,
   which is client-controlled and therefore **not access control**. The resolver
   refuses to operate when `ENVIRONMENT` is `staging` or `production`, so
   deploying this as-is fails loudly rather than leaking data.

2. **No authorization.** `UserRole` exists as an enum but nothing enforces it.

3. **No business features.** `products`, `stores`, `orders`, `analytics`, and
   `auth` routers are registered with zero endpoints. The URL prefixes and
   OpenAPI tags are fixed now so later phases do not renegotiate them.

4. **No background jobs.** Celery is configured; `task_routes` is deliberately
   empty. Queue separation is very hard to retrofit once every job shares one
   queue.

5. **Rate limiting has no enforcement during a Redis outage** — see the
   fail-open decision above.

6. **Offset pagination degrades at deep offsets.** Acceptable now; add a keyset
   variant alongside it (not replacing it — cursors cannot express "jump to page
   400") before catalogues reach millions per tenant.

7. **Frontend API types are hand-written.** They will drift from the server
   silently. Generate from `/openapi.json` before the API surface grows.

8. **No observability beyond logs.** No metrics, no tracing, no error reporter.
   `app/error.tsx` currently logs to the console.

9. **Search uses `ILIKE`.** Correct and injection-safe, but not index-assisted
   for leading-wildcard patterns. Move to `tsvector` + GIN when product search
   becomes a primary workflow.

---

## 5. Local validation commands

Backend — all four must pass:

```bash
cd backend && ruff check . && ruff format --check . && mypy app && pytest
```

Frontend:

```bash
cd frontend && npm run lint && npm run typecheck && npm run build
```

Full stack:

```bash
cp .env.example .env && docker compose up --build
```

Apply the schema (required once, and after any migration):

```bash
docker compose exec backend alembic upgrade head
```

Verify the API responds:

```bash
curl http://localhost:8000/health
```

End-to-end tests (installs browsers on first run):

```bash
cd frontend && npx playwright install && npm run test:e2e
```

---

## 6. Deviations from the specified architecture

Three, all structural. The reasoning for each is recorded in the deviations
table in [FolderStructure.md](FolderStructure.md#deviations-from-the-originally-specified-layout);
the recommendations below were reached during Phase 0 finalization.

| Specified | Implemented | Status |
|---|---|---|
| `app/config/` | `app/core/config.py` | Recommend **keep** |
| `app/exceptions/` | `app/core/exceptions.py` | Recommend **keep** |
| `app/tasks/` | `app/workers/tasks/` | Recommend **revert to the original** |

No other deviations. The tech stack, layering, API versioning scheme, database
conventions, multi-tenancy model, and folder layout otherwise follow the Phase 0
specification exactly.

One addition not in the specification: `IdentifiedBase` in `app/models/base.py`,
an abstract base carrying the UUID primary key and timestamps. It was introduced
so the repository generic has a meaningful type bound — `Base` alone declares no
columns, so a repository generic over it could not reference `id` without
defeating the strict type checking that catches a mistyped column name before
production.

---

## 7. Git state at completion

| | |
|---|---|
| Branch | `phase-0-foundation` |
| Commit | `857dacf8cf02f2133ae93b90d6cb008e52c658a2` |
| Working tree | Clean — no uncommitted or untracked changes |
| Remote | **Not pushed.** No upstream tracking branch |
| Files changed | 126 (+16,855 / −86) |

The deletion of 86 lines removed the practice statistics package used to
rehearse the pull-request workflow before Phase 0 began.

### Open item: PR #1 conflicts with the Phase 0 architecture

**<https://github.com/Adnan-Zulfiqar/ds-platform/pull/1> — "Reject empty input in
mean() and median()"** predates Phase 0. It was opened against a small
JavaScript statistics package that existed only to rehearse the pull-request
workflow.

**The conflict.** That package lived at the repository root (`src/stats.js`,
`test/stats.test.js`, a root `package.json`, and the original `README.md`).
Phase 0 defines the root as `backend/`, `frontend/`, `docker/`, `nginx/`,
`docs/`, and `scripts/`, so those files were removed on
`phase-0-foundation`. PR #1 modifies files that no longer exist on this branch,
and its root `package.json` would collide with the monorepo layout. Merging it
would either reintroduce an unrelated package into the platform root or produce
conflicts, depending on merge order.

**Status: intentionally left untouched.** The PR has not been closed, merged, or
modified. It is recorded here so the conflict is a known decision rather than a
surprise for whoever next looks at the branch list. Resolving it is a separate
call — it has no bearing on Phase 0 or Phase 1, since nothing in the platform
depends on that code.
