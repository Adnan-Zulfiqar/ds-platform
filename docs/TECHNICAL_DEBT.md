# Technical debt register

Reviewed 2026-07-31 at `phase-1-complete`; updated after Phase 2.
Housekeeping pass 2026-07-31 — **H1, H2, and H3 resolved.**
**Phase 4 release review 2026-07-31** — M10 resolved; M15 added.
**Phase 5 release review 2026-07-31** — M16 added; M9/M11/M15 updated; live
`AliExpressClient.call()` verified for category success and order error/list paths.
**Phase 6 release review 2026-07-31** — M9 resolved (dashboard uses live
analytics); M15 expanded to cover Phase 6 Celery tasks; live `product.get`
re-verified for inventory sync.
**Phase 7 release review 2026-07-31** — C1 narrowed (CI compose/images/celery);
M11/M12 mitigated (HMAC opt-in + shed); H4 foundation shipped (enforcement
off); M15 CI broker job added (local RabbitMQ still absent).
**Phase 8 release review 2026-08-01** — Shopify channel shipped; live Partner
OAuth/Admin verification still open (M17).
**Pre-production authentication audit 2026-08-01** — five gaps found by probing
rather than reading, all now closed and pinned by tests (see S1–S5 below).
**Security hardening 2026-08-01** — M18 resolved (Compose secret defaults
removed and the guard proved in CI); `.env.production.example`,
`docs/PRODUCTION_SECURITY.md` and `scripts/check_secrets.py` added.
**Production readiness checkpoint 2026-08-01** — C1 narrowed again: the CI
compose smoke job is now **blocking** and asserts cross-container connectivity
rather than merely that processes started. M15 narrowed. C1 remains open;
the exact blocker is now known and is one elevated command away. See
[PRODUCTION_READINESS_AUDIT.md](PRODUCTION_READINESS_AUDIT.md).
**Phase 9 Stage 3 (2026-08-02)** — product optimisation architecture shipped
through `StubProvider`; M19 records that live model output remains unverified.
**Full application audit fix pass (2026-08-03)** — A-01, A-02, A-03, A-04,
A-05, A-09, and A-16 resolved, A-06 fix landed (CI job unverified), A-15
partially (Shopify disconnect) (see below). Remaining: A-06 CI green, A-07,
A-08, A-15 (remaining surfaces).

**Current count: 1 critical (C1, narrowed), 1 high (narrowed), ~12 medium, 5 low.**

---

## Resolved by the full application audit fix pass (2026-08-03)

### ~~A-01 — Shopify shop domain not globally unique; webhook routing ambiguous~~ ✅ RESOLVED

**Root cause:** uniqueness was only `(tenant_id, shop_domain)`, and webhooks
resolved tenants by scanning up to 1000 connected rows and taking the first
domain match — two tenants could own one shop; HMAC-valid webhooks could write
orders into the wrong workspace.

**Fix:** migration `0012` (dedupe + global unique on `shop_domain`);
`ShopifyMaintenanceRepository.get_connected_by_shop_domain`; connect rejects
foreign ownership (`ShopifyShopTakenError`); webhook uses indexed lookup.

**Verified:** ruff, mypy strict, pytest **745** passed (including new unit +
integration coverage).

### ~~A-03 — Tenant admin can mutate platform-global AI prompts~~ ✅ RESOLVED

**Root cause:** `ai_prompts` is unscoped platform reference data, but create /
version / activate were available to any tenant admin (`RequireAdmin`). One
workspace could change prompts that drive every other tenant's AI output.

**Fix:** `AISettings.allow_prompt_mutation` defaults to `false`
(`AI_ALLOW_PROMPT_MUTATION`). `PromptService` refuses create / create_version /
activate unless the flag is on. Reads and test-render remain. Tests set the
flag so existing prompt suites keep exercising the write path.

**Verified:** ruff, mypy strict, full pytest **746** passed (including new unit +
HTTP coverage for the lock).

### ~~A-04 — Shopify `publish_product` not Celery-idempotent~~ ✅ RESOLVED

**Root cause:** `task_acks_late` delivers at least once. Publish POSTed to
Shopify then persisted `StoreListing`. A crash between those steps left no
local listing; redelivery POSTed again and created a duplicate Shopify product.

