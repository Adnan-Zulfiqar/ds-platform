# Phase 9 Stage 8 — completion report

**API: thin HTTP adapter over Stage 7 `ProductPipelineService`.**

Status: **COMPLETE / UNMERGED / AWAITING INDEPENDENT REVIEW.**

Not merged. Production undeployed — `main` unchanged.
Stages 9–11 not started. Claude has not reviewed Stage 8. Cursor is the
temporary implementation + self-review agent; this report is not a Claude
review.

| | |
|---|---|
| Date | 2026-09-20 |
| Accepted plan | [PHASE_9_STAGE_8_PLAN.md](PHASE_9_STAGE_8_PLAN.md) |
| Plan PR | [#21](https://github.com/Adnan-Zulfiqar/ds-platform/pull/21) |
| Plan merge / baseline | `develop` @ `9a7c2900406cd1fbe3a6ca94f6615666deb95cf0` |
| Post-merge plan CI | [35518849907](https://github.com/Adnan-Zulfiqar/ds-platform/actions/runs/35518849907) — 10/10 SUCCESS |
| Implementation branch | `feat/phase-9-stage-8-api` |
| Contract | four `RequireAdmin` routes; required `expectedUpdatedAt` on approve/publish |
| Migration | **none** — Alembic head remains `0033` |
| Frontend | **none** |
| Celery | **none** |
| Deployment | **NO** |
| Stage 9 / 10 / 11 | **NOT STARTED** |
| AI involvement | `StubProvider` previews are synthetic (`isSynthetic=true`, `provider=stub`) and fail-closed for Shopify. Overlay publish HTTP tests use fixture `ai_provider="test"` / `isSynthetic=False` — not a live-model claim |

CLAUDE RETURN REVIEW CHECKPOINT:
All commits from Stage 5 takeover onward require a fresh Claude
end-to-end review when Claude becomes available again.

---

## 1. Exact endpoints

Prefix: `/api/v1/products`. Auth on all four: `RequireAdmin` (ADMIN or OWNER).

| Method | Path | Status | Request | Response | Service |
|---|---|---|---|---|---|
| POST | `/{product_id}/pipeline/preview` | 201 | `PipelinePreviewRequest` | `PipelinePreviewResponse` | `ProductPipelineService.preview` |
| GET | `/{product_id}/pipeline/versions/{version_id}/preview` | 200 | query `storeId?` | `PipelinePreviewResponse` | `get_preview` |
| POST | `/{product_id}/pipeline/versions/{version_id}/approve` | 200 | `PipelineApproveRequest` | `ProductDetailRead` | `approve` |
| POST | `/{product_id}/pipeline/versions/{version_id}/publish` | 200 | `PipelinePublishRequest` | `ShopifyPublishResponse` | `publish` |

Unauthorized → 401. Viewer/Member → 403. Foreign product/version/store → 404, not 403.

GET pipeline preview is intentionally Admin-only. It is not `RequireViewer`.

Routers validate, authorize, delegate, and project. No SQL, no tenant filter,
no lock, no overlay, no sanitization, no Shopify create in the router.
Transaction commit stays on `get_db_session`.

---

## 2. Exact schemas

Added in `backend/app/schemas/product.py` (`CamelCaseModel`, `extra=forbid`).

**Requests**

- `PipelinePreviewRequest`: `tone` (default `"professional"`), optional `storeId`
- `PipelineApproveRequest`: **required** `expectedUpdatedAt` (not Optional, no default)
- `PipelinePublishRequest`: **required** `storeId` and `expectedUpdatedAt`

Not reused: `ProductUpdateRequest`, `ShopifyPublishRequest` (optional tokens).

**Preview response** (`PipelinePreviewResponse`): `productId`,
`candidateVersionId`, `candidateVersionNumber`, `candidateActive`,
`sourceUpdatedAt`, `approvalExpectedUpdatedAt`, `original`, `proposal`,
quality fields, `imageAnalysis`, `isSynthetic`, `provider`,
`channelReadiness`, `pipelineBlockers`, `pipelineWarnings`, `publishable`.

Listing views (`PipelineListingViewRead`): title, description, seoTitle,
seoDescription, keywords, tags. Proposal SEO/keywords are preview-only.

Channel readiness reuses `ShopifyPublishReadinessResponse`. Publish reuses
`ShopifyPublishResponse`. No `PipelinePublishResponse`. No raw dict.

Projection lives in `products/router.py` (`_pipeline_preview_to_response`,
`_shopify_publish_response`) so `schemas.product` never imports
`ProductPipelineService`.

---

## 3. T0 → T1 lifecycle

1. `POST .../pipeline/preview` → `T0 = approvalExpectedUpdatedAt`
   (`Product.updated_at` at preview time).
2. `POST .../approve` with `expectedUpdatedAt=T0` → 200; first approval
   activates the candidate and bumps `Product.updated_at` to **T1**.
3. First publish must send **T1** (approve response `updatedAt`, later GET
   product, or GET preview after approval). Pre-approval T0 is stale.
4. Publish with T0 after successful first approval → 409 `draft_version_stale`.

Missing / null / malformed `expectedUpdatedAt` → 422. Do not preserve T0
as `Product.updatedAt` after approval.

Shared-transaction integration tests freeze Postgres `now()`, so they
advance `Product.updated_at` after approve the same way Stage 7 did, then
read T1 from GET product / retry approve. Production commits see a real
clock bump.

---

## 4. Lost-response retry

Approve candidate with T0 → success → product is T1. Client discards the
body. Same exact **active** candidate with old T0 → **200 no-op**; response
`updatedAt` is current T1. Publish with that T1 reaches the existing
Shopify publisher.

The router does not compare tokens. Idempotent retry is Stage 7
`approve` behaviour, exposed over HTTP.

---

## 5. Nullable image-analysis contract (M1)

When Stage 7 reconstructs `ProductImage.analysis IS NULL` (or empty `{}`):

```json
{ "status": "unknown", "errorCode": null, "analysis": null }
```

`PipelineImageAnalysisItemRead.analysis` is
`PipelineImageAnalysisEvidenceRead | None`. Empty evidence is not fabricated.
Non-empty evidence is `model_validate`'d (typed blur / duplicates /
watermark). Failure payloads may null `contentSha256`, dimensions, checks,
proposals, provider fields. Malformed **non-empty** stored evidence is 500
`internal_error` through the existing Pydantic handler — not silently
rewritten. Persistence of `ProductImage.analysis` is unchanged.

GET preview does not analyse, fetch, generate, or insert
`ProductVersion` / `PromptExecution`.

---

## 6. Tenant isolation

Stage 7 services + tenant-scoped repositories only. Foreign product,
foreign version, own-tenant version on a different product, foreign store
on POST preview / GET preview / publish: **404** with `not_found`. No
router-side tenant SQL. No 403 that would confirm a foreign identifier.

Publish of an inactive candidate is 422 `candidate_not_approved` **before**
the store lookup (Stage 7 precedence). Foreign-store 404 on publish is
asserted on an otherwise valid approved non-synthetic candidate.

---

## 7. Publish overlay boundary

Existing `ShopifySyncService.publish_product(..., listing_overlay=)` only.
No second publisher. No call to merchant `POST /integrations/shopify/publish`.

Shopify receives approved AI **title** and **sanitized `body_html`**.
Merchant live fields remain: SEO title/description, tags, images, alt text,
variants, pricing, SKU, vendor, handle, product type, inventory.

AI candidate `seoTitle` / `seoDescription` / `keywords` stay preview-only.

Synthetic / `provider=stub` / missing provider remain 422. Shopify
failure does not deactivate the candidate.

Legacy `POST /products/{id}/optimize` still auto-activates unmarked rows.
Legacy `POST .../versions/{id}/activate` still refuses pipeline candidates
(`pipeline_candidate_requires_approval`).

---

## 8. Boundaries kept

| Guard | Result |
|---|---|
| Migration | none |
| Alembic head | **0033** (`alembic heads`) |
| Models / repositories | unchanged |
| `product_pipeline.py` / `product_optimization.py` / `ShopifySyncService` | unchanged |
| Frontend | none |
| Celery / bulk / polling | none |
| Stage 9 / 10 / 11 | not started |
| `origin/main` | `3ce66d488e94ad3805fe24903deda99691c234a6` unchanged |
| Deploy | no |

---

## 9. Test evidence

Isolated Postgres 17 `droppilot_test` at `127.0.0.1:5499` (container
`droppilot-stage5-testpg`). Not the shared developer database on 5432.

| Gate | Result |
|---|---|
| `tests/integration/test_product_pipeline_api.py` | **27 passed** (17.35 s) |
| Stage 7 `test_product_pipeline.py` + `_publish.py` + `_activation_lock.py` | **44 passed** |
| Optimization + publish-readiness + Shopify idempotency/lock-order/HTTP concurrency | **68 passed** |
| `ruff check .` | pass |
| `ruff format --check .` | pass (440 files) |
| `mypy app` | Success: no issues found in 230 source files |
| full `pytest` | **3312 passed**, **1 skipped**, **1 failed**, 297 warnings, 467.46 s |
| `python scripts/check_secrets.py` | PASS — 855 tracked files |
| frontend | no diff; npm not run |

The one full-suite failure is
`tests/integration/test_ebay_c0_security.py::TestNoGeneratedOrSecretFiles::test_no_env_file_was_added_to_the_repository`
— local gitignored `.env` present on this workstation. The file was not
inspected, deleted, or weakened. CI checkouts without that file are the
authority for a clean run. Same environment-only failure recorded on
earlier Phase 9 stages.

No live AI model was called.

---

## 10. Self-review

Attacked after green tests. BLOCKER / HIGH / MEDIUM were fixed in the
test matrix before this report (foreign-store publish asserted after
approve; tenant context re-bound after HTTP; Shopify failure asserted
via existing `ShopifyTimeoutError` → 503 rather than an unhandled
`RuntimeError` TaskGroup leak).

| Severity | Count |
|---|---|
| BLOCKER | 0 |
| HIGH | 0 |
| MEDIUM | 0 |
| LOW | 8 (justified) |

| ID | Residual | Why accepted |
|---|---|---|
| L1 | POST preview with a foreign `storeId` still generates, then 404s and rolls back | Stage 7 method order; reordering would duplicate store lookup in the router |
| L2 | GET preview is Admin-only | Accepted plan; `/ai/prompts` read policy |
| L7 | Duplicate `ShopifyPublishResponse` mapping vs integrations router | Plan allowed a local projector; both mappings stay field-identical |
| L10 | Claude has not reviewed Stage 5–8 | Checkpoint below; this is not a Claude review |
| L13 | Malformed non-empty `ProductImage.analysis` → 500 | Honest stored-corruption signal; NULL/empty → `analysis: null` |
| L14 | `extra=forbid` on evidence | Fail-closed if Stage 6 later adds a key |
| L15 | Shared-txn tests advance `updated_at` to observe T1 | Same Postgres `now()` freeze as Stage 7 / M2A |
| L16 | Unhandled `RuntimeError` from the fake Shopify client can escape TestClient as `ExceptionGroup` | Existing publisher behaviour; HTTP test uses `ShopifyTimeoutError` (AppError → 503) |

---

## 11. What was not done

Stage 9 Celery / bulk / progress. Stage 10 AI Product Studio. Stage 11
closeout tag. Deployment. `main`. Live provider keys. A second Shopify
publisher. AI SEO overlay. Schema/service circular import.

STOP.
