# Phase 9 Stage 5 — completion report

**Optimization-quality scoring: a deterministic, model-free rubric written on every product version.**

Everything below was verified by running the quality gates locally on
2026-09-18 against the branch at the SHA in the table. Where something has
never been executed, that is stated rather than implied.

| | |
|---|---|
| Date | 2026-09-18 |
| Branch | `feat/phase-9-stage-5-quality-scoring` |
| Base | `develop` @ `38ba9aa7fd31fa0c1fee1ac92f53c3892f468f3e` (post-merge CI run 35285058531, 10/10) |
| Last code commit | Claude's last application commit `2e4e236`; Cursor takeover added `c3d707c` (the plan's `metaKeywords: "a,b"` integration pin). Gates in §3 / §3.1 name what actually ran |
| Contract | [PHASE_9_STAGE_5_PLAN.md](PHASE_9_STAGE_5_PLAN.md) — the source of truth; this report records that it was implemented as written |
| Migration | **None.** Five keys added to the existing `ProductVersion.content` JSONB |
| Rubric version | `QUALITY_SCORE_VERSION = 1` |
| AI involvement | **None in scoring.** The scorer imports nothing from `app.ai`; generation is unchanged from Stage 4 and still `StubProvider`-only |

---

## 1. What shipped

| Capability | Where |
|---|---|
| Pure scorer: normalisation, D1–D4, `seoFormat`, total, `QualityResult`, `as_content()` | `app/services/optimization_quality.py` (new) |
| Public API `score_version(content, product) -> QualityResult`; private `_merchant_terms` | same module; `__all__` excludes the private helper |
| Original snapshot scored at creation; AI version scored and baselined at creation | `ProductOptimizationService._ensure_original_snapshot`, `optimize_product` (`app/services/product_optimization.py`) |
| Five optional wire fields on `ProductVersionRead`, typed nested read models for baseline and breakdown | `app/schemas/product.py` |
| Model comment listing the new `content` keys | `app/models/product.py` (comment-only diff) |
| Unit tests (86) | `tests/unit/test_optimization_quality.py` (new) |
| Stage 4 regression pins (3) | `tests/unit/test_product_optimization_variables.py` |
| Integration tests (9) | `tests/integration/test_product_optimization.py::TestQualityScoring` |
| This report, plan progress entry, roadmap row, changelog | `docs/`, `PROJECT_ROADMAP.md`, `CHANGELOG.md` |

**Call flow, as the plan required (§5.2):**

```
POST /products/{id}/optimize
  → ProductOptimizationService.optimize_product
      → PromptService.test_render(execute=True) ×3   (title, description, seo_optimizer — unchanged)
          → get_ai_provider(settings) → StubProvider
      → score_version(generated, product)              public, stage 5 module
      → score_version(original.content, product)       baseline, recomputed
          → _merchant_terms(product)                   private, same module
      → ProductVersion.create(content = stage 3 + stage 4 + stage 5 keys)
      → Product AI cache: optimized_title / optimized_description only (unchanged)
```

`product_optimization.py` imports exactly one name from the scoring module,
`score_version`, and contains zero references to `_merchant_terms`.

### Persisted keys

On every version written from this stage on:

| Key | Original (v1) | AI-generated |
|---|---|---|
| `qualityScoreVersion` | `1` | `1` |
| `qualityScore` | its own score | its own score |
| `qualityBreakdown` | full §9.1 object; `seoFormat: null` | full §9.1 object |
| `qualityBaseline` | absent | `{ "versionNumber": 1, "score": <original's recomputed score> }` |
| `qualityDelta` | absent | `qualityScore − qualityBaseline.score` |

Rows written before this stage have none of these keys and are read as
`null`s. They are never back-filled, and a legacy original still serves as
the baseline for new AI versions because the baseline is always recomputed
from its content (integration test 9).

### What the numbers mean — and do not

