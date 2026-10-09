# CLAUDE.md — engineering constitution

Permanent rules for this repository. Read before writing code.

This document is authoritative. Where it disagrees with a habit, a tutorial, or
a plausible-looking pattern elsewhere in the ecosystem, this document wins.
Where it disagrees with an explicit instruction from the repository owner, the
owner wins — but say so out loud rather than silently departing from what is
written here.

---

## 1. Project overview

**DropPilot AI** — a multi-tenant SaaS platform for dropshipping automation,
positioned as a modern competitor to AutoDS. The goal is a real product
deployable to production and scalable to thousands of paying customers, not a
demo.

| Layer | Stack |
|---|---|
| Frontend | Next.js 15, React 19, TypeScript, Tailwind, shadcn-style UI, React Query, Zustand |
| Backend | FastAPI, Python 3.13, SQLAlchemy 2 (async), Alembic |
| Data | PostgreSQL 17, Redis 7 |
| Async | Celery, RabbitMQ 4 |
| Infrastructure | Docker, Nginx, GitHub Actions |
| Testing | Pytest, Playwright |

Development proceeds in **phases**. Each phase is a defined scope delivered in
full before the next begins. Current status: [PROJECT_ROADMAP.md](PROJECT_ROADMAP.md).

---

## 2. Architecture principles

### Layering — the one rule that matters most

```
api → services → repositories → models      (core at the bottom, depends on nothing)
```

Dependencies flow **one way only**. Nothing below the API layer imports
`app.api`. Services never import `fastapi`. Domain code raises `AppError`
subclasses, never `HTTPException`.

This is what allows the same service to be driven by an HTTP request, a Celery
task, or a CLI without modification. Breaking it is not a style preference — it
is the difference between a service you can reuse and one you cannot.

**Handlers validate, delegate, and return. Nothing else.** No SQL, no tenant
filtering, no transaction management, no error translation. See
`app/api/v1/users/router.py` for the reference shape.

### Applied principles

| Principle | How it shows up here |
|---|---|
| **SOLID** | One reason to change per module; repositories extended by subclassing not modification; narrow dependencies injected at the composition root |
| **DRY** | Cross-cutting behaviour lives once — pagination in `schemas/common.py`, tenant filtering in `repositories/base.py`, error translation in `api/error_handlers.py` |
| **KISS** | No abstraction without a second caller. A pattern introduced for a hypothetical future need is speculative complexity |
| **Separation of concerns** | Each layer knows only the one beneath it |
| **Dependency injection** | `app/api/deps.py` is the composition root. Nothing constructs its own session, cache, or repository |
| **Repository pattern** | The only place SQL is built |
| **Multi-tenancy** | Enforced in one base class, not at call sites |

---

## 3. Coding standards

Full detail: [docs/CodingStandards.md](docs/CodingStandards.md). The rules that
are most often violated:

### Python

- `from __future__ import annotations` at the top of every module.
- mypy runs **strict**. `Any` needs a justification.
- Keyword-only arguments for anything ambiguous at the call site.
- **Docstrings explain *why*, not *what*.** The signature already says what.
  A docstring that restates the code is worse than none — it is noise that
  future readers must check against reality.
- Every suppression (`# type: ignore`, `# noqa`) carries a comment naming the
  reason. Every one currently in the codebase names the upstream library whose
  stubs are incomplete.

### TypeScript

- No `any`. Use `unknown` and narrow.
- Server Components by default; `"use client"` only for state, effects, event
  handlers, or browser APIs.
- **Never fetch in a component.** Go through `services/`, which owns the
  endpoint, the query key, and the response type together.
- **React Query owns server state. Zustand owns UI state.** Copying an API
  response into a Zustand store creates two competing sources of truth. This is
  the single most common way a React codebase decays.

### Comments

Write the comment that a reader could not derive from the code. Prefer
recording a decision and its trade-off over describing mechanics.

---

## 4. Repository rules

**This layer is the multi-tenancy security boundary.** A mistake here is a
cross-tenant data leak, which is the worst failure mode this platform has.

- Tenant-owned data uses `TenantScopedRepository`. It injects the `tenant_id`
  predicate in `_base_query()`; subclasses inherit it and cannot bypass it by
  accident.
- The tenant comes from **context**, never from an argument. An argument can be
  forgotten at any of hundreds of call sites; `require_tenant_id()` raises
  rather than returning `None`, so a missing tenant is a loud failure instead of
  an unfiltered query.
- **Cross-tenant access returns 404, not 403.** A 403 confirms the resource
  exists and lets an attacker enumerate other tenants' identifiers.
- `sort_by` and filter fields are validated against a **per-model allowlist**.
  Resolving a client-supplied string to a column without one is an injection
  vector.
