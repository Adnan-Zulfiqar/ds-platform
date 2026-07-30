# Technical debt register

Reviewed 2026-07-31, at `phase-1-complete` (`e3e0b9c`).

Only genuine issues are listed. Items are ranked by the cost of leaving them,
not by how hard they are to fix. Each has a **trigger** — the point at which it
stops being acceptable — rather than a date.

Severity meanings:

| | |
|---|---|
| **Critical** | Blocks deployment, or would cause a breach, data loss, or outage if deployed |
| **High** | A real defect or an unverified safety-critical control; fix before the feature it affects ships |
| **Medium** | Correct today, will bite at scale or on the next change |
| **Low** | Known, bounded, cheap to live with |

---

## Critical

### C1 — The entire deployment path is unverified

Three Dockerfiles, a Compose stack, and an Nginx configuration have **never been
built or run**. Docker is not installed on the development machine.

That is a large amount of infrastructure whose first execution will be its first
test. Multi-stage builds, a non-root user, `output: "standalone"` tracing, four
service dependencies with health gates, and a reverse proxy all have to work
together on the first attempt.

**Impact:** any deployment attempt is a first run of untested code.
**Trigger:** immediately — before anything is deployed anywhere.
**Fix:** install Docker Desktop and run `docker compose up --build`, or push the
branch and let the CI `docker` job build all three images. CI already has that
job; it has not run because the branch has not opened a pull request.

---

## High

### H1 — The login throttle has no test coverage, and a comment claims it does

`tests/integration/conftest.py` disables login throttling for **every**
integration test via an autouse fixture. Its docstring says *"Throttling has its
own dedicated test."* **No such test exists.**

The throttle is the primary defence against credential stuffing on the
platform's most exposed endpoint. It is currently unexercised by any test, and
the comment actively misleads the next reader into thinking otherwise.

**Impact:** a regression in the throttle would pass CI silently.
**Trigger:** before the first real user account exists.
**Fix:** write the test — it should opt out of the disabling fixture, make
`login_max_attempts + 1` failed attempts, and assert a 429 with `Retry-After`.
It needs Redis, so it belongs in `tests/integration/`. Correct the comment
either way.

### H2 — Authorization is built but used nowhere

`require_roles` and `require_minimum_role` are implemented and unit-tested, but
**no endpoint depends on either**. Every authenticated endpoint is reachable by
any role, including `viewer`.

The unit tests prove the decision logic. They do not prove the wiring —
dependency ordering, the interaction with `CurrentPrincipal`, or the shape of
the 403 response — because nothing exercises it end to end.

**Impact:** the first endpoint that needs a role restriction will be relying on
a path never run against a real request.
**Trigger:** the first endpoint requiring a role restriction.
**Fix:** apply `RequireAdmin` to a write endpoint when one exists, with an
integration test asserting a `viewer` receives 403.

### H3 — Five sidebar links lead to routes that do not exist

`layouts/sidebar.tsx` links to `/products`, `/stores`, `/orders`, `/analytics`,
and `/users`. Only `/dashboard` exists. The other five render the 404 page.

**Impact:** the application looks broken to anyone clicking around.
**Trigger:** before any demo or user testing.
**Fix:** either gate the navigation manifest on a `ready` flag and render
unbuilt destinations as disabled, or add placeholder pages that state the
feature is not yet available. The first is preferable — it keeps the
information architecture visible without pretending.

### H4 — `is_verified` is never enforced

The column exists, registration sets it, and the principal carries it, but
**nothing checks it**. An unverified user has identical access to a verified
one.

Harmless today because registration always sets it true. It becomes a real hole
the moment email verification is implemented and the default flips to false —
at which point unverified accounts would silently retain full access.

**Impact:** none today; a security hole the day verification lands.
**Trigger:** implementing email verification.
**Fix:** add a dependency that requires verification, applied to everything
except the verification endpoints themselves.

---

## Medium

### M1 — The backend Docker image installs a stub `app` package

`docker/backend.Dockerfile` creates an empty `app/__init__.py` to make
`pip install .` cache-friendly, which installs a **stub `app` package into
site-packages**. The real code is copied to `/app` afterwards and wins only
because the working directory precedes site-packages on `sys.path`.

It works, but it depends on an implicit path-ordering rule. Any change to the
working directory, or any tool that imports outside `/app`, resolves to the
empty stub and fails with a confusing `ImportError`.

**Trigger:** the first time the image is built (see C1) or a command is run
from a different directory.
**Fix:** install only dependencies in the builder stage — export them from
`pyproject.toml` — rather than installing the package itself.

### M2 — `AuthenticatedUser.email` is an empty string on the token path

