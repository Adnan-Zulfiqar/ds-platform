# Technical debt register

Reviewed 2026-07-31 at `phase-1-complete`; updated after Phase 2.
Housekeeping pass 2026-07-31 — **H1, H2, and H3 resolved.**
**Phase 4 release review 2026-07-31** — M10 resolved; M15 added.
**Phase 5 release review 2026-07-31** — M16 added; M9/M11/M15 updated; live
`AliExpressClient.call()` verified for category success and order error/list paths.
**Phase 6 release review 2026-07-31** — M9 resolved (dashboard uses live
analytics); M15 expanded to cover Phase 6 Celery tasks; live `product.get`
re-verified for inventory sync.

**Current count: 1 critical, 1 high, 12 medium, 5 low.**

The Phase 5 release verified the production AliExpress client path against the
live gateway and shipped order sync. M16 records that a populated order-detail
success body remains documentation-derived. M15 now covers order Celery tasks
as well as catalogue tasks.

The Phase 3 release review added four items, all discovered by verifying rather
than by reading: M11 and M12 are the price of the new webhook endpoint being
public and unthrottled, M13 is a flaky end-to-end suite that briefly produced 14
convincing false failures, and M14 records that local Redis is a decade older
than the production target.

M10 was **narrowed, not resolved.** Live verification covered everything that
carries a credential — signing, token exchange, OAuth, permissions. It did not
cover the application's own client calling a business endpoint, or any response
schema parsing a real payload. That distinction matters and is stated in full
under M10.

**C1 remains open and unchanged**: Docker is still not installed on this
machine, so `docker compose build` and `up` — the first item of the Phase 3
brief — could not be run. Neither WSL nor Docker Desktop is present, and
installing them needs administrator elevation and a reboot.

Phase 2 added M8 (dashboard bundle size) and M9 (dashboard mock data), and
resolved one latent defect found by running the app: `CORS_ORIGINS` could not be
parsed in the format documented in `.env.example`, so the application could not
start with its own example configuration. Regression tests now cover it.

**C1 — Docker validation — remains the single blocking item.** Three
Dockerfiles, the Compose stack, and the Nginx configuration have still never
been built or executed. Phase 2 did not change this and could not: Docker is not
installed on the development machine.

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

### ~~H1 — The login throttle has no test coverage~~ ✅ RESOLVED 2026-07-31

`tests/unit/test_login_throttle.py` now covers thresholds, the independence of
the email and IP dimensions, counter clearing, TTL and lockout extension, key
privacy, and the fail-open path — 16 tests.

They run against `fakeredis` rather than a live server, deliberately: requiring
real Redis would mean skipping on any machine without one, which is exactly how
a security control ends up untested in the first place.

The false docstring in `tests/integration/conftest.py` is corrected and now
points at the real coverage.

### ~~H2 — Authorization is built but used nowhere~~ ✅ RESOLVED 2026-07-31

Both user endpoints depend on `RequireViewer`. Nine integration tests cover
each real role, a token with no roles, a token with only unrecognised roles, and
that authorization is decided **before** resource lookup — so a 403 does not
leak whether an id exists.

`/auth/me` is deliberately left ungated and has a test asserting so: it is the
endpoint that tells a client which roles it holds, so a role gate would be
circular.

Note the floor is `viewer` rather than something stricter because reading the
team roster suits every real role. The check is still meaningful — it rejects a
validly signed token carrying no recognised role, which is what a user whose
roles were revoked mid-session presents.

### ~~H3 — Five sidebar links lead to routes that do not exist~~ ✅ RESOLVED 2026-07-31

The navigation manifest carries a `ready` flag. Unbuilt destinations render as
non-interactive items with a "Soon" badge and `aria-disabled`, with a
screen-reader equivalent when the sidebar is collapsed.

Kept visible rather than hidden: the information architecture is part of the
product, and a 404 reads as broken where a disabled item reads as unfinished.

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

### ~~M10 — The AliExpress *business* contract is unverified through our client~~ ✅ RESOLVED 2026-07-31

Phase 4 captured real `aliexpress.ds.product.get` and feed payloads, built
Pydantic models from them, and pinned the shapes as committed fixtures.
Integration tests drive the real HTTP pipeline with only the network boundary
replaced — the mock returns the **real captured JSON**, not invented success
data.

Verified behaviours that mocks would not have caught: semicolon-delimited image
URLs, string prices requiring `Decimal`, double-wrapped arrays, composite
`sku_attr` keys, and the decision to never expose supplier HTML description.

**Remaining gap:** production-path `AliExpressClient.call` against the live
gateway for product import has not been re-run as part of this release; Phase
3.7 proved access via direct HTTP. The parsing layer is verified; the full
client stack in production is not.

### M15 — Celery sync tasks have never run under a broker

Catalogue, order, and Phase 6 ops tasks
(`inventory.sync`, `pricing.recalculate`, `automation.run`, `shipment.refresh`,
`analytics.aggregate`, `cleanup.old_notifications`, plus earlier product/order
tasks) follow the established task pattern and are unit-tested via `.run()`,
but no RabbitMQ broker or Celery worker runs on the development machine. Beat
entries exist in `celery_app.py`; they have never been exercised by a running
beat process.

**Impact:** background refresh and automation are implemented but unverified in
a running worker process.
**Trigger:** before advertising scheduled sync or automation to customers.
**Fix:** run `docker compose up` with worker and beat once C1 is closed; add an
integration test that executes `.apply()` against an in-process worker if CI
cannot reach RabbitMQ.