- Unscoped data access is a short, closed list, each documented in its own
  module (review finding A-1 corrected the earlier "only two"):
  - request path: `TenantRepository` (the tenants table sits above the
    boundary) and `AuthenticationUserRepository` (login must find a user
    before a tenant is known);
  - platform reference data, not tenant-owned: `RoleRepository`,
    `PromptRepository`;
  - cross-tenant maintenance that runs before any tenant context:
    `ShopifyMaintenanceRepository`, `IntegrationMaintenanceRepository`,
    `EbayComplianceLedgerRepository`;
  - single-question lookups that return ids only, never a renderable row:
    `RuleApplicationTenantLookup`, `PipelineBulkRunTenantLookup`,
    `PipelineBulkRunSweep`, `EbayConnectedTenantsSweep` (approved by the
    owner 2026-10-03, B-011: tenant ids of connected eBay workspaces for the
    scheduled eBay jobs).
  - platform operators (Track E5, owner-approved 2026-10-04, D-015):
    `PlatformAdminRepository`, `PlatformAdminAuditRepository`,
    `PlatformAdminSessionRepository` and `PlatformSupportSessionRepository`
    (operator accounts, their audit trail, their sign-in sessions and
    their time-limited support sessions sit above every tenant; sessions
    added with D-018, support sessions with D-019), and
    `PlatformTenantDirectory` (workspace list and counts, never a
    tenant-owned row), and `PlatformMetrics` (platform-wide dashboard
    counts, never a row; D-019, 2026-10-09). These are the **only**
    request-path exception. They are reachable solely behind
    `RequirePlatformAdmin`, which is a separate token audience plus TOTP
    plus an IP allow-list, and is off by default.
  - **Operators inside one workspace (D-019, owner-approved 2026-10-09)
    are not an unscoped exception.** `app.api.deps.platform_workspace`
    checks the operator's permission, audits the visit, then **sets the
    tenant context** to that workspace for the request. Everything it
    reads or changes goes through the ordinary `TenantScopedRepository`
    classes, so the tenant predicate applies exactly as for the
    merchant. Never add an unscoped query for workspace data; enter the
    workspace instead.
  - one free trial per store (Track E6b, owner requirement 2026-10-04,
    D-016): `TrialFingerprintRegistry`. It holds a one-way hash of a
    store's public identity and returns a boolean, never a row. It runs only
    in the `billing.claim_trial` Celery task, not on a request path.

  **Do not add to this list without explicit approval**, and never on a
  request path. `tests/unit/test_unscoped_repositories_are_a_closed_list.py`
  fails when a `BaseRepository` subclass escapes tenant scoping unlisted.
- If an unscoped query is genuinely required, put it in a separate,
  explicitly-named class. Never add a bypass method to a scoped repository —
  it is one autocomplete away from being used on an ordinary request path.

---

## 5. Database conventions

- **UUID primary keys.** Sequential integers leak business volume and make
  enumeration trivial.
- **UTC timestamps, database-generated.** Application servers drift; the
  database is the single authority on time.
- **Soft deletes.** `deleted_at IS NULL` means live. Never `DELETE`. Physical
  deletion is reserved for GDPR erasure and for `user_roles`, where a
  soft-deleted row would grant a revoked privilege.
- Every business table inherits `TenantScopedBase` rather than assembling
  mixins by hand.
- **Enum columns must pass `values_callable`.** SQLAlchemy persists member
  *names* by default, not values, and the mismatch fails every insert at
  runtime.
- `eager_defaults=True` is set on `Base` and is **mandatory**, not an
  optimisation: without it, serialising a just-updated row raises
  `MissingGreenlet` in async SQLAlchemy.
- Index anything used in `WHERE`, `JOIN`, or `ORDER BY` at scale, leading with
  `tenant_id`.

Full detail: [docs/Database.md](docs/Database.md).

---

## 6. API conventions

- **Version everything.** `/api/v1/`. When v2 is needed, add `app/api/v2/`
  alongside and mount both; v1 keeps working for existing integrations.
- **Never return an ORM model.** Always an explicit response schema — that is
  what makes it impossible for `password_hash` to reach a response.
- One error envelope for every failure. Clients branch on the stable `code`,
  never on `message`.
- Every response carries `requestId`, which maps to the server log line.
- Request and response schemas are separate types even when fields overlap.
- The API speaks **camelCase**; Python stays snake_case. The alias generator
  bridges them at the boundary.
- List endpoints accept the same pagination, sorting, filtering, and search
  parameters, and return the same `Page` envelope.

---

## 7. Security rules

Non-negotiable:

1. Never return an ORM model from an endpoint.
2. Never interpolate user input into SQL.
3. Never log a credential, token, or request body containing customer data.
4. Never trust a client-supplied tenant identifier. Identity comes from a
   verified token claim.
5. Never put a secret in a `NEXT_PUBLIC_*` variable — those are inlined into
   the client bundle.
6. Never commit a `.env`.
7. Authentication failures are **uniform**. Unknown address, wrong password,
   disabled account, and suspended tenant return the same status and message.
8. Authorization is enforced as a **dependency**, not inside a handler body.
   A check buried in a function is easy to omit when the next endpoint is
   copied from it.
9. Passwords are Argon2id. Tokens in the database are stored hashed, never raw.

Full detail: [docs/Authentication.md](docs/Authentication.md).

---

