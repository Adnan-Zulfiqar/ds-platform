# Phase 9 Stage 7 — completion report

**Pipeline: preview → exact-candidate approve → overlay publish.**

Status: **COMPLETE / MERGED / GREEN.**

Merged into `develop`. Production undeployed — `main` unchanged.
Stages 8–11 not started. Claude has not reviewed Stage 7. Cursor is the
temporary implementation + self-review agent; this report is not a Claude
review.

| | |
|---|---|
| Date | 2026-09-19 |
| Accepted plan PR | [#18](https://github.com/Adnan-Zulfiqar/ds-platform/pull/18) |
| Accepted plan head | `acc0da4a4d775073b930bca50aa9f4959c44af0b` |
| Plan merge / baseline | `develop` @ `85e11772aeba49d4f801ac23a4eb6ca114f7620c` |
| Post-merge plan CI | [35465267981](https://github.com/Adnan-Zulfiqar/ds-platform/actions/runs/35465267981) — 10/10 SUCCESS |
| Implementation branch | `feat/phase-9-stage-7-pipeline` |
| Implementation PR | [#19](https://github.com/Adnan-Zulfiqar/ds-platform/pull/19) |
| Reviewed implementation head | `d42c1a4606d227e79cfb0d1232534606dd39d508` |
| Independent implementation review | PASS — BLOCKER 0, HIGH 0, MEDIUM 0 |
| PR CI | [35472626657](https://github.com/Adnan-Zulfiqar/ds-platform/actions/runs/35472626657) — 10/10 SUCCESS |
| Implementation merge | `ffa0d6e37db218c85a1facdc265087553c694e02` (2026-09-19T22:49:43Z; parents `85e11772`, `d42c1a46`) |
| Post-merge develop CI | [35474423524](https://github.com/Adnan-Zulfiqar/ds-platform/actions/runs/35474423524) — 10/10 SUCCESS |
| Contract | [PHASE_9_STAGE_7_PLAN.md](PHASE_9_STAGE_7_PLAN.md) |
| Migration | **none** — Alembic head remains `0033` |
| Deployment | **NO** |
| Stage 8 / 9 / 10 / 11 | **NOT STARTED** |
| AI involvement | `StubProvider` generation allowed for preview/approve. Pipeline publish fail-closed on `isSynthetic is True` or `ai_provider == "stub"`. Overlay publish tests use fixture `ai_provider="test"` / `isSynthetic=False` — not a live-model claim |

---

## 1. Commit sequence

From `85e11772`:

1. `0eb1e97` `feat(ai): add Stage 7 pipeline candidates`
2. `86a9511` `feat(shopify): support approved listing overlay`
3. `ffad8f7` `feat(ai): add Stage 7 pipeline preview`
4. `25adbd2` `feat(ai): add exact pipeline approval`
5. `bcbc12f` `feat(ai): publish approved pipeline candidates`
6. `9c2ff3e` `test(ai): harden Stage 7 pipeline contract`
7. `c75cc4d` `docs(ai): complete Stage 7 implementation report`
8. `776b123` `fix(ai): serialize all Stage 7 activation paths`
9. `d42c1a4` `docs(ai): update Stage 7 remediation report`

Then merged into `develop` as `ffa0d6e3` (true merge; parents `85e11772`,
`d42c1a46`).

---

## 2. What landed

```
ProductPipelineService.preview   → analyse → generate_candidate (inactive)
ProductPipelineService.get_preview → compose from an exact marked row
ProductPipelineService.approve   → Product FOR UPDATE → activate exact row
ProductPipelineService.publish   → Product FOR UPDATE first → overlay
```

| Piece | Behaviour |
|---|---|
| Shared generator | `_generate_version` in `product_optimization.py`; Stage 4 trio unchanged; Stage 5 scores candidate + original |
| Legacy `optimize_product` | Still auto-activates an **unmarked** row; three prompts run **outside** Product `FOR UPDATE`; then Product lock → version activate → cache. `AIError` still sets `ai_status=FAILED` without erasing last good cache |
| `generate_candidate` | Inactive pipeline-marked row; does not activate; does not set FAILED |
| Strict parser | `parse_pipeline_candidate_metadata` — exact int `1` (not bool), offset-aware ISO-8601, exact bool `isSynthetic` |
| `activate_version` | Product `FOR UPDATE` first; refuses parsed or corrupt pipeline rows; unmarked ORIGINAL/legacy activation unchanged |
| Overlay | `ShopifyListingOverlay(title, body_html)` only; merchant SEO/tags/images/variants unchanged |
| Approve | Lock first; `populate_existing` version read; already-active exact row is a no-op; first sibling wins |
| Publish | `expectedUpdatedAt` required **before** the Product lock; then `PUBLISH_LOCK_TIMEOUT_MS`; source → strict metadata → active; provenance fail-closed; sanitize once; delegate to existing publisher |
| Bounds | Approve title ≤ 512; publish title ≤ 255; sanitized body ≤ 64_000; no truncation |
| Activation lock order | All business-level activations: Product → ProductVersion → Product cache. Not moved into `ProductVersionRepository.activate` |

Dependency direction: `product_pipeline` → `product_optimization` → repositories. Never the reverse.

---

## 3. Boundaries kept

| Guard | Result |
|---|---|
| Migration | none; `git diff BASE...HEAD -- backend/alembic/versions` empty |
| Alembic head | `0033` |
| API route | none |
| Frontend | none |
| Celery pipeline task | none |
| Workflow / Docker / dependencies | unchanged |
| `backend/app/repositories/product_version.py` | does not exist |
| `origin/main` | `3ce66d488e94ad3805fe24903deda99691c234a6` unchanged |
| Stage 8 / 9 / 10 / 11 | not started |
| Deploy | no |

---

## 4. Local gates

### After implementation commits 1–6 (before first docs commit)

| Gate | Result |
|---|---|
| `ruff check .` | pass |
| `ruff format --check .` | pass (438 files) |
| `mypy app` | Success: no issues found in 230 source files |
| full `pytest` | **3279 passed**, **1 skipped**, **1 failed**, 263 warnings, 462.05 s |
| `python scripts/check_secrets.py` | PASS — 852 tracked files |
| `git diff --check BASE...HEAD` | clean |
| frontend | no diff; npm not run |

### After independent-review remediation (`776b123`, before this docs commit)

Targeted first: product optimization, Stage 7 pipeline, Stage 7 live publish locks, Shopify publish/idempotency, M2A concurrency — **267 passed**.

Then:

| Gate | Result |
|---|---|
| `ruff check .` | pass |
| `ruff format --check .` | pass (439 files) |
| `mypy app` | Success: no issues found in 230 source files |
| full `pytest` | **3285 passed**, **1 skipped**, **1 failed**, 307 warnings, 640.84 s |
| `python scripts/check_secrets.py` | PASS — 854 tracked files |
| `git diff --check BASE...HEAD` | clean |
| frontend | no diff; npm not run |

Skip: `tests/unit/core/test_log_retention.py` — symlink creation needs privilege on Windows (pre-existing).

### Known local-env failure (workstation only)

```
tests/integration/test_ebay_c0_security.py::
TestNoGeneratedOrSecretFiles::
test_no_env_file_was_added_to_the_repository
```

`.env` / `backend/.env` are gitignored (`gitignore:35:.env`) and **not tracked**.
Working tree remains clean of those files. The file was not opened, read,
deleted, or used to change application/tests. This is the same checkout-local
condition recorded in Stages 4–6. **Not classified as a Stage 7 product
defect.** CI has no `.env`.

No other pytest failure.

Implementation PR CI [35472626657](https://github.com/Adnan-Zulfiqar/ds-platform/actions/runs/35472626657)
on `d42c1a4` — **10/10 SUCCESS**. Post-merge develop CI
[35474423524](https://github.com/Adnan-Zulfiqar/ds-platform/actions/runs/35474423524)
on `ffa0d6e` — **10/10 SUCCESS**.

---

## 5. Self-review (complete diff `85e11772...HEAD`)

| Severity | Count |
|---|---|
| BLOCKER | 0 |
| HIGH | 0 |
| MEDIUM | 0 |
| LOW | plan residuals 1–14 still apply, plus the items below |

Attacked: optimize backward compatibility; pipeline marker / `True == 1`;
naive timestamps; activate bypass; preview mutating active state; sibling
approval race; same-candidate retry; publish active-state TOCTOU; stale
identity map; synthetic / missing provider; unsafe HTML; oversized output;
AI SEO leakage; merchant/supplier/image overwrite; foreign tenant/version;
duplicate Shopify create; publish rollback deactivation; circular imports;
unscoped query; accidental API/frontend/Celery/migration/workflow leakage.

### Additional LOW residuals from implementation

1. **Unit helper module renamed.** The plan suggested
   `tests/unit/test_product_pipeline.py` alongside
   `tests/integration/test_product_pipeline.py`. Pytest's default prepend
   import mode cannot collect two files with the same basename. Helpers live
   in `tests/unit/test_pipeline_preview_helpers.py`. Contract unchanged.
2. **Same-transaction `now()` freeze.** Integration tests that need
   `Product.updated_at` to move inside one shared client/`db_session`
   transaction write the token directly — the same pattern as M2A
   concurrency tests. Production HTTP requests are separate transactions;
   `TimestampMixin.onupdate=func.now()` bumps across them.
3. **`approve` / `publish(..., expected_updated_at: datetime | None)`.** The
   plan stubs type the token as `datetime`. Runtime `None` is a 422 on both
   paths, matching `update_draft`. ShopifySyncService still accepts an
   optional token for merchant/Celery callers.

---

## 5a. Independent review remediation

Previous reviewed head: `c75cc4d`. Independent review: BLOCKER 0, HIGH 0,
MEDIUM 2, LOW 1 contract-ordering cleanup. No PR opened.

| Finding | Fix |
|---|---|
| M1 — pipeline approve (Product then version) vs legacy activate/optimize (version then Product) can deadlock | `activate_version` and `optimize_product` now take Product `FOR UPDATE` before `versions.activate`. Optimize still generates outside the Product lock. Rule stays in the services, not in `ProductVersionRepository.activate`. Live lock tests with hang guard. |
| M2 — `publish(..., expected_updated_at=None)` skipped the M2A freshness guard | Pipeline `publish` rejects `None` with `ValidationError` **before** the Product lock. Shopify publisher is not called. `ShopifySyncService.publish_product` optional-token contract unchanged. |
| LOW — inactive legacy/malformed rows could return `candidate_not_approved` before proving pipeline metadata | Under the Product lock: AI source → strict parse → active → provenance → bounds → overlay. Inactive legacy/malformed → `not_a_pipeline_candidate`. Valid inactive pipeline → `candidate_not_approved`. |

Self-review of `85e11772...HEAD` after remediation: BLOCKER 0, HIGH 0, MEDIUM 0.

Independent implementation review of `d42c1a4`: **PASS** — BLOCKER 0,
HIGH 0, MEDIUM 0. Merged as `ffa0d6e3`.

---

## 6. Claude return-review checkpoint

CLAUDE RETURN REVIEW CHECKPOINT:
All commits from Stage 5 takeover onward require a fresh Claude
end-to-end review when Claude becomes available again.

Claude has NOT reviewed Stage 7.

Cursor is the temporary implementation + self-review agent.

All Stage 7 implementation commits require a later fresh Claude
end-to-end review.
