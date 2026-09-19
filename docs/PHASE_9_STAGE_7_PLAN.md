# Phase 9 Stage 7 — Pipeline: plan

Analyse → Generate → Score → Preview → Approve → Publish.

Written before implementation and checked against the code as it stood at
`develop` `0a26a121bf6c03a9e03e1b5ee2b9b69de5c63012` (Stage 6 docs closeout
merged; post-merge CI run 35458334556, 10/10). Where this plan narrows what
[PHASE_9_PLAN.md](PHASE_9_PLAN.md) §3 originally sketched for Stage 7, it
says so.

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
active row per product. Rollback is the same `activate` call on an older id.

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

Publication truth is `StoreListing.status == synced`, not `Product.status`.
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

---

## 4. Problem statement

Stage 7 must orchestrate Analyse → Generate → Score → Preview → Approve →
Publish **in that order**, as in-process services.

Three mismatches with the code as it exists:

1. **Preview is missing.** `optimize_product` activates on success. A
   generated version is already the live AI cache before any merchant
   approval.
2. **Approved AI copy never reaches Shopify.** The publisher reads
   `Product.title` / `Product.description`, not `optimized_*`.
3. **A naive overlay would be the first path that can send `[STUB-AI]`
   text to a real shop.** Today merchant publish cannot do that, because
   it ignores the AI cache.

Stage 7 must introduce preview-before-approve **without** changing the
existing optimize HTTP contract, must project approved copy onto the
existing publisher without rewriting merchant/supplier columns, and must
refuse to send synthetic copy to a sales channel.

---

## 5. Exact Stage 7 scope

Stage 7 implements a new domain service that composes existing services.

| In scope | Out of scope |
|---|---|
| `ProductPipelineService` (preview, approve, publish) | HTTP routes / OpenAPI / Stage 8 schemas |
| Extract `generate_candidate` that creates an **inactive** `AI_GENERATED` version | Changing `POST /products/{id}/optimize` response or auto-activation |
| Exact-candidate approval with stale guard | Celery bulk / progress (Stage 9) |
| Optional `ShopifyListingOverlay` argument on the **existing** publisher | A second Shopify client or GraphQL productSet |
| Call `ImageAnalysisService.analyse_product_images` from preview | Writing `ProductImage.alt_text` |
| Compose `PublishReadinessService` without copying its rules | Frontend / AI Studio (Stage 10) |
| Internal frozen DTOs | `ProductImageRead.analysis` |
| Tests proving order, stale reject, synthetic publish block, overlay, regressions | Migration, new tables, `ProductAIStatus` values |
| | Live providers, prompt template edits, `quality_scorer` wiring |
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

---

## 7. Pipeline state machine

States are derived from existing rows. No new enum column.

| Name | Meaning in this repository |
|---|---|
| No candidate | No `AI_GENERATED` version, or caller has not selected one |
| Previewed | An `AI_GENERATED` row exists with `active=False` and `content.pipelineSourceUpdatedAt` set; `Product.ai_*` still reflects the previously active version |
| Approved | That exact row is `active=True`; `_apply_active_version` has synced the AI cache |
| Published | `StoreListing` for `(store_id, product_id)` is `synced` **and** the last pipeline publish applied this version's overlay. Merchant-only publish (no overlay) is a different path and does not count as pipeline publish |

Illegal transitions the implementation must refuse:

- `publish` while the named `version_id` is not the active `AI_GENERATED` row.
- `approve` of `ORIGINAL`.
- `approve` of a version whose `product_id` does not match the path product.
- `approve` when `Product.updated_at != expected_updated_at`.
- `approve` when `Product.updated_at != content.pipelineSourceUpdatedAt`
  unless the named version is already active (idempotent re-approve).
- `publish` when `content.isSynthetic is True` (or provider is stub — §17).

`optimize_product` is **not** a node on this state machine. It remains the
legacy generate-and-activate shortcut for the existing HTTP/UI callers.

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