## 8. Testing requirements

- **Test behaviour, not implementation.** A test asserting on internal call
  order breaks on every refactor and catches nothing.
- Name the scenario, not the function:
  `test_create_rejects_a_foreign_tenant_id`, not `test_create`.
- **Every new tenant-scoped repository gets an isolation test.** These are the
  highest-value tests in the suite. They run without a database by inspecting
  compiled SQL, so there is no excuse to skip them.
- Unit tests must not need Postgres, Redis, or the network. Anything that does
  belongs in `tests/integration/` behind the `integration` marker.
- Integration tests build the schema by **running the migrations**, not
  `create_all()`. Creating tables from the models tests the models against
  themselves and proves nothing about what runs in production.
- **A security control without a test is not implemented.** If a fixture
  disables a control for convenience, that control needs its own test that
  re-enables it.
- Do not claim coverage that does not exist — in a comment, a docstring, or a
  report.

---

## 9. Documentation requirements

- Documentation is updated **in the same change** as the code. Docs that lag
  stop being trusted, and once distrusted they stop being read.
- Record **decisions and their trade-offs**, not mechanics.
- Every phase updates [CHANGELOG.md](CHANGELOG.md) and
  [PROJECT_ROADMAP.md](PROJECT_ROADMAP.md).
- Known limitations are written down explicitly. An unstated limitation is a
  trap for whoever hits it next.
- **State what was verified and what was not.** "Tests written but never run"
  is useful information; presenting it as working is not.

---

## 10. Git workflow

- `develop` is the application integration branch: feature, fix and docs
  PRs target it. `main` carries the Agent Bridge / repository
  infrastructure and is **not** the application source branch; never merge
  `develop` into `main` without an explicit owner decision. Branch for work:
  `feat/`, `fix/`, `chore/`, `docs/`.
- Conventional Commits. Imperative mood. The body explains *why*.
- Small, single-purpose pull requests. A 2,000-line PR gets rubber-stamped; a
  200-line PR gets reviewed.
- Every phase is tagged (`phase-N-complete`).
- Never commit secrets. Never force-push a shared branch.

---

## 11. Performance expectations

- No N+1 queries. Join or batch.
- No unbounded result sets. Pagination is capped by configuration.
- Redis failures **degrade, never fail** — a cache outage becomes a slower
  database read, not an error. This is why the rate limiter fails open, and why
  Redis needs its own alerting.
- Anything slower than a request cycle belongs in a Celery task.
- **Every task must be idempotent.** `task_acks_late` gives at-least-once
  delivery, so a task can legitimately run twice.
- Measure before optimising, and measure the right thing. A Phase 0
  investigation blamed the application for ~780 ms of latency that turned out to
  be the test client.

---

## 12. Rules for future phases

### Mandatory

1. **Never rewrite existing architecture without approval.** Propose, explain
   the reasoning, and wait.
2. **Never duplicate existing functionality.** Search before writing. If
   something similar exists, extend it.
3. **Always extend existing modules before creating new ones.** A new module
   needs a reason beyond "it felt cleaner".
4. **Preserve backwards compatibility whenever possible.** When a phase forces
   a breaking change, make the minimum necessary change and say so explicitly.
5. **Keep documentation synchronized.** Same change, not a follow-up.
6. **Run quality checks before completing every phase** — see below. A phase is
   not complete until they pass.
7. **Never silently change database schemas.** A schema change is announced,
   explained, and delivered in its own migration with a working `downgrade()`.
   Never edit a migration that has been applied anywhere.
8. **Explain significant architectural decisions.** Record the trade-off, not
   just the choice.

### Scope discipline

Implement **only** the current phase. Building ahead is not helpfulness — it
creates code nobody asked for, that nobody reviewed against a requirement, and
that constrains the design of the phase it pre-empts.

Reserved modules exist for exactly this reason: a module created ahead of its
phase holds a registered router and no endpoints. Leave it that way until its
phase. (`app/api/v1/products/` was the original example; its phases have
since shipped and it now carries the catalogue and AI pipeline routes.)

### Per-phase sequence

1. Read the whole prompt.
2. Analyse dependencies on previous phases.
3. Explain the implementation plan.
4. State risks and assumptions.
5. Implement.
6. Update documentation.
7. Run quality checks.
8. Summarise, listing files created and modified.
9. **Stop.** Do not roll into the next phase.

### Quality gate

Backend:

```bash
cd backend && ruff check . && ruff format --check . && mypy app && pytest
```

Frontend:

```bash
cd frontend && npm run lint && npm run typecheck && npm run build
```

---

## 13. Honesty requirements

These matter more than any style rule, because everything else in this document
depends on the reports being true.

- **Never claim something works that has not been run.** Distinguish "verified"
  from "written" every time.
- **Never present a limitation as a feature**, or omit it because it is
  inconvenient.
- **Correct your own errors plainly** when you find them, including errors in
  earlier reports.
- **Report failures with their output.** A test that fails is information, not
  something to work around.
- When investigating, **verify before concluding**. Check the instrument before
  blaming the system.
