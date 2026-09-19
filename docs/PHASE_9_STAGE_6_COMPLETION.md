# Phase 9 Stage 6 — completion report

**Image analysis: SSRF-safe fetch, deterministic checks, and synthetic
caption/alt proposals persisted on `ProductImage.analysis`.**

Status: **IMPLEMENTED LOCALLY / AWAITING INDEPENDENT REVIEW AND REMOTE CI.**

Not merged. Not pushed. Not deployed. `main` unchanged. Stage 7 not started.
Claude has not reviewed Stage 6. Cursor was temporarily both implementation
and review agent; this report is not a Claude review.

| | |
|---|---|
| Date | 2026-09-19 |
| Feature branch | `feat/phase-9-stage-6-image-analysis` |
| Base | `develop` @ `979e7cca1581bef96ebb53568ed72b766f262cc6` (Stage 6 plan PR #15 merged; post-merge CI 35436272869, 10/10) |
| Contract | [PHASE_9_STAGE_6_PLAN.md](PHASE_9_STAGE_6_PLAN.md) |
| Migration | `0033` — nullable JSONB `product_images.analysis`; no default; no back-fill |
| Dependency | `pillow==12.3.0` (exact pin) |
| AI involvement | `StubProvider.analyse_image` only. No live OpenAI/Anthropic/Gemini vision. `is_synthetic=true` on success |

---

## 1. Commit sequence

From `979e7cca`:

1. `07165f2` `build(ai): pin Pillow 12.3.0 for Stage 6 decode`
2. `554d62c` `feat(ai): add SSRF-safe product image fetcher`
3. `522f76c` `feat(ai): decode JPEG/PNG with an explicit pixel cap`
4. `87f5e96` `feat(ai): add deterministic blur and byte-duplicate checks`
5. `c9966ec` `feat(ai): execute image_analyzer through analyse_image`
6. `635ace9` `feat(ai): persist image-analysis evidence on product images`
7. `47952a5` `test(ai): prove Stage 6 integration and protected behavior`
8. `199bf46` `style(ai): apply ruff format to image_analysis service`
9. `e0de564` `fix(ai): map httpx request errors to ImageFetchHttpError`
10. `03d0605` `build(ai): lock Pillow 12.3.0 in runtime and dev requirements`
11. this docs commit

Commits 8–10 are quality-gate / self-review fixes. They do not change the
approved Stage 6 contract. The lock recompile is required by INFRA-L1-R1
once `pillow==12.3.0` is declared; it is not a CI-workflow change.

---

## 2. Architecture

```
ImageAnalysisService.analyse_product_images(product_id, *, executed_by_user_id=None)

PHASE A  ImageFetcher.fetch → decode_image → blur_score  (per live image)
PHASE B  group Phase-A successes by contentSha256; symmetric duplicate ids
PHASE C  PromptService.execute_image_analysis → assign ProductImage.analysis
         then flush; the service never commits
```

| Module | Role |
|---|---|
| `app/ai/image_fetch.py` | SSRF-safe HTTPS fetch |
| `app/ai/image_decode.py` | JPEG/PNG; pixel cap before `load()` |
| `app/ai/image_checks.py` | 256×256 luma canvas, Laplacian variance, SHA-256 duplicates, watermark N/A |
| `app/services/prompt.py` | `execute_image_analysis` → `analyse_image`, never `complete` |
| `app/services/image_analysis.py` | product-scoped composer |
| `alembic/versions/0033_product_image_analysis.py` | nullable JSONB column |

`IMAGE_ANALYSIS_VERSION = 1`.

---

## 3. Security model

- HTTPS only; no userinfo; port 443 only.
- IDNA-normalised logical hostname; localhost / metadata names blocked.
- Every resolved address must have `is_global is True` after unwrapping
  IPv4-mapped IPv6. Mixed global + non-global DNS rejects the host.
  CGNAT `100.64.0.0/10` is rejected.
- Connection URL is the validated IP literal. `Host` and
  `extensions["sni_hostname"]` are the logical hostname.
- Fresh `httpx.AsyncClient` per hop: `trust_env=False`, `verify=True`,
  `follow_redirects=False`.
- Redirects (301/302/303/307/308, max 3) re-parse against the logical URL
  and re-resolve. Redirect bodies are not buffered.
- Body streamed via `client.stream` / `aiter_bytes`; cap `5_242_880` bytes.
- Pixel cap `16_777_216` before `image.load()`.
- `httpx.TimeoutException` → `ImageFetchTimeout`; other `httpx.RequestError`
  → `ImageFetchHttpError` (per-image, not product abort).

---

## 4. Deterministic evidence

Blur (integer population variance of the 3×3 Laplacian, round-half-up):

| Fixture | `blurScore` | `isBlurry` (`< 100`) |
|---|---|---|
| uniform_128 | 0 | true |
| checkerboard_1px | 1040400 | false |
| checkerboard_16px | 17174 | false |
| checkerboard_16px_box15 (r=7) | 37 | true |

Duplicates: `sha256(raw_bytes)` within the same tenant, same product, same
invocation, Phase-A successes only. IDs sorted lexicographically as strings.
Symmetric.

Watermark JSON is exactly:

```json
{"applicable": false, "reason": "genericWatermarkDetectionNotImplemented"}
```

Generic watermark detection is not implemented. The column never stores
`watermark: false`.

---

## 5. What Stage 6 does not do

- Stage 7 orchestration, Stage 8 public API, Stage 9 Celery, Stage 10 frontend
- Real vision providers
- Perceptual near-duplicate hashing
- Automatic `ProductImage.alt_text` writes
- `ProductImageRead.analysis` / `ProductDetailRead.analysis`
- New HTTP route
- ProductVersion image data
- `quality_scorer` execution or Stage 4 optimize prompt-count changes
- M2A optimistic-concurrency changes
- Overwrite of merchant/supplier/SEO/`ai_status` fields
- Push, PR, merge, or deploy

---

## 6. Required CI pin

`.github/workflows/ci.yml` Playwright Alembic-head expectation only:
`0032` → `0033` (comparison and error text). No other workflow behaviour.

---

## 7. Local validation

Isolated Postgres `droppilot-stage6-testpg` on `127.0.0.1:5500` (not
`droppilot-postgres-1`). Alembic `upgrade head` / `downgrade 0032` /
`upgrade 0033` on that database: column `product_images.analysis` is
nullable JSONB with no default.

Recorded at quality-gate time (this report is committed with the counts
from the same session):

- `ruff check .` PASS
- `ruff format --check .` PASS (after `199bf46`)
- `mypy app` PASS (229 source files)
- `scripts/check_secrets.py` PASS (844 tracked files)
- Alembic heads: one head, `0033`
- Targeted Stage 6 unit + integration tests: green
- Frontend `typecheck` / `lint` / `build`: green (no frontend source diff)
- Full backend pytest against isolated Stage 6 Postgres
  (`127.0.0.1:5500` / `droppilot_stage6`): **3199 passed, 1 skipped,
  3 failed** on the pre-lock head. Two of those three were
  INFRA-L1-R1 stale-lock assertions; they pass after the lock recompile
  (`6 passed`). The remaining failure is
  `test_no_env_file_was_added_to_the_repository`, which fails because a
  gitignored local `.env` exists on this workstation. The same class of
  failure was recorded in Stage 5. The `.env` was not deleted, not
  opened, and not committed. Product code was not changed to hide it.

---

## 8. Claude return checkpoint

All commits from Stage 5 takeover onward require a fresh Claude
end-to-end review when Claude becomes available again. Cursor review of
this Stage 6 implementation is not a substitute.
