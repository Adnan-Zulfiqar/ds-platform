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

A first independent review of `7ff1ae5` returned **0 BLOCKER / 2 HIGH /
6 MEDIUM**. This revision closes those findings. **It is still PLANNING /
AWAITING REVIEW** until a further independent review. Not implemented.

---

## 1. Objective

Give DropPilot a **product-scoped image-analysis capability** that:

1. fetches a `ProductImage` URL through an SSRF-safe downloader;
2. runs **deterministic, model-free** checks on the decoded pixels
   (blur, byte-identical duplicates within that product);
3. asks the configured `AIProvider.analyse_image` for a **caption and
   alt-text proposal**, behind the existing provider boundary;
4. persists the evidence on the image row without overwriting merchant
   data and without changing the public API schema.

Stage 6 is a service Stage 7 can call. It is not the pipeline, not an
endpoint family, and not AI Studio.

---

## 2. Non-goals

Stage 6 does **not**:

- implement Stage 7's pipeline (analyse → generate → score → preview →
  approve → publish);
- add a public HTTP endpoint whose purpose is "run image analysis";
- add `analysis` (or any new field) to `ProductImageRead` / any response
  schema — that is Stage 8;
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
- change GitHub Actions merely to pin a Python lockfile;
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
| `_is_internal_address` in `config.py` | IP-*literal* only; hostnames that resolve privately are explicitly out of its scope. **Not reused.** Stage 6 uses `ip_address(...).is_global` after DNS (see §6) |
| Images are not versioned | `ProductVersion.content` holds title/description/SEO/quality. Images live on `product_images` and refresh independently |
| Image-processing libraries | `pyproject.toml`: **no Pillow, OpenCV, imagehash, numpy, scikit-image, OCR**. `httpx>=0.28.0` is present. Stdlib cannot decode JPEG/PNG safely |
| Backend CI install | `.github/workflows/ci.yml` runs `pip install -e ".[dev]"` (and e2e `pip install -e .`). **No Python lockfile is installed in CI.** An open version range is therefore not reproducible |
| Latest migration | `0032` |

---

## 4. Architecture

Three phases. Duplicate grouping cannot run inside the per-image fetch
loop: an earlier image would not yet know a later sibling's hash.

```
ImageAnalysisService.analyse_product_images(product_id, *, executed_by_user_id=None)

PHASE A — acquisition (per live image, independent expected failures)
  ImageFetcher.fetch(url)                 SSRF-safe bytes; no second DNS
  decode_image(bytes)                     JPEG/PNG; pixel cap BEFORE load
  blur_score(working 256×256 luma)
  retain interim {image, raw_bytes, sha256, blur, decode meta} OR failure

PHASE B — duplicate grouping (only Phase-A successes, this invocation)
  group by contentSha256
  duplicateOfImageIds = other ids in the same group, sorted lexicographically

PHASE C — provider + one persistence flush
  successes: PromptService.execute_image_analysis(...) then JSONB
  fetch/decode failures: JSONB failure shape; no provider call
  assign every ProductImage.analysis in memory
  one session flush
```

Layering:

```
api (unchanged in Stage 6 — no schema fields added)
  → services/image_analysis.py     (new; no fastapi)
      → services/prompt.py         (new method only)
      → ai/factory.get_ai_provider
      → ai/image_fetch.py          (new)
      → ai/image_decode.py         (new; Pillow; pixel cap)
      → ai/image_checks.py         (new; no provider import)
      → repositories/product.ProductImageRepository
```

`image_checks` imports nothing from `app.ai.provider` / `app.services.prompt`.
The fetcher imports nothing from decode/checks/prompt.
`ImageAnalysisService` is the only module that composes them.

`IMAGE_ANALYSIS_VERSION = 1`.

---

## 5. Service boundaries

### 5.1 Public Stage 6 API

```python
async def analyse_product_images(
    self,
    product_id: uuid.UUID,
    *,
    executed_by_user_id: uuid.UUID | None = None,
) -> ImageAnalysisReport
```

- Loads the product via the existing tenant-scoped product repository
  (`get_by_id_or_raise` → missing / cross-tenant `NotFoundError`).
- Analyses every **live** `ProductImage` (`deleted_at IS NULL` via
  `_base_query()`), in `position` order for the returned report.
- Runs Phase A → B → C as in §4.
- Persists `ProductImage.analysis` for every live image (success or
  expected failure) in **one flush** at the end of Phase C.
