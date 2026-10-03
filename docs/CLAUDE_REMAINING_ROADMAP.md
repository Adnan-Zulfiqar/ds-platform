# Claude — remaining work roadmap

**Audience:** Claude (or any coding agent) continuing DropPilot after Phase 9.
**Pinned baseline:** `origin/develop` @ `7af51f9` (2026-10-03 fetch). Update the
pin when you start a new session.
**Sources of truth:** `CLAUDE.md` / `AGENTS.md`, `PROJECT_ROADMAP.md`,
`docs/completion/*`, `docs/PHASE_9_COMPLETION.md`,
`docs/reviews/cursor/INDEPENDENT_REVIEW_e63508e.md`,
`docs/ebay/MASTER_EBAY_ROADMAP.md`.

This document lists **what is still left**. It does not re-plan finished work.

---

## 0. What is already done (do not rebuild)

| Area | Status |
|---|---|
| Phases 0–8.1 | Complete on `develop` |
| Phase 9 Stages 1–10 | Merged (`#11`…`#36` lineage) |
| Stage 5–9 remediation | Merged (`#26`) |
| Stage 11 completion report | Written (`docs/PHASE_9_COMPLETION.md`) |
| Cursor independent review @ `e63508e` | Implementation **ACCEPTED WITH NON-BLOCKING NOTES** |
| AI Studio UI | `/ai-studio` on `develop` |
| StubProvider pipeline | Preview → approve → publish (fails closed on synthetic) |

**Not done despite Stage 11 report:** tag `phase-9-complete`, production deploy,
live model / live OAuth evidence. Release verdict: **NOT READY**.

---

## 1. Remaining work (ordered)

Execute **top to bottom**. Finish each track’s Definition of Done before the
next. Do not invent credentials. Do not skip quality gates.

### Track A — Close Phase 9 release (highest priority)

Owner/external evidence is required for several items. Agent work that can
proceed without inventing secrets is listed as **AGENT**; human-only as
**OWNER**.

| ID | Work | Who | Definition of Done |
|---|---|---|---|
| A1 | Rebuild owner `droppilot-*` images from current `develop` so `/app/.env` is absent (B-007) | AGENT + OWNER host | Boolean check: `/app/.env` missing in new backend/worker images; document SHA/image ids; **do not print env contents** |
| A2 | Prove Playwright flakes REM-N5-a/b (DP-CR-015) or leave open with evidence | AGENT | Either root-cause fix + green CI, or written “cause unproven” with reproduction attempts — do not close without cause |
| A3 | Live AI provider (B-002) | OWNER key + AGENT code if missing | At least one real provider (`openai` / `anthropic` / `gemini`) configured; one non-stub generation recorded; publish path still refuses unverified/synthetic as designed |
| A4 | Live Shopify Partner OAuth + test-store publish (B-003 / M17) | OWNER browser | Connected test store; one successful pipeline publish evidence (no secrets in git) |
| A5 | Live AliExpress → edit → Shopify journey (B-004 / DE-8b) | OWNER browser | End-to-end evidence; only then may Draft Editor Stage 8 be marked complete |
| A6 | Owner accepts release → create `phase-9-complete` tag (D-008) | OWNER decision; AGENT tags only after explicit “tag now” | Tag on verified SHA; CHANGELOG/roadmap already match |
| A7 | Production deploy (`main`) | OWNER only | Out of autonomous scope unless owner says “deploy to production now” |

**Stop condition for Track A:** A1–A2 done by agent; A3–A5 blocked → document
exact owner actions and continue Track B. Never fake live evidence.

### Track B — Real AI providers (product follow-on)

Phase 9 shipped `StubProvider` only. Next engineering slice:

1. Read `app/ai/factory.py`, `AIProvider` protocol, config `AI_*`.
2. Implement **one** concrete provider with a real caller (OpenAI first unless
   owner names another). Follow CLAUDE.md: no abstraction without a caller.
3. Wire env: `AI_PROVIDER`, key fields — values only from owner `.env`, never
   committed.
4. Unit tests with mocked HTTP; integration tests without network if no key.
5. Docs: update Phase 9 limitations / CHANGELOG / roadmap in the **same** change.
6. PR vs `develop`, wait CI green, do not merge unless owner standing order
   allows merge-after-green (see §3).

### Track C — Draft Editor / Product Workspace leftovers

| ID | Work | Notes |
|---|---|---|
| C1 | DE-6b / DE-7 polish already largely shipped — verify against plans | Re-audit only; no rewrite |
| C2 | DE-8a bulk tools | Only actions already implied by plans; no invented bulk publish |
| C3 | DE-8b live E2E | Blocked on A4/A5 |

Plans: `docs/DRAFT_PRODUCT_EDITOR_PLAN.md`, `docs/PRODUCT_WORKSPACE_V2_PLAN.md`.

### Track D — eBay channel (after C0/C1)

