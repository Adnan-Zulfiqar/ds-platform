# Phase 9 Stage 9 — Celery bulk optimisation: implementation plan

Planning only. This document is the Stage 9 contract. It does not
implement tasks, endpoints, models, or migrations.

Written after inspecting the repository at the frozen baseline. Nothing
below is assumed from memory where the code could answer.

---

## 1. Baseline identity

| | |
|---|---|
| Phase | 9 — AI product optimization |
| Stage | 9 — Celery: bulk optimisation, progress tracking, retries |
| Mode | Planning / discovery only |
| Branch | `docs/phase-9-stage-9-plan` |
| Baseline | `develop` @ `91a7069e73957822550ac47dc14fab0c30d1608a` |
| `origin/develop` | `91a7069e73957822550ac47dc14fab0c30d1608a` |
| `origin/main` | `3ce66d488e94ad3805fe24903deda99691c234a6` — **must not move** |
| Production | UNDEPLOYED |
| Stage 7 | FULLY CLOSED — merge `ffa0d6e37db218c85a1facdc265087553c694e02` |
| Stage 8 implementation | PR [#22](https://github.com/Adnan-Zulfiqar/ds-platform/pull/22) head `f88c34c89ad030e1fe11b1ac44aaf0965cd65ae6` |
| Stage 8 merge | `91a7069e73957822550ac47dc14fab0c30d1608a` |
| Stage 8 post-merge CI | run `35529859035` — 10/10 SUCCESS |
| First plan head | `f6baa9a443dd3a3b2d87f54ea61c20f2f10ddc5c` (PR [#23](https://github.com/Adnan-Zulfiqar/ds-platform/pull/23)) |
| First plan CI | run `35533340688` — 10/10 SUCCESS |
| Independent review | BLOCKER 0, HIGH 3, MEDIUM 2 — remediations in this document |
| Alembic head | **0033** (`0033_product_image_analysis.py`) |
| Migration this planning task | **NOT APPLIED** (authorized for implementation; see §10) |
| Frontend this stage | **NO** |
| Deployment this stage | **NO** |
| Stage 10 / 11 | **NOT STARTED** |

Master-plan wording (`docs/PHASE_9_PLAN.md` §3):

> 9 | Celery | Bulk optimisation, progress tracking, retries

Stage 8 already owns single-product HTTP preview / get-preview /
approve / publish. Stage 9 is additive orchestration around
`ProductPipelineService.preview`. It must not weaken Stage 7 or 8.

CLAUDE RETURN REVIEW CHECKPOINT:
All commits from Stage 5 takeover onward require a fresh Claude
end-to-end review when Claude becomes available again.

---

## 2. Stage 9 objective

Give a tenant-admin a way to ask the platform to generate **inactive
pipeline preview candidates** for many products, then watch that work
progress after the HTTP request has returned.

Stage 9 must:

- start a durable bulk run from HTTP
- execute it on Celery under at-least-once delivery
- track progress in PostgreSQL, not Redis/Celery results
- retry transient infrastructure/provider failure
- record permanent item failures without spinning BaseTask retries
- recover from unpublished-pending and abandoned-running
- isolate tenants even when the worker message is stale or forged
- leave merchant review intact: preview → inspect → approve exact
  candidate → publish exact approved candidate

Stage 9 must **not**:

- approve, activate, or publish candidates
- call `ProductOptimizationService.optimize_product`
- change Stage 8 single-product preview semantics
- use Celery `AsyncResult` as merchant-facing truth
- put product content, credentials, or provider output on RabbitMQ
- add AI Product Studio UI
- deploy, tag Phase 9, or touch `origin/main`

---

## 3. Repository discovery

Inspected before writing this plan.

| Area | Where it lives | Finding that shapes Stage 9 |
|---|---|---|
| Celery app | `backend/app/workers/celery_app.py` | `task_acks_late=True`, `task_reject_on_worker_lost=True`, `worker_prefetch_multiplier=1`, `task_track_started=True`, `result_expires=3600`, JSON only, explicit `imports`, beat schedule |
| BaseTask | `backend/app/workers/base.py` | `autoretry_for=(Exception,)`, `max_retries=3`, exponential backoff, jitter, cap 600s; `enqueue()` attaches `_context` |
| Context | `celery_app` prerun/postrun | Binds `RequestContext` from kwargs; **always** `clear_context()` after. Worker threads reuse; a leak is a cross-tenant bug |
| Queues | `task_routes` | Only `integrations.*` and `shopify.*` → `integrations`. Everything else, including `pricing.apply_rules_to_drafts`, uses `default` |
| Worker consumption | `docker/worker.Dockerfile` CMD | `celery … worker --loglevel=info` — **no `--queues`**. Celery therefore consumes **only** `task_default_queue` (`default`) |
| CI worker | `.github/workflows/ci.yml` | `--queues=default,integrations` |
| Broker smoke | `backend/scripts/verify_celery_broker.py` | Real RabbitMQ; registration + `send_task`; does **not** currently list any AI task |
| Pricing bulk | `RuleApplication` + `app/tasks/pricing.py` | Mature durable-run pattern. Pricing-domain tables. **Do not reuse** |
| Stage 7 preview | `ProductPipelineService.preview` | Analyse + `generate_candidate`. **Does not commit.** **Not idempotent.** Does not activate. Does not set `Product.ai_status=FAILED` |
| Stage 8 HTTP | `POST /products/{id}/pipeline/preview` | `RequireAdmin`, 201, intentionally non-idempotent per successful call |
| AI errors | `app/ai/exceptions.py` | `AIError.retryable` default `False`; `AIProviderNotConfiguredError.retryable=False`. No timeout/rate-limit subclass exists yet |
| Product AI status | `ProductAIStatus` | `not_optimized` / `optimized` / `failed` only. Comment says Stage 9 would need in-flight; see §8 |
| Alembic | `0033` | Head. Next revision if schema is added: **0034** |
| Frontend | `frontend/**` | Out of scope |
| Live AI | `AISettings.provider` default `stub` | No live provider configured or verified |
| Composite FK precedent | migration `0024` + `pricing.py` / `shipping.py` | `UNIQUE (tenant_id, id)` exists solely as an FK target. `id` is already globally unique. Stage 9 copies this for `products` and `product_versions` |
| Product / version PKs | `products.id`, `product_versions.id` | **No** `UNIQUE (tenant_id, id)` today. Composite FKs from bulk items are impossible until 0034 adds those pairs |
| Stage 8 tone | `PipelinePreviewRequest.tone` | `min_length=1`, `max_length=64`. Bulk start must match |

---

## 4. Existing Celery guarantees

Verified from `celery_app.py`, `base.py`, `config.py` (`CelerySettings`),
Compose, and CI — not from memory.

| Setting | Value | Implication for Stage 9 |
|---|---|---|
| Broker | RabbitMQ (`CELERY_BROKER_URL`) | Durable queues. Delivery is not atomic with DB commits |
| Result backend | Redis DB 3 | Transient. `result_expires=3600` (one hour) |
| `task_acks_late` | `True` (default) | ACK after the task returns. Worker crash → redelivery |
| `task_reject_on_worker_lost` | `True` | Lost worker rejects rather than silently dropping |
| `worker_prefetch_multiplier` | `1` | One in-flight task per worker process |
| `worker_max_tasks_per_child` | `1000` | Process recycle; not a correctness control |
| Default soft / hard time limit | 300s / 360s | Too small for a multi-product AI run; Stage 9 must override on its process task |
| `task_track_started` | `True` | STARTED is a Redis result-backend signal. **Not** merchant progress |
| Serialiser | JSON only | No pickle. Payload must be JSON-safe ids |
| `BaseTask.autoretry_for` | `(Exception,)` | **Every uncaught exception retries**, including `ValidationError` and `NotFoundError`, unless the task catches them |
| `BaseTask.max_retries` | 3 | Four attempts total (initial + 3). No infinite retry |
| Backoff | `retry_backoff=True`, `retry_backoff_max=600`, `retry_jitter=True` | Reuse unless a task-level override is justified |
| `enqueue()` | attaches `_context` | Correlation + accidental tenant. **Not** run-ownership authority |
| `asyncio.run` + `dispose_engine` | `app/tasks/pricing.py` `_run` | Mandatory for async SQLAlchemy inside sync Celery. Second task otherwise dies with a closed-loop connection |
| Task registration | `celery_app.conf.imports` | A module that is not listed is an "unregistered task" at call time |

**Celery does not provide exactly-once execution.** Stage 9 must put
exactly-once *effect* on durable rows.

**Do not invent a new queue** unless Dockerfile CMD, Compose worker,
CI `--queues`, and any deploy worker command all consume it. Today
Compose workers do **not** consume `integrations`. Stage 9 tasks go on
`default`.

---

## 5. Existing RuleApplication precedent

Closest durable-run pattern in the repository. Pricing-domain. Copy
**guarantees**, not tables, not item vocabulary, not the 5,000-product
ceiling.

What Stage 9 **takes**:

| Guarantee | How RuleApplication does it | Stage 9 use |
|---|---|---|
| Durable run row | `rule_applications` | New `pipeline_bulk_runs` |
| Durable per-item outcome | `rule_application_items` | New `pipeline_bulk_run_items` |
| Idempotency key + fingerprint | unique `(tenant_id, idempotency_key)`; 409 on mismatch | Same contract |
| After-commit publish | SQLAlchemy `after_commit` + `enqueue` | Same handoff |
| Unpublished-pending recovery | beat every 5 min; `PENDING_GRACE=2 min` | Same idea, own task |
| Claim + lease fence | `claimed_by_task_id` is identity; `lease_token` is the fence | Same split |
| Heartbeat in the write transaction | `heartbeat_at` stamped with the batch | Stamp with each item commit |
| Recovery ceiling | `MAX_RECOVERIES=3` then park `failed` | Same bound |
| Tenant from the row | `RuleApplicationTenantLookup` — not a repository | Twin lookup class |
| Payload is one id | `application_id` only | `run_id` only |
| 202 on start | `POST /global-rules/drafts/apply` | 202 on start |
| Owner-conditional fail | lost lease writes nothing | Same |
| Partial success | `PARTIAL` is first-class | Same (different counts) |
| Never silently truncate | `MAX_APPLICATION_PRODUCTS` named in 422 | Own, lower, ceiling |

What Stage 9 **does not take**:

| Pricing detail | Why not |
|---|---|
| `MAX_APPLICATION_PRODUCTS = 5_000` | AI is provider-paid and slow. 5,000 previews in one run is a cost incident |
| `APPLICATION_BATCH_SIZE = 50` DB rows per transaction | One `preview()` is one candidate + provider HTTP. One-product transactions |
| Item outcomes `applied` / `needs_review` / `published` | Those are reprice semantics |
| Filter/`selection_filter` select-all | Stage 10 UX. Stage 9 is explicit ids |
| Embedding all items in the status GET | 50 is smaller than 5,000, but Stage 9 still paginates items so Stage 10 can grow the cap later |
| Viewer-readable status | Stage 8 GET preview is Admin-only. AI pipeline stays Admin |
| Repricing already-at-price skip | Not applicable |
| Rule version TOCTOU | No pricing rule. Product-edit policy is §30 |

`RuleApplicationTenantLookup` is deliberately **not** a
`TenantScopedRepository` and is not a third unscoped repository.
Stage 9's twin follows that exact shape: one method, returns a tenant
id, cannot load a row onto a request path.

---

## 6. Exact definition of bulk optimisation

**Bulk Stage 9 creates inactive pipeline PREVIEW CANDIDATES for many
products.**

For each selected product the worker, at most once per run item:

1. Calls `ProductPipelineService.preview(...)` (image analysis +
   `generate_candidate`).
2. Persists the exact `candidate_version_id` on the durable item row.
3. Leaves the `ProductVersion` **inactive**, pipeline-marked,
   unapproved, unpublished.

It does **not**:

- call `approve`
- call `activate_version`
- call `optimize_product`
- call `publish`
- create or update `StoreListing`
- overlay Shopify
- flip `Product.ai_status` to `optimized` or `failed`

Stage 7 plan §30 said Stage 9 "should call `preview` / `approve` /
`publish`" as **Stage 9's decision**. This plan decides **preview
only**.

Authoritative support:

- `docs/PHASE_9_PLAN.md` risk table: "preview before apply"
- Stage 8 contract: merchant agency is inspect → approve exact
  candidate → publish
- Stage 7 `generate_candidate` is the inactive pipeline path;
  `optimize_product` auto-activates unmarked rows and would skip
  review
- This prompt's Stage 9 semantic question, tested against those docs

A bulk click labelled "optimise these products" must not become
generate + approve + publish.

Synthetic `StubProvider` candidates remain `isSynthetic=true` /
`provider=stub` and remain non-publishable under Stage 7 fail-closed
publish.

---

## 7. Chosen orchestration architecture

**Selected: Architecture A — one run task processes products
sequentially, one product per DB transaction.**

Celery message: `ai.process_pipeline_bulk_run(run_id=...)`.

Worker:

1. Unscoped lookup of `tenant_id` from the run id.
2. Bind tenant context from that value (not from `_context`).
3. Claim `pending → running` with a fresh `lease_token` and this
   delivery's task id — or resume if this task id already owns a
   `running` run — or no-op if another owner holds a live lease.
4. Loop: next `pending` item, one `transaction()` per product,
   `preview` + item terminal write + counters + heartbeat, commit.
5. Finalize: `completed` / `partial` / `failed` / honour `cancelled`.
6. Always `clear_context()`.

### Why A, not B (fan-out per product)

| Concern | A | B (child per product) |
|---|---|---|
| Duplicate delivery | One message; item uniqueness + claim no-op | Coordinator retry republishes N children; survivable but noisier |
| Progress accounting | One owner updates counters | Many workers contend on the run row |
| Partial success | Natural | Natural, but finalize needs "last writer" logic |
| Retry ownership | One task retry resumes the cursor | Per-item retry budget is nicer; run-level retry is messier |
| Broker load | One message per run | One + N, and reconciler may re-fan |
| Result backend | Unused | Tempting to use group/chord; forbidden |
| Cancellation | Cooperative at item boundary | Must signal N children |
| Tenant isolation | Identical if payload is run id | Identical if careful |
| Recovery | One stale run | Stale run + stale items |
| Provider quotas | Sequential; naturally bounded | Parallel stampede across workers |
| Transaction size | One product | One product |
| Observability | One task id per run | N task ids |
| Operational simplicity | Matches proven `apply_rules_to_drafts` | New pattern |

B's main advantage is per-product time limits. A pays for that with a
**raised time limit on the run task** and a **hard cap of 50
products**. That is the cheaper correctness trade for the first bulk
AI path. Fan-out can be revisited if a live provider plus a higher cap
make sequential runs operationally intolerable. Not now.

### Why not C (group/chord)

Chord/group aggregation depends on the Redis result backend. Results
expire in one hour. A browser refresh after expiry would lose the
aggregate. Master-plan progress tracking cannot live there. C is
rejected.

---

## 8. Durable-run design

New model `PipelineBulkRun` on table `pipeline_bulk_runs`, inheriting
`TenantScopedBase`.

Conceptual columns:

| Column | Role |
|---|---|
| `id` | UUID PK. The only broker payload |
| `tenant_id` | Ownership. Authoritative |
| `idempotency_key` | Client retry key. Unique with tenant |
| `request_fingerprint` | SHA-256 hex (64 chars) of canonical payload |
| `status` | See §11 |
| `tone` | Run-level generation tone |
| `store_id` | Optional. Advisory readiness only. Nullable FK `stores.id` `ON DELETE RESTRICT`. Ownership is validated at create via tenant-scoped `StoreRepository`; see Store contract below |
| `requested_by_user_id` | Actor. `ON DELETE SET NULL` |
| `selection` | JSONB snapshot of submitted product ids (deduped, ordered) |
| `claimed_by_task_id` | Celery identity of the current attempt. Not the fence |
| `lease_token` | UUID fence. NULL when unowned |
| `started_at` | First successful claim |
| `heartbeat_at` | Last committed item (or claim) |
| `recovery_count` | Reclaims. Bounded |
| `enqueued_at` | Broker accepted a publish (reconciler-written; ordinary path may stay NULL if publish succeeded immediately — reconciler treats age + `pending` as unpublished) |
| `finished_at` | Terminal |
| `total_count` | Frozen at create. Never decreases. Never silently dropped |
| `processed_count` | Terminal items. Monotonic |
| `succeeded_count` | Items with a candidate |
| `failed_count` | Permanent item failures |
| `skipped_count` | Ineligible (archived, cancelled remainder if used) |
| `missing_count` | Unresolved / deleted / foreign-to-this-tenant ids |
| `failure_reason` | Safe, truncated, run-level only when the **run** fails |

No product titles, prompts, provider payloads, or credentials on this
row.

`Product.ai_status` is **not** an in-flight bulk flag. Stage 7's table
that assigned "in-flight `ProductAIStatus`" to Stage 9 is superseded:
pipeline preview must not look like legacy optimize, and
`generate_candidate` already refuses to write `FAILED`. Progress lives
on `PipelineBulkRun`. No new `ProductAIStatus` value in 0034.

Run-table uniqueness (database, not SELECT-then-INSERT):

| Constraint | Columns / predicate | Name |
|---|---|---|
| Idempotency | `UNIQUE (tenant_id, idempotency_key)` | `uq_pipeline_bulk_runs_tenant_idempotency` |
| Composite FK target | `UNIQUE (tenant_id, id)` | `uq_pipeline_bulk_runs_tenant_id_id` |
| **One active run per tenant** | partial unique index on `tenant_id` `WHERE status IN ('pending', 'running')` | `uq_pipeline_bulk_runs_tenant_active` |

The partial unique index is the cost bound. Two concurrent POSTs with
**different** idempotency keys cannot both insert `pending`. Application
pre-checks are allowed as a fast path but are **not** the invariant.

### Store contract (run-level)

`storeId` is optional. It does not cause publishing. It is a **run-level
resource**, not a per-product id.

At HTTP create, if `storeId` is supplied:

1. `StoreRepository.get_by_id_or_raise(store_id)` under the authenticated
   tenant (existing tenant-scoped repo).
2. Foreign or missing store → **404** `not_found`. Same envelope as
   Stage 8 foreign store. Do **not** create a run. Do **not** reveal
   whether the UUID exists in another tenant.
3. Persist only that own-tenant id.
4. Shopify connected / platform / readiness problems remain **advisory**
   at generation time, matching Stage 7 `preview` readiness. Create does
   not require a connected store.

Race: store valid at create, then soft-deleted or gone before/during
worker `preview`.

That is **not** product `missing`.

Pinned worker behaviour:

1. Classify via `NotFoundError.details["resource"]` (or an explicit
   store re-read before `preview`). `Store` → run-level
   `store_not_found`. `Product` → item `missing`. Never collapse them.
2. Roll back the current item's `preview` transaction (no candidate).
3. Stop remaining generation: the shared readiness destination is
   invalid.
4. Finalize the run `failed` with `failure_reason` / code
   `store_not_found`.
5. Prior **succeeded** items and their candidates **remain**.
6. The current item stays `pending` (rolled back) or is left `pending`;
   remaining pending items stay `pending`. None of them are marked
   `missing` because a store disappeared.

`store_id` FK: `ON DELETE RESTRICT` so a hard delete of a referenced
store cannot silently null the run. Soft-delete leaves the row; the
scoped read fails and takes the path above. No composite store FK:
`stores` has no `(tenant_id, id)` unique pair today, and Stage 9 does
not add one. Create-time scoped lookup is the tenant check. Accepted
LOW.

---

## 9. Durable item design

New model `PipelineBulkRunItem` on table `pipeline_bulk_run_items`,
inheriting `TenantScopedBase`.

Materialised **at run creation**, one row per deduped product id, all
`pending`. This is the snapshot that retries walk. A product created
after the run must not appear. A product deleted after the run keeps
its item row.

| Column | Role |
|---|---|
| `id` | UUID PK |
| `tenant_id` | Same tenant as the run (composite FK) |
| `run_id` | Parent |
| `product_id` | Nullable. SET NULL only on genuine hard delete (soft-delete leaves the id) |
| `submitted_product_id` | Always the id the merchant sent, even if `product_id` is later NULL |
| `state` | Item state machine §12 |
| `candidate_version_id` | Set iff `succeeded`. Exact preview this run produced |
| `error_code` | Stable machine code or NULL |
| `error_message` | Safe short message. Never traceback / secrets |
| `attempt_count` | Durable processing attempts **entered/claimed**. Integer, default 0. Not a guaranteed provider-call count. See §15 |
| `started_at` / `finished_at` | `started_at` set on first claim-attempt commit; `finished_at` on terminal item commit |

Unique: `(run_id, submitted_product_id)` named
`uq_pipeline_bulk_run_items_run_product` — one effect per product per
run.

`candidate_version_id` is what Stage 10 opens. Clients must not guess
from version history.

Succeeded item invariant (table CHECK `ck_pipeline_bulk_run_items_succeeded_version`):

- `state=succeeded` ⇒ `candidate_version_id IS NOT NULL`
- `candidate_version_id IS NOT NULL` ⇒ `state=succeeded`
- failed/missing/skipped/pending ⇒ `candidate_version_id IS NULL`

`candidate_version_id` FK: **`ON DELETE RESTRICT`**.

`ON DELETE SET NULL` would violate the CHECK. `ON DELETE CASCADE` would
delete bulk history. Soft-delete of `ProductVersion` does not fire the
FK (the row stays). A later hard delete of a version that a succeeded
item still names is refused by PostgreSQL until the item row is gone
(GDPR erasure of the run, or an explicit admin path that is not Stage
9). Bulk outcome remains historical for as long as the candidate row
exists. Stage 10 still reads current candidate status via Stage 8 GET
preview; a soft-deleted version is 404 there without rewriting this
run.

---

## 10. Migration decision and schema contract

**MIGRATION REQUIRED: YES**

No existing generic job table can represent AI bulk work.
`rule_applications` is a pricing event. `automation_runs` is the
automation runner. `prompt_executions` is a single prompt call.
`Product.ai_status` is a three-value legacy optimize cache.

Proposed revision:

| | |
|---|---|
| Revision | **0034** |
| File (implementation, not this PR) | `backend/alembic/versions/0034_pipeline_bulk_runs.py` |
| `down_revision` | `0033` |
| `upgrade` | see exact DDL below |
| `downgrade` | reverse of upgrade, including dropping the product/version unique pairs this revision adds. Must work |

This planning PR does **not** add 0034.

### 10.1 Target unique pairs (required for composite FKs)

PostgreSQL will not accept
`(tenant_id, product_id) → products(tenant_id, id)` unless
`products` has `UNIQUE (tenant_id, id)`. Same for `product_versions`.
Today both tables have PK `(id)` only.

**Strategy: retain DB-level tenant integrity.** Copy migration 0024.

0034 adds, before creating item FKs:

| Name | Table | Columns |
|---|---|---|
| `uq_products_tenant_id_id` | `products` | `(tenant_id, id)` |
| `uq_product_versions_tenant_id_id` | `product_versions` | `(tenant_id, id)` |

These pairs are redundant as identity (`id` is already globally unique).
They exist **solely** as referenced keys for composite FKs.

ORM metadata **must** match. Implementation is authorized to add the
matching `UniqueConstraint` entries in `backend/app/models/product.py`
(`Product` and `ProductVersion` `__table_args__` only).

Explicitly:

- **NO** column semantics change
- **NO** Stage 7/8 business behaviour change
- **NO** version-content change
- **NO** activation/publish change
- **NO** new columns on `products` or `product_versions`

Do not leave an impossible FK contract. Do not claim composite FKs
without these unique pairs.

### 10.2 Enums

Values, not names (`values_callable`). PostgreSQL types:

- `pipeline_bulk_run_status`: `pending`, `running`, `completed`,
  `partial`, `failed`, `cancelled`
- `pipeline_bulk_item_state`: `pending`, `succeeded`, `failed`,
  `skipped`, `missing`

### 10.3 `pipeline_bulk_runs` constraints

| Kind | Name | Definition |
|---|---|---|
| PK | (default) | `id` UUID |
| Tenant FK | (TenantScopedBase) | `tenant_id → tenants.id` `ON DELETE CASCADE` |
| Unique | `uq_pipeline_bulk_runs_tenant_idempotency` | `(tenant_id, idempotency_key)` |
| Unique | `uq_pipeline_bulk_runs_tenant_id_id` | `(tenant_id, id)` — item composite FK target |
| Partial unique index | `uq_pipeline_bulk_runs_tenant_active` | `UNIQUE (tenant_id) WHERE status IN ('pending', 'running')` |
| Store FK | `fk_pipeline_bulk_runs_store` | `store_id → stores.id` `ON DELETE RESTRICT`, nullable |
| Actor FK | | `requested_by_user_id → users.id` `ON DELETE SET NULL`, nullable |

Indexes (query-backed, not speculative):

| Name | Columns | Why |
|---|---|---|
| `ix_pipeline_bulk_runs_tenant_created` | `(tenant_id, created_at)` | list / poll recency |
| `ix_pipeline_bulk_runs_tenant_status` | `(tenant_id, status)` | active-run lookup; complements the partial unique |
| `ix_pipeline_bulk_runs_pending_created` | `(created_at)` `WHERE status = 'pending'` | unpublished-pending sweep |
| `ix_pipeline_bulk_runs_running_heartbeat` | `(heartbeat_at)` `WHERE status = 'running'` | stale-running sweep |

### 10.4 `pipeline_bulk_run_items` constraints

| Kind | Name | Definition |
|---|---|---|
| PK | (default) | `id` UUID |
| Tenant FK | (TenantScopedBase) | `tenant_id → tenants.id` `ON DELETE CASCADE` |
| Unique | `uq_pipeline_bulk_run_items_run_product` | `(run_id, submitted_product_id)` |
| Composite FK | `fk_pipeline_bulk_run_items_run_tenant` | `(tenant_id, run_id) → pipeline_bulk_runs(tenant_id, id)` `ON DELETE CASCADE` |
| Composite FK | `fk_pipeline_bulk_run_items_product_tenant` | `(tenant_id, product_id) → products(tenant_id, id)` `ON DELETE SET NULL`, `product_id` nullable |
| Composite FK | `fk_pipeline_bulk_run_items_version_tenant` | `(tenant_id, candidate_version_id) → product_versions(tenant_id, id)` **`ON DELETE RESTRICT`**, `candidate_version_id` nullable |
| CHECK | `ck_pipeline_bulk_run_items_succeeded_version` | `(state = 'succeeded' AND candidate_version_id IS NOT NULL) OR (state <> 'succeeded' AND candidate_version_id IS NULL)` |

Do **not** add `UNIQUE (tenant_id, id)` on items unless a later FK
needs it. Stage 9 does not.

Indexes:

| Name | Columns | Why |
|---|---|---|
| `ix_pipeline_bulk_run_items_tenant_run` | `(tenant_id, run_id)` | paginated item GET |
| `ix_pipeline_bulk_run_items_run_state` | `(run_id, state)` | worker "next pending", finalize counts |

### 10.5 Candidate delete vs CHECK

Soft-delete of a `ProductVersion` does not touch `candidate_version_id`.
Hard delete is `RESTRICT`ed while a succeeded item still points at it.
Never `SET NULL`. Never `CASCADE` the item. The CHECK and the FK then
coexist.

---

## 11. Run state machine

Statuses (values, not names — `values_callable` required):

| Status | Meaning |
|---|---|
| `pending` | Accepted, not yet claimed. Initial |
| `running` | A worker holds a live lease |
| `completed` | Every item terminal; `failed_count=0` and `missing_count=0`. `skipped_count` may be >0 |
| `partial` | Every item terminal; `succeeded_count>0` and (`failed_count>0` or `missing_count>0`) |
| `failed` | Infrastructure exhaustion, recovery ceiling, or every item terminal with `succeeded_count=0` and (`failed_count>0` or `missing_count>0`) |
| `cancelled` | Admin cancelled before or during work. Already-succeeded items keep candidates |

Transitions:

```
pending  --claim-->           running
pending  --cancel-->          cancelled
running  --finalize-->        completed | partial | failed
running  --cancel flag-->     cancelled  (cooperative, next item boundary)
running  --reclaim-->         pending    (unowned; recovery_count++)
running  --abandon-->         failed     (recovery_count >= MAX_RECOVERIES)
running  --retry same task--> running    (same task id: resume, new lease_token)
```

`completed` never means "every product succeeded" if any failed or
went missing. That is `partial` or `failed`.

Progress is monotonic: `processed_count` only increases; status does
not return from a terminal state to `pending`/`running` except the
explicit reclaim `running → pending` of an **abandoned** (not
terminal) run.

---

## 12. Item state machine

| State | When |
|---|---|
| `pending` | Materialised at create. Not yet attempted |
| `succeeded` | `preview` committed; `candidate_version_id` set |
| `failed` | Permanent domain/config error recorded |
| `skipped` | Ineligible at execution (archived). Cancelled remainder may be skipped with `error_code=cancelled` **or** left `pending` under a `cancelled` run — pin: **left `pending`**, matching RuleApplication (unprocessed stays unprocessed; run status tells the story) |
| `missing` | Id does not resolve in this tenant at execution (never existed, other tenant, or soft-deleted) |

No item-level `running` row that can be abandoned forever. Crash
mid-`preview` rolls back **txn B**; the item stays `pending`.
`attempt_count` from txn A remains. Redelivery retries that item.

No item-level `stale`. Product edits are not an item skip (see §30).
`stale_preview` remains an **approve-time** Stage 7 error.

---

## 13. Selection snapshot

**Explicit `productIds` only** in Stage 9.

No server-side filter/select-all. That is Stage 10, when a bulk UI
exists. RuleApplication needs filters because the calculator page
already has them; Stage 9 has no equivalent screen.

At create:

1. Reject empty list (after trim) — 422.
2. Reject `len(productIds) > MAX_PIPELINE_BULK_PRODUCTS` — 422 naming
   the cap. **Never truncate.**
3. Dedupe preserving first-seen order (`dict.fromkeys`).
4. Persist `selection.productIds` and one `pending` item per id.
5. `total_count =` that length. Frozen.

Unknown / foreign / deleted ids are **kept** in the snapshot. They
become `missing` at execution. The whole request is not 404'd on a
mixed list (that would enumerate which UUIDs exist). Foreign and
deleted look the same: `missing`.

Retries of the worker never re-expand the catalogue.

---

## 14. Idempotency-key / fingerprint contract

Start request **requires** `idempotencyKey` (string, 1–128, stripped).

Unique constraint: `(tenant_id, idempotency_key)` named
`uq_pipeline_bulk_runs_tenant_idempotency`.

Active-run constraint: partial unique index
`uq_pipeline_bulk_runs_tenant_active` on `tenant_id`
`WHERE status IN ('pending', 'running')`.

Fingerprint: SHA-256 hex of canonical JSON built from the **deduped**
id set (same set persisted in `selection.productIds`), then sorted for
order-independence:

```
{
  "productIds": [<sorted unique uuid strings>],
  "tone": <string>,
  "storeId": <uuid string or null>
}
```

Duplicate ids in the request do not change the fingerprint relative to
the same unique set. Selection storage keeps first-seen order;
fingerprint always sorts.

`tone` is schema-validated `min_length=1`, `max_length=64`, matching
`PipelinePreviewRequest`. Invalid tone is 422 and never inserts a run.

Create flush / `IntegrityError` handling is deterministic and does **not**
depend on inspecting which PostgreSQL constraint name fired:

1. Attempt insert + flush (run + items).
2. On `IntegrityError`:
   - **FIRST** re-read by `(tenant_id, idempotency_key)`.
   - If that key exists:
     - same fingerprint → return the original run (202)
     - different fingerprint → 409 `conflict`
   - If that key does **not** exist:
     - treat as the active-run unique index → 409
       `pipeline_bulk_run_active`

| Case | Result |
|---|---|
| Same key, same fingerprint | Return original run. Always **202**; body carries current status |
| Same key, different fingerprint | **409** `conflict` |
| Concurrent identical inserts (same key, same payload) | Exactly one row. Both callers resolve to it (step 2 above) |
| Empty key | 422 |
| Two different keys while one run is `pending`/`running` | Exactly one active row. One **202**, one **409** `pipeline_bulk_run_active` |
| After the first run is terminal | A second key **may** create a new run |

Do not `SELECT` then `INSERT` without both uniqueness constraints. Do
not weaken the one-active-run contract to avoid the partial index.

---

## 15. Transaction boundaries

HTTP start (API session, committed by `get_db_session`):

- if `storeId` supplied: scoped store lookup; missing/foreign → 404,
  no insert
- insert run `pending`
- insert all item rows `pending`
- unique-constraint flush (idempotency **and** active-run)
- register `after_commit` publisher
- return 202

Do **not** `enqueue` before commit.

Worker claim (own transaction):

- lock run
- `pending → running` or resume same task id
- mint `lease_token`
- set `claimed_by_task_id`, `started_at` / heartbeat
- commit

Worker item is **two** transactions. `attempt_count` is not incremented
inside the generation transaction (that increment would roll back with
a retryable failure and lie).

**Txn A — claim attempt (short, no provider I/O):**

1. Lock run; abort if lease lost or cancelled.
2. Lock next `pending` item (`FOR UPDATE`).
3. If already terminal → no-op (do not increment anything).
4. Increment `attempt_count`. Set `started_at` if null.
5. Heartbeat the run.
6. Commit. Item is still `pending`. No `ProductVersion`.

**Txn B — generate + terminal (one product):**

1. Re-validate run lease/fence. Abort if lost or cancelled.
2. Lock the same item. If already terminal → no-op counters.
3. Item must still be `pending`.
4. Call `ProductPipelineService.preview` on **this session**
   (no commit inside the service — verified).
5. On success: item `succeeded` + `candidate_version_id`; increment
   `succeeded_count` and `processed_count`; heartbeat.
6. On classified **permanent product** error: item
   `failed`/`missing`/`skipped` + safe error fields; increment the
   matching counter and `processed_count`; heartbeat.
7. On classified **run-level** `store_not_found`: roll back this txn
   (no candidate); then a separate lease-conditional fail of the run
   (see Store contract). Do not mark the item `missing`.
8. Commit.

If txn B rolls back: no `ProductVersion`, item still `pending`,
progress counters unchanged. `attempt_count` from txn A **remains**.

If the worker dies after txn A and before txn B: `attemptCount` may
include a claimed attempt that performed **no** provider call. That is
the defined meaning: **number of durable processing attempts
entered/claimed**, not a guaranteed provider-call count.

Retryable errors raised from txn B leave the item `pending` and let
BaseTask retry the run. The next loop/retry runs txn A again
(increments `attempt_count` again) then txn B.

Worker finalize (own transaction, lease-conditional):

- recompute counts from items (source of truth) rather than trusting
  counters alone
- set terminal status + `finished_at` + `lease_token=NULL`

Reconciler reclaim (own transaction):

- conditional `UPDATE` on observed `heartbeat_at` + `lease_token`
- `running → pending`, `lease_token=NULL`, `recovery_count+1`
- publish **after** that commit

---

## 16. Preview non-idempotency mitigation

`ProductPipelineService.preview` is intentionally not idempotent.
Stage 8 stays that way.

Exactly-once **effect** for Stage 9:

```
UNIQUE (run_id, submitted_product_id)
+ item row locked in the same transaction as generate_candidate
+ terminal state checked before preview
```

Redelivery after a successful commit sees `succeeded` +
`candidate_version_id` and does not call `preview`.

**Residual (accepted):** if the provider answered and the DB
transaction then rolled back or the process died before commit, retry
calls the provider again. That may cost another provider call. It must
not create a second durable candidate for that item. Documented, not
wished away.

No Stage 7 contract change is required: `preview` already does not
commit, and `generate_candidate` already writes an inactive marked
row on the caller's session. Implementation must **not** add a second
generation stack or quietly edit Stage 7 to "make Celery easier".

If implementation discovers `preview` cannot be reused without
changing Stage 7 behaviour: **STOP** and treat that as a contract
change requiring review. Predicted: not needed.

---

## 17. Tenant authority / context model

`enqueue()` still attaches `_context` (request id, user id, HTTP
tenant) for log correlation.

**Durable `PipelineBulkRun.tenant_id` is the only authority.**

Worker sequence:

1. Prerun may bind `_context` (existing global signal — do not remove
   it globally).
2. Stage 9 task body **immediately** reads `request_id` from context
   for logs, then `clear_context()`.
3. Unscoped `PipelineBulkRunTenantLookup.tenant_for(run_id)`.
4. If none: log `run_missing`, return. No retry.
5. `set_tenant_id(durable_tenant_id)`. Optionally restore
   `request_id` only.
6. Tenant-scoped claim / work.
7. `finally: clear_context()`.

A forged `_context.tenant_id` cannot change which catalogue is read.
A stale context cannot make tenant A's run write tenant B's rows.

Do not add `tenant_id` to the Celery payload.

`PipelineBulkRunTenantLookup` is **not** a repository. Same
justification as `RuleApplicationTenantLookup`. It is not a third
unscoped repository.

---

## 18. Broker payload

```json
{
  "run_id": "<uuid>",
  "_context": { "tenant_id": "...", "user_id": "...", "request_id": "..." }
}
```

`_context` is added by `enqueue`. The task ignores it for ownership.

Forbidden on the message: product lists, titles, descriptions, images,
candidate content, prompts, provider output, tokens, passwords,
Shopify credentials.

---

## 19. Queue / routing decision

**Queue: `default`.**

Reasons:

- `pricing.apply_rules_to_drafts` already runs there
- Compose worker CMD has **no** `--queues`, so it consumes only
  `default`
- A new `ai` queue would strand jobs in local Compose until Dockerfile
  + CI + deploy all change
- `integrations` is **not** consumed by Compose workers; routing there
  would hide a local-dev footgun
- `prefetch=1` already prevents one long AI run from hoarding a batch
  of other default-queue jobs on that worker; scale-out is more
  worker replicas, not a new queue

No `task_routes` entry required (default is `default`).

If a dedicated AI queue is wanted later, it is an infrastructure
change: Dockerfile CMD, Compose command, CI `--queues`, deploy
scripts, and a routing entry, shipped together. Not Stage 9.

---

## 20. Task registration

New module: `backend/app/tasks/ai.py`.

Not `products.py` (catalogue sync) and not `pricing.py` (repricing).

Must be added to `celery_app.conf.imports`.

| Task name | Role | Beat |
|---|---|---|
| `ai.process_pipeline_bulk_run` | Execute one run | no |
| `ai.reconcile_pipeline_bulk_runs` | Unpublished-pending + stale-running | yes, 5 minutes |

`base=BaseTask`, `bind=True`.

Process-task time limits (override): `soft_time_limit=4500`,
`time_limit=4800` (75 / 80 minutes). Sized for
`MAX_PIPELINE_BULK_PRODUCTS=50` × ~90s worst-case stub-unrelated
provider timeout (`AISettings.request_timeout_seconds=30` × three
Stage 4 prompts) plus image analysis. Live AI is **not** verified;
these limits are a crash ceiling, not a quality SLA.

Also add both names to `backend/scripts/verify_celery_broker.py`
`REQUIRED_TASKS`.

Copy `_run()` / `dispose_engine` from `app/tasks/pricing.py`. Do not
call `asyncio.run` without disposing the engine.

---

## 21. Retry classification

`BaseTask` retries every uncaught `Exception`. Stage 9 must **catch
permanent item failures inside the item loop** so they never propagate.

| Error | Item / run | Retry the Celery task? |
|---|---|---|
| `NotFoundError` whose resource is **Product** | item `missing` | no |
| `NotFoundError` whose resource is **Store** | **run-level** `store_not_found`: roll back current preview; stop remaining items; finalize `failed`; prior successes kept. **Not** item `missing` | no |
| Soft-deleted product | item `missing` | no |
| Archived / `UNAVAILABLE` | item `skipped`, `error_code=product_not_eligible` | no |
| `ValidationError` (malformed product / prompt vars / bound failure) | item `failed`, use domain `code` | no |
| `ConflictError` (unexpected on preview) | item `failed` | no |
| `AIProviderNotConfiguredError` | **run-level**: mark this item `failed` with `ai_provider_not_configured`; remaining `pending` items `failed` with the same code; finalize `failed`. Configuration will not heal mid-run | no |
| `AIError` with `retryable=False` (current default, including base `AIError`) | item `failed`, `error_code=ai_error` | no |
| `AIError` with `retryable=True` | none yet in tree; when added: **raise** so BaseTask retries the run; item stays `pending`; `attempt_count` already committed in txn A | yes |
| DB / `OperationalError` / disconnect | raise | yes |
| SoftTimeLimitExceeded / time limit | raise; lease still held until reclaim | yes, then exhaustion / reclaim |
| Unexpected `Exception` | raise (do **not** record as a quiet item failure) | yes |
| Missing run row | return no-op | no |

Catch `NotFoundError` by `details["resource"]` (see
`NotFoundError.for_resource`). Do **not** treat every `NotFoundError`
as a missing product.

`MissingPromptVariablesError` is a `ValidationError` — terminal item.

Do not globally swallow bugs as item failures. Exhaustion path logs
`task_failed_permanently` via BaseTask and lease-conditional
`fail()` on the run, **keeping committed item results**.

---

## 22. Retry budget

| Knob | Value |
|---|---|
| Task `max_retries` | 3 (BaseTask default). Do not override unless tests force a shorter path |
| Backoff / jitter / cap | inherit BaseTask (`retry_backoff_max=600`) |
| Run recoveries | `MAX_PIPELINE_BULK_RECOVERIES = 3` then park `failed` |
| Item Celery retries | not a separate budget; a task retry resumes pending items |
| Infinite retry | forbidden |

After Celery retries exhausted while still holding the lease: run
`failed` with a safe `failure_reason` (`Task exhausted retries`),
counts preserved.

During retry the run stays `running` (same task id resume) or returns
to `pending` if the reconciler reclaimed first. Merchant polling
reads DB, not STARTED.

---

## 23. Progress semantics

Merchant-facing counters (camelCase on the wire):

| Field | Rule |
|---|---|
| `totalCount` | Frozen at create |
| `processedCount` | Incremented **once**, in the same commit as the item's terminal state |
| `succeededCount` | `state=succeeded` |
| `failedCount` | `state=failed` |
| `skippedCount` | `state=skipped` |
| `missingCount` | `state=missing` |

No persisted `runningCount`. Derive in-flight as
`totalCount - processedCount` while `status=running`.

`processedCount` must never decrease, never exceed `totalCount`, never
double-count on redelivery.

Finalize **recomputes from item rows** so a bug in incremental
counters cannot lie on the terminal status.

Progress fraction: `processedCount / totalCount` (0 if total is 0,
which create already rejects).

---

## 24. Partial success semantics

Bulk AI must not fail the whole run because one product cannot
optimise.

Example: 48 succeeded, 2 failed → run **`partial`**. Stage 10 shows
48 candidate ids and 2 error rows.

| Terminal | Condition |
|---|---|
| `completed` | all processed; `failed=0`; `missing=0` |
| `partial` | all processed; `succeeded>0`; (`failed>0` or `missing>0`) |
| `failed` | all processed and `succeeded=0` and (`failed>0` or `missing>0`); **or** run-level abort (config, exhaustion, abandon) |
| `cancelled` | cancel won; committed successes remain |

98/100 with 2 permanent failures is **not** reported as 100 failed or
100 succeeded.

---

## 25. Crash / recovery strategy

Two recovery loops, one beat task, same 5-minute cadence as pricing
(rationale: a merchant watching a stuck bar needs minutes, not hours;
the sweep is one indexed query when healthy).

**Unpublished pending:** `status=pending` AND `created_at < now - 2
minutes`. Republish. Duplicate delivery is a claim no-op.

**Abandoned running:** `status=running` AND
`heartbeat_at < now - STALE_AFTER` (or NULL heartbeat aged from
`started_at`/`created_at`, same predicate shape as
`stale_running_predicate`). Conditional UPDATE on the observed
heartbeat **and** `lease_token`. Then publish after commit.

`STALE_AFTER = 20 minutes`. Above one-product worst case (~90s
generation + image fetch) by a large margin. Too-early reclaim is the
dangerous failure (two writers). Too-late reclaim is a delayed bar.

Do not use Celery STARTED as abandoned evidence.

Sweep cap: 50 rows per kind per beat, same as `_RECONCILE_LIMIT`, to
avoid a thundering herd after a worker outage.

---

## 26. Lease / fencing design

**Required**, because reclaim exists.

Copy the RuleApplication split:

- `claimed_by_task_id` — identity. A Celery retry of the **same**
  message reuses the task id and may resume.
- `lease_token` — fence. UUID minted on every successful claim,
  resume, and re-lease. Every item write is `UPDATE … WHERE
  lease_token = :held`.

A worker whose run was reclaimed and then wakes up:

- item transaction matches zero run rows
- writes nothing, including no `failed`, no heartbeat, no finalize
- returns `superseded`

Not sufficient as a fence: in-memory mutex, Redis lock, task id
alone.

---

## 27. Pending-publish reconciler

Yes.

| | |
|---|---|
| Task | `ai.reconcile_pipeline_bulk_runs` |
| Beat | 5 minutes, entry `ai-reconcile-pipeline-bulk-runs` |
| Pending grace | 2 minutes (`PENDING_GRACE`) |
| Stale threshold | 20 minutes |
| Recovery ceiling | 3 |
| Sweep limit | 50 pending + 50 stale |

Republish is duplicate-safe. After successful republish, set
`enqueued_at` (reconciler path only; after_commit publish cannot
write back, same as pricing).

If reclaim's publish fails, the row stays `pending` without
`enqueued_at` — the unpublished sweep's job.

---

## 28. Cancellation decision

**Included in Stage 9.**

Master plan does not name it. It is in scope because a 50-product AI
run can occupy a worker for tens of minutes and burn provider budget.
RuleApplication already taught the cooperative pattern. Omitting it
would leave merchants no way to stop a mistaken bulk start short of
waiting for the cap.

Rules:

- `POST …/cancel` is `RequireAdmin`
- `pending` → `cancelled` immediately; later claim no-ops
- `running` → set cancelled; worker stops at the **next item
  boundary**; in-flight `preview` for the current product is allowed
  to finish and commit (or roll back on error)
- terminal runs → 409, not rewritten as cancelled
- already-created `ProductVersion` rows are **never deleted**
- idempotent: cancelling an already-cancelled run returns it

No Celery `revoke`. Broker revoke races the worker and is not the
source of truth.

---

## 29. Multiple-runs / same-product policy

Two layers:

1. **Same request** (double click, retry): idempotency key.
2. **Two different keys while one run is `pending` or `running` for
   the tenant:** refuse with 409 `pipeline_bulk_run_active`. At most
   **one** active bulk pipeline run per tenant. Enforced by
   `uq_pipeline_bulk_runs_tenant_active`, not by a raceable
   SELECT-then-INSERT. Bounds cost before token-quota infrastructure
   exists.

Once a run is terminal, a new key may target the same products and
**will generate new independent candidates**. That matches Stage 8
preview (each successful call is a new version). Stage 10 can list
both.

No product-level advisory unique among active runs. Tenant-level
active-run uniqueness already prevents two in-flight previews of the
same product from this API. Single-product Stage 8 preview remains
allowed concurrently — it is a different entry point and stays
non-idempotent. Documented residual: an admin can still POST
single-product preview while a bulk run includes that product, creating
an extra candidate. Stage 9 will not lock Stage 8.

---

## 30. Product-edit-during-run policy

**Execute against live product state at item execution time.**

Do not snapshot `Product.updated_at` at submission to skip later.
Do not auto-regenerate in a loop because the merchant edited.

`generate_candidate` already writes `pipelineSourceUpdatedAt` from
current product state. That is what Stage 7 `stale_preview` compares
at **approve**. If the merchant edits after the bulk candidate exists,
approve fails `stale_preview` until they preview again. That
protection is unchanged.

If they edit while the item is still `pending`, the worker generates
from the new state. The candidate is fresh relative to execution, not
stale relative to an old snapshot. That is the intended behaviour.

---

## 31. Exact HTTP route table

Prefix: `/api/v1/products`. Static `/pipeline/runs` declared with
the other static paths (same ordering discipline as `/imports`).

| Method | Path | Status | Auth | Rate limit |
|---|---|---|---|---|
| POST | `/pipeline/runs` | **202** | `RequireAdmin` | `endpoint_rate_limit("pipeline-bulk-start", limit=10, window_seconds=60)` |
| GET | `/pipeline/runs/{run_id}` | 200 | `RequireAdmin` | none beyond global middleware |
| GET | `/pipeline/runs/{run_id}/items` | 200 | `RequireAdmin` | none beyond global |
| POST | `/pipeline/runs/{run_id}/cancel` | 200 | `RequireAdmin` | same named limiter as start is unnecessary; no extra |

Handlers: validate, authorize (dependency), delegate, project.
No SQL, no tenant filter, no enqueue in the handler body except
registering the existing after-commit hook via the service/task
module (same sideways import as `publish_rule_application`).

404 not 403 for foreign `run_id`.

Do not nest under `/{product_id}` — this is not a single-product
resource.

---

## 32. Request / response schemas

New schemas in `backend/app/schemas/product.py` (pipeline family)
or a dedicated `backend/app/schemas/pipeline_bulk.py` if
`product.py` would become unreadable. Prefer **`pipeline_bulk.py`**
so Stage 8 schemas stay untouched. `CamelCaseModel`, `extra=forbid`.

**POST body** `PipelineBulkRunCreateRequest`:

- `productIds: list[UUID]` (required, min 1 after validation)
- `idempotencyKey: str` (required, 1–128)
- `tone: str = "professional"` (`min_length=1`, `max_length=64`, same
  as `PipelinePreviewRequest`)
- `storeId: UUID | None = None` (if set: own-tenant store or 404; no run)

**GET run** `PipelineBulkRunRead` (summary, **no items array**):

- `id`, `status`, `idempotencyKey`, `tone`, `storeId`
- `heartbeatAt`, `recoveryCount`, `startedAt`, `finishedAt`, `createdAt`
- `totalCount`, `processedCount`, `succeededCount`, `failedCount`,
  `skippedCount`, `missingCount`
- `failureReason`

**GET items** `Page[PipelineBulkRunItemRead]` via existing
`ListQueryParams` / `Page` (`max_page_size` already 100).

`PipelineBulkRunItemRead`:

- `submittedProductId`
- `productId` (nullable)
- `state`
- `candidateVersionId` (nullable)
- `errorCode`, `errorMessage`
- `attemptCount`
- `finishedAt`

Do not embed full `PipelinePreviewResponse` in the item list. Stage 10
calls existing GET preview with `candidateVersionId`.

---

## 33. Auth matrix

`RequireAdmin` = ADMIN or OWNER (`require_minimum_role(RoleName.ADMIN)`).

| Action | Unauth | Member | Viewer | Admin | Owner |
|---|---|---|---|---|---|
| Start | 401 | 403 | 403 | 202 | 202 |
| Read run | 401 | 403 | 403 | 200 | 200 |
| Read items | 401 | 403 | 403 | 200 | 200 |
| Cancel | 401 | 403 | 403 | 200 | 200 |

**Why not Viewer-read** (unlike RuleApplication status):

Stage 8 GET pipeline preview is Admin-only. Bulk items exist to open
those previews. Opening AI pipeline status to Viewer in Stage 9 would
widen Stage 8's policy by a side door. Stage 10 may revisit.

---

## 34. Tenant-isolation matrix

| Probe | Expected |
|---|---|
| Foreign `run_id` GET | 404 |
| Foreign item via own run id | impossible (items scoped by run + tenant) |
| Foreign `productId` in start list | accepted as snapshot id; executes as `missing`; no candidate |
| Foreign / missing `storeId` on POST | **404**, no run created |
| Store deleted after run accepted | run `failed` `store_not_found`; prior successes kept; no item `missing` |
| Mixed own + foreign product ids | run starts; own products generate; foreign `missing`; no 404 that distinguishes them |
| Forged Celery `tenant_id` in `_context` | ignored; lookup from run row |
| Broker payload credentials | none |
| Viewer/Member start | 403 |
| Cross-tenant candidate_version_id on an item | prevented by composite FK `(tenant_id, candidate_version_id)` → `product_versions` |

---

## 35. Pagination / polling contract

- Summary GET is the poll target. Small. No item array.
- Stage 10 polls GET run. Fraction = `processedCount / totalCount`.
- Item page uses `ListQueryParams` (`page`, `size`, `sortBy` allowlist:
  `created_at`, `finished_at`, `state`).
- No WebSockets. No SSE. Not in the current architecture.

Default page size 25, max 100 — existing platform constants.

---

## 36. Rate limits / run-size limits

| Control | Value | Why |
|---|---|---|
| Max products per run | **`MAX_PIPELINE_BULK_PRODUCTS = 50`** | AI cost; sequential time budget; not pricing's 5,000. Named in 422. Never truncated. ~2× `max_page_size` so a UI can send two pages without a second run, still far under catalogue-wide reprice |
| Concurrent active runs / tenant | **1** (`pending`+`running`) | Cost bound without a token ledger |
| Start endpoint limiter | 10 / 60s / tenant+user | Tighter than pricing apply (20); each start can enqueue 50 generations |
| Provider token/cost quota | **does not exist** | Do not claim it. Residual: a determined admin can serialize 10×50 stub (or future live) generations per minute subject to the concurrent-run lock |
| Shopify store required | **no** | `storeId` optional, readiness advisory, no publish |

---

## 37. Failure-window matrix

| | Durable DB | Broker | Retry / recovery | Duplicate candidate risk | Merchant-visible |
|---|---|---|---|---|---|
| **A.** Run commits, publish fails | `pending`, items `pending` | no / lost message | reconciler republishes after 2 min | none (not started) | `pending` until claimed |
| **B.** Duplicate message | `running` or terminal | two deliveries | second claim no-op / resume same task id | none if item unique + lock | unchanged |
| **C.** Crash before claim | `pending` | redelivered (`acks_late`) | claim on second delivery | none | `pending` then `running` |
| **D.** Crash after claim, before first item | `running`, heartbeat ~ claim time | redelivered or lost | same-task resume **or** stale reclaim after 20 min | none | `running` until resume |
| **E.** Provider OK, DB rollback | item still `pending`; no version | task may retry | extra provider call; then commit once | **no durable duplicate** | `running`; item pending |
| **F.** Candidate+item commit, die before ACK | item `succeeded`, counters up | redelivered | item lock sees `succeeded`; no-op | **none** | progress already includes the item |
| **G.** Die between items | some succeeded, rest pending, `running` | redeliver / reclaim | resume pending items | none | partial progress, then continue |
| **H.** Lost lease, old worker wakes | new lease on new worker | old task still in process | old writes match 0 rows | none | new owner's progress |
| **I.** DB down on progress update | uncommitted item txn rolls back | task raises, BaseTask retries | item still pending | residual extra provider call | `running` or retries → `failed` |
| **J.** Provider not configured | items failed with `ai_provider_not_configured`; run `failed` | ACK after terminal | no 3× retry | none | `failed`, safe code |
| **K.** Store gone after accept | current preview rolled back; run `failed` `store_not_found`; prior successes kept | ACK after fail | no | none extra | `failed`; succeeded items still have candidates |
| **L.** Die after txn A (attempt++) before preview | item `pending`; `attempt_count>=1`; run `running` | redeliver / reclaim | txn A may increment again; then txn B | none | `running`; attemptCount may exceed provider calls |

---

## 38. Test matrix

### Model / migration

- 0034 upgrade + downgrade
- `uq_products_tenant_id_id` and `uq_product_versions_tenant_id_id`
- `uq_pipeline_bulk_runs_tenant_idempotency`
- `uq_pipeline_bulk_runs_tenant_id_id`
- **partial unique** `uq_pipeline_bulk_runs_tenant_active`
- unique `(run_id, submitted_product_id)`
- CHECK succeeded ↔ candidate_version_id
- composite FKs reject cross-tenant product / version / run pointers
- `candidate_version_id` `ON DELETE RESTRICT` (hard delete of a named
  version fails while the item exists)
- indexes in §10 exist
- enum `values_callable` (insert uses values not names)
- ORM `__table_args__` on `Product` / `ProductVersion` match 0034

### Run creation (HTTP)

- Admin/Owner 202
- Viewer/Member 403, unauth 401
- empty `productIds` 422
- 51 ids 422 naming 50
- tone `""` or >64 chars 422
- mixed foreign **product** ids accepted; later `missing`
- foreign `storeId` → 404, **no run row**
- own missing/deleted `storeId` → 404, no run
- duplicate key + same fingerprint returns original
- same key different tone/ids/store 409 `conflict`
- **A.** two simultaneous requests, same idempotency key, same payload
  → exactly one row; both resolve to that run
- **B.** two simultaneous requests, **different** idempotency keys
  → exactly one active run; one 202; one 409 `pipeline_bulk_run_active`
- **C.** after first run terminal, second key may create a new run
- snapshot frozen (new product matching nothing in the list is not added)

### Broker handoff

- publish registered only on `after_commit`
- simulated publish failure leaves recoverable `pending`
- reconciler republishes pending older than grace
- duplicate publication → claim no-op

### Tenancy

- worker tenant from row
- forged `_context.tenant_id` cannot retarget
- `clear_context` in `finally` (and after prerun overwrite)
- foreign run 404
- isolation test for new tenant-scoped repositories (compiled SQL)

### Task idempotency

- duplicate `apply` of the same run
- BaseTask retry after success commit: candidate count remains 1
- simulated ACK loss after commit: still 1 candidate
- unique item + lock proven

### Retries

- `retryable=True` AIError subclass (test double) retries; no extra
  candidate
- `attemptCount` increments on txn A even when txn B rolls back a
  retryable error; next retry increments again
- worker-death after txn A before preview: `attemptCount >= 1`, item
  still `pending`, later success does not duplicate the candidate
- `ValidationError` → item `failed`, task does not retry three times
- `AIProviderNotConfiguredError` → run `failed`, no three retries
- exhaustion → run `failed`, prior successes kept

### Progress

- `totalCount` stable
- `processedCount` +1 exactly once per item
- monotonic under redelivery
- 48/2 → `partial`
- all success → `completed`
- all missing → `failed` (`succeeded=0`)
- poll GET reads DB, not Redis

### Recovery

- stale heartbeat reclaimed; old lease cannot write
- recovery count 3 → parked `failed`
- unpublished pending republished
- no infinite loop

### Product lifecycle

- deleted after submit → `missing`; `totalCount` unchanged
- edited while queued → candidate from live state; Stage 7
  `stale_preview` still on approve if edited after generation
- two keys, one active → 409 (partial unique index)
- store deleted after accept → run `failed` `store_not_found`; no item
  marked `missing` solely because the store disappeared
- Stage 8 single preview still non-idempotent and unlocked

### Pipeline

- candidate `active=false`
- `candidateVersionId` stored
- no approve, no publish, no Shopify call
- StubProvider `isSynthetic`
- `Product.ai_status` unchanged by success or expected preview failure

### Real Celery (RabbitMQ + worker process)

Mock-only task tests and `send_task` of an **unknown** run id are **not**
sufficient. Stage 9 is the Celery stage.

Registration smoke (keep):

- both task names in `REQUIRED_TASKS`
- `send_task("ai.reconcile_pipeline_bulk_runs")` returns counts
- unknown `run_id` process no-ops

**Required durable pipeline harness** — new script
`backend/scripts/verify_pipeline_bulk_broker.py`, invoked from the
existing CI `celery-broker` job **after** migrations (including 0034)
and after the separately started real worker is pingable.

The harness must:

1. Use the job's isolated PostgreSQL (`droppilot_test`). No production
   DSN. No local `.env`.
2. Seed a tenant, an admin actor if the service needs `requested_by`,
   and **one** valid `Product` with the minimum fields StubProvider
   generation needs (reuse Stage 7/8 fixture shape: title, description).
3. Create a real `PipelineBulkRun` + pending item through
   `PipelineBulkRunService.create` (the same service POST will call).
   Commit.
4. Publish `ai.process_pipeline_bulk_run` with that real `run_id`
   through RabbitMQ (`send_task` / `enqueue` after commit — not
   `Task.run()`, not eager, not a direct Python call of `_apply`).
5. Let the **already running** Celery worker consume it.
6. Poll **PostgreSQL** (run row `status` / counts), **not**
   `AsyncResult`, until the run is terminal, with a bounded timeout.
7. Assert:
   - run left `pending`/`running` and became a success terminal
     (`completed` for a one-item success)
   - item `succeeded`
   - `candidateVersionId` stored
   - exactly one `ProductVersion` is that item's effect
   - `active=false`
   - pipeline-marked (`pipelineCandidateVersion=1`)
   - no approval, no `StoreListing`, no Shopify call
   - candidate `tenant_id` equals the durable run `tenant_id`
8. Deliver a **duplicate** real broker message for the **same** run id.
9. Assert after the worker handles it:
   - still exactly one candidate for that item effect
   - `succeededCount` unchanged
   - `processedCount` unchanged
   - same `candidateVersionId`
   - task no-ops safely (claim/terminal short-circuit)

That is the real at-least-once / duplicate-delivery acceptance.

**Worker-kill / `acks_late` redelivery of an in-flight process is not
executed in CI.** Killing a worker mid-`preview` is flaky on shared
runners. Honest split:

- Real broker + real worker prove: registration, consumption of
  `default`, durable run lookup, tenant rebind, worker DB access,
  StubProvider `preview` under the worker process, candidate + progress
  commit, `candidate_version_id` linkage, duplicate-delivery
  exactly-once effect.
- Lease / reclaim / stale-heartbeat / old-worker-write paths remain
  **DB/integration fencing tests** (same shape as RuleApplication
  fencing). Do **not** claim those as live crash-redelivery.

Uses StubProvider only. No live AI quality or live rate-limit claims.

### Regression

- all Stage 7 pipeline tests
- all Stage 8 API tests
- RuleApplication tests untouched and still passing
- existing `verify_celery_broker.py` names still registered
- worker context tests

---

## 39. Expected implementation files

Created (implementation stage, not this PR):

- `backend/app/models/pipeline_bulk.py`
- `backend/app/repositories/pipeline_bulk.py` (scoped repos +
  unscoped lookup class in that module or `global_rules`-style sibling)
- `backend/app/services/pipeline_bulk.py`
- `backend/app/schemas/pipeline_bulk.py`
- `backend/app/tasks/ai.py`
- `backend/alembic/versions/0034_pipeline_bulk_runs.py`
- `backend/tests/unit/test_pipeline_bulk_repository_scoping.py`
- `backend/tests/unit/test_pipeline_bulk_fingerprint.py`
- `backend/tests/integration/test_pipeline_bulk_api.py`
- `backend/tests/integration/test_pipeline_bulk_queue.py`
- `backend/tests/integration/test_pipeline_bulk_recovery.py`
- `backend/tests/integration/test_pipeline_bulk_fencing.py`
- `backend/scripts/verify_pipeline_bulk_broker.py`

Modified:

- `backend/app/models/__init__.py`
- `backend/app/models/product.py` — **`UniqueConstraint("tenant_id", "id", ...)` only** on `Product` and `ProductVersion`. No other model edits
- `backend/app/workers/celery_app.py` (`imports` + beat)
- `backend/app/api/v1/products/router.py` (four thin routes)
- `backend/app/api/v1/products/__init__.py` if needed
- `backend/scripts/verify_celery_broker.py` (`REQUIRED_TASKS`)
- `.github/workflows/ci.yml` — `celery-broker` job runs
  `verify_pipeline_bulk_broker.py` after the existing broker smoke,
  against the same isolated DB and already-started worker
- `docs/PHASE_9_PLAN.md`, `CHANGELOG.md`, `PROJECT_ROADMAP.md`,
  Stage 9 completion report (implementation closeout, not this PR)

Must **not** modify in Stage 9 implementation unless a STOP review
says otherwise:

- `product_pipeline.py` behaviour
- Stage 8 request/response semantics (tone bounds are copied, not
  changed)
- `frontend/**`
- `BaseTask` defaults globally
- RuleApplication tables
- `Product` / `ProductVersion` columns, defaults, or methods

---

## 40. Expected migration files

Implementation only:

`backend/alembic/versions/0034_pipeline_bulk_runs.py`

Creates the schema in §10 (enums, unique pairs on `products` /
`product_versions`, both bulk tables, partial unique active-run index,
composite FKs, CHECK, indexes). Working `downgrade()`. Never edit 0033.

This planning PR contains **no** migration.

---

## 41. Stage 7 / 8 compatibility guarantees

- `POST /products/{id}/pipeline/preview` remains non-idempotent, 201,
  Admin-only
- GET preview / approve / publish unchanged
- `expectedUpdatedAt` still required on approve/publish
- `stale_preview` still enforced at approve
- overlay still title + sanitized body; no AI SEO publish
- StubProvider still non-publishable
- `optimize_product` still auto-activates unmarked rows
- Alembic 0033 behaviour unchanged
- `Product` / `ProductVersion` **behaviour** unchanged. 0034 may add
  only `UNIQUE (tenant_id, id)` metadata matching pairs, as in 0024
- No silent Stage 7 extension. If one is discovered necessary: STOP

---

## 42. Stage 10 boundary

No `frontend/**` changes.

Stage 9 may ship APIs Stage 10 will poll. No React bulk picker, no
progress bar, no visual diff.

Stage 10 will:

- render run summary + paginated items
- open `GET …/pipeline/versions/{candidateVersionId}/preview`
- approve/publish through Stage 8 routes
- possibly add filter/select-all and manual "retry failed items"

---

## 43. Stage 11 boundary

No Phase 9 tag. No production deploy. `origin/main` stays
`3ce66d488e94ad3805fe24903deda99691c234a6`.

---

## 44. Risks / accepted LOWs

Self-review at the end of this document resolved BLOCKER / HIGH /
MEDIUM inside the plan.
Remaining LOWs:

| ID | Risk | Why accepted |
|---|---|---|
| L1 | Extra provider call after success-then-rollback | Unavoidable with non-atomic HTTP+DB. Exactly-once is on durable state |
| L2 | No AI token/cost ledger | Infrastructure does not exist. Hard cap 50 + **partial unique one-active-run** + start limiter are the Stage 9 controls. Full quota is later |
| L3 | Sequential run can occupy one worker up to ~75 minutes | prefetch=1; scale with replicas; cap 50. Fan-out deferred |
| L4 | Compose worker does not consume `integrations` | Pre-existing. Stage 9 avoids that queue |
| L5 | Stage 8 single-product preview can race a bulk item on the same product | Preserving Stage 8 non-idempotence is mandatory. Extra candidate is the same as clicking Preview twice |
| L6 | `enqueued_at` not set on the happy after_commit path | Same as RuleApplication. Age + `pending` is the unpublished signal |
| L7 | Live provider timeout/rate-limit classes do not exist | Cannot claim real provider retry behaviour. When those subclasses appear with `retryable=True`, classification in §21 already covers them |
| L8 | POST start always 202, including idempotent replay of a finished run | Matches RuleApplication. Clients read `status` in the body |
| L9 | Cancelled run leaves remaining items `pending` | Matches RuleApplication. Run `status=cancelled` is the explanation |
| L10 | No in-flight `ProductAIStatus` | Progress is the run row. Avoids lying on listing chips that mean legacy optimize |
| L11 | `attemptCount` can exceed actual provider calls | Honest: it counts claimed attempts. Documented. Better than a counter that rolls back |
| L12 | `UNIQUE (tenant_id, id)` on products/versions is redundant identity | Required as composite-FK targets. Same 0024 pattern. No behaviour change |
| L13 | `store_id` is not a composite tenant FK | `stores` has no `(tenant_id, id)` unique pair; Stage 9 does not add a third. Create uses scoped `StoreRepository`. Residual: only application code plus `ON DELETE RESTRICT` |
| L14 | Live worker-kill redelivery is not run in CI | Duplicate real-broker delivery is. Crash/reclaim is fencing-tested |

---

## 45. Implementation sequence

1. Migration 0034 (including product/version unique pairs) + models +
   `models/__init__.py` + `product.py` UniqueConstraint-only
2. Repositories (scoped + lookup) + isolation tests
3. Service: create / claim / txn A attempt / txn B preview / finalize /
   fail / cancel / reclaim / fingerprint / store validation
4. Tasks: `_run`, publish after commit, process, reconcile
5. `celery_app` imports + beat + `verify_celery_broker.py` names
6. Schemas + four HTTP routes
7. Integration tests (API, concurrent A/B/C, queue harness, fencing,
   recovery, attemptCount, store 404 / store-during-run)
8. `verify_pipeline_bulk_broker.py` + CI celery-broker step: real run
   through RabbitMQ + duplicate delivery
9. Docs: completion, CHANGELOG, roadmap, PHASE_9_PLAN
10. Quality gate. Stop. Do not open Stage 10

---

## 46. Acceptance gates

- ruff / format / mypy / pytest as in CLAUDE.md
- 0034 upgrade **and** downgrade
- Stage 7 + Stage 8 tests still pass
- RuleApplication tests still pass
- CI Celery job: tasks registered **and**
  `verify_pipeline_bulk_broker.py` passes (real durable run, real
  candidate under the worker, duplicate broker delivery, Postgres
  polling — not AsyncResult)
- No frontend diff
- `origin/main` unchanged
- No live-AI quality claims
- No claim of live worker-crash redelivery

---

## 47. Rollback strategy

- HTTP routes can be un-mounted; in-flight workers finish or reclaim
  to `failed` after recovery ceiling
- `downgrade()` of 0034 drops item/run tables and enums first, then
  drops `uq_products_tenant_id_id` and
  `uq_product_versions_tenant_id_id`. It does **not** delete
  `ProductVersion` candidates already created — those are catalogue
  history, not bulk-run rows
- Never `DELETE` products or versions as rollback
- Beat entry removed together with the task module

---

## 48. Claude return-review checkpoint

Cursor is the temporary planning agent. This document is not a
Claude review.

CLAUDE RETURN REVIEW CHECKPOINT:
All commits from Stage 5 takeover onward require a fresh Claude
end-to-end review when Claude becomes available again.

---

## Self-review (attack the design)

| Attack | Severity | Resolution |
|---|---|---|
| Duplicate `ProductVersion` after redelivery | BLOCKER | Item lock + unique `(run, submitted_product_id)` + `preview` in txn B with `succeeded` |
| Duplicate `ProductVersion` after duplicate broker delivery | BLOCKER | Same; real-broker harness step 8–9 |
| Candidate generated, item result missing | BLOCKER | Same transaction B; rollback drops both |
| Item success without candidate | BLOCKER | CHECK `ck_pipeline_bulk_run_items_succeeded_version` |
| Progress double-count | BLOCKER | Increment only `pending → terminal` under row lock in txn B |
| Redis result backend as truth | BLOCKER | Merchant API and broker harness poll Postgres only |
| Worker trusts forged `tenant_id` | BLOCKER | Payload has no tenant field used; lookup then rebind |
| Tenant context leak | BLOCKER | `clear_context` before bind and in `finally`; existing postrun kept |
| Queue not consumed | BLOCKER | `default` queue; Compose/CI already consume it |
| Task not registered | BLOCKER | `imports` + `REQUIRED_TASKS` + tests |
| Real worker never exercises pipeline | HIGH | `verify_pipeline_bulk_broker.py`: real run, real product, real `preview` under worker |
| Fake "broker verified" from Python `.run()` | HIGH | Harness uses `send_task` + separate worker; unknown-run smoke is extra, not enough |
| Concurrent different-key active-run race | HIGH | partial unique `uq_pipeline_bulk_runs_tenant_active`; test B |
| Same-key `IntegrityError` ambiguity | HIGH | Always re-read by key first; fingerprint; else `pipeline_bulk_run_active` |
| Impossible PostgreSQL composite FK | HIGH | 0034 adds `UNIQUE (tenant_id, id)` on products and product_versions first |
| ORM / migration schema drift | HIGH | authorized `product.py` UniqueConstraint-only matching 0034 |
| Candidate delete / CHECK contradiction | HIGH | `ON DELETE RESTRICT`, never SET NULL |
| Foreign/deleted store as product `missing` | HIGH | create-time 404; worker classifies `NotFoundError` by resource; run-level `store_not_found` |
| `attemptCount` rolls back with preview | HIGH | txn A commits the increment before provider I/O |
| Permanent 4xx retried three times | HIGH | Catch inside item loop; do not raise |
| Provider retry duplicates candidates | HIGH | Terminal check before `preview` |
| Stale worker writes after recovery | HIGH | `lease_token` fence on every write |
| Infinite recovery | HIGH | `MAX_PIPELINE_BULK_RECOVERIES=3` then `failed` |
| DB committed, never queued | HIGH | pending reconciler |
| Duplicate publication | MEDIUM | claim no-op |
| Two concurrent starts same key | HIGH | idempotency unique + IntegrityError path; test A |
| Same key different payload | HIGH | fingerprint 409 |
| Mixed foreign product ids | MEDIUM | snapshot + `missing`; no existence oracle |
| Huge broker payload | HIGH | `run_id` only |
| Huge status response | HIGH | summary vs paginated items |
| Auto approve / publish / AI SEO | BLOCKER | preview only; pin §6 |
| Stage 8 contract change | BLOCKER | forbidden; STOP if preview cannot be reused |
| Stage 10 frontend creep | BLOCKER | no `frontend/**` |
| Live AI claims | BLOCKER | StubProvider honesty |
| `integrations` queue stranding | HIGH | not used |
| Chord/group result expiry | HIGH | architecture A, no chord |
| Holding one 50-product DB transaction | HIGH | one product per txn B |
| Silent truncation | HIGH | 422 over cap |
| `optimize_product` in the worker | BLOCKER | forbidden; would auto-activate |
| Stage 7 modification for idempotency | HIGH | reuse `preview`; STOP if not possible |
| Third unscoped repository | MEDIUM | lookup class, not a repository |
| Viewer reads AI bulk | MEDIUM | Admin-only, matching Stage 8 GET preview |
| Cancel deletes versions | BLOCKER | never |
| `Product.ai_status=generating` migration surprise | MEDIUM | not added; run row is progress |

After resolution: **BLOCKER 0, HIGH 0, MEDIUM 0.** LOWs in §44.
