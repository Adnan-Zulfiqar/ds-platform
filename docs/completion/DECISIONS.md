# Autonomous completion — decision log

Decisions taken while executing the owner's autonomous-completion roadmap
(`DropPilot_Claude_Autonomous_Completion_Roadmap.md`, issued 2026-10-01,
supplied by the owner outside the repository). Each entry records the
alternative and the trade-off. Newest last.

---

## D-001 — Development/review policy change (2026-10-01)

**Decision.** The owner authorised autonomous completion and moved Cursor's
independent review to the end of implementation. Author-verified work may be
integrated into `develop` when its required checks pass on the exact head.
Each such integration is recorded as
`AUTHOR_VERIFIED — INTEGRATED — CURSOR_REVIEW_PENDING`.

**Conflict stated, not hidden.** `CLAUDE.md` §12 says "Stop. Do not roll into
the next phase", and earlier conversation instructions required an
independent review before each stage merged. `CLAUDE.md` itself says an
explicit owner instruction wins but must be named out loud. This entry is
that statement. The technical quality gates in §12, the security rules in §7
and the honesty rules in §13 are **not** relaxed.

**Unchanged.** Historical review documents keep their original verdicts.
No FAIL/NOT ACCEPTED result was edited. Independent review status of every
item stays `PENDING` until Cursor actually reviews it. `main` is not touched
and nothing is deployed.

## D-002 — Release scope (2026-10-01)

**In release** (the owner confirmed this list on 2026-10-01):

1. Phase 9 Stage 10 — AI Product Studio (`docs/PHASE_9_STAGE_10_PLAN.md`, PR #25).
2. Phase 9 Stage 11 — "Docs, gates, tag — Completion report"
   (`docs/PHASE_9_PLAN.md` §3).
3. Draft Editor stages 6–8 (`docs/DRAFT_PRODUCT_EDITOR_PLAN.md`, "Stage progress").
4. Open remediation findings (`docs/REVIEW_REMEDIATION_STAGE_5_9.md`).

**Out of release, with the source that says so.** `PROJECT_ROADMAP.md`,
"Later phases": *"Not scheduled, and listed only so that architectural seams
are built with them in mind. Nothing here is committed to a phase number."*
That covers additional store channels (eBay EBAY-C2…C6, WooCommerce, Etsy,
TikTok), Shopify fulfilment push, the remainder of real FX (M24B/M24C),
subscription billing, team management, admin panel and outbound email.
`docs/ebay/MASTER_EBAY_ROADMAP.md` lists C2–C6 as "not started". They are
recorded in the scope matrix as `FUTURE` with this citation, not deleted.

**Alternative rejected.** Treating every roadmap row as release scope would
start several unplanned phases (billing, eBay listings) that have no
approved plan; the completion roadmap forbids inventing product direction.

## D-003 — Lockfile toolchain (2026-10-01)

**Decision.** Lockfile edits use npm 11 (`npx -y npm@11 install
--package-lock-only`); installs use Node 22's bundled npm (`npm ci`), as CI
and `docker/frontend.Dockerfile` do.

**Why.** No `packageManager` or `engines` field pins npm. `develop`'s
lockfile carries `libc` arrays that npm 10.9.x drops when it rewrites
entries; npm uses them to choose the glibc or musl build of native optional
packages. npm 11 preserves them. Verified: `npm ci` on `node:22-alpine` and
`node:22-bookworm-slim` (npm 10.9.9) installs only the matching native
binary and does not rewrite the lockfile.

**Alternative rejected.** Declaring `packageManager: npm@11` would change
what CI and the Docker image install with — a toolchain change outside the
dependency fixes. Recorded for Cursor as a possible follow-up.

## D-004 — Local-only encryption key (2026-10-01, AUT-03)

**Decision.** A new Fernet key was generated and written to the
repository-root `.env` of the local development checkout. No value was
printed, logged, passed as an argument or committed.

**Conditions verified immediately before writing** (roadmap §8.2):

1. Destination is the local Docker Compose project `droppilot` on the
   owner's workstation; no deployment reads that file.
2. `SECURITY_ENCRYPTION_KEYS` was empty in the root `.env` and
   `backend/.env`, unset in the Windows User/Machine/Process environment and
   empty in the backend, worker and beat containers.
3. Every column written by `app.core.encryption` was inspected read-only in
   both local databases (`droppilot`, `droppilot_test`):
   `aliexpress_connections` (3 columns), `shopify_connections` (1),
   `ebay_connections` (2), `stores.encrypted_credentials` — 0 non-null values.
   `stores` had not been checked in the earlier L-1 diagnosis.
4. No backup file or backup volume exists locally; only the scripts.
5. `encrypt()` raises `EncryptionNotConfiguredError` when no key is set, so
   no worker could write ciphertext between the check and the write.

**Load path.** Compose `env_file: [.env]` → process environment of backend,
worker and beat; the process environment outranks pydantic's file sources.
Only those three services were recreated (`--no-deps`); no volume was
touched. Round trip through the application's own `encrypt`/`decrypt`
succeeded in backend and worker.

**Limits.** Host-run backends read `backend/.env` after the root `.env`;
its empty entry was left as it was, so a host-run backend still reports no
key. The key must never be copied into a deployment.

## D-005 — Merge permission incident (2026-10-01)

Recorded in `BLOCKERS.md` (B-001). The integration branch built while
diagnosing it was deleted locally and never pushed.

## D-006 — Draft Editor Stage 8 "bulk tools" has no authoritative definition (2026-10-02)

**Finding.** The source gives one line: "Bulk tools + live E2E verification
— Pending" (`DRAFT_PRODUCT_EDITOR_PLAN.md`). The only design text is a UX
baseline proposal (`docs/ux/ux-l2d-01-baseline.md` §183): a selection bar
"only for actions the API supports today per item (Optimize ×n via repeated
`POST /optimize`, Refresh ×n). Bulk publish is **not** offered."

