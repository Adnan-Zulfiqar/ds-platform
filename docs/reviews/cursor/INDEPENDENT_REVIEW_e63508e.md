# Independent review — develop @ e63508e

Reviewer: Cursor, 2026-10-02. This file is the independent verdict.
Author CI and author reports were treated as leads, not as acceptance.

## Identity

| Item | Value |
|---|---|
| Remote | `https://github.com/Adnan-Zulfiqar/ds-platform.git` |
| Repository | Adnan-Zulfiqar/ds-platform |
| Review worktree | `C:\Projects\ds-platform-ir` on `audit/cursor-independent-review-e63508e` |
| Pinned review target | `e63508e014a89e611d535476904d7d1888554665` |
| `origin/develop` at review start | same SHA (develop had not moved past it) |
| Last application commit | `9a62b9e818350fa8dafc16276a3ea0200411971b` (PR #42) |
| Baseline named by the handoff | `72e76921fc80b203133423ad3bd92bd380c7e02b` — prior Cursor acceptance of that baseline is **not** claimed here |
| Owner checkout left untouched | `docs/phase-9-stage-10-plan` @ `ef125ed` |

Commits after `9a62b9e` and included in `e63508e`:

- `8859492` — `backend/tests/unit/test_server_launch_surfaces.py` (+7) and launch-surface audit docs
- `4be1b39` — completion / checkpoint documentation
- `e63508e` — merge of PR #43

No application source outside that one test file. No permission, migration, or production change in that range.

Author CI `36995948409` is on `9a62b9e`, not on `e63508e`. It is 10/10 and is **not** this review's acceptance.

## Verdicts

**A. Implementation acceptance: ACCEPTED WITH NON-BLOCKING NOTES**

The application behaviour reviewed in source, and the backend and frontend gates re-run below, match the release contracts that are implemented. No blocking repository defect was reproduced. Notes record evidence that this review did not re-execute (full browser suite, backup drill, live providers) and items the author correctly left open.

**B. Release readiness: NOT READY**

Exact remaining blockers:

1. No live AI provider (B-002). StubProvider only.
2. No live Shopify Partner OAuth or designated test-store publish (B-003).
3. No live AliExpress → Shopify editor journey (B-004 / Draft Editor DE-8b).
4. `phase-9-complete` stays untagged until an owner accepts after this review (D-008). This review does not create the tag.
5. Two Playwright flakes stay cause-unproven (DP-CR-015). Green repeats are not a cause.
6. Owner `droppilot-backend` and `droppilot-worker` images still contain `/app/.env` (B-007). Candidate images checked here do not. Absence of a registry host is not proof the old images were never pushed.
7. The backup/restore drill was not repeated on `e63508e` in this review. The author's drill is a claim about an earlier image id, not a result produced here.

Nothing in this review is provider-verified or production-ready.

## Findings

| ID | Severity | Location | Reproduction / evidence | Impact | Acceptance |
|---|---|---|---|---|---|
| IR-01 | Note | `backend/app/api/deps.py` `DbSession` | Read: `Depends(..., scope="function")` commits in the dependency exit before the response. Suite includes the surrounding tests and passed | Success responses are only sent after commit | Met in the tree that pytest ran |
| IR-02 | Note | `product_pipeline.py` `approve` / `publish`; `sync.py` `_resolve_listing_content` | Approve returns the product and does not call Shopify. Pipeline publish requires the exact version id, active, non-stub. Ordinary publish re-sends `listing.content_version_id` only after a prior successful AI publish recorded it | Approval does not publish. A later ordinary publish does not pick an unselected inactive candidate | Met by inspection; covered by the passing suite |
| IR-03 | Note | `frontend/proxy.ts`, `frontend/lib/csp.ts` | Inbound `x-nonce` is overwritten. Protected routes set `Cache-Control: no-store`. Proxy is documented as UX, not the auth boundary | Client-supplied nonce cannot select the CSP nonce | Met by inspection. Browser suite not re-run |
| IR-04 | Note | `.dockerignore` `**/.env` | `dp-candidate-backend` and `dp-candidate-worker`: `/app/.env` absent. `droppilot-backend` and `droppilot-worker`: present. No image contents printed | Future builds from this tree exclude env files. The owner's September images do not | Code fix accepted. Operational cleanup still open (B-007) |
| IR-05 | Note | DP-CR-015 | Author history kept. This review did not repeat the 5× flake specs | Cause of both flakes remains unproven | Do not close them |
| IR-06 | Note | `docs/DRAFT_PRODUCT_EDITOR_PLAN.md` Stage 8; UX baseline §183; D-010 | "Refresh ×n" and "Publish Selected" are a UX proposal and a terminology label. The editor plan does not define those controls. Bulk publish is explicitly not offered in the baseline. Live E2E remains B-004 | B-006 classification as future is consistent with the sources. Stage 8 is not fully done | Do not tag Stage 8 complete |
| IR-07 | Note | Backup drill in `FINAL_REPORT.md` §7 | Not re-run. Author cites `48008107…` as identical to the candidate | Drill evidence is author-reported | Re-run only if the tag requires a fresh drill |

No high or medium repository defect was found.

## Coverage

| Requirement | Evidence from this review | Gap |
|---|---|---|
| Stage 5–9 remediation, commit-before-response | Source plus pytest 3477 passed | — |
| AI preview → approve → publish, tokens, immutable history | Source of approve/publish/overlay | Not a live Shopify call |
| Ordinary publish does not publish an unselected candidate | `_resolve_listing_content` | Not a live Shopify call |
| Draft editor readiness, images, ambiguous publish | Implemented under PRs #37/#38; included in pytest | Browser not re-run |
| Bulk idempotency / cancel | Stage 9 tables and tests in the passing suite | Celery broker not driven against the owner's RabbitMQ |
| React lint, hydration, proxy | `npm run lint`, `typecheck`, `build` (Next 16.3.8), vitest 90 passed | Behavioural browser suite not re-run |
| Dependency advisories | `npm ci` reported `found 0 vulnerabilities` (570 packages) | Python advisory scan not re-run; no gate was edited |
| Docker secret exclusion | `.dockerignore` and boolean `/app/.env` checks | Owner images remain |
| Flakes | Left open | — |
| Backup/restore | Not repeated | Author claim only |
| B-006 | Sources agree those two labels are not specified requirements | DE-8b still blocked |

## Gates re-run here

Environment: Windows, review worktree at `e63508e`. Disposable Postgres `dp-review-pg` on `127.0.0.1:15432` and Redis `dp-review-redis` on `127.0.0.1:16379`, both removed after the run. The owner's Compose volumes, the `:80` stack, and the `:18080` candidate stack were not recreated. Host port 5432 is a different server from Docker's Postgres; the first pytest attempts failed with `database "droppilot_test" does not exist` against that other server. That is an environment mismatch, not a product failure.

| Command | Result |
|---|---|
| `ruff check .` | All checks passed |
| `ruff format --check .` | 471 files already formatted |
| `mypy app` | Success, 236 source files |
| `pytest -q` | **3477 passed, 1 skipped, 303 warnings**, 914s. Skip: `test_log_retention.py` symlink privilege on Windows. No retries |
| `npm ci` | 569 packages added, **0 vulnerabilities** |
| `npm run lint` | passed |
| `npm run typecheck` | passed |
| `npm run build` | Next.js 16.3.8, compiled, 26 static pages including `/ai-studio`. Next rewrote `frontend/tsconfig.json`; that edit was reverted and is not part of the product |
| `npm run test:unit` | vitest **90 passed** (4 files) |
| Playwright | **Not re-run.** Default config uses `http://localhost:3000` and `reuseExistingServer` outside CI, which is the owner's older frontend, not this tree |
| Candidate page | `http://localhost:18080/login` rendered the sign-in form (candidate stack already running). Not a merchant journey and not provider OAuth |
| Worker broker harness | Not run, so the owner's RabbitMQ was not given new tasks. `dp-candidate-worker` was already healthy |
| Alembic | Tree contains `0034`, `0035`, `0036`. Pytest applied migrations on the disposable database |

The eight Playwright skips on author CI `36995948409` were **not** re-listed from a fresh local run. This review does not invent their names. They remain author-job output, not independently reproduced.

## Separate buckets

**Repository defects:** none blocking.

**Operational cleanup:** rebuild or retire `droppilot-backend` and `droppilot-worker` (they still contain `/app/.env`). Rotate `backend/.env` values only if those images were ever shared. That cannot be proven from local image metadata.

**Missing external verification:** live model, live Shopify, live AliExpress, fresh backup drill, full Playwright on this tree.

## Remediation list for Claude

1. Leave `phase-9-complete` untagged.
2. Do not close DP-CR-015 on green repeats.
3. Do not mark Draft Editor Stage 8 complete while DE-8b is blocked.
4. Do not describe StubProvider runs as production AI, or local OAuth URL construction as a connected store.
5. No application-code change is requested by this review.

CLAUDE RETURN REVIEW CHECKPOINT:
All commits from Stage 5 takeover onward require a fresh Claude
end-to-end review when Claude becomes available again.