The rubric inspects title length, plain-text description length,
repetition, and coverage of the merchant's own keywords. That is all. It
is not marketplace ranking, conversion, or model quality. With
`StubProvider` every AI version scores the same fixed number (75 without
merchant keywords, 56 with), so **a Stage 5 delta today is a statement
about the supplier's original listing, not evidence that AI improved it**
— exactly as the plan's §5.3 and §20 say.

---

## 2. Commits

| Commit | Description |
|---|---|
| `338fa64` | feat(ai): add deterministic optimization quality scorer |
| `98fb248` | test(ai): prove Stage 5 deterministic quality rubric |
| `068e8fb` | feat(ai): persist quality evidence on product versions |
| `2e4e236` | test(ai): prove Stage 5 integration and protected behavior |
| `0732482` | docs(ai): record Stage 5 implementation evidence (Claude) |
| `c3d707c` | test(ai): pin seo_optimizer keywords remain a,b (Cursor takeover) |
| this commit | docs(ai): record Cursor Stage 5 takeover evidence |

---

## 3. Gates executed

Claude's targeted runs below were against an isolated `postgres:17-alpine`
container (`droppilot-stage5-testpg`, `droppilot_test`, port 5499) and
rebuilt the schema from `alembic downgrade base` → `upgrade head` (head
`0032`). Cursor could not repeat that environment; see §3.1.

