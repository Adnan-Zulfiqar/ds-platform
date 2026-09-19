# Phase 9 Stage 7 — Pipeline: plan

Analyse → Generate → Score → Preview → Approve → Publish.

Written before implementation and checked against the code as it stood at
`develop` `0a26a121bf6c03a9e03e1b5ee2b9b69de5c63012` (Stage 6 docs closeout
merged; post-merge CI run 35458334556, 10/10). Remediated after independent
plan review of `78674f83df921cbe19e4b239988e175f7a812f3b` (H1, H2, M1–M4)
and a second independent review of
`49e5b26a96620fa9be5bab32c6d1ae5b3b67e977` (publish TOCTOU, fingerprint vs
approved state, fail-open provenance, output bounds, quality-baseline DTO).
Final contract cleanup after review of
`5a878aa13f22bd0b3c5dc14edcddbd1412474f39` (parser module location,
`ProductVersionRepository` path).
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
| Planning status | PLANNING / AWAITING ACCEPTANCE REVIEW |
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
5. **Checking `version.active` before `lock_for_update` is a TOCTOU:**
   another transaction can activate B after A was observed active and
   before Shopify is called.

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
| Strict `parse_pipeline_candidate_metadata` in `product_optimization.py`; publish locks Product before the active-state decision | Copying `PublishReadinessService` rules; `product_optimization` importing `product_pipeline`; a new `product_version.py` repository |
| Fail-closed title/body bounds (512 / 255 / 64_000); no truncation | Turning Stage 5 quality score into a publish blocker |
| Tests proving order, marker, bypass refusal, TOCTOU, stale-vs-approved, sanitizer, fail-closed provenance, overlay, bounds, regressions | Migration, new tables, `ProductAIStatus` values |
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
- No `app/repositories/product_version.py`. `ProductVersionRepository`
  stays in `app/repositories/product.py`.
- No `product_optimization → product_pipeline` import, including delayed
  imports.

---

## 7. Pipeline state machine

Persisted Stage 7 candidate states are derived from existing rows plus
the JSONB marker. No new enum column. No durable "Published" state.

| Name | Meaning in this repository |
|---|---|
| No candidate | No row for which `parse_pipeline_candidate_metadata` succeeds, or the caller has not selected one |
| Previewed | An `AI_GENERATED` row exists with valid pipeline metadata, `active=False`. `Product.ai_*` still reflects whichever version is currently active (legacy optimize, a previous pipeline approval, or ORIGINAL) |
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
- `publish` of a version whose pipeline metadata fails the strict parser
  (`not_a_pipeline_candidate`) — includes every legacy `optimize_product`
  row, even if it is active and later non-synthetic.
- `publish` while the named pipeline `version_id` is not `active`
  **after** the Product row lock and a fresh version read
  (`candidate_not_approved`).
- `approve` of `ORIGINAL`.
- `approve` of a version whose pipeline metadata fails the strict parser.
- `approve` of a version whose `product_id` does not match.
- `approve` of an **inactive** pipeline candidate when
  `Product.updated_at != expected_updated_at` or
  `Product.updated_at != metadata.source_updated_at`.
- `publish` when metadata `is_synthetic is True` or stripped
  `ai_provider == "stub"` (`synthetic_publish_blocked`).
- `publish` when `ai_provider` is missing, empty, or otherwise unverified
  (`ai_provenance_unverified`).
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
class PipelineQualityBaseline:
    version_number: int
    score: int


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
    quality_baseline: PipelineQualityBaseline | None
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

`quality_baseline` mirrors Stage 5's persisted object
`{"versionNumber": 1, "score": ...}` (`ProductVersionRead` already keeps
both fields). Do not collapse it to a bare integer.

`original` is always the live `Product` merchant state (`title`,
`description`, `seo_title`, `seo_description`, `meta_keywords` as
keywords, `tags`). It is not the ORIGINAL snapshot if the merchant has
since edited.

`proposal` is the candidate version's `title` / `description` /
`seoTitle` / `seoDescription` / `keywords` **as stored data**. Preview
does not sanitize into HTML and does not treat the description as a
storefront document. `proposal.tags` is `()` — generated keywords are
not merchant tags. AI SEO is review-only; Stage 7 does not publish it.
An invalid-length proposal is still shown; blockers record why it cannot
be approved or published.

`candidate_active` is `False` after `preview()`. `get_preview` reports
the row's real flag.

`approval_expected_updated_at` equals live `Product.updated_at` at DTO
build time. Stage 8 sends this as `expectedUpdatedAt` into `approve` for
a **first** approval of an inactive candidate. After approval, publish
uses the **post-approve** (or later merchant-edit) token from live
`Product.updated_at`, not `source_updated_at`.