- Forwards `executed_by_user_id` into `PromptExecution` (None is allowed).
- Returns a frozen `ImageAnalysisReport`. Callers in-process (tests,
  later Stage 7) read the report and/or the ORM column. HTTP clients do
  not see `analysis` in Stage 6.

Stage 6 does not call `ProductOptimizationService` and does not add an
`analyse` step inside `optimize_product`.

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
   - `input_tokens` / `output_tokens` are `None`;
   - `is_synthetic` / `provider` / `model` copied from the result.

On `AIError`: write `FAILED` the same way `test_render` does, return
`(rendered, None, execution)`. That is an **expected** domain failure.

On anything that is not `AIError` and not a missing-variable render error:
**propagate**. Do not write a `PromptExecution` for `TypeError`,
`AssertionError`, `sqlalchemy.exc.SQLAlchemyError`, etc.

`test_render` remains byte-identical for the three Stage 4 prompts. A
regression test calls `test_render("image_analyzer", ..., execute=True)`
and asserts it still goes through `complete()`.

### 5.3 What Stage 6 does not import

- `StubProvider` (factory only).
- `app.services.seo_score`.
- `app.services.optimization_quality`.
- `fastapi`.
- `app.schemas.product` (the service does not mutate response models).

---

## 6. Image-fetch security

There is **no** existing safe downloader to reuse. Stage 6 adds
`app/ai/image_fetch.py`.

`ProductService._validate_image_url` remains the store-time check.
Fetching is stricter.

### 6.1 Closed allow-policy for addresses

Reject-lists of "private / loopback / link-local / reserved / multicast"
are **not** the contract. Python 3.13 treats CGNAT `100.64.0.0/10` as
`is_private == False` and `is_global == False`. A private-only reject
list would allow it.

After parsing an IP literal **or** resolving DNS:

1. Normalise every address with `ipaddress.ip_address(...)`.
2. Reject IPv6 zone / scoped identifiers (`addr.scope_id` not empty, or
   `%` in the hostname) → `ImageFetchZoneId`.
3. If the address is IPv4-mapped IPv6 (`::ffff:x.x.x.x`), replace it
   with `addr.ipv4_mapped` and judge **that** IPv4 address.
4. An address is connectable **only** when `address.is_global is True`.
5. If **any** resolved A/AAAA is not connectable, reject the **host**
   → `ImageFetchNotGlobalAddress` (DNS rebinding / mixed records).
6. Only if every resolved address is global: connect to the **first**
   address in resolver order. No fallback to later records.

Verified on CPython 3.13 (this machine, 2026-09-18):

| Address | `is_global` | Stage 6 |
|---|---|---|
| `100.64.0.1` | False (and `is_private` False) | reject |
| `127.0.0.1` | False | reject |
| `10.0.0.1` | False | reject |
| `169.254.169.254` | False | reject |
| `192.168.1.1` | False | reject |
| `192.0.2.1` (TEST-NET-1) | False | reject |
| `::1` | False | reject |
| `fc00::1` | False | reject |
| `fe80::1` | False | reject |
| `::ffff:10.0.0.1` | False (after unwrap: `10.0.0.1`) | reject |
| `8.8.8.8` | True | allow (policy fixture only; fake transport; no internet) |
| `::ffff:8.8.8.8` | True (after unwrap) | allow as IPv4 `8.8.8.8` |

### 6.2 Per-hop procedure (logical URL ≠ connection URL)

`httpx` must never be asked to resolve the original hostname.

For each hop, with `logical_url` starting as the stored `https://...`
URL:

1. Parse `logical_url`. Apply scheme / userinfo / port / hostname
   blocklist / zone rules in §6.3.
2. If hostname is an IP literal: skip DNS; wrap it as the sole candidate
   list. Else resolve **once** through the **injected** resolver
   (`getaddrinfo`-shaped). Do not call a second resolver. Failure →
   `ImageFetchDnsFailure`.
3. Validate **all** candidates with §6.1. Any failure rejects the hop.
4. `chosen_ip` = first validated address (resolver order).
5. Connection URL:
   - IPv4: `https://<chosen_ip><path><?query>`
   - IPv6: `https://[<chosen_ip>]<path><?query>`
   - path and query copied from `logical_url`; fragment dropped
6. Issue **one** GET with a client constructed as:

```python
client = httpx.AsyncClient(
    timeout=httpx.Timeout(10.0, connect=3.0),
    follow_redirects=False,
    trust_env=False,
    verify=True,  # never False
)
response = await client.request(
    "GET",
    connection_url,
    headers={"Host": logical_hostname},  # original hostname, not the IP
    extensions={"sni_hostname": logical_hostname},
)
```