**Fix:** deterministic handle `droppilot-{product_id}`;
`_create_or_adopt` looks up by handle before create and adopts any existing
product from a prior attempt.

**Verified:** ruff, mypy strict, pytest **748** passed (new unit coverage for
handle + adopt-vs-post). Untracked WIP webhook tests excluded from this gate.

### ~~A-05 — Nginx same-origin story broken by baked `NEXT_PUBLIC_API_URL`~~ ✅ RESOLVED

**Root cause:** Compose baked `NEXT_PUBLIC_API_URL=http://localhost:8000`, so
browsers entering via nginx `:80` still called the published API port — CORS /
cookie failures and an unused `/api` proxy.

**Fix:** Compose default and `.env.example` use `http://localhost`; CORS lists
both `:3000` and the nginx origin; CI compose smoke asserts the frontend image
does not embed `localhost:8000`; docs corrected.

**Verified:** ruff, mypy strict, pytest **751** passed (compose-default + CORS
default coverage). Compose smoke bundle assertion is in CI (not executed in
this local pass).

### ~~A-02 — Logout does not clear React Query cache~~ ✅ RESOLVED

**Root cause:** `logout` commented that React Query was discarded, but only
called `router.refresh()` (Next RSC). The `QueryClient` retained the previous
tenant's queries (`staleTime: 60s`).

**Fix:** `queryClient.clear()` on logout (in `finally`), on `onTokenCleared`,
and on login/register before mounting the new identity. Non-production
exposes a probe handle for Playwright.

**Verified:** `npm run lint`, `npm run typecheck`, `npm run build`; Playwright
`signing out clears the React Query cache (A-02)` passed (chromium).

### ~~A-06 — Playwright not in CI; rate-limit flakes on default env (M13)~~ ✅ RESOLVED

**Root cause:** No CI Playwright job; default `SECURITY_RATE_LIMIT_REQUESTS=100`
is exceeded by a full local e2e run; `npm run start` mismatches standalone
output.

**Fix:** `frontend-e2e` CI job with `SECURITY_RATE_LIMIT_REQUESTS=1000`;
`.env.example` documents the e2e ceiling without changing the production
default; Playwright boots via `npm run start:e2e` (standalone server).

**Verified:** unit wiring tests; local Playwright against `start:e2e` /
standalone (A-02 probe). Full CI job runs on push (not executed in this
local pass).

### ~~A-16 — Uncommitted Shopify webhook tunnel workaround on working tree~~ ✅ RESOLVED

**Root cause:** a prior session's local-tunnel workaround was left uncommitted
and was actually broken — `webhook_delivery_address()` was referenced by a
test but never defined, so the whole unit test module failed to collect.

**Fix:** implemented `webhook_delivery_address()` (`shopify/service.py`): when
the configured callback base is not itself a `.../webhooks` address (i.e. a
path-scoped tunnel that only forwards the OAuth callback path in local
development), every topic registers against that one address and the receiver
tells topics apart via `X-Shopify-Topic`. Also closed a related gap found
while fixing this: webhook registration covered 4 of 6 documented topics —
added `products/create` and `app/uninstalled`.

**Verified:** ruff, ruff format, mypy strict (158 files), pytest **754**
passed, including new coverage for `webhook_delivery_address` itself.

### ~~A-09 — Shopify webhook replay fails open into mutating upserts~~ ✅ RESOLVED

**Root cause:** on a Redis error, the replay-dedup check swallowed the
exception and let the webhook through — a Redis outage during a replayed
`orders/create`/`orders/updated` delivery would silently re-run the upsert.

**Fix:** fails closed (503) specifically for mutating topics (`orders/create`,
`orders/updated`, `app/uninstalled`); non-mutating topics (`products/*`,
`inventory_levels/*`) still acknowledge on a dedup-store outage, since
DropPilot only acknowledges those today without processing them — a dedup
failure there cannot produce a duplicate write. Also added handling for
`app/uninstalled` itself: the connection is now marked `ERROR` the instant
Shopify sends the webhook, rather than waiting for the next Admin API call to
fail with 401.

**Verified:** ruff, ruff format, mypy strict, pytest **754** passed —
new integration coverage: replay-fails-closed for a mutating topic,
replay-still-open for a non-mutating one, and `app/uninstalled` actually
marking the connection `ERROR` (verified via a genuinely separate committed
transaction, matching how the webhook handler itself reads).