| Phase | Scope | Status |
|---|---|---|
| EBAY-C0 / C0.1 | Compliance / deletion | Complete (prod) |
| EBAY-C1 | Seller OAuth connect | Complete on develop; live consent still OWNER |
| **EBAY-C2** | Policies, marketplaces, inventory locations | **Not started** |
| **EBAY-C3** | Draft → eBay listing publish | **Not started** |
| **EBAY-C4** | Inventory / pricing sync | **Not started** |
| **EBAY-C5** | Orders / fulfilment / tracking | **Not started** |
| **EBAY-C6** | Production hardening | **Not started** |

Rule: every new eBay-stored identifier must update
`EBAY_STORAGE_DECLARATIONS` + deletion eraser in the **same** change.

Sequence: plan doc → implement → gates → PR → CI → stop at track boundary
unless this roadmap authorises continuing.

### Track E — Later product (do not start until D is authorised)

From `PROJECT_ROADMAP.md` “Later phases” — not scheduled:

- Shopify fulfilment push to Admin API
- FX M24B / M24C
- WooCommerce / Etsy / TikTok
- Subscription billing, team invites, platform admin panel
- Outbound email delivery

---

## 2. Quality gate (every track)

Backend:

```bash
cd backend && ruff check . && ruff format --check . && mypy app && pytest
```

Frontend:

```bash
cd frontend && npm run lint && npm run typecheck && npm run build
```

Also: secrets scan if the repo script exists; never commit `.env`; update
CHANGELOG + roadmap in the same change as behaviour.

---

## 3. Master command (paste into Claude)

Copy everything in the box below as the user message.

```text
You are continuing DropPilot AI (ds-platform). Follow CLAUDE.md / AGENTS.md
and docs/CLAUDE_REMAINING_ROADMAP.md exactly.

AUTHORISATION (owner Addi — standing order for this session):
- Do the entire remaining roadmap (Tracks A → D in order) without asking me
  for permission, confirmation, or “should I continue?” between steps.
- Do not stop for optional clarifications, style preferences, or mid-track
  approval. Decide using the repo docs and keep going.
- Create branches, implement, run gates, open PRs, wait for CI, and — when
  CI is green on the PR head — merge into develop with
  `gh pr merge --merge --match-head-commit <sha>` unless a hard-stop below
  applies.
- After one track’s Definition of Done is met, start the next track
  immediately. Do not wait for me to say “next”.
- If a tool/classifier denies a safe, authorised action, retry via an
  allowed equivalent (or leave auto-mode / use the documented bypass path)
  and continue. Record denials in docs/completion/BLOCKERS.md.

HARD STOPS (only these — then document and continue other unblocked work):
1. Never invent, print, commit, or rotate secrets. Never commit .env.
2. Never force-push shared branches. Never rewrite git history on develop/main.
3. Never merge to main / never production-deploy unless I explicitly typed
   “deploy to production now” in this chat.
4. Never create tag phase-9-complete unless I explicitly typed “tag phase-9
   now” OR Tracks A3–A5 evidence is complete and written down.
5. Never claim live OAuth / live AI / live publish without a real run.
   If I must click consent in a browser, write the exact OWNER checklist,
   leave that item OPEN, and continue AGENT work elsewhere.
6. Never rewrite architecture without a written proposal in the PR/docs;
   if CLAUDE.md requires waiting on an architecture rewrite, write the
   proposal and continue non-blocked items — do not idle.

START NOW:
1. git fetch; pin origin/develop; update the pin in
   docs/CLAUDE_REMAINING_ROADMAP.md.
2. Read docs/completion/RESUME_STATE.md, BLOCKERS.md, SCOPE_MATRIX.md,
   PHASE_9_COMPLETION.md, INDEPENDENT_REVIEW_e63508e.md.
3. Execute Track A (agent-owned parts), then B, then C, then D.
4. Keep docs/completion/PROGRESS.md and RESUME_STATE.md current after each
   meaningful step.
5. Stop only when Tracks A–D are Done or every remaining item is an OWNER
   hard-stop with a written checklist — then send me one short status.
```

---

## 4. Honesty rules (non-negotiable)

- Distinguish **verified** vs **written**.
- Do not present StubProvider output as live AI quality.
- Do not mark Draft Editor Stage 8 complete while B-004 is open.
- Report failing gate output; fix or document — do not hide.

---

## 5. Session checklist (agent)

- [x] Pin updated to current `origin/develop` (`7af51f9`, 2026-10-03)
- [x] Track A agent items done or blocked with OWNER checklist (`docs/completion/BLOCKERS.md`)
- [x] Track B provider PR (if keys available) or scaffolding + tests — #46, no key: mocked tests only
- [x] Track C audit / remaining DE items — `PROGRESS.md`; DE-8b blocked on A4/A5
- [x] Track D: only after owner has not paused eBay — start at C2 plan — C2 #48; C3 proposal awaits approval
- [x] PROGRESS.md + RESUME_STATE.md updated
- [ ] No secrets in git status