| Gate | Result |
|---|---|
| `ruff check .` | Pass |
| `ruff format --check .` | Pass (420 files) |
| `mypy app` (strict) | Pass (225 source files) |
| `scripts/check_secrets.py` | Pass |
| Stage 5 unit — `test_optimization_quality.py` | **86 passed** (1.0 s, no database) |
| Stage 4 pins — `test_product_optimization_variables.py` | **13 passed** (10 existing + 3 new) |
| Stage 3/4/5 integration — `test_product_optimization.py` | **39 passed** (30 existing + 9 new; the 9 new ran as a class in 4 min 50 s, the 30 existing re-ran green with scoring wired: 135-test batch with the unit files and version scoping) |
| Repository scoping — `test_product_version_repository_scoping.py` + `test_product_repository_scoping.py` | **6 + 22 = 28 passed** (re-run 2026-09-18 takeover; product-repository file is the Stage 3 file the plan's §16.3 also requires) |
| **Full `pytest`** | **NOT COMPLETED LOCALLY — gate not satisfied.** See §3.1. |
| `git diff --check origin/develop...HEAD` | Clean |

### 3.1 Cursor takeover re-verification (2026-09-18)

Claude reached its monthly usage limit after drafting the report above.
Cursor took over on `feat/phase-9-stage-5-quality-scoring` at
`0732482` (clean tree, five commits on `develop` `38ba9aa7`). Nothing
was recovered from a live pytest process — the terminals folder was
empty and Docker Desktop was not running.

| Gate | Cursor result | Against |
|---|---|---|
| `ruff check .` | Pass | current tree |
| `ruff format --check .` | Pass (420 files) | current tree |
| `mypy app` (strict) | Pass (225 source files) | current tree |
| `scripts/check_secrets.py` | Pass (831 tracked files) | current tree |
| Stage 5 unit — `test_optimization_quality.py` | **86 passed** (3.59 s batch with the three files below) | current tree |
| Stage 4 pins — `test_product_optimization_variables.py` | **13 passed** | current tree |
| Scoping — version + product repositories | **6 + 22 passed** | current tree |
| Combined targeted unit/scoping | **127 passed** | current tree |
| Stage 3/4/5 integration — `test_product_optimization.py` | **Not re-run.** Collection reached the module (not skipped), then every test errored at `db_session` setup: `database "droppilot_test" does not exist`. Local Windows PostgreSQL accepts `droppilot`/`droppilot` on `:5432` and holds `droppilot` at Alembic `0032` (39 public tables, **not migrated by this takeover**). Role `droppilot` has `CREATEDB=false`. Docker Desktop's engine was down (`dockerDesktopLinuxEngine` pipe missing); starting it was blocked as a host-runtime change, so the isolated `postgres:17-alpine` path Claude used could not be repeated. | n/a |
| **Full `pytest`** | **Not re-run** — same database blocker. Claude's earlier full-suite attempt remains **not completed** (killed ~15 % in by a reboot). One local environmental failure is still expected if `.env` / `backend/.env` exist: `test_ebay_c0_security.py::test_no_env_file_was_added_to_the_repository`. Those files are gitignored and untracked; they were not deleted. | n/a |

Claude's reported integration **39 passed** and the 9 Stage 5 integration
cases are **not independently confirmed** in this takeover. They stay in
the table above as Claude's claimed runs against `2e4e236`, not as
Cursor-verified results.

The plan's §16.2 item 7 additionally required the `seo_optimizer`
`input_variables["keywords"] == "a,b"` pin on the unused-prompt
integration test. That assertion was missing; it is added in the takeover
code commit (`c3d707c`). It has **not** been executed (same database blocker).

Frontend lint / typecheck / build / Playwright: **not applicable — no
frontend file changed.** CI: **not run** — branch not pushed, by
instruction. CI is the authority for the full suite regardless of the
local re-run.

### Plan → test coverage

Every rule in the plan's §16.1 and every item in §16.2 has a named test.
Highlights, with the plan's own expected values:

| Contract | Test | Asserted |
|---|---|---|
| D1 tiers at every boundary (0, 1, 9, 10, 19, 20, 120, 121, 255, 256) | `TestTitleLength::test_every_tier_boundary` | 0, 6, 6, 12, 12, 25, 25, 15, 15, 0 |
| D2 tiers at every boundary (0 … 5001) | `TestDescriptionLength::test_every_tier_boundary` | 0, 6, 6, 12, 12, 25, 25, 18, 18, 12 |
| D3a/b at exactly two (pass) and three (fail); D3c at 19 tokens, 30 %, exactly 25 %; D3d equal / one char off / blank | `TestRepetition::*` | per plan |
| D4 N/A exact shape and `applicableMax = 75`; applicable exact shape; 0 / 13 / 25 for 0 / 1 / 2 of 2 | `TestKeywordCoverage::*` | per plan |
| Source priority, comma split, strip, case-insensitive dedupe, non-`str` skip, 50-cap + `truncated`, substring + case-insensitive match, generated `keywords` never a source | `TestKeywordCoverage::*` | per plan |
| Round-half-up: 62/75→83, 1/75→1, 74/75→99, 56/75→75, 56/100→56 | `TestTotal::test_round_half_up_integer_formula` | exact |
| Totality over `{}`, non-`str`, nested, `None` | `TestTotal::test_is_total_over_malformed_content` | no raise |
| Stub fixture 75 / 56; `seoFormat` all true; 60-char bound | `TestStubFixture`, `TestSeoFormat` | exact |
| **Delta contract**: 52 → 52 / 100 / 41, Δ 0 / +48 / −11; with `["camera protection"]` 39 → 39 / 100 / 31, Δ 0 / +61 / −8 | `TestDeltaContract::test_scores_and_deltas` | baseline, candidate, subtraction, repeated-run equality — each separately |
| Module hygiene: no `app.ai`, `app.services.prompt`, `app.services.seo_score`, `httpx`, `asyncio`, `random`, `datetime`; private helper not exported; scoring succeeds with the provider factory patched to raise | `TestModuleHygiene::*` | AST-verified |
| Stage 4 pins: `"a,b"` → `"a,b"`; `["x","x"]` → `"x, x"`; exact six-key `_build_variables` | `TestStage4PromptRenderingIsPinned::*` | literal |
| v2 scored, baselined to v1, exact N/A shape, `seoFormat`; keywords via PATCH → applicable, 56 | integration 1–2 | exact |
| v1 scored, no delta/baseline; v3 shares v2's baseline | integration 3–4 | exact |
| Rollback after keyword change leaves every quality field byte-identical; detail has no quality field | integration 5 | field-by-field |
| SEO-only failure leaves v2's score untouched | integration 6 | exact |
| Merchant + supplier fields never written | integration 7 | re-read |
| Execution log = exactly the three stage 4 prompts; `seo_optimizer` `keywords` is `"a,b"` when that is the only merchant source | integration 8 | set equality, count 3, literal `"a,b"` |
| Legacy v1 → `null`s, not back-filled, still baselines v2 | integration 9 | exact |
| Cross-tenant 404, viewer 403, `AI_PROVIDER=openai` 503 | existing tests | unchanged, green |

---

## 4. Security and tenancy

| Requirement | Verification |
|---|---|
| No new repository | Structural — `ProductVersionRepository` writes and reads the row; scoping tests re-run |
| Cross-tenant optimize / versions / activate → 404; viewer → 403 | Existing integration tests, unchanged and green |
| No `Product` write | `_apply_active_version` unchanged; grep of the branch diff for assignments to `product.seo_title` / `seo_description` / `meta_keywords` / `tags` / `title` / `description` / `supplier_*` finds none; integration test 7 |
| No provider or network reachable from the scorer | AST-verified import list; factory monkeypatched to raise |
| Prompt inputs unchanged | `_build_variables` / `_keywords_for_prompt` byte-identical to `develop`; three literal pins |
| Secrets | `check_secrets.py` pass; no new configuration surface |

---

## 5. Limitations, stated

1. **No live AI, so no live evidence of improvement.** Every AI version
   under `StubProvider` scores 75 or 56; the delta is the original's
   distance from that constant. The rubric is verified; the claim "AI
   improves listings" is not made and cannot be until a real provider
   exists.
2. **`frontend/types/api.ts` not updated.** The five fields are optional
   additions TypeScript consumers ignore; the plan (§15) placed the type
   change with Stage 10, which first displays a score. The hand-written
   `ProductVersion` type now lags the wire by eight optional fields (three
   from Stage 4, five from this stage) — recorded under M4.
3. **Frontend gates not run** (consequence of 2 — no frontend file
   changed).
4. **CI has not run on this branch** — unpushed by instruction. The local
   full suite is **not** evidence yet (never completed; see §3.1). CI on
   the eventual PR is the authority.
5. **The baseline can differ between optimisations** of the same product
   if the merchant edits their keywords in between (plan §8). Each delta
   is like-for-like within its own version; deltas across versions are
   comparable only when their recorded keyword lists match.
6. **Substring keyword matching is lenient** by design (`"case"` matches
   `"showcase"`); plan §20 item 3.
7. **Three sequential provider calls plus two scorer calls per optimize**,
   still synchronous. The scorer costs microseconds; the argument for
   Stage 9's Celery path is unchanged from Stage 4.

---

## 6. Out of scope — deliberately not done

Real providers; any call to `quality_scorer` (still seeded, still
unwired — the scorer's import hygiene is unit-tested; integration test 8
asserts the optimize execution log, but that class was not re-run in the
Cursor takeover); model-assisted explanations;
`image_analyzer` / Stage 6; Stage 7 pipeline; Stage 9 Celery; Stage 10 AI
Studio and any frontend; changes to `seo_score.py`, its endpoint, or the
editor SEO panel; a migration or product column; back-filling legacy
rows; denormalising a score onto `Product`; M2A concurrency / conflict /
publish-integrity code; CI workflows; `main`; deployment.

---

## 7. What an independent reviewer should look at

- `app/services/optimization_quality.py` — each rule against the plan's
  §6 tables; `_round_half_up`; `_merchant_terms` is private and
  `__all__` excludes it.
- `app/services/product_optimization.py` — the import line (only
  `score_version`), the two `score_version` calls, that `_build_variables`
  and `_keywords_for_prompt` have no changed line, and that
  `_apply_active_version` is unchanged.
- `app/schemas/product.py` — the nested read models mirror §9.1/§9.2
  exactly; `ProductRead` / `ProductDetailRead` / `ProductOptimizeRequest`
  untouched.
- `app/models/product.py` — comment-only diff.
- `tests/unit/test_optimization_quality.py::TestDeltaContract` — the
  fixtures are the plan's, and the numbers are literal.
- `tests/integration/test_product_optimization.py::TestQualityScoring::test_scoring_adds_no_prompt_execution`
  — the proof that `quality_scorer` stays unwired.