7. Certificate verification stays enabled. SNI is the logical hostname
   so the cert still matches the name the merchant stored.
8. The original hostname is **never** the network-connection target of
   the httpx request (`request.url.host` in tests is the IP literal).

Connect / read timeout → `ImageFetchTimeout`. No retry on another A
record.

### 6.3 Other hop rules

| Rule | Pin |
|---|---|
| Scheme | `https` only. `http`, `file`, `data`, `gopher`, `ftp`, empty → `ImageFetchDisallowedScheme` |
| Userinfo | Reject URLs with `user:pass@` |
| Port | Default 443, or 443 explicitly. Any other port → reject |
| Hostname | `urlparse(...).hostname`; IDNA-encode. `None` → reject |
| Hostname blocklist (case-insensitive) | `localhost`, `localhost.`, `metadata.google.internal`, `metadata.internal` |
| Redirects | `follow_redirects=False`. Honour `Location` up to **3** hops. Resolve a relative `Location` against the **logical** URL, never the IP connection URL. The next hop is a new logical URL that re-runs §6.2 from step 1 (new DNS if the host changed) |
| Fourth redirect | `ImageFetchTooManyRedirects` |
| Timeouts | connect 3 s, read 10 s |
| Size | Stream; abort if more than **5_242_880 bytes** (5 MiB) → `ImageFetchTooLarge` |
| HTTP status | Only `200` is success. Anything else → `ImageFetchHttpError` |
| Content-Type | Required. Media type (before `;`) must be `image/jpeg`, `image/jpg`, or `image/png` (case-insensitive). GIF/WEBP/BMP/TIFF → `ImageFetchBadContentType` |
| Magic bytes | JPEG `FF D8 FF` or PNG `\x89PNG\r\n\x1a\n`. Mismatch → `ImageFetchBadMagic` |

No test hits the public internet. Tests inject the resolver **and** an
httpx mock transport that records `request.url`, `Host`, `extensions`,
and the client’s `trust_env` / `follow_redirects`.

### 6.4 Why not `httpx.get(image_url)`

httpx would resolve the name itself (second DNS, including to loopback /
CGNAT), honour `trust_env` proxy settings, and follow `Location` onto
`http://169.254.169.254/`. That is SSRF. Stage 6 will not ship it.

---

## 7. Decode and blur

### 7.0 Decode (before any Laplacian)

Do **not** use `Image.MAX_IMAGE_PIXELS = 16_777_216` as the sole control.
Pillow's default cap is larger than 4096×4096, and a warning can pass
silently.

```
import warnings
from io import BytesIO
from PIL import Image

def decode_image(body: bytes) -> Image.Image:
    # Convert DecompressionBombWarning into an error for this call only.
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            image = Image.open(BytesIO(body))
        except Image.DecompressionBombError as exc:
            raise ImageFetchPixelLimit from exc
        except Image.DecompressionBombWarning as exc:
            raise ImageFetchPixelLimit from exc
    width, height = image.size
    if width <= 0 or height <= 0 or width * height > 16_777_216:
        raise ImageFetchPixelLimit
    try:
        image.load()
    except Image.DecompressionBombError as exc:
        raise ImageFetchPixelLimit from exc
    except Exception as exc:
        raise ImageFetchDecodeFailed from exc
    if image.format not in {"JPEG", "PNG"}:
        raise ImageFetchDecodeFailed
    return image
```

`load()` happens **only after** the `width * height` check. Tests for
4096×4096 / 4097×4096 / forged huge IHDR **must not allocate** a giant
decoded buffer: they stub/fake `Image.size` (or feed a PNG IHDR-only
header that Pillow rejects at open). `4096 * 4096 == 16_777_216` is
permitted by the **policy**; the test asserts the comparison, not a
16-million-pixel array.

Pillow format names are `"JPEG"` and `"PNG"`; persisted `decodedFormat`
is `jpeg` / `png`.

### 7.1 Working-canvas normalisation

Metric input is always a 256×256 luma image. **Never upscale.** Every
positive `(w, h)` has exactly one path. No behaviour may depend on
Pillow clipping a paste that does not fit.

Integer scale uses truncating division (Python `//`).

**Step 1 — RGB then L.** Convert to `RGB` then `L` (ITU-R 601 luma,
Pillow default). `w, h = image.size` after convert (same as source for
JPEG/PNG).

