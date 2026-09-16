# CI baseline repair — security templates, pytest import, and PostgreSQL CI tooling

## Manager task
Restore the existing `develop` CI baseline without weakening any guard. The security-template and pytest-package fixes are complete. Authoritative CI then exposed environment-only failures: PostgreSQL 17.11 server with PostgreSQL 16.15 client tools on the hosted runner, and a brittle Compose refusal probe that unsets two required passwords while asserting only the PostgreSQL diagnostic.

## Scope
Only these files may change:
- `deploy/lightsail/app.env.example`
- `scripts/r7_provision_stack.py`
- `backend/tests/__init__.py` (create if required)
- `backend/tests/conftest.py` only if package initialization alone is insufficient
- `backend/tests/unit/test_infra_l1_deployment.py` only to align its stale placeholder assertion with the scanner's required blank secret assignments
- `.github/workflows/ci.yml` only to (a) install/select PostgreSQL 17 client tools for the backend CI job so `pg_dump`/`pg_restore` match the existing PostgreSQL 17 service and (b) make the existing PostgreSQL-password Compose refusal probe deterministic without weakening it
- this task document only for implementation/verification notes

## Acceptance criteria
1. `python scripts/check_secrets.py` passes without weakening or editing the scanner.
2. Sensitive values in `deploy/lightsail/app.env.example` remain documented but secret-bearing template assignments flagged by the scanner are blank.
3. `scripts/r7_provision_stack.py` does not contain either published test Fernet key. Generate any synthetic R7 encryption key at runtime using the existing cryptography dependency rather than committing another fixed key.
4. The backend test suite can import `tests.environment` under the CI Python/pytest layout. Prefer the smallest package-layout correction; do not rewrite application imports.
5. `test_the_environment_template_contains_no_real_value` recognizes the scanner-required blank secret assignments as valid only when they are empty; it continues requiring `CHANGE-ME` placeholders for the other non-allowlisted template variables.
6. Backend CI keeps the existing PostgreSQL 17 service and runs the real backup/restore drill with PostgreSQL 17 `pg_dump` and `pg_restore`. The hosted runner must configure the official PostgreSQL APT repository (or an equivalently explicit trusted source) before installing `postgresql-client-17`, then verify the resolved client major version. Do not skip, xfail, mock, or weaken the backup drill.
7. The Compose password guard still proves an unset `POSTGRES_PASSWORD` is rejected, but isolates that assertion from the separate required `RABBITMQ_PASSWORD` so diagnostic ordering cannot make the guard flaky. Supplying only a synthetic CI RabbitMQ placeholder while explicitly unsetting PostgreSQL is acceptable; accepting a successful Compose config is not.
8. Workflow changes remain limited to these two CI-environment corrections. Do not change workflow triggers, permissions, service database major version, test commands, scanner, deployment behavior, or production configuration.
9. No production application behaviour, migrations, deployment execution, or real credentials are changed.

## Forbidden actions
Do not edit `scripts/check_secrets.py` to silence findings. Do not add scanner allowlists for the flagged files. Do not broadly weaken or skip the infra unit test. Do not skip, xfail, mock, or delete the backup/restore drill. Do not modify `app/services/database_backup.py` to tolerate an incompatible PostgreSQL client. Do not downgrade the PostgreSQL 17 CI service. Do not remove or bypass the Compose required-password guard; the probe must still fail unless PostgreSQL password is supplied. Do not alter GitHub workflow triggers or permissions. Do not commit a real or reusable secret. Do not merge, deploy, contact production, or use real provider credentials. Do not modify application source.

## Verification
Trusted CI is authoritative. Security scanner and Compose guard, backend lint/format/mypy/migrations/full pytest, frontend lint/types/build, and all downstream CI jobs that are enabled after the primary jobs must pass. Cursor performs a final independent read-only review of the exact pushed HEAD.

## Implementation note (retry on head `210176a`)
- `deploy/lightsail/app.env.example`: blanked the four flagged assignments
  (`SECURITY_ENCRYPTION_KEYS`, `SHOPIFY_API_KEY`, `SHOPIFY_API_SECRET`,
  `ALIEXPRESS_APP_SECRET`) while leaving every surrounding comment intact.
