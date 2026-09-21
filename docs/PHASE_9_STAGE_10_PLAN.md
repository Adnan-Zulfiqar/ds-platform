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

1. Select up to 50 catalogue products.
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

There is no shared Tabs primitive. Studio does not invent one. Comparison is
a two-column layout, not a tab set inside the draft editor.

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
3. **Generate preview** calls `POST /products/{id}/pipeline/preview` with
   `{ tone, storeId? }`.
4. The page stores `approvalExpectedUpdatedAt` as token **T0** and renders
   the returned `PipelinePreview`.
5. Side-by-side comparison (section 7).
6. Quality, image evidence, and readiness render from that payload only.
7. **Approve this version** is a confirmed action. It means "make this exact
   candidate the active product version." It does not mean publish.
   Body: `{ candidateVersionId, expectedUpdatedAt: T0 }`.
8. On 200, replace the local token with `ProductDetail.updatedAt` (**T1**).
   Invalidate product detail, versions, and list queries. Do not write the
   candidate into the draft editor cache.
9. **Publish** stays disabled until the server says the approved version is
   publishable (`publishable` and `channelReadiness.canPublish`, and the
   version is active). Publish sends **T1**, never T0.
10. Success shows the Stage 8 `PipelinePublishResult` (reused Shopify publish
    fields plus pipeline version ids). Links use the existing trusted URL
    helpers.

If the merchant opens `?candidate=<id>` (from bulk, or from version history):

- `GET /products/{id}/pipeline/versions/{id}/preview?storeId=`
- 200: this is a pipeline candidate. Continue the review flow. T0 is
  `approvalExpectedUpdatedAt` from this GET.
- 422 with reason `not_a_pipeline_candidate`: this is a legacy AI version.
  Show it as legacy (section 26) with Activate available. Do not show the
  pipeline Approve button.

No step calls approve or publish by itself.

---

## 7. Side-by-side comparison

Desktop (`lg` and up): two columns, **Current** and **AI candidate**.

Below `lg`: the same blocks stacked, candidate first so the merchant sees
the proposal without scrolling past the whole current product. Approve and
publish stay in a sticky footer so they are reachable without horizontal
scroll.

Fields that are actually on `PipelinePreview` / `PipelineProposal`:

| Field | Current column | Candidate column |
|---|---|---|
| Title | `original.title` | `proposal.title` |
| Description | `original.description` shown as text via the existing `stripHtml` helper | `proposal.description` as plain text (`whitespace-pre-wrap`). It is stored plain text |
| Optimization score | `qualityBaseline` | `qualityScore` and `qualityDelta` |
| Breakdown | — | `qualityBreakdown` labels from the server keys |
| Version | active version number from product detail when present | `candidate.versionNumber` |
| Source | — | `candidate.source` (`ai_generated`) |
| Provider | — | `provider` |
| Synthetic | — | `isSynthetic` (section 16) |

SEO title, meta description, tags, and image alt text live on `proposal`.
The publish overlay does not write them. The UI labels that block **Proposal
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
| `qualityBaseline` | Previous optimization score |
| `qualityDelta` | Change vs original |
| `qualityScoreVersion` | shown as small meta ("Score version N"), not as a grade |
| `qualityBreakdown` | one row per returned key, using the server's name |

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

Per image (`PipelineImageEvidence`):

| Server state | UI |
|---|---|
| `status: analyzed` and `analysis` object | Show the structured notes the server returned |
| `status: unknown` or `analysis: null` | **Not analyzed**. This is not a failure |
| `status: failed` | **Analysis unavailable** plus `failureReason` when present |
| `synthetic: true` | Badge **Test caption** on that image's proposed alt text |

Product-level `status: unknown` with empty `images` is the same "Not
analyzed" state, not an error banner.

Proposed alt text is proposal copy (section 7), not a published alt.

---

## 10. Readiness UX

Readiness is server-authoritative. React does not re-implement blocker rules.

Render, when present:

- `channelReadiness.canPublish`
- `channelReadiness.blockers[]` and `recommendations[]` (`code`, `message`,
  `field`, `section`, `action`)
- `pipelineBlockers[]` and `pipelineWarnings[]`
- top-level `publishable`

A blocker card shows `message`, and `field` / `section` when set. `action`
may be mapped to an in-app link only when the code is one the Studio already
knows (`connect_shopify` → store settings, `approve_candidate` → the approve
control on this page). Unknown actions render as text.

Publish is enabled only when `publishable` is true, a store is selected, the
candidate is the active approved version, and the local token is T1. If the
server disagrees, the button stays disabled and the blocker list is the
explanation. The client does not invent a blocker the payload omitted.

---

## 11. T0 / T1 concurrency state machine

Held in React state on the review page only. Not in Zustand. Not in the
draft form.

```text
idle
  → preview 201
  → reviewing(T0 = approvalExpectedUpdatedAt, candidateVersionId)
  → approve 200
  → approved(T1 = response.updatedAt, candidateVersionId)
  → publish(expectedUpdatedAt = T1)
```

Rules:

- T0 is saved only from `approvalExpectedUpdatedAt` on the preview that is
  on screen (POST preview or GET preview).
- The approve request sends that T0.
- After approve 200, discard T0. The only publish token is
  `ProductDetail.updatedAt` from that response.
- A later refetch of product detail may refresh T1 if the merchant has not
  started a newer preview. It must not put T0 back.
- Starting a new preview replaces both the candidate and the token with the
  new response's T0.
- Publish never reads `approvalExpectedUpdatedAt`.

`expectedUpdatedAt` on approve and publish is the ISO timestamp string the
API returned, passed through unchanged.

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
- The client stores that `updatedAt` as T1 and continues to publish.

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

- Badge **Approved** on the candidate.
- Product queries invalidated (section 27).
- T1 stored.
- Publish stays subject to server readiness.
- No Shopify request has been made. Copy under the badge says so.

Approve is hidden when the candidate is already active (GET preview of the
approved version). Publish is the next step, still manual.

---

## 15. Publish UX

Endpoint: `POST /products/{id}/pipeline/publish`.

Body: `{ candidateVersionId, storeId, expectedUpdatedAt: T1 }`.

Not `POST /integrations/shopify/publish`. Stage 8 publish already calls the
existing publisher.

UI:

- Store selector from connected stores. Required.
- Readiness cards from section 10.
- Button **Publish approved version**, disabled until section 10's conditions
  hold.
- In-flight label **Publishing…**. One in-flight request; the button ignores
  a second click.
- Success: status, external id, and admin/storefront links from the result,
  through existing URL helpers only.
- Failure: `ApiError` mapping in section 29. The candidate stays approved.
  The merchant can retry publish with the same T1 unless a 409 says the
  product changed, in which case they reload the product and the token
  updates from the refetched `updatedAt` only after a successful refetch
  confirms the approved version is still active.

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

Studio home has its own catalogue list (same product query, page size 20):

- One checkbox per row, and per card below `lg`.
- **Select page** selects only the ids on the current page that fit under
  the cap.
- Selection is a `Set` of product ids in component state, so it survives
  pagination within the visit.
- Header shows **n / 50 selected** and **Clear**.
- At 50, further checkboxes are disabled. Copy: "You can optimize up to 50
  products at a time."
- The start request sends exactly the selected ids. It never slices a larger
  set down to 50 without telling the merchant, because the UI never lets the
  set exceed 50.

There is no "select all products matching filters". Stage 9 accepts an
explicit id list only.

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

`GET /products/pipeline/runs/{runId}/items` with the existing page params
(`page`, `pageSize` max 100, default 20). The UI does not request every item
in a loop.

Each row (card below `lg`): product id (title from the product cache when
that id was in the selection list; otherwise the id), item `status`,
`attemptCount`, and `errorCode` / `errorMessage` when failed. Those strings
are the API's safe codes, shown as text.

