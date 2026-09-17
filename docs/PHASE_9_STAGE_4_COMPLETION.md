# Phase 9 Stage 4 — completion report

**Generation services: the `seo_optimizer` prompt wired through the Stage 3 pipeline.**

Everything below was verified by running the quality gates locally on
2026-09-17 against the branch as it stood at the SHA in the table. Where
something has never been executed, that is stated rather than implied.

| | |
|---|---|
| Date | 2026-09-17 |
| Branch | `feat/phase-9-stage-4-generation-services` |
| Base | `develop` @ `109f43f50b1022bc7f6e3a84e13dd24e836e531f` (post-merge CI run 35246290568, 10/10) |
| Last code commit | `4acc8ebd566b268dc3c0ee8c7d9413ad929298d7` (`4acc8eb`) — every gate below ran against this code |
| Branch HEAD | documentation-only commits on top of `4acc8eb` (this report, the `PHASE_9_PLAN.md` entry, whitespace fixes). `git diff --stat 4acc8eb..HEAD` touches only `docs/` |
| Migration | **None.** `ProductVersion.content` is JSONB; three keys were added to the shape |
| AI generation | **`StubProvider` only** — no real provider key; no live model call was made or is claimed |
| Plan | [PHASE_9_STAGE_4_PLAN.md](PHASE_9_STAGE_4_PLAN.md) |

---

## 1. Objective, and what shipped

Stage 3 generated a title and a description per optimisation. Stage 4 adds a
third generation — the seeded `seo_optimizer` prompt — through the **same**
provider-agnostic path, and stores what it returns in the version that
already held the other two.

| Capability | Where |
|---|---|
| `keywords` prompt variable, sourced from merchant data | `ProductOptimizationService._build_variables` → `_keywords_for_prompt` (`app/services/product_optimization.py`) |
| Third generation call (`seo_optimizer`) in `optimize_product` | same module |
| `seoTitle` / `seoDescription` / `keywords` in AI-generated `ProductVersion.content` | same module; `app/models/product.py` (comments only) |
| Optional `seoTitle` / `seoDescription` / `keywords` on `ProductVersionRead` | `app/schemas/product.py` |
| Failure aggregation over all three executions | `_first_failure(title, description, seo)` |
| Tests | `tests/unit/test_product_optimization_variables.py`, `tests/integration/test_product_optimization.py` |
| Plan + this report | `docs/PHASE_9_STAGE_4_PLAN.md`, `docs/PHASE_9_STAGE_4_COMPLETION.md` |

**Pipeline, exercised end-to-end through `StubProvider`:**

```
POST /products/{id}/optimize
  → ProductOptimizationService.optimize_product
      → PromptService.test_render(execute=True)  × product_title_generator
                                                  × product_description_generator
                                                  × seo_optimizer               ← new
          → get_ai_provider(settings) → StubProvider           (unchanged boundary)
          → PromptExecution (provider=stub, is_synthetic=true)  × 3
      → ProductVersion(AI_GENERATED, content={title, description,
                                              seoTitle, seoDescription, keywords})
      → Product AI cache: optimized_title / optimized_description only
```

### Provider boundary

Unchanged. The service imports no provider class; it calls
`PromptService.test_render`, which calls `app.ai.factory.get_ai_provider`.
`AI_PROVIDER=openai` (or any non-stub value) still raises
`AIProviderNotConfiguredError` → 503 at the first generation, with no version
written — re-asserted by the existing test.

### Where the SEO values live — and where they do not

Generated SEO lives in **`ProductVersion.content` only**, and on the wire as
optional fields of `ProductVersionRead`. It is a proposal attached to a
version.

It is **not** written to `Product.seo_title`, `Product.seo_description`,
`Product.meta_keywords`, `Product.tags`, `Product.title`,
`Product.description`, `Product.supplier_title`, or
`Product.supplier_description`. `_apply_active_version` — the only code that
syncs a version into the product — still reads only `title` and
`description` from `content`, so activation and rollback cannot promote the
proposal either. Integration tests seed the merchant's SEO fields through
the existing PATCH endpoint and assert they survive optimise, a failed
optimise, and a rollback byte-for-byte.

Because nothing on `Product` changes, nothing new can ride into the Shopify
publish payload: `integrations/shopify/sync.py` reads `Product` fields, and
none of them moved.

### What the SEO values are, honestly

The `seo_optimizer` template asks for a title, a meta description, and up to
ten keywords in **one** completion. `StubProvider` returns one opaque
synthetic string. Stage 4 stores that string, verbatim, as `seoTitle`,
`seoDescription`, and `keywords` — so with the stub the three are identical,
and every one of them is `[STUB-AI]`-prefixed.

No parse, no trimming to 60/155 characters, no score, no "SEO quality"
structure. Any of those would have been fabricated model output. The stage
that ships a real provider owns the parse contract, and is the first point
at which the three values can legitimately differ. A test pins this
(`test_generated_seo_is_the_stub_providers_synthetic_output_verbatim`).

---

## 2. Commits