New method on `ProductOptimizationService`:

```python
async def generate_candidate(
    self,
    product_id: uuid.UUID,
    *,
    tone: str = "professional",
    requested_by_user_id: uuid.UUID | None,
) -> ProductVersion:
```

Behaviour:

1. `get_by_id_or_raise(product_id)`.
2. `_ensure_original_snapshot` (unchanged). If this is the first version
   ever, ORIGINAL is created `active=True` and `_apply_active_version`
   may bump `Product.updated_at` once. Capture `pipelineSourceUpdatedAt`
   **after** that flush.
3. Same `_build_variables` and the same three `test_render` calls as today.
   Prompt names stay `product_title_generator`,
   `product_description_generator`, `seo_optimizer`.
4. Any failure: raise `AIError`. **Do not** set `Product.ai_status = FAILED`
   inside `generate_candidate`. Last approved cache stays as it was.
5. `score_version` unchanged.
6. `create(..., active=False)` with content:

```python
{
    **generated,  # title, description, seoTitle, seoDescription, keywords
    **candidate.as_content(baseline=baseline),
    "pipelineSourceUpdatedAt": product.updated_at.isoformat(),
    "isSynthetic": bool(
        title_execution.is_synthetic
        or description_execution.is_synthetic
        or seo_execution.is_synthetic
    ),
}
```

7. Return the inactive row. Do not call `activate`. Do not call
   `_apply_active_version`.

`optimize_product` becomes:

```python
try:
    version = await self.generate_candidate(
        product_id, tone=tone, requested_by_user_id=requested_by_user_id
    )
except AIError:
    product = await self.products.get_by_id_or_raise(product_id)
    product.ai_status = ProductAIStatus.FAILED
    await self.flush()
    raise
activated = await self.versions.activate(
    product_id=product_id, version_id=version.id
)
self._apply_active_version(product, activated)
await self.flush()
return product, activated
```

That preserves today's HTTP/UI/tests: generate still auto-activates **on
the optimize path only**. Failed optimize still records `FAILED`.

A failed `generate_candidate` from the pipeline does **not** record
`FAILED` and does **not** change the last approved version.

Previously approved (active) version remains active while a new candidate
sits inactive. Multiple inactive historical candidates may exist; approval
names one id.

---

## 10. Score contract

Unchanged Stage 5: `score_version(content, product)` in
`backend/app/services/optimization_quality.py`. Called inside
`generate_candidate` before `create`. Rubric version 1. `quality_scorer`
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
`seoTitle` / `seoDescription` / `keywords`. `proposal.tags` is `()` —
generated keywords are not merchant tags.

`candidate_active` is `False` after `preview()`. `get_preview` reports
the row's real flag.

`approval_expected_updated_at` equals live `Product.updated_at` at DTO
build time for a fresh preview (and equals `source_updated_at` when the
candidate is not stale). Stage 8 will send this as `expectedUpdatedAt`
into `approve`.

`publishable` is `True` only when **all** of: candidate is active,
not stale, not synthetic, `channel_readiness` is present and
`can_publish`, and `pipeline_blockers` is empty. A fresh `preview()`
therefore always has `publishable=False` because the candidate is
inactive. That is the proof that preview is not approval.

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
    """Analyse, generate an inactive candidate, return a preview DTO.

    Does not activate. Does not publish. Does not commit.
    """

async def get_preview(
    self,
    product_id: uuid.UUID,
    *,
    version_id: uuid.UUID,
    store_id: uuid.UUID | None = None,
) -> PipelinePreview:
    """Compose a preview from an existing candidate. No analyse, no generate.
    """
