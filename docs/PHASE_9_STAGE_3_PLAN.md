# Phase 9 Stage 3 — Product optimization data architecture: plan

Written before writing code, per CLAUDE.md's per-phase sequence. Verified
against the current code rather than assumed: `Product` (`app/models/product.py`)
has no SEO, slug, vendor, tags, AI-status, or version columns; no
`product_versions` table exists; migrations are at `0010`. Stage 1
(`AIProvider`/`StubProvider`) and Stage 2 (`AIPrompt`/`PromptRenderer`/
`PromptExecution`/`PromptService`) are complete, tagged in commit history at
`37c7e5f` and `2a9fecc`, and unchanged by this plan.

---

## 1. Scope boundary — what this stage does and does not do

The brief is explicit: *"Do not generate AI content yet. This stage only
creates the product optimization data architecture."* Taken together with
section 3 ("connect with existing AI prompt system... use `StubProvider`")
and section 4 (record prompt used, provider used, execution status,
generated result), the correct reading is: **the pipeline is exercised
end-to-end through `StubProvider`, never through a real model.** This is the
same posture Stage 2 took for "test prompt rendering" — proving the plumbing,
not generation quality.

**What Stage 3 optimizes:** title and description only, reusing the
`product_title_generator` and `product_description_generator` prompts Stage 2
already seeded. **What it does not touch:** `seo_optimizer`, `quality_scorer`,
`image_analyzer`. Those prompts exist but stay unwired — they are the original
Phase 9 plan's Stage 4 ("generation services"), Stage 5 (quality scoring,
deliberately model-free), and Stage 6 (image analysis) respectively. Wiring
them now would be building ahead of the phase that owns them.

**Already exists, not duplicated:** `Product.brand`. The brief lists `brand`
under "Marketplace" fields to add; it has been a column since Phase 4. This
plan reuses it rather than adding a second one — CLAUDE.md: "search before
writing."

---

## 2. Product model extension

Additive columns only, all nullable or defaulted, on the existing `products`
table — no column is renamed, retyped, or dropped, and no existing row's data
changes.

| Group | Column | Type | Notes |
|---|---|---|---|
| SEO | `seo_title` | `String(512)`, null | |
| SEO | `seo_description` | `String(512)`, null | |
| SEO | `meta_keywords` | `Text`, null | Comma-separated, matching how a `<meta name="keywords">` tag is actually rendered — not an array for a value that is fundamentally one string on the way out. |
| Marketplace | `slug` | `String(255)`, null | `UniqueConstraint(tenant_id, slug)`; Postgres treats multiple `NULL`s as distinct, so unoptimized products (no slug yet) never collide. |
| Marketplace | `vendor` | `String(255)`, null | |
| Marketplace | `tags` | `JSONB list[str]`, not null, default `[]` | Matches the existing `AutomationRule.config` / `PricingRule.tiers` JSONB-list precedent. |
| AI status | `ai_status` | `Enum(ProductAIStatus)`, not null, default `not_optimized` | `not_optimized \| optimized \| failed` — see §4 for why no `pending`/`generating`. |
| AI status | `ai_last_generated_at` | `DateTime(tz)`, null | Set only on a *successful* generation. |
| AI status | `ai_provider` | `String(64)`, null | Copied from the active version's recorded provider. |
| AI status | `ai_version` | `Integer`, null | The active `ProductVersion.version_number` — denormalised for list/detail views that should not join to read it. |
| Content | `optimized_title` | `String(512)`, null | |
| Content | `optimized_description` | `Text`, null | |

