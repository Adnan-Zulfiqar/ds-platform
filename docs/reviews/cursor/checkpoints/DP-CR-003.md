# DP-CR-003 — Next.js 15.5.27 / React 19.0.8 and lockfile metadata (PR #28)

- **Original IDs:** N-3; completion roadmap §2.3 (#28 had lost 36 `libc` entries).
- **Base / head:** `72e7692` → `e2095f589fb067e25638c9aa82f00a4d86e7d4d9`; merge `249e18f`.
- **Change:** next 15.5.27, eslint-config-next 15.5.27, react/react-dom 19.0.8. Root `app/loading.tsx` removed: on Next 15.5 it made /privacy and /terms stream into a hidden div, so they were blank without JavaScript. `e2095f5` restores the `libc` metadata (lock rebuilt from develop's with npm 11; every version and integrity hash identical).
- **Design choice:** D-003.
- **Security:** `docs/completion/SECURITY_STATUS.md`. Bundled postcss 8.4.31 remains (needs Next 16; DP-CR-016).
- **Evidence:** PR CI 36933835735 at `e2095f5`: 10/10, Playwright 722 passed / 9 skipped / 0 flaky. `npm ci` on node:22-alpine and node:22-bookworm-slim installs only the musl / gnu native binary respectively and does not rewrite the lockfile.
- **Limitations:** two postponed upstream Next.js fixes with no public detail.
- **Recovery:** revert the merge; `(app)/(protected)/loading.tsx` is unaffected.
- **Cursor must inspect:** no-JS rendering of public pages; that removing the root `loading.tsx` lost no needed loading state.
- **Author verification:** PASS.
- **Independent review:** PENDING — NOT YET PERFORMED