**Step 2 — downscale to fit inside 256×256 if needed, preserving aspect
with integer arithmetic:**

```
if w > 256 or h > 256:
    scale_den = max(w, h)
    new_w = max(1, (w * 256) // scale_den)
    new_h = max(1, (h * 256) // scale_den)
    image = image.resize((new_w, new_h), Image.Resampling.BILINEAR)
else:
    new_w, new_h = w, h
```

**Step 3 — pad, never upscale, onto a 256×256 canvas of luma 128:**

```
canvas = Image.new("L", (256, 256), 128)
left = (256 - new_w) // 2
top  = (256 - new_h) // 2
canvas.paste(image, (left, top))
# leftover odd pixel is on the right / bottom by integer division
```

If `new_w == 256` and `new_h == 256`, paste at `(0, 0)` — identity.

Pinned geometry (source → after step 2 → paste origin):

| Source `w×h` | After step 2 | `left, top` |
|---|---|---|
| 256×256 | 256×256 (skip step 2) | 0, 0 |
| 512×512 | 256×256 | 0, 0 |
| 500×200 | 256×102 | 0, 77 |
| 200×500 | 102×256 | 77, 0 |
| 128×128 | 128×128 (skip step 2) | 64, 64 |
| 128×512 | 64×256 | 96, 0 |
| 512×128 | 256×64 | 0, 96 |
| 301×500 | 154×256 | 51, 0 |
| 500×301 | 256×154 | 0, 51 |

Tests in §18.2 assert these exact `(new_w, new_h, left, top)` values and
that pad pixels are 128. They do not need blur scores.

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

Pixels 0–255. `L` is an integer.

### 7.3 Score

Population variance, integer round-half-up (same rule as Stage 5, never
Python `round()`):

```
sum_l  = Σ L
sum_sq = Σ L²
numerator   = n * sum_sq - sum_l²
denominator = n²
blurScore   = (numerator + denominator // 2) // denominator
```

`isBlurry = blurScore < 100`.

`threshold` is stored as `100`.

### 7.4 Threshold evidence

Computed locally with the formulas above (CPython, 2026-09-18), not
guessed:

| Fixture | Construction | `blurScore` | `isBlurry` |
|---|---|---|---|
| `uniform_128.png` | 256×256, every pixel 128 | `0` | `true` |
| `checkerboard_1px.png` | `p(x,y) = 255 if (x+y)%2 else 0` | `1040400` | `false` |
| `checkerboard_16px.png` | 16×16-cell checkerboard: `255 if ((x//16)+(y//16))%2 else 0` | `17174` | `false` |
| `checkerboard_16px_box15.png` | the 16px checkerboard, then the box filter in §7.5 | `37` | `true` |

`37 < 100 < 17174`. Threshold 100 **does** separate the paired
structured fixture from its documented blur. It is still not a
marketplace grade; it **is** a demonstrated classifier on this pair.

The comparison itself is also tested at `blurScore ∈ {0, 37, 99, 100, 17174, 1040400}` without PNG: `99 → true`, `100 → false`.

### 7.5 Box-filter construction (paired fixture)

Radius `r = 7` → window 15×15, `area = 225`. Replicate (clamp) edges.
Integer mean:

```
for y in 0..255:
  for x in 0..255:
    s = 0
    for dy in -7..7:
      for dx in -7..7:
        yy = min(max(y+dy, 0), 255)
        xx = min(max(x+dx, 0), 255)
        s += src[yy][xx]
    dst[y][x] = s // 225
```

Tests may build both arrays in process (no network). If a PNG is
committed, it must be generated from this loop, not from an unspecified
Gaussian.

Unsupported/malformed inputs never produce a blur score; they take the
fetch/decode failure shape in §14.

---

## 8. Duplicate algorithm

Three different facts. Stage 6 names them separately.

| Kind | Who owns it | Stage 6? |
|---|---|---|
| **A. Duplicate URL** | `uq_images_product_url` already rejects it | Not re-implemented |
| **B. Byte-identical body, different URLs** | Stage 6 | **Yes.** SHA-256 of the **fetched bytes** |
| **C. Visually near-identical** | Perceptual hash | **No** |

### 8.1 Scope

**Within one product, one tenant, live images that succeeded Phase A in
this invocation.** Not tenant-wide, not catalogue-wide, not stale stored
hashes.

### 8.2 Two-phase contract

Phase A stores `contentSha256 = sha256(raw_bytes).hexdigest()` (64
lowercase hex) on each success.

