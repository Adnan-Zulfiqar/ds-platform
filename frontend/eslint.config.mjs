import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

import { FlatCompat } from "@eslint/eslintrc";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// ESLint 9 uses flat config, while eslint-config-next still ships the legacy
// shareable-config format. FlatCompat bridges the two.
const compat = new FlatCompat({ baseDirectory: __dirname });

const eslintConfig = [
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  {
    ignores: [
      ".next/**",
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
    },
  },
];

export default eslintConfig;