- `scripts/r7_provision_stack.py`: replaced the committed published test
  Fernet key with `Fernet.generate_key()`, generated once per run and used
  only for the disposable reviewer stack's env file.
- `backend/tests/__init__.py`: added so pytest's default import mode roots
  the `tests` package at `backend/` instead of `backend/tests`. That is what
  makes `tests.environment` (imported by `conftest.py`) resolve regardless of
  which files an editable install exposes on `sys.path`. `conftest.py` itself
  did not need to change.

## Cursor follow-up finding on head `9302c16`
Cursor independently confirmed the four-file repair is security-scoped, but found
that `backend/tests/unit/test_infra_l1_deployment.py` still required `CHANGE-ME`
in every non-allowlisted assignment. That contradicted the scanner rule that the
four sensitive assignments above must be empty. The follow-up repair changed
only that unit-test assertion, narrowly: those four secret names assert an empty
value, while the existing `CHANGE-ME` requirement remains intact for all other
variables. A subsequent Ruff-only annotation (`ClassVar`) preserved those semantics.

## Authoritative CI follow-up on head `29a23115`
Run `34761459399` proved Security and Frontend green. Backend Ruff, format, strict
mypy, and Alembic migrations also passed, and full pytest reached all 2,984 tests.
The only failures were 14 setup errors in
`tests/integration/test_backup_restore_drill.py`, all caused by the same tool
mismatch: the `postgres:17-alpine` service reported PostgreSQL 17.11 while the
Ubuntu runner invoked `pg_dump` 16.15. PostgreSQL correctly aborted with
`server version mismatch`.

## Authoritative CI follow-up on head `939dad85`
Run `34762526996` proved two details before the full suite could run:
- Ubuntu 24.04 runner sources could not locate `postgresql-client-17` directly, so the workflow must configure the official PGDG repository first. PostgreSQL's official Ubuntu instructions support Noble and publish PostgreSQL 17 client packages.
- The secret scanner still passed. The Compose guard failed only because both `POSTGRES_PASSWORD` and `RABBITMQ_PASSWORD` were removed, Compose reported the missing RabbitMQ password first, and the probe asserted that the first diagnostic contain `POSTGRES_PASSWORD`. The corrected probe must isolate the PostgreSQL-password assertion rather than relax it.

## Contract amendment on head `1f4dc5d2` — owner adopted 2026-09-14
Authoritative CI on the last in-contract head `ddfce552` (run `34762683334`) exposed two latent
downstream failures once the upstream gates were green: the Celery broker job and the compose
smoke both failed with RabbitMQ `Queue.declare (541) INTERNAL_ERROR - Feature transient_nonexcl_queues
is deprecated` (the floating `rabbitmq:4-alpine` tag now denies that feature by default), and the
CI worker consumed queue `celery` while `task_default_queue` is `default`, so the execution checks
could never complete. The Playwright job then exposed cross-site `localhost`/`127.0.0.1` refresh-cookie
loss and a GSI/password "Sign in" selector ambiguity. On the PR base those downstream jobs were
*skipped* because Security and Backend failed first; the original acceptance criterion "downstream
CI all pass" was therefore unachievable within the original file list.

These were fixed by commits `51d6d277`, `333efdb6`, `d930eafb`, `a298fb44` and `1f4dc5d2`, pushed
directly by Cursor Agent outside the agent bridge's read-only Cursor role and its workflow-path
refusal. The owner adopted them on 2026-09-14 with that route disclosed; no further bypass is
authorized.

**Superseded only for the adopted change set and the Phase 1 closure paths named in the PR body:**
- "Do not modify application source" is superseded solely for `backend/app/workers/celery_app.py`
  adding `control_queue_exclusive=True` and `event_queue_exclusive=True`. These are application
  runtime settings, not CI-only: on the next deployment every worker's pidbox control queue and
  every event receiver's queue becomes exclusive to its connection. Single-worker CI verifies
  declaration, `inspect ping` and end-to-end execution of five real tasks; multi-worker,
  restart/reconnect and external-monitor behavior are unverified and gated to the later
  safe-staging phase. Deployment currently pins `rabbitmq:4.0-alpine`, which still permits the
  deprecated feature, so production does not yet require the change.
