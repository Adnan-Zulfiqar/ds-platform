import { execFile, spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { promisify } from "node:util";

const run = promisify(execFile);
// Playwright executes from `frontend/`; keep paths cwd-relative for CJS transpile.
const HELPERS_DIR = path.join(process.cwd(), "tests", "e2e", "helpers");

export type SeedStdinPayload = Record<string, unknown>;

let lastSpawnArgv: string[] | undefined;

export function peekLastSeedSpawnArgvForTests(): string[] | undefined {
  return lastSpawnArgv;
}

export function resetLastSeedSpawnArgvForTests(): void {
  lastSpawnArgv = undefined;
}

export function resolveSeedScriptPath(scriptName: string): string {
  return path.join(HELPERS_DIR, scriptName);
}

export async function runSeedPythonScript(
  python: string,
  scriptName: string,
  payload: SeedStdinPayload,
  options?: { timeoutMs?: number },
): Promise<{ stdout: string; stderr: string; argv: string[] }> {
  const scriptPath = resolveSeedScriptPath(scriptName);
  const argv = [python, scriptPath];
  lastSpawnArgv = argv;
  const timeoutMs = options?.timeoutMs ?? 120_000;
  const stdinBody = JSON.stringify(payload);

  return new Promise((resolve, reject) => {
    const child = spawn(python, [scriptPath], {
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true,
    });
    let stdout = "";
    let stderr = "";
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      child.kill();
      reject(new Error(`Seed script timed out after ${timeoutMs}ms.`));
    }, timeoutMs);
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk: string) => {
      stderr += chunk;
    });
    child.on("error", (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(error);
    });
    child.on("close", (code) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (code === 0) {
        resolve({ stdout, stderr, argv });
        return;
      }
      reject(new Error((stderr || stdout || `exit ${code}`).slice(0, 500)));
    });
    child.stdin.write(stdinBody);
    child.stdin.end();
  });
}

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

/** Repository root when Playwright executes from `frontend/`. */
export function resolveRepoRoot(): string {
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
): string {
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
  const { stdout } = await runSeedPythonScript(python, "seed_db_identity.py", {
    databaseUrl,
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
  resetLastSeedSpawnArgvForTests();
}
