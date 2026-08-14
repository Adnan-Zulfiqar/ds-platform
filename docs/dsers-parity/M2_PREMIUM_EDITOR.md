# M2A — Premium Product Editor Foundation

Status: **Delivered, acceptance-fix pass applied, data-loss recovery fix
applied.** Branch: `feature/dsers-parity-m2a-editor-foundation`. Audit date:
2026-08-12. Acceptance-fix pass: 2026-08-13. Data-loss recovery fix:
2026-08-14.

## Data-loss recovery fix (2026-08-14)

The controlled-integration pass blocked M2A on a defect the entire test
suite was blind to: against a **real** conflict, clicking "Review my
changes" silently destroyed the merchant's unsaved work. This section
records the cause, the redesign, and why the tests missed it.

### What went wrong

`handleOpenReview()` called `refetch()`. The refetch returned genuinely
different data (someone really had saved a new version), so React Query
produced a **new `data` object reference**, which fired this effect:

```tsx
useEffect(() => {
  if (!data) return;
  applyDraftToForm(data);      // overwrote every merchant field
  setConflictPhase("none");    // closed the conflict
}, [data]);
```

The merchant clicked a button labelled "Review my changes" and the editor
replaced their changes with the server's, cleared the conflict, and
displayed "All changes saved".

### Why the tests were green

The existing suite fakes its 409 by intercepting `PATCH` and fulfilling a
synthetic conflict. **The underlying row never changes.** A refetch
therefore returns byte-identical JSON, React Query's structural sharing
preserves the object reference, `[data]` never re-fires, and the
destructive branch is never executed. All 32 tests passed against the
build that lost data.

> **Structural sharing is a rendering optimisation, not a safety
> property.** It says nothing about whether the merchant has unsaved work.
> Any invariant that holds only because a reference happened not to change
> is not an invariant — it is a coincidence with good uptime. Code that
> depends on it is untested by construction, because the mock that keeps
> the reference stable is the same mock that hides the bug.

### The hydration invariant

Server data may overwrite the form **only** at an explicitly enumerated
transition, never because a query result changed:

1. the first successful load of a draft (there is nothing to lose yet);
2. a confirmed "Reload latest version" (the merchant chose to discard).

Enforced by `hydratedFromRef`, a ref holding the draft id the form was
built from. The `[data]` effect returns early for every subsequent change
to the same draft — including the refetch `useUpdateDraft` triggers after
*every* successful save, which was silently re-hydrating the form and
refreshing the concurrency token before this fix. Adding a third
transition requires answering "is there unsaved work?" first, in code, at
that call site.

### Separated state

| State | Holds | Written by |
|---|---|---|
| `data` (React Query) | latest server draft | the cache |
| form fields | merchant's live values | typing; the two safe transitions |
| `savedUpdatedAt` | active concurrency token | first load, reload, save response |
| `conflictServerSnapshot` | server's side of the collision | `enterConflict` |
| `conflictLocalSnapshot` | merchant's side, frozen at the 409 | `enterConflict` |
| `conflictStaleToken` | the token the server rejected | `enterConflict` |

The two conflict snapshots are captured together when the 409 lands, and
the comparison renders from **both snapshots** — never from the live form
or the live query — so it always shows the two versions that actually
collided. `conflictLocalSnapshot` is typed as `EditableSnapshot` (plain
strings), deliberately *not* `ProductDetail`: keeping the merchant's side
and the server's side structurally different types makes "apply the server
version to the form" a compile error instead of a data-loss bug.

`enterConflict` fetches the server snapshot through `apiClient` directly
rather than `refetch()`, so inspecting the server's version cannot write to
the query cache at all. Review no longer refetches anything.

### Behaviour now

- **Review** is inert: no field written, `dirty` untouched, conflict left
  open, token unchanged, no request. Shows both versions with a provenance
  line naming the two timestamps.
- **Save my version anyway** sends exactly one guarded PATCH asserting the
  *reviewed* token. Success establishes the new baseline (token + dirty
  only — the fields already hold what was sent). A second 409 re-enters
  conflict resolution against the newer server version; it never retries.
- **Reload latest version** still requires explicit confirmation; Cancel
  preserves local edits; confirming is the one path allowed to discard.