**Stale vs publishable (inactive vs active):**

- **Inactive** pipeline candidate: `source_updated_at != Product.updated_at`
  → pipeline blocker `stale_preview`. `publishable` is False because the
  row is inactive **and** because it is stale.
- **Active** approved pipeline candidate: do **not** compare
  `source_updated_at` to `Product.updated_at`. Approval itself bumps
  `updated_at`, so that comparison would be permanently true and is not
  a staleness signal. The candidate stays approved until another version
  is activated. Merchant edits after approval do not auto-revoke
  approval (no extra persistence). Publish freshness is
  `expected_updated_at` via existing readiness/publisher M2A.

`publishable` is `True` only when **all** of: strict metadata parse
succeeds, candidate is **active**, metadata `is_synthetic is False`,
`ai_provider` is a non-empty non-stub string, title is a non-blank
string of length ≤ 255, sanitized description length ≤ 64_000,
`channel_readiness` is present and `can_publish`, and `pipeline_blockers`
is empty. A fresh `preview()` therefore always has `publishable=False`
because the candidate is inactive. That is the proof that preview is not
approval.

`publishable` does **not** mean "this version is the last one synced to
Shopify". It means this **active** candidate would pass pipeline publish
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

`get_preview` requires a successful `parse_pipeline_candidate_metadata`
(§23). Otherwise `ValidationError` `details.reason = "not_a_pipeline_candidate"`.
It reads current `ProductImage.analysis` as stored. It does not refetch
images. It does not read `StoreListing` to claim a published version.
After approval, `get_preview` of the now-active id does **not** emit
`stale_preview` merely because `Product.updated_at` moved.

---

## 12. Stale-preview / concurrency contract

Approval identifies an **exact** `version_id`. "Latest version" is not a
valid selector.

### 12.1 What the candidate records

Only pipeline rows (`as_pipeline_candidate=True`) write:

- `pipelineCandidateVersion`: exact JSON integer `1` (never `true`)
- `pipelineSourceUpdatedAt`: `Product.updated_at` immediately before
  `create`, as offset-aware ISO-8601
- `isSynthetic`: exact JSON boolean, OR of the three execution flags

`pipelineSourceUpdatedAt` is an **approval fingerprint for inactive
candidates only**. After the row is activated, it is historical: it
records the merchant `updated_at` the copy was generated from. It is
**not** compared to live `Product.updated_at` to decide whether an
already-approved candidate is still approved.

Merchant title/description/SEO/slug/tag edits go through
`ProductService.update_draft` / `update_product` and bump
`Product.updated_at`. For an **inactive** candidate that is sufficient
to refuse approval. No extra hash column. No migration.

Image/variant routes do not bump `Product.updated_at` (existing M2A).
Stage 7 does not change that. Approval does not write image alt; publish
reads **live** image/variant rows, so a later image edit is what gets
sent, not the preview-time set. Documented residual §29.

### 12.2 Approve preconditions

`approve` takes a row lock (`ProductRepository.lock_for_update(product_id)`
with default `timeout_ms=None`, so it waits for an in-flight publish
rather than surfacing `ShopifyPublishBusyError`) then:

1. Product exists in tenant (`NotFoundError` otherwise). Refresh the
   locked Product (`populate_existing` is already on `lock_for_update`).
2. Fresh `get_by_id_for_product(product_id, version_id,
   populate_existing=True)` — foreign or wrong-product version →
   `NotFoundError` (404, not 403).
3. `version.source is AI_GENERATED`. `ORIGINAL` → `ValidationError`
   with `details.reason = "original_not_approvable"`.
4. `parse_pipeline_candidate_metadata(version.content)` — any missing or
   malformed field → `ValidationError`
   `details.reason = "not_a_pipeline_candidate"`.
5. `expected_updated_at` is required. `None` → `ValidationError`
   (same posture as `update_draft`).
6. **If this exact row is already `active`:** return the current
   `Product` immediately. Do not compare `expected_updated_at` to live
   `updated_at`. Do not compare `pipelineSourceUpdatedAt`. Do not call
   `versions.activate`. Do not call `_apply_active_version`. Do not
   create a version. This is the lost-response retry: the client may
   still hold the pre-approval token.
7. If the version is inactive:
   `expected_updated_at` must equal live `product.updated_at` else
   `ConflictError` `details.reason = "draft_version_stale"`.
   Live `product.updated_at` must equal `metadata.source_updated_at`
   else `ConflictError` `details.reason = "stale_preview"`. Caller must
   `preview()` again.
8. Approval storage safety (§13.2): title is a `str`, non-blank after
   strip, length ≤ 512. Else `ValidationError`
   `details.reason = "candidate_content_invalid"`. Do not truncate.
9. `versions.activate` then `_apply_active_version` then flush.

