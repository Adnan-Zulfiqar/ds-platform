import path from "node:path";

/**
 * Repository-relative default for visual evidence PNGs.
 *
 * Keeps tracked defaults free of developer-specific absolute paths while still
 * allowing env overrides for local evidence collection outside Git.
 */
export function resolveSuiteShotRoot(
  suiteName: string,
  envKeys: readonly string[] = [],
): string {
  for (const key of envKeys) {
    const value = process.env[key]?.trim();
    if (value) {
      return value;
    }
  }
  return path.join(process.cwd(), "test-results", suiteName);
}
