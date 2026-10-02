# Cursor independent review — index

Cursor reviews the completed application at the end of implementation
(owner decision, `docs/completion/DECISIONS.md` D-001). Nothing in this
directory records an independent verdict; every checkpoint says
`Independent review: PENDING — NOT YET PERFORMED` until Cursor writes one.

| Item | Value |
|---|---|
| Review baseline (`develop` before this programme) | `72e76921fc80b203133423ad3bd92bd380c7e02b` |
| Candidate | not pinned yet — set at AUT-10 |
| Authoritative ledgers | `docs/REVIEW_REMEDIATION_STAGE_5_9.md` (Stage 5–9 findings); `docs/completion/*` (this programme) |

## Suggested review order (completion roadmap §16.3)

1. Scope/authority: `docs/completion/DECISIONS.md` D-001, D-002; `SCOPE_MATRIX.md`; baseline-to-candidate diff.
2. Tenant/auth/security and dependencies: DP-CR-003…006, DP-CR-008, DP-CR-017; `SECURITY_STATUS.md`.
3. Database, migrations, erasure/recovery: DP-CR-001 (migrations 0035/0036, D-2).
4. Published-content resolution and tokens: DP-CR-001 (E-1, G-2, I-1), DP-CR-012.
5. AI Studio single/bulk: DP-CR-009…014.
6. Workers, retry, cancellation: DP-CR-001 (H-1…H-5).
7. Remaining modules and journeys: DP-CR-021…023.
8. Browser UX, accessibility, flakes: DP-CR-006, DP-CR-015.
9. Release rehearsal and evidence gaps: DP-CR-020+.

## Checkpoints

| ID | Title | Status |
|---|---|---|
| [DP-CR-001](checkpoints/DP-CR-001.md) | Stage 5–9 review remediation (PR #26) | Integrated; IR pending |
| [DP-CR-002](checkpoints/DP-CR-002.md) | CI installs backend from the hash lock (PR #27) | Integrated; IR pending |
| [DP-CR-003](checkpoints/DP-CR-003.md) | Next 15.5.27 / React 19.0.8 + lockfile metadata (PR #28) | Integrated; IR pending |
| [DP-CR-004](checkpoints/DP-CR-004.md) | axios 1.20.0 (PR #29) | Integrated; IR pending |
| [DP-CR-005](checkpoints/DP-CR-005.md) | tiptap 3.31.4, js-yaml, brace-expansion (PR #30) | Integrated; IR pending |
| [DP-CR-006](checkpoints/DP-CR-006.md) | Global-rules dialog waits for the list (PR #31) | Integrated; IR pending |
| [DP-CR-007](checkpoints/DP-CR-007.md) | Local-only encryption key (no repository change) | Done locally; IR pending |
| [DP-CR-008](checkpoints/DP-CR-008.md) | Nested `.env` kept out of Docker contexts (PR #32) | PR open; IR pending |
| DP-CR-009…014 | Stage 10 plan reconciliation and implementation | Reserved |
| DP-CR-015 | Remaining Playwright flakes | Reserved |
| DP-CR-016 | Next.js bundled postcss / postponed fixes | Reserved |
| DP-CR-017 | pyjwt / urllib3 advisories | Branch pushed when gated |
| DP-CR-020…023 | Stage 11; Draft Editor 6–8 | Reserved |