**What already covers it.** "Optimize ×n" is delivered properly by AI
Studio's bulk run (Stage 10: durable, reviewed, never auto-activating), not
by looping the legacy endpoint that this programme retired. Bulk pricing on
drafts exists through Global Rules apply.

**What is not done.** A bulk supplier refresh ("Refresh ×n"). It would be
N sequential AliExpress calls with no durable progress, it cannot be
verified without a live AliExpress connection, and no requirement says it
is in the release. Recorded as an open scope question (`BLOCKERS.md`
B-006) rather than built speculatively or declared out of scope.

## D-007 — Read-after-write ordering is a framework setting, not a handler concern (2026-10-02)

`DbSession` uses `Depends(get_db_session, scope="function")` (PR #35,
DP-CR-018). Recorded here because it is a cross-cutting rule: any future
yield dependency that owns a transaction must use function scope, or the
client can receive a 2xx before the commit.

## D-008 — `phase-9-complete` waits for Cursor's acceptance (2026-10-02)

Stage 11 is "Docs, gates, tag". The report and the gates are done in this
programme; the tag is not created yet. A tag is a permanent, public claim
that the phase passed, and `PHASE_9_PLAN.md` §7 ties it to "every stage in
§3 is done and its verification executed". With the independent review
deferred to the end (D-001), tagging now would claim an acceptance that has
not happened, and a tag should not be moved afterwards. The candidate SHA is
recorded in the final report; the tag is created on it once Cursor accepts.

## D-009 — New Next 16 lint rules as warnings in the upgrade PR (2026-10-02)

eslint-config-next 16 added `react-hooks/refs`, `react-hooks/set-state-in-effect`
and `react-hooks/purity`; they flag 23 existing call sites in 13 files. The
Next 16 upgrade (PR #40) is a security change (bundled PostCSS advisories),
so these three rules are warnings there and the 23 fixes are a separate,
behaviour-preserving task. Alternative rejected: fixing all 23 inside the
security PR, which would mix behaviour changes across reviewed components
into a dependency upgrade. No rule that existed before is relaxed.

## D-010 — B-006 resolved from the sources: "Refresh ×n" is future scope (2026-10-02)

Supersedes the open question in D-006. The release requirement is the
one-line "bulk tools" in Draft Editor Stage 8 / Workspace V2 Stage 8; it is
met by AI Studio bulk runs and Global Rules bulk pricing. "Refresh ×n" and
"Publish Selected" appear only as a UX proposal and a terminology label,
and the same UX programme recorded bulk selection as not built. They are
future scope, not an owner-decision blocker. Live E2E (the other half of
Stage 8) stays an external blocker. Trace: DP-CR-023.

## D-011 — Local candidate stack beside the owner's stack (2026-10-02)

The candidate runs as Compose project `dp-candidate` from a `git archive`,
on ports 18080/18000, with its own volumes and broker, no beat, and the
owner's root `.env` passed by absolute path. Alternative rejected: rebuilding
the owner's `droppilot` stack, which would replace images the owner's
running stack uses and requires their main checkout (on an older branch) to
change. Runbook: `docs/operations/LOCAL_CANDIDATE_STACK.md`.

## D-009 — closed (2026-10-02)

All 25 Next 16 lint findings fixed in PR #42; the three react-hooks rules are
back at their defaults.

## D-012 — Owner decisions of 2026-10-03: B-011 approved; Track E in roadmap order

**B-011 approved.** One new unscoped, ids-only class,
`EbayConnectedTenantsSweep`, for the scheduled eBay jobs (hourly order
import, six-hourly price/stock backstop). Added to CLAUDE.md §4's closed list
in the same change. Every action it leads to runs under the tenant's context.

**Track E** (`docs/CLAUDE_REMAINING_ROADMAP.md`) is authorised, in roadmap
order: Shopify fulfilment push → FX M24B/M24C → outbound email → team
invites → platform admin panel → subscription billing → WooCommerce / Etsy /
TikTok. Each one: plan doc, implementation, gates, PR, CI, merge. Live
provider testing stays with the owner.

## D-013 — Team invitation links name their tenant (Track E4)

Accepting an invitation has no session, so the link carries
`<tenant id>.<secret>`. The invitation is looked up inside that tenant by the
SHA-256 of a 256-bit secret. A forged tenant id finds nothing and is a 404.
The rejected alternative was an unscoped lookup by token hash, which would
put a cross-tenant query on a request path (CLAUDE.md §4).

Trade-off: an address that already has an account anywhere cannot accept,
because login cannot yet choose between two accounts that share a password.
Fixing that means tenant-qualified login, a separate decision. Details:
`docs/track-e/E4_TEAM_INVITATIONS.md`.
