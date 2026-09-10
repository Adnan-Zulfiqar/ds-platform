import {
  E2eSeedConfigError,
  resolveSeedAvailability,
  runSeedPythonScript,
  type ResolvedSeedConfig,
} from "./seed-config";

/**
 * Seed draft products directly into the test database.
 *
 * Products only enter this system through the AliExpress import, which needs
 * supplier credentials and a mocked transport — neither of which a browser
 * test can provide. The alternative would be a "create a product" endpoint
 * that exists only for tests, and production API surface built for tests is
 * surface an attacker gets too.
 *
 * So the seeding happens here, in test code, by shelling out to the backend's
 * own interpreter. It writes the same rows an import would leave behind; every
 * assertion afterwards goes through the real API.
 *
 * Database credentials travel over stdin JSON, never argv.
 *
 * Skips the whole file cleanly when the interpreter or database is not
 * configured, rather than failing with a wall of spawn errors — except in CI,
 * where missing seed configuration is a hard failure.
 */

let cachedConfig: ResolvedSeedConfig | null | undefined;

async function loadSeedConfig(): Promise<ResolvedSeedConfig | null> {
  if (cachedConfig !== undefined) {
    return cachedConfig;
  }
  const availability = await resolveSeedAvailability();
  cachedConfig = availability.available ? availability.config : null;
  return cachedConfig;
}

export interface SeedOptions {
  published?: number;
  flagged?: number;
  prefix?: string;
}

export async function seedDrafts(
  tenantId: string,
  count: number,
  options: SeedOptions = {},
): Promise<void> {
  const config = await loadSeedConfig();
  if (!config) {
    throw new E2eSeedConfigError(await explainSeedUnavailable());
  }

  const { stdout } = await runSeedPythonScript(config.python, "seed_drafts.py", {
    databaseUrl: config.databaseUrl,
    tenantId,
    count,
    published: options.published ?? 0,
    flagged: options.flagged ?? 0,
    prefix: options.prefix ?? "Impact draft",
  });

  if (!stdout.includes("seeded")) {
    throw new E2eSeedConfigError("Seeding did not confirm success.");
  }
}

/** Human-readable reason when seeding is unavailable locally (for test.skip). */
export async function explainSeedUnavailable(): Promise<string> {
  const availability = await resolveSeedAvailability();
  if (availability.available) {
    return "Seed configuration is available.";
  }
  return availability.reason;
}

/** Whether seeding can run at all, so the suite skips instead of erroring locally. */
export async function canSeed(): Promise<boolean> {
  const availability = await resolveSeedAvailability();
  return availability.available;
}

/** Test-only: clear cached seed resolution between hermetic cases. */
export function resetSeedConfigCacheForTests(): void {
  cachedConfig = undefined;
}

export { E2eSeedConfigError };
