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

**Stage 1 begins next.**
