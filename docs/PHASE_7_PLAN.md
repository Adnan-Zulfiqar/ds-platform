# Phase 7 plan — production hardening & operational readiness

| | |
|---|---|
| Date | 2026-07-31 |
| Branch | `develop` |
| Base tag | `phase-6-complete` (`6725d3a`) |
| Local Docker | **Unavailable** (`docker` not on PATH) |

This plan is derived from a mandatory audit of the Phase 6 tree, `TECHNICAL_DEBT.md`,
CI, Compose, Celery, webhooks, and auth. Items that cannot be verified on this
machine are marked as such up front — they will not be reported as complete.

---

## Audit summary

| Area | State |
|---|---|
| Images / Compose / Nginx | Written; never run locally. CI builds images only on `main` PRs/pushes |
| Celery | Tasks + beat entries exist; unit-tested via `.run()`; never against RabbitMQ |
| Compose worker | Present; **no beat service** |
| Webhook | Replay + classify + count; unsigned; globally rate-limit exempt (M11/M12) |
| `is_verified` | Column + principal; registration always sets `true`; no gate (H4) |
| Email | No mail provider; forgot-password UI honestly disabled |
| Dashboard mocks | Removed in Phase 6 (M9 closed) |
| Unused FE | `stores/notification-store.ts` orphaned; `/products/import` nav stale |

---

## Priorities and approach

### 1. C1 — Deployment validation (highest)

**Goal:** a proven deployment path, not a claim.

| Action | Verifiable without local Docker? |
|---|---|
| Extend CI to run on `develop` / PRs targeting `develop` | Yes |
| Keep image matrix build (`backend`, `worker`, `frontend`) | Yes (CI) |
| Add `docker compose config` validation in CI | Yes |
| Add Compose `beat` service (gap found in audit) | Yes (code); runtime via CI/Docker later |
| Optional short Compose smoke (`up` → health → `down`) in CI | Yes if CI has Docker-in-Docker (ubuntu-latest does) |
| Local `docker compose up --build` | **No** — document remaining gap |

**Honesty rule:** C1 is only fully closed when images build **and** the stack
has been brought up with health checks. This phase aims to close the *build*
and *compose-config/smoke-in-CI* legs. If the CI smoke cannot run, the
completion report states exactly what remains.

### 2. M15 — Celery under a real broker

| Action | Notes |
|---|---|
| Add RabbitMQ service to CI Celery job | Real broker, not eager mode for that job |
| Worker health / inspect registered tasks | Script + pytest integration |
| Execute representative tasks against broker | inventory / pricing / orders / cleanup |
| Document retry + idempotency (already on `BaseTask`) | Tests prove enqueue+ack path |
| Try native RabbitMQ locally if installable | If not, CI is the verification path |

**Honesty rule:** mocked `.run()` alone does not close M15.

### 3. M11 / M12 — Webhook security

AliExpress still has no confirmed public signing scheme for this endpoint.
Phase 7 implements a **documented dual mode**:

1. **If `ALIEXPRESS_WEBHOOK_SECRET` is set:** HMAC verification over raw body;
   invalid signature → 401; valid → existing path.
2. **If unset (default):** current non-mutating behaviour, plus a **shed-without-429**
   limiter so the public URL is not an unbounded DoS surface (M12).

Replay protection stays (reject duplicate message ids). Payload logging stays
field-name-only. Order mutation remains forbidden until a verified signature
path exists in production with a real delivery.

### 4. AliExpress client hardening

- Regression tests for retry / rate-limit / timeout mapping (extend existing)
- Live `AliExpressClient.call()` re-run where credentials exist
- No schema changes from documentation guesses

### 5. H4 — Verification foundation (no fake mail)

- `EmailVerificationToken` model + migration
- Mailer protocol + logging backend (records intent; does not pretend SMTP works)
- `POST /auth/verify-email/request` and `…/confirm`
- `RequireVerified` dependency wired when `SECURITY_REQUIRE_EMAIL_VERIFICATION=true`
- Default remains **off** and registration still sets `is_verified=true` until
  a real mail provider is configured — flipping the default without mail would
  lock every new user out

### 6. Store channel decision

`docs/STORE_CHANNEL_DECISION.md` — A/B/C with recommendation; **no OAuth
implementation** in Phase 7 unless the decision explicitly says implement now
(expected: decide, defer build).

### 7. Frontend cleanup

- Delete unused Zustand notification store
- Fix `/products/import` nav inconsistency
- Loading/error/empty audit on ops pages (light touch)
- lint / typecheck / build

### 8. Testing & docs

Full gates; Playwright with honest M13 notes; `PHASE_7_COMPLETION.md`;
roadmap / changelog / debt updates; tag `phase-7-complete`; push `develop` + tag.

---

## Risks and assumptions

| Risk | Mitigation |
|---|---|
| Local Docker absent | CI is the deployment verifier; score C1 partially if smoke fails |
| No AliExpress webhook signature docs | Dual-mode secret; never mutate from unsigned body |
| No SMTP | Verification foundation + feature flag; do not flip default |
| RabbitMQ hard to install on Windows | CI RabbitMQ job is the authoritative M15 proof |
| Playwright flakes (M13) | Prefer serial/retries for full suite; document remaining flakes |

---

## Out of scope

- Phase 8+ product features
- Shopify/Woo OAuth implementation (decision doc only)
- Touching `main`
- Claiming C1/M15/M11 complete without the corresponding real run