**These five AI-status/content fields are a cache of the active version, not
independent state.** Activating a version (§3) always rewrites all five
together from that version's data; nothing else writes them. This is what
guarantees `Product.title`/`Product.description` — the supplier-sourced
columns — can never be overwritten by AI content (§8's security requirement):
there is no code path that assigns to them from anywhere in this stage.

---

## 3. `product_versions`

One row per version, patterned directly on Stage 2's `AIPrompt`: versions are
rows, not a nested history table; "history" is every row sharing a
`product_id`; "rollback" is activating an older version through the same
endpoint that activates a new one.

| Column | Type | Notes |
|---|---|---|
| `id`, `tenant_id`, `created_at`, `updated_at`, `deleted_at` | — | From `TenantScopedBase` |
| `product_id` | FK → `products.id`, `CASCADE` | Versions are tenant-owned data belonging to a product; unlike `PromptExecution → ai_prompts` (`SET NULL`, a reference table), this FK points at data this tenant owns, so it cascades like `ProductVariant`/`ProductImage` already do. |
| `version_number` | `Integer` | 1 is always the original snapshot; 2+ are AI-generated. `UniqueConstraint(product_id, version_number)`. |
| `source` | `Enum(ProductVersionSource)` | `original \| ai_generated` |
| `content` | `JSONB` | `{"title": ..., "description": ...}` today. JSONB rather than discrete columns so Stage 4+ can add `seoTitle`/`seoDescription`/`tags` to the shape without a migration — the same reasoning `AutomationRule.config` already established in this codebase. |
| `active` | `Boolean` | At most one `true` per `product_id`, enforced by a **partial unique index** (`WHERE active`), not application discipline alone — identical mechanism to `uq_ai_prompts_name_active` in migration `0010`. |
| `ai_provider` | `String(64)`, null | Null for `original`. |
| `prompt_execution_id` | FK → `prompt_executions.id`, `SET NULL`, null | Links a version to the exact `PromptExecution` that produced it, closing the Stage 2↔3 loop without duplicating `PromptExecution`'s own columns (provider, status, tokens, error) onto this table — DRY. Two executions (title + description) produce one version, so this points at one of them (the description execution) as the representative link; both remain independently queryable by `product_id`/`prompt_name` if ever needed. |
| `created_by_user_id` | FK → `users.id`, `SET NULL`, null | Null for the lazily-created original snapshot (see §4). |

**The original snapshot is created lazily, on first optimization, not at
import time.** `ProductImportService.import_product()` is not touched —
literally satisfying "do not rewrite existing product flows." The first call
to `POST /products/{id}/optimize` checks whether any version exists for the
product; if not, it writes version 1 (`source=original`, a snapshot of the
product's current `title`/`description`) and activates it, *then* proceeds to
generate version 2. A product that is never optimized never gets a version
row — `GET /versions` returns an empty page, which is the true state.

---

## 4. `ProductOptimizationService`

```
Product → ProductOptimizationService → PromptService.test_render(execute=True)
                                          ├─ product_title_generator
                                          └─ product_description_generator
                                        → ProductVersion (new, active)
                                        → Product's five cached AI fields
```

Reuses `PromptService.test_render` from Stage 2 rather than re-implementing
render→call→record — it already does exactly that, and CLAUDE.md's "always
extend existing modules" applies as much to Stage 2's code as to anything
older. Two calls (title, description) with variables derived from the
product's own fields (`product_title`, `category`, `brand`, `tone` — default
`"professional"`, overridable in the request body — and `features` from the
existing `description` column, defaulting to an empty string so rendering
never fails on a sparsely-populated product).

**No `pending`/`generating` status.** Like `PromptExecutionStatus` in Stage 2,
optimization here is one synchronous request — Celery-backed async
optimization is Stage 9's job, not this one's. `ai_status` has exactly two
terminal values plus the unstarted default: `not_optimized`, `optimized`,
`failed`.

**Failure handling.** `PromptService.test_render(execute=True)` does not raise
on a provider failure — Stage 2 designed it to catch `AIError` and return a
`FAILED` `PromptExecution` instead, correct for an admin "test" tool that
should always return 200 with a result. `/optimize` is not that: a caller
needs an HTTP error. So `ProductOptimizationService` inspects the returned
execution's status, and on `FAILED` sets `Product.ai_status = FAILED` (leaving
every other AI field untouched — a failed *attempt* does not erase the last
*good* result) and raises `app.ai.exceptions.AIError` with the execution's
recorded message, which the existing global handler turns into the standard
error envelope. No new exception class for one call site.