| Commit | Description |
|---|---|
| `0ecef92` | docs(ai): plan Phase 9 Stage 4 generation services |
| `1f01fa6` | feat(ai): supply a keywords prompt variable from merchant product data |
| `d34bac5` | feat(ai): generate an SEO proposal as a third step of product optimisation |
| `9f416e4` | feat(api): expose the SEO proposal on ProductVersionRead |
| `a0a2ac9` | test(ai): prove the SEO generation, its failure path, and what it must not touch |
| `4acc8eb` | style(models): keep the prompt_execution_id attribute comment attached to its column |
| `d4eb5ef` | docs(ai): record Phase 9 Stage 4 completion — this report and the `PHASE_9_PLAN.md` progress entry |
| `4acc8eb..HEAD` | any further commits are documentation-only (whitespace and wording); no code after `4acc8eb` |

---

## 3. Gates executed

Local, on the developer machine, against an isolated `postgres:17-alpine`
container (`droppilot_test`, port 5499) started for the suite — the same
image CI uses. The suite rebuilt the schema from `alembic downgrade base` →
`upgrade head` (Alembic head `0032`) at session start, as it always does.

| Gate | Result |
|---|---|
| `ruff check .` | Pass |
| `ruff format --check .` | Pass (418 files) |
| `mypy app` (strict) | Pass (224 source files) |
| `scripts/check_secrets.py` | Pass (826 tracked files, 5 rules) |
| Stage 4 targeted — `test_product_optimization_variables.py` + `test_product_optimization.py` + `test_product_version_repository_scoping.py` + `test_product_repository_scoping.py` | **68 passed** (10 unit variable tests, 30 integration, 6 version-scoping, 22 product-scoping) |
| `pytest` (full backend suite) | **3000 passed, 1 skipped, 1 failed** of 3002 collected (1 h 13 min). Skip: `test_log_retention.py` — symlink creation needs privilege on Windows (pre-existing). Failure: `test_ebay_c0_security.py::TestNoGeneratedOrSecretFiles::test_no_env_file_was_added_to_the_repository`, which asserts `.env` / `backend/.env` do not exist *on disk*; this developer checkout holds both as gitignored local configuration (untracked, absent from the branch diff — `git check-ignore` confirms). It fails identically at `109f43f` in this checkout and passes in CI, which has no `.env`. **Not a Stage 4 regression; not claimed as a pass.** Baseline reconciles: CI's 2984 + 18 new = 3002 collected. |
| `git diff --check origin/develop...HEAD` | Clean |
| Frontend lint / typecheck / build / Playwright | **Not run — no frontend file changed** (see §5) |
| CI | **Not run** — branch not pushed, by instruction |

The full suite was launched at `a0a2ac9`. The two commits after it are a
whitespace-only change to a model comment (verified: the model's diff against
`develop` contains no non-comment line) and this documentation; the Stage 4
targeted tests were re-run on `4acc8eb`, the last code commit: **68 passed**.

### Required-test coverage

The Stage 4 brief listed 23 behaviours. Each maps to a named test:

| # | Behaviour | Test |
|---|---|---|
| 1 | `_build_variables` always contains `keywords` | `TestKeywordsVariable::test_keywords_is_always_present` |
| 2 | `search_topics` priority | `::test_search_topics_win_over_every_other_source` |
| 3 | `tags` fallback | `::test_tags_are_used_when_search_topics_are_empty` |
| 4 | `meta_keywords` fallback | `::test_meta_keywords_are_used_when_both_lists_are_empty` |
| 5 | empty-string fallback | `::test_falls_back_to_an_empty_string_not_a_fabricated_keyword`, `::test_blank_list_entries_do_not_count_as_a_source` |
| 6 | optimize invokes `seo_optimizer` | `TestSeoGeneration::test_optimize_executes_the_seo_optimizer_prompt`, `::test_seo_execution_receives_the_keywords_variable` |
| 7 | version content contains SEO keys | `::test_generated_version_content_carries_the_seo_keys` |
| 8 | generated SEO is `StubProvider` output | `::test_generated_seo_is_the_stub_providers_synthetic_output_verbatim` |
| 9–11 | merchant `seo_title`, `seo_description`, `tags` unchanged | `::test_merchant_seo_fields_and_tags_are_not_written` (also `meta_keywords`, `search_topics`) |
| 12–13 | supplier title / description unchanged | `TestOptimize::test_never_changes_the_suppliers_title_or_description` (now also `description`, `supplierTitle`, `supplierDescription`) |
| 14 | SEO failure → no new AI version | `TestSeoFailure::test_seo_failure_creates_no_ai_version_and_marks_failed` |
| 15 | SEO failure → existing failure state | same, plus `::test_seo_failure_is_recorded_on_the_execution_not_hidden` |
| 16 | last good cache survives failed regeneration | `::test_last_good_optimized_cache_survives_a_failed_regeneration` |
| 17 | second optimize increments correctly | `TestSeoGeneration::test_second_optimize_still_produces_version_three`, `TestOptimize::test_a_second_optimize_call_adds_a_third_version` |
| 18 | activate / rollback intact | `TestActivateVersion::*` (unchanged), `TestSeoGeneration::test_rollback_does_not_touch_merchant_seo_fields` |
| 19 | `ProductVersionRead` exposes optional SEO | `::test_versions_endpoint_exposes_the_optional_seo_fields` |
| 20 | cross-tenant optimize 404 | `TestOptimize::test_cannot_optimize_another_tenants_product` (unchanged) |
| 21 | cross-tenant versions 404 | `TestVersionsEndpoint::test_cannot_see_another_tenants_product_versions` (unchanged) |
| 22 | viewer 403 | `TestOptimize::test_non_admin_cannot_optimize`, `TestActivateVersion::test_non_admin_cannot_activate` (unchanged) |
| 23 | unconfigured non-stub provider 503 | `TestOptimize::test_provider_failure_marks_the_product_failed_without_a_new_version` (unchanged) |