### 12.3 First approval wins for that source token

Two inactive pipeline candidates A and B generated from the same
`Product.updated_at`:

- Approving A succeeds, writes `Product.ai_*` / `optimized_*`, flushes,
  and **bumps** `Product.updated_at`.
- Approving B then fails `stale_preview`: B is still **inactive** and
  still carries the old `pipelineSourceUpdatedAt`. B remains historical
  and inactive.
- The caller must run a new `preview()` to mint a candidate against the
  new product token.

A is **not** stale after that bump. `get_preview(A)` must not emit
`stale_preview` solely because approval moved `updated_at`.

Do not state that the later activate wins. The later sibling is stale.

Same-candidate concurrent retries: lock serialises. First activates.
Second sees `active=True` and takes the no-op path in step 6.

`requested_by_user_id` is not persisted on approve (the version already
has `created_by_user_id` from generate).

After a successful first approve, `Product.updated_at` moves. Pipeline
`publish` must use the **current** live token (post-approve, or later
merchant-edit token after the client reloads), not the preview's
generation `source_updated_at`.

### 12.4 Merchant edit after approval

No extra persistence, so approval is **not** auto-revoked.

1. Candidate A is approved (`active=True`).
2. `Product.updated_at` moved because of that approval.
3. Merchant later edits merchant-owned fields; `updated_at` moves again.
4. Pipeline publish with the old token → existing M2A 409
   `draft_version_stale` inside `require_publishable`.
5. If the caller reloads and supplies the **new** `expected_updated_at`,
   publishing still-active A is allowed.
6. That publish sends live merchant tags/images/variants/SEO together
   with A's already-approved AI title and sanitized description overlay.

Stronger "merchant edit revokes AI approval" needs stored approval
provenance. Stage 7 does not add it and must not imply that guarantee.

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
to activate a parsed pipeline candidate. It calls
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
pipeline candidate. Detection uses the same strict parser as approve
and publish, not `content.get("pipelineCandidateVersion") == 1`
(`True == 1` in Python).

That parser, `PipelineCandidateMetadata`, and the key-presence helper
live in `app/services/product_optimization.py` next to version create /
activate. `activate_version` calls them **locally**. It does **not**
import `ProductPipelineService`. Do not duplicate the parser. Do not
use a delayed/local import to hide a circular service dependency.

If `content_has_any_pipeline_metadata_key` is false (none of the three
keys present), the row is legacy/ORIGINAL and keeps today's
activate/rollback behaviour.

If any of `pipelineCandidateVersion`, `pipelineSourceUpdatedAt`, or
`isSynthetic` is present and the parser fails → `ValidationError`
`not_a_pipeline_candidate` (do not activate a corrupt row).

If the parser succeeds:

```python
raise ValidationError(
    "This version is a pipeline preview and must be approved "
    "through the product pipeline.",
    details={"reason": "pipeline_candidate_requires_approval"},
)
```

`ProductVersionRepository.activate` stays a persistence helper. It is
not a public business API. Production callers in Stage 7:

- `optimize_product` — unmarked rows only
- `activate_version` — unmarked rows only, after the refusal above
- `ProductPipelineService.approve` — parsed pipeline rows only, after §12.2

Does not publish. Does not call Shopify.

### 13.2 Approval storage bounds

`Product.optimized_title` is `String(512)`, matching
`ProductUpdateRequest.title` in `app/schemas/product.py`. Before first
approval, the candidate title must be a `str`, non-blank after strip,
and `len(title) <= 512`. Otherwise `ValidationError`
`details.reason = "candidate_content_invalid"`. Do not truncate. Do not
rely on a database `DataError`.

A 256–512 character title may be approved into the AI cache. It is
**not** pipeline-publishable (Stage 5 documents Shopify's 255-character
title limit). Preview records `candidate_title_not_publishable` so
`publishable` stays False.

Description length is not an approval-storage blocker (`optimized_description`
is `Text`). Publish still caps sanitized HTML at `DESCRIPTION_MAX_LENGTH`.

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

Immediately before constructing `ShopifyListingOverlay`, **after**
§14.2 has accepted `validated_title`. This is the **same**
`safe_body_html` already length-checked in §14.2 — not a second
sanitize pass. Do not coerce a non-string title with `str(...)`.

```python
raw_description = version.content.get("description")
if not isinstance(raw_description, str):
    raw_description = ""
safe_body_html = sanitize_html(raw_description) or ""
overlay = ShopifyListingOverlay(
    title=validated_title,
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

### 14.2 Channel output bounds

Do not silently truncate model output. Preview may show an oversize
proposal; first approval and pipeline publish fail closed.

Constants live in `app/services/product_pipeline.py` so Stage 7 does not
invent a migration or a new schema module:

```python
from app.schemas.product import DESCRIPTION_MAX_LENGTH  # 64_000