- Save state now reports **"Conflict detected"** rather than the generic
  "Save failed". The indicator had rendered this state since the
  acceptance pass, but the editor kept its own copy of the `SaveState`
  union that omitted `"conflict"`, so nothing could set it. The duplicated
  type is gone — the editor imports the indicator's.

### Real-409 regression coverage

`frontend/tests/e2e/draft-editor-real-conflict.spec.ts` (new, 9 tests × 2
projects) mocks nothing. An independent API client saves a real new version
between the editor's load and its save, so the 409 comes from the backend's
own compare-and-swap and the refetched draft really is materially
different. Covers: local values preserved through the 409; review showing
both versions with input untouched; no PATCH while reviewing; closing
review preserving both; one-PATCH override on the reviewed token that
actually persists; a second real change mid-review producing another 409
with values still intact; reload cancel/confirm; a background
`invalidateQueries` during a conflict not hydrating the form; keyboard-only
resolution; and 375px without horizontal overflow.

Red/green evidence is recorded in the delivery report: against `0a65975`
the suite fails at the review assertion with the dialog never rendering
(`element(s) not found`), which is the defect exactly; after the fix, 18/18
pass across both projects.

## Acceptance-fix pass (2026-08-13)

The initial M2A delivery (below) had two acceptance gaps, closed in this
pass without touching anything else about the milestone's scope:

### Gap 1 — `expectedUpdatedAt` was bypassable

The original mechanism made the version token **optional**, so a draft
editor save that omitted it fell back to unconditional last-write-wins —
exactly the hazard optimistic concurrency exists to prevent, just reachable
by leaving a field off the request instead of by a race.

**Fix:** `ProductService.update_draft` now raises `ValidationError` (422,
`"expectedUpdatedAt is required to save a draft..."`) before doing anything
else — checked ahead of the tenant/existence lookup, so the response is
identical whether the draft exists, belongs to another tenant, or doesn't
exist at all (no existence leak via a different status code for a missing
token). `PATCH /api/v1/drafts/{id}` is the only endpoint this is mandatory
on; every draft-editing sub-route (`/images`, `/images/reorder`,
`/images/{id}`, `/variants/{id}`) goes through `ProductService`'s other
methods, none of which touch `title`/`description`/etc., so there is no
alternate route that can smuggle a title change past the guard — regression
tests assert this explicitly (`TestNoAlternateDraftRouteBypassesTheGuard`).

**Deliberate scope decision:** `PATCH /products/{id}` (the general,
pre-existing "edit any imported product" endpoint) still leaves
`expectedUpdatedAt` optional. This was audited, not overlooked: zero
frontend callers exist (`frontend/services/products.ts` only ever `GET`s
that route), the endpoint's own docstring and its pre-existing test suite
(`test_product_update.py`) establish this was intentional, pre-M2A design,
and restricting it would break established, tested behaviour to close a
"bypass" that grants no privilege escalation — the same tenant-scoped admin
auth is required either way, and the acceptance brief's own scope was the
draft editor path specifically.

### Gap 2 — the prior conflict-resolution UX could still let autosave overwrite

The original "Keep my changes" recovery action silently refreshed the
version token and left autosave free to fire again immediately — a
follow-up save (autosave or manual) could land moments later with no review
of what it was overwriting, which is not meaningfully different from the
silent-overwrite bug optimistic concurrency exists to prevent.

**Fix — replaced with two explicit, safe paths, neither of which resumes
autosave or saves anything without a second, separate merchant action:**

- **Reload latest version** — now two steps, not one: clicking it opens a
  confirmation dialog (`conflict-reload-confirm-dialog`) that states local
  edits will be discarded; only the explicit "Discard my changes and
  reload" button in that dialog actually replaces the form with the
  server's current values. Cancelling leaves the local edit and the
  conflict banner exactly as they were.
- **Review my changes** (replaces "Keep my changes") — fetches the latest
  server version into a *separate* `conflictServerSnapshot`, shown side by
  side with the merchant's still-untouched local fields
  (`conflict-review-dialog`), listing only the fields that actually differ.
  Nothing is auto-merged and no field is auto-selected. A second, distinct
  action — **"Save my version anyway"** — is required to overwrite the
  server's version wholesale with the merchant's; it asserts against the
  *reviewed* snapshot's token, so if the row moved again while the review
  was open, the save is rejected with a fresh 409 and the merchant returns
  to the conflict banner rather than the save silently retrying.