The SEO-only failure is induced at the production seam
(`app.services.prompt.get_ai_provider`) by a provider that behaves as
`StubProvider` for the title and description prompts and raises `AIError`
for the `seo_optimizer` prompt — so the test drives the real
`PromptExecution` FAILED path, not a shortcut.

---

## 4. Security and tenancy

| Requirement | Verification |
|---|---|
| No new repository, scoped or otherwise | Structural — `ProductVersionRepository` and `PromptExecutionRepository` unchanged; scoping tests re-run |
| Cross-tenant optimize / versions / activate → 404 | Existing integration tests, unchanged and green |
| Viewer role → 403 | Existing integration tests, unchanged and green |
| Generated content cannot overwrite merchant or supplier fields | No assignment path exists (`_apply_active_version` reads `title`/`description` only); integration tests seed and re-read the merchant fields |
| Prompt input size | `features` still bounded by `_MAX_FEATURES_CHARS`; `keywords` comes from bounded columns (`search_topics`/`tags` JSONB lists, `meta_keywords` text) — no new unbounded input |
| Prompt injection | Unchanged posture from PHASE_9_PLAN.md §4: supplier text is substituted, never interpreted, and `StubProvider` ignores its input. A real provider stage must revisit this |
| Secrets | `check_secrets.py` pass; no new configuration surface |

---

## 5. Limitations, stated

1. **No live AI.** Every generated string, SEO included, is `StubProvider`
   output. Nothing about real-model quality — SEO or otherwise — is verified
   or claimed. This is the same gap PHASE_9_PLAN.md §6 records for the
   whole phase.
2. **The three SEO values are one string.** With the stub there is nothing
   to parse. A real provider stage defines whether the model returns
   structured output or a documented parse is applied; until then
   `seoTitle == seoDescription == keywords`. The wire type is `str | None`
   for all three; if a later stage makes `keywords` a list, that is a wire
   change to record then, not now.
3. **`frontend/types/api.ts` was not updated.** The brief allowed a change
   only if compile correctness required it, and it does not: the three keys
   are optional additions the TypeScript consumers ignore, no runtime shape
   validation exists, and the build does not read them. The hand-written
   `ProductVersion` type therefore lags the wire contract by three optional
   fields — the M4 debt (`PROJECT_ROADMAP.md`) applies. The stage that first
   *renders* generated SEO (Stage 10, AI Studio) should add them.
4. **Frontend gates were not run.** Consequence of 3 — no frontend file
   changed, so lint / typecheck / build / Playwright were not part of this
   stage's evidence. The backend wire change is additive and optional.
5. **CI has not run on this branch.** By instruction the branch is unpushed.
   The full local suite is the evidence; CI on the eventual PR is the
   authority.
6. **`prompt_execution_id` still points at the description execution.** The
   Stage 3 choice of a representative link is preserved rather than changed
   to a list or to the SEO execution; the three executions per request
   remain individually queryable by `prompt_name`.
7. **Optimisation is still synchronous** and now makes three provider calls
   per request instead of two. With the stub this is negligible; with a real
   provider it is three round-trips inside one HTTP request — the argument
   for Stage 9's Celery path, unchanged.

---

## 6. Out of scope — deliberately not done

Stage 5 quality scoring and `quality_scorer` wiring; `seo_score.py` changes;
Stage 6 image analysis and `image_analyzer`; Stage 9 Celery bulk
optimisation; Stage 10 AI Studio; any real provider; any migration or new
product column; auto-filling the editor's SEO fields; any change to the M2A
concurrency/conflict machinery, publish integrity, CI workflows, `main`, or
deployment.

---

## 7. What an independent reviewer should look at

- `app/services/product_optimization.py` — the third `test_render` call, the
  five-key `content`, and that `_apply_active_version` did not change.
- `app/schemas/product.py` — three optional fields on `ProductVersionRead`
  only; `ProductUpdateRequest` and `expected_updated_at` untouched.
- `app/models/product.py` — comment-only diff.
- `tests/integration/test_product_optimization.py` — `_SeoFailsProvider` is
  installed at `app.services.prompt.get_ai_provider`, not inside the service.
- The claim in §1 that no `Product` field is written: grep the branch diff
  for `product.seo_title`, `product.tags`, `product.meta_keywords` —
  they appear only as *reads* in `_keywords_for_prompt` and in tests.