```

`preview` order: analyse → `generate_candidate` → compose DTO (readiness
if `store_id` is not None). One caller transaction. If generate raises,
analyse is rolled back with it.

`get_preview` reads current `ProductImage.analysis` as stored. It does
not refetch images.

---

## 12. Stale-preview / concurrency contract

Approval identifies an **exact** `version_id`. "Latest version" is not a
valid selector.

### 12.1 What the candidate records

`content.pipelineSourceUpdatedAt` = `Product.updated_at` immediately
before `create`, as ISO-8601. That is the generation fingerprint.

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
4. `content.pipelineSourceUpdatedAt` present. Missing (legacy
   optimize-created rows from before this key) → `ValidationError`
   with `details.reason = "not_a_pipeline_candidate"`. Legacy activate
   remains `ProductOptimizationService.activate_version`.
5. `expected_updated_at` is required. `None` → `ValidationError`
   (same posture as `update_draft`).
6. `product.updated_at != expected_updated_at` → `ConflictError`
   with `details.reason = "draft_version_stale"` (same code as publish
   readiness).
7. If the version is **already active**: return the product. Idempotent.
   Do not re-compare `pipelineSourceUpdatedAt` (activation itself already
   bumped `updated_at`).
8. If the version is inactive:
   `product.updated_at` must equal parsed `pipelineSourceUpdatedAt`.
   Mismatch → `ConflictError` with `details.reason = "stale_preview"`.
   Caller must `preview()` again.
9. `versions.activate` then `_apply_active_version` then flush.

Two concurrent approves of the **same** candidate: lock serialises. First
activates and bumps `updated_at`. Second either sees already-active
(idempotent) if it holds the pre-activation token under the lock before
the bump is visible — under `FOR UPDATE` the second waiter re-reads
after the first commits. Across HTTP requests the second uses a stale
`expected_updated_at` and gets 409. That is correct.

Two concurrent approves of **different** candidates generated from the
same `updated_at`: lock serialises; the later one wins the active flag
if its fingerprint still matches (it will, because generate_candidate
does not bump `Product.updated_at`). Both candidates are valid for that
merchant snapshot. Explicit ids, last committed activate wins. No extra
"must be max(version_number)" rule.

`requested_by_user_id` is not persisted on approve (the version already
has `created_by_user_id` from generate).

Regeneration is required after `stale_preview`. Re-approving the old id
is refused.

Interaction with M2A: `expected_updated_at` **is** the M2A token. Approve
is another compare-and-swap on `Product.updated_at`. It does not bypass
draft PATCH rules.

After a successful first approve, `Product.updated_at` moves. Publish
must use the **post-approve** token from the returned `Product`, not the
preview's `approval_expected_updated_at`.

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

Approve means: make this candidate the active `ProductVersion` and sync
the AI **cache** via existing `_apply_active_version`.

| Field | Written? |
|---|---|
| `ProductVersion.active` | Yes — this row true, previous false |
| `Product.ai_status` | `optimized` |
| `Product.ai_version` | candidate `version_number` |
| `Product.ai_provider` / `ai_last_generated_at` | Yes |
| `Product.optimized_title` / `optimized_description` | Yes, from version content |
| `Product.title` / `description` / `supplier_*` | **No** |
| Merchant SEO / tags / slug | **No** |
| `ProductImage.alt_text` | **No** |
| `Product.status` | **No** |
| New `ProductVersion` row | **No** — activates the existing candidate |

ORIGINAL cannot be pipeline-approved. Rollback to ORIGINAL stays
`ProductOptimizationService.activate_version` (existing HTTP).

Inactive older AI pipeline candidates can be approved if the stale
guard passes (same `updated_at` as generation).

Already-active exact id is idempotent (§12.2 step 7).

Does not publish. Does not call Shopify.

---

## 14. Publish projection contract

Approved AI copy reaches Shopify **only** through an overlay on the
existing publisher. Merchant/supplier columns stay as they are.

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class ShopifyListingOverlay:
    title: str
    body_html: str
    seo_title: str | None
    seo_description: str | None
```

