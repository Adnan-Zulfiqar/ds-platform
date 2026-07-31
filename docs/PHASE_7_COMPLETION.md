# Phase 7 — completion report

**Production hardening and operational readiness.**

Everything below was verified by running the quality gates on 2026-07-31. Where
something has never been executed on this machine, that is stated rather than
implied.

| | |
|---|---|
| Date | 2026-07-31 |
| Branch | `develop` |
| Tag | `phase-7-complete` → commit `1ed5477` (pushed; not moved) |
| Base | `phase-6-complete` (`6725d3a`) |
| Migration | `0007` — `email_verification_tokens` |
| Local Docker | **Unavailable** (`docker` not on PATH) |

### Commits

| Hash | Message |
|---|---|
| `2ddbced` | feat(ops): harden webhooks and add Celery/Compose deployment validation |
| `3839e58` | feat(auth): add email verification foundation without fake SMTP |
| `8d6eeda` | fix(frontend): remove stale nav/store and stabilize e2e registration |
| `52da999` | docs: close Phase 7 production hardening |
| `1ed5477` | docs: record Phase 7 commit and tag tip hashes |

`phase-7-complete` was pushed at `1ed5477`. Later docs-only tip commits may sit
ahead of the tag on `develop` without moving the tag.

---

## 1. What shipped

| Priority | Outcome |
|---|---|
| C1 deployment path | Compose `beat` service; CI on `develop`; image builds; `compose config`; compose smoke (best-effort, `continue-on-error`); local Docker still unavailable |
| M15 Celery | `workers.health` task; worker healthcheck; `scripts/verify_celery_broker.py`; CI job with RabbitMQ + live worker task execution |
| M11/M12 webhooks | Opt-in HMAC (`ALIEXPRESS_WEBHOOK_SECRET`); shed-without-429 when unsigned; unit + integration tests |
| AliExpress client | Existing retry/timeout/rate-limit suite remains; live `call()` **not** re-run (no connected account after DB rebuild) |
| H4 verification | Token claim + `RequireVerified`; mailer protocol + logging backend; request/confirm endpoints; enforcement **off** by default |
| Store channels | `docs/STORE_CHANNEL_DECISION.md` — stay manual; Shopify first later |
| Frontend | Removed orphaned notification Zustand store; removed stale `/products/import` nav item |

---

## 2. Deployment status (C1)

| Check | Status |
|---|---|
| Dockerfiles present (`backend`, `worker`, `frontend`) | Yes |
| Compose includes postgres, redis, rabbitmq, backend, worker, **beat**, frontend, nginx | Yes |
| Worker HEALTHCHECK / compose healthcheck | Yes (`celery inspect ping`) |
| Local `docker compose up --build` | **Not run** — Docker not installed |
| CI image matrix | Extended to `push`/`pull_request` on **`develop`** and `main` |
| CI `docker compose config` | Added |
| CI compose smoke | Added, `continue-on-error: true` — must be confirmed on GitHub Actions after push |

**Honest score for C1:** partial. Build + compose-config path is CI-ready; full stack bring-up is not proven on this workstation.

---

## 3. Celery status (M15)

| Check | Status |
|---|---|
| Tasks registered in app imports | Yes (incl. `workers.health`) |
| Beat schedule entries present | Yes |
| Retry / failure logging on `BaseTask` | Yes (`on_failure`, `on_retry`, backoff+jitter, `acks_late`) |
| Local RabbitMQ + worker | **Not available** on this machine |
| CI broker job (RabbitMQ service, worker process, `verify_celery_broker.py`) | Added — executes `workers.health`, `inventory.sync`, `pricing.recalculate`, `orders.sync_all`, `orders.cleanup` against a real broker |

**Honest score for M15:** closed via CI design; not locally exercised. Confirm the `celery-broker` job is green after push.

---

## 4. Webhook security status

| Mode | Behaviour |
|---|---|
| Secret set | HMAC-SHA256 over raw body; bad/missing → **401** |
| Secret unset (default) | Shed-without-429 per IP; still **200** |
| Replay | Duplicate message ids ignored |
| Mutation | Still **forbidden** — polling sync is source of truth |
| Logging | Field names/counts only |

Tests: valid signature accepted; invalid rejected; replay ignored; shed over limit.

---

## 5. H4 — email verification foundation

- Migration `0007` / model `EmailVerificationToken` (hashed tokens, tenant-scoped)
- `LoggingMailer` — logs intent, does not pretend SMTP works
- `POST /auth/verify-email/request` and `/confirm` (authenticated confirm)
- Access tokens carry `email_verified`; `RequireVerified` on role gates when
  `SECURITY_REQUIRE_EMAIL_VERIFICATION=true`
- Default remains **off**; registration keeps `is_verified=true` until a real
  mail provider exists

---

## 6. Tests executed (this machine)

| Gate | Result |
|---|---|
| `ruff check` / `ruff format --check` | Pass |
| `mypy app` (strict) | Pass |
| `pytest` | **583 passed** |
| Frontend `lint` / `typecheck` / `build` | Pass |
| Live `AliExpressClient.call()` | **Not run** — no `CONNECTED` AliExpress row |
| Playwright (chromium, workers=1, retries=2) | **Exit 0** — 73 passed, 3 flaky (passed on retry), 3 skipped |
| Local Docker / Celery broker | **Not run** |

Playwright honesty (M13): parallel run was **68 passed / 9 failed**, dominated by
inbound **rate_limit_exceeded (429)** on `/auth/register`. Helpers now back off
on 429. Residual flakes remain under load even serially.

---

## 7. Production readiness score

| Area | Score (0–10) | Notes |
|---|---|---|
| App correctness (API + FE gates) | 8 | Full local quality gates green |
| Deployment path | 5 | CI ready; local Docker absent; smoke unconfirmed until Actions |
| Background jobs | 6 | Code + CI job; not locally proven |
| Webhook security | 7 | Dual mode + tests; no real AliExpress signed delivery yet |
| Auth / verification | 6 | Foundation solid; no SMTP; enforcement off |
| Store channels | 5 | Manual-first by decision; OAuth deferred |
| **Overall** | **~6.5 / 10** | Hardened for next deploy attempt; not yet “flip to production” |

---

## 8. Remaining limitations

1. Docker Desktop / Compose never executed on the development machine (C1 residue).
2. Celery broker verification depends on the new CI job after push.
3. No live AliExpress connection in the local database for re-verification.
4. Email verification cannot be enforced until a mail provider is wired.
5. Webhook HMAC scheme is DropPilot’s documented fallback — not confirmed against a live AliExpress push signature.
6. Playwright timing flakes under parallelism (M13) remain; prefer serial/`--retries=2` for merge confidence.

---

## 9. Files of note

- `docs/PHASE_7_PLAN.md`, `docs/STORE_CHANNEL_DECISION.md`
- `.github/workflows/ci.yml` — develop triggers, compose config, celery-broker, smoke
- `docker-compose.yml` — `beat` + worker healthcheck
- `backend/app/integrations/aliexpress/webhook_security.py`
- `backend/app/services/email_verification.py`, `mailer.py`, migration `0007`
- `backend/scripts/verify_celery_broker.py`, `backend/app/tasks/health.py`
