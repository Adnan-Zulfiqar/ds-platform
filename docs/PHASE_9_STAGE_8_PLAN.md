# Phase 9 Stage 8 — API: implementation plan

Planning only. This document is the Stage 8 HTTP contract. It does not
implement endpoints.

Written after inspecting the repository at the frozen baseline. Nothing
below is assumed from memory where the code could answer.

---

## 1. Baseline identity

| | |
|---|---|
| Phase | 9 — AI product optimization |
| Stage | 8 — API |
| Mode | Planning / discovery only |
| Branch | `docs/phase-9-stage-8-plan` (new; not reused) |
| Baseline | `develop` @ `52d6046e55cc7eebb1d5d5b887a39151f173fee4` |
| `origin/develop` | `52d6046e55cc7eebb1d5d5b887a39151f173fee4` |
| `origin/main` | `3ce66d488e94ad3805fe24903deda99691c234a6` — **must not move** |
| Production | UNDEPLOYED |
| Stage 7 implementation | PR [#19](https://github.com/Adnan-Zulfiqar/ds-platform/pull/19) merge `ffa0d6e37db218c85a1facdc265087553c694e02` |
| Stage 7 docs closeout | PR [#20](https://github.com/Adnan-Zulfiqar/ds-platform/pull/20) merge `52d6046e55cc7eebb1d5d5b887a39151f173fee4` |
| Latest post-merge CI | run `35510344949` on `52d6046e` — 10/10 SUCCESS |
| Alembic head | **0033** (`0033_product_image_analysis.py`) |
| Migration this stage | **NO** |
| Frontend this stage | **NO** |
| Celery this stage | **NO** |
| Deployment this stage | **NO** |
| Stages 9–11 | **NOT STARTED** |

Master-plan wording for this stage (`docs/PHASE_9_PLAN.md` §3):

> 8 | API | Endpoints on the existing auth and tenant middleware

Stage 7 already owns the service. Stage 8 is a thin HTTP adapter.

CLAUDE RETURN REVIEW CHECKPOINT:
All commits from Stage 5 takeover onward require a fresh Claude
end-to-end review when Claude becomes available again.

---

## 2. Stage 8 objective

Expose the approved Stage 7 pipeline over HTTP, on the existing FastAPI
auth, tenant, session, and error-envelope stack:

| HTTP intent | Service method |
|---|---|
| Generate a pipeline preview | `ProductPipelineService.preview` |
| Read an existing exact candidate preview | `ProductPipelineService.get_preview` |
| Approve an exact candidate | `ProductPipelineService.approve` |
| Publish an already-approved exact candidate | `ProductPipelineService.publish` |

Stage 8 must **not**:

- duplicate Stage 7 business logic in routers
- auto-activate on preview
- publish from preview or approve
- replace `POST /products/{id}/optimize` or `POST /products/{id}/versions/{id}/activate`
- introduce a second Shopify publisher
- overlay AI SEO/tags/images/variants onto Shopify
- add bulk/Celery/progress endpoints (Stage 9)
- add AI Product Studio UI (Stage 10)
- change models, repositories, or Alembic

Routers stay: validate → authorize (dependency) → delegate → project
typed camelCase schemas. The global exception handlers already own
translation.

---

## 3. Repository discovery findings

Inspected before writing this plan. Authoritative sources:

| Area | Where it lives | Finding that shapes Stage 8 |
|---|---|---|
| Master plan | `docs/PHASE_9_PLAN.md` | Stage 8 is HTTP only; Stage 9 Celery; Stage 10 frontend |
| Stage 7 plan | `docs/PHASE_9_STAGE_7_PLAN.md` §30, §21, §19–20 | Preview POST / get-preview GET / approve POST / pipeline-publish POST wrapping `ProductPipelineService`. `RequireAdmin` on new routes. No HTTP in Stage 7. Session commit owned by HTTP. |
| Stage 7 completion | `docs/PHASE_9_STAGE_7_COMPLETION.md` | Service complete/merged; Alembic 0033; publish fail-closed on synthetic/`stub` |
| Pipeline service | `backend/app/services/product_pipeline.py` | DTOs + four methods as specified in the Stage 8 mission |
| Optimization | `backend/app/services/product_optimization.py` | `generate_candidate` inactive+marked; `parse_pipeline_candidate_metadata` fail-closed; legacy `optimize_product` auto-activates unmarked; `activate_version` refuses pipeline rows |
| Image analysis | `backend/app/services/image_analysis.py` | No HTTP today; per-image fetch failures stored, not product-level abort |
| Publish readiness | `backend/app/services/publish_readiness.py` | Shopify-only; foreign product/store → 404 |
| Products router | `backend/app/api/v1/products/router.py` | Thin handlers; literals before `/{product_id}` for one-segment collisions; optimize 201 Admin; activate 200 Admin; versions GET Viewer |
| API mount | `backend/app/api/v1/router.py` + `settings.api_v1_prefix` | `/api/v1` + `/products` |
| Deps | `backend/app/api/deps.py` | `get_db_session` commit-on-success / rollback-on-exception; `RequireAdmin` = ADMIN **or OWNER**; `RequireViewer` = VIEWER+ |
| Error envelope | `backend/app/api/error_handlers.py` + `schemas/common.py` | One `ErrorResponse`: `code`, `message`, `details[]`, `requestId`. `AppError.status_code` / `AppError.code` pass through. Request-schema failures are 422 `validation_error`. |
| Product schemas | `backend/app/schemas/product.py` | `CamelCaseModel`, `extra=forbid`; `ProductOptimizeRequest.tone`; `ProductUpdateRequest.expected_updated_at` **optional**; `ProductDetailRead` / `ProductVersionRead` / quality breakdown already typed |
| Shopify HTTP | `backend/app/api/v1/integrations/router.py` | `POST /integrations/shopify/publish-readiness` and `POST /integrations/shopify/publish` are Admin; token **optional** on merchant publish |
| Shopify schemas | `backend/app/integrations/shopify/schemas.py` | `ShopifyPublishResponse` is the existing publish wire type. `ShopifyPublishRequest.expected_updated_at` is optional — **must not be reused** for pipeline approve/publish. |
| Drafts M2A | `backend/app/api/v1/drafts/router.py` | PATCH requires `expectedUpdatedAt` in the **service**, not a dedicated required-field schema |
| AI prompts HTTP | `backend/app/api/v1/ai/router.py` | All endpoints `RequireAdmin`, including reads |
| Drafts router | `backend/app/api/v1/drafts/router.py` | No optimize/activate/pipeline routes. Stage 8 lives on `/products` only. |
| Exceptions | `backend/app/core/exceptions.py`, `app/ai/exceptions.py`, `app/integrations/shopify/exceptions.py`, `app/ai/image_fetch.py` | Status codes already assigned on the exception classes |
| Isolation tests | `backend/tests/integration/test_product_optimization.py` | Foreign product optimize → 404; non-admin optimize/activate → 403 |
| Pipeline tests | `test_product_pipeline.py`, `_publish.py`, `_activation_lock.py` | Service-level contracts already pinned; Stage 8 adds HTTP coverage, does not rewrite them |

API prefix used throughout: `/api/v1`.

---

## 4. Existing Stage 7 service contract

Pinned from `ProductPipelineService`. Stage 8 must not weaken any of it.

### 4.1 `preview(product_id, *, store_id=None, tone="professional", requested_by_user_id)`

- Analyses live product images (`ImageAnalysisService.analyse_product_images`).
- Generates **one new inactive** pipeline candidate (`generate_candidate`).
- Does **not** activate. Does **not** publish. Does **not** commit.
- Optional `store_id` only affects `channel_readiness` composition after generation.
- `requested_by_user_id` is recorded on analysis/generation executions.
- Returns `PipelinePreview`.

### 4.2 `get_preview(product_id, *, version_id, store_id=None)`

- Loads the product and the version via `get_by_id_for_product`.
- Does **not** analyse again. Does **not** generate again.
- `parse_pipeline_candidate_metadata` — original, unmarked legacy AI, or
  malformed marker → `ValidationError` `reason=not_a_pipeline_candidate`.
- Optional `store_id` controls channel readiness only.
- Image evidence is reconstructed from **stored** `ProductImage.analysis`.

### 4.3 `approve(product_id, *, version_id, expected_updated_at)`

- `expected_updated_at is None` → `ValidationError` 422 (before freshness).
- Product `FOR UPDATE` first; then version `populate_existing=True`.
- Missing product/version (including foreign / wrong product) → `NotFoundError` 404.
- Original snapshot → `ValidationError` `reason=original_not_approvable`.
- Non-pipeline / corrupt marker → `ValidationError` `reason=not_a_pipeline_candidate`.
- Exact already-active candidate → **no-op**, return product (even if the
  token is the pre-approval timestamp).
- Stale M2A token → `ConflictError` `reason=draft_version_stale` 409.
- Inactive candidate whose `pipelineSourceUpdatedAt != product.updated_at`
  → `ConflictError` `reason=stale_preview` 409.
- Title storage bound: ≤ 512; no truncation.
- Activates only a valid pipeline candidate. No publish. No merchant /
  supplier / SEO / image overwrite. Product AI cache updated via
  `_apply_active_version`.

### 4.4 `publish(product_id, *, store_id, version_id, expected_updated_at)`

- `expected_updated_at is None` → `ValidationError` 422 **before** the Product lock.
- Product `FOR UPDATE` with `PUBLISH_LOCK_TIMEOUT_MS`; then version `populate_existing`.
- Candidate must be `AI_GENERATED` **and** `active=True` after strict parse;
  otherwise `reason=candidate_not_approved`.
- Missing / blank `ai_provider` → `reason=ai_provenance_unverified`.
- `isSynthetic is True` **or** `ai_provider == "stub"` → `reason=synthetic_publish_blocked`.
- Title publish bound: ≤ 255; sanitized description ≤ `DESCRIPTION_MAX_LENGTH` (64_000).
- Overlay is `ShopifyListingOverlay(title, body_html)` only.
- Delegates to existing `ShopifySyncService.publish_product(..., listing_overlay=overlay)`.
- No second publisher. Merchant SEO/tags/images/variants untouched.

`PipelinePreview` fields Stage 8 must project (snake → camel on the wire):

`product_id`, `candidate_version_id`, `candidate_version_number`,
`candidate_active`, `source_updated_at`, `approval_expected_updated_at`,
`original`, `proposal`, `quality_score`, `quality_baseline`,
`quality_delta`, `quality_score_version`, `quality_breakdown`,
`image_analysis`, `is_synthetic`, `provider`, `channel_readiness`,
`pipeline_blockers`, `pipeline_warnings`, `publishable`.

`PipelineListingView`: `title`, `description`, `seo_title`,
`seo_description`, `keywords`, `tags`.

Proposal SEO/keywords are **display-only**. Stage 7 overlay publish does
not send them to Shopify. Stage 8 must not invent an overlay field that
would.

---

## 5. Exact route table

Prefix: `/api/v1/products` (`APIRouter(prefix="/products")`).

Do **not** add `/drafts/.../pipeline` duplicates. Drafts have no optimize
route today; the existing optimize path is already on `/products`. Stage 10
will call these product URLs from the studio.

Do **not** overload `POST /api/v1/integrations/shopify/publish`. That
route remains the merchant overlay-less publisher with an **optional**
token. Pipeline publish is a different contract (required token, required
approved candidate, overlay).

| # | Method | Path | Status | Auth | Request | Response | Service |
|---|---|---|---|---|---|---|---|
| 1 | `POST` | `/api/v1/products/{product_id}/pipeline/preview` | **201** | `RequireAdmin` | `PipelinePreviewRequest` body | `PipelinePreviewResponse` | `preview` |
| 2 | `GET` | `/api/v1/products/{product_id}/pipeline/versions/{version_id}/preview` | **200** | `RequireAdmin` | query `storeId?` | `PipelinePreviewResponse` | `get_preview` |
| 3 | `POST` | `/api/v1/products/{product_id}/pipeline/versions/{version_id}/approve` | **200** | `RequireAdmin` | `PipelineApproveRequest` body | `ProductDetailRead` | `approve` |
| 4 | `POST` | `/api/v1/products/{product_id}/pipeline/versions/{version_id}/publish` | **200** | `RequireAdmin` | `PipelinePublishRequest` body | `ShopifyPublishResponse` | `publish` |

Path parameters: `product_id: UUID`, `version_id: UUID` (FastAPI path
parsing; malformed UUID → 422 `validation_error` via the global
request-validation handler).

**201 on generate** matches `POST /products/{id}/optimize`: a successful
call creates a `ProductVersion` (and analysis executions). GET/approve/
publish do not create a candidate, so they stay 200. Shopify merchant
publish is already 200; pipeline publish reuses that status.

Handlers pass `requested_by_user_id=principal.user_id` into `preview`.
Approve/publish/get_preview do not take an actor id today; do not add one.

Empty JSON `{}` is accepted on generate (tone defaults). Approve and
publish **reject** `{}` because `expectedUpdatedAt` is required at schema
layer.

---

## 6. Exact request schemas

All new request models: `CamelCaseModel` (`to_camel`, `populate_by_name`,
`extra="forbid"`). Added to `backend/app/schemas/product.py` — extend the
existing product schema module; do not create `schemas/pipeline.py`.

Do **not** reuse:

- `ProductOptimizeRequest` — no `storeId`
- `ProductUpdateRequest` — `expectedUpdatedAt` is **optional** (legacy PATCH last-write-wins)
- `ShopifyPublishRequest` — `expectedUpdatedAt` is **optional**; also carries `productId` which would duplicate the path

### 6.1 `PipelinePreviewRequest`

```text
tone: str = Field(default="professional", min_length=1, max_length=64)
store_id: uuid.UUID | None = None
```

Wire: `{ "tone"?: string, "storeId"?: uuid | null }`.

`tone` matches `ProductOptimizeRequest`. `storeId` in the **body** matches
`ShopifyPublishReadinessRequest` / `ProductImportRequest`, not a query
string, because this is a POST with a body.

### 6.2 GET preview query

```text
store_id: Annotated[uuid.UUID | None, Query(alias="storeId")] = None
```

GET has no body. Existing inventory/global-rules routes already alias
`storeId` this way.

### 6.3 `PipelineApproveRequest`

```text
expected_updated_at: datetime   # required; no default; not Optional
```

Wire: `{ "expectedUpdatedAt": "<ISO-8601>" }`.

- Missing / `null` / wrong type → 422 `validation_error` (Pydantic /
  `RequestValidationError` handler).
- Extra fields → 422 (`extra=forbid`).
- Service still rejects `None` if a future caller bypasses the schema
  (defense in depth; not a second HTTP convention).

### 6.4 `PipelinePublishRequest`

```text
store_id: uuid.UUID             # required
expected_updated_at: datetime   # required
```

Wire: `{ "storeId": "<uuid>", "expectedUpdatedAt": "<ISO-8601>" }`.

Channel is not a client field. Stage 8 is Shopify-only because Stage 7 is
Shopify-only. Do not accept `channel`.

Clients should send `approvalExpectedUpdatedAt` from the latest preview
(or `updatedAt` from `GET /products/{id}`) as `expectedUpdatedAt`.

---

## 7. Exact response schemas

### 7.1 `PipelinePreviewResponse`

Typed projection of `PipelinePreview`. Never return the dataclass or a
bare `dict`.

| Python field | Wire | Type |
|---|---|---|
| `product_id` | `productId` | UUID |
| `candidate_version_id` | `candidateVersionId` | UUID |
| `candidate_version_number` | `candidateVersionNumber` | int |
| `candidate_active` | `candidateActive` | bool |
| `source_updated_at` | `sourceUpdatedAt` | datetime |
| `approval_expected_updated_at` | `approvalExpectedUpdatedAt` | datetime |
| `original` | `original` | `PipelineListingViewRead` |
| `proposal` | `proposal` | `PipelineListingViewRead` |
| `quality_score` | `qualityScore` | int \| null |
| `quality_baseline` | `qualityBaseline` | `ProductVersionQualityBaselineRead` \| null |
| `quality_delta` | `qualityDelta` | int \| null |
| `quality_score_version` | `qualityScoreVersion` | int \| null |
| `quality_breakdown` | `qualityBreakdown` | `ProductVersionQualityBreakdownRead` \| null |
| `image_analysis` | `imageAnalysis` | `PipelineImageAnalysisReportRead` |
| `is_synthetic` | `isSynthetic` | bool |
| `provider` | `provider` | str \| null |
| `channel_readiness` | `channelReadiness` | `ShopifyPublishReadinessResponse` \| null |
| `pipeline_blockers` | `pipelineBlockers` | `PipelineCheckItemRead[]` |
| `pipeline_warnings` | `pipelineWarnings` | `PipelineCheckItemRead[]` |
| `publishable` | `publishable` | bool |

Reuse, do not duplicate:

- `ProductVersionQualityBaselineRead` (`versionNumber`, `score`) — same
  shape as `PipelineQualityBaseline`.
- `ProductVersionQualityBreakdownRead` — already the typed Stage 5
  `qualityBreakdown` mirror. `None` when the stored dict is absent.
- `ShopifyPublishReadinessResponse` for `channelReadiness` when
  `store_id` was supplied. Same check-item shape the merchant readiness
  endpoint already returns (`code`, `message`, `field`, `section`,
  `action`). **Do not** invent a second readiness model.

Do **not** expose on the preview wire:

- `pipelineCandidateVersion` (server-written marker; Stage 7 forbids
  client supply)
- raw `ProductVersion.content`
- `prompt_execution_id` (available on `GET .../versions` if needed)
- supplier twins (`supplierTitle` / `supplierDescription` / costs) —
  listing views do not carry them
- listing overlay internals
- credentials, tokens, Shopify access tokens

`PipelineListingViewRead`:

| Python | Wire |
|---|---|
| `title` | `title` |
| `description` | `description` |
| `seo_title` | `seoTitle` |
| `seo_description` | `seoDescription` |
| `keywords` | `keywords` |
| `tags` | `tags` (`list[str]`; proposal is always `[]` from Stage 7) |

`original` is the **merchant/current product** listing (including merchant
SEO/tags). `proposal` is the **candidate content** (AI title/description
and AI SEO proposal). Stage 10 uses that pair as a visual diff.
Publication still overlays title + sanitized body only.

`PipelineCheckItemRead`: `code: str`, `message: str` only — that is the
Stage 7 DTO. Do not force `field`/`section`/`action` onto pipeline
blockers; those exist on Shopify readiness items.

`PipelineImageAnalysisReportRead`:

- `productId: UUID`
- `images: PipelineImageAnalysisItemRead[]`

`PipelineImageAnalysisItemRead`:

- `imageId: UUID`
- `position: int`
- `status: str`
- `errorCode: str | null`
- `analysis: PipelineImageAnalysisEvidenceRead`

`PipelineImageAnalysisEvidenceRead` is the **typed** Stage 6 persisted
JSON (`_evidence` / `_failure_payload`), not `dict[str, Any]`:

`imageAnalysisVersion`, `sourceUrl`, `contentSha256`, `byteLength`,
`decodedWidth`, `decodedHeight`, `decodedFormat`, `status`, `errorCode`,
`checks`, `captionProposal`, `altTextProposal`, `isSynthetic`, `provider`,
`model`, `promptName`, `promptVersion`.

`checks` may be `null` on fetch failure. Nested check objects stay typed
enough to round-trip stored keys (`blur`, `duplicates`, `watermark`)
without dropping fields. Implementation must `model_validate` the stored
dict; if a fixture is missing a required key, fail the test rather than
silently dumping JSON.

These keys are already tenant-owned `ProductImage.analysis` data. They are
not a new leak. Caption/alt are **proposals**; Stage 7 still does not write
`ProductImage.alt_text`.

### 7.2 Approve response — `ProductDetailRead`

Yes. Legacy `POST .../versions/{id}/activate` already returns
`ProductDetailRead` via `_to_detail`. Approve is the pipeline activation
path; reuse the same projection so the client sees `aiStatus`,
`optimizedTitle` / `optimizedDescription`, and `updatedAt` after the
cache write.

`ProductDetailRead` includes supplier twins and variant costs. That is
the existing authenticated product-detail contract for this tenant, not a
Stage 8 invention. Do not return a narrower type that would make approve
look unlike activate.

Do **not** return `PipelinePreviewResponse` from approve. Approval is not
a preview refresh.

### 7.3 Publish response — `ShopifyPublishResponse`

Reuse `app.integrations.shopify.schemas.ShopifyPublishResponse`
**verbatim**. Map the `publish_product` dict the same way
`publish_to_shopify` already does (`message`, `listingId`,
`externalProductId`, `externalHandle`, `externalGraphqlId`, `shopDomain`,
`storefrontUrl`, `adminUrl`, `onlineStorePublished`, `updated`).

Do not invent `PipelinePublishResponse`. A second Shopify success model
would drift.

A small shared projector used by both routers is allowed if it avoids
copy-paste; it is not required. Duplicating the existing ten-line mapping
is acceptable under KISS (second caller, still tiny).

---

## 8. Auth matrix

Discovered policy, not assumed:

| Existing operation | Dependency | Viewer | Member | Admin | Owner |
|---|---|---|---|---|---|
| `GET /products/{id}` | `RequireViewer` | 200 | 200 | 200 | 200 |
| `GET /products/{id}/versions` | `RequireViewer` | 200 | 200 | 200 | 200 |
| `POST /products/{id}/optimize` | `RequireAdmin` | 403 | 403 | 201 | 201 |
| `POST /products/{id}/versions/{id}/activate` | `RequireAdmin` | 403 | 403 | 200 | 200 |
| `POST /integrations/shopify/publish` | `RequireAdmin` | 403 | 403 | 200 | 200 |
| `GET/POST /ai/prompts...` | `RequireAdmin` (including reads) | 403 | 403 | ok | ok |

`RequireAdmin` is `require_minimum_role(ADMIN)`: **ADMIN and OWNER**.
MEMBER is below the threshold (rank 10 < 20).

Stage 7 plan §21: “Stage 8 will use `RequireAdmin` on new routes,
matching optimize/publish.”

Stage 8 pins **all four** pipeline routes to `RequireAdmin`:

| Endpoint | Unauthenticated | Viewer | Member | Admin | Owner | Why |
|---|---|---|---|---|---|---|
| POST preview | 401 `authentication_required` | 403 | 403 | 201 | 201 | Consumes provider work; creates a `ProductVersion`; same class as optimize |
| GET preview | 401 | 403 | 403 | 200 | 200 | Image analysis + publishability + quality; AI prompt HTTP is also admin-on-read. Stricter than `GET .../versions` by intent. |
| POST approve | 401 | 403 | 403 | 200 | 200 | Changes active AI state; same class as activate |
| POST publish | 401 | 403 | 403 | 200 | 200 | Changes a sales channel; same class as Shopify publish |

Unauthenticated uses the existing bearer dependency (`auto_error=False`)
so the envelope stays standard. Unverified users follow
`require_verified` already inside `RequireAdmin` (no-op while
`SECURITY_REQUIRE_EMAIL_VERIFICATION` is false).

Do not introduce `RequireMember` for generate. Optimize does not.

---

## 9. Tenant-isolation matrix

No router-side SQL. No ad-hoc `tenant_id` argument. Tenant comes from
context via `TenantScopedRepository` inside the services Stage 8 calls.

Cross-tenant access is **404 `not_found`**, never 403. A 403 would
confirm the foreign row exists.

| Attempt | Expected | Why |
|---|---|---|
| Preview another tenant's `product_id` | 404 | `ProductRepository.get_by_id_or_raise` |
| GET preview foreign product | 404 | same |
| GET/approve/publish foreign `version_id` | 404 | `get_by_id_for_product` |
| Version of **this** tenant but **another product** | 404 | same helper; not 409, not 422 |
| Approve/publish another tenant's version id with own product id | 404 | version not in that product |
| Preview/GET with another tenant's `storeId` | 404 | readiness/store lookup (`Store was not found` / equivalent) — same as missing store |
| Publish to another tenant's store | 404 | `ShopifySyncService` / store repository |
| Valid UUID that does not exist | 404 | indistinguishable from foreign |
| Own resources | success per §5 | scoped query finds the row |

A foreign `storeId` on **POST preview** is evaluated **after** analyse +
generate inside `_compose_preview`. If that lookup 404s, `get_db_session`
rolls back the whole request — no leftover `ProductVersion` /
`PromptExecution`. Do not “succeed preview then omit readiness” for a
foreign store; that would be a tenant leak (`publishable: false` vs 404).

Do not add a third unscoped repository.

---

## 10. M2A concurrency semantics

Pipeline approve and publish **require** `expectedUpdatedAt` at the HTTP
schema **and** at the service. Optional-token merchant PATCH and optional
token `ShopifyPublishRequest` are **not** the precedent.

| Case | Approve | Publish |
|---|---|---|
| Field omitted | 422 `validation_error` (Pydantic) | same |
| `null` | 422 `validation_error` | same |
| Malformed timestamp | 422 `validation_error` | same |
| Extra unknown field | 422 `extra_forbidden` | same |
| Token ≠ live `Product.updated_at` (inactive candidate) | 409 `conflict`, details reason `draft_version_stale` | 409 from pipeline compare **or** from `publish_product` / readiness `enforce_version` — still 409 `conflict` |
| Candidate generated against older product (`stale_preview`) | 409 `conflict`, reason `stale_preview` | N/A if still inactive (`candidate_not_approved` 422). If merchant edited **after approval**, M2A token must be the **current** `updatedAt`; stale token 409; approval is **not** revoked |
| Exact already-active candidate retry | 200 no-op (Stage 7 §12.2) | If still approved + fresh token: existing publisher idempotency |
| Sibling candidate after another approved | 409 `stale_preview` or activation race as Stage 7 tests | 422 `candidate_not_approved` if not the active row |
| Publish before approve | — | 422 `candidate_not_approved` |
| Merchant edit after candidate generation, before approve | 409 `stale_preview` and/or `draft_version_stale` | — |

Envelope for domain errors (existing `app_error_handler`):

```json
{
  "code": "conflict",
  "message": "The product changed after this preview was generated.",
  "details": [
    { "field": null, "message": "stale_preview", "type": "reason" }
  ],
  "requestId": "..."
}
```

`AppError.details` is flattened as `ErrorDetail(type=key, message=str(value))`.
Clients already branch on `code`. The pipeline `reason` lives in
`details[].type == "reason"`. **Do not** invent Stage-8-only envelopes or
promote `reason` to `code` in the router.

Naive timestamps: if Pydantic accepts them, `datetime != timestamptz`
fails the equality check and becomes 409, not a silent pass. Tests should
send the ISO-8601 string from `updatedAt` / `approvalExpectedUpdatedAt`
(offset-aware), matching existing M2A editor tests.

---

## 11. Error / status mapping

Routers must **not** catch `AppError`. `register_exception_handlers`
already maps every subclass.

| Situation | Exception | HTTP | `code` | `details` reason (when set) |
|---|---|---|---|---|
| Missing/invalid body field, bad UUID, extra field | `RequestValidationError` | 422 | `validation_error` | Pydantic `loc` |
| Product not found / foreign product | `NotFoundError` | 404 | `not_found` | `resource` / `identifier` when `for_resource` used |
| Version not found / wrong product / foreign version | `NotFoundError` | 404 | `not_found` | same |
| Store not found / foreign store | `NotFoundError` | 404 | `not_found` | message only on some paths (`Store was not found.`) |
| Non-pipeline version (GET preview / approve / publish parse) | `ValidationError` | 422 | `validation_error` | `not_a_pipeline_candidate` |
| Original snapshot approve | `ValidationError` | 422 | `validation_error` | `original_not_approvable` |
| Missing token at service (schema bypass) | `ValidationError` | 422 | `validation_error` | (none today) |
| Stale M2A token | `ConflictError` | 409 | `conflict` | `draft_version_stale` |
| Stale inactive preview fingerprint | `ConflictError` | 409 | `conflict` | `stale_preview` |
| Publish while not active/approved | `ValidationError` | 422 | `validation_error` | `candidate_not_approved` |
| Missing provider | `ValidationError` | 422 | `validation_error` | `ai_provenance_unverified` |
| Synthetic / stub publish | `ValidationError` | 422 | `validation_error` | `synthetic_publish_blocked` |
| Title too long for storage (approve) | `ValidationError` | 422 | `validation_error` | Stage 7 storage code |
| Title too long for Shopify (publish) | `ValidationError` | 422 | `validation_error` | Stage 7 publish code / `candidate_title_not_publishable` on preview blockers |
| Sanitized description too long | `ValidationError` | 422 | `validation_error` | `candidate_description_too_long` |
| Channel/readiness hard-block inside `publish_product` | `ValidationError` | 422 | `validation_error` | `publish_blocked` (+ `blocker_codes`) |
| Concurrent Shopify publish lock timeout | `ShopifyPublishBusyError` | 409 | `shopify_publish_busy` | — |
| Shopify upstream failure | `ShopifyError` and subclasses | **503** (`InfrastructureError` / `ExternalServiceError`) unless the subclass sets otherwise | `shopify_error`, `shopify_timeout`, `shopify_rate_limited`, `shopify_graphql_error`, … | `service=shopify` |
| Shopify not connected | `ShopifyNotConnectedError` | 422 | `validation_error` | — |
| Provider generation failure on preview | `AIError` | **503** | `ai_error` | — |
| Unauthenticated | `AuthenticationError` | 401 | `authentication_required` | — |
| Authenticated, under-privileged | `PermissionDeniedError` | 403 | `permission_denied` | — |
| Unhandled bug | generic handler | 500 | `internal_error` | no internals |
| DB driver leak | `SQLAlchemyError` handler | 503 | `database_error` | no driver text |

Image fetch/decode errors during `analyse_product_images` are **per-image
status on the report**, not a product-level abort (`ImageFetchError`
docstring). Preview still 201 with `pipelineWarnings` / failed image
`status`. Do not translate those into 422 at the router.

Preview **blockers** (`pipelineBlockers`, `publishable: false`) are
success-path data, not HTTP errors. A synthetic StubProvider preview is
a valid 201 that cannot later publish.

---

## 12. Route-order / collision analysis

`products/router.py` documents declaration order because FastAPI matches
in order. The dangerous case is a **one-segment** literal (`/imports`,
`/import`, `/workspace-counts`, `/feeds/{name}`) colliding with
`/{product_id}`.

Stage 8 paths are **multi-segment after `{product_id}`**:

- `/{product_id}/pipeline/preview`
- `/{product_id}/pipeline/versions/{version_id}/preview`
- `/{product_id}/pipeline/versions/{version_id}/approve`
- `/{product_id}/pipeline/versions/{version_id}/publish`

They cannot be swallowed by `GET|PATCH /{product_id}` (different remainder
and, for POST, different method). They cannot collide with
`GET /{product_id}/versions` (literal `pipeline` vs `versions` as the
second segment). They cannot collide with
`POST /{product_id}/versions/{version_id}/activate` (literal `pipeline`
vs `versions` first).

`POST /{product_id}/optimize` and `POST /{product_id}/sync` remain.

**Required declaration:** add the four pipeline routes in
`products/router.py` **immediately after** optimize + activate (end of
file today). Relative order among the four:

1. POST `/{product_id}/pipeline/preview`
2. GET `/{product_id}/pipeline/versions/{version_id}/preview`
3. POST `/{product_id}/pipeline/versions/{version_id}/approve`
4. POST `/{product_id}/pipeline/versions/{version_id}/publish`

No new one-segment literal under `/products`. Do not name a route
`/products/pipeline` without `{product_id}` — that **would** need to sit
before `/{product_id}`. Stage 8 does not add that.

Static check: OpenAPI must list all four paths; `GET /products/pipeline`
must not exist.

---

## 13. Transaction / locking boundaries

`get_db_session`: one request-scoped transaction; **commit on success**,
**rollback on any exception**. Handlers never call `commit()` or
`rollback()`.

Stage 8 routers must **not**:

- open a nested transaction
- `SELECT FOR UPDATE`
- reorder Product vs ProductVersion locks
- call `activate_version` / `optimize_product` / `publish_product`
  instead of `ProductPipelineService`
- wrap analyse+generate+approve+publish in one HTTP handler

Lock ownership stays in Stage 7:

| HTTP | Locks (service) |
|---|---|
| POST preview | Image analysis + `generate_candidate` (generation **outside** Product lock, same as optimize). Readiness evaluate has no Product write lock. |
| GET preview | Read-only composition |
| POST approve | Product `FOR UPDATE` → version `populate_existing` → activate → cache |
| POST publish | Token check **before** lock → Product `FOR UPDATE` (`PUBLISH_LOCK_TIMEOUT_MS`) → version → overlay → existing publisher |

A handler that added its own Product lock around `approve` would risk
lock-order inversion with legacy activate. **Forbidden.**

---

## 14. Idempotency / retry rules

No Stage 8 idempotency table. No `Idempotency-Key` header.

| Endpoint | Retry |
|---|---|
| POST preview | **Not idempotent.** Each 201 creates a **new** inactive candidate and re-runs analysis. Prior inactive rows remain history. Safe to retry; do not treat two 201s as one candidate. |
| GET preview | Pure read. Must **not** insert `PromptExecution`, must **not** fetch images, must **not** call the provider. Repeatable. |
| POST approve | Exact already-active candidate → 200 no-op. Inactive sibling of an old token is **not** a retry. |
| POST publish | Relies on existing adopt-by-handle + `uq_store_listings_tenant_store_product` + publish row lock. No second create path. After Shopify timeout, retry with the **current** `expectedUpdatedAt`. Publish failure does **not** deactivate the candidate (Stage 7). |

GET preview tests must assert PromptExecution count and image
`analysis` timestamps/bytes do not change.

---

## 15. Legacy compatibility

**Unchanged paths:**

- `POST /api/v1/products/{product_id}/optimize` — generate + auto-activate
  **unmarked** AI version; 201 `ProductOptimizeResponse`.
- `POST /api/v1/products/{product_id}/versions/{version_id}/activate` —
  still refuses parsed **and** corrupt pipeline candidates (Stage 7).
  Unmarked ORIGINAL / legacy AI activation unchanged.
- `POST /api/v1/integrations/shopify/publish` — optional token; no
  overlay unless that caller already passed one (today's HTTP does not).
- M2A product PATCH optional token; draft PATCH required in service.
- Stage 7 lock-order tests remain service tests; HTTP must not bypass
  them.

Stage 8 is additive. No silent change to those contracts. Frontend
Optimize button (Stage 10) still hits legacy optimize until the studio
migrates; Stage 8 must not change that button.

---

## 16. Store / readiness semantics

| Call | `storeId` | `channelReadiness` | `publishable` |
|---|---|---|---|
| POST preview omit / `null` | none | `null` | **false** (Stage 7: cannot become true without a store) |
| POST preview own Shopify store | body | `ShopifyPublishReadinessResponse` | `true` only if no pipeline blockers **and** `canPublish` |
| GET preview omit | none | `null` | false |
| GET preview `?storeId=` | query | projected | same rule |
| POST publish | **required** body | n/a (publish or error) | — |

Stage 8 is **not** multi-channel. Hard-code nothing in the router;
readiness/publish already use `CHANNEL_SHOPIFY`. Do not accept eBay or a
`channel` field.

Foreign/unknown store: **404**, not a preview with empty readiness
(§9).

---

## 17. Test matrix

New file: `backend/tests/integration/test_product_pipeline_api.py`
(HTTP). Do **not** rename existing Stage 7 service tests. Reuse
`StubProvider` / deterministic fixtures. **No live AI claim.**

Publish-success HTTP tests use the same non-synthetic fixture pattern as
Stage 7 (`ai_provider="test"`, `isSynthetic=False`) plus the existing
Shopify test doubles — not a live model and not a live shop unless the
suite already has that harness for merchant publish.

### A. Route / schema

- Request camelCase aliases (`storeId`, `expectedUpdatedAt`, `tone`).
- Response camelCase for every §7 field.
- OpenAPI contains the four paths; schemas named, not `additionalProperties` blobs.
- POST preview 201; GET 200; approve 200; publish 200.
- Approve/publish missing `expectedUpdatedAt` → 422.
- `expectedUpdatedAt: null` → 422.
- Malformed UUID path → 422.
- Malformed timestamp → 422.
- Extra body field → 422.
- GET preview does not accept a JSON body as a substitute for `storeId`.

### B. Authentication / authorization

- No `Authorization` → 401 on all four.
- Viewer and Member → 403 on all four (including GET preview).
- Admin and Owner → allowed.
- Match optimize's non-admin test style.

### C. Tenant isolation

- Foreign product on all four → 404, same body shape as missing product.
- Foreign candidate version → 404.
- Version belonging to another **own** product → 404.
- Foreign store on preview GET/POST and on publish → 404.
- Status/body must not differ in a way that enumerates foreign ids
  (404 vs 403; 404 vs 422 `not_a_pipeline_candidate` for a version the
  caller cannot see).

### D. POST preview

- Creates inactive candidate; `candidateActive` is false.
- Returns `candidateVersionId` / `candidateVersionNumber`.
- Returns `sourceUpdatedAt` and `approvalExpectedUpdatedAt`.
- Omit store → `channelReadiness` null, `publishable` false.
- Own store → readiness projected; `publishable` false under StubProvider
  (synthetic blocker) unless a non-synthetic fixture is used.
- Image analysis projected (`imageId`, `status`, evidence keys).
- `isSynthetic` / `provider` projected (`stub` in default tests).
- Product `optimizedTitle` unchanged; no Shopify call.
- Prompt executions created for generation (and analysis as Stage 6
  already does).

### E. GET preview

- Returns the exact `version_id`.
- Second GET does not increment `ProductVersion` count.
- No new `PromptExecution` rows.
- Image `analysis` content unchanged (no refetch).
- Original snapshot → 422 `not_a_pipeline_candidate`.
- Legacy unmarked optimize version → 422 same.
- Malformed marker → 422 same.
- Wrong product + own version → 404 (not 422).

### F. Approve

- Exact candidate → 200; `ProductDetailRead` cache reflects candidate
  title/description; `candidate` would now be active on a subsequent GET
  preview.
- `expectedUpdatedAt` required (422).
- Stale token → 409 `draft_version_stale`.
- Stale preview (merchant edit after generate) → 409 `stale_preview`.
- Exact active retry → 200 no-op; merchant/supplier/SEO/images untouched.
- Sibling race: first sibling wins; second 409 as Stage 7.
- Calling **legacy activate** on a pipeline candidate still 422 (regression).

### G. Publish

- Not approved → 422 `candidate_not_approved`.
- `expectedUpdatedAt` required → 422.
- Synthetic candidate → 422 `synthetic_publish_blocked`.
- Missing provider → 422 `ai_provenance_unverified`.
- `ai_provider=="stub"` → 422 even if `isSynthetic` were false (Stage 7).
- Non-synthetic fixture reaches **existing** `publish_product` (mock/
  recorded shop as current Shopify tests do).
- Overlay title/body only — assert merchant SEO/tags/images/variants
  unchanged on the product and on the mocked Shopify payload.
- Duplicate Shopify create prevented by existing publisher tests still
  passing.
- Title/body bounds 422; sanitized HTML (script stripped) as Stage 7.
- Foreign store 404.
- Shopify failure does not deactivate the candidate (HTTP-level replay of
  Stage 7 publish test).

### H. Regression (must still pass unchanged)

- `test_product_optimization.py` optimize + activate + isolation.
- M2A editor conflict tests.
- `test_product_pipeline.py` / `_publish.py` / `_activation_lock.py`.
- Shopify publish/idempotency/lock-order tests.
- Legacy optimize still auto-activates unmarked rows **via HTTP**.

### I. Honesty

Tests may use `StubProvider` or a deterministic fixture provider. Do not
claim real model quality. Publish-success is a publisher-contract test,
not an AI-quality test.

---

## 18. Expected files for implementation

| File | Change |
|---|---|
| `backend/app/schemas/product.py` | Add request/response models listed in §6–7 |
| `backend/app/api/v1/products/router.py` | Four thin routes; import `ProductPipelineService`, `ShopifyPublishResponse` |
| `backend/tests/integration/test_product_pipeline_api.py` | HTTP matrix §17 |
| `backend/tests/unit/` (optional, small) | Schema alias / extra=forbid / required token |
| `docs/PHASE_9_STAGE_8_COMPLETION.md` | Written in the **implementation** change, not this plan PR |
| `CHANGELOG.md` / `PROJECT_ROADMAP.md` | Implementation change, same PR as code |

Optional: a ten-line `ShopifyPublishResponse` projector shared with
`integrations/router.py` **only if** implementation would otherwise
copy-paste. Not a new service.

**Not in Stage 8:** `frontend/**`, `app/tasks/**`, Alembic, models,
repositories, `product_pipeline.py` behaviour changes, Celery, Stage 9–11
docs except a one-line roadmap tick when implementation lands.

This planning PR contains **only** `docs/PHASE_9_STAGE_8_PLAN.md`.

---

## 19. Explicit boundaries

| Item | Stage 8 |
|---|---|
| Database migration | **NO** — Alembic head stays **0033** |
| Model / repository rewrite | **NO** |
| Frontend | **NO** — do not modify `frontend/` |
| Celery / bulk / progress / task polling | **NO** — Stage 9 |
| Batch HTTP whose implementation needs a queue | **NO** |
| Provider integration / live keys | **NO** |
| Second Shopify publisher | **NO** |
| AI SEO overlay | **NO** |
| Deployment / `main` | **NO** |
| Stage 9–11 start | **NO** |

If implementation discovery shows a migration is required: **STOP** and
explain before proposing one. Current discovery: none required. Preview
state is `ProductVersion` + JSONB markers + `ProductImage.analysis` +
`StoreListing`.

---

## 20. Risks / residual LOW items

Independent review of **this plan** (see §20.1). BLOCKER / HIGH / MEDIUM
were resolved in the plan text before commit. LOW residuals accepted:

| ID | Residual | Why accepted |
|---|---|---|
| L1 | POST preview with a bad/foreign `storeId` still runs analyse+generate, then 404s and **rolls back** | Stage 7 method order is analyse → generate → compose. Reordering in Stage 8 would duplicate store lookup in the router. Wasteful, not a leak. |
| L2 | GET preview is Admin-only while `GET .../versions` is Viewer | Matches Stage 7 plan + `/ai/prompts` read policy. Viewers still see version title/description via the list. |
| L3 | `AppError.details` flattens `reason` into `details[].type` | Existing global handler. Do not fork an envelope. |
| L4 | Approve returns supplier fields on `ProductDetailRead` | Same as GET product / activate for that tenant. |
| L5 | Image evidence includes `sourceUrl` / hashes / byteLength | Already stored on `ProductImage.analysis`; admin of that tenant. |
| L6 | No extra `endpoint_rate_limit` on preview | Optimize has none. Adding one would be new policy, not HTTP-contract. |
| L7 | Duplicate `ShopifyPublishResponse` mapping until a helper exists | KISS; both mappings must stay field-identical. |
| L8 | Naive `expectedUpdatedAt` may 409 rather than 422 | Same datetime equality as Stage 7 / M2A. Tests send API `updatedAt`. |
| L9 | Shopify credential failures can surface as 401 `ShopifyAuthError` | Existing merchant publish already does. Distinct `code` from missing user JWT. |
| L10 | Claude has not reviewed Stage 5–8 | Checkpoint below. Not a Stage 8 HTTP defect. |
| L11 | No `Idempotency-Key` on generate | Stage 7 preview is intentionally not idempotent. |
| L12 | StubProvider previews are 201 but never Shopify-publishable | Correct fail-closed publish; Studio (Stage 10) will show blockers. |

### 20.1 Self-review (attack the plan)

| Attack | Verdict | Resolution |
|---|---|---|
| Pipeline activation through legacy activate | **Resolved** | Legacy path stays; tests assert 422. New path is `/pipeline/.../approve` only. |
| Missing `expectedUpdatedAt` | **Resolved** | Dedicated required schemas; do not reuse optional Shopify/PATCH models. |
| Store tenant escape | **Resolved** | 404 via existing store/readiness; rollback on preview failure. |
| Version/product mismatch | **Resolved** | Service `get_by_id_for_product` → 404. |
| Stale candidate bypass | **Resolved** | HTTP does not catch/ignore `stale_preview`. |
| Synthetic publish bypass | **Resolved** | Publish still `ProductPipelineService.publish`; no merchant publish overload. |
| Duplicate Shopify listing | **Resolved** | Existing publisher only; no second create. |
| Supplier/internal leak on preview | **Resolved** | Listing views omit supplier twins; approve reuses existing detail schema. |
| Untyped JSON response | **Resolved** | Typed schemas; breakdown + readiness reused; image evidence typed. |
| Route collision | **Resolved** | Multi-segment paths; declaration after optimize/activate. |
| Business logic in router | **Resolved** | Four delegates + projection. |
| Manual error mapping | **Resolved** | None; global handler. |
| Transaction weakening lock order | **Resolved** | No router locks. |
| GET preview regenerating AI | **Resolved** | Calls `get_preview` only; tests forbid new executions. |
| Preview activating | **Resolved** | Service contract + `candidateActive` assertion. |
| Publish before approval | **Resolved** | Service 422 `candidate_not_approved`. |
| AI SEO leaking into Shopify | **Resolved** | Overlay title/body only; tests assert payload. |
| Stage 9 scope | **Resolved** | Single-product HTTP; no bulk/progress. |
| Frontend scope | **Resolved** | No `frontend/` in expected files. |

Counts for this plan: **BLOCKER 0, HIGH 0, MEDIUM 0, LOW 12** (accepted).

---

## 21. Implementation sequence

Do not start until this plan is independently reviewed and an
implementation branch is authorized.

1. Add schemas to `schemas/product.py` (requests + preview responses).
   Reuse quality + `ShopifyPublishReadinessResponse`.
2. Add four routes to `products/router.py` in the order in §12. Thin
   handlers only. `from_dto` / `_to_detail` / Shopify response mapping.
3. HTTP integration tests §17 A–G.
4. Run regression §17 H (existing files, not rewritten).
5. Quality gate: `cd backend && ruff check . && ruff format --check . && mypy app && pytest`.
6. Completion docs + CHANGELOG + roadmap in the **same** implementation
   change.
7. **Stop.** Do not open Stage 9.

---

## 22. Acceptance gates

Stage 8 implementation is not complete until:

1. All four routes exist with the verbs/paths/status codes in §5.
2. Routers contain no SQL, no locks, no `optimize_product` /
   `activate_version` / raw `publish_product` for these four.
3. `expectedUpdatedAt` required on approve and publish at schema layer.
4. Tenant 404 (not 403) for foreign product/version/store.
5. Viewer/Member 403; Admin/Owner allowed; unauthenticated 401.
6. Preview does not activate or publish.
7. GET preview does not generate or analyse.
8. Legacy optimize + activate HTTP contracts unchanged.
9. Alembic head still `0033`.
10. No `frontend/` and no Celery task files in the implementation diff.
11. Quality gate green. Honesty: StubProvider / fixtures, no live-AI claim.
12. Claude return-review checkpoint preserved.

This **plan** PR is accepted when: docs-only diff, CI 10/10, independent
review of the contract, **not merged until that review says so**.

---

## 23. Rollback strategy

This plan PR is documentation. Revert the merge commit if needed.

Implementation (future): revert the Stage 8 API commit(s). Legacy
optimize/activate/Shopify publish keep working because they are untouched.
No down-migration: there is no schema change. In-flight clients calling
the new paths would 404 after revert — Stage 10 will not ship against
them until Stage 8 is accepted.

Do not revert Stage 7 to roll back Stage 8.

---

## 24. Claude return-review checkpoint

Cursor is the temporary planning agent. This document is **not** a Claude
review.

CLAUDE RETURN REVIEW CHECKPOINT:
All commits from Stage 5 takeover onward require a fresh Claude
end-to-end review when Claude becomes available again.