| Shopify field | Pipeline publish source |
|---|---|
| `title` | overlay.title ← version.content["title"] |
| `body_html` | overlay.body_html ← version.content["description"] |
| `metafields_global_title_tag` | overlay.seo_title if not None else merchant `seo_title` |
| `metafields_global_description_tag` | overlay.seo_description if not None else merchant `seo_description` |
| `tags` | **merchant** `Product.tags` (never version `keywords`) |
| images | **live** `ProductImage.url` / `alt_text` (never analysis proposals) |
| variants, prices, SKU, inventory, weight | **merchant** variant/product fields |
| handle / slug | **merchant** slug / deterministic handle |
| vendor, product_type, Shopify REST status | **merchant** |

Meta keywords / `search_topics` remain unsent (current publisher).

If overlay `seo_title` / `seo_description` are empty strings, treat as
None and fall back to merchant SEO.

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

When provided: substitute only the four overlay fields in `product_body`
construction. Readiness, lock, adopt-by-handle, `StoreListing` upsert,
and error handling stay as they are.

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
2. Version must be `active` and `AI_GENERATED`. Else `ValidationError`
   `details.reason = "candidate_not_approved"`.
3. Synthetic guard (§17).
4. Build `ShopifyListingOverlay` from version content.
5. `return await ShopifySyncService(self.session).publish_product(
       store_id=store_id,
       product_id=product_id,
       expected_updated_at=expected_updated_at,
       listing_overlay=overlay,
   )`.

Do not duplicate REST mapping, handle adopt, or listing upsert.

Existing merchant publish without overlay remains valid: a merchant can
still push `Product.title` to Shopify without ever running the pipeline.
Pipeline publish is the only path that sends approved AI copy.

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
| No/inactive candidate (fresh preview) | blocker for **pipeline publish**, not for merchant publish | `candidate_not_approved` |
| `stale_preview` (source token ≠ live `updated_at`) | pipeline blocker | `stale_preview` |
| `isSynthetic` | pipeline blocker for publish | `synthetic_publish_blocked` |
| Failed generate | no DTO; `AIError` | n/a |
| Image `fetchFailed` / `decodeFailed` | pipeline **warning** | `image_analysis_incomplete` |
| Image `checksOnly` | pipeline **warning** | `image_analysis_checks_only` |
| Blur / duplicate flags | pipeline **warning** | `image_blurry` / `image_duplicate` |
| `store_id` omitted on preview | `channel_readiness is None`; `publishable=False`; evaluate is not called | n/a |
| Channel blockers | from `channel_readiness.blockers` | existing codes |

`publishable` on the DTO is the AND of pipeline blockers empty, candidate
active, not synthetic, not stale, and `channel_readiness.can_publish`.

`ProductPipelineService.publish` still calls the existing
`require_publishable` **inside** `publish_product`. Do not re-implement
destination/currency/connection checks.

AI optimisation remains **not** required for merchant publish.

---

## 17. Synthetic-provider policy

Current factory resolves to `StubProvider`. Every `complete` /
`analyse_image` result has `is_synthetic=True` and text prefixed
`[STUB-AI]`.

| Action | Allowed with synthetic candidate? |
|---|---|
| Generate | Yes |
| Preview | Yes |
| Approve | Yes — studio can mark a stub version active in the AI cache |
| Publish to Shopify (pipeline overlay) | **No** |

Guard in `ProductPipelineService.publish` **before**
`ShopifySyncService.publish_product` is called (no provider HTTP):

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

Tests that need a successful overlay publish construct a candidate with
`isSynthetic=False` and `ai_provider="test"` (or a fake non-stub
provider in unit tests). They do not claim a live model.

When a real provider exists in a later stage, `isSynthetic=False` and a
non-stub `ai_provider` will pass this guard without a Stage 7 rewrite.

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
approved candidate.

---

## 20. Idempotency / retry semantics

