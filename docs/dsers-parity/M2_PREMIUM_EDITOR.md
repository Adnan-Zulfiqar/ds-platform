# M2A — Premium Product Editor Foundation

Status: **Delivered.** Branch: `feature/dsers-parity-m2a-editor-foundation`.
Audit date: 2026-08-12.

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
- `expectedUpdatedAt` is **optional** on the shared schema: a caller that
  omits it (any pre-M2A integration, or `/products/{id}` callers that
  haven't adopted it) keeps the exact previous last-write-wins behaviour.
  The premium draft editor always sends it, so its own save path is fully
  protected.

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
  banner (`data-testid="draft-conflict-banner"`), because "reload or keep
  your text" is a different recovery action than "fix a field and retry."
- **"Reload latest version"** discards local edits and adopts the server's
  current values (reuses the existing load-effect that already resets every
  field from a fresh fetch).
- **"Keep my changes"** refetches the current version token and read-only
  supplier context *without* touching the merchant's still-unsaved
  title/description (a `preserveEditsOnNextLoad` ref gates the one load
  effect that would otherwise overwrite them), so a follow-up save can
  succeed against the current version instead of repeating the same
  conflict.
- Autosave is frozen while a conflict is showing (no repeated request-then-409
  every 1.8s), and `handleSave` itself guards against firing a second
  concurrent request while one is already in flight or a conflict is
  unresolved.
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
  required, tenant-scoped, publish-state gated, optionally version-gated via
  `expectedUpdatedAt`.
- Success: `200` with the full updated `ProductDetailRead`, including the
  new `updatedAt` to use as the next save's version token.
- Conflict (stale version): `409`, standard error envelope
  (`code`/`message`/`details`/`requestId`), no partial write.
- Already published: `409`, same envelope, distinct message.
- Cross-tenant / nonexistent: `404` (checked first, so a stale-version
  request against a foreign draft cannot leak existence via a different
  status than a fresh one would get).
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

Full backend suite: **984 passed** (968 at the verified M1-integrated
baseline + these 16; zero regressions).

**Frontend** (`frontend/tests/e2e/draft-editor-concurrency.spec.ts`, new) —
successful save clears unsaved state; a mocked 409 shows the dedicated
conflict banner (not the generic error alert) with both recovery actions;
"Reload latest version" discards local edits and adopts the server value;
"Keep my changes" preserves the unsaved title while dismissing the
conflict; a double-click on Save fires exactly one request; no console
errors through the save/conflict/reload cycle. These mock only the specific
`PATCH /drafts/{id}` response inside a real authenticated session (the same
approach `import-history.spec.ts` established for M1's duplicate-warning
tests) — a genuine two-editor race was additionally exercised manually
against real, separate HTTP requests (see Live verification below), since a
real race needs two actually-separate transactions, which this specific
mocking approach does not need and a shared test-fixture session cannot
provide (see the backend note above).

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
