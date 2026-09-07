import { execFile } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const run = promisify(execFile);

/** Configuration / isolation failure — must not be treated as a quiet skip in CI. */
export class E2eSeedConfigError extends Error {
  override readonly name = "E2eSeedConfigError";
}

const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "::1"]);
const FORBIDDEN_DATABASE_NAMES = new Set(["droppilot"]);
const APPROVED_DATABASE_PATTERN = /^droppilot_[a-z0-9][a-z0-9_]*$/i;

const SEED_IMPORT_CHECK = "import sqlalchemy, psycopg";

export type ParsedDatabaseTarget = {
  url: string;
  host: string;
  port: string;
  database: string;
};

export type ResolvedSeedConfig = {
  python: string;
  databaseUrl: string;
  databaseName: string;
};

export type SeedAvailability =
  | { available: true; config: ResolvedSeedConfig }
  | { available: false; reason: string };

function refuse(message: string): never {
  throw new E2eSeedConfigError(message);
}

export function isCiSeedEnvironment(env: NodeJS.ProcessEnv = process.env): boolean {
  return env.CI === "true" && env.GITHUB_ACTIONS === "true";
}

/** Repository root from this module's location (`frontend/tests/e2e/helpers`). */
export function resolveRepoRoot(moduleUrl?: string): string {
  if (moduleUrl) {
    return path.resolve(path.dirname(fileURLToPath(moduleUrl)), "../../../..");
  }
  // Playwright executes from `frontend/`; the repository root is one level up.
  return path.resolve(process.cwd(), "..");
}

