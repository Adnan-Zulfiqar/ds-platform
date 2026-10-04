# Resume state

Single continuation point after an interruption. Overwritten, not appended.

**Updated:** 2026-10-04 (end of session) — E5, E6 and the CSP image fix merged; owner items open.

| Item | Value |
|---|---|
| Candidate | `develop` @ `21c86a7abb3a7ade5f43f54e4e06649078c44d9a` (PR #75 merge; every PR #69–#76 merged on 10/10 CI) |
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
| E1 Shopify fulfilment push | Merged (#55). Stores connected earlier must reconnect for the new scopes (owner) |
| E2 sale fees (M24C) | Merged (#56). M27 freight quote blocked on a live AliExpress response |
| E3 notification email | Merged (#57). Real-inbox delivery not verified (owner: Resend + `EMAIL_APP_BASE_URL`) |
| E4 team invitations | Merged (#59) |
| E5 platform admin | All merged: E5a #69, E5b #70, E5c #71, E5d #72 (`docs/track-e/E5_PLATFORM_ADMIN.md`). Off by default; needs `PLATFORM_ADMIN_ALLOWED_CIDRS` and an operator account |
| E6 billing | All merged: E6a #73, E6b #74, E6c #75 (`docs/track-e/E6_BILLING.md`). Sandbox keys in the root `.env`. Owner: confirm trial limits (450 listings, no AI), update Terms §12/§14, roll the webhook secret, run one test checkout |
| CSP image fix | #76 merged. Supplier image hosts allowed in `img-src`; the owner's `droppilot-frontend-1` was recreated on the new image (old image kept as `droppilot-frontend:pre-csp-fix`) |
| E7 WooCommerce | All merged: W1 #60, W2 #61, W3 #62, W4a #63, W5 #64, W4b webhooks #65. The periodic sweep needs approval (**B-016**). Etsy/TikTok: **B-015** |

Every remaining Track E item is an owner decision or an owner registration.
Nothing in Track E was verified against a live provider. For each stage,
the stage doc in `docs/track-e/` lists what was verified and what was not.

## Owner stack note (2026-10-04)

The owner's `docker-compose.yml` bind-mounts `./backend` from the checkout
at `C:\Projects\ds-platform`, which is on branch
`feat/aliexpress-catalog-import-without-merchant-oauth` with uncommitted
changes. The running backend therefore has none of E5, E6 or migrations
0040–0048. To run them: commit or set aside that work, check out `develop`,
recreate backend/worker/beat on fresh images, then
`docker compose -p droppilot exec backend alembic upgrade head`.
Only the frontend container is on `develop` (#76).

## Next safe action

After #48: every remaining item is an owner action or an approval (C3 proposal). Do **not**
tag `phase-9-complete` or mark Draft Editor Stage 8 complete. Owner items:
B-009 first, then B-007 recreate, then B-002/B-003/B-004 checklists.
