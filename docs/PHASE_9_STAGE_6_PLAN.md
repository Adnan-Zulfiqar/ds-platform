# Phase 9 Stage 6 — Image analysis: plan

The implementation contract for Stage 6, written before any Stage 6 code
exists. Every rule below is meant to be copied into a test and asserted
exactly. Where a number is chosen, its justification is stated next to it;
where a capability cannot be justified from this repository, available
libraries, and a committed fixture, it is not claimed.

Baseline this plan was written against: `develop`
`8d47763acd8a126e93050b5fa0c97bd6d2b33271` (Stage 5 fully closed: PR #13
implementation merge `e1558d5c`, closeout PR #14 merge `8d47763`;
post-merge CI 35400737556, 10/10).

**Status: PLANNING / AWAITING REVIEW. Not implemented.**

---

## 1. Objective

Give DropPilot a **product-scoped image-analysis capability** that:

1. fetches a `ProductImage` URL through an SSRF-safe downloader;
2. runs **deterministic, model-free** checks on the decoded pixels
   (blur, byte-identical duplicates within that product);
3. asks the configured `AIProvider.analyse_image` for a **caption and
   alt-text proposal**, behind the existing provider boundary;
4. persists the evidence on the image row without overwriting merchant
   data.

Stage 6 is a service Stage 7 can call. It is not the pipeline, not an
endpoint family, and not AI Studio.

---

## 2. Non-goals

Stage 6 does **not**:

- implement Stage 7's pipeline (analyse → generate → score → preview →
  approve → publish);
- add a public HTTP endpoint whose purpose is "run image analysis";
- add frontend / AI Studio UI (Stage 10);
- add a Celery task (Stage 9);
- wire `quality_scorer` or change Stage 4/5 generation and scoring;
- call `PromptService.test_render` for `image_analyzer` (that method
  always uses `complete()`, which is the wrong provider method);
- write `ProductImage.alt_text` automatically;
- write `Product.title` / `description` / SEO fields / Stage 5 quality
  keys / `ProductVersion` rows;
- copy image bytes into object storage (images remain URL-only);
- detect visually near-identical images (perceptual hashing);
- claim generic watermark detection;
- implement a real vision provider (`OpenAIProvider` etc. stay
  unconfigured);
- modify `main` or deploy.

---

## 3. Current-state audit

Verified against the baseline SHA, not assumed.

| Claim | Evidence |
|---|---|
| `ImageAnalysisRequest` / `ImageAnalysisResult` exist | `app/ai/provider.py` — frozen dataclasses; result is caption + alt_text + provider + model + `is_synthetic` |
| `AIProvider.analyse_image(...)` exists | Protocol method; docstring says captions/alt text only; blur/duplicate/watermark are "their own path that never calls a model" |
| `StubProvider.analyse_image` exists | Hashes `image_url` with SHA-256[:8]; returns `[STUB-AI]` caption/alt; `is_synthetic=True`; **does not fetch bytes**; **ignores `instructions`** |
| `image_analyzer` is seeded | Migration `0010`; template variables `{{image_url}}` and `{{product_title}}`; still unwired (Stage 5 integration test 8) |
| `PromptService.test_render` is text-only | Always `provider.complete(CompletionRequest(prompt=rendered))`. Using it for `image_analyzer` would send a vision task through the text method |
| `ProductImage` is URL-only | Model docstring: URL rather than a copied file. Columns: `url`, `position`, `alt_text`, `is_supplier`. No caption, no hash, no analysis JSONB |
| URL uniqueness | `UniqueConstraint("product_id", "url", name="uq_images_product_url")`. Duplicate **URLs on one product** are already a database error |
| Merchant vs supplier lifecycle | `sync_for_product` matches by URL, leaves `position` and `alt_text` alone, deletes supplier URLs no longer listed, **never deletes merchant-added URLs** (`is_supplier=False`) |
| `alt_text` is merchant-editable | `ProductImageUpdateRequest`; draft `PATCH .../images/{id}` writes it. Auto-generation must not overwrite it |
| Captions have no persistence location | No column, no JSONB key, no version field |
| No deterministic image service | Grep: no blur/watermark/image-hash service in `app/` |
| No safe image downloader | `_validate_image_url` only checks `http`/`https` + netloc + length 1024. It does **not** fetch, and does **not** block localhost/private IPs. eBay's SSRF guard is UUID-to-path construction for public keys — a different problem, not reusable as an image fetcher |
| `_is_internal_address` in `config.py` | IP-*literal* only; hostnames that resolve privately are explicitly out of its scope |
| Images are not versioned | `ProductVersion.content` holds title/description/SEO/quality. Images live on `product_images` and refresh independently |
| Image-processing libraries | `pyproject.toml`: **no Pillow, OpenCV, imagehash, numpy, scikit-image, OCR**. `httpx>=0.28.0` is present. Stdlib cannot decode JPEG/PNG safely |
| Latest migration | `0032` |