**Open review** renders only when `status === succeeded` and
`candidateVersionId` is set. Link:

`/ai-studio/products/{productId}?candidate={candidateVersionId}`

using `productId` from the item. The review page loads that version. It does
not pick the newest `ai_generated` row from version history.

---

## 22. Bulk cancellation

**Cancel run** on a pending or running run. Confirm: "Stop this run? The
product already being optimized may still finish. Cancellation is not
instant."

`POST /products/pipeline/runs/{runId}/cancel`.

Then refetch the summary on the same query key. Item rows update from the
server. The client does not mark leftover pending rows as skipped.

`cancelled` is terminal. Polling stops. Historical pending rows may still
say pending; the run status is the authority. Copy says that.

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
| Bulk item | Open review | `/ai-studio/products/{productId}?candidate={candidateVersionId}` |
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
productKeys.pipelinePreview(productId, versionId | "new")
productKeys.pipelineRun(runId)
productKeys.pipelineRunItems(runId, query)
```

| Event | Invalidate |
|---|---|
| Preview 201 | that preview key only (the response is the cache) |
| Approve 200 | `productKeys.detail(id)`, `versions(id)`, `productKeys.lists()`, `draftKeys.detail(id)` |
| Publish 200 | detail, versions, lists, draft detail, draft listings / Shopify status keys already used by the editor publish mutation |
| Bulk 202 | `pipelineRun(runId)` and items |
| Cancel 200 | `pipelineRun(runId)` |

Do not `invalidateQueries()` with no key. Do not put candidate title or
description into the draft editor's form state.

Hooks to add beside the existing product hooks:

- `usePipelinePreview` mutation
- `usePipelineCandidate` query (GET preview)
- `useApprovePipelineCandidate` mutation
- `usePublishPipelineCandidate` mutation
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

Families:

- `PipelineTone`
- `PipelinePreviewRequest`, `PipelinePreview` (response)
- `PipelineProposal`, `PipelineVersionSummary`
- `PipelineImageAnalysis`, `PipelineImageEvidence`
- `PipelineQualityBreakdown` (record of string → number, values as returned)
- `PipelineReadiness`, `PipelineReadinessIssue`
- `PipelineApproveRequest`
- `PipelinePublishRequest`, `PipelinePublishResult`
- `PipelineBulkRunCreate`, `PipelineBulkRun`, `PipelineBulkRunItem`
- `PipelineBulkRunStatus`, `PipelineBulkItemStatus`

Timestamps stay `string` (ISO), matching the rest of `api.ts`.

---

## 29. Error-code mapping

Branch on `ApiError.code` and, for `conflict` / `validation_error`, on
`details[].type === "reason"` and `details[].message` equal to the stable
reason. Never branch on the human `message` of the envelope.

| Condition | Merchant copy | Action |
|---|---|---|
| 401 | Session expired | Existing auth redirect |
| 403 `permission_denied` | AI Studio is for owners and admins | Permission panel |
| 404 `not_found` | Product or run not found | Not-found state. Do not say which other tenant owns it |
| 422 `validation_error` | Show `details` field messages | Fix the form. Includes more than 50 ids if a client bug sends them |
| 409 reason `stale_preview` | This product changed after the preview | Generate fresh preview |
| 409 reason `draft_version_stale` | The draft changed after the preview | Generate fresh preview |
| 409 `pipeline_bulk_run_active` | Another bulk optimization is running | Section 23 |
| 409 `conflict` without those reasons | Someone else updated this product or this run key | Reload. Do not blind-retry with a new key |
| `ai_provider_not_configured` / unavailable | AI is not configured for this workspace | No retry loop |
| reason `synthetic_publish_blocked` | Test previews cannot be published | None. Banner stays |
| reason `candidate_not_approved` | Approve this version before publishing | Focus approve |
| `store_not_found` | That store is not connected | Reselect store |
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
| No products | Existing empty pattern: import or open drafts |
| No candidate yet | Short explanation and Generate preview |
| Generating | Button busy, live region "Generating preview" |
| Candidate ready | Comparison |
| Approving / Publishing | Button busy |
| Approved | Badge plus publish panel |
| Published | Result panel |
| Bulk idle | Selection list |
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
- Generate preview posts tone and optional store
- Side-by-side title and description from the fixture
- Description fixture containing HTML-like text is rendered as text, not as
  live markup
- Approve request body `expectedUpdatedAt` equals the preview's
  `approvalExpectedUpdatedAt` (T0)
- Approve response `updatedAt` (T1, different from T0) is what publish sends
- `stale_preview` keeps the candidate and disables approve until Generate
  fresh preview
- A failed approve transport, then a retry, sends T0 again and adopts T1
  from the 200 body
- Synthetic fixture: banner "Test AI preview", publish disabled; a forced
  publish error `synthetic_publish_blocked` matches the copy
- Unknown product: not-found
- Admin sees the nav item
- Member/viewer fixture: no nav item; direct URL shows the permission panel
  and does not call preview
- Quality labels say "Optimization score" and "Change vs original", and do
  not say "confidence"
- Image `status: unknown` renders "Not analyzed"
- Blocker `message` from the fixture is shown; the client does not add one

**Bulk** (`ai-product-studio-bulk.spec.ts`):

- Select rows, count visible, cannot check a 51st
- Start returns 202; body length ≤ 50
- Retry of the same confirm sends the same `idempotencyKey`
- Pending then running updates the live region
- Interval effect: a terminal status does not schedule another poll (assert
  no further run GET after the terminal response, within a bounded wait)
- Partial fixture shows Partial, not a success headline
- Failed and missing rows show `errorCode`
- Open review href contains the fixture `candidateVersionId`
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
| Publish sent with T0 | Token state drops T0 on approve 200; publish reads T1 only |
| Lost T1 | Retry approve; adopt `updatedAt` from the 200 |
| Draft autosave clobbers or is clobbered by AI | Studio is a different route and does not write the editor cache |
| Score read as model confidence | Fixed labels in section 8 |
| HTML in model output | Candidate description is plain text, not `dangerouslySetInnerHTML` |
| Stub text treated as shippable | Test AI preview banner; publish disabled |
| Client-side readiness drift | Buttons follow `publishable` and server blockers |
| Wrong publish URL | Only the pipeline publish hook |
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

Reviewed against the failure list in the Stage 10 planning brief.

| Attack | Result |
|---|---|
| Legacy optimize still bypasses review | Closed: removed from primary UI; backend route kept |
| Pipeline candidate shows Activate | Closed: no Activate on `ai_generated` rows; GET preview splits legacy vs pipeline |
| Publish uses T0 | Closed: section 11 |
| T1 lost after approve | Closed: section 13 |
| Merchant edits overwritten | Closed: no shared form state; stale preview does not auto-write |
| Quality score called confidence | Closed: section 8 |
| Unsafe HTML | Closed: plain text for proposal description |
| Synthetic shown as production AI | Closed: section 16 |
| Client readiness rules | Closed: section 10 |
| Wrong publish endpoint | Closed: pipeline publish only |
| Auto approve / auto publish | Closed: both require a confirm |
| New idempotency key on HTTP retry | Closed: section 19 |
| More than 50 ids | Closed: section 18 |
| Polling after terminal | Closed: section 20 |
| Celery from the browser | Closed: GET run only |
| Guessing newest candidate | Closed: `candidateVersionId` |
| Foreign resource leak | Closed: 404/403 copy |
| Viewer/Member treated as authorized because the button is hidden | Closed: section 17 states the API is the control |
| Second editor | Closed: one review route |
| Mobile catalogue regression | Closed: Products table selection unchanged |
| Stage 8/9 redesign | Closed: no backend change |
| Stage 11 or deploy creep | Closed: sections 41 and 42 |

**BLOCKER 0. HIGH 0. MEDIUM 0.** LOWs are section 40 only.