#: Matches Product.optimized_title and ProductUpdateRequest.title.
PIPELINE_APPROVAL_TITLE_MAX = 512
#: Shopify product-title limit documented by Stage 5 D1 in
#: app.services.optimization_quality._score_title.
PIPELINE_PUBLISH_TITLE_MAX = 255
```

Before `ShopifySyncService` is called (after lock + fresh read):

- Candidate title is a `str` (do not coerce), non-blank after strip,
  `len(title) <= PIPELINE_PUBLISH_TITLE_MAX`
  else `ValidationError` `details.reason = "candidate_title_not_publishable"`
- `len(safe_body_html) <= DESCRIPTION_MAX_LENGTH` else `ValidationError`
  `details.reason = "candidate_description_too_long"`

That accepted title is `validated_title` for §14.1. Compute
`safe_body_html` once, check its length, and pass **that same string**
to the overlay. Do not sanitize a second time. No Shopify client call
on validation failure. Stored `ProductVersion.content` is not modified.
Quality score remains advisory; it is not a length or publish blocker.

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

Pinned order. A candidate observed active **before** the Product lock
must never be sent to Shopify.

1. Acquire the tenant-scoped Product lock first:
   `ProductRepository.lock_for_update(product_id,
   timeout_ms=PUBLISH_LOCK_TIMEOUT_MS)` from
   `app.integrations.shopify.sync` (30_000 ms). Timeout raises the
   existing `ShopifyPublishBusyError`. `None` → `NotFoundError`.
2. Fresh `ProductVersion` read for the exact `(product_id, version_id)`
   with `populate_existing=True` (extend
   `ProductVersionRepository.get_by_id_for_product` in
   `app/repositories/product.py` with that flag, same pattern as
   `ProductRepository.lock_for_update`). Do not create
   `app/repositories/product_version.py`. Do not trust a previously
   loaded identity-map instance. Missing → `NotFoundError`.
3. Under that lock validate:
   - `source is AI_GENERATED` else `candidate_not_approved`
   - `parse_pipeline_candidate_metadata` else `not_a_pipeline_candidate`
   - `version.active is True` else `candidate_not_approved`
   - provenance (§17): missing/empty/non-string `ai_provider` →
     `ai_provenance_unverified`; then `metadata.is_synthetic is True` or
     stripped `ai_provider == "stub"` → `synthetic_publish_blocked`
   - publish bounds (§14.2) before any overlay build
4. Build the sanitized overlay (§14.1).
5. `return await ShopifySyncService(self.session).publish_product(
       store_id=store_id,
       product_id=product_id,
       expected_updated_at=expected_updated_at,
       listing_overlay=overlay,
   )` on the **same** `AsyncSession` / transaction.

`ShopifySyncService.publish_product` may take the Product lock again.
Re-locking the same row in the same transaction is acceptable and keeps
the merchant-publish path unchanged. Do not create a second publisher.
M2A `expected_updated_at` continues to be enforced inside
`require_publishable`.

Race semantics:

- If B's approval **commits before** pipeline publish acquires the
  Product lock: the fresh re-read sees A `active=False` →
  `candidate_not_approved`; Shopify client is not constructed.
- If pipeline publish **acquires the lock first**: A may publish;
  competing approval cannot commit its Product update until this
  transaction releases the lock.
- Never publish an overlay for a candidate that was active only in a
  pre-lock identity-map copy.

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
| Legacy/malformed pipeline metadata | pipeline blocker | `not_a_pipeline_candidate` |
| Inactive candidate whose `source_updated_at !=` live `updated_at` | pipeline blocker | `stale_preview` |
| Active approved candidate whose `source_updated_at !=` live `updated_at` | **not a blocker** — fingerprint is inactive-only | n/a |
| `metadata.is_synthetic is True` or stripped `ai_provider == "stub"` | pipeline blocker for publish | `synthetic_publish_blocked` |
| `ai_provider` missing, empty, or non-string | pipeline blocker for publish | `ai_provenance_unverified` |
| Title blank or not a str, or length > 512 | pipeline blocker (also refuses first approve) | `candidate_content_invalid` |
| Title length 256–512 | pipeline blocker for publish; approval still allowed | `candidate_title_not_publishable` |
| Sanitized body_html length > 64_000 | pipeline blocker for publish | `candidate_description_too_long` |
| Failed generate | no DTO; `AIError` | n/a |
| Image `fetchFailed` / `decodeFailed` | pipeline **warning** | `image_analysis_incomplete` |
| Image `checksOnly` | pipeline **warning** | `image_analysis_checks_only` |
| Blur / duplicate flags | pipeline **warning** | `image_blurry` / `image_duplicate` |
| `store_id` omitted on preview | `channel_readiness is None`; `publishable=False`; evaluate is not called | n/a |
| Channel blockers | from `channel_readiness.blockers` | existing codes |

`publishable` on the DTO is the AND of pipeline blockers empty, strict
metadata parse, candidate **active**, `is_synthetic is False`, verified
non-stub `ai_provider`, title ≤ 255, sanitized body ≤ 64_000, and
`channel_readiness.can_publish`. Quality score is advisory only and is
never a publish blocker.

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

Guard in `ProductPipelineService.publish` **after** the Product lock and
fresh version read, **before** overlay build and **before**
`ShopifySyncService.publish_product` (no provider HTTP).

Use `parse_pipeline_candidate_metadata` first. Then fail closed. Check
provider type/emptiness **before** the stub equality so a missing
provider cannot skip into a channel-publishable state:

```python
provider = version.ai_provider
if not isinstance(provider, str) or provider.strip() == "":
    raise ValidationError(
        "AI provider provenance is missing or unverified.",
        details={"reason": "ai_provenance_unverified"},
    )
