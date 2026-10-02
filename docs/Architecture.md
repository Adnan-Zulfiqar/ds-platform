# Architecture

This document records the decisions that shape the codebase and, more
importantly, *why* each was made. A decision without its rationale gets
reversed by the next person who finds it inconvenient.

## Contents

1. [Layering](#layering)
2. [Multi-tenancy](#multi-tenancy)
3. [Data model conventions](#data-model-conventions)
4. [Request lifecycle](#request-lifecycle)
5. [Error handling](#error-handling)
6. [Background processing](#background-processing)
7. [Caching and rate limiting](#caching-and-rate-limiting)
8. [Frontend architecture](#frontend-architecture)
9. [Security posture](#security-posture)
10. [Deferred decisions](#deferred-decisions)

---

## Layering

Clean architecture with a strict dependency direction. Each layer may depend
only on layers below it.

```
  API              app/api/            HTTP concerns only
   │                                   routing, DI wiring, error translation
   ▼
  Services         app/services/       business rules, orchestration
   │                                   raises domain exceptions
   ▼
  Repositories     app/repositories/   the ONLY place SQL is built
   │                                   tenant scoping enforced here
   ▼
  Models           app/models/         ORM mappings

  Core             app/core/           config, logging, context, exceptions
                                       depended on by everything, depends on nothing
```

**The rule that matters:** nothing below the API layer may import from
`app.api`, and services never import `fastapi`. That is what lets the same
service be driven by an HTTP request, a Celery task, or a CLI command without
modification — and it is why domain code raises `AppError` subclasses rather
than `HTTPException`.

Handlers stay at three lines — validate, delegate, return. See
`app/api/v1/users/router.py` for the reference shape. A handler that grows
business logic is the first sign the layering is eroding.

### Why not put logic in the ORM models

Active-record style ties business rules to persistence. A rule living on a model
cannot be tested without a database, cannot be reused across aggregates, and
tends to trigger lazy loads at unpredictable moments — which in async SQLAlchemy
raises, because implicit IO has no await point.

---

## Multi-tenancy

**Model: shared database, shared schema, `tenant_id` discriminator.**

The alternatives were considered and rejected:

| Approach | Why not |
|---|---|
| Database per tenant | Tens of thousands of databases is unmanageable; connection pooling collapses |
| Schema per tenant | Every migration becomes tens of thousands of DDL executions per release |
| **Discriminator column** | **Scales to the target tenant count on ordinary Postgres** |

The cost is that isolation becomes a property of application code rather than of
the database. Three mitigations:

1. **A single choke point.** All tenant-scoped data access goes through
   `TenantScopedRepository`, which injects the `tenant_id` predicate in
   `_base_query()`. Subclasses inherit it and cannot bypass it accidentally.

2. **Context, not arguments.** The tenant is read from a `contextvar` bound once
   per request, never passed as a parameter. A parameter can be forgotten at any
   of hundreds of call sites; `require_tenant_id()` raises rather than returning
   `None`, so a missing tenant is a loud failure instead of an unfiltered query.

3. **Index shape.** Every tenant-scoped index leads with `tenant_id`, so a
   forgotten filter is a visible performance cliff rather than something that
   silently looks correct in testing.

### Cross-tenant access returns 404, not 403

A 403 confirms the resource exists, which lets an attacker enumerate other
tenants' identifiers. A resource outside the current tenant is simply invisible.

### Writes

`TenantScopedRepository.create()` stamps `tenant_id` from context. If a caller
supplies a *different* tenant id it raises rather than silently correcting —
silently correcting would hide the bug that produced it. `update()` drops
`tenant_id` entirely: moving a row between tenants is not a supported operation.

### Tenant identity comes from a verified token claim

Phase 1 replaced the Phase 0 `X-Tenant-ID` header. The tenant now comes from the
`tid` claim of a signed access token, verified on every request.

The change was contained to `app/api/deps.py`, exactly as predicted when the
placeholder was written: no endpoint, service, or repository signature changed,
because they all depended on bound context rather than on the header. That is
the payoff of building the seam before the feature.

Full detail in [Authentication.md](Authentication.md).

---

## Data model conventions

Defined once in `app/models/base.py` as mixins.

**UUID primary keys.** Sequential integers leak business volume (an order id
tells a competitor the order count) and make enumeration trivial. UUIDs also
exist before the INSERT, so a service can build an object graph without a round
trip. Cost: 16 bytes and a randomly-distributed index.

**UTC timestamps, database-generated.** `TIMESTAMP WITH TIME ZONE` with
`server_default=now()`. Application servers drift and may sit in different
regions; the database is the single authority on time.

**Soft deletes.** `deleted_at IS NULL` means live. Customers delete things by
mistake, support needs to restore them, and financial records must stay
auditable. Physical deletion is reserved for GDPR erasure.

The filter is applied by the repository, deliberately *not* by a global
SQLAlchemy event. Implicit query rewriting is very hard to reason about when a
query legitimately needs to see deleted rows.

**Deterministic constraint names.** `NAMING_CONVENTION` in `models/base.py`.
Without it, Alembic generates names that vary between versions, producing
migrations that cannot be reliably downgraded. It also makes constraint-name
matching in `_translate_integrity_error` reliable.

---

## Request lifecycle

```
Nginx  →  TrustedHost  →  RequestContext  →  CORS  →  SecurityHeaders
       →  RateLimit  →  Route
       →  Dependencies ─┬─ get_db_session      (transaction opens)
                        ├─ get_current_principal
                        │     └─ verify JWT → AuthenticatedUser → bind context
                        └─ get_user_repository (tenant filter now guaranteed)
       →  Handler  →  Service  →  Repository  →  PostgreSQL
```

Middleware registration order in `main.py` is the *reverse* of execution order —
Starlette wraps each added middleware around the previous one. The ordering
rationale is documented at that call site.

**One session, one transaction, one request.** `get_db_session` commits if the
handler returns and rolls back if it raises. Handlers never call `commit()`, so
a request either fully succeeds or leaves no trace.

**Tenant-scoped repositories depend on `CurrentPrincipal`, not just
`DbSession`.** FastAPI resolves dependencies in declaration order, so the
principal — and therefore the bound tenant context — always exists before a
scoped query can run. A repository that depended on the session alone could be
constructed with no tenant bound, and `require_tenant_id()` would then raise at
query time rather than the request failing cleanly at authentication.

Phase 1 changed this dependency from `CurrentTenant` (which resolved a
header) to `CurrentPrincipal` (which verifies a token). The guarantee it
provides is identical; only the source of truth moved.

---

## Error handling

Domain exceptions in `app/core/exceptions.py`, each carrying a stable `code` and
an HTTP `status_code`. The status lives on the exception because the mapping
from failure to status is a property of the failure — this keeps the HTTP layer
a dumb translator rather than a growing if/elif chain.

Every failure produces the same `ErrorResponse` shape, so clients need one error
path. Two invariants:

- **Nothing internal leaks.** Driver messages and tracebacks contain table
  names, column names and sometimes row data. They go to the log; the client
  gets a generic message.
- **Every response carries `requestId`.** It turns "the app is broken" into one
  indexed log query.

Clients branch on `code`, never on `message`. Messages may be reworded or
localised freely.

---

## Background processing

**RabbitMQ brokers, Redis stores results.** Redis alone is simpler but is not a
durable broker — a restart can lose queued work, which for order fulfilment
means a customer's order is silently never placed.

**At-least-once delivery.** `task_acks_late=True` means a task whose worker dies
is redelivered rather than lost. The consequence is that **every task must be
idempotent** — running it twice must be indistinguishable from running it once.

**Context crosses the process boundary explicitly.** `contextvars` do not
survive a process hop, so `enqueue()` serialises the tenant and request id into
the task payload and `task_prerun` rebinds them. `task_postrun` clears them —
mandatory, because worker threads are reused and a leaked tenant id would scope
the *next* task to the wrong customer.

No tasks exist in Phase 0. The routing table is configured but empty, because
queue separation is very hard to retrofit once every job shares one queue.

---

## Caching and rate limiting

**Three Redis logical databases** — cache, sessions, rate limits — so that
flushing the cache during a bad deploy cannot sign every customer out.

**Cache keys are tenant-namespaced** in `CacheClient`. A cross-tenant key
collision would serve one customer another's data with no second line of
defence.

**Cache failures are swallowed.** An unavailable cache degrades a request to a
slower database read rather than failing it.

**Rate limiting is a fixed window** implemented as a single Lua script — atomic
increment-and-expire. Two separate calls would risk a counter with no TTL that
never resets and locks a client out permanently.

It **fails open**: a Redis outage must not take the API down with it. A circuit
breaker stops the limiter retrying Redis on every request during an outage —
without it, each request paid a full connection timeout. This is an explicit
availability-over-enforcement trade, and it means Redis needs its own alerting.

---

## Frontend architecture

> Expanded in Phase 2. Full detail — directory layout, component tiers, routing,
> theming, accessibility — is in [Frontend.md](Frontend.md). The essentials
> follow.

**Server Components by default**, `"use client"` only where interactivity or a
browser API demands it. Keeps JavaScript off the wire.

**React Query owns server state. Zustand owns UI state.** Copying API responses
into Zustand is the most common way a React codebase ends up with two competing
sources of truth. The boundary is stated in `stores/ui-store.ts`.

**The QueryClient is created per component instance**, not at module scope. A
module-level client is shared across every request on the server — in a
multi-tenant application that means one customer's cached data served to
another.

**One HTTP client.** `lib/api-client.ts` normalises every failure — envelope,
timeout, network error — into a single `ApiError`, and attaches a correlation
id. Components never call `fetch` directly.

**Design tokens as CSS variables.** Components reference semantic roles
(`bg-background`), never literal colours, so theming is one class on `<html>`.

**Access control is client-side, in `AuthGuard`.** `proxy.ts` (formerly `middleware.ts`; renamed for Next.js 16) cannot see
the httpOnly, path-scoped session cookie, so gating there would cause redirect
loops. The `(protected)` route group is a naming convention providing no
enforcement; the guard enforces, and the API is the actual security boundary —
bypassing the guard reveals an empty shell that cannot load data.

**One navigation manifest.** `lib/navigation.ts` feeds the desktop sidebar, the
mobile drawer, and the top bar. Each entry declares whether its destination
exists; unbuilt ones render as non-interactive items rather than links, so
primary navigation can never reach a 404.

**Three tiers of component**, distinguished by what they may know:
primitives (`components/ui/`) know nothing about the application; composed
components know routes, session, and stores; layouts know how the pieces fit.
A primitive importing `useAuth` has left its tier.

---

## Security posture

| Concern | Mechanism |
|---|---|
| Authentication | JWT access tokens (15 min) + rotating refresh tokens with reuse detection — see [Authentication.md](Authentication.md) |
| Password storage | Argon2id, NFKC-normalised, transparently rehashed on parameter change |
| Authorization | Role dependencies (`require_roles`, `require_minimum_role`) resolved before the handler body |
| Credential brute force | Login throttle on email *and* IP, checked before password verification |
| Secrets | Environment only; app refuses to boot on a placeholder key in deployed environments, or on a signing key under 32 characters in any environment |
| SQL injection | Parameterised statements throughout; `sort_by` validated against a per-model allowlist |
| Search injection | LIKE metacharacters escaped in `_apply_search` |
| XSS | React escapes by default; strict CSP; no `dangerouslySetInnerHTML` |
| Clickjacking | `X-Frame-Options: DENY` plus `frame-ancestors 'none'` |
| Host header attacks | `TrustedHostMiddleware`; wildcard rejected in deployed environments |
| CORS | Explicit origin list — required, since credentials are enabled |
| Transport | HSTS in deployed environments only |
| Rate limiting | Per tenant, falling back to IP; plus a coarse edge limit in Nginx |
| Container hardening | Non-root users, multi-stage builds, no build toolchain at runtime |
| Data exposure | ORM models never serialised directly; response schemas are explicit allowlists |

**CSRF is prepared, not implemented.** The refresh cookie is `SameSite=Lax` and
path-scoped to `/api/v1/auth`, and the only cookie-authenticated endpoints are
refresh and logout — neither performs a damaging state change. A double-submit
token should be added if cookie authentication is ever extended to mutating
endpoints. Every other endpoint authenticates with a bearer header, which is not
attached automatically by the browser and so is not CSRF-exposed.

---

## Deferred decisions

Recorded so they are chosen deliberately rather than by accident.

| Decision | Trigger to revisit |
|---|---|
| RS256 signing | A separate auth service, edge gateway, or third-party token verifier |
| Breached-password corpus check | Before live customer accounts exist |
| Multi-factor authentication | Customer or compliance requirement; slots between password verification and token issue |
| Keyset pagination | Product catalogues approaching millions per tenant |
| Full-text search (`tsvector` + GIN) | When product search becomes a primary workflow |
| Read replica for analytics | When reporting queries contend with transactional load |
| Generated API types (openapi-typescript) | Before the API surface grows past a handful of endpoints |
| Domain event bus | When the second subscriber to a state change appears |
| Encrypted credential storage | Required by the store-connection phase — third-party secrets must not be plaintext columns |
| PgBouncer | When replica count × pool size approaches `max_connections` |