- Autosave's debounce effect now checks `isConflicted` for **every** phase
  (`detected` / `reload-confirm` / `reviewing`), not only whether the top
  banner is showing — it stays frozen through the entire resolution flow,
  confirmed by a dedicated regression test that keeps typing during an open
  conflict and asserts no second `PATCH` fires.

State machine: `conflictPhase: "none" | "detected" | "reload-confirm" |
"reviewing"` replaces the earlier boolean `conflict` flag
(`draft-product-editor.tsx`).

### Testing added this pass

**Backend** (`test_draft_editor_concurrency.py`, extended) — missing token
(422), malformed token (422), the same 404 for a foreign draft regardless of
token, a repeated save with the same stale token rejected every time (not
just once), and the no-alternate-route-bypass suite above. Combined
concurrency-adjacent suite: **51 passed**.

**Frontend** (`draft-editor-concurrency.spec.ts`, rewritten) — 16 scenarios
covering every required case: successful save, 409 shows Reload+Review (not
the old Keep-mine), no autosave after 409, Reload requires confirmation and
Cancel preserves state, confirmed Reload discards+adopts the server value,
Review shows both versions without touching local fields, Back from Review
returns to the banner untouched, "Save my version anyway" performs the
explicit overwrite, a second conflict during Review returns to the banner
(not a silent save), reaching Reload from inside Review, a double-click
Save guard, `role=alert` announcement, the Radix dialog's accessible
title/description, a keyboard-only resolution path, Escape closing the
review dialog, and no console errors through the full cycle. Final verified
result, both projects in one invocation (`--workers=1`) once the
environment issues below were fixed: **32/32 passing** (16 `chromium` + 16
`mobile-chrome`) — see "Environment characteristics" below for why
`--workers=1` and the full diagnostic history, and the final acceptance
report for the complete re-run log.

### Environment characteristics found and worked around (not app defects)

Diagnosed with the same discipline as any other failure — reproduced,
re-run individually, root-caused before deciding how to respond — three
separate issues surfaced purely from *how this test file exercises a real
backend*, none of which are defects in the app:

1. **Login throttle.** The file originally logged in fresh per test;
   16+ real logins to one account within ~2 minutes tripped this repo's own
   `SECURITY_LOGIN_MAX_ATTEMPTS=5`-per-300s control. Fixed by sharing one
   authenticated session across the file (`test.describe.configure({ mode:
   "serial" })`), which is also the more realistic test design, not a
   workaround.
2. **API rate limit.** A hard `page.goto()` before every test refetches the
   draft, SEO score, listings, pricing, and version history from a cold
   cache; 16 of those back to back cleared this backend's own per-tenant
   `security.rate_limit_requests` (100/60s), surfaced on screen as "Rate
   limit exceeded. Please retry later." Neither the login throttle nor the
   rate limit was loosened for test convenience — both are legitimate
   controls working as designed against a request pattern no real merchant
   produces. Fixed by pacing the test file itself (a deliberate gap between
   tests spreads the same request volume across more than one fixed 60s
   window).
3. **Chromium instability under sustained/parallel use.** On this
   development machine specifically, a single browser tab surviving all 16
   hard reloads — and, separately, two Playwright projects (`chromium`,
   `mobile-chrome`) launching browsers in parallel — intermittently crashed
   the renderer process outright (`browser.newPage: Test ended`), at a
   different test each time, with the isolated app processes' PIDs
   unaffected throughout every occurrence. Mitigated with proactive browser
   rotation (a fresh, independently-launched Chromium instance every 5
   tests) and reactive recovery (a crashed session is replaced with a new
   one, not retried in place) — and, pragmatically, by running each project
   serially rather than relying on the two-project default, which is also
   documented as this file's supported invocation going forward.