if metadata.is_synthetic is True or provider.strip() == "stub":
    raise ValidationError(
        "Synthetic AI content cannot be published to a sales channel.",
        details={"reason": "synthetic_publish_blocked"},
    )
```

Publish is allowed only when `metadata.is_synthetic is` exactly `False`
**and** `ai_provider` is a non-empty string **and** the stripped
provider is not `"stub"`. Missing `isSynthetic` never means "real AI";
the parser already rejected it as `not_a_pipeline_candidate`.

Rationale: Stage 7 overlay is the first code path that would send AI
copy to Shopify. Stub output is labelled, but a merchant can still press
Publish. Blocking is the only way to keep "no live AI quality claim"
true at the channel boundary. Merchant-only publish of `Product.title`
is unaffected.

Tests that need a successful overlay publish construct a **pipeline**
candidate with strict metadata (`pipelineCandidateVersion` integer `1`,
offset-aware `pipelineSourceUpdatedAt`, `isSynthetic` boolean `False`)
and `ai_provider="test"`. They do not claim a live model. Legacy
optimize rows are never used as that fixture.

When a real provider exists in a later stage, a **pipeline** candidate
with exact `isSynthetic=False` and a non-empty non-stub `ai_provider`
will pass this guard without a Stage 7 rewrite. A legacy
`optimize_product` row from that same provider still cannot
pipeline-publish: it fails the strict parser.

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
| `approve` | Idempotent no-op when the exact pipeline row is already active (§12.2 step 6), even if `expected_updated_at` is the pre-approval token. Inactive siblings from the same old token are stale, not retries |
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
| Inactive pipeline candidate | `ProductVersion.active=False` plus strict `parse_pipeline_candidate_metadata` success |
| Active approved pipeline AI | same parsed metadata, `active=True`, plus `Product.ai_*` cache |
| Legacy optimize version | `AI_GENERATED` with **none** of the three pipeline keys |
| Generation fingerprint | `content.pipelineSourceUpdatedAt` — approval fingerprint for **inactive** rows only |
| Merchant-edit-after-approve | live `Product.updated_at` via M2A on publish; approval is not revoked |
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
`PipelineCheckItem`, `PipelineQualityBaseline`) and §14
(`ShopifyListingOverlay`). `ImageAnalysisReport` /
`PublishReadinessResult` are reused, not wrapped in public pydantic
models.

Shared fail-closed parser used by `get_preview`, `approve`, `publish`,
and `activate_version` detection. **Location:
`app/services/product_optimization.py`.** That is the module that
creates and activates `ProductVersion` rows. `ProductPipelineService`
imports these names from there. `product_optimization.py` never imports
`product_pipeline.py`.

```python
# app/services/product_optimization.py

@dataclass(frozen=True, slots=True)
class PipelineCandidateMetadata:
    source_updated_at: datetime
    is_synthetic: bool


def parse_pipeline_candidate_metadata(
    content: object,
) -> PipelineCandidateMetadata:
    """Raise ValidationError(not_a_pipeline_candidate) unless all three
    pipeline keys are present with exact types.
    """


def content_has_any_pipeline_metadata_key(content: object) -> bool:
    """True if any of pipelineCandidateVersion / pipelineSourceUpdatedAt /
    isSynthetic is present.

    Distinguishes unmarked legacy/ORIGINAL rows from a corrupt pipeline
    JSON object. Does not validate types; `parse_pipeline_candidate_metadata`
    does that.
    """