export function parseE2eDatabaseUrl(raw: string): ParsedDatabaseTarget {
  const trimmed = raw.trim();
  if (!trimmed) {
    refuse("E2E seed: E2E_DATABASE_URL is missing.");
  }

  let parsed: URL;
  try {
    parsed = new URL(trimmed.replace(/^postgresql(\+\w+)?:/, "http:"));
  } catch {
    refuse(`E2E seed: refusing malformed E2E_DATABASE_URL.`);
  }

  const database = decodeURIComponent(parsed.pathname.replace(/^\//, "")).trim();
  if (!database) {
    refuse("E2E seed: E2E_DATABASE_URL must include a database name.");
  }

  const host = parsed.hostname.trim().toLowerCase();
  const port = parsed.port || "5432";

  return {
    url: trimmed,
    host,
    port,
    database,
  };
}

export function validateDatabaseTarget(target: ParsedDatabaseTarget): void {
  if (!LOOPBACK_HOSTS.has(target.host)) {
    refuse(
      `E2E seed: refusing remote database host "${target.host}". ` +
        "Only loopback hosts (127.0.0.1, localhost, ::1) are allowed.",
    );
  }

  if (FORBIDDEN_DATABASE_NAMES.has(target.database)) {
    refuse(
      `E2E seed: refusing production database name "${target.database}". ` +
        "Use an isolated test database such as droppilot_e2e.",
    );
  }

  if (!APPROVED_DATABASE_PATTERN.test(target.database)) {
    refuse(
      `E2E seed: refusing database name "${target.database}". ` +
        "Expected an isolated test name matching droppilot_<suffix> " +
        "(for example droppilot_e2e or droppilot_uxl2b_r5_<timestamp>).",
    );
  }

  if (target.database.includes("placeholder")) {
    refuse(
      `E2E seed: refusing placeholder database name "${target.database}". ` +
        "Configure a real isolated database that exists for this run.",
    );
  }
}

export function resolveE2ePython(
  env: NodeJS.ProcessEnv = process.env,
  repoRoot: string = resolveRepoRoot(),
): string | null {
  const explicit = env.E2E_PYTHON?.trim();
  if (explicit) {
    return explicit;
  }

  const venvCandidates =
    os.platform() === "win32"
      ? [path.join(repoRoot, "backend", ".venv", "Scripts", "python.exe")]
      : [path.join(repoRoot, "backend", ".venv", "bin", "python")];

  for (const candidate of venvCandidates) {
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }

  return os.platform() === "win32" ? "python" : "python3";
}

async function pythonCommandExists(python: string): Promise<boolean> {
  try {
    await run(python, ["-c", SEED_IMPORT_CHECK], { timeout: 15_000 });
    return true;
  } catch {
    return false;
  }
}

async function readCurrentDatabase(python: string, databaseUrl: string): Promise<string> {
  const script = `
import sys
import sqlalchemy as sa

engine = sa.create_engine(sys.argv[1])
with engine.connect() as connection:
    print(connection.execute(sa.text("select current_database()")).scalar())
`;
  const { stdout } = await run(python, ["-c", script, databaseUrl], {
    timeout: 20_000,
  });
  return stdout.trim();
}

function sanitizeSeedError(error: unknown): string {
  if (error instanceof E2eSeedConfigError) {
    return error.message;
  }
  if (error instanceof Error) {
    const message = error.message.replace(/postgresql(\+\w+)?:\/\/[^\s'"]+/gi, "[database-url]");
    return message.slice(0, 500);
  }
  return "Unknown seed configuration error.";
}

/**
 * Resolve and validate the seed harness configuration.
 *
 * - Unsafe or production-looking targets always throw.
 * - In CI, missing or unreachable configuration throws (no green skip).
 * - Locally, missing or unreachable configuration returns `{ available: false }`.
 */
export async function resolveSeedAvailability(
  env: NodeJS.ProcessEnv = process.env,
  repoRoot: string = resolveRepoRoot(),
): Promise<SeedAvailability> {
  const ci = isCiSeedEnvironment(env);
  const rawDatabaseUrl = env.E2E_DATABASE_URL?.trim();

  if (!rawDatabaseUrl) {
    const reason =
      "DB draft seeding unavailable — set E2E_DATABASE_URL to an isolated loopback test database.";
    if (ci) {
      refuse(`E2E seed: ${reason}`);
    }
    return { available: false, reason };
  }

  let target: ParsedDatabaseTarget;
  try {
    target = parseE2eDatabaseUrl(rawDatabaseUrl);
    validateDatabaseTarget(target);
  } catch (error) {
    if (error instanceof E2eSeedConfigError) {
      throw error;
    }
    refuse("E2E seed: refusing malformed E2E_DATABASE_URL.");
  }

  const python = resolveE2ePython(env, repoRoot);
  if (!python) {
    const reason =
      "DB draft seeding unavailable — set E2E_PYTHON or install backend dependencies in backend/.venv.";
    if (ci) {
      refuse(`E2E seed: ${reason}`);
    }
    return { available: false, reason };
  }

  if (!(await pythonCommandExists(python))) {
    const reason =
      "DB draft seeding unavailable — Python cannot import sqlalchemy and psycopg for seeding.";
    if (ci) {
      refuse(`E2E seed: ${reason} (interpreter: ${python}).`);
    }
    return { available: false, reason };
  }

  try {
    const currentDatabase = await readCurrentDatabase(python, target.url);
    if (currentDatabase !== target.database) {
      refuse(
        `E2E seed: connected database "${currentDatabase}" does not match ` +
          `configured "${target.database}".`,
      );
    }
  } catch (error) {
    if (error instanceof E2eSeedConfigError) {
      throw error;
    }
    const detail = sanitizeSeedError(error);
    const reason = `DB draft seeding unavailable — could not reach isolated database (${detail}).`;
    if (ci) {
      refuse(`E2E seed: ${reason}`);
    }
    return { available: false, reason };
  }

  return {
    available: true,
    config: {
      python,
      databaseUrl: target.url,
      databaseName: target.database,
    },
  };
}

/** Test-only helpers */
export function resetSeedDiagnosticsForTests(): void {
  // Reserved for future once-per-process logging, mirroring auth helpers.
}