---

## 4. Architecture

```
ImageAnalysisService.analyse_product_images(product_id)
  → ProductImageRepository.list_for_product          tenant-scoped
  → for each live image (independent):
      ImageFetcher.fetch(url)                        SSRF-safe bytes
      image_checks.blur(pixels)                      pure + Pillow decode
      image_checks.duplicates(sha256, siblings)      within this product
      PromptService.execute_image_analysis(...)      render + analyse_image + PromptExecution
      ProductImage.analysis = <pinned JSONB>         proposal, not alt_text
```

Layering:

```
api (unchanged in Stage 6)
  → services/image_analysis.py     (new; no fastapi)
      → services/prompt.py         (new method only)
      → ai/factory.get_ai_provider
      → ai/image_fetch.py          (new)
      → ai/image_checks.py         (new; no provider import)
      → repositories/product.ProductImageRepository
```

`image_checks` imports nothing from `app.ai.provider` / `app.services.prompt`.
The fetcher imports nothing from the scorer or the prompt layer.
`ImageAnalysisService` is the only module that composes the three.

`IMAGE_ANALYSIS_VERSION = 1`.

---

## 5. Service boundaries

### 5.1 Public Stage 6 API

One public function on `ImageAnalysisService`:

```python
async def analyse_product_images(
    self,
    product_id: uuid.UUID,
    *,
    executed_by_user_id: uuid.UUID | None = None,
) -> ImageAnalysisReport
```

- Loads the product via the existing tenant-scoped product repository
  (`get_by_id_or_raise` → 404 across tenants, same as everywhere else).
- Analyses every **live** `ProductImage` for that product (`deleted_at IS NULL`
  via `_base_query()`).
- Returns a frozen `ImageAnalysisReport` (product_id, list of per-image
  results in `position` order).
- Persists `ProductImage.analysis` for every image it attempted.
- Forwards `executed_by_user_id` into `PromptExecution` (None is allowed;
  Stage 8 will pass the actor when an endpoint exists).

Stage 7 is expected to call exactly this method. Stage 6 does not call
`ProductOptimizationService` and does not add an `analyse` step inside
`optimize_product`.

### 5.2 PromptService extension

Add **one** new method. Do not overload `test_render`.

```python
async def execute_image_analysis(
    self,
    *,
    name: str,
    variables: dict[str, str],
    executed_by_user_id: uuid.UUID | None,
) -> tuple[str, ImageAnalysisResult | None, PromptExecution]
```

Behaviour:

1. `get_active(name)` — Stage 6 always passes `"image_analyzer"`.
2. Render with `PromptRenderer` (missing variables still raise, no row).
3. `provider = get_ai_provider(settings)`.
4. `result = await provider.analyse_image(ImageAnalysisRequest(image_url=variables["image_url"], instructions=rendered))`.
5. Write `PromptExecution` as today, except:
   - `response_text` is the exact JSON in §11;
   - `input_tokens` / `output_tokens` are `None` (`ImageAnalysisResult` has none);
   - `is_synthetic` / `provider` / `model` copied from the result, never from config.

On `AIError`: write `FAILED` the same way `test_render` does, return
`(rendered, None, execution)`.

`test_render` remains byte-identical in behaviour for the three Stage 4
prompts. A regression test calls `test_render("image_analyzer", ...,
execute=True)` and asserts it still goes through `complete()` — that path
is **not** Stage 6's path, and Stage 6 must not start using it.

### 5.3 What Stage 6 does not import

- `StubProvider` (factory only).
- `app.services.seo_score`.
- `app.services.optimization_quality` (Stage 5 stays unwired from this
  stage, and this stage stays unwired from scoring).
- `fastapi`.

---

## 6. Image-fetch security

There is **no** existing safe downloader to reuse. Stage 6 adds
`app/ai/image_fetch.py`.

`ProductService._validate_image_url` remains the store-time check. Fetching
is stricter: a merchant-added `http://127.0.0.1/...` URL that passed
store-time validation must still be refused at fetch time.

### 6.1 Policy (every hop, including redirects)

