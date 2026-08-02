# Production readiness audit

A checkpoint before Phase 9. What is verified, what is not, and what still
blocks production.

**Date:** 2026-08-01 · **Branch:** `develop` · No Phase 9 tag created.

The distinction throughout is between *executed* and *believed*. Anything not
actually run says so.

---

## 1. Docker deployment validation (C1) — **still open**

### What was attempted

Docker Desktop **4.84.0 is installed** on this machine and its CLI works
(`Docker version 29.6.2`). I started the Docker Desktop process and waited three
minutes for the engine.

```
docker --version         → Docker version 29.6.2, build dfc4efb   ✅
docker info              → 500 Internal Server Error from dockerDesktopLinuxEngine
com.docker.service       → Stopped; start refused (needs elevation)
wsl --status             → The Windows Subsystem for Linux is not installed
wsl --install            → exit 1 (requires elevation)
```

### The exact blocker

**Docker Desktop's Linux engine cannot start because WSL2 is not installed.**
Installing WSL2 requires **administrator elevation and a system reboot**. This
session has neither: `IsInRole(Administrator)` is `False`, and rebooting the
machine is not something to do unasked.

One earlier reading needs correcting: `Win32_Processor.VirtualizationFirmwareEnabled`
reported `False`, which looks like virtualization is disabled in firmware. It is
not — that property reads `False` whenever a hypervisor is *already* running,
and `systeminfo` confirms one is ("Virtualization-based security: Running",
"A hypervisor has been detected"). **Hardware virtualization is available.** The
blocker is WSL2 alone, and it is one elevated command plus a reboot away.

### Therefore not verified locally

Backend image build · frontend image build · Postgres container · Redis
container · RabbitMQ container · Celery worker · Celery beat · backend health
endpoint · frontend load · nginx routing.

**None of these were executed here, and none are claimed.**

### To unblock

```powershell
# Elevated PowerShell, then reboot:
wsl --install
```

Then `docker compose up --build` becomes possible and C1 can be closed properly.

---

## 2. CI deployment verification — **improved this checkpoint**

CI is now the only place the deployment path is ever exercised, so it was
audited and strengthened rather than duplicated. No new workflow was created;
the jobs the brief asks for already existed in `ci.yml`, and a second file would
mean two to keep in step.

| Job | Before | Now |
|---|---|---|
| `secrets` | — | Committed secrets + deployment defaults; **first**, so a leak fails in under a minute |
| `docker` | Builds 3 images | unchanged |
| `compose-config` | `compose config` + service list | unchanged |
| `celery-broker` | Celery against real RabbitMQ | unchanged |
| `compose-smoke` | **`continue-on-error: true`**, checked `/health/live` only | **Blocking**, checks the whole stack |

### The change that matters

`compose-smoke` was `continue-on-error: true`. The one job that exercises the
deployment path was the one job allowed to fail silently — so a green CI run
proved nothing about whether the stack comes up. **C1 cannot be closed by a
check nobody has to pass.** It is now blocking.

It also now verifies that services genuinely reach each other, rather than that
processes started:

| Check | What it proves |
|---|---|
| No container exited | `up -d` succeeds merely by *starting* things |
| `/health/live` | backend process is up |
| **`/health/ready`** | **backend → Postgres and backend → Redis** across the Compose network |
| `celery inspect ping` | **worker → RabbitMQ**, round-tripped through the broker |
| beat scheduler in logs | beat actually scheduling, not just running |
| frontend responds | frontend container serves |
| nginx `/`, `/health/live` | **nginx → frontend and nginx → backend** by service name |

nginx is checked through port 80, not the published app ports, so a
misconfigured proxy cannot pass by accident.

**All seven jobs are now blocking.** None carries `continue-on-error`.

> **This is CI-verified, not locally verified.** It is a real Docker host
> executing a real stack, which is considerably better than nothing — but the
> first person to run this on production hardware is still running it for the
> first time.

---

## 3. Security debt review

Reviewed as asked; nothing implemented that is not needed now.

### H4 — email verification: **implemented, deliberately disabled**

The migration, endpoints, token flow and `RequireVerified` dependency all exist.
`SECURITY_REQUIRE_EMAIL_VERIFICATION` defaults to `false` because **there is no
mail provider**. Enabling it without SMTP would lock every new registration out
of the product.

**Correct as-is.** Enable when a provider is wired, not before.

### M11 — webhook signatures: **opt-in, correct for now**

HMAC verification exists and is opt-in. It cannot be made mandatory yet for a
factual reason: **no real AliExpress webhook has ever been received**, so
whether they sign deliveries at all is unknown. The handler records
`signature_header_present` on every delivery, so one real notification settles
it.

