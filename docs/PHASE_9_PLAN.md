# Phase 9 — AI product optimization: plan

Turning imported supplier products into listings worth publishing.

Written after auditing the current implementation. Nothing below is assumed
from the roadmap; each statement was checked against the code.

---

## 1. Audit findings

| Checked | Result |
|---|---|
| AI / LLM code in `app/` | **None.** Grep matches (`mailer.py`, `email_verification.py`) are the substring "ai" in "mail" |
| AI SDK in `pyproject.toml` | **None declared** |
| AI provider key configured | **None** — `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `GEMINI_API_KEY`, `AI_PROVIDER` all absent from `.env` |
| Phase 9 fields on `Product` | **None.** No SEO, slug, vendor, tags, collections, or version columns |
| Latest migration | `0009` — Phase 9 starts at `0010` |
| Backend test baseline | **652 collected** |
| Branch | `develop` at `63e9d98`; `main` untouched at `82de677` |

Phase 9 is therefore greenfield. Nothing to preserve inside it, and everything
around it — tenancy, encryption, the AliExpress and Shopify integrations, the
product catalogue — must be left exactly as it is.

### The finding that shapes the phase

**There is no AI provider key, so no AI output can be live-verified.**

Every other phase closed by exercising the real dependency: Phase 3.6 completed
a real OAuth round trip, Phase 4 parsed a captured AliExpress payload. Phase 9
cannot do that without a key, and the project's rules are explicit — *never
claim live AI verification without a real API call*.

This is not a reason to stall. It is a reason to design so that the unverifiable
part is as small and as isolated as possible: everything except the HTTP call to
a model can be tested deterministically, and the provider boundary is where the
untested surface stops.

---

## 2. Architecture

### The dependency rule

```
api → services → repositories → models
                     ↑
        ai/ (providers, prompts, pipeline)
```

`app/ai/` is an integration package, sitting where `integrations/aliexpress/`
sits. It may import `app.models`; nothing in `app.models` may import it. A
product does not know it was optimised by a model, exactly as it does not know
it came from AliExpress.

### Provider abstraction

```python
class AIProvider(Protocol):
    async def complete(self, request: CompletionRequest) -> CompletionResult: ...
    async def analyse_image(self, request: ImageRequest) -> ImageResult: ...