| Rule | Pin |
|---|---|
| Scheme | `https` only. `http`, `file`, `data`, `gopher`, `ftp`, empty → `ImageFetchDisallowedScheme` |
| Userinfo | Reject URLs with `user:pass@` |
| Port | Default 443, or 443 explicitly. Any other port → reject |
| Hostname | `urlparse(...).hostname`; IDNA-encode. `None` → reject. IP literals skip DNS and are judged as addresses |
| Hostname blocklist (case-insensitive) | `localhost`, `localhost.`, `metadata.google.internal`, `metadata.internal` |
| DNS | Resolve with `getaddrinfo`. Failure → `ImageFetchDnsFailure` |
| Resolved addresses | Reject if **any** A/AAAA is loopback, link-local, private (RFC1918 / ULA), unspecified, multicast, reserved, or IPv4-mapped IPv6 wrapping those. Includes `169.254.0.0/16` (cloud metadata / link-local) |
| Connect | Connect to a resolved **public** address; TLS SNI and HTTP `Host` are the original hostname. Certificate verification stays **on**; never `verify=False`. Do not ask httpx to resolve the name a second time |
| Redirects | `follow_redirects=False`. Honour `Location` up to **3** hops. Re-run the full policy on each absolute URL (relative `Location` resolved against the current URL). Redirect-to-http or redirect-to-private is rejected with the matching code |
| Timeouts | connect 3 s, read 10 s (`httpx.Timeout(10.0, connect=3.0)`). Timeout → `ImageFetchTimeout` |
| Size | Stream; abort if more than **5_242_880 bytes** (5 MiB) → `ImageFetchTooLarge` |
| Content-Type | Required. Media type (before `;`) must be one of `image/jpeg`, `image/jpg`, `image/png` (case-insensitive). GIF/WEBP/BMP/TIFF are **out of Stage 6** — `ImageFetchBadContentType` |
| Magic bytes | After download, sniff: JPEG `FF D8 FF` or PNG `\x89PNG\r\n\x1a\n`. Mismatch or unknown → `ImageFetchBadMagic` |
| Decode | Pillow; see §7. `Image.MAX_IMAGE_PIXELS = 16_777_216` (4096×4096). Exceeding → `ImageFetchPixelLimit`. Corrupt → `ImageFetchDecodeFailed` |

No test hits the public internet. Tests inject a resolver and an httpx
transport.

### 6.2 Why not `httpx.get(image_url)`

httpx will follow DNS to whatever the name resolves to, including
loopback and RFC1918, and a `Location` header can bounce a public URL
onto `http://169.254.169.254/`. That is SSRF. Stage 6 will not ship it.

---

## 7. Blur algorithm

**Metric:** variance of Laplacian on a normalized 256×256 grayscale image.

**Why this, and why not OpenCV:** `pyproject.toml` has no OpenCV/numpy.
Pillow can decode; a 3×3 convolution over 254×254 integers is small enough
to run in pure Python and be bit-identical across platforms.

### 7.1 Preprocess

1. Decode with Pillow (`Image.open(BytesIO(body)).load()`). Only JPEG and
   PNG are accepted by §6; any other Pillow format reaching here is
   `ImageFetchDecodeFailed`.
2. If mode is not `RGB`, convert to `RGB` (this drops alpha; palette and
   `L` go through RGB then back to `L` so the path is one path).