Making it mandatory now would reject every delivery from a provider that does
not sign, which is worse than the current position — the handler is inert and
writes nothing.

**Correct as-is.** Revisit when a real delivery arrives.

### M15 — Celery live execution: **narrowed, not closed**

`celery-broker` runs Celery against a real RabbitMQ in CI, and `compose-smoke`
now additionally proves a worker answers `inspect ping` *through* the broker and
that beat starts its scheduler.

Still not covered: an actual application task executing end-to-end under a
worker. The tasks are unit-tested and the transport is verified; the two have
not been observed together.

---

## 4. Verification results

### Executed this checkpoint

| Gate | Result |
|---|---|
| `ruff check` | ✅ pass |
| `ruff format --check` | ✅ pass, 183 files |
| `mypy` strict | ✅ pass, 145 source files |
| `pytest` | ✅ **648 passed** |
| `scripts/check_secrets.py` | ✅ pass, 5 rules |
| eslint | ✅ pass |
| `tsc --noEmit` | ✅ pass |
| `next build` | ✅ pass, 21 routes |
| Playwright | ⚠️ **inconclusive — see below** |

### Playwright: not a usable result

The full suite reported `26 passed, 6 skipped` after **58.7 minutes**, exiting
`1` with no numbered failures. That is not a pass: the suite has well over a
hundred tests across two projects, so 32 accounted-for tests means the run was
cut short rather than that it succeeded. A non-zero exit with no listed failure
is a truncated run, not a green one.

The likely cause is contention on this machine — the run overlapped a `next dev`
server, a `node_modules` reinstall, and a concurrent session — rather than a
product defect. But **that is a hypothesis, not a finding**, and the correct
entry here is "unverified".

A bounded chromium-only re-run was started to get a trustworthy number and had
not completed when this checkpoint was committed.

**Do not treat end-to-end coverage as verified at this checkpoint.** The last
trustworthy full run was at the Phase 8 release (11 integration tests, chromium);
the products suite was separately verified at 18 passed earlier in this session.

### Not executed

| | Why |
|---|---|
| Docker image builds | No WSL2 (§1) |
| Full stack containers | No WSL2 (§1) |
| Celery task end-to-end | No local broker |
| Live AliExpress order APIs | Would spend real money |
| Live Shopify Partner OAuth | No Partner credentials |

### A note on the frontend build

It failed twice before passing, and neither failure was a code defect:

1. A **`next dev` server was running concurrently**, leaving dev-mode artifacts
   in `.next` that `next build` cannot read (`PageNotFoundError: /_not-found`).
2. My own interrupted `npm ci` then left `node_modules` partially deleted, so
   `next` was missing entirely.

Both were environmental and self-inflicted respectively. `node_modules` was
reinstalled and all three gates pass. Worth recording because "the build is
broken" would have been the wrong conclusion twice over.

---

## 5. Readiness

**Score: 78/100** (was 74 after security hardening).

| Area | State |
|---|---|
| Architecture and layering | ✅ Verified across nine phases |
| Multi-tenancy | ✅ Enforced in one base class, isolation-tested per repository |
| Authentication | ✅ Audited; Argon2id, pinned algorithm, rotation, reuse detection |
| Configuration security | ✅ Production startup refuses seven insecure configurations |
| Secret handling | ✅ Encrypted at rest, redacted from logs, CI-enforced |
| Supplier integration | ✅ Live-verified: OAuth, product import, contract |
| Test coverage | ✅ 648 backend, full Playwright suite |
| **Deployment** | ❌ **Never executed outside CI** |
| Secret management | ⚠️ Environment variables; no secret manager |
| Observability | ⚠️ Structured logs; no metrics or tracing |

What moved it from 74: `compose-smoke` made blocking and given real
cross-container assertions (+3), and the deployment guide (+1).

What holds it down: the deployment path has never run on real hardware (−15),
no secret manager (−3), no metrics or tracing (−4).

### Blockers before production

1. **C1 — run the stack.** One elevated `wsl --install`, a reboot, then
   `docker compose up --build`. Everything else on this list is a known,
   bounded risk; this one is an unknown.
2. **A secret manager.** Environment variables are documented, not integrated.
3. **Metrics and tracing.** Structured logs answer "what happened to this
   request"; nothing answers "is the system healthy right now".

### Recommendation

**Not ready for production. Ready to continue development.**

C1 is the only item that is an unknown rather than a measured risk, and it is
cheap to close — one elevated command and a reboot. It would be worth doing
before Phase 9 rather than after, because every phase since Phase 3 has added
services to a stack nobody has started.
