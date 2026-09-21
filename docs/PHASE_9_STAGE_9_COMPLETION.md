# Phase 9 Stage 9 — completion report

**Celery bulk pipeline preview: durable PostgreSQL progress, one sequential
worker, lease fencing, honest retries.**

Status: **COMPLETE / UNMERGED / AWAITING INDEPENDENT REVIEW.**

Not merged. Production undeployed — `main` unchanged.
Stage 10 / 11 not started. Claude has not reviewed Stage 9. Cursor is the
temporary implementation + self-review agent; this report is not a Claude
review.

| | |
|---|---|
| Date | 2026-09-21 |
| Accepted plan | [PHASE_9_STAGE_9_PLAN.md](PHASE_9_STAGE_9_PLAN.md) |
| Plan PR | [#23](https://github.com/Adnan-Zulfiqar/ds-platform/pull/23) |
| Accepted plan head | `5e453b7776ba532dea8593cf2864e9aa552fb8c7` |
| Plan merge / baseline | `develop` @ `e65b2f5b28210d71c107651d935195d7c694c15a` |
| Post-merge plan CI | [35619263921](https://github.com/Adnan-Zulfiqar/ds-platform/actions/runs/35619263921) — 10/10 SUCCESS |
| Implementation branch | `feat/phase-9-stage-9-bulk-ai` |
| Migration | **0034** (`0034_pipeline_bulk_runs.py`, `down_revision=0033`) |
| Frontend | **none** |
| Deployment | **NO** |
| Stage 10 / 11 | **NOT STARTED** |
| AI involvement | `StubProvider` only. Stage 9 does **not** claim live provider timeout / rate-limit retry. Provider preflight uses `get_ai_provider(settings)` without a completion |

CLAUDE RETURN REVIEW CHECKPOINT:
All commits from Stage 5 takeover onward require a fresh Claude
end-to-end review when Claude becomes available again.

---

## 1. What Stage 9 does

A tenant admin can start one bulk run of up to 50 product ids. HTTP returns
**202**. One Celery task (`ai.process_pipeline_bulk_run`, default queue)
processes the run **sequentially**, one product at a time.

Each successful item creates **only** an inactive pipeline preview candidate
via `ProductPipelineService.preview(...)`. Stage 9 does not call `approve`,
`activate_version`, `optimize_product`, or `publish`. It does not write
`StoreListing`. It does not set `Product.ai_status` to optimized. It does not
publish AI SEO.

Merchant review remains Stage 8: inspect → approve exact candidate → publish
exact approved candidate.

---

## 2. Migration 0034

`backend/alembic/versions/0034_pipeline_bulk_runs.py`

- `down_revision = "0033"`. **0033 was not edited.**
- Unique pairs for composite FK targets:
  - `uq_products_tenant_id_id` on `products(tenant_id, id)`
  - `uq_product_versions_tenant_id_id` on `product_versions(tenant_id, id)`
- Matching SQLAlchemy metadata on `Product` and `ProductVersion` only. No
  product/version column changes.
- Enums use **values**, not Python names:
  - run: `pending`, `running`, `completed`, `partial`, `failed`, `cancelled`
  - item: `pending`, `succeeded`, `failed`, `skipped`, `missing`
- Tables: `pipeline_bulk_runs`, `pipeline_bulk_run_items`

Run constraints:

- `uq_pipeline_bulk_runs_tenant_idempotency` — `(tenant_id, idempotency_key)`
- `uq_pipeline_bulk_runs_tenant_id_id` — `(tenant_id, id)`
- partial unique `uq_pipeline_bulk_runs_tenant_active` on `tenant_id`
  `WHERE status IN ('pending', 'running')`

Item constraints:

- `uq_pipeline_bulk_run_items_run_product` — `(run_id, submitted_product_id)`
- composite run FK `(tenant_id, run_id)` → runs `(tenant_id, id)` **ON DELETE CASCADE**
- composite product FK `(tenant_id, product_id)` → products **ON DELETE RESTRICT**
- composite candidate FK `(tenant_id, candidate_version_id)` → versions **ON DELETE RESTRICT**
- CHECK `ck_pipeline_bulk_run_items_succeeded_version`:
  `state='succeeded'` iff `candidate_version_id IS NOT NULL`

Disposable-database gate (isolated Postgres `droppilot_mig_0034_s9` on
`127.0.0.1:5499`, not the development Compose database):

1. `alembic heads` → exactly `0034 (head)`
2. `upgrade head` → `0034 (head)` including `0033 → 0034`
3. constraint / index / enum inspection matched the contract
4. `downgrade 0033` → `0033`
5. `upgrade head` again → `0034 (head)`

FK delete actions verified: product/version/store **RESTRICT** (`confdeltype=r`);
run composite and tenant FKs **CASCADE**; `requested_by_user_id` **SET NULL**.

---

## 3. Models / tables

`backend/app/models/pipeline_bulk.py`

- `PipelineBulkRun` — durable run, counters, claim/lease, heartbeat, recovery
- `PipelineBulkRunItem` — one submitted UUID; `product_id` and
  `candidate_version_id` are NULL at create

At create every item is:

- `submitted_product_id` = the submitted UUID
- `product_id = NULL`
- `candidate_version_id = NULL`
- `state = pending`

Ownership is resolved later, tenant-scoped, by the worker. Mixed own + foreign
ids therefore cannot 404-enumerate the catalogue at start.

`PipelineBulkRun.requested_by_user_id` is declared in
`USER_REFERENCES` as **clear** (tenant-scoped), same pattern as
`rule_applications`.

---

## 4. Celery tasks

Registered in `celery_app.conf.imports` via `app.tasks.ai`.

| Task | Queue | Beat |
|---|---|---|
| `ai.process_pipeline_bulk_run` | `default` | no |
| `ai.reconcile_pipeline_bulk_runs` | `default` | every 5 minutes |

Payload is **`run_id` only**. No tenant id argument. No product arrays. No
credentials or provider output on RabbitMQ.

Worker tenant authority:

1. read `request_id` if useful
2. `clear_context()`
3. unscoped `PipelineBulkRunTenantLookup.tenant_for(run_id)`
4. bind durable `run.tenant_id`
5. all subsequent work tenant-scoped
6. `finally: clear_context()`

Forged `_context.tenant_id` is ignored.

---

## 5. Run / item states

**Run**

- `completed` — all processed, `failed=0`, `missing=0`
- `partial` — all processed, `succeeded>0`, and (`failed>0` or `missing>0`)
- `failed` — all processed with `succeeded=0` and failures/missing; or a
  run-level fatal; or retry/recovery exhaustion
- `cancelled` — merchant cancellation

A 48-success / 2-failure run is **partial**, not completed.

**Item**

- `succeeded` — exact `candidate_version_id` stored; candidate inactive
- `failed` — permanent classified failure; no candidate
- `skipped` — archived / unavailable; no preview
- `missing` — foreign / deleted / not found; `product_id` remains NULL
- `pending` — not yet terminal

Postgres is merchant progress truth. `AsyncResult` is never used as progress.

`processed_count` increments only in the same commit as item terminalization.
`finalize` recomputes counts from item rows.

---

## 6. Idempotency and one-active-run

Request requires `idempotencyKey` (1–128). Fingerprint is SHA-256 of canonical
`deduped/sorted productIds` + `tone` + `storeId`. Duplicate ids do not change
the fingerprint. Selection storage keeps first-seen order.

Create:

```
try insert run + items (flush run first, then items)
except IntegrityError:
    rollback
    re-read by tenant_id + idempotency_key
```

Then: same key + same fingerprint → original 202; same key + different
fingerprint → 409; no matching key → 409 `pipeline_bulk_run_active`.

The failed SQLAlchemy transaction is never queried before rollback.

One-active-run is the **partial unique index**, not SELECT-before-INSERT.
Concurrent tests use two real sessions.

`storeId` is optional. Tenant-scoped `StoreRepository` lookup before insert.
Foreign / missing / deleted store → **404, no run**. Store is advisory
readiness only. If the store disappears after creation: run-level
`store_not_found`, current preview transaction is not committed as success,
the product is **not** classified missing, prior successful candidates remain,
remaining generation stops.

---

## 7. Lease / fencing

Copied from RuleApplication:

- `claimed_by_task_id` is identity
- `lease_token` is the fence
- claim under `FOR UPDATE`
- pending → running + mint lease
- same task owns running → resume + mint new lease
- other task owns live running → no-op
- terminal → no-op

`_lock_owned`: tenant-scoped `SELECT … FOR UPDATE` with
`populate_existing=True`, then require `status == running` and matching
`lease_token`. Otherwise LOST / superseded — **zero writes**.

`_PipelineBulkLeaseHolder`: `holder.lease` is set only after a successful
claim. Ownership loss sets `holder.lease = None`. The task wrapper keeps the
current lease for exhaustion handling.

Successful Txn B holds the run row `FOR UPDATE` for the whole preview
transaction, so reclaim / cancel wait at the item boundary.

---

## 8. Txn A / success-only Txn B / classified failure

**Txn A (`claim_attempt`)** — durable attempt claim. `_lock_owned`, lock next
pending item `FOR UPDATE`, increment `attempt_count`, set `started_at` the
first time, heartbeat, commit. No provider call. No candidate. Item stays
pending. `attemptCount` is durable processing attempts entered, not guaranteed
provider calls.

**Txn B (`process_item_success`)** — success only. `_lock_owned`, item
`FOR UPDATE`, then preview on the **same session**. Missing / foreign /
deleted may terminalize as `missing` here because preview was never called.
Archived / unavailable → `skipped`, no preview. Only if preview fully
succeeds: attach `product_id`, `state=succeeded`, exact
`candidate_version_id`, increment succeeded + processed, heartbeat, commit.

If preview raises **any** exception, Txn B does **not** terminalize. The
caller’s `transaction()` rolls back (lower repositories may already have
rolled back on `ConflictError`). Classification happens afterward.

**Classified failure** — a **new** transaction: `_lock_owned`, lost lease →
zero writes, lock item, require still pending, write terminal outcome,
increment counters once, heartbeat, commit.

- `AIError` → item `failed`, `error_code=ai_error` (or the exception code), no task retry
- `ValidationError` / missing prompt variables → item `failed`, stable safe code, no task retry
- product `NotFound` → `missing`, `product_id=NULL`, processed + missing once
- store `NotFound` → **run-level** `store_not_found`, item not marked missing

**`ConflictError`** from preview (Stage 8 race on
`uq_product_versions_product_number`) is a **task/concurrency retry**, not a
provider retry and not a permanent item failure. Item stays pending; candidate
absent; no item counter write; Txn A `attemptCount` survives; the error is
re-raised for `BaseTask`. Successful retry yields exactly one pipeline
candidate.

---

## 9. Provider preflight and retry honesty

After a successful run claim, **before the first item**,
`get_ai_provider(settings)` is called once (no completion).

`AIProviderNotConfiguredError` → lease-conditionally mark remaining pending
items `failed` with `errorCode=ai_provider_not_configured`, recompute counts,
run `failed`, clear lease, **no** `BaseTask` retry, **no** `ProductVersion`.

Live `preview` converts provider failures into base `AIError(retryable=False)`.
Stage 9 therefore does **not** claim live provider timeout / rate-limit retry.
`BaseTask` covers escaping DB / infrastructure errors, worker failures, time
limits, unexpected errors, and the `ConflictError` concurrency race.

`PromptService` was not modified.

---

## 10. Retry exhaustion

Existing `BaseTask`. **Not modified.** `max_retries` remains **3**.

| `request.retries` | Behaviour |
|---|---|
| 0 | re-raise (`celery.exceptions.Retry`) |
| 1 | re-raise |
| 2 | re-raise |
| ≥ 3 | owner-conditionally `_mark_pipeline_bulk_failed` via `_lock_owned`, **return**, no further raise |

No fourth retry. Stale lease → superseded, zero writes. Prior committed
successes remain.

---

## 11. Reconciler

`ai.reconcile_pipeline_bulk_runs`

- beat: 5 minutes
- pending grace: 2 minutes
- stale-after: 20 minutes
- recovery ceiling: 3
- sweep: 50 pending + 50 stale

Recover unpublished pending runs. Recover abandoned running runs with observed
`heartbeat_at` + `lease_token` conditional update: `running → pending`, lease
cleared, `recovery_count + 1`, publish **after** commit. At the ceiling: run
`failed`. No infinite recovery.

---

## 12. Cancellation

`POST /api/v1/products/pipeline/runs/{run_id}/cancel`

- pending → cancelled immediately
- running → cooperative at the item boundary (current Txn B may finish)
- already cancelled → idempotent 200
- other terminal → 409

No Celery revoke. Already-created candidates are kept.

---

## 13. API

Prefix `/api/v1/products`. All four `RequireAdmin`. Static `/pipeline/runs*`
paths are declared before `/{product_id}` so `pipeline` is not parsed as an id.

| Method | Path | Status |
|---|---|---|
| POST | `/pipeline/runs` | 202 |
| GET | `/pipeline/runs/{run_id}` | 200 |
| GET | `/pipeline/runs/{run_id}/items` | 200 `Page[PipelineBulkRunItemRead]` |
| POST | `/pipeline/runs/{run_id}/cancel` | 200 |

Start body: required `productIds` + `idempotencyKey`; `tone` default
`professional` (1–64); optional `storeId`. Rate limit
`endpoint_rate_limit("pipeline-bulk-start", limit=10, window_seconds=60)`.

`MAX_PIPELINE_BULK_PRODUCTS = 50`. 51+ → 422, never truncated. Empty after
validation / dedup → 422.

Foreign run → **404**. Foreign store on start → **404**, no run.

Schemas: `backend/app/schemas/pipeline_bulk.py`, `CamelCaseModel`,
`extra=forbid`. Run read has **no** embedded item array.

Stage 8 schemas and endpoints were not changed.

---

## 14. Real RabbitMQ harness

`backend/scripts/verify_pipeline_bulk_broker.py`

CI job `celery-broker` runs it after `verify_celery_broker.py`. Isolated test
PostgreSQL, real RabbitMQ, separately running real Celery worker, StubProvider
only. Creates a run through `PipelineBulkRunService`, commits, publishes
`ai.process_pipeline_bulk_run` through RabbitMQ. Polls **PostgreSQL**, not
`AsyncResult`.

Requires: run `completed`; item `succeeded`; `candidateVersionId` stored;
exactly **one** pipeline-marked `AI_GENERATED` candidate as this item’s effect;
candidate id == item; `active=false`; `pipelineCandidateVersion=1`; candidate
tenant == run tenant. Original snapshot may also exist. No approval, no
Shopify publish, no `StoreListing`.

Then a **duplicate** broker message with the same `run_id`: pipeline candidate
count remains 1; `candidateVersionId` unchanged; counters unchanged.

Worker-kill testing is **not** claimed.

Local execution (2026-09-21): real RabbitMQ + `--pool=solo` worker against
isolated `droppilot_test` on `127.0.0.1:5499` →
`pipeline bulk broker verification: PASSED`.

---

## 15. Tests and gates

Focused Stage 9 suite: **51 passed** (9 unit + 42 integration).

| File | Role |
|---|---|
| `test_pipeline_bulk_repository_scoping.py` | tenant isolation SQL |
| `test_pipeline_bulk_fingerprint.py` | canonical SHA-256 |
| `test_pipeline_bulk_api.py` | HTTP, 0034 schema, concurrent create |
| `test_pipeline_bulk_queue.py` | worker semantics, ConflictError, forged tenant |
| `test_pipeline_bulk_recovery.py` | unpublished / stale / ceiling |
| `test_pipeline_bulk_fencing.py` | Txn B vs reclaim / cancel |
| `test_pipeline_bulk_retry_exhaustion.py` | retries 0/1/2/3 and stale lease |

Helpers (not in the plan’s file list; justified):
`pipeline_bulk_harness.py`, `pipeline_bulk_live.py`.

Stage 7 / 8 / optimization / RuleApplication / Shopify publish tests ran as
part of full pytest and passed. Stage 8 POST preview remains intentionally
non-idempotent.

Static (backend):

- `ruff check .` — pass
- `ruff format --check .` — 456 files already formatted
- `mypy app` — Success, 235 source files, no type-check loosening

Full pytest (isolated `droppilot_test` on `127.0.0.1:5499`):

**3363 passed / 1 skipped / 1 failed / 474.39s**

The sole failure is environment-only:

`tests/integration/test_ebay_c0_security.py::TestNoGeneratedOrSecretFiles::test_no_env_file_was_added_to_the_repository`

because a local gitignored `.env` exists. The file was **not** deleted, **not**
inspected, and the test was **not** weakened.

`python scripts/check_secrets.py` — PASS (858 tracked files).

---

## 16. Narrow extra files (justified)

| Path | Why |
|---|---|
| `app/core/exceptions.py` | `PipelineBulkRunActiveError` (`code=pipeline_bulk_run_active`) |
| `app/services/data_subject_erasure.py` | `pipeline_bulk_runs.requested_by_user_id` in `USER_REFERENCES` (clear) |
| `tests/integration/test_backup_restore_drill.py` | live Alembic head pin `0033` → `0034` |
| `tests/unit/test_stage_6_protected.py` | Playwright CI head pin pairing |
| `.github/workflows/ci.yml` | celery-broker harness **and** Playwright expected head `0034` |
| `scripts/r7_provision_stack.py` | provision script still asserted head `0033` |

`product_pipeline.py`, `product_optimization.py`, `prompt.py`,
`BaseRepository`, `BaseTask`, Stage 8 schemas/endpoints, and `frontend/**`
were not behaviourally modified.

---

## 17. Known LOW residuals

| Id | Residual | Why accepted |
|---|---|---|
| L6 | `enqueued_at` is not set on the happy after_commit publish path | Accepted in the plan. Age + `pending` is the unpublished signal; reconciler writes `enqueued_at` on the recovery path |
| L7 | `store_id` FK is `stores.id`, not composite `(tenant_id, store_id)` | Plan required tenant-scoped lookup before persist. API/service cannot attach a foreign store. Composite store unique pair was not in the 0034 contract |
| L8 | `store_not_found` leaves remaining items `pending` on a `failed` run | Plan: stop remaining generation; do not mark the current product missing. Remaining items are not executed because the run is terminal |
| L9 | Local ebay C0 `.env` failure | Environment-only; CI checkout has no `.env` |

No BLOCKER / HIGH / MEDIUM residuals remain after self-review.

---

## 18. Boundaries

- Frontend: **NO**
- Stage 10: **NO**
- Stage 11: **NO**
- Deployment: **NO**
- `origin/main`: unchanged (`3ce66d488e94ad3805fe24903deda99691c234a6`)
- Merge of this implementation: **NO** (awaiting independent review)