Phase B, after **all** images have finished Phase A:

```
groups = {sha: [interim, ...] for successes sharing sha}
for interim in successes:
    others = [peer.image.id for peer in groups[interim.contentSha256]
              if peer.image.id != interim.image.id]
    interim.duplicateOfImageIds = sorted(str(uuid) for uuid in others)
```

Two successes with the same bytes → **symmetric** lists (each lists the
other). A fetch failure is not in any group. Three identical bodies →
each lists the other two, sorted.

---

## 9. Watermark decision

**Option B — do not claim it.**

`PHASE_9_PLAN.md` §3 listed "watermark detection" as an example. A
general-purpose detector is not implementable here and must not be faked.

```json
"watermark": {
  "applicable": false,
  "reason": "genericWatermarkDetectionNotImplemented"
}
```

UI copy must not say "no watermark found". A later
`IMAGE_ANALYSIS_VERSION` may add a narrow heuristic with fixtures.

---

## 10. Caption / alt-text provider flow

Phase C, successes only:

```
variables = {"image_url": image.url, "product_title": product.title}
rendered, result, execution = await prompts.execute_image_analysis(
    name="image_analyzer",
    variables=variables,
    executed_by_user_id=...,
)
```

- `image_url` is the **stored** URL, not the connection IP URL.
- `product_title` is `Product.title`, never `supplier_title`.
- Supplier description is **not** a template variable.
- `StubProvider` ignores `instructions` and hashes the URL (Stage 1).
  Recorded `rendered_prompt` is still the audit of what a real provider
  would have been asked.
- `is_synthetic` copied from the result. Stub output is never labelled
  as real visual understanding.

No live vision-provider verification exists. None is claimed.

---

## 11. Prompt / audit flow

`response_text` for a successful image analysis is exactly:

```json
{"altText":"<alt_text>","caption":"<caption>"}
```

`json.dumps(..., ensure_ascii=True, separators=(",", ":"), sort_keys=True)`.

Failed `AIError`: `status=failed`, `response_text=None`,
`error_code=type(exc).__name__`.

Stage 6 never writes a `PromptExecution` for `product_title_generator`,
`product_description_generator`, `seo_optimizer`, or `quality_scorer`.

---

## 12. Persistence

**Migration required: `0033`.**

Add nullable JSONB `analysis` on `product_images`, no back-fill, no
server default. Comment on the **model** lists the keys.

**Do not** add `analysis` to `ProductImageRead`. Stage 8 owns response
schema. Stage 6 tests read `ProductImage.analysis` from the ORM.

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
      "blurScore": 17174,
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
image before the working canvas.

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
  "errorCode": "ImageFetchNotGlobalAddress",
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
`checksOnly`. `checksOnly`: Phase A succeeded, provider raised `AIError`;
`checks` populated (including final Phase-B duplicates); proposals null.

### 12.3 What is never written

- `ProductImage.alt_text`
- `ProductImage.url`, `position`, `is_supplier`
- `Product` merchant/supplier/SEO/AI-cache fields
- `ProductVersion` rows or `content` keys
- `Product.updated_at`
- `ProductImageRead` / any API schema

---

## 13. Supplier-refresh semantics

| Event | Analysis |
|---|---|
| Supplier drops a URL | Row deleted by `sync_for_product`; analysis goes with it |
| Supplier keeps the same URL | Row id stable; `alt_text` / `position` untouched by sync; analysis remains until the next `analyse_product_images` |
| Same URL, different bytes | Next analysis replaces JSONB; `contentSha256` is the validity key |
| Merchant adds an image | Analysed like any other live row; never deleted by sync |
| Position change | Irrelevant to the fingerprint |
| Image soft-deleted | Not listed; not re-analysed |

Always recompute. No skip-if-hash-matches path.

No `fetchedAt` in the JSON. `ProductImage.updated_at` moves when
`analysis` is written.

---

## 14. Failure semantics

### 14.1 Expected vs unexpected

**Closed expected types** (per-image evidence, siblings continue):

- `ImageFetchDisallowedScheme`
- `ImageFetchInvalidUrl`
- `ImageFetchNotGlobalAddress`
- `ImageFetchHostnameBlocked`
- `ImageFetchZoneId`
- `ImageFetchDnsFailure`
- `ImageFetchTimeout`
- `ImageFetchHttpError`
- `ImageFetchTooLarge`
- `ImageFetchBadContentType`
- `ImageFetchBadMagic`
- `ImageFetchTooManyRedirects`
- `ImageFetchPixelLimit`
- `ImageFetchDecodeFailed`
- `AIError` (including `AIProviderNotConfiguredError`)

