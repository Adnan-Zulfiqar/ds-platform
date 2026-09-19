# Phase 9 Stage 7 — Pipeline: plan

Analyse → Generate → Score → Preview → Approve → Publish.

Written before implementation and checked against the code as it stood at
`develop` `0a26a121bf6c03a9e03e1b5ee2b9b69de5c63012` (Stage 6 docs closeout
merged; post-merge CI run 35458334556, 10/10). Remediated after independent
plan review of `78674f83df921cbe19e4b239988e175f7a812f3b` (H1, H2, M1–M4).
Where this plan narrows what [PHASE_9_PLAN.md](PHASE_9_PLAN.md) §3 originally
sketched for Stage 7, it says so.

This is the authoritative Stage 7 implementation contract. Claude has not
reviewed Stage 7.

---

## 1. Baseline identity

| | |
|---|---|
| Branch to implement from | `develop` @ `0a26a121bf6c03a9e03e1b5ee2b9b69de5c63012` |
| `origin/main` | `3ce66d488e94ad3805fe24903deda99691c234a6` (unchanged; no deploy) |
| Stage 6 | COMPLETE / MERGED / GREEN — impl merge `96b890d2`; docs closeout merge `0a26a12`; PR #17 |
| Latest migration | `0033` (`product_images.analysis`) |
| Current provider | `StubProvider` via `get_ai_provider(settings)` |
| Planning status | PLANNING / AWAITING INDEPENDENT REVIEW |
| Pipeline origin marker | `ProductVersion.content["pipelineCandidateVersion"] = 1` |

---

## 2. Current-state audit

### 2.1 Generation auto-activates

`ProductOptimizationService.optimize_product`
(`backend/app/services/product_optimization.py` L71–182) does:

1. `_ensure_original_snapshot` — version 1 `ORIGINAL`, `active=True` on first call.
2. Three `PromptService.test_render(..., execute=True)` calls:
   `product_title_generator`, `product_description_generator`, `seo_optimizer`.
3. Any `FAILED` execution → `Product.ai_status = FAILED`, flush, raise `AIError`.
   No new version. Last good `optimized_*` / `ai_version` left untouched.
4. `score_version(generated, product)` and `score_version(original.content, product)`.
5. `ProductVersionRepository.create(..., active=False)`.
6. **`versions.activate(...)` immediately.**
7. `_apply_active_version` writes `Product.ai_status`, `ai_version`, `ai_provider`,
   `ai_last_generated_at`, `optimized_title`, `optimized_description`.
8. Flush. Does not commit (caller session does).

`_apply_active_version` (L223–256) never assigns `Product.title`,
`description`, `supplier_*`, `seo_title`, `seo_description`, `meta_keywords`,
`tags`, or any `ProductImage` column. That is the live guarantee behind
"AI cannot overwrite merchant/supplier fields".

Partial unique index `uq_product_versions_product_active` enforces at most one
active row per product. Rollback is the same `activate` call on an older id
via `ProductOptimizationService.activate_version`.

Legacy optimize-created rows have **no** `pipelineCandidateVersion` key.
They must never become pipeline candidates, even after a later real
provider exists.

### 2.2 Image analysis is service-only

`ImageAnalysisService.analyse_product_images` writes `ProductImage.analysis`
JSONB and flushes. It does not commit, does not bump `Product.updated_at`,
does not write `ProductImage.alt_text`, and has no HTTP route.
`ProductImageRead` does not include `analysis`.

Per-image statuses: `succeeded`, `checksOnly`, `fetchFailed`, `decodeFailed`.
Siblings continue after an expected per-image failure. Stage 6 always
recomputes; there is no skip-if-hash path.

### 2.3 Publish uses merchant fields, not `optimized_*`

Authoritative publisher: `ShopifySyncService.publish_product`
(`backend/app/integrations/shopify/sync.py` L160–383).

HTTP: `POST /api/v1/integrations/shopify/publish` (admin). Celery:
`shopify.publish_product` (no `expected_updated_at`).

Payload (`sync.py` L261–275):

| Shopify field | Current source |
|---|---|
| `title` | `Product.title` |
| `body_html` | `Product.description` |
| `metafields_global_title_tag` | `Product.seo_title` if set |
| `metafields_global_description_tag` | `Product.seo_description` if set |
| `tags` | `Product.tags` |
| images `src` / `alt` | `ProductImage.url` / `ProductImage.alt_text` |
| variants / prices / SKU / inventory | merchant variant columns |
| `handle` (create) | `Product.slug` or `droppilot-{product_id}` |
| `vendor` / `product_type` / `status` | merchant/supplier catalogue fields |

`optimized_title` / `optimized_description` / version SEO proposals are
**never read** by `sync.py`. AI optimisation is not a publish blocker.
Image analysis is not on the publish path.

`StoreListing.status == synced` means the channel listing is synced. It
does **not** record which `ProductVersion` was last projected. Stage 7
does not add that provenance (no migration).

Publish does not flip `Product.status` to `active`. A typical imported
`DRAFT` is created on Shopify as REST `status=draft`.

### 2.4 Readiness is already a single engine

`PublishReadinessService.evaluate` / `require_publishable`
(`backend/app/services/publish_readiness.py`).

Blockers: store required, unsupported channel, store disconnected,
destination mismatch, selling-currency mismatch, draft version stale.

Recommendations only (do not set `can_publish=False`): `title_thin`,
`description_empty`, `images_missing`.

Frontend `readinessFor()` is presentation-only.

### 2.5 M2A token is `Product.updated_at`

DB-generated (`TimestampMixin.onupdate=func.now()`). Draft `PATCH` requires
`expectedUpdatedAt`; mismatch → `ConflictError` (409, code `conflict`).
Publish readiness/publish reuse the same token.

Image add/update/reorder/remove and variant routes do **not** bump
`Product.updated_at` (existing M2A design,
`tests/integration/test_draft_editor_concurrency.py`). Title/description/SEO
edits through `update_draft` do.

Optimize/activate **do** bump `Product.updated_at` because they assign
`Product.ai_*` / `optimized_*`.

### 2.6 HTML sanitization already exists

`app.core.sanitize.sanitize_html` (`backend/app/core/sanitize.py`) uses
nh3 with an explicit tag/attribute allow-list and `http`/`https` URL
schemes only. `javascript:`, `data:`, `vbscript:`, `<script>`, and `on*`
handlers are stripped. Merchant `Product.description` is sanitized on
write. Model completions are **not**. Stage 7 must sanitize generated
description at overlay-build time, not by mutating stored version
content (§14).

### 2.7 Stage 4 SEO is an opaque blob

`seo_optimizer` completions are stored verbatim as `seoTitle`,
`seoDescription`, and `keywords` on `ProductVersion.content`. With
`StubProvider` they are the same opaque string. There is no structured
SEO parse contract. Stage 7 must not send those values to Shopify
metafields.

---

## 3. Existing call graph

```
POST /api/v1/products/{id}/optimize
  → ProductOptimizationService.optimize_product
      → generate three prompts → score → create → activate → cache fields
GET  /api/v1/products/{id}/versions
GET  /api/v1/drafts/{id}/versions          (same list_versions; frontend unused)
POST /api/v1/products/{id}/versions/{vid}/activate
  → ProductOptimizationService.activate_version
POST /api/v1/integrations/shopify/publish-readiness
  → PublishReadinessService.evaluate(..., enforce_version=False)
POST /api/v1/integrations/shopify/publish
  → ShopifySyncService.publish_product
      → require_publishable → lock_for_update → REST PUT/POST

Celery shopify.publish_product → same ShopifySyncService.publish_product
  (no expected_updated_at)

No Celery caller of optimize_product.
No HTTP caller of ImageAnalysisService.
```

