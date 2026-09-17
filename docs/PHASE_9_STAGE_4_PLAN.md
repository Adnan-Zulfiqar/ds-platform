# Phase 9 Stage 4 — Generation services: plan

Wiring the third seeded prompt, `seo_optimizer`, through the pipeline Stage 3
already built, so one optimisation produces richer content than a title and a
description.

Written before implementation and checked against the code as it stood at
`develop` `109f43f5` (post-merge CI run 35246290568, 10/10). Where this plan
narrows what [PHASE_9_PLAN.md](PHASE_9_PLAN.md) §3 originally sketched for
Stage 4, it says so.

---

## 1. Scope boundary — what this stage does and does not do

**Does.** Extends `ProductOptimizationService.optimize_product` from two
generation calls to three: `product_title_generator`,
`product_description_generator`, and the already-seeded `seo_optimizer`.
The SEO output lands in the AI-generated `ProductVersion.content` JSONB
alongside `title` and `description`, and `ProductVersionRead` exposes it.
Nothing else changes shape.

**Does not.**

| Out of scope | Why |
|---|---|
| A new AI provider, or any real provider (`openai`, `anthropic`, `gemini`, `local`) | Stage 1's decision stands: a concrete provider lands in the stage that can verify it, and no key exists. `get_ai_provider()` still resolves to `StubProvider` or raises. |
| A new provider *architecture* | The Stage 1–3 boundary (`get_ai_provider(settings)` behind `PromptService.test_render`) is reused as-is. Nothing here imports `StubProvider`. |
| Writing `Product.seo_title`, `seo_description`, `meta_keywords`, `tags`, `title`, `description` | These are merchant- or supplier-controlled. Generated SEO is a *proposal* that lives only in `ProductVersion.content`; promoting it is a product decision for a later stage, made by a merchant, not by a stub. |
| Auto-filling the editor's SEO fields after Optimize | Same reason. The editor is untouched. |
| Stage 5 quality scoring, `quality_scorer` wiring, `seo_score.py` changes | Deliberately model-free and separately staged. |
| Stage 6 image analysis, `image_analyzer` | Separately staged. |
| Stage 9 Celery bulk optimisation | Optimisation stays synchronous, one product per request, as in Stage 3. |
| Stage 10 AI Studio | No frontend surface is built. |
| A database migration | `ProductVersion.content` is JSONB precisely so this stage could add keys without one (Stage 3 plan §3). No new column anywhere. |
| Parsing the model output into structured fields, character-limit enforcement, scores | `StubProvider` returns one opaque synthetic string. Splitting it into "a 60-character title" and "a 155-character description" would be fabricated model output — exactly what PHASE_9_PLAN.md §1 says never to claim. See §3. |
| Any change to `expectedUpdatedAt`, `update_product` / `update_draft` concurrency, 409 handling, the draft-editor conflict state machine, or publish integrity (M2A) | Stage 4 adds no second write path into the product PATCH logic. |
| CI workflow, deployment, `main` | Untouched. |

---

## 2. Provider boundary — unchanged

```
POST /products/{id}/optimize
  → ProductOptimizationService.optimize_product
      → PromptService.test_render(name=..., execute=True)   ×3
          → get_ai_provider(settings)                        (app/ai/factory.py)
              → StubProvider.complete(...)                   (the only implementation)
          → PromptExecution row (provider=stub, is_synthetic=true)
      → ProductVersion (AI_GENERATED, content incl. SEO)
      → Product AI cache fields (title/description only — see §5)
```

The third call is the only structural addition. The service still never names
a provider class; `AI_PROVIDER=openai` still fails with
`AIProviderNotConfiguredError` → 503 at the first generation call, before any
version is written.

---

## 3. What the SEO call produces, honestly

The seeded `seo_optimizer` template asks for a page title, a meta description,
and up to ten keywords in one completion. `StubProvider.complete` returns a
single deterministic string: `[STUB-AI] synthetic completion (prompt <digest>).`

Stage 4 stores **that raw response text** as the value of each SEO key:

| `content` key | Value |
|---|---|
| `title` | `product_title_generator` response (unchanged from Stage 3) |
| `description` | `product_description_generator` response (unchanged) |
| `seoTitle` | `seo_optimizer` response, verbatim |
| `seoDescription` | `seo_optimizer` response, verbatim |
| `keywords` | `seo_optimizer` response, verbatim |