These are raised by the fetcher/decoder or by `get_ai_provider` /
`analyse_image`. The service catches **only** these types (or a shared
`ImageFetchError` base plus `AIError`).

**Unexpected** (`IndexError`, `AssertionError`, `TypeError`,
`RuntimeError`, `MemoryError`, `sqlalchemy.exc.SQLAlchemyError`,
anything else): **propagate**. No per-image JSONB for that failure, no
success report, no fake `decodeFailed`. The current unit of work rolls
back uncommitted Stage 6 writes (Phase C has not flushed yet, or the
request session rolls back). No savepoints.

The product-level call therefore fails when:

- the product cannot be loaded (`NotFoundError`), **or**
- an unexpected exception occurs.

It does **not** fail the whole product merely because one image 404s.

### 14.2 Expected mapping

| Condition | `status` | `errorCode` | Provider? | Checks |
|---|---|---|---|---|
| Disallowed scheme / userinfo / port | `fetchFailed` | matching token | no | null |
| Hostname blocklist | `fetchFailed` | `ImageFetchHostnameBlocked` | no | null |
| Zone id | `fetchFailed` | `ImageFetchZoneId` | no | null |
| Any resolved address not `is_global` (incl. CGNAT, loopback, RFC1918, link-local, TEST-NET, mixed DNS) | `fetchFailed` | `ImageFetchNotGlobalAddress` | no | null |
| DNS failure | `fetchFailed` | `ImageFetchDnsFailure` | no | null |
| Timeout | `fetchFailed` | `ImageFetchTimeout` | no | null |
| HTTP not 200 | `fetchFailed` | `ImageFetchHttpError` | no | null |
| Oversize | `fetchFailed` | `ImageFetchTooLarge` | no | null |
| Bad Content-Type | `fetchFailed` | `ImageFetchBadContentType` | no | null |
| Bad magic | `fetchFailed` | `ImageFetchBadMagic` | no | null |
| Too many redirects | `fetchFailed` | `ImageFetchTooManyRedirects` | no | null |
| Pixel limit / decompression bomb | `decodeFailed` | `ImageFetchPixelLimit` | no | null |
| Corrupt JPEG/PNG after size check | `decodeFailed` | `ImageFetchDecodeFailed` | no | null |
| Phase A OK, `AIError` | `checksOnly` | exception type name | attempted | populated, duplicates final |
| StubProvider | `succeeded` | null | yes | populated; `isSynthetic=true` |

Empty image list: report `images: []`, no provider calls, success, no
flush of analysis.

JSONB `errorCode` is the closed token list above only.

---

## 15. Tenancy / auth

- `ProductImageRepository` stays the only image repository.
- `PromptExecutionRepository` stays tenant-scoped.
- Cross-tenant `analyse_product_images` → `NotFoundError`.
- No new router. Stage 8, if it adds an endpoint, uses `RequireAdmin`
  unless that stage's plan says otherwise.

---

## 16. API / frontend boundary

Stage 6 adds **no** route under `/api/v1/`.

Stage 6 adds **no** field to `ProductImageRead`, `ProductDetailRead`, or
`frontend/types/api.ts`.

Existing draft image routes keep their current meaning and do not
trigger analysis.

In-process tests and Stage 7 read `ProductImage.analysis` from the ORM.

---

## 17. Stage 7 boundary

```
report = await ImageAnalysisService(session).analyse_product_images(product.id)
```

Stage 6 does not call optimize, does not change `ai_status`, does not
create versions, and does not publish.

---

## 18. Exact test matrix

**No live URLs.** Resolver and httpx transport are fakes.

### 18.1 Fetch / SSRF

Policy (no sockets):

- `100.64.0.1` → reject (`ImageFetchNotGlobalAddress`) **named CGNAT test**
- `127.0.0.1`, `10.0.0.1`, `169.254.169.254`, `192.168.1.1` → reject
- `192.0.2.1` → reject (not global on 3.13)
- `::1`, `fc00::1`, `fe80::1` → reject
- `::ffff:10.0.0.1` → reject (mapped)
- `8.8.8.8` via fake resolver → policy **allow**
- mixed `8.8.8.8` + `127.0.0.1` → reject host

Hop mechanics (fake transport records the request):

- `https://example.test/a.png` resolving only to `8.8.8.8` →
  `request.url.host == "8.8.8.8"`, `Host: example.test`,
  `extensions["sni_hostname"] == "example.test"`, client
  `trust_env is False`, `follow_redirects is False`