Frontend:

| Hook | File | Endpoint |
|---|---|---|
| `useOptimizeProduct` | `frontend/services/products.ts` | `POST /products/{id}/optimize` |
| `useProductVersions` | same | `GET /products/{id}/versions` |
| `useActivateProductVersion` | same | `POST /products/{id}/versions/{id}/activate` |
| Publish | `draft-product-editor.tsx` | `POST /integrations/shopify/publish` |

UI: `optimize-product-button.tsx`, `draft-product-editor.tsx`,
`product-table.tsx`, `product-version-history-sheet.tsx`.

Integration tests that assert **immediate activation** after optimize:
`backend/tests/integration/test_product_optimization.py`
(`body["version"]["active"] is True`, `aiStatus == "optimized"`).

Those callers remain valid for **legacy** versions. They must not be able
to activate or pipeline-publish a Stage 7 candidate (§9, §13, §15).

---

## 4. Problem statement

Stage 7 must orchestrate Analyse → Generate → Score → Preview → Approve →
Publish **in that order**, as in-process services.

Mismatches with the code as it exists:

1. **Preview is missing.** `optimize_product` activates on success. A
   generated version is already the live AI cache before any merchant
   approval.
2. **Approved AI copy never reaches Shopify.** The publisher reads
   `Product.title` / `Product.description`, not `optimized_*`.
3. **A naive overlay would be the first path that can send `[STUB-AI]`
   text, unsanitized model HTML, or Stage 4's opaque SEO blob to a real
   shop.** Today merchant publish cannot do that, because it ignores the
   AI cache.
4. **Sharing one public `generate_candidate` with legacy optimize would
   let `optimize_product` mint a pipeline-marked row, auto-activate it,
   and let `ProductPipelineService.publish` skip `approve`.** Version
   history `activate_version` would be a second skip of `approve`.

Stage 7 must introduce preview-before-approve **without** changing the
existing optimize HTTP contract, must mark pipeline candidates distinctly
from legacy optimize rows, must project only sanitized approved title and
description onto the existing publisher without rewriting merchant
columns or publishing AI SEO, and must refuse to send synthetic copy to a
sales channel.

---

## 5. Exact Stage 7 scope

Stage 7 implements a new domain service that composes existing services.

| In scope | Out of scope |
|---|---|
| `ProductPipelineService` (preview, approve, publish) | HTTP routes / OpenAPI / Stage 8 schemas |
| Private shared `_generate_version` plus public `generate_candidate` that creates an **inactive pipeline** `AI_GENERATED` version (`pipelineCandidateVersion=1`) | Changing `POST /products/{id}/optimize` response or auto-activation |
| Exact-candidate approval with stale guard | Celery bulk / progress (Stage 9) |
| `ProductOptimizationService.activate_version` refuses pipeline candidates | Allowing version-history activate to approve a preview |
| Optional `ShopifyListingOverlay` (`title`, `body_html` only) on the **existing** publisher | Overlaying AI SEO, tags, images, variants |
| Sanitize generated description with `sanitize_html` at overlay build | Mutating stored `ProductVersion.content` or `Product.description` |
| Call `ImageAnalysisService.analyse_product_images` from preview | Writing `ProductImage.alt_text` |
| Compose `PublishReadinessService` without copying its rules | Frontend / AI Studio (Stage 10) |
| Internal frozen DTOs | `ProductImageRead.analysis`; durable "this version was published" provenance |
| Tests proving order, marker, bypass refusal, stale reject, sanitizer, synthetic block, overlay, regressions | Migration, new tables, `ProductAIStatus` values |
| | Live providers, prompt template edits, `quality_scorer` wiring, structured SEO parse |
| | Deploy, `main` |

---

## 6. Explicit non-goals

- No new FastAPI router, no new path under `/api/v1/`, no change to
  `app/api/v1/ai/router.py` (prompt admin stays prompt admin).
- No frontend file, no `frontend/types/api.ts` catch-up (still Stage 10 / M4).
- No Celery task for the pipeline.
- No Alembic migration. Head stays `0033`.
- No rewrite of `score_version`, `ImageFetcher`, decode/checks, or the
  three Stage 4 prompt names/templates.
- No silent write to `Product.title` / `description` / `supplier_*` /
  merchant SEO / tags / slug / prices / SKU / inventory / image alt.
- No claim of live model quality. `StubProvider` only.
- No Online Store publications API (M24). Shopify REST product create/update
  remains the publisher.
- No change to M2A: draft `expectedUpdatedAt` still mandatory; 409 still
  `conflict`; cross-tenant still 404.
- No StoreListing column or JSON for last-published `ProductVersion` id.
- No publishing of `seoTitle` / `seoDescription` / `keywords` from version
  content.

---

## 7. Pipeline state machine

Persisted Stage 7 candidate states are derived from existing rows plus
the JSONB marker. No new enum column. No durable "Published" state.

| Name | Meaning in this repository |
|---|---|
| No candidate | No row with `content.pipelineCandidateVersion == 1` for this product, or the caller has not selected one |
| Previewed | An `AI_GENERATED` row exists with `pipelineCandidateVersion == 1`, `active=False`, and `pipelineSourceUpdatedAt` set. `Product.ai_*` still reflects whichever version is currently active (legacy optimize, a previous pipeline approval, or ORIGINAL) |
| Approved | That exact pipeline row is `active=True`; `_apply_active_version` has synced the AI cache |

**Publish is an operation, not a persisted state.** A successful
`ProductPipelineService.publish` return proves that **this call**
delegated a sanitized overlay of the selected approved pipeline version
to `ShopifySyncService.publish_product`. After the request ends, that
fact is not stored.

`StoreListing.status == synced` means the channel listing is synced. It
does **not** mean this exact `ProductVersion` is the last version
projected. `get_preview`, `PipelinePreview`, and future Stage 8 API must
not claim exact candidate→listing provenance from `StoreListing`. Exact
provenance requires a future migration; Stage 7 does not fake it.

Illegal transitions the implementation must refuse:

- `publish` of a version that is not `AI_GENERATED`.
- `publish` of a version lacking `pipelineCandidateVersion == 1`
  (`not_a_pipeline_candidate`) — includes every legacy `optimize_product`
  row, even if it is active and later non-synthetic.
- `publish` while the named pipeline `version_id` is not `active`
  (`candidate_not_approved`).
- `approve` of `ORIGINAL`.
- `approve` of a version lacking `pipelineCandidateVersion == 1`.
- `approve` of a version whose `product_id` does not match.
- `approve` of an inactive pipeline candidate when
  `Product.updated_at != expected_updated_at` or
  `Product.updated_at != content.pipelineSourceUpdatedAt`.
- `publish` when `content.isSynthetic is True` or `ai_provider == "stub"`.
- `ProductOptimizationService.activate_version` of a pipeline candidate
  (`pipeline_candidate_requires_approval`).

`optimize_product` is **not** a node on this state machine. It remains
the legacy generate-and-activate shortcut. Its versions cannot enter
Previewed/Approved and cannot be pipeline-published.

---

## 8. Analyse contract

| | |
|---|---|
| Method | `ImageAnalysisService.analyse_product_images(product_id, *, executed_by_user_id=None) -> ImageAnalysisReport` |
| When | Every `ProductPipelineService.preview` call, before generation. Not on `get_preview`, `approve`, or `publish`. |
| Reuse | None. Stage 6 has no skip-if-hash. Stage 7 does not add one. |
| Persist | `ProductImage.analysis` JSONB; `ProductImage.updated_at` moves; `Product.updated_at` does not |
| Forbidden | `alt_text`, URL, position, `is_supplier`, any `Product` merchant/AI-cache field, `ProductVersion`, public schemas |
| Failure | Expected per-image `fetchFailed` / `decodeFailed` / `checksOnly` are stored and **not** pipeline-fatal. Unexpected exceptions propagate; the caller transaction rolls back analyse + generate together |
| Empty images | Empty report; generation still runs |
| Tenant | `ProductRepository.get_by_id_or_raise` then `ProductImageRepository.list_for_product` |