| Operation | Retry |
|---|---|
| `preview` | Not idempotent. Each call analyses + creates a **new** inactive version. Safe to retry; prior inactive candidates remain history |
| `get_preview` | Read-only. Idempotent |
| `approve` | Idempotent for the same `(product_id, version_id)` when already active and `expected_updated_at` matches live token |
| `publish` | Delegates to existing adopt-by-handle + `uq_store_listings_tenant_store_product` + row lock. Retry after Shopify/timeout uses the same overlay and the current `expected_updated_at` |

No duplicate `StoreListing`. No second Shopify product when handle adopt
works (existing A-04 behaviour).

---

## 21. Tenant isolation

All reads go through `TenantScopedRepository` subclasses already used by
the composed services. Stage 7 adds no unscoped repository.

Required tests (SQL compilation and/or integration):

- Own product: preview/approve/publish succeed.
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
| Inactive candidate | `ProductVersion.active=False` (already created that way before activate) |
| Active approved AI | `active=True` + `Product.ai_*` cache |
| Generation fingerprint | new keys on `ProductVersion.content` JSONB (Stage 3/4/5 established this extension point) |
| Synthetic flag | `content.isSynthetic` plus existing `ai_provider` |
| Image evidence | `ProductImage.analysis` |
| Channel listing | `StoreListing` |
| Stale merchant edit | `Product.updated_at` |

A pipeline-status enum or `approved_at` column would duplicate `active`
and the cache. Not added.

---

## 23. Internal DTO shapes

Defined in §11 (`PipelinePreview`, `PipelineListingView`,
`PipelineCheckItem`) and §14 (`ShopifyListingOverlay`).
`ImageAnalysisReport` / `PublishReadinessResult` are reused, not wrapped
in public pydantic models.

Stage 8 maps these to camelCase response schemas. Stage 7 does not.

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

    async def activate_version(  # unchanged
        self, product_id: uuid.UUID, version_id: uuid.UUID
    ) -> Product: ...

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

**Commit 1 — inactive candidate extract**

- `backend/app/services/product_optimization.py` — add
  `generate_candidate`; `optimize_product` wraps it + activate + FAILED
  handling.
- Tests: existing `test_product_optimization.py` still green; new tests
  that `generate_candidate` returns `active=False`, does not change
  `optimized_*`, does not set `FAILED` on error.

**Commit 2 — listing overlay**

- `backend/app/integrations/shopify/sync.py` — `ShopifyListingOverlay` +
  optional `listing_overlay`.
- Tests: overlay substitutes title/body/SEO; `None` preserves current
  mapping; tags/images/variants/handle unchanged.

**Commit 3 — preview composer**

- `backend/app/services/product_pipeline.py` — DTOs, `preview`,
  `get_preview`.
- Tests: candidate inactive; previous active version remains; DTO
  separates original vs proposal; `publishable is False`; merchant
  fields unchanged; no Shopify call; analysis invoked.

**Commit 4 — exact-candidate approve**

- `approve` with lock + stale / M2A / ORIGINAL guards.
- Tests: exact id; idempotent re-approve; stale reject; foreign/wrong
  product 404; concurrent edit 409; older inactive candidate remains
  a row.

**Commit 5 — pipeline publish**

- `publish` synthetic guard + overlay delegation.
- Tests: publisher called once; overlay fields; unrelated merchant data
  preserved; synthetic blocked before client; unapproved rejected;
  Shopify failure leaves approval intact (service-level: approve
  flushed in a prior transaction in the test, or two sessions).

**Commit 6 — integration + protected regressions**

- `backend/tests/integration/test_product_pipeline.py`
- `backend/tests/unit/test_product_pipeline.py`
- Protected: no new `/api/v1/` path; `ProductImageRead` still lacks
  `analysis`; Stage 4 prompt trio on generate; Stage 5 `score_version`
  import unchanged; M2A tests still green; Shopify publish tests still
  green with overlay `None`.