### M16 — Populated order-detail success body is documentation-derived

Live verification through `AliExpressClient.call()` captured real error
envelopes for `aliexpress.ds.trade.order.get` and a live
`commissionorder.listbyindex` response. The sandbox account has **no real
orders**, so the happy-path order-detail body used by the sync mapper is built
from AliExpress documentation rather than a captured success payload.

**Impact:** field names or nesting that differ from documentation will surface
only when the first real order is synced.
**Trigger:** the first connected account with live orders, or a fixture
captured from one.
**Fix:** re-run `scripts/verify_orders_live.py` (or sync) against an account
with orders; commit the success fixture; align the Pydantic models to the live
shape; add a regression test.

### M11 — The webhook accepts unsigned, unauthenticated deliveries

`/api/v1/integrations/aliexpress/webhook` is public and performs no signature
verification. Anyone who learns the URL can post to it.

Phase 5 added Redis replay protection, message classification, and delivery
counters. The handler still **must not** mutate order state from the payload:
there is no verifiable tenant claim and no signature. At most it may nudge a
sync that re-fetches from the supplier API under the tenant's own credentials.

The open question is factual and cheap to answer — whether AliExpress signs
deliveries at all. The handler already records `signature_header_present` on
every delivery, so a single real notification settles it.

**Trigger:** before any webhook payload is trusted to write order fields
directly.
**Fix:** confirm the signing scheme, compute the HMAC over the raw body, and
reject mismatches. `parse_webhook_payload` reads `request.body()`, which Starlette
caches, so the exact bytes remain available for verification.

### M12 — The webhook is exempt from inbound rate limiting

Added to `_EXEMPT_PATHS` deliberately: returning 429 to a delivery agent reads as
failure and provokes redelivery, converting a burst of legitimate notifications
into a larger one. Dropping a supplier's order update is worse than absorbing
the traffic.

The cost is an unauthenticated public POST endpoint with no throttle at all —
a denial-of-service surface, and with M11 an unauthenticated one.

**Trigger:** same as M11 — when the handler stops being inert.
**Fix:** a webhook-specific limiter that sheds load without returning a
retry-provoking status.

### M13 — The Playwright suite is flaky under full parallelism

The 116-test suite fails 2–5 tests per run at default parallelism on this
machine, with a **different** set failing each time. Every failure passes when
re-run serially, and the suite is green with `--retries=2`.

Two distinct causes were separated during the Phase 3 release:

* A stale `next start` server on port 3000 was being reused
  (`reuseExistingServer: !CI`), so tests ran against a build hours old. This
  produced 14 confident, repeatable failures that looked like real defects and
  were not. Killing the server dropped it to 5.
* The remaining 2–5 are genuine timing flakes under parallel load, mostly
  navigation-drawer and theme-toggle transitions.

**Impact:** a flaky suite trains people to re-run rather than investigate, which
is how a real regression gets waved through.
**Trigger:** before the suite gates a merge.
**Fix:** await the transition end rather than a timeout in the drawer and theme
tests. Separately, make CI always start a fresh server.

### M14 — Local Redis is a decade old, and the client is pinned to RESP2

Development runs Redis 3.0.504 — the archived 2016 Windows port, which predates
`HELLO` and therefore RESP3. `app/core/redis.py` sets `protocol=2` so the client
can connect at all.

Production targets Redis 7. The pin is harmless there, but the divergence means
local development exercises a materially different server from the one that will
run in production.

Memurai (Redis 7 compatible) is the intended fix; its installer fails 1603 on
this machine because MSI custom actions cannot write to `C:\Windows\Temp`, which
needs an elevated shell to correct.

**Trigger:** before relying on any Redis 5+ feature, or before treating local
Redis behaviour as evidence about production.

### ~~M1 — The backend Docker image installs a stub `app` package~~ ✅ RESOLVED 2026-07-31

The builder now extracts the dependency list from `pyproject.toml` and installs
only that, so nothing named `app` reaches site-packages. The layer cache is
preserved — it still invalidates only when `pyproject.toml` changes.

The extraction command was verified locally against the real manifest, though
the image itself remains unbuilt (C1).

### M1 (original) — The backend Docker image installs a stub `app` package

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

### ~~M9 — The dashboard still renders mock charts and headline stats~~ ✅ RESOLVED 2026-07-31

Phase 6 replaced the mock row with `GET /analytics/dashboard`. Charts and
headline stats come from `services/dashboard.ts`. `lib/mock/dashboard-data.ts`
was deleted; a repo-wide search finds no remaining `MOCK_` symbols.

### M8 — The dashboard bundle is 108 kB, almost all Recharts

Other routes are ~192 B. Recharts is imported statically by the dashboard, so
the whole library ships with that route.

Acceptable for one authenticated page. It stops being acceptable if analytics
adds more chart-heavy routes, since each would pull the same weight.

**Trigger:** a second chart-heavy route.
**Fix:** `next/dynamic` for the chart components so the library loads only where
it is used.

### M7 — The frontend build cache was tracked in git

`frontend/tsconfig.tsbuildinfo` had been committed since the Phase 0 scaffold.
It is machine-specific and regenerated on every typecheck, so it produced a
spurious diff on every run.

**Resolved 2026-07-31** — untracked and added to `.gitignore`.

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
