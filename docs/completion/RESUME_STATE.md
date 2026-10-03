# Resume state

Single continuation point after an interruption. Overwritten, not appended.

**Updated:** 2026-10-03 (later) — Track E in progress (owner standing order, D-012 order).

| Item | Value |
|---|---|
| Candidate | `develop` @ `9a62b9e818350fa8dafc16276a3ea0200411971b` (CI 36995948409 10/10) |
| Independent verdict | Cursor on `e63508e`: implementation ACCEPTED WITH NON-BLOCKING NOTES; release NOT READY |
| `main` | `3ce66d4` — untouched |
| Untracked files to preserve | `C:\Projects\ds-platform\AGENTS.md`; root `.env` (local-only key, D-004); `backend/.env` |

## Running resources owned by this work

- Compose project **`dp-candidate`** (http://localhost:18080, API :18000):
  containers `dp-candidate-*`, volumes `dp-candidate_postgres_data`,
  `dp-candidate_rabbitmq_data`, images `dp-candidate-*`. Stop with the
  commands in `docs/operations/LOCAL_CANDIDATE_STACK.md`.
- Image `dp-e2e-clean` (harness). Renamed directories
  `%LOCALAPPDATA%\Docker\run.stale-*` and
  `%LOCALAPPDATA%\docker-secrets-engine.stale-*` (B-008) — safe to delete
  once Docker has run normally for a while.

## Remaining roadmap (Tracks A–D), 2026-10-03

| Track | State |
|---|---|
| A | Agent items done (A1 images, A2 flake A reproduced). A3–A5 OPEN with owner checklists in `BLOCKERS.md`; B-009 (encryption key missing from root `.env`) found |
| B | `OpenAIProvider` merged (#46, `9ffa700`); mocked-transport tests only, live call is owner item B-002 |
| C | Audit only; nothing left that is not blocked on A4/A5 |
| D | C2–C6 merged (#48–#52); review fixes in the follow-up PR. Live eBay steps are owner actions (`docs/ebay/EBAY_C6_OPERATIONS.md`) |

## Track E (2026-10-03, D-012 order)

| Item | State |
|---|---|
| B-011 scheduled eBay jobs | Merged (#54) |
| E1 Shopify fulfilment push | PR #55 — waiting for CI, then merge. Stores must reconnect for the new scopes (owner) |
| E2 sale fees (M24C) | PR #56 — waiting for CI. M27 freight quote blocked on a live AliExpress response |
| E3 notification email | PR #57, stacked on #56 (migration 0043 after 0042). Merge after #56 |
| E4 team invitations | Branch `feat/team-invitations`, stacked on E3 (migration 0044). PR after #57 |
| E5 platform admin | **Blocked, B-013** — proposal in PR #58 |
| E6 billing | **Blocked, B-014** — proposal in PR #58 |
| E7 channels | WooCommerce next (no app approval needed); Etsy/TikTok need owner app registrations |

Merge order: #55, #56, #57, then E4, then #58 (expect PROJECT_ROADMAP/CHANGELOG
conflicts; resolve by keeping both sides).

## Next safe action

After #48: every remaining item is an owner action or an approval (C3 proposal). Do **not**
tag `phase-9-complete` or mark Draft Editor Stage 8 complete. Owner items:
B-009 first, then B-007 recreate, then B-002/B-003/B-004 checklists.