- "Workflow changes remain limited to these two CI-environment corrections" is superseded solely
  for the named `ci.yml` hunks (`default,integrations` queue selection; synthetic Google/Shopify
  values; `127.0.0.1` origins/CORS/callbacks; report upload on cancellation) plus the Phase 1
  F-2 report-publication change.
- The Scope file list is widened to the PR body's 12-file table plus the Phase 1 closure paths.

All other prohibitions — scanner, allowlists, triggers, permissions, migrations, dependency ranges,
backup drill, Compose guard, merge, deploy, production, credentials — remain in force unchanged.

**Independent-review findings carried into Phase 1 (DP-P00-02, 2026-09-14):**
- F-1 (Medium): with the synthetic Google client id configured, `auth-provider-boundary.spec.ts`,
  `auth.spec.ts`, `smoke.spec.ts` and `terms.spec.ts` load `accounts.google.com/gsi/client` for
  real; no global interception exists. Closed by Phase 1 task DP-PH1-05.
- F-2 (Low): the CI `github` reporter prints skip totals only and the report uploads only on
  failure/cancel, so the composition of the 9 skips is not independently visible. Closed by
  DP-PH1-07.
- F-3 (Low): PGDG signing key trusted via HTTPS + `Signed-By`, no fingerprint pin. Accepted.
- F-4 (Info): the `celery_app.py` comment's "until 5.7" is a forward claim; resolved versions are
  Celery 5.6.3 / Kombu 5.6.2 / amqp 5.3.1, in which both settings exist with default `False`.
- F-5 (Info): CI `rabbitmq:4-alpine` vs deployment `rabbitmq:4.0-alpine`.

## Phase 1 execution record — DP-PH1-03 … DP-PH1-09 (2026-09-14)

**Worker mode (DP-PH1-03).** Claude Code in the owner's workspace, worktree `C:\dspph1` on
`fix/ci-baseline-security-pytest`. Isolated ports `3137` (frontend) and `8137` (API origin,
build-inlined; no backend was run). Test ports `3000`/`8000`/`8001` were observed occupied by
unrelated owner processes and were not touched. No `.env` read; `frontend/.env.local` is
synthetic and git-ignored. No local PostgreSQL, Redis or broker: the backend needs credentials
this worker must not read, so every backend-dependent check is evidenced by trusted CI only.

**Guard verification (DP-PH1-04).** `scripts/check_secrets.py` byte-identical to `develop`;
`deploy/lightsail/app.env.example` carries exactly the four scanner-required blank values;
`scripts/r7_provision_stack.py` generates the Fernet key per run; the deployment test's
`SCANNER_REQUIRED_BLANK` names the same four keys; `ci.yml` keeps the PG17 client step and the
deterministic Compose probe (`RABBITMQ_PASSWORD` placeholder, `POSTGRES_PASSWORD` unset, asserts
the non-zero exit and the message); `docker-compose.yml` keeps all six `:?` guards.

**F-1 mechanism, refined (DP-PH1-05).** The GIS button issues `POST /api/v1/auth/google/nonce`
*before* it injects `<script src="https://accounts.google.com/gsi/client">`. With a backend up
(CI) the nonce succeeds and the script request follows; without one (a backend-less local run)
the nonce fails, the button settles on "unavailable", and Google is never contacted. This is why
F-1 was invisible locally and only real in CI. The fixture
`frontend/tests/e2e/fixtures/provider-isolation.ts` therefore routes `accounts.google.com/**` on
the **browser context** (popups inherit it), fulfilling an empty script, and is imported by
`auth-provider-boundary`, `auth`, `smoke` and `terms`. `auth-g1.spec.ts` and `csp.spec.ts` keep
their own page-level Google stubs, which Playwright evaluates before context routes. The
regression `provider-isolation.spec.ts` stubs only the nonce endpoint so it reaches the script
step hermetically; its negative control targets a same-origin sentinel path, never a real
provider. What it proves: the route layer answered the request (`fulfilled-local`/`aborted`)
and the real library never executed (`window.google` undefined). What it does not measure:
socket-level egress.

