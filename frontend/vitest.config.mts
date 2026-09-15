import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

/**
 * Unit tests for the pure lifecycle modules under `lib/` (UX-L2D-05).
 *
 * Deliberately narrow: `environment: "node"` and only the `lib/` test files,
 * because the functions under test take plain objects and return plain
 * objects — no DOM, no React, no network. Component behaviour stays in
 * Playwright, which exercises the built application rather than a jsdom
 * approximation of it. Not wired into CI in this milestone; run locally with
 * `npm run test:unit`.
 *
 * `.mts` because the package is CommonJS and Vite loads an ESM config from a
 * `.ts` file with a deprecation warning.
 */
export default defineConfig({
  test: {
    environment: "node",
    include: ["lib/**/*.test.ts"],
  },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL(".", import.meta.url)),
    },
  },
});
