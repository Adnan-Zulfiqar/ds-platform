# Phase 9 Stage 10 — AI Product Studio frontend plan

**Status:** Planning only. Stage 10 is not implemented.
**Date:** 2026-09-21

This document is the implementation contract for the merchant UI over the
Stage 7, Stage 8, and Stage 9 APIs already on `develop`. It does not change
those APIs.

---

## 1. Baseline identity

| Pin | Value |
|---|---|
| Branch this plan was written from | `docs/phase-9-stage-10-plan` |
| Base | `develop` @ `72e76921fc80b203133423ad3bd92bd380c7e02b` |
| `origin/develop` at planning start | `72e76921fc80b203133423ad3bd92bd380c7e02b` |
| `origin/main` | `3ce66d488e94ad3805fe24903deda99691c234a6` (unchanged) |
| Stage 7 | Fully closed |
| Stage 8 | Fully closed (PR #23, merge `e65b2f5`) |
| Stage 9 | Fully closed (PR #24, merge `72e7692`, post-merge CI `35656740281` 10/10) |
| Alembic head | `0034` |
| Production | Undeployed |

**Wire-contract revision.** The approve route, publish route, preview DTO,
bulk item field, and Studio catalogue in this document match the merged
Stage 8 and Stage 9 handlers. Earlier wording that put `candidateVersionId`
in those POST bodies, or that bulk-selected only published products, is
withdrawn.

Stage 10 consumes:

- `docs/PHASE_9_PLAN.md`
- `docs/PHASE_9_STAGE_7_PLAN.md` and `docs/PHASE_9_STAGE_7_COMPLETION.md`
- `docs/PHASE_9_STAGE_8_PLAN.md` and `docs/PHASE_9_STAGE_8_COMPLETION.md`
- `docs/PHASE_9_STAGE_9_PLAN.md` and `docs/PHASE_9_STAGE_9_COMPLETION.md`

It does not redesign them.

---

## 2. Stage 10 objective

**AI Product Studio** is the merchant workflow for the pipeline that already
exists. It is not a second product editor.

One product:

1. Generate a preview candidate.
2. Compare it with the current product.
3. Read the evidence the server returned.
4. Approve that exact candidate.
5. Publish that exact approved candidate, only after approval and only with
   the post-approval token.

Many products:

1. Select up to 50 products from Drafts and from Published.
2. Start one bulk preview run.
3. Watch durable Postgres-backed progress.
4. Open each successful item on its exact `candidateVersionId`.
5. Review that candidate with the same single-product screen.

The merchant reviews before anything is activated or published. The Studio
never auto-approves and never auto-publishes.

---

## 3. Existing frontend audit

What Stage 10 reuses:

| Piece | Where | How Stage 10 uses it |
|---|---|---|
| App shell, sidebar, protected layout | `frontend/lib/navigation.ts`, `sidebar-nav.tsx` | One new nav item |
| Auth | `useAuth().hasRole` | Show or hide Studio. Backend stays `RequireAdmin` |
| Store picker | `useShopifyStores` in the draft editor | Same connected-store list for preview and publish |
| Dialog / Sheet | `components/ui/dialog.tsx`, `sheet.tsx` | Confirm approve, publish, cancel, bulk start |
| Alert, Badge, Button, Skeleton, ErrorState, EmptyState | existing primitives | All Studio states |
| Page envelope | `Page<T>`, `ListQuery`, `toQuery` | Bulk items |
| Errors | `ApiError.code`, `details`, `requestId` | Branch on code and `details` reason, never on message text |
| Polling precedent | `useApplication` in `global-rules.ts` | `refetchInterval` returns `false` on a terminal status; `refetchIntervalInBackground: false` |
| Idempotency precedent | global-rules impact panel `useRef` | One key per merchant intent, reused on retry |
| Query defaults | `query-provider.tsx` | `staleTime` 60s, `refetchOnWindowFocus: false` |
| Breakpoint | catalogue table `lg:block` / cards `lg:hidden` | Same `lg` (1024px) split |
| External links | existing trusted URL helpers | Shopify admin / storefront only through those helpers |
| Description display | `stripHtml` in `published-product-summary.tsx` | Current-side comparison text |

What Stage 10 does not reuse as the Studio itself:

- The draft editor form, autosave, or conflict banner. Studio does not write
  into that form.
- `dangerouslySetInnerHTML`. The only current use is the supplier snapshot
  in `draft-product-editor.tsx`. AI text does not go through it.
- `POST /products/{id}/optimize` and `POST /integrations/shopify/publish` as
  Studio actions.
- Celery task state. There is no frontend task-status client, and Stage 10
  does not add one.
- WebSockets or SSE. The app does not have them.

There is no shared Tabs primitive and Stage 10 does not add a component
library. Studio home uses two existing `Button`s with `role="tab"` for
Drafts and Published. Comparison stays a two-column layout, not a tab inside
the draft editor.

`frontend/package.json` has `lint`, `typecheck`, and `build`. It has no unit
test script. Stage 10 does not add a test runner. Behaviour is covered by
Playwright.

---

## 4. Existing AI UX audit

| Control | Current behaviour | Conflict with Stage 7–9 |
|---|---|---|
| `OptimizeProductButton` | `POST /products/{id}/optimize`. Label "Optimize with AI". No role check. Invalidates product detail, versions, and lists. | That endpoint generates **and activates**. It skips preview, approval, and the T0/T1 token. |
| Draft menu "Improve with AI tools" | Same optimize mutation (`product-actions-menu.tsx`) | Same bypass, inside the editor. |
| `ProductVersionHistorySheet` | "Activate" on every inactive row | Stage 7 returns 422 `pipeline_candidate_requires_approval` for pipeline rows. The sheet would show a control the API refuses. |
| Product table badge | `aiStatus`: optimized / not optimized / failed | Still true for the active version. It is not a pipeline-candidate badge. |
| `PublishedProductSummary` | Shopify lifecycle. No AI action. Unpublished drafts redirect to `/drafts/{id}` | Studio must not become a second editor on this page, and must not fight that redirect. |
| History empty copy | Tells the merchant to use "Optimize with AI" | That copy points at the bypass. |

`ProductVersion` in `frontend/types/api.ts` matches `ProductVersionRead`:
`versionNumber`, `source`, `active`, title, description, prices, `createdAt`.
It does **not** include `pipelineCandidateVersion`, `pipelineSourceUpdatedAt`,
or `isSynthetic`. `source` is `ai_generated` for both legacy optimize output
and pipeline candidates. The version list alone cannot tell them apart.

---

## 5. Chosen Studio information architecture

**Hybrid.**

| Surface | Route | Job |
|---|---|---|
| Studio home | `/ai-studio` | Bulk selection, start, and the run dashboard |
| Candidate review | `/ai-studio/products/[productId]` | One product: preview, compare, approve, publish |
| Exact bulk candidate | same review route with `?candidate=<candidateVersionId>` | Opens that version, not "whatever is newest" |

Why this and not the alternatives:

- A draft-editor AI tab would sit on the autosave form. Approving would be
  one click away from overwriting a dirty merchant draft. Stage 10 keeps
  Studio state off that form.
- `/products/{id}/ai` fights the existing rule that an unpublished draft at
  `/products/{id}` redirects to `/drafts/{id}`.
- A single global page with no per-product route cannot deep-link a bulk
  item to one `candidateVersionId`.

Draft products and published products use the **same** review route. The
draft editor and the published summary only link to it. There is one review
screen, not two editors.

Navigation: add **AI Studio** under Catalogue in `NAV_SECTIONS`, href
`/ai-studio`, status `ready`. The item is rendered only when
`hasRole("owner")` or `hasRole("admin")`. Other roles do not see it. Hiding
it is presentation. The API still returns 403.

---

## 6. Single-product workflow

Route: `/ai-studio/products/[productId]`.

1. Load product detail (`GET /products/{id}`). 404 uses the existing not-found
   pattern. The page does not follow the draft redirect; this route is valid
   for drafts and published products.
2. Merchant picks tone (`professional` default, `persuasive`, `luxury`,
   `technical`, `friendly`) and an optional connected store.
3. **Generate preview** calls
   `POST /products/{productId}/pipeline/preview` with `{ tone, storeId? }`.
4. A normal fresh candidate is inactive. That response has
   `candidateActive: false`, `publishable: false`, and a `pipelineBlockers`
   entry whose `code` is `candidate_not_approved`. The page stores
   `candidateVersionId` and `approvalExpectedUpdatedAt` as token **T0**, and
   renders that object. Publish stays disabled. The pre-approval object is
   never treated as permission to publish.
5. Side-by-side comparison (section 7). Quality, image evidence, and
   readiness render from the preview object currently on screen.
6. **Approve this version** is a confirmed action. It means "make this exact
   candidate the active product version." It does not mean publish.
   `POST /products/{productId}/pipeline/versions/{candidateVersionId}/approve`
   with body exactly `{ expectedUpdatedAt: T0 }`. The version id is only in
   the URL.
7. On 200, the body is `ProductDetailRead`. Replace the local token with
   `updatedAt` (**T1**). Discard T0. Invalidate product detail, versions,
   and list queries. Do not write the candidate into the draft editor cache.
8. Immediately GET
   `/products/{productId}/pipeline/versions/{candidateVersionId}/preview?storeId=<selected store>`.
   Use that response for `candidateActive`, `channelReadiness`,
   `pipelineBlockers`, `pipelineWarnings`, `publishable`, and the evidence
   on screen. Do not keep the pre-approval preview for those fields. T1
   stays the approve response's `updatedAt`. Do not copy
   `approvalExpectedUpdatedAt` from this GET over T1.
9. Publish becomes enabled only when that GET succeeded, `candidateActive`
   is true, `publishable` is true, a store is selected, and the local token
   is T1. A fresh candidate's `candidate_not_approved` blocker is gone on
   that GET when approval stuck and no other blocker remains. If the GET
   fails, Publish stays disabled and the merchant can retry the GET. Do not
   generate a new candidate to recover a failed refresh.
10. **Publish** calls
    `POST /products/{productId}/pipeline/versions/{candidateVersionId}/publish`
    with body exactly `{ storeId, expectedUpdatedAt: T1 }`. The version id
    is only in the URL. Success is `ShopifyPublishResponse`, the same shape
    as the existing `ShopifyPublishResult` type. It has no pipeline version
    ids. The candidate id stays the one already on this page. Links use the
    existing trusted URL helpers.

If the merchant opens `?candidate=<id>` (from bulk, or from version history):

- `GET /products/{id}/pipeline/versions/{id}/preview?storeId=`
- 200: this is a pipeline candidate. Continue the review flow. T0 is
  `approvalExpectedUpdatedAt` from this GET.
- 422 with reason `not_a_pipeline_candidate`: this is a legacy AI version.
  Show it as legacy (section 26) with Activate available. Do not show the
  pipeline Approve button.

Changing the selected store does not call POST preview. GET the same
`candidateVersionId` with the new `storeId`. `channelReadiness` and
`publishable` on screen come only from that GET. While it is in flight,
readiness for the previous store is not shown and Publish stays disabled.
Before approval, T0 stays the value from the generate response. After
approval, T1 stays the approve response. The GET is composition for an
existing candidate, not a new candidate and not a new concurrency token.

No step calls approve or publish by itself.

---

## 7. Side-by-side comparison

Desktop (`lg` and up): two columns, **Current** and **AI candidate**.

Below `lg`: the same blocks stacked, candidate first so the merchant sees
the proposal without scrolling past the whole current product. Approve and
publish stay in a sticky footer so they are reachable without horizontal
scroll.

`PipelinePreviewResponse` is flat. There is no nested `candidate` object,
no `candidate.versionNumber`, and no `candidate.source`.

| Field | Current column | Candidate column |
|---|---|---|
| Title | `original.title` | `proposal.title` |
| Description | `original.description` shown as text via the existing `stripHtml` helper | `proposal.description` as plain text (`whitespace-pre-wrap`). It is stored plain text |
| Optimization score | `qualityBaseline.score` (and that object's `versionNumber` as meta) | `qualityScore` and `qualityDelta` |
| Breakdown | — | `qualityBreakdown` as in section 8 |
| Version | active version number from product detail when present | `candidateVersionNumber` |
| What this column is | Current product | Label **AI candidate**. Do not read a `source` field; the response does not have one |
| Provider | — | `provider` |
| Active | — | `candidateActive` |
| Synthetic | — | `isSynthetic` (section 16) |

`proposal` is `PipelineListingViewRead`: `title`, `description`, `seoTitle`,
`seoDescription`, `keywords`, `tags`. Image caption and alt text are not on
`proposal`. They are `imageAnalysis.images[].analysis.captionProposal` and
`altTextProposal` (section 9).

SEO title, meta description, keywords, and tags are proposal evidence. The
publish overlay does not write them. The UI labels that block **Proposal
only — not sent to Shopify**. Stage 4 SEO is not implied to publish.

Do not add fields the payload does not have. Do not render model rationale
that was not returned.

---

## 8. Quality score UX

Stage 5 scores are deterministic. The UI must not call them confidence,
accuracy, or probability.

| API field | Label |
|---|---|
| `qualityScore` | Optimization score |
| `qualityBaseline` | Previous optimization score. The value is `{ versionNumber, score }`. Show `score`. Do not render the object as text |
| `qualityDelta` | Change vs original |
| `qualityScoreVersion` | shown as small meta ("Score version N"), not as a grade |
| `qualityBreakdown` | structured block below. Not a map of numbers |

`qualityBreakdown` is `ProductVersionQualityBreakdownRead`:

- `earned` and `applicableMax` — "Points earned" as `earned` of `applicableMax`
- `dimensions.title` — points, max, applicable, length
- `dimensions.description` — points, max, applicable, length
- `dimensions.repetition` — points, max, applicable, and the four boolean
  checks (`titleNotStuffed`, `descriptionNotPhraseStuffed`,
  `descriptionNotDominated`, `descriptionDistinctFromTitle`) as text rows
- `dimensions.keywordCoverage` — applicable, points, max, matched, total,
  `source` (`search_topics`, `tags`, `meta_keywords`, or null), the
  `keywords` array as text, and `truncated`
- `seoFormat`, when present — three booleans: SEO title bound, SEO
  description bound, keywords present. When null, omit the block

Nested values are numbers, booleans, strings, arrays, or a nullable source.
Render each field as itself. Do not stringify an object. Do not type the
breakdown as `Record<string, number>` or `Record<string, any>`. `null`
breakdown: "Score breakdown unavailable".

Delta copy:

- positive: "Higher than the current version"
- zero: "Same as the current version"
- negative: "Lower than the current version"

That sentence describes the score arithmetic only. It does not claim sales,
conversion, or ranking.

`null` score: "Score unavailable". Do not show 0.

---

## 9. Image evidence UX

Use `imageAnalysis` from the preview payload. Do not fetch supplier image
bytes in the browser to re-run Stage 6.

The report is `productId` plus `images[]`. Each image has `imageId`,
`position`, `status`, `errorCode`, and `analysis` (nullable). There is no
`failureReason` and no status value `analyzed` or `failed`.

Decide the row from the outer `status`, in this order:

1. `fetchFailed` or `decodeFailed` — **Analysis unavailable**, plus `errorCode`
   when set. This holds even when `analysis` is null.
2. `unknown`, or `analysis == null` — **Not analyzed**. This is not a failure.
3. `succeeded` — checks plus `analysis.captionProposal` and
   `analysis.altTextProposal`.
4. `checksOnly` — checks when `analysis.checks` is present. Caption and alt
   text are unavailable. This is not a total failure.
5. Any other string — **Analysis unavailable**. Do not crash.

When `analysis.isSynthetic === true`, badge **Test caption** on that image's
proposed caption and alt text.

When `analysis.isSynthetic === true`, badge **Test caption** on that image's
proposed caption and alt text.

`analysis`, when present, also carries `imageAnalysisVersion`, `sourceUrl`,
`contentSha256`, dimensions, `checks` (blur, duplicates, watermark),
`provider`, `model`, `promptName`, and `promptVersion`. Show checks that are
present. Do not invent a product-level analysis status; an empty `images`
array is **Not analyzed**, not an error banner.

Caption and alt text are evidence on the image, not fields of `proposal`,
and they are not a published alt. Label them **Proposal only — not sent to
Shopify**.

---

## 10. Readiness UX

Readiness is server-authoritative. React does not re-implement blocker rules.

Render, when present:

- `channelReadiness.canPublish` when a store was passed and the server
  returned readiness. With no `storeId`, `channelReadiness` is null and
  `publishable` is false
- `channelReadiness.blockers[]` and `recommendations[]`: `code`, `message`,
  `field`, `section`, `action`
- `pipelineBlockers[]` and `pipelineWarnings[]`: `code` and `message` only.
  They have no `field`, `section`, or `action`
- top-level `publishable`

A card shows `message`, plus `field` / `section` when the channel item has
them. A channel `action` may link to the draft editor or Integrations only
when the string is one the app already uses (`Go to Overview`, `Go to
Description`, `Go to Media`, `Go to Pricing`, `Go to Shipping`, `Choose a
store`, `Open Integrations`, `Reload draft`). Any other action is text.
`candidate_not_approved` is a pipeline blocker code. The Approve button on
this page is how it clears. Do not invent an action named
`approve_candidate` or `connect_shopify`.

Publish is enabled only from the latest successful GET preview for the
**selected** store (section 6), and only when that payload has
`publishable: true` and `candidateActive: true`, a store is selected, and
the local token is T1. The POST preview payload does not unlock Publish.
The client does not invent a blocker the payload omitted.

---

## 11. T0 / T1 concurrency state machine

Held in React state on the review page only. Not in Zustand. Not in the
draft form.

```text
idle
  → POST preview 201
  → reviewing(T0 = approvalExpectedUpdatedAt, candidateVersionId,
              candidateActive false, publishable false)
  → POST .../versions/{candidateVersionId}/approve { expectedUpdatedAt: T0 }
  → approved(T1 = ProductDetail.updatedAt)
  → GET .../versions/{candidateVersionId}/preview?storeId=
  → publishable state comes from that GET
  → POST .../versions/{candidateVersionId}/publish
       { storeId, expectedUpdatedAt: T1 }
```

Rules:

- T0 is saved from `approvalExpectedUpdatedAt` on the POST preview response,
  or on the GET that opened `?candidate=` before any approve.
- Approve sends only that T0. The version id is the URL path.
- After approve 200, discard T0. The publish token is
  `ProductDetail.updatedAt` from that response and nothing else.
- The following GET may show a new `approvalExpectedUpdatedAt`. Do not copy
  it onto T1. Do not replace T1 with an older timestamp from any later
  response. A newer product-detail `updatedAt` may replace T1 only when it
  is strictly newer and the refreshed preview still has this candidate
  active. Never write T0 back.
- Starting a new preview replaces the candidate and stores that response's
  T0. Publish is disabled again until a later approve and GET.
- Publish never reads `approvalExpectedUpdatedAt`.

`expectedUpdatedAt` is the ISO timestamp string the API returned, passed
through unchanged.

---

## 12. Stale-preview recovery

Approve can return 409 `conflict` with a `details` entry `type: "reason"`
and `message: "stale_preview"` (the product changed after T0). Also
`draft_version_stale` when the draft's active version moved.

UX for both:

- Keep the candidate on screen.
- Banner: the product changed after this preview was generated.
- Approve disabled.
- One action: **Generate fresh preview**. It runs only after the click.
- Do not auto-regenerate.
- Do not copy the candidate into the product or the draft form.
- Do not clear the merchant's draft editor if they have it open in another
  tab. Studio does not share that form.

---

## 13. Lost-approve-response recovery

If the approve request times out or the connection drops:

- The same `candidateVersionId` stays selected.
- T0 stays in state because the client never received a replacement.
- The merchant may press Approve again.
- Stage 8 treats a retry of the already-active candidate with the old T0 as
  a 200 no-op and returns the current `ProductDetail` whose `updatedAt` is
  T1.
- The client stores that `updatedAt` as T1.
- It then GETs the exact candidate preview for the selected store, the same
  as a normal approve. Publish stays disabled until that GET says
  `candidateActive` and `publishable`.

The UI does not offer "generate again" as the recovery for a dropped
response. Regeneration is a separate, explicit action.

If the retry returns `stale_preview`, follow section 12. The product really
changed.

---

## 14. Approval UX

Confirm dialog title: **Approve this AI version?**

Body: "This replaces the current product text with this exact candidate. It
does not publish to Shopify."

Confirm button: **Approve version**. Cancel: **Keep current version**.

After 200:

- Badge **Approved** on the candidate (`candidateActive` from the follow-up
  GET, not from the stale POST preview).
- Product queries invalidated, and the exact candidate preview query
  refetched (section 27).
- T1 stored from the approve body.
- Publish stays disabled until that refetch says `publishable`.
- No Shopify request has been made. Copy under the badge says so.

Approve is hidden when the candidate is already active (GET preview of the
approved version). Publish is the next step, still manual.

---

## 15. Publish UX

Endpoint:
`POST /products/{productId}/pipeline/versions/{candidateVersionId}/publish`.

Body exactly: `{ storeId, expectedUpdatedAt: T1 }`. No `candidateVersionId`
field.

Not `POST /integrations/shopify/publish`. Stage 8 publish already calls the
existing publisher. The response is `ShopifyPublishResponse` (`message`,
`listingId`, `externalProductId`, `externalHandle`, `externalGraphqlId`,
`shopDomain`, `storefrontUrl`, `adminUrl`, `onlineStorePublished`,
`updated`). Reuse `ShopifyPublishResult`. Do not expect pipeline version ids
in the body. The candidate id remains the route and the local review state.

UI:

- Store selector from connected stores. Required. Changing it refetches GET
  preview (section 6) and does not POST a new candidate.
- Readiness cards from the GET for that store (section 10).
- Button **Publish approved version**, disabled until section 6 step 9 holds.
- In-flight label **Publishing…**. One in-flight request; the button ignores
  a second click.
- Success: the fields above, through existing URL helpers only.
- Failure: `ApiError` mapping in section 29. The candidate stays approved.
  The merchant can retry publish with the same T1 unless a 409 says the
  product changed, in which case they reload the product. The token updates
  from a newer `updatedAt` only when the refreshed preview still shows this
  candidate active. Do not publish with T0.

---

## 16. Synthetic-provider UX

`provider === "stub"` or `isSynthetic === true` is a test candidate.

Visible banner: **Test AI preview**. Supporting line: "This text came from
the test provider. It cannot be published to Shopify."

Do not use the words "production AI", "real AI", or "ready for Shopify" on
that state.

Publish stays off. The server also returns `synthetic_publish_blocked` if a
client bypasses the button. The UI shows that reason as: "Test previews
cannot be published." There is no client override.

Approve is still allowed. Activation of a synthetic candidate is what the
API already permits; publish is what it blocks. The banner stays after
approve so a published-looking button never appears.

---

## 17. Role / auth UX

Stage 8 and Stage 9 routes are admin/owner.

| Role | Nav item | Buttons | Direct URL |
|---|---|---|---|
| owner, admin | Visible | Visible | Studio loads |
| member, viewer | Hidden | Hidden | Permission panel: "AI Studio is available to owners and admins." No pipeline request is sent once the role is known |
| session loading | Skeleton | Hidden | Skeleton |

A 403 from the API, if a request is made anyway, uses the same panel plus
`requestId`. It does not show another tenant's product. 404 stays 404.

Stage 10 does not change backend roles.

---

## 18. Bulk selection

Lives on `/ai-studio`, not as a new checkbox column on the main Products
table. The Products table keeps row click → product. Adding selection there
would fight that click target and the mobile card layout.

`GET /products` returns only products published to at least one channel.
Imported unpublished products are `GET /drafts`. Studio home lists both.
Optimization is part of the pre-publish workflow, so a Drafts-only merchant
must be able to select here. No backend change: two existing hooks.

Tabs, default **Drafts**:

| Tab | Query |
|---|---|
| Drafts | `useDrafts` |
| Published | `useProducts` |

Each tab uses page size 20 and the existing list query (`page`, `size`,
search). The HTTP parameter is `size`. It is not `pageSize`.

- One checkbox per row, and per card below `lg`, on both tabs.
- **Select page** selects only the ids on the current page of the current
  tab that fit under the cap.
- Selection is one `Set` of product ids. It survives pagination and tab
  changes for the visit. A product is not in both server lists, so the set
  does not need de-duplication across lifecycles.
- Header shows **n / 50 selected** and **Clear**, counting both tabs.
- At 50, further checkboxes on either tab are disabled. Copy: "You can
  optimize up to 50 products at a time."
- The start request sends exactly the selected ids. It never slices a larger
  set down to 50 without telling the merchant, because the UI never lets the
  set exceed 50.

There is no "select all products matching filters". Stage 9 accepts an
explicit id list only. Selecting only published products is not the design.

---

## 19. Bulk run creation and idempotency

`POST /products/pipeline/runs` → 202.

Controls on the confirm dialog: selected count, tone, optional store.

Idempotency key:

- Mint one UUID when the merchant opens the confirm dialog for a frozen
  snapshot: sorted unique ids, tone, store id.
- Store it in a ref beside that snapshot.
- Double-click, button retry, and a network retry of that snapshot send the
  same key and the same body.
- If they change tone, store, or selection, that is a new intent: mint a new
  key. Reusing the old key with a different fingerprint is a 409 `conflict`
  (payload mismatch). The client avoids that by minting.
- A brand-new dialog after success mints a new key. Do not reuse a key whose
  run already exists unless the merchant is explicitly retrying the same
  failed HTTP attempt.

---

## 20. Bulk progress polling

`GET /products/pipeline/runs/{runId}`.

React Query `refetchInterval`:

- `5_000` while `status` is `pending` or `running`
- `false` when `completed`, `partial`, `failed`, or `cancelled`
- `refetchIntervalInBackground: false`
- no `refetchOnWindowFocus` override (provider default is false)
- unmounting the page drops the observer, which stops the interval

Do not poll at 500ms. Do not read Celery. Do not open a socket.

Dashboard shows status, `totalCount`, `processedCount`, `succeededCount`,
`failedCount`, `skippedCount`, `missingCount`.

Progress percent is `processedCount / totalCount` (0 when total is 0). The
word next to a finished run is the status:

| Status | Label |
|---|---|
| pending | Queued |
| running | Running |
| completed | Completed |
| partial | Partial |
| failed | Failed |
| cancelled | Cancelled |

A partial run with 48 succeeded and 2 failed is **Partial**, even if
processed equals total. Do not show "100% success".

---

## 21. Bulk item review

`GET /products/pipeline/runs/{runId}/items` with `page` and `size` (default
20, backend max from `ListQueryParams`). The query string uses `size`, not
`pageSize`. The UI does not request every item in a loop.

The run summary field is `status`. The item field is `state`
(`PipelineBulkItemState`): `pending`, `succeeded`, `failed`, `skipped`,
`missing`. There is no item `status`.

Each row (card below `lg`): `submittedProductId` (title from the selection
list when that id was selected; otherwise the id), `productId` when set,
`state`, `attemptCount`, and `errorCode` / `errorMessage` when failed.
Those strings are the API's safe codes, shown as text.

**Open review** renders only when `state === "succeeded"` and `productId`
is not null and `candidateVersionId` is not null. Link:

`/ai-studio/products/{productId}?candidate={candidateVersionId}`

using `item.productId` and `item.candidateVersionId`. The review page loads
that version. It does not pick the newest `ai_generated` row from version
history.

`state === "failed"`, `"skipped"`, `"pending"`, or `"missing"` does not show
Open review. A `missing` item may have `productId: null`. Do not invent an
id or a link for it.

---

## 22. Bulk cancellation

**Cancel run** on a pending or running run. Confirm: "Stop this run? The
product already being optimized may still finish. Cancellation is not
instant."

`POST /products/pipeline/runs/{runId}/cancel`.

Then refetch the summary on the same query key. Item rows update from the
server. The client does not mark leftover pending rows as skipped.

`cancelled` is terminal. Polling stops. Historical rows may still have
`state: "pending"`. The run `status` is the authority. Copy says that. Do
not locally rewrite remaining items to `skipped`.

---

## 23. Active-run conflict UX

A second start while one run is pending or running returns 409
`pipeline_bulk_run_active`. The body does not include the active run id, and
there is no list-runs endpoint.

Client behaviour:

- On 202, save `runId` in `sessionStorage` under
  `droppilot.aiStudio.activeRun.<tenantId>`.
- Clear that key when a fetch shows a terminal status.
- On 409 `pipeline_bulk_run_active`: stop. Do not retry.
  - If this browser still has the stored id, the banner links to
    `/ai-studio?run=<id>`.
  - If it does not, the banner says another bulk optimization is already
    running for this workspace, and that this browser does not have that
    run. No invented id. No new endpoint in Stage 10.

Same key and same fingerprint returning the original 202 is success: open
that run. It is not an error.

---

## 24. Route and navigation design

| From | Control | To |
|---|---|---|
| Sidebar | AI Studio | `/ai-studio` |
| Studio home | a selected product's review, before a bulk run | `/ai-studio/products/{id}` |
| Studio home after 202 | dashboard | `/ai-studio?run={runId}` |
| Bulk item | Open review | `/ai-studio/products/{item.productId}?candidate={item.candidateVersionId}` only when `state` is `succeeded` and both ids are set |
| Draft editor More menu | Open AI Studio | `/ai-studio/products/{id}` |
| Published summary | Optimize in AI Studio | `/ai-studio/products/{id}` |
| Catalogue row | AI Studio (replaces Optimize) | `/ai-studio/products/{id}` |
| Version history | Review in AI Studio | `/ai-studio/products/{id}?candidate={versionId}` |

`/products/{id}` is unchanged, including the redirect of unpublished drafts
to `/drafts/{id}`.

Query `run` on the home page selects which dashboard to show. Absence means
the selection screen, plus a link to the stored active run when one exists.

---

## 25. Legacy Optimize button migration

| Control | Decision |
|---|---|
| `OptimizeProductButton` on the product table | **Remove from the primary flow.** Replace with a link "AI Studio". The component stops calling `POST /optimize` |
| Draft menu "Improve with AI tools" | **Replace** with "Open AI Studio" linking to the review route. Remove the optimize mutation from this menu |
| History empty-state sentence that names "Optimize with AI" | **Change** to point at AI Studio |
| Backend `POST /products/{id}/optimize` | **Keep.** Stage 10 does not delete it. No primary merchant button calls it |

Leaving the current button in place would keep an activation path next to a
review path. That is the ambiguity this stage exists to remove.

---

## 26. Version-history activation conflict

`ProductVersionRead` cannot flag a pipeline candidate. Stage 10 does not add
that field.

Sheet behaviour:

| Row | Control |
|---|---|
| Active version | Current badge. No Activate |
| Inactive, `source === original` | **Activate** (restore). This is the pre-AI snapshot path Stage 7 still allows |
| Inactive, `source === ai_generated` | **Review in AI Studio** linking to `?candidate=`. No Activate button on the sheet |

The review page classifies:

- GET preview 200 → pipeline review (approve / publish).
- GET preview 422 reason `not_a_pipeline_candidate` → legacy AI version.
  Show title and date, and an **Activate** button that calls the existing
  activate endpoint. Pipeline rows never reach that button, so the merchant
  does not hit `pipeline_candidate_requires_approval` from a primary control.

If activate still returns that 422 (a race, or a client bug), the alert says
this version has to be approved in AI Studio, with the same link. It does
not look like a generic failure.

---

## 27. React Query design

Extend `productKeys` in `frontend/services/products.ts`:

```text
productKeys.pipelineCandidate(productId, candidateVersionId, storeId ?? null)
productKeys.pipelineRun(runId)
productKeys.pipelineRunItems(runId, query)
```

POST preview is a mutation. It does not use a query key named `"new"`.
The candidate key includes `storeId` because `channelReadiness` and
`publishable` are composed for that store. A different store is a different
cache entry. Do not read the previous store's entry while the new one loads.

| Event | Invalidate |
|---|---|
| Preview 201 | Write the response into `pipelineCandidate(productId, candidateVersionId, storeId)` for the store that was sent (null when omitted). Do not leave a stale pre-approval entry for a different store marked publishable |
| Approve 200 | `productKeys.detail(id)`, `versions(id)`, `productKeys.lists()`, `draftKeys.detail(id)`, and refetch `pipelineCandidate(productId, candidateVersionId, selectedStoreId)` |
| Store change | Fetch `pipelineCandidate` for the same candidate and the new store. Keep T0 or T1 as section 6 says |
| Publish 200 | detail, versions, lists, draft detail, draft listings / Shopify status keys already used by the editor publish mutation |
| Bulk 202 | `pipelineRun(runId)` and items |
| Cancel 200 | `pipelineRun(runId)` |

Do not `invalidateQueries()` with no key. Do not put candidate title or
description into the draft editor's form state.

Hooks to add beside the existing product hooks:

- `usePipelinePreview` mutation (`POST .../pipeline/preview`)
- `usePipelineCandidate` query (`GET .../pipeline/versions/{versionId}/preview`, key includes store id)
- `useApprovePipelineCandidate` mutation (`POST .../pipeline/versions/{versionId}/approve`, body `{ expectedUpdatedAt }` only)
- `usePublishPipelineCandidate` mutation (`POST .../pipeline/versions/{versionId}/publish`, body `{ storeId, expectedUpdatedAt }` only, response `ShopifyPublishResult`)
- `usePipelineRun` query (section 20 interval)
- `usePipelineRunItems` query
- `useStartPipelineRun` mutation
- `useCancelPipelineRun` mutation

`useOptimizeProduct` remains in the service so the legacy endpoint is still
typed, but no primary screen calls it after this stage. Tests that required
the button move to the Studio specs; the legacy hook is not deleted in case
a later explicit legacy screen needs it. If implementation finds zero
callers, deleting the hook in the same stage is acceptable **only** together
with the button removal, and the backend route still stays.

---

## 28. Frontend types

Add hand-written mirrors to `frontend/types/api.ts`. No OpenAPI codegen in
this stage. No `any` on these types.

Families, matching the camelCase wire:

- `PipelinePreviewRequest` — `{ tone, storeId? }`
- `PipelinePreview` — flat `PipelinePreviewResponse`: `productId`,
  `candidateVersionId`, `candidateVersionNumber`, `candidateActive`,
  `sourceUpdatedAt`, `approvalExpectedUpdatedAt`, `original`, `proposal`,
  `qualityScore`, `qualityBaseline`, `qualityDelta`, `qualityScoreVersion`,
  `qualityBreakdown`, `imageAnalysis`, `isSynthetic`, `provider`,
  `channelReadiness`, `pipelineBlockers`, `pipelineWarnings`, `publishable`.
  No nested `candidate`. No `source` on this object
- `PipelineListingView` — `title`, `description`, `seoTitle`,
  `seoDescription`, `keywords`, `tags`
- `PipelineCheckItem` — `code`, `message`
- `ProductVersionQualityBaseline` — `versionNumber`, `score`
- `ProductVersionQualityBreakdown` — `earned`, `applicableMax`,
  `dimensions` (`title`, `description`, `repetition`, `keywordCoverage`),
  optional `seoFormat`. Nested fields are the Stage 5 numbers, booleans,
  strings, string arrays, and nullable `source`. Not `Record<string, number>`
- `PipelineImageAnalysisReport` — `productId`, `images`
- `PipelineImageAnalysisItem` — `imageId`, `position`, `status`,
  `errorCode`, `analysis`
- `PipelineImageAnalysisEvidence` — `status`, `errorCode`, `checks`,
  `captionProposal`, `altTextProposal`, `isSynthetic`, `provider`, `model`,
  `promptName`, `promptVersion`, and the Stage 6 measurement fields
- Item `status` union used by the UI: `succeeded`, `checksOnly`,
  `fetchFailed`, `decodeFailed`, `unknown`, plus a string fallback so an
  unknown status does not fail the type parse
- `PipelineApproveRequest` — `{ expectedUpdatedAt }` only
- `PipelinePublishRequest` — `{ storeId, expectedUpdatedAt }` only
- Publish result: existing `ShopifyPublishResult`. Do not add
  `PipelinePublishResult`
- `channelReadiness`: existing `ShopifyPublishReadiness`
- `PipelineBulkRunCreate`, `PipelineBulkRun`, `PipelineBulkRunItem`
- `PipelineBulkRunStatus` — run `status`
- `PipelineBulkItemState` — item `state`, not `PipelineBulkItemStatus`

`PipelineBulkRunItem` fields: `submittedProductId`, `productId` (nullable),
`state`, `candidateVersionId` (nullable), `errorCode`, `errorMessage`,
`attemptCount`, `finishedAt`.

Timestamps stay `string` (ISO), matching the rest of `api.ts`. No `any`.

---

## 29. Error-code mapping

Branch on `ApiError.code`. Never branch on the envelope `message`.

Reason identity is a `details` entry with `type: "reason"` and `message` equal
to the stable reason (`stale_preview`, `draft_version_stale`,
`not_a_pipeline_candidate`, `pipeline_candidate_requires_approval`,
`candidate_not_approved`, `synthetic_publish_blocked`). A missing store is
`code: "not_found"` plus a `details` entry `type: "resource"` and
`message: "Store"`. That is not a code named `store_not_found`.

| Condition | Merchant copy | Action |
|---|---|---|
| 401 | Session expired | Existing auth redirect |
| 403 `permission_denied` | AI Studio is for owners and admins | Permission panel |
| 404 `not_found` | Product, run, or version not found | Not-found state. Do not say which other tenant owns it |
| 404 `not_found` with `details` `type: "resource"` and `message: "Store"` | That store is not connected | Reselect store. This is not a code named `store_not_found` |
| 422 `validation_error` | Show `details` field messages | Fix the form. Includes more than 50 ids if a client bug sends them |
| 409 reason `stale_preview` | This product changed after the preview | Generate fresh preview |
| 409 reason `draft_version_stale` | The draft changed after the preview | Generate fresh preview |
| 422 reason `not_a_pipeline_candidate` | This version is not a pipeline candidate | Legacy panel (section 26) |
| 422 reason `pipeline_candidate_requires_approval` | Approve this version in AI Studio | Link to the review route |
| 409 `pipeline_bulk_run_active` | Another bulk optimization is running | Section 23. This code is stable |
| 409 `conflict` without those reasons | Someone else updated this product or this run key | Reload. Do not blind-retry with a new key |
| `ai_provider_not_configured` / unavailable | AI is not configured for this workspace | No retry loop |
| reason `synthetic_publish_blocked` | Test previews cannot be published | None. Banner stays |
| reason `candidate_not_approved` | Approve this version before publishing | Focus approve. Also the normal blocker on a fresh preview |
| run `status: "failed"` with `failureReason` | Show `failureReason` as text on the run | Includes a store that disappeared during a bulk run. That is run state, not an HTTP code `store_not_found` |
| `internal_error` | Something went wrong. Reference `requestId` | No stack trace |

---

## 30. Responsive design

| Viewport | Behaviour |
|---|---|
| `lg` and up | Comparison side by side. Bulk items as a table |
| Below `lg` | Comparison stacked. Bulk selection and items as cards. Same breakpoint as the catalogue |
| Phone width | Sticky action bar: Approve, Publish, or Cancel. No horizontal scroll to reach them. Store and tone selectors are full width |

Touch targets use the existing `Button` sizes (`size="sm"` minimum on rows,
default on the sticky bar).

---

## 31. Accessibility

- One `h1` per page ("AI Studio", or the product title on review).
- Buttons have text names ("Approve version", "Publish approved version",
  "Cancel run", "Generate preview"). Icon-only controls get `aria-label`.
- Status badges include text, not colour alone.
- The run summary is an `aria-live="polite"` region. Polling updates that
  text. Focus does not move on poll.
- Dialogs use the existing dialog primitive (focus trap, Escape).
- After preview lands, focus moves to the candidate heading once. After
  approve, focus moves to the Approved banner once. Not on each refetch.
- `prefers-reduced-motion`: no extra animation beyond what Button/Skeleton
  already do. Progress is a numeric percent, not a required animation.
- Checkboxes are real inputs, not clickable divs.

---

## 32. Loading, empty, and error states

| State | UI |
|---|---|
| Studio first load | Skeleton list |
| No products on the open tab | Drafts: import. Published: nothing published yet. The other tab stays available |
| No candidate yet | Short explanation and Generate preview. Publish disabled |
| Generating | Button busy, live region "Generating preview" |
| Candidate ready | Comparison. Fresh candidate shows not approved. Publish disabled |
| Approving | Button busy |
| Approved, preview refresh in flight | Approved badge. Publish disabled |
| Approved, refreshed preview publishable | Publish enabled |
| Publishing | Button busy |
| Published | `ShopifyPublishResult` panel. No pipeline ids |
| Bulk idle | Drafts tab (default) and Published tab |
| Bulk pending / running | Counts and progress |
| Bulk empty items page | "No items on this page" |
| Partial / failed / completed / cancelled | Status word from section 20 |
| Any error | Section 29. `ErrorState` or `Alert`, with `requestId` in muted text |

---

## 33. Expected implementation files

Create when implementing (not in this planning change):

- `frontend/app/(app)/(protected)/ai-studio/page.tsx`
- `frontend/app/(app)/(protected)/ai-studio/products/[productId]/page.tsx`
- `frontend/components/ai-studio/` — home, selection, run dashboard, item
  list, review layout, comparison, quality, images, readiness, approve
  dialog, publish panel, banners
- `frontend/lib/ai-studio/` — token helpers, progress label, reason
  extraction, idempotency snapshot (pure functions, no React)
- `frontend/tests/e2e/ai-product-studio.spec.ts`
- `frontend/tests/e2e/ai-product-studio-bulk.spec.ts`

Edit:

- `frontend/services/products.ts`
- `frontend/types/api.ts`
- `frontend/lib/navigation.ts`
- `frontend/components/layout/sidebar-nav.tsx` (role-aware item)
- `frontend/components/products/product-table.tsx`
- `frontend/components/products/optimize-product-button.tsx` (remove from
  primary use, or replace the component)
- `frontend/components/products/product-version-history-sheet.tsx`
- `frontend/components/products/published-product-summary.tsx`
- `frontend/components/drafts/editor-header/product-actions-menu.tsx`
- `frontend/components/drafts/draft-product-editor.tsx` (stop passing
  optimize into the menu)
- Existing Playwright specs that assert "Optimize with AI" or "Improve with
  AI tools": `product-optimization.spec.ts`, `catalogue.spec.ts`,
  `editor-header.spec.ts`

No backend files. No migration.

---

## 34. Backend-gap assessment

**No backend change in Stage 10.**

| Gap | Class | Why it does not block |
|---|---|---|
| Version list cannot mark pipeline vs legacy | LOW | GET preview classifies before any Activate control is shown for `ai_generated` rows |
| 409 active-run has no run id, and there is no list endpoint | LOW | Same browser stores the id from the 202 it started. Another device gets an honest message and does not retry. A list endpoint would be a new contract and is out of scope |
| `ProductVersionRead` has no `isSynthetic` | LOW | Synthetic is on the pipeline preview payload, which is what the review screen renders |

Not required, and not proposed:

- widening `ProductVersionRead`
- `GET /pipeline/runs`
- a merchant retry-failed endpoint
- role changes
- removing `POST /optimize`

Stage 7, 8, and 9 semantics stay as merged.

---

## 35. Test matrix

Playwright, route-mocked, unless noted.

**Single product** (`ai-product-studio.spec.ts`):

- Admin opens `/ai-studio/products/{id}`
- POST `/products/{id}/pipeline/preview` body is `{ tone, storeId? }` only
- Response is flat: `candidateVersionId`, `candidateVersionNumber`,
  `candidateActive: false`. No nested `candidate`. No `source` field is read
- Side-by-side title and description from `original` and `proposal`
- Description fixture containing HTML-like text is rendered as text, not as
  live markup
- SEO and tags render from `proposal` and are labeled not sent to Shopify.
  Alt text renders from `imageAnalysis.images[].analysis.altTextProposal`,
  not from `proposal`
- POST preview fixture includes `publishable: false` and
  `pipelineBlockers` containing `candidate_not_approved`. Publish is disabled
- Approve is
  `POST /products/{id}/pipeline/versions/{candidateVersionId}/approve`
- Approve body is exactly `{ expectedUpdatedAt }` equal to T0
  (`approvalExpectedUpdatedAt`). The body has no `candidateVersionId`
- Approve response `updatedAt` is T1, different from T0
- Client then GETs
  `/products/{id}/pipeline/versions/{candidateVersionId}/preview?storeId=`
  for the selected store
- That GET fixture has `candidateActive: true`, `publishable: true`, and no
  `candidate_not_approved`. Publish becomes enabled
- If that GET fails, Publish stays disabled
- Publish is
  `POST /products/{id}/pipeline/versions/{candidateVersionId}/publish`
- Publish body is exactly `{ storeId, expectedUpdatedAt: T1 }`. No
  `candidateVersionId` in the body
- Publish fixture is `ShopifyPublishResult` (`listingId`,
  `externalProductId`, `adminUrl`, `storefrontUrl`, and the other existing
  fields). The test does not require pipeline version ids on it
- Changing the store GETs preview again with the new `storeId` and does not
  POST preview. The previous store's `publishable` is not what enables the
  button
- `stale_preview` keeps the candidate and disables approve until Generate
  fresh preview
- A failed approve transport, then a retry, sends T0 again on the version
  URL and adopts T1 from the 200 body, then GETs preview before publish
- Synthetic fixture: banner "Test AI preview", publish disabled; a forced
  publish error reason `synthetic_publish_blocked` matches the copy
- Unknown product: not-found
- GET preview 422 reason `not_a_pipeline_candidate` shows the legacy panel
  and Activate, not pipeline Approve
- Admin sees the nav item
- Member/viewer fixture: no nav item; direct URL shows the permission panel
  and does not call preview
- Quality labels say "Optimization score" and "Change vs original", and do
  not say "confidence"
- Quality fixture is the nested breakdown (`earned`, `applicableMax`,
  `dimensions.title`, `dimensions.description`, `dimensions.repetition`,
  `dimensions.keywordCoverage`, optional `seoFormat`). A dimension object is
  not rendered as `[object Object]`
- Image outer `status: "unknown"` or `analysis: null` renders "Not analyzed"
- `status: "succeeded"` shows checks and caption/alt proposals
- `status: "checksOnly"` shows checks and does not call the row a failure
- `status: "fetchFailed"` and `"decodeFailed"` render "Analysis unavailable"
  and `errorCode` when set
- An unrecognized image status does not throw
- Blocker `message` from the fixture is shown; the client does not add one

**Bulk** (`ai-product-studio-bulk.spec.ts`):

- Drafts tab is default and a draft row can be selected
- Published tab can be selected, and a published row can be selected in the
  same set
- The set survives a page change and a tab change
- The 51st check is refused. Count reads n / 50. Start body length is at
  most 50
- List requests use `page` and `size`, not `pageSize`
- Start returns 202
- Retry of the same confirm sends the same `idempotencyKey`
- Pending then running updates the live region from run `status`
- Interval effect: a terminal run `status` does not schedule another poll
  (assert no further run GET after the terminal response, within a bounded
  wait)
- Partial fixture shows Partial, not a success headline
- Item fixture field is `state`, not `status`
- `state: "succeeded"` with `productId` and `candidateVersionId` shows Open
  review. The href is
  `/ai-studio/products/{productId}?candidate={candidateVersionId}`
- `state: "failed"` does not show Open review
- `state: "missing"` with `productId: null` does not show Open review
- Cancel posts cancel and shows the cooperative copy
- 409 `pipeline_bulk_run_active` shows the banner and does not post again
- Mobile viewport: cards, sticky action reachable

**Live StubProvider** (only if the existing seed harness can create a
product without AliExpress OAuth, same as `product-optimization.spec.ts`):

- Preview 201, approve 200, publish attempt returns the synthetic block
- Optional: one bulk run of a single seeded id reaches a terminal summary

Skip the live block rather than calling a real model. No provider API key
is required.

Pure helpers (token pick, progress label, 50-cap, idempotency snapshot
equality) are covered by the Playwright assertions on request bodies and
visible labels. No new unit runner.

---

## 36. Regression matrix

| Existing spec | Must still pass | Expected edit |
|---|---|---|
| Draft editor concurrency and real conflict | Yes, untouched | None |
| Product pages and route isolation | Yes | None, unless a selector was the optimize button |
| `product-optimization.spec.ts` | Replaced by Studio coverage for the merchant path | Stop requiring "Optimize with AI" and the auto-activate badge as the primary path |
| `catalogue.spec.ts` optimize visibility | Button gone | Assert AI Studio link instead |
| `editor-header.spec.ts` "Improve with AI tools" | Menu item gone | Assert "Open AI Studio" |
| Version history | Activate remains for original snapshots | AI rows link to Studio |
| Shopify publish from the draft editor | Unchanged endpoint | None |
| Catalogue mobile cards | Still `lg:hidden` | None beyond the new Studio link |

---

## 37. Acceptance gates

Frontend:

```bash
cd frontend && npm run lint && npm run typecheck && npm run build
```

Playwright: the two new specs plus the regression specs above.

Backend: no source change, so no new backend tests and no pytest obligation
from this stage.

PR CI on the implementation PR: 10/10. Alembic head remains `0034`. No
migration `0035`.

---

## 38. Scope exclusions

- Implementing any of the files in section 33 in the planning PR
- Backend edits, including "small" read-model additions
- Deleting `POST /optimize`
- OpenAPI client generation
- WebSockets, SSE, or Celery `AsyncResult` in the browser
- A select-all-matching-filters bulk mode
- A merchant "retry failed items" action
- Auto-approve, auto-publish, auto-regenerate
- Embedding an editor in `PublishedProductSummary`
- An AI tab inside the draft form
- Stage 11
- Deployment, `origin/main`, production

---

## 39. Risks

| Risk | Mitigation in this plan |
|---|---|
| Merchant still one click from silent activation | Primary optimize controls removed |
| Activate on a pipeline row | Sheet has no Activate for `ai_generated`; GET preview classifies first |
| Publish sent with T0 | Approve stores T1 from `ProductDetail.updatedAt`. Publish body sends that T1. GET preview does not replace it |
| Lost T1 | Retry approve on the same version URL with T0; adopt `updatedAt` from the 200; then GET preview |
| Stale pre-approval `publishable: false` left on screen | Approve is followed by GET of that candidate. Publish waits on that GET |
| Readiness from the wrong store | Candidate query key includes `storeId`. Store change refetches GET and hides the previous readiness |
| Item `status` instead of `state` | Open review reads `item.state` |
| Drafts omitted from bulk | Home tabs use `useDrafts` and `useProducts` under one 50-cap set |
| Draft autosave clobbers or is clobbered by AI | Studio is a different route and does not write the editor cache |
| Score read as model confidence | Fixed labels in section 8 |
| HTML in model output | Candidate description is plain text, not `dangerouslySetInnerHTML` |
| Stub text treated as shippable | Test AI preview banner; publish disabled |
| Client-side readiness drift | Buttons follow `publishable` and server blockers |
| Wrong publish URL | `POST .../pipeline/versions/{versionId}/publish`. Body is `{ storeId, expectedUpdatedAt }` only |
| Idempotency key churn | Key tied to the confirm snapshot, not to each `fetch` |
| 51 ids | Checkbox cap before the request |
| Poll after terminal | `refetchInterval` returns false |
| Guessing the newest version | Link uses `candidateVersionId` |
| Viewer told the UI is the security boundary | Copy and tests say the API enforces 403 |

---

## 40. Accepted LOWs

1. **Cross-device active run.** Another browser that did not start the run
   cannot deep-link it after 409, because the API does not return the run id.
   The UI says so and does not retry. A list endpoint is not part of Stage 10.
2. **Version list has no pipeline flag.** Classification is a GET preview
   when the merchant opens the candidate, not a badge on every history row.
3. **No frontend unit runner.** Assertions live in Playwright. This stage
   does not add Vitest.
4. **Legacy hook may remain uncalled.** The backend route stays. Primary UI
   does not call it.
5. **Stage 9 completion doc** still mentions older pre-remediation test
   counts. Refreshing that doc is not part of Stage 10 planning or the
   Studio implementation.

No blocker, high, or medium items remain in this plan.

---

## 41. Stage 11 boundary

Stage 11 is not specified here and does not start. Studio does not add
scheduling, automatic re-optimization, supplier re-import, or a new provider
integration.

---

## 42. No deployment

This plan does not deploy. Implementation of Stage 10, when separately
authorized, also does not deploy unless a later instruction says so.
`origin/main` stays `3ce66d488e94ad3805fe24903deda99691c234a6`. Production
stays undeployed.

---

## 43. Claude checkpoint

CLAUDE RETURN REVIEW CHECKPOINT:
All commits from Stage 5 takeover onward require a fresh Claude
end-to-end review when Claude becomes available again.

---

## Self-review

Reviewed against the wire-contract remediation list.

| Attack | Result |
|---|---|
| Wrong approve URL | Closed: `POST /products/{productId}/pipeline/versions/{versionId}/approve` |
| `candidateVersionId` in the approve body | Closed: body is `{ expectedUpdatedAt: T0 }` only |
| Wrong publish URL | Closed: `POST /products/{productId}/pipeline/versions/{versionId}/publish` |
| `candidateVersionId` in the publish body | Closed: body is `{ storeId, expectedUpdatedAt: T1 }` only |
| Version ids expected on the publish response | Closed: `ShopifyPublishResult` only. Candidate id stays on the route |
| Stale pre-approve `publishable` used after approval | Closed: Publish waits on the follow-up GET |
| `candidateActive` never refreshed | Closed: that GET is required before Publish enables |
| Readiness for the wrong store | Closed: query key includes `storeId` |
| Store switch recomposes by POST preview | Closed: store switch is GET of the same candidate |
| `item.status` instead of `item.state` | Closed: `PipelineBulkItemState` |
| Successful bulk item never opens review | Closed: `state === "succeeded"` plus both ids, exact href |
| Nested `candidate` object | Closed: flat `candidateVersionId` and `candidateVersionNumber` |
| Invented `candidate.source` | Closed: the column is labeled AI candidate |
| Alt text read from `proposal` | Closed: `imageAnalysis.images[].analysis.altTextProposal` |
| Fictional image statuses | Closed: `succeeded`, `checksOnly`, `fetchFailed`, `decodeFailed`, `unknown` |
| Invented `failureReason` on an image | Closed: `errorCode` |
| Quality breakdown as a number map | Closed: `ProductVersionQualityBreakdownRead` |
| Published-only bulk catalogue | Closed: Drafts and Published tabs, one 50-cap set |
| `pageSize` sent on the wire | Closed: `page` and `size` |
| `store_not_found` as a Stage 8 code | Closed: `not_found` plus resource `Store`. Run failure uses `failureReason` |
| Legacy Activate regression | Closed: `original` rows still Activate. `ai_generated` goes to Studio |
| T0 reused for publish | Closed: section 11 |
| T1 lost | Closed: section 13, then GET preview |
| Stage 8/9 semantic change | Closed: no backend change |
| Backend scope creep | Closed: section 34 |
| Stage 11 or deployment creep | Closed: sections 41 and 42 |
| Legacy optimize still bypasses review | Closed: removed from primary UI; backend route kept |
| Auto approve / auto publish | Closed: both require a confirm |
| Polling after terminal, or Celery from the browser | Closed: GET run, interval stops |
| Guessing the newest candidate | Closed: `candidateVersionId` from the item or the route |

**BLOCKER 0. HIGH 0. MEDIUM 0.** LOWs are section 40 only.
