# Security hardening — completion report

Closing the production security gaps found by the pre-production authentication
audit, plus M18.

**Date:** 2026-08-01 · **Branch:** `develop` · **Tag:** `security-hardening-complete`

---

## Commits

| Commit | What |
|---|---|
| `731d534` | Five authentication gaps closed (S1–S5) |
| *this release* | M18 resolved, production template, security CI, documentation |

`main` was not touched at any point.

---

## 1. M18 — Docker secret hardening ✅

`docker-compose.yml` substituted a password published in this repository when
`POSTGRES_PASSWORD` or `RABBITMQ_PASSWORD` was unset. Both now use Compose's
`:?` syntax and refuse to start, naming the missing variable. Three
`CELERY_BROKER_URL` occurrences carried the same default and were changed too.

**Local development is unaffected.** `cp .env.example .env` supplies the values
and Compose reads `.env` automatically. What no longer works is `docker compose
up` with no environment at all — the dangerous path.

Non-secret defaults were deliberately kept (`POSTGRES_USER`, `POSTGRES_DB`,
`RABBITMQ_USER`, `NEXT_PUBLIC_API_URL`). A rule that fires on harmless things is
one people learn to route around.

Added [`.env.production.example`](../.env.production.example): every variable a
deployment needs, every secret blank, each annotated with what happens if it is
wrong.

## 2. Docker deployment validation (C1) — **NOT COMPLETE**

**Docker is not installed on this machine and neither is WSL.** Verified, not
assumed:

```
docker : NOT INSTALLED
wsl    : The Windows Subsystem for Linux is not installed
```

So the following were **not** run here, and I will not claim otherwise: backend
image build, frontend image build, database container startup, redis startup,
backend health check, frontend availability, nginx routing.

What exists instead, from Phase 7 and this release, in `.github/workflows/ci.yml`:

| CI job | Covers | Status |
|---|---|---|
| `docker` | Builds all three images | Runs on `develop` |
| `compose-config` | `docker compose config` + service list | Runs on `develop` |
| `celery-broker` | Celery against a real RabbitMQ | Runs on `develop` |
| `compose-smoke` | Full stack up, health checks | **Best effort — continues on failure** |
| `secrets` *(new)* | Committed secrets, deployment defaults | Runs on `develop` |

A separate `docker-validation.yml` was **not** created: the jobs the brief asks
for already exist, and a second workflow duplicating them would mean two files
to keep in step.

**C1 stays open.** `compose-smoke` continuing on failure means a green CI run
does not prove the stack comes up. Until either that job is made blocking on a
green run, or someone runs `docker compose up --build` on a real host, the
deployment path is unproven.

## 3. Authentication production review ✅ — no changes needed

Reviewed and found correct. Per the brief, working authentication was not
rewritten.

| Area | Finding |
|---|---|
| JWT signing key | From `SECURITY_SECRET_KEY`; ≥32 chars enforced in **every** environment; placeholder refused when deployed |
| Algorithm | Pinned to a **single-element list**, which is what defeats `alg=none` and algorithm-confusion |
| Claims | `sub, exp, iat, jti, typ, iss, aud` all required; `aud`/`iss`/`exp` verified |
| Token type | `typ` checked on decode, so a refresh token cannot be presented as an access token |
| Expiry | 15 min access, 30 day refresh |
| Rotation | Refresh token consumed and reissued on every use |
| Revocation | Stored **SHA-256 hashed**; reuse of a revoked token **terminates every session** |
| Passwords | **Argon2id** — memory-hard, so GPU parallelism does not help |
| Brute force | Login throttled on email **and** client IP independently |
| Cookies | `httpOnly` + `Secure` + `SameSite`; `Secure` now mandatory when deployed |
| Headers | HSTS, CSP, `X-Frame-Options: DENY`, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy` |

## 4. Documentation ✅

[`docs/PRODUCTION_SECURITY.md`](PRODUCTION_SECURITY.md) — required variables and
what breaks if each is wrong, secret management, rotation runbooks for both keys
(they differ, and the difference matters), a deployment checklist, and a
local-versus-production table.

It also states which rules are enforced by code and which are only convention. A
document that cannot tell you the difference is worse than none.

## 5. Automated security checks ✅

[`scripts/check_secrets.py`](../scripts/check_secrets.py) — five rules, each
targeting a specific unrecoverable mistake:

1. A committed `.env`
2. A published Fernet key outside the test suite
3. A template with a populated secret
4. An incomplete production template
5. A re-introduced Compose secret default

**Deliberately not a generic scanner.** Entropy heuristics on a repository this
size produce dozens of hits — base64 fixtures, lockfile hashes, UUIDs — and a
check that cries wolf is one people learn to skip. There is no warning level.

**Every rule was verified to actually fire**, by breaking each in a scratch copy
and confirming the checker failed. That found a genuine defect in the checker
itself: it scanned only *tracked* files, so a brand-new template was invisible
until first commit — exactly when a mistake is most likely. Now fixed and
re-verified at 5/5.

CI additionally runs `docker compose config` with the passwords **unset**, to
prove the M18 guard refuses rather than trusting that `:?` was written
correctly.

---

## Tests

| Gate | Result |
|---|---|
| `ruff check` | pass |
| `ruff format --check` | pass |
| `mypy` strict | pass, 145 source files |
| `pytest` | **648 passed** |
| `scripts/check_secrets.py` | pass, 376 files against 5 rules |
| Checker self-verification | **5/5 rules fire** |
| Startup validation probe | **10/10** correct behaviours |
| Log redaction probe | **0** secrets leaked (was 6) |

Frontend gates were not re-run: nothing in this release touches frontend code.

---

## Remaining risks

1. **C1 — the deployment path has still never been executed.** Images unbuilt
   locally; the compose smoke job continues on failure. **This is the largest
   remaining risk and it is unchanged by this release.**
2. **No secret manager.** The documented path is environment variables. Nothing
   integrates with Vault or AWS Secrets Manager.
3. **Key rotation is a manual runbook**, not automation.
4. **Webhook signature verification is opt-in** (M11), not mandatory.
5. **Email verification is implemented but disabled** — no mail provider (H4).
6. **Local `.env` still uses the placeholder signing key**, so local JWTs are
   forgeable by anyone with the repository. Correct for development; never treat
   a local session as evidence.

---

## Readiness

**Configuration and authentication security: ready.** Every control is enforced
by code and verified by a test that was observed failing before the fix.

**Deployment: not ready.** Unchanged by this release, and the reason the score
is not higher.

**Score: 74/100** (was 62 at Phase 3).

What moved it: five authentication gaps closed and pinned (+6); M18 resolved
with the guard proved in CI rather than assumed (+3); production secret
documentation and template (+2); automated secret checks that are themselves
verified (+1).

What still holds it down: the deployment path has never run (−20), no secret
manager (−3), and the manual rotation runbook (−3).

**Recommendation:** resolve C1 before any production deployment. Everything else
on this list is a known, bounded risk; C1 is an unknown, because nothing about
the deployment path has ever been executed.