**Not verified:** live Shopify Partner OAuth/webhook delivery — no Partner app
credentials on this machine (see M17). All Shopify fixes in this pass are
verified by unit/integration tests against a mocked or fake Redis/HTTP
boundary, not a live Shopify store.

---

## Resolved by the pre-production authentication audit (2026-08-01)

Each was verified failing before the fix and passing after. The audit probed
behaviour rather than reading code, which is the only reason these were found:
every one of them is silent at runtime — the application starts, reports
healthy, and is wrong.

### ~~S1 — Production started with no encryption keys~~ ✅ RESOLVED

`SECURITY_ENCRYPTION_KEYS` was unvalidated at startup. A deployment with the
value empty booted normally and failed only when someone first connected a
supplier, where it read as an integration bug rather than a misconfiguration.
Startup now refuses.

### ~~S2 — Production accepted an encryption key published in this repository~~ ✅ RESOLVED

The two Fernet keys in `tests/conftest.py` are printed in a public repository
and decode to the literal `test-key-N-NEVER-USE-IN-PROD-!!!`. Nothing stopped
one reaching production via a copied `.env`, where every customer credential
would have been encrypted with a key any reader already has. Both are now
denylisted for deployed environments, and the check covers a published key
anywhere in the rotation list rather than only first.

### ~~S3 — Production accepted `SECURITY_COOKIE_SECURE=false`~~ ✅ RESOLVED

The refresh cookie is the longest-lived credential a browser holds. Without
`Secure` it travels over plain HTTP, where anyone on the path can lift it and
mint access tokens for its full thirty-day life. It is weakened locally on
purpose — the test client speaks HTTP — which is precisely why the deployed case
needed a guard rather than a convention.

### ~~S4 — The signing key could double as the encryption key~~ ✅ RESOLVED

They have different rotation stories. A signing key can be replaced the moment a
leak is suspected, at the cost of ending every session; an encryption key cannot,
because stored ciphertext must be re-encrypted first. Sharing one value silently
blocks an urgent rotation behind a slow migration.

### ~~S5 — Logs emitted credentials verbatim~~ ✅ RESOLVED

The pipeline had no redaction processor. A probe logging six credential-shaped
fields produced six secrets in the output. The audit found no call site that
actually does this — but discipline describes the code as it is today, and the
pipeline is also fed by third-party libraries.

A redaction processor now runs before rendering. The field *name* is kept and
only the value replaced, so "there was an Authorization header and it was
redacted" stays visible. Booleans and numbers are preserved
(`password_valid=False` reveals nothing), and an allowlist protects diagnostics
that merely look sensitive — `token_type` says which kind of token was
rejected, `signature_header_present` answers whether a provider signs its
webhooks at all.

**32 tests** cover these, including three asserting local development still
starts with no encryption keys, an insecure cookie, and the published test keys
— hardening that breaks local development gets disabled, and then protects
nothing.

---

## Medium

### ~~M18 — Compose defaulted to a guessable password for Postgres and RabbitMQ~~ ✅ RESOLVED 2026-08-01

`docker-compose.yml` used `${POSTGRES_PASSWORD:-droppilot}` and
`${RABBITMQ_PASSWORD:-droppilot}`, so `docker compose up` on a host with those
variables unset silently brought up infrastructure with a password published in
this repository.

Unlike S1–S4 the application could not detect this: it receives a working DSN
and has no way to know the password was a default. The guard therefore had to
live where the substitution happens.

**Fixed** by replacing both defaults with Compose's `:?` syntax, which refuses
to start and names the missing variable. The `CELERY_BROKER_URL` occurrences —
three of them, easy to miss — were changed too.

**Local development is unaffected.** `cp .env.example .env` supplies the values
and Compose reads `.env` automatically. What no longer works is running
`docker compose up` with no environment at all, which was the dangerous path.

Non-secret defaults were deliberately kept (`POSTGRES_USER`, `POSTGRES_DB`,
`RABBITMQ_USER`, `NEXT_PUBLIC_API_URL`). Requiring those would add friction
without removing risk, and a rule that fires on harmless things is a rule people
learn to route around.

**Verified two ways.** `scripts/check_secrets.py` fails the build if any
`${…PASSWORD…:-}` or `${…SECRET…:-}` default reappears, and a CI step runs
`docker compose config` with the variables *unset* to prove the guard actually
refuses — rather than trusting that the `:?` syntax was written correctly. Each
of the checker's five rules was individually verified to fire.

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