Partial analysis is acceptable for preview. It is advisory for publish
(§18).

---

## 9. Generate contract

One private core. Two public wrappers. They must not share a pipeline
marker. The boolean `as_pipeline_candidate` is the only switch.

Pinned executable contract:

```python
async def _generate_version(
    self,
    product_id: uuid.UUID,
    *,
    tone: str,
    requested_by_user_id: uuid.UUID | None,
    as_pipeline_candidate: bool,
) -> tuple[Product, ProductVersion]:
    product = await self.products.get_by_id_or_raise(product_id)
    original = await self._ensure_original_snapshot(product)
    await self.session.refresh(product)
    variables = self._build_variables(product, tone=tone)
    _, _, title_execution = await self.prompts.test_render(
        name=_TITLE_PROMPT, variables=variables, execute=True,
        executed_by_user_id=requested_by_user_id,
    )
    _, _, description_execution = await self.prompts.test_render(
        name=_DESCRIPTION_PROMPT, variables=variables, execute=True,
        executed_by_user_id=requested_by_user_id,
    )
    _, _, seo_execution = await self.prompts.test_render(
        name=_SEO_PROMPT, variables=variables, execute=True,
        executed_by_user_id=requested_by_user_id,
    )
    failed = _first_failure(title_execution, description_execution, seo_execution)
    if failed is not None:
        raise AIError(failed.error_message or "Product optimisation failed.")
    if title_execution is None or description_execution is None or seo_execution is None:
        raise AIError("Product optimisation did not produce a result to record.")
    generated = {
        "title": title_execution.response_text,
        "description": description_execution.response_text,
        "seoTitle": seo_execution.response_text,
        "seoDescription": seo_execution.response_text,
        "keywords": seo_execution.response_text,
    }
    scored = score_version(generated, product)
    baseline = score_version(original.content, product)
    content: dict[str, Any] = {
        **generated,
        **scored.as_content(baseline=baseline),
    }
    if as_pipeline_candidate:
        content["pipelineCandidateVersion"] = 1
        content["pipelineSourceUpdatedAt"] = product.updated_at.isoformat()
        content["isSynthetic"] = bool(
            title_execution.is_synthetic
            or description_execution.is_synthetic
            or seo_execution.is_synthetic
        )
    next_number = await self.versions.next_version_number(product.id)
    version = await self.versions.create(
        product_id=product.id,
        version_number=next_number,
        source=ProductVersionSource.AI_GENERATED,
        content=content,
        active=False,
        ai_provider=title_execution.provider,
        prompt_execution_id=description_execution.id,
        created_by_user_id=requested_by_user_id,
    )
    return product, version


async def generate_candidate(
    self,
    product_id: uuid.UUID,
    *,
    tone: str = "professional",
    requested_by_user_id: uuid.UUID | None,
) -> ProductVersion:
    _product, version = await self._generate_version(
        product_id,
        tone=tone,
        requested_by_user_id=requested_by_user_id,
        as_pipeline_candidate=True,
    )
    return version


async def optimize_product(
    self,
    product_id: uuid.UUID,
    *,
    tone: str = "professional",
    requested_by_user_id: uuid.UUID | None,
) -> tuple[Product, ProductVersion]:
    try:
        product, version = await self._generate_version(
            product_id,
            tone=tone,
            requested_by_user_id=requested_by_user_id,
            as_pipeline_candidate=False,
        )
    except AIError:
        product = await self.products.get_by_id_or_raise(product_id)
        product.ai_status = ProductAIStatus.FAILED
        await self.flush()
        raise
    activated = await self.versions.activate(
        product_id=product.id, version_id=version.id
    )
    self._apply_active_version(product, activated)
    await self.flush()
    return product, activated
```

Rules:

- `_generate_version` does not call `activate` and does not call
  `_apply_active_version` (except `_ensure_original_snapshot` on first
  ever version, which is existing Stage 3 behaviour).
- `as_pipeline_candidate=True` writes `pipelineCandidateVersion=1`,
  `pipelineSourceUpdatedAt` (ISO-8601 of `product.updated_at` **after**
  the original-snapshot refresh), and `isSynthetic`.
- `as_pipeline_candidate=False` **omits** those three keys. Legacy
  optimize rows stay unmarked.
- Prompt names stay `product_title_generator`,
  `product_description_generator`, `seo_optimizer`. Do not duplicate
  those three calls or `score_version` outside `_generate_version`.
- Failed `_generate_version` raises `AIError` without setting
  `Product.ai_status = FAILED`. `optimize_product` still records
  `FAILED` in its `except`. Pipeline `generate_candidate` does not.
- `optimize_product` still auto-activates the unmarked row. HTTP/UI/tests
  stay. `product` on that success path is the instance returned by
  `_generate_version`.
- Previously active version (pipeline-approved, legacy, or ORIGINAL)
  remains active while a new pipeline candidate sits inactive.

---

## 10. Score contract

Unchanged Stage 5: `score_version(content, product)` in
`backend/app/services/optimization_quality.py`. Called inside
`_generate_version` before `create`. Rubric version 1. `quality_scorer`
prompt stays unwired. No new score keys.

---

## 11. Preview contract

Stage 7 has no HTTP. Preview is an internal frozen DTO.

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class PipelineListingView:
    title: str | None
    description: str | None
    seo_title: str | None
    seo_description: str | None
    keywords: str | None
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class PipelineCheckItem:
    code: str
    message: str


@dataclass(frozen=True, slots=True, kw_only=True)
class PipelinePreview:
    product_id: uuid.UUID
    candidate_version_id: uuid.UUID
    candidate_version_number: int
    candidate_active: bool
    source_updated_at: datetime
    approval_expected_updated_at: datetime
    original: PipelineListingView          # current merchant Product fields
    proposal: PipelineListingView          # candidate ProductVersion.content
    quality_score: int | None
    quality_baseline: int | None
    quality_delta: int | None
    quality_score_version: int | None
    quality_breakdown: dict[str, Any] | None
    image_analysis: ImageAnalysisReport    # Stage 6 internal type
    is_synthetic: bool
    provider: str | None
    channel_readiness: PublishReadinessResult | None
    pipeline_blockers: tuple[PipelineCheckItem, ...]
    pipeline_warnings: tuple[PipelineCheckItem, ...]
    publishable: bool