Local evidence (standalone build, `127.0.0.1:3137`, no backend, both Playwright projects):
`provider-isolation` 10/10; `auth`, `smoke`, `terms`, `csp`, `auth-provider-boundary`,
`auth-g1`, `api-isolation` together 252 passed / 46 skipped / 0 failed — every skip reads
"Backend API is not reachable" (`auth-g1` 28, `csp` 10, `auth-provider-boundary` 8). The
first draft of the regression failed 8/10 locally for two reasons that are recorded here
because they changed the design: (a) the nonce precondition above; (b) the sentinel guard was
built as `<url>/**`, which never matches the exact URL — it now routes the exact sentinel URL.

**Affected-flow verification (DP-PH1-06).** Hermetic parts verified locally as above.
Backend-dependent parts (`auth-provider-boundary` boundary cases, `auth-g1` Google flows, CSP
under a live API) are verified only by the authoritative CI run on the final SHA; see the
Phase 1 report.

**F-2 evidence (DP-PH1-07).** `playwright.config.ts` adds `list` and `json`
(`test-results/playwright-results.json`) reporters under `CI`. `ci.yml` uploads
`frontend/playwright-report` and `frontend/test-results` on `always()` under
`playwright-report-<sha>-run<run_id>-attempt<attempt>` with `if-no-files-found: warn`. Known
limits, stated rather than hidden: the workflow runs Playwright twice (the 18-test
`seed-harness` pre-step, then the suite) and Playwright clears `test-results/` per run, so the
artifact carries the *suite* run only — the pre-step is evidenced by the job log's `list`
output and its exit code; CI runs `--project=chromium` only, so the `mobile-chrome` project is
local-only evidence; a warn-level empty upload is not a pass — the Playwright step's exit code
gates the job.

**Celery evidence statement (DP-PH1-08).**
- Resolved versions: `celery==5.6.3`, `kombu==5.6.2`, `amqp==5.3.1` (pinned in
  `backend/requirements/runtime.txt` and `dev.txt`). CI broker `rabbitmq:4-alpine` resolved to
  **RabbitMQ 4.3.5 / Erlang 27.3.4.17** (digest `sha256:3486d982…bbabd`, run 34785118939).
  Deployment: Lightsail `rabbitmq:4.0-alpine` (minor pin), local `rabbitmq:4-management-alpine`
  (floating).
- Failure that was repaired: run 34762683334, both the broker job and the Compose smoke, failed
  on `Queue.declare: (541) INTERNAL_ERROR - Feature transient_nonexcl_queues is deprecated.` —
  Celery's pidbox control and event queues are declared transient and non-exclusive by
  default, which RabbitMQ 4.3.5 refuses.
- Change: `control_queue_exclusive=True` and `event_queue_exclusive=True` in
  `app/workers/celery_app.py` (settings exist in Celery 5.6 with default `False`; Celery guards
  against setting `*_durable` together with `*_exclusive`). Queue mapping:
  `task_default_queue="default"`; CI worker consumes `default,integrations` — the earlier
  `celery` queue name matched nothing and tasks sat unconsumed.
- Executed checks (CI, head `1f4dc5d2`): worker start with `--queues=default,integrations`;
  `inspect ping` readiness through the broker; `scripts/verify_celery_broker.py` — broker
  connection ok, 6 required tasks registered, 13 beat entries resolve to registered tasks,
  `workers.health` executed with `ok`, then `inventory.sync`, `pricing.recalculate`,
  `orders.sync_all`, `orders.cleanup` executed end-to-end (5 tasks in total); Compose smoke:
  `docker compose exec worker celery inspect ping` answered via the broker.
- Runtime effect: these are application settings, not CI-only. On the next deployment every
  worker's pidbox control queue and every event receiver's queue is declared exclusive
  (deleted when its connection closes; not shareable across connections).
- Unverified and deferred to the safe-staging phase: more than one concurrent worker
  (mingle/gossip and `inspect` across workers); worker restart/reconnect under the new
  declarations; any external event consumer (Flower, `celery events`); behaviour on RabbitMQ
  4.0.x as pinned for Lightsail; the beat process. The single-worker CI drill does not stand
  in for these.

**Documentation freeze (DP-PH1-09).** `CHANGELOG.md` `[Unreleased] → Fixed` Phase 1 entry;
`PROJECT_ROADMAP.md` Phase 1 row and "Last updated"; ledger `phase-1-progress.md`. Unrelated
UX-L2B lines untouched.