**Commit 7 — completion docs** (implementation closeout, not this PR)

- `docs/PHASE_9_STAGE_7_COMPLETION.md`, progress/changelog/roadmap.

Expected new files at implementation:

- `backend/app/services/product_pipeline.py`
- `backend/tests/unit/test_product_pipeline.py`
- `backend/tests/integration/test_product_pipeline.py`
- optional `backend/tests/unit/test_product_version_repository_scoping.py`
  already covers the repository; add pipeline isolation tests there or
  beside it.

No workflow, Docker, dependency, or frontend files.

---

## 26. Test matrix

### A. Pipeline ordering

- `preview` then inspect: candidate `active=False`; `Product.ai_status`
  still previous; `publishable is False`.
- `publish` without `approve` → `candidate_not_approved`.
- `preview` → `approve` → `publish` (non-synthetic fixture) → overlay
  used; listing synced.
- Calling `publish` before `preview` (no AI version) → not found or
  `candidate_not_approved`.

### B. Preview

- New candidate inactive; previously approved version remains `active`.
- DTO contains candidate id, number, score keys, image report,
  `is_synthetic`, `approval_expected_updated_at`.
- `Product.title` / `description` / SEO / tags / `alt_text` unchanged.
- No `StoreListing` write; Shopify client not constructed.
- Analyse ran (images have `analysis` JSON).
- `get_preview` does not create another version.

### C. Approval

- Exact `version_id` becomes active; cache fields match that content.
- Re-approve same id with post-approve token: success, no extra version.
- After merchant `update_draft`, old candidate → `stale_preview`.
- Foreign tenant version id → 404.
- Version belonging to product B used with product A → 404.
- Missing `expected_updated_at` → 422.
- Stale `expected_updated_at` → 409 `draft_version_stale`.
- `ORIGINAL` → `original_not_approvable`.
- Legacy optimize version without `pipelineSourceUpdatedAt` →
  `not_a_pipeline_candidate` on pipeline approve; existing
  `activate_version` still works.
- Two inactive candidates; approving the older one (same fingerprint)
  succeeds; the newer stays inactive history.

### D. Publish

- `publish_product(..., listing_overlay=...)` invoked once.
- Shopify body title/body_html/SEO from version; tags/images/variants
  from merchant.
- Unique listing constraint still holds on retry.
- Forced Shopify error after approve: candidate remains active;
  `optimized_*` remain.
- Retry uses existing idempotent path.
- Merchant `POST /integrations/shopify/publish` without overlay still
  sends `Product.title`.

### E. Image analysis

- `succeeded` / `checksOnly` / `fetchFailed` / `decodeFailed` fixtures
  do not abort preview.
- Blur/duplicate → warnings, `publishable` not blocked by them.
- `alt_text` unchanged.
- `ProductImageRead` schema test from Stage 6 still passes.

### F. Provider

- Stub preview/approve allowed; pipeline publish raises
  `synthetic_publish_blocked`.
- No test name or docstring claims a live model.

### G. Regression

- Stage 4: execution log after `optimize_product` still exactly the
  three generation prompts (existing assertion).
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
| Optimize HTTP auto-activates | `optimize_product` still activates; tests stay |
| Merchant/supplier fields | `_apply_active_version` untouched except being called from approve |
| SEO not copied onto Product | same |
| Image alt | analyse/approve/publish never assign it |
| M2A 409 | approve/publish compare `updated_at`; draft PATCH untouched |
| Cross-tenant 404 | repositories + `get_by_id_for_product` |
| Publish idempotency | unchanged `publish_product` core |
| Stub not presented as live AI | synthetic publish block + `[STUB-AI]` content |
| No Stage 8 leak | no schemas/routers |
| No Stage 9 leak | no tasks |
| No Stage 10 leak | no frontend |