- original hostname is never `request.url.host`
- IPv6 chosen address uses bracketed connection URL
- `http://example.test/a.png` → `ImageFetchDisallowedScheme`
- `https://localhost/a.png` → hostname blocked
- `https://127.0.0.1/a.png` → not global
- `https://[::1]/a.png` → not global
- userinfo / port 8443 / `%` zone id → reject
- 302 `Location: https://127.0.0.1/secret` → not global
- 302 relative `Location: /b.png` resolved against **logical**
  `https://example.test/a.png` → next logical
  `https://example.test/b.png`, then a **new** resolve+validate (assert
  resolver called again for that hop)
- 200 body 5_242_881 bytes → too large
- `Content-Type: text/html` → bad type
- `Content-Type: image/png` + `GIF89a` body → bad magic
- mocked timeout → `ImageFetchTimeout`
- DNS exception → `ImageFetchDnsFailure`

### 18.2 Decode + normalisation + blur

- 4096×4096 size (stubbed, **no 16MP allocation**) → pixel policy allow
- 4097×4096 stubbed size → `ImageFetchPixelLimit`
- forged huge IHDR (header only) → `ImageFetchPixelLimit`
- `DecompressionBombWarning` during open mapped to `ImageFetchPixelLimit`
- truncated JPEG/PNG → `ImageFetchDecodeFailed`
- normalisation geometry for every row in the §7.1 table
- `uniform_128.png` → `blurScore=0`, `isBlurry=true`
- `checkerboard_1px.png` → `1040400`, `false`
- `checkerboard_16px.png` → `17174`, `false`
- `checkerboard_16px_box15.png` → `37`, `true`
- `isBlurry` at 99 / 100

### 18.3 Duplicates (Phase A then B)

- two images, identical PNG bytes, different URLs, **either order** →
  each `duplicateOfImageIds` lists the other (symmetric)
- three identical → each lists the other two, sorted
- two different bytes → empty lists
- one success + one fetch failure → success has empty duplicate list
- watermark N/A JSON exact

### 18.4 Model path

- StubProvider caption/alt = Stage 1 formula
- `is_synthetic is True`
- `PromptExecution.prompt_name == "image_analyzer"`
- `test_render("image_analyzer", execute=True)` still calls `complete`
- `execute_image_analysis` never calls `complete`
- `AI_PROVIDER=openai` → `checksOnly` + `AIProviderNotConfiguredError`
- fetch failure → provider **not** called for that image
- programming bug in checks (`monkeypatch` raising `TypeError`) →
  **propagates**; no `decodeFailed` JSONB; no successful report

### 18.5 Tenancy / data

- other tenant's product id → `NotFoundError`
- `ProductImage.alt_text` unchanged (`None` or merchant text)
- `is_supplier`, `url`, `position` unchanged
- merchant-added URL survives subsequent `sync_for_product`
- re-analysis with different bytes replaces `contentSha256`
- Stage 5 quality keys unchanged
- `Product.updated_at` unchanged
- `ProductImageRead` schema unchanged (no `analysis` attribute)

### 18.6 Regression

- optimize execution log: exactly three Stage 4 names; no
  `image_analyzer` / `quality_scorer`
- Stage 4 pins `a,b` / `x, x`
- Stage 5 unit file still 88 passed
- no new router; no frontend file; no schema field on `ProductImageRead`

---

## 19. Dependencies

Backend CI installs with `pip install -e ".[dev]"` from
`backend/pyproject.toml` (`.github/workflows/ci.yml`). There is **no**
Python lockfile in that path. An open range such as `pillow>=11,<12`
does **not** pin a patch across local, CI, and future rebuilds, and that
range is also stale: current Pillow is 12.x.

| Package | Decision |
|---|---|
| **Pillow** | **Add at implementation time**, not in this planning commit. Pin **one exact patch** in `pyproject.toml`: `pillow==12.3.0`. Verified 2026-09-18 against PyPI/release notes: Pillow 12.3.0 (2026-07-01) requires Python `>=3.10` and publishes CPython 3.13 wheels (standard, not free-threaded). Changing the pin later requires rerunning the whole Stage 6 deterministic fixture suite. Do **not** change CI workflows for this |
| OpenCV | **Do not add** |
| imagehash / numpy | **Do not add** |
| httpx | Already present |
| requests | Do not use for this fetch |

