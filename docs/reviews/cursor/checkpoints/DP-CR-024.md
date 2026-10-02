# DP-CR-024 — Docker secret exclusion: existing images, clean replacements, local candidate

Follows DP-CR-008 (PR #32, the `.dockerignore` fix for future builds).

## Existing images assessed (2026-10-02; names of files only, never contents)

| Image | Created | Contains `/app/.env` | Registry digest | Owner |
|---|---|---|---|---|
| `droppilot-backend`, `droppilot-worker`, `droppilot-beat` | 2026-09-22 | **yes** | local only (`droppilot-*@sha256:…`, no registry host) | owner's running stack |
| `droppilot-frontend` | 2026-09-22 | no | local only | owner's running stack |
| `dp-remed-test`, `dp-remed-e2e` | 2026-09-30 / 10-01 | **yes** | local only | this task |

No evidence of distribution: no registry host in any digest, CI builds with `push: false`, and no push was made by this work. Whether the owner ever pushed these images manually cannot be seen from here.

## Actions

- **Task-owned images removed:** `dp-remed-test`, `dp-remed-e2e` (no container used them). A `docker image prune` for **dangling** (untagged) images was also run; that can remove untagged build leftovers not created by this task. No tagged image, container or volume was affected.
- **Clean replacements built:** the e2e harness image `dp-e2e-clean` (from the clean candidate backend image + Node 22 + Playwright Chromium) — full-filesystem scan found no `.env`; candidate images `dp-candidate-backend/worker/frontend` built from a `git archive` of `9a62b9e` — no `.env`.
- **Not changed:** the owner's `droppilot-*` images, which the running stack uses.

## Owner remediation

1. Rebuild the development stack from a checkout of `develop` at or after `7fe0a89` (the main checkout is on an older branch whose `.dockerignore` still copies `backend/.env`).
2. If any `droppilot-*` image built before that was ever pushed or shared, treat every value in `backend/.env` as exposed and rotate it at its provider.

## Local candidate stack

`docs/operations/LOCAL_CANDIDATE_STACK.md`: project `dp-candidate`, http://localhost:18080, own volumes and broker, beat off, the existing local key reused by path. Browser-verified flows are listed there.

- **Independent review:** PENDING — NOT YET PERFORMED
