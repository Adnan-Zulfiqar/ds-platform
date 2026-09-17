# Phase 9 Stage 5 — Optimization-quality scoring: plan

The implementation contract for Stage 5, written before any Stage 5 code
exists. Every rule below is meant to be copied into a test and asserted
exactly. Where a number is chosen, its justification is stated next to it;
where a number cannot be justified from this repository or a documented
platform limit, it is not used.

Baseline this plan was written against: `develop` `c3814e8b` (Stage 4 merged
via PR #11; post-merge CI run 35272545887, 10/10).

---

## 1. Objective

Measure, with a **deterministic, model-free rubric**, whether an
AI-generated version of a product listing is structurally better or worse
than the supplier's original — and record that measurement on the version
itself, immutably, at the moment the version is written.

"Better" here means only what the rubric in §6 can inspect: presence,
length bounds, repetition, and coverage of keywords the merchant themselves
provided. It does not mean, and must never be presented as, marketplace
ranking, conversion, or model quality. See §3.

---

## 2. Non-goals

Stage 5 does **not**:

- call any AI provider, for scoring or anything else;
- wire the seeded `quality_scorer` prompt (§4);
- wire the seeded `image_analyzer` prompt (Stage 6);
- rewrite, extend, or reroute `app/services/seo_score.py` or its endpoint (§4);
- add a database migration or a product column;
- write any merchant- or supplier-controlled `Product` field (§13);
- add a frontend surface (§15 — Stage 10 is AI Studio);
- add a Celery task (Stage 9);
- implement Stage 7's pipeline (analyse → generate → score → preview → approve → publish);
  it implements only the `score` step's *function*, in the place the existing
  flow already produces a version;
- add an endpoint (§10).

---

## 3. Why the score is deterministic and model-free

`PHASE_9_PLAN.md` §3 decided this before Stage 1 shipped: *"A quality score
that varies run to run cannot be used to decide whether an edit improved
anything."* Stage 5 keeps that decision for three concrete reasons:

1. **A delta needs a stable baseline.** The score of the original version
   must be the same number whenever it is computed, or `score(AI) −
   score(original)` measures noise.
2. **Exactly assertable tests.** Every rule in §6 is a pure function of a
   string; a test can state the expected integer. A model-backed score
   could only be tested against a mock of itself.
3. **No key exists.** As with every Phase 9 stage, nothing model-backed can
   be verified live. A score that cannot be verified cannot gate anything.

The score is therefore a pure function: `(version content, merchant
keywords) → integer`. No I/O, no session, no clock, no randomness, no
network. It may be called anywhere, including from a unit test without a
database.

---

## 4. What Stage 5 is not — three things it must be kept apart from

| Thing | What it is | Stage 5's relationship to it |
|---|---|---|
| **`quality_scorer` prompt** (seeded in migration `0010`) | A reserved prompt whose own seeded description says Stage 5 will not call it; intended, if ever, for a model-assisted one-sentence *explanation* of a deterministic score | **Untouched and uncalled.** No code in Stage 5 references the name. A regression test asserts that after an optimize the only `PromptExecution` rows are the three Stage 4 prompts (§16). Any future explanation feature is a separate stage with its own plan. |
| **`app/services/seo_score.py`** + `GET /api/v1/drafts/{id}/seo-score` + `draft-seo-panel.tsx` | The existing **merchant-listing SEO advisory score**: 0–100 over the merchant's *current* `Product` fields (`seo_title`, `seo_planning`, `search_topics`, `slug`, image alt, shipping data…), shown in the editor and publish checklist, shipped under the DSers-parity work | **A different concept, left exactly as it is.** It scores what the merchant has published-ready *now*; Stage 5 scores an immutable *version snapshot* and compares two of them. Stage 5 does not import from `seo_score.py`, does not change its endpoint, schema, thresholds, or tests, and does not surface the two numbers side by side. The two overlap in technique (both are deterministic rubrics), not in subject. If a later stage wants shared text helpers (tokenising, repetition), it extracts them into a neutral module in its own commit; Stage 5 keeps its helpers private to its own module to avoid coupling the two by accident. |
| **`image_analyzer` prompt** | Seeded, unwired; Stage 6 | **Untouched and uncalled.** Same regression test. |

And the generation path is unchanged:

```
ProductOptimizationService.optimize_product
  → PromptService.test_render(execute=True) ×3   (title, description, seo_optimizer)
      → get_ai_provider(settings) → StubProvider
  → [Stage 5: score the content — pure function, no provider]
  → ProductVersion.create(content = generated + quality keys)
```

The scorer sits between generation and persistence and never touches the
provider boundary.

---

## 5. Exact scoring input

Stage 5 scores **one `ProductVersion.content` snapshot at a time**, and
compares an AI-generated version to the **original** version (version 1).

### 5.1 What is read from the version

| `content` key | Type | Present on |
|---|---|---|
| `title` | `str \| None` | every version (supplier title on the original; generated title on AI versions) |
| `description` | `str \| None` | every version. **On the original it is sanitized HTML** (the supplier's description as stored on `Product.description`); on AI versions it is provider text. Both are reduced to plain text with the existing `app.core.sanitize.html_to_plain_text` before any measurement, so the two are measured the same way. |
| `seoTitle`, `seoDescription`, `keywords` | `str \| None` | AI versions from Stage 4 onward only. **Read for the informational `seoFormat` block (§7.4) only. They do not contribute to the score.** See §6 preamble for why. |

Anything else in `content` is ignored. Any value that is not a `str` is
treated as absent (§11).

### 5.2 What is read from the product

**Merchant-provided keywords**, with exactly the Stage 4 **source
priority** so the two stages cannot disagree about whose keywords count:
`Product.search_topics` → `Product.tags` → `Product.meta_keywords` → none.
First non-empty source wins. Terms are: split on `,` (`meta_keywords` is
free text; the two lists are already terms), stripped, blanks dropped,
non-`str` entries skipped, de-duplicated case-insensitively preserving
first occurrence, and **capped at the first 50** (a bound, since
`search_topics` and `tags` are unbounded JSONB lists). Original case is
kept in the list; matching lower-cases both sides (§6 D4). The list
actually used, its source, and whether it was truncated are written into
the breakdown (§9) so the score can be recomputed from the row alone later
without consulting the product.

**Effect on Stage 4, stated rather than slipped in.** Stage 4's
`_keywords_for_prompt` today passes `meta_keywords` through unsplit. If the
implementation shares one term-extraction helper between the two stages
(§18, commit 2), Stage 4's `{{keywords}}` variable becomes
`", ".join(terms)`; the only observable change is that `meta_keywords`
spacing is normalised (`"a,b"` → `"a, b"`) and exact duplicates collapse.
The Stage 4 unit tests keep passing as written; one new test pins the
normalisation. If reviewers prefer zero change to Stage 4, the helper is
duplicated for Stage 5 instead — both are acceptable, the choice must be
recorded in the completion report.

Nothing else on `Product` is read. In particular `Product.seo_title`,
`seo_description`, `seo_planning`, `slug`, images, and shipping fields —
the inputs of `seo_score.py` — are **not** inputs here.

### 5.3 What Stage 5 does not score, stated plainly

- It does not score the merchant's SEO fields. Those are inputs to
  `seo_score.py`, and reading them here would blur the two concepts.
- It does not treat `StubProvider` text as if it were real output. With the
  stub, every AI version scores the same fixed number (§7.5), and the plan
  says so. Until a real provider exists, a Stage 5 delta is a statement
  about the *original* listing's structure, not evidence that AI improved
  anything.

### 5.4 Why the baseline is the original, not the previous AI version

Every AI version is generated from the product's **supplier** fields
(`_build_variables` reads `Product.title` / `Product.description`), never
from the previous AI version. AI versions are therefore siblings of the
original, not descendants of each other, and "did this optimisation improve
the listing" is answered by comparing to the original. Comparing v3 to v2
would answer "which run was luckier", which is not the question. The
original always exists before any AI version (Stage 3's lazy snapshot runs
first), so the baseline is always available.

---

## 6. Rubric dimensions

Four dimensions, 25 points each, 100 total. Only dimensions whose inputs
exist on **every** version are scored, so the original and an AI version are
always measured on the same axes. That is why the Stage 4 SEO keys
(`seoTitle`, `seoDescription`, `keywords`) are *not* a dimension: the
original never has them, and scoring their mere presence would hand every
AI version a systematic advantage that reflects nothing about its content.
Their format is recorded separately and informationally (§7.4).

### Text normalisation used by every rule

- `plain(x)`: if `x` is not a `str`, `""`. Otherwise
  `html_to_plain_text(x)`, then all Unicode whitespace runs collapsed to a
  single space, then `strip()`.
- `tokens(x)`: `re.findall(r"[A-Za-z0-9']+", plain(x))`, each lower-cased.
- `len(x)`: character length of `plain(x)`.
- "blank": `plain(x) == ""`.

`html_to_plain_text` is applied to titles too, for symmetry; on a plain
string it is a no-op.

### D1 — Title presence and length · max 25

| Purpose | Input | Rule |
|---|---|---|
| A title exists and is neither a fragment nor unpublishable | `len(title)` | tiered, below |

| `len(title)` | Points | Why this bound |
|---|---|---|
| 0 (blank) | **0** | nothing to publish |
| 1–9 | **6** | a fragment — shorter than a product name plus one attribute |
| 10–19 | **12** | a bare name |
| 20–120 | **25** | a name with attributes, readable in one line |
| 121–255 | **15** | storable and publishable but long |
| ≥ 256 | **0** | exceeds Shopify's documented product-title limit of 255 characters (external platform limit; this repository has no constant for it, so it is cited, not derived). Such a title cannot be published unmodified. |

Edge cases: whitespace-only → blank. A title of exactly 20, 120, 121, 255,
256 characters lands in the tier shown (inclusive bounds). Tags in a title
are stripped before measuring.

### D2 — Description presence and length · max 25

| Purpose | Input | Rule |
|---|---|---|
| A description exists with enough substance to describe, not so much it is a dump | `len(description)` on the plain text | tiered, below |

| `len(description)` | Points | Why this bound |
|---|---|---|
| 0 (blank) | **0** | |
| 1–49 | **6** | a line, not a description |
| 50–149 | **12** | one sentence or two |
| 150–2000 | **25** | a paragraph or a few. 2000 is the bound Stage 4 already uses for prompt input (`_MAX_FEATURES_CHARS`), reused rather than inventing a second number |
| 2001–5000 | **18** | long |
| ≥ 5001 | **12** | very long; the merchant editor caps HTML at 64 000 characters, so plain text here can be large |

Edge cases: HTML-only content with no text (e.g. `<p></p>`) → blank → 0.
Measurement is on plain text, so tag characters never count.

### D3 — Repetition and distinctness · max 25

Four independent checks; points add.

| # | Check | Input | Rule | Points |
|---|---|---|---|---|
| D3a | Title is not word-stuffed | `tokens(title)` | award unless any token of length ≥ 3 occurs **3 or more** times | **8** |
| D3b | Description is not phrase-stuffed | `tokens(description)` | award unless any adjacent-token bigram occurs **3 or more** times | **8** |
| D3c | Description is not dominated by one word | `tokens(description)` | if fewer than 20 tokens: award (not evaluable, no evidence of stuffing). Otherwise award unless one token of length ≥ 3 accounts for **more than 25 %** of all tokens | **5** |
| D3d | Description is not just the title | `plain(title)`, `plain(description)` | award if description is non-blank **and** `plain(description).lower() != plain(title).lower()` | **4** |

Edge cases: a blank title has no tokens → D3a awards 8 (no stuffing; the
missing title is already penalised by D1). A blank description → D3b and
D3c award (no tokens), D3d does not. "3 or more" and "more than 25 %" are
exact; a token occurring exactly twice, or exactly 25 %, passes.

### D4 — Merchant keyword coverage · max 25 · **applicable only when the merchant provided keywords**

| Purpose | Input | Rule |
|---|---|---|
| The listing text mentions the keywords the merchant said matter | merchant keyword list `K` (§5.2); haystack `H = plain(title).lower() + " " + plain(description).lower()` | if `K` is empty: **not applicable** (§7.2). Otherwise `matched = count of k in K where k is a substring of H`; points `= (50 × matched + len(K)) ÷ (2 × len(K))` in integer arithmetic — exactly round-half-up of `25 × matched / len(K)` (verified exhaustively for every `matched ≤ len(K) ≤ 50` against `Decimal.quantize(ROUND_HALF_UP)`) |

Edge cases: substring match, not word match — `"case"` matches `"cases"`
(deliberately lenient; the alternative needs stemming, which is a quality
claim). Matching is case-insensitive via lower-casing both sides. A
keyword that is itself blank after stripping was already dropped in §5.2.
If the source list had more than 50 entries, only the first 50 are `K`,
and the breakdown records `truncated: true`.

---

## 7. Total score contract

### 7.1 Scale and calculation

- `earned` = D1 + D2 + D3 + (D4 if applicable), an integer.
- `applicableMax` = **100** if D4 is applicable, else **75**.
- `score = (earned × 100 + applicableMax ÷ 2) ÷ applicableMax` using
  integer arithmetic — i.e. **round half up** to an integer in 0–100.
  Stated as a formula so no implementation reaches for Python's `round()`,
  which rounds half to even.

Worked example (the `StubProvider` fixture, §7.5): no merchant keywords,
D1 = 25, D2 = 6, D3 = 25 → earned 56, applicableMax 75 →
`(5600 + 37) ÷ 75 = 75` (integer division of 5637 by 75). The formula was
checked exhaustively for every `earned` at both maxima against
`Decimal.quantize(ROUND_HALF_UP)`; `applicableMax ÷ 2` being 37 rather than
37.5 never changes the result because `earned × 100` is always a multiple
of 25.

### 7.2 Why an inapplicable dimension rescales instead of scoring 0 or 25

Scoring D4 as 0 when the merchant provided no keywords would penalise the
merchant, not the version. Scoring it as 25 would reward absence. Rescaling
to the applicable maximum keeps 0–100 meaningful for every product, and the
breakdown records `applicableMax` so a reader can see which scale applied.
Because the original and every AI version of the same product are scored
against the same keyword list at the same moment (§8), D4 is applicable to
all of them or none, and the delta is always between like scales.

### 7.3 Sub-scores

The breakdown (§9) carries every dimension's points, maximum, and
applicability, plus the D3 check flags and D4's `matched`/`total`. The
total alone would be unexplainable.

### 7.4 `seoFormat` — informational, not scored

For versions that carry Stage 4 SEO keys, the breakdown also records three
booleans, each a format check against the bound **the seeded
`seo_optimizer` template itself requests** — not a quality judgment:

| Flag | Rule |
|---|---|
| `seoTitleWithinRequestedBound` | `seoTitle` non-blank and `len(seoTitle) < 60` |
| `seoDescriptionWithinRequestedBound` | `seoDescription` non-blank and `len(seoDescription) < 155` |
| `keywordsPresent` | `keywords` non-blank |

On the original (no SEO keys) `seoFormat` is `null`. These flags do not
enter `earned`. With `StubProvider` all three are always `true`
(the stub string is 49 characters), which is exactly why they are not
scored.

### 7.5 Predictability, and the canonical fixture

The score is a pure function, so the same content always produces the same
number, and scoring the same version twice is a no-op in effect.

**`StubProvider` fixture.** The stub returns the 49-character string
`[STUB-AI] synthetic completion (prompt <8 hex>).` for every prompt (6
tokens: `stub`, `ai`, `synthetic`, `completion`, `prompt`, `<hex>`). An AI
version generated by the stub therefore scores:

| Dimension | Points | Because |
|---|---|---|
| D1 | 25 | title length 49 → tier 20–120 |
| D2 | 6 | description length 49 → tier 1–49 |
| D3a | 8 | no token repeats |
| D3b | 8 | no bigram repeats |
| D3c | 5 | 6 tokens < 20 → not evaluable, awarded |
| D3d | 4 | title and description differ (different prompt digests) |
| D4 | 0 if applicable (stub text contains no merchant keyword) · N/A otherwise | |
| **score** | **56** with merchant keywords (`56/100`) · **75** without (`(5600+37)÷75`) | |

This fixture is asserted exactly by a unit test and by an integration test
(§16). It is also the plainest statement of §5.3: with the stub, the AI
side of every delta is a constant.

---

## 8. Improvement contract

For every **AI-generated** version, Stage 5 stores three things:

| Key | Semantics |
|---|---|
| `qualityScore` | This version's own score (§7.1) |
| `qualityBaseline` | `{ "versionNumber": 1, "score": <int> }` — the **original** version's score, **recomputed from the original's immutable `content` at the moment this AI version is scored**, with the same merchant keyword list. Never copied from a number stored on the original: a stored number could carry an older `qualityScoreVersion`, and the delta must compare like with like. |
| `qualityDelta` | `qualityScore − qualityBaseline.score`. Positive means this version scores higher than the original on this rubric. Integer, may be negative. |

For the **original** version, `qualityScore` is stored (its own content,
scored at snapshot time) and `qualityBaseline` / `qualityDelta` are absent
— it is the baseline.

The delta is a property of the version, fixed at creation. Activating,
rolling back, or generating later versions never changes it.

---

## 9. Persistence

**Where:** `ProductVersion.content` JSONB. **No migration.** The column was
made JSONB in Stage 3 precisely so later stages could add keys, Stage 4 did
so, and Stage 5 adds a second group. `ProductVersion` is already
tenant-scoped, immutable, and cascades with the product; a separate table
would duplicate all of that for five keys.

**Keys added to `content`** (camelCase, matching the existing keys):

```jsonc
{
  "title": "...", "description": "...",                 // Stage 3
  "seoTitle": "...", "seoDescription": "...", "keywords": "...",   // Stage 4, AI versions
  "qualityScoreVersion": 1,                              // Stage 5 — rubric version (int)
  "qualityScore": 56,                                    // Stage 5 — int 0–100
  "qualityBreakdown": {                                  // Stage 5
    "earned": 56,
    "applicableMax": 100,
    "dimensions": {
      "title":           { "points": 25, "max": 25, "applicable": true, "length": 49 },
      "description":     { "points": 6,  "max": 25, "applicable": true, "length": 49 },
      "repetition":      { "points": 25, "max": 25, "applicable": true,
                           "checks": { "titleNotStuffed": true, "descriptionNotPhraseStuffed": true,
                                       "descriptionNotDominated": true, "descriptionDistinctFromTitle": true } },
      "keywordCoverage": { "points": 0,  "max": 25, "applicable": true,
                           "matched": 0, "total": 2, "source": "search_topics",
                           "keywords": ["canvas case", "camera protection"], "truncated": false }
    },
    "seoFormat": { "seoTitleWithinRequestedBound": true,
                   "seoDescriptionWithinRequestedBound": true, "keywordsPresent": true }   // or null
  },
  "qualityBaseline": { "versionNumber": 1, "score": 41 },   // AI versions only
  "qualityDelta": 15                                        // AI versions only: 56 − 41
}
```

**Immutability.** Written once, inside the same `versions.create(...)` call
that writes the Stage 3/4 keys — a version row is never updated afterwards,
which is the existing `ProductVersion` contract ("a correction is a new
version"). If the rubric changes, `qualityScoreVersion` is incremented in
code and only versions created from then on carry the new number; existing
rows are **never recomputed in place**. A reader comparing two versions with
different `qualityScoreVersion` values is comparing different rubrics and
the breakdown says so.

**Rows written before Stage 5 ships** have none of these keys. They are
read as `null` on the wire (§10) and are never back-filled.

**Rollback / activation.** `_apply_active_version` continues to read only
`title` and `description`. The quality keys ride along in the version row
and are never copied to `Product`. Rolling back to the original does not
"reset" any score; each version keeps its own.

---

## 10. API contract — Option A, no new endpoint

**Decision: the score is produced inside the existing optimize flow**, at
the two places a version is already written:

1. `_ensure_original_snapshot` — the original is scored when it is lazily
   created (`qualityScore`, no delta).
2. `optimize_product` — after the three executions succeed and before
   `versions.create`, the AI content is scored, the original's content is
   re-scored for the baseline, and all Stage 5 keys are included in the
   `content` passed to `create`.

Why not a separate service or endpoint (Option B): the score is a property
of an immutable snapshot and costs microseconds; computing it anywhere
other than at creation would either recompute on every read or require a
cache with its own invalidation. Phase 9's own sequencing puts `score`
directly after `generate` (§3 of `PHASE_9_PLAN.md`, Stage 7's pipeline), and
Stage 3 already put version creation inside optimize. Adding an endpoint
would be a new surface with no caller until Stage 10.

**Existing endpoints, unchanged in path, method, status code, and
authorization:**

| Endpoint | Change |
|---|---|
| `GET /products/{id}/versions` (`RequireViewer`) | items may carry the new optional fields below |
| `POST /products/{id}/optimize` (`RequireAdmin`, 201) | response `version` carries them |
| `POST /products/{id}/versions/{version_id}/activate` (`RequireAdmin`) | unchanged; returns `ProductDetailRead`, which has no quality fields |

**`ProductVersionRead` gains, all optional (`null` on pre-Stage 5 rows):**

```
qualityScoreVersion: int | None
qualityScore:        int | None
qualityDelta:        int | None            # None on the original and on legacy rows
qualityBaseline:     ProductVersionQualityBaselineRead | None   # { versionNumber: int, score: int }
qualityBreakdown:    ProductVersionQualityBreakdownRead | None  # typed mirror of §9's object
```

Typed, not `dict[str, Any]`: CLAUDE.md §6 requires explicit response
schemas, and the breakdown's shape is fixed by this plan. `ProductRead` /
`ProductDetailRead` do **not** gain quality fields — the score belongs to a
version, and denormalising the active version's score onto the product is
a Stage 10 question if the UI needs it.

`ProductOptimizeRequest` is unchanged (`tone` only).

---

## 11. Failure semantics

| Situation | Behaviour |
|---|---|
| Provider needed? | **Never.** The scorer imports nothing from `app.ai`. A test asserts the scoring module has no import from `app.ai`, `app.services.prompt`, or `httpx`. |
| Network / I/O / session? | **None.** Pure function over in-memory values. |
| `content` value not a `str` (`None`, number, list, nested dict) | Treated as blank for that key. The function is **total**: it must not raise for any `dict` input, including `{}`. |
| `content` is not a `dict` | Treated as `{}`. Cannot happen through the ORM (`content` is `dict` by column type), but the function is written to be total anyway. |
| Merchant keyword value not a `str` (a non-string inside `search_topics`) | Skipped. |
| Scorer raises anyway (a bug) | It is called **before** `versions.create`, so no version row is written; the request fails with the platform's standard unhandled-error envelope; the request transaction rolls back per `get_db_session`. `Product.ai_status` is **not** set to `FAILED` — generation did not fail, scoring did — and no partial version, score, or delta is persisted. There is no separate "scoring failed" status; a total function has no expected failure mode to name. |
| Generation fails (Stage 4 path) | Unchanged: `ai_status = FAILED`, no version, 503. Scoring is never reached. |
| Last good version / cache | Unchanged: a failed optimize leaves the previously active version, its cached title/description, **and its stored score** as they were. |
| Original snapshot for a product first optimised after Stage 5 | Scored at creation. |
| Original snapshot created before Stage 5 (legacy row) | Never back-filled; `qualityScore` reads `null`. Its **baseline for new AI versions is still computed** (recomputed from its content, §8), so new AI versions of an old product get a real delta. |

Atomicity: the AI version's Stage 3, 4, and 5 keys are written in one
`create` call within one request transaction. There is no state in which a
version exists without its score.

---

## 12. Tenant isolation

**No new repository.** The scorer has no persistence of its own;
`ProductVersionRepository` (tenant-scoped since Stage 3) writes and reads
the row, and `ProductRepository.get_by_id_or_raise` remains the ownership
check on every path (404, not 403, for another tenant's product). Existing
scoping tests are re-run unchanged (§16). No unscoped query is added.

---

## 13. Merchant-controlled data protection

Stage 5 writes **nothing** to `Product`. Specifically not:

`Product.seo_title`, `Product.seo_description`, `Product.meta_keywords`,
`Product.tags`, `Product.search_topics`, `Product.seo_planning`,
`Product.title`, `Product.description`, `Product.supplier_title`,
`Product.supplier_description`, nor the five AI cache fields beyond what
`_apply_active_version` already does.

It **reads** `search_topics` / `tags` / `meta_keywords` (§5.2) and records
the derived list inside the version breakdown — a copy in the version, not
a change to the product. The Stage 4 integration tests that seed merchant
fields through the existing PATCH and assert they survive optimise and
rollback are re-run, and extended to assert they survive scoring (§16).

---

## 14. M2A protected behaviour

Unchanged and untouched by Stage 5, with no second write path into any of
them:

`expectedUpdatedAt`; `ProductService.update_product` /
`update_draft` optimistic concurrency; the 409 conflict flow; autosave
freeze during conflict; "reload latest"; "review my changes"; the
draft-product-editor conflict state machine; publish integrity. Stage 5
touches no file under `app/api/v1/drafts/`, no `ProductService` method, no
frontend file.

---

## 15. Frontend

**None in Stage 5.** The current roadmap (`PHASE_9_PLAN.md` §3) places every
AI-facing surface in Stage 10 (AI Studio); `PROJECT_ROADMAP.md` has no
earlier requirement to display a version score. The wire fields in §10 are
additive and optional, so no TypeScript change is required for compile
correctness — the same reasoning Stage 4 recorded. `frontend/types/api.ts`
is therefore left as-is, and the drift (now three Stage 4 fields plus the
Stage 5 fields) is recorded once more as M4 for Stage 10 to close.

---

## 16. Testing contract

### 16.1 Unit — `tests/unit/test_optimization_quality.py` (new; pure, no database)

Every rule has a test that asserts an exact integer. Fixtures are literal
strings built to land on a boundary.

**Normalisation**
1. `plain()` strips HTML and collapses whitespace: `"<p>a  b</p>\n<p>c</p>"` → `"a b c"`.
2. Non-`str` values are blank: `None`, `0`, `[]`, `{}` each → `""`.

**D1 — title tiers** (one test per row, lengths exactly 0, 1, 9, 10, 19, 20, 120, 121, 255, 256): expected points 0, 6, 6, 12, 12, 25, 25, 15, 15, 0.

**D2 — description tiers** (lengths 0, 1, 49, 50, 149, 150, 2000, 2001, 5000, 5001): expected 0, 6, 6, 12, 12, 25, 25, 18, 18, 12. Plus: `"<p></p>"` → 0 (HTML-only is blank); tags do not count toward length.

**D3**
- D3a: `"Case Case Case for Phone"` → 0 (token ×3); `"Case Case for Phone"` → 8 (×2 passes); token `"ab"` repeated 5× → 8 (length < 3 ignored).
- D3b: `"good fit good fit good fit"` → 0; `"good fit good fit"` → 8.
- D3c: 19 tokens with one token ×10 → 5 (not evaluable); 20 tokens with one token ×6 (30 %) → 0; 20 tokens with one token ×5 (exactly 25 %) → 5.
- D3d: description equal to title ignoring case/whitespace → 0; differs by one character → 4; blank description → 0.
- Blank title → D3a still 8; blank description → D3b 8, D3c 5, D3d 0.

**D4**
- No merchant keywords → `applicable=False`, `applicableMax=75`.
- `K=["canvas case","camera protection"]`, neither in text → 0; one → `round_half_up(12.5)=13`; both → 25.
- Case-insensitive: `"Canvas Case"` in text matches `"canvas case"`.
- Substring: keyword `"case"` matches text `"cases"`.
- Source priority: `search_topics` beats `tags` beats `meta_keywords`; `meta_keywords` is split on commas; blanks dropped; duplicates removed; a non-`str` entry skipped.
- 60 entries → `K` is the first 50 and `truncated=True`.

**Total**
- Rounding: earned 62 / 75 → 83; earned 1 / 75 → 1 (`(100+37)÷75=1`); earned 74 / 75 → 99 (`(7400+37)÷75=99`).
- Determinism: scoring the same inputs twice yields equal results, and `qualityScoreVersion == 1`.
- Totality: `score(content={})`, `score(content={"title": 5, "description": ["x"]})` return a result without raising.
- **Canonical stub fixture**: content `{"title": "[STUB-AI] synthetic completion (prompt 00000000).", "description": "[STUB-AI] synthetic completion (prompt 11111111)."}` → 75 without keywords, 56 with `K=["anything"]`; `seoFormat` all `true` when the SEO keys hold the same string.
- `seoFormat` is `null` when no SEO key is present; `seoTitleWithinRequestedBound` is `False` for a 60-character `seoTitle` and `True` for 59.

**Module hygiene**
- The scoring module's imports contain none of `app.ai`, `app.services.prompt`, `httpx`, `asyncio`, `random`, `datetime`.

### 16.2 Integration — extend `tests/integration/test_product_optimization.py`

1. Optimize → the returned `version` has `qualityScoreVersion == 1`,
   `qualityScore == 75` (the imported fixture product has no merchant
   keywords — import populates none of `search_topics` / `tags` /
   `meta_keywords`) or `56` after seeding `searchTopics` via PATCH; `qualityBaseline.versionNumber == 1`;
   `qualityDelta == qualityScore − qualityBaseline.score`.
2. The original (version 1) in `GET …/versions` carries `qualityScore` and
   `null` `qualityDelta` / `qualityBaseline`.
3. Second optimize → version 3 has its own score and the **same**
   `qualityBaseline.score` as version 2 (baseline recomputed from the same
   immutable original).
4. Rollback to version 2 → `GET …/versions` shows every version's quality
   fields unchanged; `ProductDetailRead` has no quality fields.
5. SEO-only failure (Stage 4's `_SeoFailsProvider`) → no version, `failed`,
   and the previously active version's stored score unchanged.
6. **Merchant fields unchanged**: seed `seoTitle`, `seoDescription`,
   `metaKeywords`, `tags`, `searchTopics` via PATCH; optimize; re-read the
   product — all five identical; `searchTopics` appears only inside the
   version breakdown's `keywords` list.
7. **`quality_scorer` and `image_analyzer` unused**: after optimize, the set
   of `PromptExecution.prompt_name` in the test transaction is exactly
   `{product_title_generator, product_description_generator, seo_optimizer}`.
8. A legacy row: insert a version through the repository with Stage 3-only
   content, read it through the endpoint → quality fields `null`; then
   optimize → the new AI version has a real `qualityDelta` computed against
   that legacy original.
9. Existing tests re-run unchanged: cross-tenant optimize / versions /
   activate → 404; viewer → 403; `AI_PROVIDER=openai` → 503 with no version;
   supplier title / description untouched; Stage 4 SEO keys still present.

### 16.3 Scoping

`tests/unit/test_product_version_repository_scoping.py` and
`tests/unit/test_product_repository_scoping.py` re-run unchanged.

---

## 17. Out of scope

- real providers (`openai`, `anthropic`, `gemini`, `local`)
- any call to the `quality_scorer` prompt
- model-assisted score explanation
- image analysis / `image_analyzer` / Stage 6
- Stage 7 full pipeline (preview → approve → publish)
- Stage 9 Celery bulk optimisation
- Stage 10 AI Studio / any frontend
- rewriting, extending, or re-plumbing `seo_score.py`, its endpoint, or the editor SEO panel
- a migration or product column
- back-filling scores onto existing version rows
- denormalising a score onto `Product`
- workflow changes, `main`, deployment
- `npm audit fix` / `npm audit fix --force`

---

## 18. Implementation sequence (future branch `feat/phase-9-stage-5-quality-scoring`)

Small commits, none rewritten after review begins:

1. `feat(ai): add the deterministic optimisation-quality scorer` —
   `app/services/optimization_quality.py`: normalisation helpers, D1–D4,
   `seoFormat`, total, `QualityResult` dataclass, `QUALITY_SCORE_VERSION = 1`.
   Pure module; no session; unit tests for every rule (§16.1).
2. `feat(ai): score versions at creation and record the delta` —
   `ProductOptimizationService`: merchant term extraction with Stage 4's
   source order (one shared helper, with the §5.2 normalisation effect
   recorded — or a Stage 5-only copy if review prefers Stage 4 untouched),
   score the original in `_ensure_original_snapshot`, score the AI content
   and baseline in `optimize_product`, include keys in `create`. Model
   comment on `content` updated.
3. `feat(api): expose version quality fields on ProductVersionRead` — typed
   optional fields and nested read models.
4. `test(ai): integration coverage for scoring, deltas, legacy rows, and
   unchanged protected behaviour` (§16.2).
5. `docs(ai): record Phase 9 Stage 5 completion` — completion report;
   `PHASE_9_PLAN.md` progress entry; `PROJECT_ROADMAP.md` Phase 9 row and
   `CHANGELOG.md` entry (the latter two were left stale by Stage 4 and are
   owed under CLAUDE.md §9).

---

## 19. Acceptance gates

Backend, all required:

- `ruff check .`
- `ruff format --check .`
- `mypy app` (strict)
- targeted Stage 5 tests: `tests/unit/test_optimization_quality.py`,
  `tests/integration/test_product_optimization.py`,
  `tests/unit/test_product_optimization_variables.py`, both scoping files
- full `pytest`
- `scripts/check_secrets.py`
- `git diff --check origin/develop...HEAD`

Frontend gates (`npm run lint`, `typecheck`, `build`, relevant Playwright)
only if a frontend file changes — which this plan says none will.

Reported as in Stage 4: what ran, the exact numbers, and any environmental
failure named and explained rather than omitted.