```

`original` is always the live `Product` merchant state (`title`,
`description`, `seo_title`, `seo_description`, `meta_keywords` as
keywords, `tags`). It is not the ORIGINAL snapshot if the merchant has
since edited.

`proposal` is the candidate version's `title` / `description` /
`seoTitle` / `seoDescription` / `keywords` **as stored data**. Preview
does not sanitize into HTML and does not treat the description as a
storefront document. `proposal.tags` is `()` — generated keywords are
not merchant tags. AI SEO is review-only; Stage 7 does not publish it.

`candidate_active` is `False` after `preview()`. `get_preview` reports
the row's real flag.

`approval_expected_updated_at` equals live `Product.updated_at` at DTO
build time for a fresh preview (and equals `source_updated_at` when the
candidate is not stale). Stage 8 will send this as `expectedUpdatedAt`
into `approve` for a first approval.

`publishable` is `True` only when **all** of: candidate has
`pipelineCandidateVersion == 1`, is active, not stale, not synthetic,
`channel_readiness` is present and `can_publish`, and `pipeline_blockers`
is empty. A fresh `preview()` therefore always has `publishable=False`
because the candidate is inactive. That is the proof that preview is not
approval.

`publishable` does **not** mean "this version is the last one synced to
Shopify". It means this candidate would pass pipeline publish
preconditions **right now** if `publish` were called with the current
M2A token.

Image analysis evidence stays on the internal DTO / ORM. It is not added
to `ProductImageRead`.

Service methods:

```python
async def preview(
    self,
    product_id: uuid.UUID,
    *,
    store_id: uuid.UUID | None = None,
    tone: str = "professional",
    requested_by_user_id: uuid.UUID | None,
) -> PipelinePreview:
    """Analyse, generate an inactive pipeline candidate, return a preview DTO.

    Does not activate. Does not publish. Does not commit.
    """

async def get_preview(
    self,
    product_id: uuid.UUID,
    *,
    version_id: uuid.UUID,
    store_id: uuid.UUID | None = None,
) -> PipelinePreview:
    """Compose a preview from an existing pipeline candidate.
    No analyse, no generate. Rejects non-pipeline versions.
    """
```

`preview` order: analyse → `generate_candidate` → compose DTO (readiness
if `store_id` is not None). One caller transaction. If generate raises,
analyse is rolled back with it.

`get_preview` requires `pipelineCandidateVersion == 1`. Otherwise
`ValidationError` `details.reason = "not_a_pipeline_candidate"`. It reads
current `ProductImage.analysis` as stored. It does not refetch images.
It does not read `StoreListing` to claim a published version.

---

## 12. Stale-preview / concurrency contract

Approval identifies an **exact** `version_id`. "Latest version" is not a
valid selector.

### 12.1 What the candidate records

Only pipeline rows (`as_pipeline_candidate=True`) write:

- `pipelineCandidateVersion`: `1`
- `pipelineSourceUpdatedAt`: `Product.updated_at` immediately before
  `create`, as ISO-8601
- `isSynthetic`: OR of the three execution flags

Merchant title/description/SEO/slug/tag edits go through
`ProductService.update_draft` / `update_product` and bump
`Product.updated_at`. That is sufficient to detect those edits. No extra
hash column. No migration.

Image/variant routes do not bump `Product.updated_at` (existing M2A).
Stage 7 does not change that. Approval does not write image alt; publish
reads **live** image/variant rows, so a later image edit is what gets
sent, not the preview-time set. Documented residual §29.

### 12.2 Approve preconditions

`approve` takes a row lock (`ProductRepository.lock_for_update(product_id)`
with default `timeout_ms=None`, so it waits for an in-flight publish
rather than surfacing `ShopifyPublishBusyError`) then:

1. Product exists in tenant (`NotFoundError` otherwise).
2. `get_by_id_for_product(product_id, version_id)` — foreign or
   wrong-product version → `NotFoundError` (404, not 403).
3. `version.source is AI_GENERATED`. `ORIGINAL` → `ValidationError`
   with `details.reason = "original_not_approvable"`.
4. `version.content.get("pipelineCandidateVersion") == 1`. Missing or
   any other value (legacy optimize rows) → `ValidationError` with
   `details.reason = "not_a_pipeline_candidate"`.
5. `content.pipelineSourceUpdatedAt` present. Missing on a marked row is
   a bug → `ValidationError` `details.reason = "not_a_pipeline_candidate"`.
6. `expected_updated_at` is required. `None` → `ValidationError`
   (same posture as `update_draft`).
7. **If this exact row is already `active`:** return the current
   `Product` immediately. Do not compare `expected_updated_at` to live
   `updated_at`. Do not compare `pipelineSourceUpdatedAt`. Do not call
   `versions.activate`. Do not call `_apply_active_version`. Do not
   create a version. This is the lost-response retry: the client may
   still hold the pre-approval token.
8. If the version is inactive:
   `expected_updated_at` must equal live `product.updated_at` else
   `ConflictError` `details.reason = "draft_version_stale"`.
   Live `product.updated_at` must equal parsed `pipelineSourceUpdatedAt`
   else `ConflictError` `details.reason = "stale_preview"`. Caller must
   `preview()` again.
9. `versions.activate` then `_apply_active_version` then flush.

### 12.3 First approval wins for that source token

Two inactive pipeline candidates A and B generated from the same
`Product.updated_at`:

- Approving A succeeds, writes `Product.ai_*` / `optimized_*`, flushes,
  and **bumps** `Product.updated_at`.
- Approving B then fails `stale_preview`: B still carries the old
  `pipelineSourceUpdatedAt`. B remains historical and inactive.
- The caller must run a new `preview()` to mint a candidate against the
  new product token.

Do not state that the later activate wins. The later sibling is stale.

Same-candidate concurrent retries: lock serialises. First activates.
Second sees `active=True` and takes the no-op path in step 7.

`requested_by_user_id` is not persisted on approve (the version already
has `created_by_user_id` from generate).

After a successful first approve, `Product.updated_at` moves. Pipeline
`publish` must use the **post-approve** token from the returned
`Product`, not the preview's `approval_expected_updated_at`.

---

## 13. Approve contract

```python
async def approve(
    self,
    product_id: uuid.UUID,
    *,
    version_id: uuid.UUID,
    expected_updated_at: datetime,
) -> Product:
```

Approve means: make this **pipeline** candidate the active
`ProductVersion` and sync the AI **cache** via existing
`_apply_active_version`.

`ProductPipelineService.approve` is the **only** business method allowed
to activate a row with `pipelineCandidateVersion == 1`. It calls
`ProductVersionRepository.activate` after the §12.2 checks, then
`ProductOptimizationService._apply_active_version`.

| Field | Written? |
|---|---|
| `ProductVersion.active` | Yes on first approve — this row true, previous false. No-op if already active |
| `Product.ai_status` | `optimized` (first approve only) |
| `Product.ai_version` | candidate `version_number` (first approve only) |
| `Product.ai_provider` / `ai_last_generated_at` | Yes (first approve only) |
| `Product.optimized_title` / `optimized_description` | Yes, from version content (first approve only) |
| `Product.title` / `description` / `supplier_*` | **No** |
| Merchant SEO / tags / slug | **No** |
| `ProductImage.alt_text` | **No** |
| `Product.status` | **No** |
| New `ProductVersion` row | **No** |
| `ProductVersion.content` | **No** — never rewritten, including sanitizer |

### 13.1 Version-history activation must not approve a preview

`ProductOptimizationService.activate_version` (HTTP
`POST /products/{id}/versions/{version_id}/activate`) **refuses** a
pipeline candidate:

```python
if version.content.get("pipelineCandidateVersion") == 1:
    raise ValidationError(
        "This version is a pipeline preview and must be approved "
        "through the product pipeline.",
        details={"reason": "pipeline_candidate_requires_approval"},
    )
```

Historical `ORIGINAL` and legacy `AI_GENERATED` rows (no marker) keep
today's activate/rollback behaviour.

`ProductVersionRepository.activate` stays a persistence helper. It is
not a public business API. Production callers in Stage 7:

- `optimize_product` — unmarked rows only
- `activate_version` — unmarked rows only, after the refusal above
- `ProductPipelineService.approve` — marked rows only, after §12.2

Does not publish. Does not call Shopify.

---

## 14. Publish projection contract

Approved pipeline copy reaches Shopify **only** through an overlay on
the existing publisher. Merchant/supplier columns stay as they are.

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class ShopifyListingOverlay:
    title: str
    body_html: str
```