**C1 remains open but narrowed**: Docker is still not installed on this
machine. Phase 7 added develop-branch CI image builds, `docker compose config`,
a Celery broker job, and a best-effort compose smoke job. Local
`docker compose up --build` still cannot be run here.

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

### C1 — Deployment path not proven on a local Docker host

Three Dockerfiles, Compose (now including **beat**), and Nginx remain unbuilt on
this development machine (no Docker). Phase 7 made the path **CI-ready**:
`develop` triggers image builds, `compose config` validation, and a best-effort
compose smoke job.

**Still missing locally:** a human-confirmed `docker compose up --build` with
healthy postgres/redis/rabbitmq/backend/worker/beat/frontend/nginx.

**Impact:** first deploy on a real host may still surprise.
**Trigger:** before production traffic.
**Fix:** install Docker Desktop and run the stack, or confirm the CI
`docker` / `compose-config` / `compose-smoke` jobs green on `develop`.

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

### H4 — Email verification enforcement is off (foundation only) — narrowed

Phase 7 added `EmailVerificationToken`, a logging mailer (no SMTP),
`/auth/verify-email/request|confirm`, JWT `email_verified`, and
`RequireVerified` on role gates when `SECURITY_REQUIRE_EMAIL_VERIFICATION=true`.

Enforcement remains **off by default** and registration still creates verified
users when the flag is false — flipping the default without a mail provider
would lock every signup out. That is intentional, not incomplete wiring.

**Impact:** none while the flag is false; correct denial once enabled with mail.
**Trigger:** wiring a real mail provider and setting the flag true.
**Fix:** SES/Resend (or equivalent) behind `Mailer`; flip the flag; set
registration `is_verified=false` (already conditional on the flag).

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

### M15 — Celery under a broker — CI path added, local still absent

Phase 7 added `workers.health`, Compose beat, a worker healthcheck, and
`scripts/verify_celery_broker.py`, plus a GitHub Actions `celery-broker` job
(RabbitMQ + worker + task execution). Local RabbitMQ was not available on this
machine, so the job has not been observed green yet.

**Impact:** scheduled sync is still unproven until the CI job (or a local
Compose run) succeeds.
**Trigger:** before advertising scheduled sync to customers.
**Fix:** confirm `celery-broker` green on `develop`; optionally run Compose
worker+beat once Docker is installed.

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

### ~~M11 — The webhook accepts unsigned, unauthenticated deliveries~~ ⚠️ MITIGATED 2026-07-31

Phase 7 added opt-in HMAC via `ALIEXPRESS_WEBHOOK_SECRET` (401 on mismatch) and
documented that AliExpress has not confirmed a public signing scheme. Unsigned
mode remains the default. **Order mutation from webhook payloads is still
forbidden.**

**Residual:** confirm the real AliExpress signature header/algorithm against a
live delivery, then require the secret in production.

### ~~M12 — The webhook is exempt from inbound rate limiting~~ ⚠️ MITIGATED 2026-07-31

Phase 7 added a per-IP shed limiter that drops excess traffic while still
returning **200** (no retry-storm). Global rate-limit exemption remains for the
same reason.

**Residual:** tune shed limits under real delivery volume.

### M19 — Phase 9 AI output is StubProvider-only (no live model key)

Stages 1–3 exercise the full provider → prompt → product-version pipeline, but
every generation uses `StubProvider`. No `OPENAI_API_KEY` / Anthropic / Gemini
key is configured, so marketplace-ready copy quality cannot be verified.

**Impact:** Optimise UI and APIs work; published AI text would be obviously
synthetic (`[STUB-AI]`).
**Trigger:** before merchants rely on AI copy in production.
**Fix:** configure a real `AI_PROVIDER` + key; build the concrete provider in
Stage 4+; capture one live fixture per generator (same pattern as M10).

### M17 — Shopify live Partner OAuth and Admin API unverified

**2026-08-01 update:** Live connect tracing shows DropPilot OAuth *start* works
and the failure is Shopify's authorize screen (Unauthorized / 403) before
callback — typically Allowed redirection URL / app install eligibility. See
`docs/SHOPIFY_CONNECTION_DEBUG_REPORT.md`.