Do not set a process-global `Image.MAX_IMAGE_PIXELS` as the only pixel
cap. Decode follows §7.0.

---

## 20. Migration decision

**Yes — `0033`**, one nullable JSONB column `product_images.analysis`.

Not on `ProductVersion.content`. Not a new table (no second caller).
`downgrade()` drops the column. No back-fill.

ORM-only in Stage 6. No response-schema migration.

---

## 21. Commit sequence

1. `build(ai): pin Pillow 12.3.0 for Stage 6 decode` — `pillow==12.3.0`
   in `pyproject.toml` only (no workflow change).
2. `feat(ai): add SSRF-safe product image fetcher` — §6 + §18.1.
3. `feat(ai): decode JPEG/PNG with an explicit pixel cap` — §7.0.
4. `feat(ai): add deterministic blur and byte-duplicate checks` — §7–8
   + fixtures.
5. `feat(ai): execute image_analyzer through analyse_image` —
   `PromptService.execute_image_analysis`. `test_render` untouched.
6. `feat(ai): persist image-analysis evidence on product images` —
   migration `0033` + `ImageAnalysisService` Phase A/B/C. **No**
   `ProductImageRead` change.
7. `test(ai): prove Stage 6 integration and protected behavior`.
8. `docs(ai): record Phase 9 Stage 6 completion`.

---

## 22. Quality gates

Same as Stage 5, plus the new tests. No frontend gate (no frontend
files). CI on the eventual implementation PR is the clean-checkout
authority.

---

## 23. Known limitations

1. **No live vision model.** Stub captions are a URL digest with
   `[STUB-AI]`. They are not image understanding.
2. **Blur is the Laplacian variance after the §7.1 canvas.** Threshold
   100 is justified by the 16px-checkerboard pair (`17174` vs `37`). It
   is not a marketplace sharpness grade.
3. **No perceptual duplicates.**
4. **No watermark detector.**
5. **HTTPS and JPEG/PNG only.**
6. **A future real provider may fetch the stored URL itself.** Stage 6
   SSRF protects *our* bytes path only.
7. **`StubProvider` ignores prompt instructions.**
8. **Duplicates are this-invocation Phase-A successes only.**
9. **`analysis` is ORM-only until Stage 8.**
10. **Not deployed.**

---

## 24. Acceptance criteria

Stage 6 is done when:

- [ ] `pyproject.toml` contains `pillow==12.3.0` (not a range).
- [ ] Fetcher tests in §18.1 pass without network, including CGNAT and
      connect-to-IP / Host / SNI / `trust_env` assertions.
- [ ] Pixel cap is enforced **before** `load()`; 4096×4096 allowed by
      policy; 4097×4096 rejected; tests do not allocate huge images.
- [ ] Normalisation geometry matches the §7.1 table exactly.
- [ ] Blur scores match §7.4 exactly, including the 16px pair.
- [ ] Duplicate lists are computed in Phase B and are symmetric.
- [ ] Watermark JSON is the N/A object.
- [ ] `execute_image_analysis` calls `analyse_image`, not `complete`.
- [ ] `ProductImage.alt_text` is never assigned by Stage 6.
- [ ] `ProductImageRead` is unchanged.
- [ ] Expected fetch/decode/`AIError` stay per-image; `TypeError` in
      checks propagates.
- [ ] One flush at end of Phase C; unexpected exception rolls back.
- [ ] Optimize log still has exactly three Stage 4 names.
- [ ] No new API route, no frontend file, no Stage 7 pipeline wiring.
- [ ] Full local pytest recorded honestly; CI 10/10 on the
      implementation PR.

---

## 25. Independent-review notes

Look hardest at:

- `image_fetch.py` — `is_global` allow-list; CGNAT; mixed DNS; connection
  URL is the IP; `Host` + `sni_hostname` are the logical name;
  `trust_env=False`; relative redirects against the logical URL; no
  second DNS per hop.
- Decode — size check before `load()`; bomb warning/error mapped;
  no silent warning.
- Blur pair — `17174` / `37` / threshold 100.
- Phase A → B → C ordering and symmetric duplicates.
- `ProductImageRead` diff empty.
- `prompt.py`: `test_render` untouched.
- Unexpected exceptions propagate.

**CLAUDE RETURN REVIEW CHECKPOINT:** All commits from the Stage 5
takeover onward, including this plan and any Stage 6 implementation,
require a fresh Claude end-to-end review when Claude becomes available
again. Cursor review is not a substitute. Claude has not reviewed
Stage 6.