Stage 7 overlay contains **only** title and sanitized description. It
does not carry SEO fields. When an overlay is present, the publisher
substitutes `title` and `body_html` and **leaves merchant SEO mapping
unchanged**.

| Shopify field | Pipeline publish source |
|---|---|
| `title` | overlay.title ← version.content["title"] (raw string; not HTML) |
| `body_html` | overlay.body_html ← `sanitize_html(version.content["description"]) or ""` |
| `metafields_global_title_tag` | **merchant** `Product.seo_title` if set |
| `metafields_global_description_tag` | **merchant** `Product.seo_description` if set |
| `tags` | **merchant** `Product.tags` (never version `keywords`) |
| images | **live** `ProductImage.url` / `alt_text` (never analysis proposals) |
| variants, prices, SKU, inventory, weight | **merchant** variant/product fields |
| handle / slug | **merchant** slug / deterministic handle |
| vendor, product_type, Shopify REST status | **merchant** |

Meta keywords / `search_topics` remain unsent (current publisher).

Do not send `version.content["seoTitle"]`, `seoDescription`, or
`keywords` to Shopify in Stage 7. There is no structured SEO-output
contract. Those values remain on the preview DTO for review only.
A later stage that introduces a validated SEO parse may extend the
overlay; Stage 7 must not pre-empt that.

### 14.1 Sanitize at overlay build, not in storage

Immediately before constructing `ShopifyListingOverlay`:

```python
raw_description = version.content.get("description")
if not isinstance(raw_description, str):
    raw_description = ""
safe_body_html = sanitize_html(raw_description) or ""
overlay = ShopifyListingOverlay(
    title=str(version.content.get("title") or ""),
    body_html=safe_body_html,
)
```

Rules:

- `ProductVersion.content` keeps the raw generated description.
- Preview continues to expose proposal description as stored data.
- Shopify receives only the sanitizer output.
- Do not write `Product.description`.
- `sanitize_html` is `app.core.sanitize.sanitize_html` (nh3 allow-list).
  `<script>` is removed; `on*` attributes are stripped; `javascript:` /
  `data:` / `vbscript:` URL values do not survive; allowed formatting
  (`p`, `br`, `strong`, `em`, `ul`/`ol`/`li`, `a[href=http(s)]`, …)
  survives.

---

## 15. Existing publisher delegation

Extend `ShopifySyncService.publish_product` with one optional argument:

```python
async def publish_product(
    self,
    *,
    store_id: uuid.UUID,
    product_id: uuid.UUID,
    expected_updated_at: datetime | None = None,
    listing_overlay: ShopifyListingOverlay | None = None,
) -> dict[str, Any]:
```