```

A valid pipeline candidate requires **all three** keys:

| Key | Rule |
|---|---|
| `pipelineCandidateVersion` | `type(x) is int` (bool is **not** accepted; `True == 1` in Python) and value exactly `1` |
| `pipelineSourceUpdatedAt` | `type(x) is str`, parseable offset-aware ISO-8601 datetime. Missing, non-string, malformed, or **naive** datetime → reject |
| `isSynthetic` | `type(x) is bool`. Missing, `"false"`, `0`, `1` → reject |

Malformed/missing metadata is `not_a_pipeline_candidate` **before**
approval and **before** channel publication. Do not silently assume
missing `isSynthetic` means real AI.

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

Pinned import direction (no cycles, no delayed imports to hide one):

```
product_pipeline.py
  → product_optimization.py
      → repositories/product.py
  → integrations/shopify/sync.py
```

Never `product_optimization → product_pipeline`. Never
`integrations/shopify/sync.py → product_pipeline`.

`ProductVersionRepository.get_by_id_for_product` in
`app/repositories/product.py` gains `populate_existing: bool = False`.
Publish and approve pass `True`. Do not add
`app/repositories/product_version.py`. Do not move the class.

---

## 24. Exact service signatures

```python
# app/services/product_optimization.py

@dataclass(frozen=True, slots=True)
class PipelineCandidateMetadata:
    source_updated_at: datetime
    is_synthetic: bool


def parse_pipeline_candidate_metadata(
    content: object,
) -> PipelineCandidateMetadata: ...


def content_has_any_pipeline_metadata_key(content: object) -> bool: ...


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

        Uses parse_pipeline_candidate_metadata. Parsed pipeline rows
        raise pipeline_candidate_requires_approval. Partial/malformed
        pipeline keys raise not_a_pipeline_candidate.
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


# app/repositories/product.py  — existing file; do not add product_version.py
class ProductVersionRepository:
    async def get_by_id_for_product(
        self,
        *,
        product_id: uuid.UUID,
        version_id: uuid.UUID,
        populate_existing: bool = False,
    ) -> ProductVersion | None: ...