**A fourth issue, this one self-inflicted rather than found — stated plainly
per the honesty requirement to correct one's own errors, not just the
app's:** running `npm run build` (for the Gap 3 frontend quality gate)
against the same `.next` directory an already-running `next dev` server
(the isolated verification frontend) was using corrupted that server's
static-asset serving for every route not already warm in its in-memory
module cache — `/login` and `/register` (statically prerendered) started
404ing on every JS chunk with an HTML body instead, silently breaking all
client-side interactivity there, while `/drafts/{id}` (already hit
repeatedly, and dynamically rendered) kept working, which is why this went
unnoticed through all of Gap 1/Gap 2's own verification. Manifested during
the Gap 3 full-suite regression pass as `auth.spec.ts` failing every test
requiring a client-side validation message (11/19), all with the exact same
signature. Root-caused by inspecting the browser console directly (not by
guessing) rather than accepted as "pre-existing" — an earlier draft of this
report incorrectly concluded exactly that before this was found, and is
corrected here rather than silently fixed. **Fix:** stop the frontend
process, delete `.next`, restart `next dev` clean. A related, separately
self-inflicted issue surfaced at the same time: restarting the isolated
*backend* (see item 2's follow-up below) without re-specifying
`CORS_ORIGINS` dropped it back to the framework default
(`http://localhost:3000`, `http://localhost`), which does not include
`http://127.0.0.1:3010` — every authenticated request failed as a CORS
preflight rejection until the restart was redone with `CORS_ORIGINS`
explicitly set. Re-verified clean afterward: `auth.spec.ts` **19/19**.

**Gap 3 addendum on item 2 (API rate limit):** for the *full* Playwright
suite (all 12 spec files, not just this one), pacing alone was not the
right fix — the project's own CI config (`.github/workflows/ci.yml`,
`frontend-e2e` job) already documents and uses
`SECURITY_RATE_LIMIT_REQUESTS=1000` specifically for full-suite e2e runs,
rate limiting itself left **enabled**. That is not a security control being
loosened for convenience; it is this repository's own established,
documented tuning for exactly this scenario, applied to the isolated
backend the same way CI already applies it. The per-file pacing this file
adds stays in place regardless (harmless overhead under the higher limit,
and still correct if it is ever run against the production-tuned default).