When `listing_overlay is None` (today's HTTP + Celery callers): behaviour
byte-identical to current mapping.

When provided: substitute **only** `title` and `body_html` in
`product_body`. Do not touch SEO keys, tags, images, variants, or
handle. Readiness, lock, adopt-by-handle, `StoreListing` upsert, and
error handling stay as they are.

`ProductPipelineService.publish`:

```python
async def publish(
    self,
    product_id: uuid.UUID,
    *,
    store_id: uuid.UUID,
    version_id: uuid.UUID,
    expected_updated_at: datetime,
) -> dict[str, Any]:
```

1. Load product + `get_by_id_for_product` (404 if foreign/mismatch).
2. `source is AI_GENERATED` else `ValidationError`
   `details.reason = "candidate_not_approved"`.
3. `content.get("pipelineCandidateVersion") == 1` else `ValidationError`
   `details.reason = "not_a_pipeline_candidate"` (legacy optimize
   versions, even if active and later non-synthetic).
4. `version.active is True` else `ValidationError`
   `details.reason = "candidate_not_approved"`.
5. Synthetic guard (§17).
6. Build overlay with `sanitize_html` (§14.1).
7. `return await ShopifySyncService(self.session).publish_product(
       store_id=store_id,
       product_id=product_id,
       expected_updated_at=expected_updated_at,
       listing_overlay=overlay,
   )`.

Do not duplicate REST mapping, handle adopt, or listing upsert.

Existing merchant publish without overlay remains valid: a merchant can
still push `Product.title` to Shopify without ever running the pipeline.
Pipeline publish is the only path that sends approved AI title +
sanitized AI description.

A successful return is evidence of **this call**. It is not persisted as
"version V is the listing's AI provenance."

---

## 16. Readiness integration

Do not copy blocker rules into the pipeline service.

`preview` / `get_preview` with `store_id` set call
`PublishReadinessService.evaluate(channel="shopify", product_id=...,
store_id=..., expected_updated_at=product.updated_at,
enforce_version=False)` and attach the result as `channel_readiness`.

Pipeline-specific conditions live in `pipeline_blockers` /
`pipeline_warnings`, not inside `PublishReadinessService`:

| Condition | Bucket | Code |
|---|---|---|
| No/inactive pipeline candidate (fresh preview) | blocker for **pipeline publish**, not for merchant publish | `candidate_not_approved` |
| Legacy optimize version used as pipeline id | pipeline blocker | `not_a_pipeline_candidate` |
| `stale_preview` (source token ≠ live `updated_at`) | pipeline blocker | `stale_preview` |
| `isSynthetic` | pipeline blocker for publish | `synthetic_publish_blocked` |
| Failed generate | no DTO; `AIError` | n/a |
| Image `fetchFailed` / `decodeFailed` | pipeline **warning** | `image_analysis_incomplete` |
| Image `checksOnly` | pipeline **warning** | `image_analysis_checks_only` |
| Blur / duplicate flags | pipeline **warning** | `image_blurry` / `image_duplicate` |
| `store_id` omitted on preview | `channel_readiness is None`; `publishable=False`; evaluate is not called | n/a |
| Channel blockers | from `channel_readiness.blockers` | existing codes |

`publishable` on the DTO is the AND of pipeline blockers empty, pipeline
marker present, candidate active, not synthetic, not stale, and
`channel_readiness.can_publish`.

`ProductPipelineService.publish` still calls the existing
`require_publishable` **inside** `publish_product`. Do not re-implement
destination/currency/connection checks.

AI optimisation remains **not** required for merchant publish.

---

## 17. Synthetic-provider policy

Current factory resolves to `StubProvider`. Every `complete` /
`analyse_image` result has `is_synthetic=True` and text prefixed
`[STUB-AI]`.

| Action | Allowed with synthetic pipeline candidate? |
|---|---|
| Generate | Yes |
| Preview | Yes |
| Approve | Yes — studio can mark a stub pipeline version active in the AI cache |
| Publish to Shopify (pipeline overlay) | **No** |

Guard in `ProductPipelineService.publish` **before** overlay build and
**before** `ShopifySyncService.publish_product` (no provider HTTP):

If `version.content.get("isSynthetic") is True` **or**
`version.ai_provider == "stub"`:

```python
raise ValidationError(
    "Synthetic AI content cannot be published to a sales channel.",
    details={"reason": "synthetic_publish_blocked"},
)
```

Rationale: Stage 7 overlay is the first code path that would send AI
copy to Shopify. Stub output is labelled, but a merchant can still press
Publish. Blocking is the only way to keep "no live AI quality claim"
true at the channel boundary. Merchant-only publish of `Product.title`
is unaffected.

Tests that need a successful overlay publish construct a **pipeline**
candidate (`pipelineCandidateVersion=1`) with `isSynthetic=False` and
`ai_provider="test"` (or a fake non-stub provider in unit tests). They
do not claim a live model. Legacy optimize rows are never used as that
fixture.

When a real provider exists in a later stage, a **pipeline** candidate
with `isSynthetic=False` and a non-stub `ai_provider` will pass this
guard without a Stage 7 rewrite. A legacy `optimize_product` row from
that same provider still cannot pipeline-publish: it lacks
`pipelineCandidateVersion`.

---

## 18. Image-analysis policy

| Question | Decision |
|---|---|
| Every preview runs analysis? | Yes (`preview` only) |
| Reuse identical bytes/URL? | No. Always call `analyse_product_images` |
| `fetchFailed` / `decodeFailed` pipeline-fatal? | No |
| `checksOnly` acceptable if provider unconfigured? | Yes; warning; proposals null |
| Synthetic caption/alt previewable? | Yes, on the internal DTO / `analysis` JSON |
| Approve writes `ProductImage.alt_text`? | **No** |
| Publish uses analysis proposals? | **No** — live merchant `alt_text` only |
| Duplicate / blurry block publish? | No. Advisory warnings only |

Stage 6 security (SSRF, pixel cap, stream cap) is unchanged. Stage 7
does not import `ImageFetcher` directly.

---

## 19. Failure / transaction semantics

No service in this stage calls `commit`. The HTTP/Celery session
(Stage 8 / existing publish) owns the transaction.

| Step | Persistence if this step fails | Network |
|---|---|---|
| Analyse (inside `preview`) | Rolled back with the preview transaction | Outbound HTTPS to image URLs (SSRF-safe) |
| Generate/score | No new `AI_GENERATED` row; last active version unchanged; `ai_status` unchanged on the pipeline path | None beyond stub `complete` |
| Approve | No activate; previous active version remains | None |
| Publish | Approval already committed in a **previous** request. Shopify failure follows existing `publish_product` semantics: first-create rolls back `StoreListing`; existing listing ERROR write is itself rolled back when the exception leaves the request (current behaviour, not changed) | Shopify REST |

Do not wrap analyse+generate+approve+publish in one method. Stage 8 will
expose them as separate requests. Tests may call them sequentially in
one session; production callers must not expect cross-network atomicity.

Approve is local-only. Publish failure does **not** deactivate the
approved pipeline candidate.

---

## 20. Idempotency / retry semantics

| Operation | Retry |
|---|---|
| `preview` | Not idempotent. Each call analyses + creates a **new** inactive pipeline version. Safe to retry; prior inactive candidates remain history |
| `get_preview` | Read-only. Idempotent |
| `approve` | Idempotent no-op when the exact pipeline row is already active (§12.2 step 7), even if `expected_updated_at` is the pre-approval token. Inactive siblings from the same old token are stale, not retries |
| `publish` | Delegates to existing adopt-by-handle + `uq_store_listings_tenant_store_product` + row lock. Retry after Shopify/timeout uses the same overlay and the current `expected_updated_at` |

No duplicate `StoreListing`. No second Shopify product when handle adopt
works (existing A-04 behaviour).

---

## 21. Tenant isolation

All reads go through `TenantScopedRepository` subclasses already used by
the composed services. Stage 7 adds no unscoped repository.

Required tests (SQL compilation and/or integration):

- Own product: preview/approve/publish succeed (publish with
  non-synthetic pipeline fixture).
- Foreign product id: `NotFoundError` (indistinguishable from missing).
- Foreign `version_id` (other tenant or other product): `NotFoundError`
  from `get_by_id_for_product`.
- Foreign `store_id`: existing readiness/store lookup 404.
- `ProductPipelineService` never queries `ProductVersion` /
  `ProductImage` / `StoreListing` except via those repositories.

No new auth middleware. Stage 8 will use `RequireAdmin` on new routes,
matching optimize/publish.

---

## 22. Persistence / migration decision

**No migration.** Head remains `0033`.

| Need | Existing representation |
|---|---|
| Inactive pipeline candidate | `ProductVersion.active=False` plus `content.pipelineCandidateVersion == 1` |
| Active approved pipeline AI | same marker, `active=True`, plus `Product.ai_*` cache |
| Legacy optimize version | `AI_GENERATED` **without** `pipelineCandidateVersion` |
| Generation fingerprint | `content.pipelineSourceUpdatedAt` (pipeline rows only) |
| Synthetic flag | `content.isSynthetic` plus existing `ai_provider` (pipeline rows only) |
| Image evidence | `ProductImage.analysis` |
| Channel listing synced | `StoreListing.status` — **not** version provenance |
| Stale merchant edit | `Product.updated_at` |

A pipeline-status enum, `approved_at` column, or
`StoreListing.product_version_id` would be a migration. Not added.
Publish remains an operation.

---

## 23. Internal DTO shapes

Defined in §11 (`PipelinePreview`, `PipelineListingView`,
`PipelineCheckItem`) and §14 (`ShopifyListingOverlay`).
`ImageAnalysisReport` / `PublishReadinessResult` are reused, not wrapped
in public pydantic models.

Stage 8 maps these to camelCase response schemas. Stage 7 does not.
Stage 7 does not add `pipelineCandidateVersion` to `ProductVersionRead`
(wire-invisible JSONB, same pattern as unused content keys today).

`ShopifyListingOverlay` lives next to the publisher
(`app/integrations/shopify/sync.py` or a small sibling module imported by
it) so `publish_product` does not import `app.services.product_pipeline`
(layering: integrations must not import the pipeline service).

The pipeline service **may** import `ShopifySyncService` and
`ShopifyListingOverlay`, same as `PublishReadinessService` already
imports `ShopifySyncService`.

---

## 24. Exact service signatures

```python
# app/services/product_optimization.py
class ProductOptimizationService:
    async def _generate_version(
        self,
        product_id: uuid.UUID,
        *,
        tone: str,
        requested_by_user_id: uuid.UUID | None,
        as_pipeline_candidate: bool,
    ) -> tuple[Product, ProductVersion]: ...

    async def generate_candidate(
        self,
        product_id: uuid.UUID,
        *,
        tone: str = "professional",
        requested_by_user_id: uuid.UUID | None,
    ) -> ProductVersion: ...

    async def optimize_product(  # existing contract preserved
        self,
        product_id: uuid.UUID,
        *,
        tone: str = "professional",
        requested_by_user_id: uuid.UUID | None,
    ) -> tuple[Product, ProductVersion]: ...

    async def activate_version(
        self, product_id: uuid.UUID, version_id: uuid.UUID
    ) -> Product:
        """Activate ORIGINAL or legacy AI versions.

        Refuses pipelineCandidateVersion == 1 with
        details.reason = pipeline_candidate_requires_approval.
        """

    async def list_versions(self, product_id: uuid.UUID) -> list[ProductVersion]: ...


# app/services/product_pipeline.py
class ProductPipelineService(BaseService):
    def __init__(self, session: AsyncSession) -> None: ...

    async def preview(
        self,
        product_id: uuid.UUID,
        *,
        store_id: uuid.UUID | None = None,
        tone: str = "professional",
        requested_by_user_id: uuid.UUID | None,
    ) -> PipelinePreview: ...

    async def get_preview(
        self,
        product_id: uuid.UUID,
        *,
        version_id: uuid.UUID,
        store_id: uuid.UUID | None = None,
    ) -> PipelinePreview: ...

    async def approve(
        self,
        product_id: uuid.UUID,
        *,
        version_id: uuid.UUID,
        expected_updated_at: datetime,
    ) -> Product: ...

    async def publish(
        self,
        product_id: uuid.UUID,
        *,
        store_id: uuid.UUID,
        version_id: uuid.UUID,
        expected_updated_at: datetime,
    ) -> dict[str, Any]: ...
```

`ProductPipelineService` constructs `ImageAnalysisService`,
`ProductOptimizationService`, `PublishReadinessService`, and
`ShopifySyncService` with the same session (same pattern as
`ShopifySyncService` constructing readiness internally).

Do not register new FastAPI dependencies in Stage 7.

---

## 25. File-by-file implementation plan

Do not implement in this planning change. Future implementation commits,
each independently testable:

**Commit 1 — shared generator, distinct pipeline marker**

- `backend/app/services/product_optimization.py` — add
  `_generate_version`, `generate_candidate`; `optimize_product` calls
  the core with `as_pipeline_candidate=False` then activates; FAILED
  handling stays on the optimize path.
- `activate_version` refuses `pipelineCandidateVersion == 1`.
- Tests: existing `test_product_optimization.py` still green; legacy
  optimize version has no `pipelineCandidateVersion` and is active;
  `generate_candidate` returns `active=False` with marker `1`;
  `activate_version` on that row raises
  `pipeline_candidate_requires_approval`; generate_candidate error does
  not set `FAILED`.

**Commit 2 — listing overlay (title + sanitized body only)**

- `backend/app/integrations/shopify/sync.py` — `ShopifyListingOverlay`
  with `title` and `body_html`; optional `listing_overlay`.
- Tests: overlay substitutes title and body_html; merchant SEO still
  mapped from `Product`; `None` preserves current mapping;
  tags/images/variants/handle unchanged.

**Commit 3 — preview composer**

- `backend/app/services/product_pipeline.py` — DTOs, `preview`,
  `get_preview`.
- Tests: candidate inactive with `pipelineCandidateVersion=1`; previous
  active version remains; DTO separates original vs proposal including
  raw SEO for review; `publishable is False`; merchant fields unchanged;
  no Shopify call; analysis invoked; `get_preview` of a legacy optimize
  id is `not_a_pipeline_candidate`.

**Commit 4 — exact-candidate approve**

- `approve` with lock + marker + stale / M2A / ORIGINAL guards.
- Tests: exact id; already-active no-op with original token; stale
  reject after merchant edit; foreign/wrong product 404; sibling B
  rejected after A approved; HTTP activate still refused.

**Commit 5 — pipeline publish**

- `publish` marker + active + synthetic guard + `sanitize_html` overlay.
- Tests: publisher called once; overlay title + sanitized body; merchant
  SEO/tags/images/variants preserved; `<script>` / `onerror` /
  `javascript:` / `data:` cannot reach the Shopify body; stored version
  content unchanged; `Product.description` unchanged; synthetic blocked
  before client; unapproved rejected; legacy optimize version rejected
  as `not_a_pipeline_candidate`; Shopify failure leaves approval intact.

**Commit 6 — integration + protected regressions**

- `backend/tests/integration/test_product_pipeline.py`
- `backend/tests/unit/test_product_pipeline.py`
- Protected: no new `/api/v1/` path; `ProductImageRead` still lacks
  `analysis`; Stage 4 prompt trio on **both** generate paths; Stage 5
  `score_version` import unchanged; M2A tests still green; Shopify
  publish tests still green with overlay `None`.

**Commit 7 — completion docs** (implementation closeout, not this PR)

- `docs/PHASE_9_STAGE_7_COMPLETION.md`, progress/changelog/roadmap.

Expected new files at implementation:

- `backend/app/services/product_pipeline.py`
- `backend/tests/unit/test_product_pipeline.py`
- `backend/tests/integration/test_product_pipeline.py`

No workflow, Docker, dependency, or frontend files.

---

## 26. Test matrix

### A. Pipeline ordering and provenance

- `preview` then inspect: candidate `active=False`;
  `pipelineCandidateVersion == 1`; `Product.ai_status` still previous;
  `publishable is False`.
- `publish` without `approve` → `candidate_not_approved`.
- `preview` → `approve` → `publish` (non-synthetic **pipeline** fixture)
  → overlay used; listing synced for this call.
- `POST /products/{id}/optimize` produces an **active** version **without**
  `pipelineCandidateVersion`.
- Pipeline publish of that legacy optimize version →
  `not_a_pipeline_candidate`.
- `POST /products/{id}/versions/{id}/activate` on a pipeline candidate →
  `pipeline_candidate_requires_approval`; row stays inactive.
- `ProductPipelineService.approve` can activate that same candidate.
- Publish succeeds only after that approval, and only for a non-synthetic
  pipeline fixture.

### B. Preview

- New candidate inactive; previously approved or legacy-active version
  remains `active`.
- DTO contains candidate id, number, score keys, image report,
  `is_synthetic`, `approval_expected_updated_at`, raw SEO proposal.
- `Product.title` / `description` / SEO / tags / `alt_text` unchanged.
- No `StoreListing` write; Shopify client not constructed.
- Analyse ran (images have `analysis` JSON).
- `get_preview` does not create another version.
- `get_preview` / `PipelinePreview` do not claim the candidate is the
  listing's published version from `StoreListing`.

### C. Approval

- Exact `version_id` becomes active; cache fields match that content.
- Re-approve same id with the **pre-approval** token: success, no extra
  version, cache not rewritten.
- Re-approve same id with the post-approve token: also success no-op.
- After merchant `update_draft`, old inactive candidate → `stale_preview`.
- Generate A and B from the same source token; approve A; approve B →
  `stale_preview`; active remains A; no extra version created.
- Foreign tenant version id → 404.
- Version belonging to product B used with product A → 404.
- Missing `expected_updated_at` → 422.
- Stale `expected_updated_at` on an **inactive** candidate → 409
  `draft_version_stale`.
- `ORIGINAL` → `original_not_approvable`.
- Legacy optimize version → `not_a_pipeline_candidate` on pipeline
  approve; `activate_version` still works for that unmarked row.

### D. Publish

- `publish_product(..., listing_overlay=...)` invoked once.
- Shopify `title` from version; `body_html` is sanitized generated
  description; SEO metafields from merchant `seo_title` /
  `seo_description`; tags/images/variants from merchant.
- Merchant `seo_title` / `seo_description` unchanged in the database.
- Candidate raw SEO remains on the version and in preview.
- Generated keywords never become `Product.tags` or `meta_keywords`.
- Unique listing constraint still holds on retry.
- Forced Shopify error after approve: candidate remains active;
  `optimized_*` remain.
- Retry uses existing idempotent path.
- Merchant `POST /integrations/shopify/publish` without overlay still
  sends `Product.title`.
- Successful pipeline publish is not readable later as "this version id
  is stored on StoreListing."

### E. Sanitizer on overlay

- Generated description containing `<script>alert(1)</script>` does not
  appear in the Shopify `body_html` payload.
- `onerror` / `onclick` attributes are removed.
- `javascript:` and `data:` URL values do not survive.
- Allowed formatting (`<p>`, `<strong>`) survives.
- Stored `ProductVersion.content["description"]` still contains the raw
  generated string.
- `Product.description` is unchanged.

### F. Image analysis

- `succeeded` / `checksOnly` / `fetchFailed` / `decodeFailed` fixtures
  do not abort preview.
- Blur/duplicate → warnings, `publishable` not blocked by them.
- `alt_text` unchanged.
- `ProductImageRead` schema test from Stage 6 still passes.

### G. Provider

- Stub pipeline preview/approve allowed; pipeline publish raises
  `synthetic_publish_blocked`.
- No test name or docstring claims a live model.

### H. Regression

- Stage 4: execution log after `optimize_product` still exactly the
  three generation prompts (existing assertion). Same trio on
  `generate_candidate`.
- Stage 5: `score_version` module still has no `app.ai` import.
- Stage 6: SSRF/pixel/cap tests unchanged; analyse still not an HTTP
  route.
- M2A: `test_draft_editor_concurrency.py` green.
- Shopify: existing publish/readiness/idempotency tests green.
- API: grep that Stage 7 adds no router include.
- Frontend: `git diff -- frontend` empty for the implementation PR.

---

## 27. Protected regressions

| Protection | How Stage 7 preserves it |
|---|---|
| Optimize HTTP auto-activates | `optimize_product` still activates unmarked rows; tests stay |
| Preview → Approve → Publish | Marker required; legacy optimize and `activate_version` cannot mint or activate a pipeline candidate |
| Merchant/supplier fields | `_apply_active_version` still does not write them |
| Merchant SEO on channel | Overlay has no SEO fields; publisher keeps `Product.seo_*` |
| Image alt | analyse/approve/publish never assign it |
| Unsanitized model HTML | `sanitize_html` at overlay build; content row unchanged |
| M2A 409 | first approve and publish compare `updated_at`; draft PATCH untouched |
| Cross-tenant 404 | repositories + `get_by_id_for_product` |
| Publish idempotency | unchanged `publish_product` core |
| Stub not presented as live AI | synthetic publish block + `[STUB-AI]` content |
| No Stage 8 leak | no schemas/routers |
| No Stage 9 leak | no tasks |
| No Stage 10 leak | no frontend |

Stage 8/10 must wire "Approve preview" to `ProductPipelineService.approve`,
never to `POST .../versions/{id}/activate`.

---

## 28. Acceptance criteria

Stage 7 is done when all of the following are true:

1. `ProductPipelineService.preview` creates an inactive candidate with
   `pipelineCandidateVersion == 1` and a DTO with `publishable is False`.
2. `optimize_product` still auto-activates and does **not** write
   `pipelineCandidateVersion`.
3. `approve` requires exact `version_id`, marker `1`, and (when inactive)
   matching `expected_updated_at` plus `pipelineSourceUpdatedAt`.
4. Already-active exact pipeline id is a no-op (no extra version, no
   cache rewrite).
5. `activate_version` refuses pipeline candidates.
6. `publish` without prior pipeline approve of that id fails.
7. `publish` of a legacy optimize version fails as
   `not_a_pipeline_candidate`.
8. `publish` of synthetic pipeline content fails before any Shopify
   client call.
9. `publish` of a non-synthetic approved **pipeline** candidate
   delegates overlay of sanitized title+description; merchant
   title/description/SEO columns unchanged; Shopify SEO still merchant.
10. No new HTTP route, no frontend change, no migration, no `main`
    change. `StoreListing` is not treated as version provenance.
11. Quality gates: `ruff check`, `ruff format --check`, `mypy app`,
    `pytest` (implementation PR).
12. Independent review of the implementation: BLOCKER 0, HIGH 0,
    MEDIUM 0.

This planning document is accepted when an independent review of **the
plan** is BLOCKER 0, HIGH 0, MEDIUM 0, and the planning PR is merged to
`develop`. Implementation must not start from an unreviewed plan.

---

## 29. Risks / LOW residuals

Plan self-review after remediation: BLOCKER 0, HIGH 0, MEDIUM 0.

LOW (accepted, not elevated):

1. **Image/variant draft routes do not bump `Product.updated_at`.**
   Existing M2A. Pipeline stale guard therefore does not see image-only
   edits. Publish sends live images anyway; alt is never auto-written.
2. **`ProductVersionRepository.activate` remains a persistence helper**
   that does not itself inspect `pipelineCandidateVersion`. Business
   refusal lives on `activate_version` and `approve`. A new service that
   called the repository directly would bypass that; Stage 7 adds no
   such caller, and tests enumerate the three production call sites.
3. **`ProductVersionRepository.next_version_number` does not use
   `_base_query`.** Pre-existing. Callers only pass ids from
   tenant-scoped `get_by_id_or_raise`. Out of Stage 7 scope.
4. **Listing `ERROR` status on Shopify failure is rolled back** with the
   request exception. Pre-existing `publish_product` behaviour. Stage 7
   does not invent a nested commit to persist it.
5. **Stage 4 SEO remains an opaque blob on the version.** Stage 7 keeps
   it on the preview DTO and does not publish it. A later structured
   SEO contract is out of scope, not a Stage 7 overlay feature.
6. **First-ever `_generate_version` may bump `updated_at` once** when
   creating ORIGINAL. Fingerprint is captured after that flush. Callers
   must use the DTO token, not a token loaded before preview.
7. **Draft editor does not refresh `savedUpdatedAt` after optimize.**
   Pre-existing. Stage 7 adds no UI. Stage 10 must invalidate draft
   queries after pipeline approve.
8. **Stage 6 `is_global` multicast/NAT64 residual** still applies to
   analyse-during-preview. Not re-opened here.
9. **Pipeline `preview` is not idempotent** (new version each call).
   Intentional so retries cannot silently reuse a failed half-write.
   History can accumulate inactive rows; that is the existing version
   model.
10. **No durable candidate→listing provenance.** `StoreListing.synced`
    is channel sync, not "this ProductVersion was published." Callers
    must not infer otherwise. A future migration can add it; Stage 7
    does not.

LOW #2 from the first plan draft ("version-history activate omits
fingerprint") is **removed**: `activate_version` now refuses pipeline
candidates. LOW #5 from the first draft ("Stub SEO opacity, overlay
will send it later") is **removed as a residual of the overlay plan**:
Stage 7 does not overlay AI SEO at all (M1).

---

## 30. Stage 8 / 9 / 10 boundaries

| Stage | Owns | Must not do in Stage 7 |
|---|---|---|
| 8 | Request/response schemas, endpoints, auth dependencies, camelCase wire for `PipelinePreview` | Claiming StoreListing records the published version id |
| 9 | Celery bulk optimize/preview, progress, retries, in-flight `ProductAIStatus` | — |
| 10 | AI Product Studio, visual diff, approval UI, bulk UI; must call pipeline approve not `activate_version` | — |

Stage 8 recommended endpoint shape (not built now): preview POST,
get-preview GET, approve POST, pipeline-publish POST — all wrapping
`ProductPipelineService`. Existing `/optimize` stays until Stage 10
migrates the Optimize button. Stage 8 must not expose
`pipelineCandidateVersion` as a client-supplied field; it is server
written.

Stage 9 must not call `optimize_product` if the product requirement is
preview-before-approve; it should call `preview` / `approve` /
`publish`. That decision is Stage 9's. Stage 7 keeps `optimize_product`
for current HTTP.

---

## 31. Claude return checkpoint

Claude has not reviewed Stage 6 or Stage 7.

CLAUDE RETURN REVIEW CHECKPOINT:
All commits from Stage 5 takeover onward require a fresh Claude
end-to-end review when Claude becomes available again.

---

## Architecture choice (preview vs auto-activation)

**Chosen: Option B**, with a **private** shared `_generate_version`
core and a **distinct** pipeline origin marker.

Option A (make `optimize_product` leave candidates inactive) would break:

- `POST /products/{id}/optimize` 201 (`version.active`, `aiStatus`)
- `useOptimizeProduct` and the Optimize button on drafts + product table
- `test_product_optimization.py` activation assertions

Option B keeps that contract. `generate_candidate` is pipeline-only and
writes `pipelineCandidateVersion=1`. `optimize_product` calls the same
private core with `as_pipeline_candidate=False`, then auto-activates an
unmarked row. Pipeline approve/publish refuse unmarked rows.
`activate_version` refuses marked rows.

Internals share prompt execution and `score_version` so generation
cannot drift. The marker is what prevents Preview → Approve → Publish
from being skipped via the legacy HTTP shortcuts.

Option C (new version table or pipeline-status enum) needs a migration
the JSONB marker + `active` flag already make unnecessary.
