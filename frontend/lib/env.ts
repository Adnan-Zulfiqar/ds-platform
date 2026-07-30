import { z } from "zod";

/**
 * Validated environment configuration.
 *
 * Environment variables are strings that may be absent. Parsing them once here
 * turns a whole class of runtime failure — a typo'd URL surfacing as a
 * confusing fetch error deep in a component — into a startup error naming the
 * exact variable.
 *
 * Only `NEXT_PUBLIC_*` variables may appear in this file. Next.js inlines those
 * into the client bundle at build time, so anything referenced here is public
 * by definition. A server-only secret must never be read from this module.
 */
const clientEnvSchema = z.object({
  NEXT_PUBLIC_API_URL: z
    .string()
    .url("NEXT_PUBLIC_API_URL must be a valid URL, e.g. http://localhost:8000")
    .default("http://localhost:8000"),

  NEXT_PUBLIC_API_TIMEOUT_MS: z.coerce
    .number()
    .int()
    .positive()
    .default(30_000),

  NEXT_PUBLIC_APP_NAME: z.string().min(1).default("DropPilot AI"),

  NEXT_PUBLIC_ENVIRONMENT: z
    .enum(["local", "test", "staging", "production"])
    .default("local"),
});

/**
 * Variables must be referenced by their full literal name rather than looked up
 * dynamically. Next.js performs a static find-and-replace at build time, so
 * `process.env[key]` is not substituted and would be undefined in the browser.
 */
const parsed = clientEnvSchema.safeParse({
  NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL,
  NEXT_PUBLIC_API_TIMEOUT_MS: process.env.NEXT_PUBLIC_API_TIMEOUT_MS,
  NEXT_PUBLIC_APP_NAME: process.env.NEXT_PUBLIC_APP_NAME,
  NEXT_PUBLIC_ENVIRONMENT: process.env.NEXT_PUBLIC_ENVIRONMENT,
});

if (!parsed.success) {
  // Fail loudly at module load rather than producing undefined values that
  // surface much later as an unexplained network error.
  const issues = parsed.error.issues
    .map((issue) => `  - ${issue.path.join(".")}: ${issue.message}`)
    .join("\n");
  throw new Error(`Invalid environment configuration:\n${issues}`);
}

export const env = parsed.data;

export const isProduction = env.NEXT_PUBLIC_ENVIRONMENT === "production";
export const isLocal = env.NEXT_PUBLIC_ENVIRONMENT === "local";