```

`ProductPipelineService` constructs `ImageAnalysisService`,
`ProductOptimizationService`, `PublishReadinessService`, and
`ShopifySyncService` with the same session (same pattern as
`ShopifySyncService` constructing readiness internally). It imports
`ProductOptimizationService`, `PipelineCandidateMetadata`,
`parse_pipeline_candidate_metadata`, and
`content_has_any_pipeline_metadata_key` from
`app.services.product_optimization`.

Do not register new FastAPI dependencies in Stage 7.

---

## 25. File-by-file implementation plan

Do not implement in this planning change. Future implementation commits,
each independently testable:

**Commit 1 — shared generator, distinct pipeline marker**

- `backend/app/services/product_optimization.py` — add
  `PipelineCandidateMetadata`, `parse_pipeline_candidate_metadata`,
  `content_has_any_pipeline_metadata_key`, `_generate_version`,
  `generate_candidate`; `optimize_product` calls the core with
  `as_pipeline_candidate=False` then activates; FAILED handling stays
  on the optimize path.
- `activate_version` uses those local helpers: parsed pipeline rows
  raise `pipeline_candidate_requires_approval`; any pipeline key present
  with a failed parse raises `not_a_pipeline_candidate`. No import of
  `product_pipeline`.
- Tests: existing `test_product_optimization.py` still green; parser
  unit tests (bool `True` marker, missing keys, malformed timestamp,
  `isSynthetic` string `"false"`); legacy optimize version has no
  `pipelineCandidateVersion` and is active; `generate_candidate`
  returns `active=False` with marker `1`; `activate_version` on that
  row raises `pipeline_candidate_requires_approval`; generate_candidate
  error does not set `FAILED`.

**Commit 2 — listing overlay (title + sanitized body only)**

- `backend/app/integrations/shopify/sync.py` — `ShopifyListingOverlay`
  with `title` and `body_html`; optional `listing_overlay`.
- Tests: overlay substitutes title and body_html; merchant SEO still
  mapped from `Product`; `None` preserves current mapping;
  tags/images/variants/handle unchanged.

**Commit 3 — preview composer**

- `backend/app/services/product_pipeline.py` — DTOs including
  `PipelineQualityBaseline`, `preview`, `get_preview`. Consumes
  `parse_pipeline_candidate_metadata` / `PipelineCandidateMetadata`
  from `product_optimization.py`. Does **not** redefine them.
- Tests: candidate inactive with integer marker `1`; previous active
  version remains; DTO separates original vs proposal including raw SEO
  and baseline `{version_number, score}`; `publishable is False`;
  merchant fields unchanged; no Shopify call; analysis invoked;
  `get_preview` of a legacy optimize id is `not_a_pipeline_candidate`;
  after approve, `get_preview` is not `stale_preview` solely because
  `updated_at` moved.

**Commit 4 — exact-candidate approve**

- `approve` with lock + strict parser + inactive-only fingerprint /
  M2A / ORIGINAL / title≤512 guards.
- Tests: exact id; already-active no-op with original token; stale
  reject after merchant edit of an **inactive** candidate;
  foreign/wrong product 404; sibling B rejected after A approved; HTTP
  activate still refused; title >512 → `candidate_content_invalid` with
  no DataError.

**Commit 5 — pipeline publish**

- `publish` locks Product first (`PUBLISH_LOCK_TIMEOUT_MS`), fresh
  version read, strict parser, fail-closed provenance, length bounds,
  `sanitize_html` overlay, then existing publisher on the same session.
- `backend/app/repositories/product.py` —
  `ProductVersionRepository.get_by_id_for_product(...,
  populate_existing=True)`. Do not add `product_version.py`.
- Tests: publisher called once; overlay title + sanitized body; merchant
  SEO/tags/images/variants preserved; `<script>` / `onerror` /
  `javascript:` / `data:` cannot reach the Shopify body; stored version
  content unchanged; `Product.description` unchanged; synthetic blocked
  before client; missing provider → `ai_provenance_unverified`;
  unapproved rejected; legacy optimize version rejected as
  `not_a_pipeline_candidate`; Shopify failure leaves approval intact;
  TOCTOU: B commits before lock → A rejected, client not called;
  length 256 title blocked at publish; 64_001 sanitized body blocked.

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
- `backend/tests/unit/test_pipeline_candidate_metadata.py` (parser
  only; no DB; imports from `product_optimization`)

Expected **modified** files at implementation (not an exhaustive list
of every line, the modules that must change):

- `backend/app/services/product_optimization.py`
- `backend/app/repositories/product.py`
- `backend/app/integrations/shopify/sync.py`

Do **not** create `backend/app/repositories/product_version.py`.
Do **not** move `ProductVersionRepository`.

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
- DTO contains candidate id, number, score keys, `PipelineQualityBaseline`
  with `version_number` and `score`, image report, `is_synthetic`,
  `approval_expected_updated_at`, raw SEO proposal.
- Inactive current candidate is not `stale_preview`.
- `Product.title` / `description` / SEO / tags / `alt_text` unchanged.
- No `StoreListing` write; Shopify client not constructed.
- Analyse ran (images have `analysis` JSON).
- `get_preview` does not create another version.
- `get_preview` / `PipelinePreview` do not claim the candidate is the
  listing's published version from `StoreListing`.
- After approve, `get_preview(active id)` does **not** become
  `stale_preview` merely because approval changed `updated_at`.

### C. Approval

- Exact `version_id` becomes active; cache fields match that content.
- Re-approve same id with the **pre-approval** token: success, no extra
  version, cache not rewritten.
- Re-approve same id with the post-approve token: also success no-op.
- After merchant `update_draft`, old **inactive** candidate →
  `stale_preview`.
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
- Title length > 512 → `candidate_content_invalid`; no DB `DataError`.
- Blank title → `candidate_content_invalid`.

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
- Old post-approval token after a later merchant edit → 409
  `draft_version_stale`.
- Reloaded current token → still-active candidate may publish (live
  merchant tags/images/SEO + approved overlay).
- Title length 255 → publish eligible (remaining guards passing).
- Title length 256 → `candidate_title_not_publishable`; no Shopify
  client.
- Sanitized body length 64_000 → allowed; 64_001 →
  `candidate_description_too_long`; stored raw content unchanged.

### D2. Publish vs concurrent activation (TOCTOU)

- Candidate A is active. Between the pipeline-publish call setup and
  the Shopify client call, candidate B is approved in another
  transaction.
- If B commits before A acquires `lock_for_update`: A is rejected
  `candidate_not_approved`; Shopify client is not constructed.
- If A owns the lock first: A's publish finishes before B can commit
  its Product update.
- Never publish an inactive/stale A overlay after B has committed.
- Lock timeout still raises `ShopifyPublishBusyError`.

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

### G. Provider and provenance

- Parser unit tests import from `app.services.product_optimization`, not
  from `product_pipeline`.
- Stub pipeline preview/approve allowed; pipeline publish raises
  `synthetic_publish_blocked`.
- Marker `True` (bool) instead of integer `1` →
  `not_a_pipeline_candidate`.
- Marker missing → `not_a_pipeline_candidate`.
- `pipelineSourceUpdatedAt` malformed → `not_a_pipeline_candidate`.
- `isSynthetic` missing → `not_a_pipeline_candidate`.
- `isSynthetic` string `"false"` → `not_a_pipeline_candidate`.
- `ai_provider is None` → `ai_provenance_unverified`; no Shopify client.
- `ai_provider == ""` → `ai_provenance_unverified`.
- `ai_provider == "stub"` even with `isSynthetic is False` →
  `synthetic_publish_blocked`.
- Non-stub provider + exact `isSynthetic is False` → eligible subject
  to remaining guards.
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
- Import graph: `product_optimization.py` has no `product_pipeline`
  import. No `app/repositories/product_version.py`.

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
| Oversized AI copy | fail-closed 512/255/64_000 bounds; no truncate |
| Fail-open JSONB | strict parser in `product_optimization.py`; `True == 1` rejected |
| No service import cycle | pipeline → optimization only; no delayed import |
| No new repository module | `ProductVersionRepository` stays in `repositories/product.py` |
| Publish TOCTOU | lock Product before active check; fresh version read |
| M2A 409 | inactive approve and publish compare `updated_at`; draft PATCH untouched |
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
3. `approve` requires exact `version_id`, strict metadata parse, and
   (when inactive) matching `expected_updated_at` plus
   `metadata.source_updated_at`. Active approved rows are not fingerprint-
   stale.
4. Already-active exact pipeline id is a no-op (no extra version, no
   cache rewrite).
5. `activate_version` refuses parsed pipeline candidates using helpers
   in `product_optimization.py` (no import of `product_pipeline`).
6. `publish` acquires `lock_for_update` **before** the active-state
   decision and re-reads the version with `populate_existing`.
7. `publish` without prior pipeline approve of that id fails.
8. `publish` of a legacy optimize version fails as
   `not_a_pipeline_candidate`.
9. `publish` of synthetic or unverified-provenance pipeline content
   fails before any Shopify client call.
10. `publish` of a non-synthetic approved **pipeline** candidate
    delegates overlay of sanitized title+description; merchant
    title/description/SEO columns unchanged; Shopify SEO still merchant.
11. Title >512 cannot approve; title 256–512 cannot pipeline-publish;
    sanitized body >64_000 cannot pipeline-publish; no truncation.
12. No new HTTP route, no frontend change, no migration, no `main`
    change. `StoreListing` is not treated as version provenance.
    Parser/types live in `product_optimization.py`. No
    `product_version.py` repository module.
13. Quality gates: `ruff check`, `ruff format --check`, `mypy app`,
    `pytest` (implementation PR).
14. Independent review of the implementation: BLOCKER 0, HIGH 0,
    MEDIUM 0.

This planning document is accepted when an independent review of **the
plan** is BLOCKER 0, HIGH 0, MEDIUM 0, and the planning PR is merged to
`develop`. Implementation must not start from an unreviewed plan.

---

## 29. Risks / LOW residuals

Plan self-review after remediation: BLOCKER 0, HIGH 0, MEDIUM 0.

LOW (accepted, not elevated):

1. **Image/variant draft routes do not bump `Product.updated_at`.**
   Existing M2A. The inactive-candidate approval fingerprint therefore
   does not see image-only edits. Publish sends live images anyway; alt
   is never auto-written.
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
11. **Merchant edit after approval does not revoke the AI candidate.**
    Intentional with no extra persistence. Publish of the still-active
    overlay is allowed with a fresh M2A token; live merchant fields go
    with it (§12.4).
12. **Titles of 256–512 characters can be approved into `optimized_title`
    but cannot pipeline-publish.** Preview surfaces
    `candidate_title_not_publishable`. Regeneration is required for the
    channel path. Stage 5's quality score remains advisory.
13. **Description length is not an approval-storage blocker** because
    `optimized_description` is `Text`. An oversize body is refused at
    pipeline publish after sanitization, not at approve.
14. **Nested Product `FOR UPDATE`** when `ShopifySyncService` re-locks
    the same row in the same transaction is accepted so the merchant
    publish path stays unchanged.

LOW #2 from the first plan draft ("version-history activate omits
fingerprint") is **removed**: `activate_version` now refuses pipeline
candidates. LOW #5 from the first draft ("Stub SEO opacity, overlay
will send it later") is **removed as a residual of the overlay plan**:
Stage 7 does not overlay AI SEO at all.

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
`activate_version` refuses marked rows via helpers in
`product_optimization.py`. `ProductPipelineService` imports that module;
the reverse import is forbidden. `ProductVersionRepository` stays in
`app/repositories/product.py`. Pipeline `publish` locks the Product row
before the active-state decision and fail-closes on malformed
provenance. Overlay HTML is sanitized; AI SEO is not published;
title/body length is fail-closed.

Internals share prompt execution and `score_version` so generation
cannot drift. The marker is what prevents Preview → Approve → Publish
from being skipped via the legacy HTTP shortcuts.

Option C (new version table or pipeline-status enum) needs a migration
the JSONB marker + `active` flag already make unnecessary.
