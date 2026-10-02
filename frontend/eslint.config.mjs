import nextCoreWebVitals from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";

// eslint-config-next 16 ships native flat configs; the FlatCompat bridge the
// 15.x legacy format needed is gone (and fails on 16 with a circular plugin).
const eslintConfig = [
  ...nextCoreWebVitals,
  ...nextTypescript,
  {
    ignores: [
      ".next/**",
      ".next-r6/**",
      ".next-r7/**",
      "node_modules/**",
      "out/**",
      "next-env.d.ts",
      "playwright-report/**",
      "test-results/**",
    ],
  },
  {
    rules: {
      // An unused variable is usually a leftover from a refactor. The
      // underscore prefix is the documented escape hatch for a deliberately
      // ignored binding, such as an unused destructured field.
      "@typescript-eslint/no-unused-vars": [
        "error",
        {
          argsIgnorePattern: "^_",
          varsIgnorePattern: "^_",
          caughtErrorsIgnorePattern: "^_",
        },
      ],
      // `any` discards the type safety this codebase is configured for. Warn
      // rather than error so it can be used briefly during development, but it
      // stays visible in review.
      "@typescript-eslint/no-explicit-any": "warn",
      // New in eslint-config-next 16 (React Compiler-era react-hooks rules).
      // They flag 23 existing call sites in 13 files that pass review and
      // tests today. Kept visible as warnings so the security upgrade that
      // introduced them stays a focused change; fixing those sites is
      // tracked separately (docs/completion/DECISIONS.md D-009). No rule that
      // existed before the upgrade is relaxed.
      "react-hooks/refs": "warn",
      "react-hooks/set-state-in-effect": "warn",
      "react-hooks/purity": "warn",
    },
  },
];

export default eslintConfig;