**Two more self-inflicted config gaps found restarting the backend for the
rate-limit fix, same pattern as CORS above:** `SECURITY_ENCRYPTION_KEYS`
(credential-at-rest encryption) and `SHOPIFY_FRONTEND_RETURN_URL` /
`ALIEXPRESS_FRONTEND_RETURN_URL` (where the backend redirects the browser
after an OAuth-style callback) were also present on the isolated backend's
original process from an earlier session and were not carried over on
restart. The missing encryption key surfaced as `integrations.spec.ts`
failing with `encryption_not_configured`; the missing return-URL setting is
more serious — it defaults to `http://localhost:3000/settings/integrations`
(this repository's own production-default convenience default), and one
`shopify-oauth.spec.ts` test genuinely navigated the browser to
`localhost:3000` — the protected, must-not-touch original process — as a
result, before this was caught and fixed (confirmed via `curl`, not another
navigation, that the redirect now correctly targets `127.0.0.1:3010`
before re-running that test). Both fixed using the same CI-documented test
values (`.github/workflows/ci.yml`, `frontend-e2e` job) rather than
improvised ones. **Remaining, not fixed:** `ALIEXPRESS_APP_KEY` /
`ALIEXPRESS_APP_SECRET` and `SHOPIFY_API_KEY` / `SHOPIFY_API_SECRET` — real
third-party OAuth app credentials, explicitly documented as blank-by-default
in `.env.example` ("Set the real values in `.env`, which is gitignored.
Never commit them.") and, notably, **not set even by this repository's own
CI `frontend-e2e` job** — this isolated worktree's backend has never had
them configured. Seven of the eight failures in the full-suite run below
trace to this one pre-existing gap (AliExpress/Shopify *connect* flows
specifically); fabricating placeholder values for real third-party app
credentials would misrepresent the environment's integration-readiness, so
this was left as a documented gap rather than "fixed."

## Scope, as briefed vs. as actually needed

M2A was briefed as building "the safe foundation of DropPilot's premium
product editor" on top of M1: a dedicated editor route, title and plain-text
description editing, a save-state UI, and optimistic-concurrency protection —
with image/variant/pricing/SEO editing explicitly deferred to M2B–E.

The mandatory pre-implementation audit (reading the actual repository before
writing any code, per this milestone's own instructions) found that
**almost none of that foundation needed building** — it already existed,
shipped, and working, via work that predates the M1 dsers-parity branch
entirely.

## What already existed (audit finding, not built this pass)

Verified directly against the running code, not against other documentation's
claims about it:

- **A dedicated, fully-built premium editor route.**
  `frontend/app/(protected)/drafts/[productId]/page.tsx` →
  `DraftProductEditor` (`frontend/components/drafts/draft-product-editor.tsx`).
- **A three-layer premium header**
  (`frontend/components/drafts/editor-header/*`): breadcrumb, identity,
  save-state indicator, Publish action, supplier-sync status, a More-actions
  menu, and a responsive mobile action bar. Documented in
  `docs/PREMIUM_PRODUCT_EDITOR_UI.md`.
- **Tabs for Overview, Description, Media, Variants, Pricing, Inventory,
  Shipping, and SEO**, each backed by its own panel component and, for the
  write paths, its own backend endpoint.
- **The write path.** `PATCH /drafts/{id}` → `ProductService.update_product`
  already edited title, sanitized description, brand, category, vendor, tags,
  every SEO field, slug, status, and shipping/customs fields — far beyond
  "title and plain description."
- **Supplier-snapshot protection.** `supplier_title` / `supplier_brand` /
  `supplier_description` twin columns and `ProductImportService._SYNCED_FIELDS`
  divergence tracking (built in earlier Product Editor stages, reused
  unchanged here) already ensured a merchant edit is never silently reverted
  by the next supplier sync.
- **Autosave, unsaved-changes state, and a `beforeunload` guard** were already
  wired in `draft-product-editor.tsx` before this milestone touched it.
- **Migrations `0013`–`0022`** already back all of the above on `develop`.

This was built across three prior branches — `cursor/draft-product-editor`,
`cursor/premium-product-editor`, `cursor/product-workspace-v2` — merged to
`develop` **before** the M1 dsers-parity branch even started.
`docs/dsers-parity/FEATURE_MATRIX.md` §3 had never been re-audited against
this and understated it as a "basic safe draft view"; it has been corrected
in the same change as this document.

**Consequence for scope:** per this repository's own rule ("if equivalent
functionality already exists, reuse and harden it — do not create duplicate
routes or components") and per `CLAUDE.md` §12 ("never duplicate existing
functionality"), M2A was re-scoped, with the user's explicit sign-off, from
"build a new minimal editor" to **hardening the existing one** against the
two genuine gaps the audit did find.

## What this milestone actually built

Two gaps, confirmed by reading `ProductService.update_product`'s body before
this change: it fetched the row, applied every field from the request with
`setattr`, and flushed — with no version check and no state/publication
check of any kind.

### 1. Optimistic concurrency

**Mechanism.** Every tenant-scoped table already carries a database-generated
`updated_at` (`TimestampMixin`, `onupdate=func.now()`). Rather than add a
dedicated version/revision column, M2A reuses it as the version token:

- The client echoes back the `updatedAt` it last loaded as
  `expectedUpdatedAt` on save (`ProductUpdatePayload.expected_updated_at` /
  `ProductRead.updated_at`, both new fields — additive, not breaking).
- `TenantScopedRepository.update_if_unmodified_since` (new, in
  `app/repositories/base.py` — generic, not Product-specific, so any future
  tenant-scoped table gets the same primitive for free) issues a single
  guarded `UPDATE ... WHERE id = :id AND tenant_id = :tenant AND updated_at
  = :expected`, explicitly setting `updated_at = func.now()` in the same
  statement. If zero rows match, the row moved since the caller last saw it.
- `ProductService.update_product` treats a zero-row result as a
  `ConflictError` (409), never as a silent no-op or a silent overwrite.
- `expectedUpdatedAt` is **optional on the shared schema**, so
  `/products/{id}` callers that haven't adopted it keep the exact previous
  last-write-wins behaviour — but **mandatory specifically on the draft
  editor's own save path** as of the acceptance-fix pass (see above):
  `ProductService.update_draft` rejects a request that omits it with a 422
  before it can fall back to unguarded last-write-wins. The premium draft
  editor's frontend always sends it regardless, so this closes what had
  been a latent bypass (omit the field, get the old unguarded behaviour)
  rather than changing its own normal save path's behaviour.

**No-op protection.** Before any write is attempted,
`changes` is diffed against the currently-stored row
(`effective_changes`). A save whose values are already identical to what's
stored returns immediately without issuing any `UPDATE` — issuing one
anyway would still bump `updated_at` (Postgres fires `onupdate` for any
UPDATE regardless of whether values differ), which would misrepresent "last
edited" and could spuriously invalidate a concurrent editor's still-valid
version token for a save that changed nothing.

**A genuine bug found and fixed during testing:** the guarded UPDATE
initially relied on the `updated_at` column's `onupdate=func.now()` firing
implicitly, matching how the ORM's normal `flush()` path already behaves.
It does not fire reliably for this Core-style bulk-UPDATE statement, which
silently defeated the entire mechanism — a "stale" second write would find
`updated_at` unmoved and succeed. Fixed by setting `updated_at=func.now()`
explicitly in the statement's `.values()`, so the version bump is a
guaranteed part of the exact statement, not a hoped-for side effect of a
column default. Caught by an integration test before this branch was pushed
anywhere; see Testing below for how the test itself required a second fix.

**Frontend.** `draft-product-editor.tsx` tracks `savedUpdatedAt` (the last
server-confirmed version) and sends it on every save. On a 409:

- The generic `formError` alert is **not** shown — a conflict gets its own
  banner (`data-testid="draft-conflict-banner"`), because "reload or review
  your text" is a different recovery action than "fix a field and retry."
- **As of the acceptance-fix pass** (superseding the description that
  originally followed here — see "Acceptance-fix pass" above for the full
  account): **Reload latest version** now requires an explicit second
  confirmation before discarding local edits, and **Review my changes**
  (replacing "Keep my changes") shows the server's version and the
  merchant's version side by side and requires its own explicit "Save my
  version anyway" action before overwriting anything. Neither path silently
  refreshes the version token and leaves autosave free to fire again — that
  was the acceptance gap this pass closed.
- Autosave is frozen for every phase of conflict resolution, not only while
  the top banner is showing, and `handleSave` itself guards against firing
  a second concurrent request while one is already in flight or a conflict
  is unresolved.
- Merging is never attempted automatically. Concurrency is enforced entirely
  by the guarded database statement, not by any frontend state — the
  frontend only decides what to show, never what to accept.

**Limitation, stated plainly:** the mechanism cannot distinguish "another
editor changed this row" from "a background supplier sync refreshed it" —
both bump `updated_at` identically. This is the intended, conservative
behaviour (any concurrent write invalidates a stale save) rather than a
gap, but it does mean a save can occasionally be rejected by a routine
re-sync rather than only by a true editor-vs-editor conflict. Given imports
in this codebase are synchronous, request-scoped operations (see below), a
sync that races an in-progress edit is a narrow window in practice.

### 2. Draft-state / role edit-gating

**What was checked and is real:** a product that already has a synced
`StoreListing` (i.e., has been published to a channel, and by the existing
Drafts/Products projection rule is no longer shown in Drafts at all) could
still be edited by direct navigation to `/drafts/{id}`, since `update_draft`
had no publication check. `ProductService.update_draft` (new; wraps
`update_product`) now checks
`ProductRepository.get_by_id_with_publication` first and rejects with a 409
if the product is already published — "edit it from Products instead."

**What the brief asked for but does not apply to this data model, verified
by reading `ProductImportService.import_product` in full rather than
assumed:** the brief's other states — `fetching`, `import_requested`,
`import_failed` — have no corresponding *editable draft row* to gate. A
`Product` row is only ever created or updated by `_upsert`, which runs only
**after** a supplier fetch already succeeded, inside the same request-scoped
transaction that rolls back entirely on any failure
(`app.api.deps.get_db_session`: commit on success, rollback on any
exception). There is no reachable state where a persisted, navigable draft
corresponds to an in-progress or failed import — those exist only as
`ProductImport` audit rows with no `product_id`, surfaced in Import History,
never reachable as an editable draft in the first place. Inventing a gate
for a state this data model cannot produce would have been speculative
schema/logic, which this repository's rules explicitly warn against — so
none was added, and this is recorded here rather than silently omitted.

Role enforcement (`RequireAdmin`) and tenant isolation (cross-tenant →
404, via the existing `get_by_id_or_raise` tenant-scoped lookup) were
already correctly in place before this milestone and are re-verified by the
new test suite, not re-implemented.

## Editable vs. immutable fields (unchanged by M2A)

Unchanged from the pre-existing editor — M2A did not touch which fields are
editable, only how a write to any of them is now protected:

| Editable (merchant) | Immutable (supplier snapshot) |
|---|---|
| `title`, `description`, `brand`, `category_name`, `vendor`, `tags`, SEO fields, `slug`, `status`, shipping/customs fields | `supplier_title`, `supplier_brand`, `supplier_description`, `external_id`, `source`, original variants/images, `import_ship_to_country`, `import_ship_to_checked_at` |

Regression-tested this pass (`TestSupplierSnapshotImmutability`): editing
title/brand/description through the hardened write path still never touches
the `supplier_*` twins.

## Save contract

- `PATCH /api/v1/drafts/{id}` — PATCH semantics (`exclude_unset`), admin-role
  required, tenant-scoped, publish-state gated, and — as of the
  acceptance-fix pass — **`expectedUpdatedAt` is mandatory**, checked before
  any lookup.
- Success: `200` with the full updated `ProductDetailRead`, including the
  new `updatedAt` to use as the next save's version token.
- Missing or malformed `expectedUpdatedAt`: `422` (acceptance-fix pass).
- Conflict (stale version, or a second conflict landing during "Review my
  changes"): `409`, standard error envelope
  (`code`/`message`/`details`/`requestId`), no partial write.
- Already published: `409`, same envelope, distinct message.
- Cross-tenant / nonexistent: `404`.

### Response matrix — validation runs first

An earlier revision of this document claimed the tenant lookup ran *first*,
so that a missing-token request against a foreign draft returned `404`. That
was wrong, and it was corrected against the code (and verified against a
live server) during the controlled-integration pass. The real order is
**validation, then tenant lookup**:

| Draft | `expectedUpdatedAt` | Status |
|---|---|---|
| Own, editable | missing | `422` |
| Own, editable | malformed | `422` |
| Own, editable | current | `200` |
| Own, editable | stale | `409` |
| Foreign tenant | missing | `422` |
| Foreign tenant | syntactically valid | `404` |
| Nonexistent id | missing | `422` |
| Nonexistent id | syntactically valid | `404` |

Two different code paths produce the `422`s: a *malformed* value fails
Pydantic's own schema validation at the request boundary
(`RequestValidationError`), before the endpoint body runs at all, while a
*missing* value is rejected by the explicit `expected_updated_at is None`
guard at the top of `ProductService.update_draft`. Both surface through
`app/api/error_handlers.py` as the same `422 validation_error` envelope, so
the distinction is invisible to clients — which is what matters here.

**Why this is still non-enumerable.** Enumeration needs the response to vary
with whether the id exists. It does not: the rows compare equal in pairs.
Foreign and nonexistent are indistinguishable with a valid token (`404` vs
`404`) *and* with a missing one (`422` vs `422`). An attacker holding a
syntactically valid token learns nothing, and one omitting the token is
rejected before any lookup happens, so the database is never consulted at
all. Validation-first is not a weakening of the tenant boundary — it runs
strictly *earlier* than it.
- Unknown/unapproved field: `422` (`extra="forbid"` on the shared schema —
  unchanged, pre-existing).
- Whitespace-only title: `422` — found, during this milestone's audit, to
  already be handled by the pre-existing `_reject_blank_when_provided`
  validator combined with `str_strip_whitespace=True` on the schema base;
  confirmed with a new regression test rather than assumed from reading the
  config.

## Testing

**Backend** (`backend/tests/integration/test_draft_editor_concurrency.py`,
new; `test_product_update.py`, extended) — 16 new tests, all passing:
current-version success, stale-conflict rejection (409) and non-overwrite,
backward-compatible omission of the version field, repeated saves with
fresh versions, true no-op saves (both that they don't move `updatedAt`,
and that they succeed even against a since-stale token, proving the no-op
check runs before the version check), publish-state gating (and that an
unpublished draft is unaffected by it), supplier-snapshot immutability
(title/brand and description), cross-tenant 404 (including for a
stale-version request specifically), and role enforcement.

**A genuine test-infrastructure lesson, found and fixed while writing
these:** the shared `client`/`db_session` fixture wraps an entire test in
one Postgres transaction, and `now()` is frozen for the duration of a
transaction in Postgres — so two sequential `client.patch()` calls in the
same test can never show `updated_at` actually advancing, regardless of
whether the underlying mechanism works. The first version of the
stale-write test used two real PATCH calls and passed for the wrong reason
whenever the fix above was in place, and failed for the wrong reason before
it (the failure looked like "the row never became stale" when the real bug
was "the version never bumped" — both true, but for different underlying
timestamps). Fixed by simulating the concurrent write as a direct row
`UPDATE` inside the test rather than a second HTTP call — the same fact
("this row changed since you loaded it") without depending on wall-clock
time the test harness cannot provide. Documented in both affected test
files so a future reader does not "fix" it back to the broken form.

Full backend suite (original M2A delivery): **984 passed** (968 at the
verified M1-integrated baseline + these 16; zero regressions). **Full
backend suite, re-run after the acceptance-fix pass: 991 passed** (see
"Acceptance-fix pass" above for the pass's own added tests, and the final
acceptance report for the complete regression run).

**Frontend, original M2A delivery** (`draft-editor-concurrency.spec.ts`) —
successful save clears unsaved state; a mocked 409 shows the dedicated
conflict banner (not the generic error alert); "Reload latest version"
discards local edits and adopts the server value; "Keep my changes"
preserves the unsaved title while dismissing the conflict; a double-click
on Save fires exactly one request; no console errors through the
save/conflict/reload cycle. **Superseded by the acceptance-fix pass** — see
"Testing added this pass" above for the current 16-scenario suite, which
replaces "Keep my changes" coverage with Review/Reload-confirmation
coverage matching the redesigned UX. Both the original and current suites
mock only the specific `PATCH /drafts/{id}` response inside a real
authenticated session (the same approach `import-history.spec.ts`
established for M1's duplicate-warning tests) — a genuine two-editor race
was additionally exercised manually against real, separate HTTP requests
(see Live verification below), since a real race needs two actually-separate
transactions, which this specific mocking approach does not need and a
shared test-fixture session cannot provide (see the backend note above).

## Live verification

Fresh, loopback-only processes from the isolated worktree
(`C:\dspm2a`) — backend on `127.0.0.1:8010`, frontend on `127.0.0.1:3010` —
neither reusing nor restarting the existing 8000/8002/3000/3001 processes.
Full account of what was checked, defects found and fixed, and screenshots
is in the final delivery report.

## Deferred (M2B–E) — not built by this milestone

Unchanged scope, still missing, tracked in
[MASTER_ROADMAP.md](MASTER_ROADMAP.md): rich-text/markdown description
editing, bulk variant editing, image crop/watermark editing, and the AI
Studio side-by-side proposal review. None of these were started.

## Known limitations

1. The concurrency mechanism cannot distinguish an editor-vs-editor conflict
   from a routine supplier re-sync landing at the same moment (see above).
2. `expectedUpdatedAt` protection is opt-in at the schema level for
   `/products/{id}`; only the drafts editor's frontend actually sends it
   today. Extending `/products/{id}`'s own UI to send it, if one exists
   outside the drafts editor, is unscoped work for a future pass.
3. No migration was added or needed — this was verified, not assumed,
   against the actual write path before deciding so.
4. `draft-editor-concurrency.spec.ts` is not idempotent across Playwright
   projects when both are pointed at one pre-seeded `E2E_PRODUCT_ID`. Its
   `"Save my version anyway"` test both hard-codes a title *and* persists
   it, so when chromium and mobile-chrome run in parallel against the same
   row, the second project's `fill()` writes a value the field already
   holds — no change event, never dirty, Save stays disabled, timeout. It
   passes 32/32 serially, and in CI (which seeds a fresh product per run
   and runs chromium only). Left as-is rather than papered over: it is a
   fixture-sharing limitation of the *test*, not editor behaviour, and
   fixing it belongs with a broader per-project seeding change.
5. The `conflictLocalSnapshot` shown in review is frozen at the 409. The
   fields stay editable while the banner is open, so a merchant who keeps
   typing during a conflict will see the review comparison show what was
   rejected while "Save my version anyway" sends what is currently on
   screen. Both are defensible; they differ only if the merchant edits
   *after* the conflict, and the on-screen values are what they can see.
