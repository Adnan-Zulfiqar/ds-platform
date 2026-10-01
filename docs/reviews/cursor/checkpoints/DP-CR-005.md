# DP-CR-005 — tiptap 3.31.4, js-yaml 4.3.2, brace-expansion (PR #30)

- **Base / head:** `72e7692` → `23daed7ea973685fc4d59dc348221e3a53bccd84` (contains #29, merged in to pre-resolve adjacent `package.json` lines); merge `4182219`.
- **Change:** @tiptap/* ^3.30.6 resolving to 3.31.4; prosemirror-model 1.25.12 and prosemirror-view 1.42.6 move with @tiptap/pm; js-yaml and brace-expansion lockfile-only. No `--legacy-peer-deps`, no override.
- **Results:** PR CI 36886931099 attempt 2: 10/10, Playwright 722 passed / 9 skipped / 0 flaky. Attempt 1: the Playwright job hit its 35-minute timeout inside `npx playwright install --with-deps chromium`; no test ran.
- **Cursor must inspect:** description-editor hydration and paste sanitisation after the prosemirror bump.
- **Author verification:** PASS (CI).
- **Independent review:** PENDING — NOT YET PERFORMED