```

Concrete providers: `OpenAIProvider`, `AnthropicProvider`, `GeminiProvider`,
`LocalProvider`, and `StubProvider`.

**`StubProvider` is not a test double.** It is the provider selected when no key
is configured, and it returns deterministic, obviously-synthetic output clearly
marked as such. That matters for two reasons: development works without a key,
and — more importantly — a deployment that forgets its key produces visibly fake
copy rather than silently plausible copy that someone publishes.

Selected by configuration (`AI_PROVIDER`), never imported directly by a service.
A service that names a provider is a service that cannot be switched.

### Prompt management

Prompts are data, not string literals. Stored in the database with a version, a
template body, declared variables, and the model they were written for.

Rendering is explicit and validated: a template whose variables are not all
supplied fails loudly at render time rather than sending `{title}` to a model
and paying for the answer.

Every generation records **which prompt version produced it**, which is the only
way to answer "why did output quality change last Tuesday".

### Versioning

Every optimisation writes a new `product_versions` row: a snapshot, an actor
(user or AI), a reason, and the prompt version if AI. Restore writes a *new*
version rather than deleting forward history — an audit trail that can be
rewritten is not one.

---

## 3. Sequencing

Each stage is independently testable and independently committable. Quality
gates run after each, not only at the end.

| # | Stage | Deliverable |
|---|---|---|
| 1 | Provider abstraction | `AIProvider` protocol, `StubProvider`, config, tests |
| 2 | Prompt management | Model, repository, renderer, migration `0010` |
| 3 | Product extension | SEO/slug/vendor/tags/collections columns, `product_versions`, migration `0011` |
| 4 | Generation services | Title, description, SEO — provider-agnostic |
| 5 | Quality scoring | Deterministic rubric; **no model call** |
| 6 | Image analysis | Deterministic checks first; model-backed captions behind the provider |
| 7 | Pipeline | Analyse → generate → score → preview → approve → publish |
| 8 | API | Endpoints on the existing auth and tenant middleware |
| 9 | Celery | Bulk optimisation, progress tracking, retries |
| 10 | Frontend | AI Product Studio |
| 11 | Docs, gates, tag | Completion report |

Stage 5 is deliberately model-free. A quality score that varies run to run cannot
be used to decide whether an edit improved anything, and a deterministic rubric
is both cheaper and more useful than asking a model to grade itself.

Stage 6 splits the same way: blur, duplicate and watermark detection are image
processing, not language. Only captions and alt text need a model.

---

## 4. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| **No AI key — nothing model-backed can be verified** | **High** | Provider boundary keeps the unverifiable surface to one method; everything else deterministic and tested |
| Cost: bulk optimisation across a catalogue | High | Per-tenant quotas before bulk ships; preview before apply |
| Prompt injection via supplier text | **High** | Supplier titles and descriptions are untrusted input. Never concatenate into a prompt without delimiting; treat model output as data, never as instructions |
| Model output rendered as HTML | High | Same rule as the supplier description already in the catalogue: stored, never rendered unsanitised |
| Latency: generation exceeds a request | Medium | Single generations inline; bulk through Celery |
| Provider API drift | Medium | Contract captured per provider, as with AliExpress |
| Scope: 17 sections in one phase | **High** | Staged above; each stage is shippable alone |

The prompt-injection risk deserves emphasis. Supplier product text is written by
third parties and already flows into this system. The moment it flows into a
prompt, "ignore previous instructions" becomes a live attack, and the blast
radius is whatever the model is allowed to do.

---

## 5. Existing debt that touches Phase 9

| Item | Effect |
|---|---|
| **C1** — deployment never executed | Unchanged; Phase 9 adds services to a stack nobody has started |
| M13 — flaky E2E | New Playwright specs must not add to it |
| M15 — Celery not observed end-to-end | Stage 9 adds tasks to that unverified path |
| M4 — hand-written frontend types | New AI types must stay in step |

---

## 6. Verification plan

| Layer | How | Live? |
|---|---|---|
| Provider abstraction | Unit tests against `StubProvider` | n/a |
| Prompt rendering | Unit tests, including missing-variable failure | n/a |
| Quality scoring | Unit tests — deterministic, so exactly assertable | n/a |
| Image checks | Unit tests on committed fixtures | n/a |
| Pipeline | Integration tests, stage by stage | n/a |
| API | Integration tests through real HTTP, real database | ✅ |
| Tenant isolation | SQL-compile tests per new repository | ✅ |
| Frontend | lint, typecheck, build, Playwright | ✅ |
| **Model output quality** | **Cannot be verified without a key** | ❌ |

The last row will be stated as a gap in the completion report, not omitted. If a
key is provided later, one real call per generator closes it and the result gets
committed as a fixture — the same approach that closed M10 for AliExpress.

---

## 7. Scope reality

Seventeen sections is several phases of work by the standard this project has
held so far: Phase 4 was *one* contract layer plus one domain model, and it took
a full session including the discovery that keyword search does not work.

The staging above exists so that each stage lands finished — migrations applied,
tests passing, gates green — rather than seventeen half-built systems. A phase
tag should mean the work behind it was verified, and `phase-9-complete` will not
be created until every stage in §3 is done and its verification executed.

---

## 8. Progress

### Stage 1 — Provider abstraction: done

| Delivered | Where |
|---|---|
| `AIProvider` protocol, request/result contract types | `app/ai/provider.py` |
| `AIError`, `AIProviderNotConfiguredError` | `app/ai/exceptions.py` |
| `StubProvider` — deterministic, clearly-synthetic | `app/ai/stub_provider.py` |
| `AIProviderName` enum, `AISettings` (`AI_*`) | `app/core/config.py` |
| `get_ai_provider()` resolver | `app/ai/factory.py` |
| Unit tests (29) | `tests/unit/test_ai_{provider,stub_provider,config,factory}.py` |

**A scope decision worth recording.** §2's architecture sketch named five
concrete providers (`OpenAIProvider`, `AnthropicProvider`, `GeminiProvider`,
`LocalProvider`, `StubProvider`). Only `StubProvider` was built. `AIProviderName`
lists all five so the configuration surface will not change shape later, and
`get_ai_provider()` raises `AIProviderNotConfiguredError` — loudly, not a
silent fallback to the stub — for any of the other four. Building HTTP clients
against three different real APIs with no key to verify any of them against
would have meant a stage that is all code and no confidence; CLAUDE.md's "no
abstraction without a second caller" applies here too — a concrete provider
gets built in the stage that first has a caller for it (stage 4, generation
services), not ahead of one. This is narrower than §2 implied, stated here
rather than left for someone to notice later.

**Verified:** `ruff check`, `ruff format --check`, `mypy app` (strict, 150
files), `pytest` (681 passed — 652 baseline + 29 new), `scripts/check_secrets.py`.
All against `StubProvider`; no live model call was made or claimed, because
none is possible without a key (§1's finding stands unchanged).

### Stage 2 — Prompt management: done

| Delivered | Where |
|---|---|
| `AIPrompt` (versioned, reference data), `PromptExecution` (tenant-scoped audit trail), `PromptExecutionStatus` | `app/models/ai_prompt.py` |
| `PromptRenderer` — safe `{{variable}}` extraction and substitution | `app/ai/prompt_renderer.py` |
| `MissingPromptVariablesError` | `app/ai/exceptions.py` |
| `PromptRepository` (unscoped), `PromptExecutionRepository` (tenant-scoped) | `app/repositories/ai_prompt.py` |
| `PromptService` — create, version, activate/rollback, history, list, test-render | `app/services/prompt.py` |
| 7 endpoints behind `RequireAdmin` | `app/api/v1/ai/router.py`, mounted in `app/api/v1/router.py` |
| Migration `0010` — both tables, partial unique active index, 5 seeded prompts | `alembic/versions/20260802_1942_0010_ai_prompt_management.py` |
| Tests (35 new: 12 renderer, 5 repository-scoping, 18 integration — routes, permissions, tenant isolation) | `tests/unit/test_prompt_renderer.py`, `tests/unit/test_prompt_execution_repository_scoping.py`, `tests/integration/test_ai_prompts.py` |

**Architecture decisions worth recording.**

*Versions are rows, not a nested history table.* One `AIPrompt` row per
version, sharing a `name`; "history" is every row for that `name`, "rollback"
is activating an older version through the exact same endpoint that activates
a newly-created one. A separate `PromptVersion` child table was considered
and rejected — it would duplicate every field on `AIPrompt` for no behaviour
this stage needs, the KISS violation CLAUDE.md warns against.

*Required variables are parsed from the template, never stored as a column.*
The brief's own field list for `ai_prompts` omits a variables column, and a
stored list would be a second copy of information the template text already
contains exactly — one that could silently drift from it. `PromptRenderer`
is the only source of truth.

*"At most one active version" is a database constraint, not a service-layer
check.* `uq_ai_prompts_name_active` is a Postgres partial unique index
(`WHERE active`). `PromptRepository.activate()` still checks first, for a
fast, friendly error — but the index is what actually stops two concurrent
activations from both succeeding.

*The rendering engine is deliberately not a template language.* `{{name}}`
substitution only — no conditionals, no expressions, no `str.format()`
(which permits attribute-chain injection through a hostile value). Every
generation prompt will eventually carry supplier-authored text; the smallest
substitution grammar is the smallest surface for that text to do anything
other than sit there as data. See `app/ai/prompt_renderer.py`'s docstring.

*"Test prompt rendering" with `execute=true` calls `get_ai_provider()`, which
Stage 1 wires to `StubProvider` — nothing else is configured.* This is how
the render → call → record execution pipeline gets exercised end-to-end
without violating "no real AI API calls yet": `StubProvider` makes no network
call, and every execution it produces is stored with `is_synthetic=true`. It
proves the plumbing, not generation quality — §6's verification table is
unchanged by this stage.

**Known limitations, stated rather than hidden.**

1. **Every endpoint requires `RequireAdmin`, which is a *tenant* admin, not a
   platform operator.** `ai_prompts` is platform-global — one tenant's admin
   editing `product_title_generator` changes what every other tenant's
   generations produce. This platform has no role above tenant-admin yet
   (`PROJECT_ROADMAP.md`'s "Admin panel: platform operations across tenants"
   is an unscheduled later phase). `RequireAdmin` is the strongest existing
   check and what the brief asked for ("respect tenant/admin permissions"),
   but it does not close this gap — a tenant admin has real, unaudited
   leverage over every other tenant's output today. This needs a genuine
   platform-operator role before production use with multiple real tenants.
2. **`PromptExecution` rows are written but not independently readable.**
   The brief's admin-capability list (list, create, update, activate,
   history, test) does not include an execution log viewer, so none was
   built — adding one now would be ahead of a caller. Tenant isolation is
   still verified: the SQL-compile test plus an integration test that reads
   the table directly through the repository (`TestTenantIsolation` in
   `test_ai_prompts.py`), which is more than the compile test alone proves.
3. **The `/history` endpoint paginates in Python, not SQL** (`list_versions`
   fetches every version, then the router slices). Deliberate: per-name
   version counts are small and admin-curated, and real OFFSET/LIMIT for a
   dataset that never approaches page 2 in practice would be complexity
   without a caller who needs it.
4. **`target_model` is stored, never read.** Recorded per prompt version for
   a future auditor ("what model was this tuned for"); no code branches on
   it yet.
5. **`quality_scorer` is seeded but Stage 5's quality score will not call
   it** — quality scoring is designed to be model-free (§3). It exists so
   the name is reserved in the same library, documented in the migration
   itself so the discrepancy is not a surprise later.

**Verified:** `ruff check`, `ruff format --check`, `mypy app` (strict, 157
files), `pytest` (716 passed — 681 baseline + 35 new), migration `0010`
round-tripped (`upgrade → downgrade → upgrade`, all 5 seeded prompts intact
and re-seeded correctly after both cycles), `scripts/check_secrets.py`. No
real AI provider call was made or claimed — every execution in every test
carries `provider=stub`, `is_synthetic=true`.

**Stage 3 — Product extension — begins next.**