`get_current_principal` constructs the principal with `email=""`, because the
access token deliberately carries no email claim (it is personal data and would
land in every log line containing a decoded token).

The reasoning is sound; the representation is not. A field that is always empty
on the main code path is a trap: the first caller to read `principal.email` gets
`""` and no error.

Currently harmless — nothing reads it.
**Trigger:** the first consumer of `principal.email`.
**Fix:** make it `email: str | None = None`, so the absence is explicit and a
consumer must handle it.

### M3 — `refresh_tokens` grows without bound

`RefreshTokenRepository.purge_expired()` exists and **nothing calls it**. One
row accumulates per login per user, forever.

**Trigger:** the first scheduled-job infrastructure, or noticeable table growth.
**Fix:** a periodic Celery beat task once background jobs exist.

### M4 — Frontend API types are hand-written

`types/api.ts` duplicates the backend contract by hand. It will drift, and the
drift surfaces only at runtime.

**Trigger:** before the API grows past the current handful of endpoints.
**Fix:** generate from `/openapi.json` with `openapi-typescript` and delete the
hand-written file.

### M5 — `/auth/refresh` and `/auth/logout` are not specifically throttled

Both are unauthenticated and hit the database on every call. They are covered by
the global rate limiter but not by the strict login throttle.

Refresh tokens are 256-bit, so brute force is infeasible — the concern is
request volume, not guessing.

**Trigger:** observed abuse, or before opening the API to the public internet.
**Fix:** apply the login throttle keyed on IP to both.

### M6 — Dead code: `generate_token_secret`

Defined and exported from `repositories/refresh_token.py`, called nowhere. Its
only call site was removed when the JWT `jti` was found to provide the same
uniqueness guarantee.

**Trigger:** now — it costs nothing to remove.
**Fix:** delete the function and its two exports.

---

## Low

### L1 — Offset pagination degrades at deep offsets

Postgres walks and discards every skipped row. Acceptable while tenant-scoped
result sets are small.
**Trigger:** catalogues approaching millions per tenant.
**Fix:** add a keyset variant *alongside* — cursors cannot express "jump to page
400", so both have a place.

### L2 — Search uses `ILIKE`, which no index serves

Correct and injection-safe, but a full scan for leading-wildcard patterns.
**Trigger:** when product search becomes a primary workflow.
**Fix:** a `tsvector` column with a GIN index. The call site does not change.

### L3 — No partial indexes for soft deletes

`WHERE deleted_at IS NULL` is served by ordinary composite indexes, which also
carry dead rows.
**Trigger:** noticeable index bloat.

### L4 — `middleware.ts` runs on every route to set one header

It no longer redirects — that moved to `AuthGuard` — so it matches every path
purely to set `Cache-Control`.
**Trigger:** if edge middleware cost becomes measurable.
**Fix:** narrow the matcher to protected paths, or set the header in the layout.

### L5 — `docs/PHASE_0_COMPLETION.md` contains statements Phase 1 superseded

It lists "no authentication" and "the initial migration has not been run" as
limitations. Both were true when written and are now false.

It is a point-in-time record, so this is arguably correct behaviour — but a
reader arriving at it cold could be misled.
**Fix:** a one-line banner at the top noting it describes Phase 0 as delivered
and directing readers to the roadmap for current status.

---

## Security improvements (not defects)

Ranked by value, none currently blocking.

1. **Breached-password corpus check.** Catches far more real compromise than any
   composition rule. Trigger: before live customer accounts.
2. **Multi-factor authentication.** The token machinery accommodates it — it
   slots between password verification and token issue.
3. **Password reset and email verification.** Both need email delivery, the
   largest single gap in Phase 1.
4. **CSRF token.** Not needed today: the only cookie-authenticated endpoints are
   refresh and logout, neither of which makes a damaging state change. Trigger:
   extending cookie auth to a mutating endpoint.
5. **User-visible session list with per-device revocation.** `logout-all` exists;
   granular control does not.
6. **Secret rotation procedure.** Rotating `SECURITY_SECRET_KEY` currently
   invalidates every session at once, with no documented process.

## Scalability considerations

Not yet needed, recorded so seams are built with them in mind.

| Concern | Trigger |
|---|---|
| PgBouncer | Replica count × pool size approaching `max_connections` |
| Read replica for analytics | Reporting queries contending with transactional load |
| Celery queue separation | The first slow job class — the routing table is configured but empty precisely so this is cheap |
| Redis high availability | The rate limiter and login throttle both fail open; a Redis outage removes all quota enforcement |
| Observability | No metrics, tracing, or error reporter. `app/error.tsx` logs to the console |