3. Convert to `L` (ITU-R 601 luma, Pillow's default).
4. Let `w, h = image.size`. If `w == 0` or `h == 0` → decode failed.
5. Fit to 256×256 **without upscaling** (upscaling a thumbnail invents
   blur the file did not contain):
   - if `min(w, h) > 256`, scale **down** with
     `Image.Resampling.BILINEAR` so `min(w, h) == 256`, then center-crop
     to 256×256 (`left = (w - 256) // 2`, `top = (h - 256) // 2`; leftover
     odd pixel comes off the right / bottom);
   - if `w == 256` and `h == 256`, use as-is;
   - if `w < 256` or `h < 256`, do **not** scale up: paste onto a 256×256
     canvas filled with luma `128`, centered (`left = (256 - w) // 2`,
     `top = (256 - h) // 2`).
6. Committed fixtures in §18 are **already** 256×256 PNG so the happy-path
   tests never exercise resample or letterbox.

### 7.2 Laplacian

Kernel (integer):

```
 0  1  0
 1 -4  1
 0  1  0
```

Valid region: `x in 1..254`, `y in 1..254` (0-based), `n = 254 * 254 = 64516`.

```
L(x, y) = p(x,y-1) + p(x,y+1) + p(x-1,y) + p(x+1,y) - 4 * p(x,y)
```

Pixels are 0–255 integers. `L` is an integer.

### 7.3 Score

Population variance, then integer round-half-up (same rule as Stage 5,
never Python `round()`):

```
sum_l  = Σ L
sum_sq = Σ L²
numerator   = n * sum_sq - sum_l²
denominator = n²
blurScore   = (numerator + denominator // 2) // denominator
```

`isBlurry = blurScore < 100`.

`threshold` is stored as `100` so a later rubric version can change it
without rewriting history.

### 7.4 Why 100

Uniform 8-bit gray has `blurScore = 0`. A 256×256 1-pixel checkerboard
(alternating 0/255) has `|L| = 1020` on every interior pixel, `sum_l = 0`,
`blurScore = 1020² = 1_040_400`. The gap between "no structure" and "any
hard edge" is three to six orders of magnitude. 100 sits above integer
noise on a flat field and far below any edged fixture. **It is not a
marketplace sharpness grade.** Recalibrating against real product photos
is a new `IMAGE_ANALYSIS_VERSION`, not a silent tweak.

### 7.5 Exact fixture outcomes

| Fixture | Construction | `blurScore` | `isBlurry` |
|---|---|---|---|
| `uniform_128.png` | 256×256, every pixel 128 | `0` | `true` |
| `checkerboard_1px.png` | 256×256, `p(x,y) = 255 if (x+y)%2 else 0` | `1040400` | `false` |

The comparison itself is tested at `blurScore ∈ {0, 99, 100, 1040400}`
without PNG: `99 → true`, `100 → false`.

Unsupported/malformed inputs never produce a blur score; they take the
fetch/decode failure shape in §14.

---

## 8. Duplicate algorithm

Three different facts. Stage 6 names them separately.

| Kind | Who owns it | Stage 6? |
|---|---|---|
| **A. Duplicate URL** | `uq_images_product_url` already rejects it | Not re-implemented. Not reported as a "finding" |
| **B. Byte-identical body, different URLs** | Stage 6 | **Yes.** SHA-256 of the **fetched bytes** (not decoded pixels) |
| **C. Visually near-identical** | Perceptual hash | **No.** No `imagehash`, no pHash, no Hamming-distance threshold. Deferred until a caller needs it and can pin fixtures |

### 8.1 Scope

**Within one product, one tenant, live images only.**

Not tenant-wide, not catalogue-wide. Cross-product matching would need an
unscoped or extra-scoped query and is a different feature.

### 8.2 Contract

For each successfully fetched image:

```
contentSha256 = sha256(raw_bytes).hexdigest()   # 64 lowercase hex
duplicateOfImageIds = sorted(
    sibling.id for sibling in live_images
    if sibling.id != self.id
    and sibling was also fetched this run
    and sibling.contentSha256 == self.contentSha256
)
```

IDs as UUID strings, sorted lexicographically for stability.

Two URLs serving the same JPEG bytes → each lists the other.
Re-encoded same picture (different bytes) → not a duplicate (kind C,
out of scope).

---

## 9. Watermark decision

**Option B — do not claim it.**

`PHASE_9_PLAN.md` §3 listed "watermark detection" as an example of
deterministic image processing. A general-purpose watermark detector is
not implementable with the libraries we have, is not definable as an
exact integer rule over committed fixtures, and must not be faked with a
model call (that would violate the Stage 6 split) or with a function that
always returns `false` (a silent false-negative presented as a check).

Stage 6 records an explicit not-applicable block, the same honesty
pattern as Stage 5 D4 when the merchant gave no keywords:

```json
"watermark": {
  "applicable": false,
  "reason": "genericWatermarkDetectionNotImplemented"
}
```

A later stage that pins a **narrow** heuristic (for example a specific
marketplace overlay with committed positive/negative PNGs) is a new
`IMAGE_ANALYSIS_VERSION` and its own plan. Until then, UI copy must not
say "no watermark found".

---

## 10. Caption / alt-text provider flow

```
variables = {"image_url": image.url, "product_title": product.title}
rendered, result, execution = await prompts.execute_image_analysis(
    name="image_analyzer",
    variables=variables,
    executed_by_user_id=...,
)
```

- `image_url` is the **stored** URL, not the resolved IP URL.
- `product_title` is `Product.title` (merchant listing title), never
  `supplier_title`. The seeded template already names it `product_title`.
- Supplier description is **not** a template variable and must not be
  added (prompt-injection surface; the seeded template does not ask for it).
- `StubProvider` ignores `instructions` and hashes the URL. That is
  existing Stage 1 behaviour; Stage 6 does not change it. Recorded
  `rendered_prompt` still contains the rendered template so an auditor
  can see what *would* have been sent to a real provider.
- `is_synthetic` is copied from the result. Stub output is never labelled
  as real visual understanding.

No live vision-provider verification exists. None is claimed.

---

## 11. Prompt / audit flow

`response_text` for a successful image analysis is exactly:

```json
{"caption":"<caption>","altText":"<alt_text>"}
```

- UTF-8, `ensure_ascii=False` is **not** used; `json.dumps(...,
  ensure_ascii=True, separators=(",", ":"), sort_keys=True)` so the byte
  string is stable (`altText` before `caption` alphabetically).
- Keys `altText`, `caption` — API camelCase at the JSON boundary, matching
  Stage 5's persisted keys.

`input_variables` stores the dict passed in (`image_url`, `product_title`).

Failed provider call: `status=failed`, `response_text=None`,
`error_code=type(exc).__name__`, same as `test_render`.

Stage 6 never writes a `PromptExecution` for `product_title_generator`,
`product_description_generator`, `seo_optimizer`, or `quality_scorer`.
Optimize remains three Stage 4 names.

---

## 12. Persistence

**Migration required: `0033`.**

`ProductImage` has no JSONB column. `ProductVersion.content` is the wrong
place: images are not versioned, they refresh, and Stage 5's quality keys
must not be overloaded. Ephemeral-only results would leave Stage 7 with
nothing to show and no fingerprint to invalidate.

Add nullable JSONB `analysis` on `product_images`, no back-fill, no
server default (legacy rows read as `null`). Comment on the model lists
the keys.

### 12.1 Success shape (`status: "succeeded"`)

```json
{
  "imageAnalysisVersion": 1,
  "sourceUrl": "https://cdn.example/a.jpg",
  "contentSha256": "<64 hex>",
  "byteLength": 12345,
  "decodedWidth": 800,
  "decodedHeight": 600,
  "decodedFormat": "jpeg",
  "status": "succeeded",
  "errorCode": null,
  "checks": {
    "blur": {
      "applicable": true,
      "blurScore": 1040400,
      "isBlurry": false,
      "threshold": 100,
      "workingSize": 256
    },
    "duplicates": {
      "applicable": true,
      "contentSha256": "<64 hex>",
      "duplicateOfImageIds": []
    },
    "watermark": {
      "applicable": false,
      "reason": "genericWatermarkDetectionNotImplemented"
    }
  },
  "captionProposal": "[STUB-AI] synthetic caption (image deadbeef).",
  "altTextProposal": "[STUB-AI] synthetic alt text (image deadbeef).",
  "isSynthetic": true,
  "provider": "stub",
  "model": "stub-1",
  "promptName": "image_analyzer",
  "promptVersion": 1
}
```

`decodedWidth` / `decodedHeight` / `decodedFormat` are the **source**
image before the 256 working crop (`decodedFormat` one of `jpeg`, `png`).

### 12.2 Fetch/decode failure shape

```json
{
  "imageAnalysisVersion": 1,
  "sourceUrl": "https://127.0.0.1/x.jpg",
  "contentSha256": null,
  "byteLength": null,
  "decodedWidth": null,
  "decodedHeight": null,
  "decodedFormat": null,
  "status": "fetchFailed",
  "errorCode": "ImageFetchPrivateAddress",
  "checks": null,
  "captionProposal": null,
  "altTextProposal": null,
  "isSynthetic": null,
  "provider": null,
  "model": null,
  "promptName": null,
  "promptVersion": null
}
```

`status` is one of `succeeded`, `fetchFailed`, `decodeFailed`,
`checksOnly`. `checksOnly` means bytes and checks succeeded but the
provider call failed; `checks` is populated; proposals are null;
`errorCode` is the provider error.

### 12.3 What is never written

- `ProductImage.alt_text`
- `ProductImage.url`, `position`, `is_supplier`
- `Product` merchant/supplier/SEO/AI-cache fields
- `ProductVersion` rows or `content` keys
- `Product.updated_at` (do not `touch` the product; M2A stays idle)

Optional wire: `ProductImageRead.analysis` as an optional nested model
(`null` on pre-Stage-6 rows). This is the Stage 5 pattern (optional fields
on an existing read schema), **not** a new endpoint. Frontend types are
not updated (M4, Stage 10).

---

## 13. Supplier-refresh semantics

| Event | Analysis |
|---|---|
| Supplier drops a URL | Row deleted by `sync_for_product`; analysis goes with it |
| Supplier keeps the same URL | Row id stable; `alt_text` / `position` untouched by sync; analysis remains until the next `analyse_product_images` |
| Same URL, different bytes | Next analysis computes a new `contentSha256`. If it differs from the stored one, the previous evidence is replaced wholesale. Callers must treat `contentSha256` as the validity key, not the URL |
| Merchant adds an image | Analysed like any other live row; never deleted by sync |
| Position change | Irrelevant to the fingerprint; checks are about bytes/pixels |
| Image soft-deleted | Not listed; not re-analysed; stale JSONB sits on the soft-deleted row and is not returned by list |

Stage 6 always recomputes (no "skip if hash matches" optimisation). A skip
cache would be a second, untested path.

There is no `fetchedAt` in the JSON: application clocks are not an
authority in this codebase; `ProductImage.updated_at` (existing mixin)
moves when `analysis` is written.

---

## 14. Failure semantics

**Per-image independence.** One image's fetch/decode/provider failure
does not skip the others. The product-level call fails only when the
product itself cannot be loaded (missing / cross-tenant → `NotFoundError`).

| Condition | `status` | `errorCode` | Provider called? | Checks |
|---|---|---|---|---|
| Disallowed scheme / private IP / blocked host / bad port | `fetchFailed` | as §6 | no | null |
| DNS failure | `fetchFailed` | `ImageFetchDnsFailure` | no | null |
| Connect/read timeout | `fetchFailed` | `ImageFetchTimeout` | no | null |
| HTTP 404 / 500 / anything not 200 | `fetchFailed` | `ImageFetchHttpError` | no | null |
| Oversize | `fetchFailed` | `ImageFetchTooLarge` | no | null |
| Bad Content-Type | `fetchFailed` | `ImageFetchBadContentType` | no | null |
| Bad magic | `fetchFailed` | `ImageFetchBadMagic` | no | null |
| Pixel limit / decompression bomb | `decodeFailed` | `ImageFetchPixelLimit` | no | null |
| Corrupt / unsupported codec after magic | `decodeFailed` | `ImageFetchDecodeFailed` | no | null |
| Deterministic checker exception | `decodeFailed` | exception type name | no | null |
| Checks OK, `AIProviderNotConfiguredError` | `checksOnly` | `AIProviderNotConfiguredError` | attempted | populated |
| Checks OK, other `AIError` | `checksOnly` | exception type name | attempted | populated |
| StubProvider | `succeeded` | null | yes | populated; `isSynthetic=true` |

HTTP statuses are not copied into `errorCode`; they may be included in
`error_message` (PromptExecution) / omitted from the JSONB to keep the
shape closed. JSONB `errorCode` is the closed token above only.

Empty image list: report with `images: []`, no provider calls, success.

---

## 15. Tenancy / auth

- `ProductImageRepository` stays the only image repository.
  `TenantScopedRepository` injects `tenant_id`. **No third unscoped
  repository.**
- `PromptExecutionRepository` stays tenant-scoped; image-analysis
  executions are tenant rows like Stage 2/4.
- Cross-tenant `analyse_product_images` → `NotFoundError` → 404 if a
  future endpoint wraps it. Stage 6 itself raises `NotFoundError`.
- No viewer-role change. No new router, so no new dependency. When
  Stage 8 adds an endpoint it must use `RequireAdmin` to match draft
  media mutations, unless that stage's plan says otherwise.

---

## 16. API / frontend boundary

Stage 6 adds **no** route under `/api/v1/`.

Existing draft image routes keep their current meaning (add / reorder /
patch alt / delete). They do not trigger analysis.

`ProductImageRead.analysis` is optional and ignored by today's frontend.
`frontend/types/api.ts` is not updated (M4).

---

## 17. Stage 7 boundary

Stage 7 owns orchestration. The intended call is:

```
report = await ImageAnalysisService(session).analyse_product_images(product.id)
```

before or beside generation, as that plan decides. Stage 6:

- does not call optimize;
- does not change `ai_status`;
- does not create versions;
- does not publish.

---

## 18. Exact test matrix

Fixtures live under `backend/tests/fixtures/images/` and are generated
by a documented snippet in the test module (Pillow, in-process, no
network). They are either committed PNGs or built in `setup_module` from
the pixel rules in §7.5 — both are deterministic. **No live URLs.**

### 18.1 Image fetch security (unit, fake resolver + httpx mock transport)

- `https://example.test/a.png` with public resolved IP → bytes returned
- `http://example.test/a.png` → `ImageFetchDisallowedScheme`
- `https://127.0.0.1/a.png` (IP literal) → `ImageFetchPrivateAddress`
- `https://[::1]/a.png` → `ImageFetchPrivateAddress`
- URL with userinfo → reject
- port 8443 → reject
- resolved `127.0.0.1` → `ImageFetchPrivateAddress`
- resolved `10.0.0.1` → private
- resolved `192.168.1.1` → private
- resolved `169.254.169.254` → private / metadata
- resolved `::1` → private
- resolved `fc00::1` → private
- 302 `Location: https://127.0.0.1/secret` → `ImageFetchPrivateAddress`
- 200 body 5_242_881 bytes → `ImageFetchTooLarge`
- `Content-Type: text/html` → `ImageFetchBadContentType`
- `Content-Type: image/png` but body `GIF89a...` → `ImageFetchBadMagic`
- truncated PNG → `ImageFetchDecodeFailed`
- PNG claiming 100000×100000 in IHDR → `ImageFetchPixelLimit`
- mocked timeout → `ImageFetchTimeout`
- DNS exception → `ImageFetchDnsFailure`

### 18.2 Deterministic checks

- `uniform_128.png` → `blurScore=0`, `isBlurry=true`
- `checkerboard_1px.png` → `blurScore=1040400`, `isBlurry=false`
- `isBlurry` at 99 / 100
- two images, identical PNG bytes, different URLs → each
  `duplicateOfImageIds` lists the other id
- two images, different bytes → empty duplicate lists
- watermark block exactly the N/A JSON
- malformed input never returns a blur score

### 18.3 Model path

- StubProvider caption/alt equal the Stage 1 formula
  (`[STUB-AI] synthetic caption (image {sha256(url)[:8]}).`)
- `is_synthetic is True`
- `PromptExecution.prompt_name == "image_analyzer"`
- `prompt_version` is the active row's version
- `provider == "stub"`, `model == "stub-1"`
- `response_text` equals the sort_keys JSON in §11
- `test_render("image_analyzer", execute=True)` still calls `complete`
  (monkeypatch: `analyse_image` raises if invoked)
- `execute_image_analysis` never calls `complete` (monkeypatch the other way)
- `AI_PROVIDER=openai` → `checksOnly` + `AIProviderNotConfiguredError`;
  no exception swallow that marks synthetic output as real

### 18.4 Tenancy / data

- analysing another tenant's product id → `NotFoundError`
- after analysis, `ProductImage.alt_text` is unchanged even when it was
  `None` and even when it was `"merchant wrote this"`
- `is_supplier`, `url`, `position` unchanged
- supplier-only vs merchant-added images both analysed; a subsequent
  `sync_for_product` still does not delete the merchant URL
- re-analysis with different bytes replaces `contentSha256`
- Stage 5 quality keys on versions unchanged
- `Product.updated_at` unchanged (compare before/after)
- expectedUpdatedAt / 409 path untouched (no product PATCH)

### 18.5 Regression

- `test_scoring_adds_no_prompt_execution` still: exactly the three Stage 4
  names; `image_analyzer` absent; `quality_scorer` absent
- Stage 4 `_build_variables` / `_keywords_for_prompt` pins (`a,b` / `x, x`)
- Stage 5 unit file still 88 passed
- no new router module
- no frontend file in the diff

---

## 19. Dependencies

| Package | Decision |
|---|---|
| **Pillow** | **Add** at implementation time, not in this planning commit. Needed to decode JPEG/PNG and convert to `L`. Stdlib cannot do this safely. Version strategy: declare `pillow>=11,<12` (Python 3.13 wheels; regenerate
   the lock in the same implementation commit). Exact patch is whatever the
   lock resolves. Set `Image.MAX_IMAGE_PIXELS` at import of the checks
   module. Security: decompression bombs handled by that limit + 5 MiB
   fetch cap |
| OpenCV | **Do not add.** Too heavy for a 3×3 Laplacian |
| imagehash / numpy | **Do not add.** Kind-C duplicates are out of scope |
| httpx | Already present; used by the fetcher |
| requests | Do not use (sync, and already a narrower Google-auth transport) |

---

## 20. Migration decision

**Yes — `0033`**, one nullable JSONB column `product_images.analysis`.

Why not avoid a migration: there is no existing JSONB on `ProductImage`,
and stuffing this into `ProductVersion.content` would attach image
evidence to a snapshot that does not own the images.

Why not a new table: a child history table has no second caller. JSONB on
the image row is one write, one read, one fingerprint. Version history of
analyses is a later stage if a caller needs it.

`downgrade()` drops the column. No back-fill.

---

## 21. Commit sequence

1. `build(ai): add Pillow for Stage 6 image decode` — dependency + lock
   only.
2. `feat(ai): add SSRF-safe product image fetcher` — fetch module + §18.1
   tests.
3. `feat(ai): add deterministic blur and byte-duplicate checks` — checks
   + fixtures + §18.2.
4. `feat(ai): execute image_analyzer through analyse_image` —
   `PromptService.execute_image_analysis` + unit tests. `test_render`
   untouched.
5. `feat(ai): persist image-analysis evidence on product images` —
   migration `0033`, service, optional `ProductImageRead.analysis`.
6. `test(ai): prove Stage 6 integration and protected behavior`.
7. `docs(ai): record Phase 9 Stage 6 completion`.

Do not combine 4 with `test_render` changes.

---

## 22. Quality gates

Same as Stage 5, plus the new tests:

- `ruff check`, `ruff format --check`, `mypy app`
- `scripts/check_secrets.py`
- targeted Stage 6 unit tests
- Stage 4 pins + Stage 5 scorer + optimization integration (must keep
  `image_analyzer` off the optimize path)
- full pytest on isolated `droppilot_test`
- `git diff --check`
- no frontend gate (no frontend files)
- CI on the eventual PR is the clean-checkout authority

---

## 23. Known limitations

1. **No live vision model.** Stub captions are a URL digest with a
   `[STUB-AI]` marker. They are not image understanding.
2. **Blur threshold is structural, not photographic.** `isBlurry` means
   "almost no Laplacian energy after 256×256 normalisation", not "a
   marketplace would reject this photo".
3. **No perceptual duplicates.**
4. **No watermark detector.** The N/A block exists so nobody reports
   "clean".
5. **HTTPS and JPEG/PNG only.** `http://` URLs and GIF/WEBP/BMP fail
   analysis. Store-time validation still allows `http` and whatever URL
   the merchant saved; Stage 6 will not fetch or decode them.
6. **Provider may fetch the URL independently** when a real vision API
   exists. Stage 6's SSRF guard protects *our* bytes path. A future real
   provider's server-side fetch is that provider's problem and out of
   Stage 6.
7. **`StubProvider` ignores prompt instructions.** Recorded
   `rendered_prompt` is still the audit of what a real provider would
   have been asked.
8. **Frontend types lag** (M4).
9. **Duplicates are this-run only.** A sibling that failed fetch is not
   compared; we do not read stale stored hashes.
10. **Not deployed.**

---

## 24. Acceptance criteria

Stage 6 is done when:

- [ ] Pillow is a declared dependency with a lockfile update.
- [ ] Fetcher tests in §18.1 all exist and pass without network.
- [ ] Blur scores match §7.5 exactly.
- [ ] Byte-duplicate ids match §8.2 exactly.
- [ ] Watermark JSON is the N/A object; no test asserts "watermark
      detected" or "watermark absent".
- [ ] `execute_image_analysis` calls `analyse_image`, not `complete`.
- [ ] `test_render` is unchanged for Stage 4 prompts.
- [ ] `ProductImage.alt_text` is never assigned by Stage 6.
- [ ] `analysis` JSON matches §12; migration `0033` upgrades and
      downgrades.
- [ ] Cross-tenant analyse raises `NotFoundError`.
- [ ] Optimize execution log still has exactly three Stage 4 names.
- [ ] No new API route, no frontend file, no Stage 7 pipeline wiring.
- [ ] Full local pytest recorded honestly; CI 10/10 on the PR.

---

## 25. Independent-review notes

A reviewer should look hardest at:

- `image_fetch.py` — DNS + connect-to-IP + redirect re-validation. If any
  path uses raw `httpx.get(url)` with default redirects, that is a
  blocker.
- Watermark: if implementation adds a detector without this plan being
  revised, that is a blocker.
- `product.py` service: grep `alt_text=` on the Stage 6 branch.
- `prompt.py`: `test_render` diff should be empty or comments-only.
- Migration: JSONB null, no back-fill, working `downgrade()`.

**CLAUDE RETURN REVIEW CHECKPOINT:** All commits from the Stage 5
takeover onward, including this plan and any Stage 6 implementation,
require a fresh Claude end-to-end review when Claude becomes available
again. Cursor review is not a substitute.