With `StubProvider` the three SEO values are therefore identical. That is the
truthful state, not a defect to paper over: no parse of the completion exists
yet because no provider produces a parseable one. The stage that ships a real
provider owns defining that contract (structured output or a documented
parse) and is where distinct values first become possible. Until then, every
SEO value in every version is visibly `[STUB-AI]`-prefixed synthetic text.

---

## 4. Prompt variables

`_build_variables` gains one key, `keywords`, supplied to all three prompts
(the renderer ignores keys a template does not reference — Stage 2 designed
it that way so one context dict serves several prompts).

Source priority, first non-empty wins:

1. `Product.search_topics` — the planning-topics list Stage 3 introduced for
   exactly this purpose ("Prefer `search_topics` for planning inputs").
2. `Product.tags`
3. `Product.meta_keywords` — legacy free text, passed through stripped
4. `""` — the prompt still renders; `keywords` is never *missing*, so
   `MissingPromptVariablesError` is reserved for a genuinely absent variable

Lists are joined with `", "`, the same join `integrations/shopify/sync.py`
already uses for `tags`. No keyword is generated, inferred, or padded — an
empty product yields an empty string.

---

## 5. All-or-nothing, and what activation touches

**Generation is atomic per request.** If any of the three executions is
`FAILED`, the existing Stage 3 path runs unchanged: `Product.ai_status =
FAILED`, no `ProductVersion` is created, every other AI field — including the
last good `optimized_title` / `optimized_description` — is left as it was,
and `AIError` propagates to a 503. `_first_failure` simply receives three
executions instead of two. A partially generated result (title and
description succeeded, SEO failed) is never persisted.

**Activation and rollback are unchanged.** `_apply_active_version` still
writes only the five AI cache fields and reads only `content["title"]` /
`content["description"]`. The SEO keys are present in the version row and on
the wire, and nowhere else. Version numbering, the `original` /
`ai_generated` split, the one-active-version index, and the
`prompt_execution_id` link (still the description execution, as Stage 3 chose)
are all as they were.

---

## 6. API

No new endpoint. No status-code or authorization change.

| Endpoint | Change |
|---|---|
| `GET /products/{id}/versions` | `ProductVersionRead` items may now carry `seoTitle`, `seoDescription`, `keywords` (all optional, `null` on the `original` snapshot and on Stage 3-era rows) |
| `POST /products/{id}/optimize` | Same request (`tone` unchanged); response `version` carries the same optional keys |
| `POST /products/{id}/versions/{version_id}/activate` | Unchanged |

`frontend/types/api.ts` is changed only if the build needs it. The
additional optional JSON keys are ignored by TypeScript consumers, so the
current expectation is no frontend change.

---

## 7. Tests

Extending Stage 3's files, not adding parallel ones.

| File | Adds |
|---|---|
| `tests/unit/test_product_optimization_variables.py` | `keywords` always present; `search_topics` priority; `tags` fallback; `meta_keywords` fallback; empty-string fallback |
| `tests/integration/test_product_optimization.py` | `seo_optimizer` is executed; version content carries the SEO keys with synthetic output; merchant `seoTitle` / `seoDescription` / `tags` and supplier `title` / `description` are unchanged after optimize; an SEO-only failure creates no version, sets `failed`, and preserves the last good cache; second optimize still yields version 3; rollback unchanged; `ProductVersionRead` exposes the keys; cross-tenant 404s, viewer 403, unconfigured provider 503 all re-asserted |
| `tests/unit/test_product_version_repository_scoping.py` | Unchanged — no repository change, so the isolation proof stands as-is and is re-run |

An SEO-only failure is induced by substituting the provider *at the existing
boundary* (`app.services.prompt.get_ai_provider`) with one that fails only
for the `seo_optimizer` rendered prompt — the same seam a real provider would
occupy, so the test exercises the production failure path rather than a
service-internal shortcut.

---

## 8. Sequencing

Small commits, in this order, none rewritten after review begins:

1. This plan
2. `keywords` in `_build_variables` + unit tests
3. `seo_optimizer` generation + `ProductVersion.content` keys
4. `ProductVersionRead` exposure
5. Integration / failure / isolation tests
6. Frontend type update — only if the build requires it
7. Completion report + `PHASE_9_PLAN.md` progress entry, after the gates pass

Gates per CLAUDE.md §12: `ruff check`, `ruff format --check`, `mypy app`,
targeted Stage 4 tests, full `pytest`, `scripts/check_secrets.py`,
`git diff --check`; frontend lint / typecheck / build only if a frontend file
changes.