Existing `POST /products/{id}/versions/{id}/activate` remains a
version-history switch **without** the pipeline stale fingerprint. That
is Stage 3's rollback contract. Stage 8/10 must not wire "Approve
preview" to that endpoint; they must call `ProductPipelineService.approve`.
A protected comment/test documents the split.

---

## 28. Acceptance criteria

Stage 7 is done when all of the following are true:

1. `ProductPipelineService.preview` creates an inactive candidate and a
   DTO with `publishable is False`.
2. `approve` requires exact `version_id` + matching
   `expected_updated_at` + matching `pipelineSourceUpdatedAt` (unless
   already active).
3. `publish` without prior approve of that id fails.
4. `publish` of synthetic content fails before any Shopify client call.
5. `publish` of a non-synthetic approved candidate delegates to
   `ShopifySyncService.publish_product` with an overlay; merchant
   title/description columns unchanged after success.
6. `POST /products/{id}/optimize` still auto-activates.
7. No new HTTP route, no frontend change, no migration, no `main` change.
8. Quality gates: `ruff check`, `ruff format --check`, `mypy app`,
   `pytest` (implementation PR).
9. Independent review of the implementation: BLOCKER 0, HIGH 0, MEDIUM 0.

This planning document is accepted when an independent review of **the
plan** is BLOCKER 0, HIGH 0, MEDIUM 0, and the planning PR is merged to
`develop`. Implementation must not start from an unreviewed plan.

---

## 29. Risks / LOW residuals

Plan self-review after writing: BLOCKER 0, HIGH 0, MEDIUM 0.

LOW (accepted, not elevated):

1. **Image/variant draft routes do not bump `Product.updated_at`.**
   Existing M2A. Pipeline stale guard therefore does not see image-only
   edits. Publish sends live images anyway; alt is never auto-written.
2. **Version-history `activate_version` omits `pipelineSourceUpdatedAt`.**
   Preserving Stage 3 rollback. Stage 8/10 constraint in §27.
3. **`ProductVersionRepository.next_version_number` does not use
   `_base_query`.** Pre-existing. Callers only pass ids from
   tenant-scoped `get_by_id_or_raise`. Out of Stage 7 scope.
4. **Listing `ERROR` status on Shopify failure is rolled back** with the
   request exception. Pre-existing `publish_product` behaviour. Stage 7
   does not invent a nested commit to persist it.
5. **Stub SEO fields are three copies of one opaque string.** Stage 4
   decision. Overlay will send that string as SEO only if a non-synthetic
   provider later returns it; synthetic publish is blocked today.
6. **First-ever `generate_candidate` may bump `updated_at` once** when
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

---

## 30. Stage 8 / 9 / 10 boundaries

| Stage | Owns | Must not do in Stage 7 |
|---|---|---|
| 8 | Request/response schemas, endpoints, auth dependencies, camelCase wire for `PipelinePreview` | — |
| 9 | Celery bulk optimize/preview, progress, retries, in-flight `ProductAIStatus` | — |
| 10 | AI Product Studio, visual diff, approval UI, bulk UI; must call pipeline approve not `activate_version` | — |

Stage 8 recommended endpoint shape (not built now): preview POST,
get-preview GET, approve POST, pipeline-publish POST — all wrapping
`ProductPipelineService`. Existing `/optimize` stays until Stage 10
migrates the Optimize button.

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

**Chosen: Option B**, with a shared `generate_candidate` helper.

Option A (make `optimize_product` leave candidates inactive) would break:

- `POST /products/{id}/optimize` 201 (`version.active`, `aiStatus`)
- `useOptimizeProduct` and the Optimize button on drafts + product table
- `test_product_optimization.py` activation assertions

Option B keeps that contract. Stage 7's new methods never auto-activate.
Internals are shared so generation/scoring cannot drift.

Option C (new version table or pipeline-status enum) needs a migration
the JSONB + `active` flag already make unnecessary.