**Ownership.** "Validate product ownership" is not a separate check —
`ProductRepository.get_by_id_or_raise`, already `TenantScopedRepository`,
raises `NotFoundError` for a product belonging to another tenant. A 404, not
a 403, matching the whole platform's existing cross-tenant policy. Adding a
second, manual ownership check on top would be redundant code with its own
chance to disagree with the first.

---

## 5. API — extending, not replacing, `app/api/v1/products/router.py`

Three additions to the existing router; none of its current endpoints
change.

| Endpoint | Role | Returns |
|---|---|---|
| `GET /products/{id}/versions` | `RequireViewer` — read, matches every other product read | `Page[ProductVersionRead]` |
| `POST /products/{id}/optimize` | `RequireAdmin` — matches `import`/`sync`: changes what the workspace shows | `{ product: ProductDetailRead, version: ProductVersionRead }` |
| `POST /products/{id}/versions/{version_id}/activate` | `RequireAdmin` | `ProductDetailRead` |

---

## 6. Frontend — foundation, not the editor

Per the brief: an optimize button, a version-history view, the current active
version, and an AI-status indicator, added to the existing product table —
no new page, no editor. `types/api.ts` and `services/products.ts` gain the
new fields/hooks; two new components
(`optimize-product-button.tsx`, `product-version-history-sheet.tsx`) reuse
existing primitives (`Sheet`, `Badge`, `Alert`, `Button`) rather than
introducing new ones, matching Stage 2's frontend-restraint precedent (Stage 2
added no UI at all, since it shipped nothing to click). Playwright coverage
reuses `seedCatalogueViaApi` from `helpers/catalogue.ts` and skips cleanly
when live AliExpress OAuth cannot complete — the same, already-accepted
limitation the existing product-import Playwright suite lives with.

---

## 7. Security (§8 of the brief), addressed by construction

| Requirement | How |
|---|---|
| Tenant cannot access another tenant's versions | `ProductVersionRepository` is `TenantScopedRepository`; SQL-compile isolation test required by CLAUDE.md, plus an integration test reading real rows |
| AI content cannot overwrite supplier source | No code path assigns to `Product.title`/`Product.description` anywhere in this stage — verified by test, not just by design |
| Prompt execution logs remain isolated | Already covered by Stage 2's `PromptExecutionRepository` isolation test; unchanged here |
| No secrets stored | No column introduced by this stage can hold a credential — structural, not a matter of care, same guarantee the rest of the schema already gives |

---

## 8. Verification plan

| Layer | How |
|---|---|
| `ProductVersionRepository` tenant isolation | SQL-compile test (no DB) |
| Version creation, lazy original snapshot, rollback | Integration tests, real DB |
| Optimization service, prompt integration | Integration tests, `StubProvider` |
| Provider failure | Integration test forcing `AIProviderNotConfiguredError` via `AI_PROVIDER` override, same mechanism Stage 1's factory tests use |
| API — auth, tenant isolation, validation | Integration tests through real HTTP |
| Frontend | lint, typecheck, build |
| Playwright | New specs for the button/history/error states; skip, not fail, without live AliExpress |
| **Model output quality** | **Still cannot be verified without a key** — unchanged from Stage 1/2; every execution this stage produces carries `provider=stub`, `isSynthetic=true` |

---

## 9. Out of scope, stated rather than discovered later

- SEO/quality/image generation — Stage 4/5/6.
- Celery-backed bulk or async optimization — Stage 9.
- A full AI editor UI — Stage 10.
- Manually editing a version's content, or creating a version from anything
  other than the optimize flow — not requested by this brief.

**Stage 3 begins now.**