Phase 8 implemented OAuth, Admin REST client, publish/inventory/price/order
import, and HMAC webhooks. No Shopify Partner app credentials were available on
the development machine, so nothing was exercised against a real shop.

**Impact:** first live install may surface scope, API version, or webhook HMAC
mismatches.
**Trigger:** before advertising Shopify to customers.
**Fix:** configure `SHOPIFY_*` against a development store; run OAuth once;
publish one product; confirm webhook HMAC; commit fixtures from live payloads.

### M13 — The Playwright suite is flaky under load / rate limits — **mitigated (A-06)**

Phase 7 re-run (chromium):

* Default workers: **68 passed, 9 failed** — many failures were registration
  `429 rate_limit_exceeded` or timeouts waiting for `/dashboard`.
* `--workers=1 --retries=2`: **73 passed, 3 flaky, 2 failed** — remaining hard
  failures were still API registration 429 after retries exhausted.

E2E helpers back off on 429 for API and UI registration. That reduces but does
not eliminate shared-IP bucket exhaustion on a long suite against a live local
backend.

**2026-08-02 investigation.** A prior checkpoint reported 8 failures (13
passed, 8 failed, 18 skipped) and left them uninvestigated. Reproduced and
root-caused rather than assumed:

1. **Compounding, now-fixed environmental fault:** three stray `next dev`
   processes (from an unrelated earlier session) were squatting on port 3000.
   `playwright.config.ts` sets `reuseExistingServer: true` locally, so
   Playwright silently attached to a dev-mode server instead of the
   `npm run start` production build the config declares. Stopping them and
   rebuilding cleanly (`rm -rf .next && npm run build`) alone fixed 7 of the 8
   reported failures.
2. **Real root cause of the remainder:** `SECURITY_RATE_LIMIT_REQUESTS=100`
   (the production default, also the local `.env` default) is genuinely
   exceeded by one legitimate serial run of the full suite. Confirmed directly
   — polling `ratelimit:ip:127.0.0.1` in Redis mid-run showed the counter
   reaching **101, 103, 106, 110** against a limit of 100, in a single clean
   `--workers=1` pass. This is unauthenticated traffic (registration, and
   anything before a session exists) sharing one IP-keyed bucket — the same
   bucket a credential-stuffing attempt would hit — and a 77-test suite
   generates more than 100 such requests inside one 60-second window even run
   serially. Confirmed not a test-logic bug: the one failure without the
   "registration" signature (`products.spec.ts` AliExpress-import test) was
   re-run in isolation and skipped cleanly, as designed.

**Fix applied (local only):** raised `SECURITY_RATE_LIMIT_REQUESTS` to `1000`
in the local, gitignored `.env` — not `.env.example`, not the `config.py`
default, not any CI job. The production default is unchanged. Raising the
ceiling rather than setting `SECURITY_RATE_LIMIT_ENABLED=false` (the fix
`docs/DevelopmentSetup.md` previously suggested) keeps the limiter itself
exercised during e2e instead of switched off — a regression that made the
limiter *too* lenient would still be caught; one that made it too strict for
real traffic would not have been. `docs/DevelopmentSetup.md`'s troubleshooting
entry now recommends this instead of disabling the control.

**Result:** full suite, both projects, re-run clean after the fix —
chromium 74 passed / 0 failed / 3 skipped, mobile-chrome 74 passed / 0 failed
/ 3 skipped. The 3 skips per project are the documented live-AliExpress-OAuth
tests, which correctly skip when the live gateway rejects a synthetic auth
code (see M10).

**What this does NOT close:** ~~there is still no CI job that runs Playwright.~~
**Closed by A-06 (2026-08-03):** `frontend-e2e` in `.github/workflows/ci.yml`
runs chromium against a live API with `SECURITY_RATE_LIMIT_REQUESTS=1000`.
`.env.example` documents the same ceiling for local full-suite runs without
changing the production default of 100. Playwright boots the standalone
server (`npm run start:e2e`) instead of `next start`.

**Impact:** merge confidence requires either serial workers with the local
ceiling raised, or the CI e2e job (now present).
**Trigger:** before the suite gates a merge without a human running it first.
**Fix:** CI e2e job + documented local `SECURITY_RATE_LIMIT_REQUESTS=1000`;
standalone e2e server script.

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
