import { expect, test } from "@playwright/test";

import { registerViaApi } from "./helpers/catalogue";
import {
  CI_ALEMBIC_HEAD_REVISION,
  CI_E2E_DATABASE_URL,
  CI_E2E_POSTGRES_DB,
  CI_E2E_PYTHON,
} from "./helpers/ci-e2e-database";
import {
  E2eSeedConfigError,
  isCiSeedEnvironment,
  parseE2eDatabaseUrl,
  peekLastSeedSpawnArgvForTests,
  resetLastSeedSpawnArgvForTests,
  resolveE2ePython,
  resolveRepoRoot,
  resolveSeedAvailability,
  runSeedPythonScript,
  validateDatabaseTarget,
} from "./helpers/seed-config";
import { resetSeedConfigCacheForTests, seedDrafts } from "./helpers/seed";

const VALID_URL =
  "postgresql+psycopg://droppilot:droppilot@127.0.0.1:5432/droppilot_e2e";

test.describe("E2E seed harness", () => {
  test.beforeEach(() => {
    resetSeedConfigCacheForTests();
    resetLastSeedSpawnArgvForTests();
  });

  test("missing database configuration skips locally", async () => {
    await expect(
      resolveSeedAvailability({
        E2E_DATABASE_URL: "",
        CI: "false",
      }),
    ).resolves.toEqual({
      available: false,
      reason: expect.stringContaining("E2E_DATABASE_URL"),
    });
  });

  test("CI missing database configuration fails loudly", async () => {
    await expect(
      resolveSeedAvailability({
        E2E_DATABASE_URL: "",
        CI: "true",
        GITHUB_ACTIONS: "true",
      }),
    ).rejects.toThrow(E2eSeedConfigError);
  });

  test("refuses production database name", () => {
    expect(() =>
      validateDatabaseTarget(
        parseE2eDatabaseUrl(
          "postgresql+psycopg://droppilot:droppilot@127.0.0.1:5432/droppilot",
        ),
      ),
    ).toThrow(/refusing production database name "droppilot"/);
  });

  test("refuses remote database host", () => {
    expect(() =>
      validateDatabaseTarget(
        parseE2eDatabaseUrl(
          "postgresql+psycopg://droppilot:droppilot@db.example.com:5432/droppilot_e2e",
        ),
      ),
    ).toThrow(/refusing remote database host/);
  });

  test("refuses placeholder or non-approved database names", () => {
    expect(() =>
      validateDatabaseTarget(
        parseE2eDatabaseUrl(
          "postgresql+psycopg://droppilot:droppilot@127.0.0.1:5432/droppilot_uxl2b_r4_placeholder",
        ),
      ),
    ).toThrow(/refusing placeholder database name/);
  });

  test("accepts approved isolated database names on loopback", () => {
    for (const database of [
      "droppilot_e2e",
      "droppilot_uxl2b_r5_20260731153000",
    ]) {
      expect(() =>
        validateDatabaseTarget(
          parseE2eDatabaseUrl(
            `postgresql+psycopg://droppilot:droppilot@127.0.0.1:5432/${database}`,
          ),
        ),
      ).not.toThrow();
    }
  });

  test("prefers explicit E2E_PYTHON", () => {
    expect(
      resolveE2ePython({ E2E_PYTHON: "/opt/ci/python3" }, resolveRepoRoot()),
    ).toBe("/opt/ci/python3");
  });

  test("resolves repository-relative virtualenv on Windows", () => {
    const repoRoot = resolveRepoRoot();
    const resolved = resolveE2ePython({}, repoRoot);
    if (process.platform === "win32") {
      expect(resolved === "python" || resolved.endsWith("python.exe")).toBe(true);
    } else {
      expect(resolved === "python3" || resolved.endsWith("/bin/python")).toBe(true);
    }
  });

  test("refuses malformed database URL", () => {
    expect(() => parseE2eDatabaseUrl("not-a-database-url")).toThrow(
      E2eSeedConfigError,
    );
  });

  test("wrong current_database identity fails loudly", async () => {
    const python = resolveE2ePython({ E2E_PYTHON: process.env.E2E_PYTHON });
    test.skip(!python, "No Python interpreter available for seed harness test.");

    const availability = await resolveSeedAvailability({
      E2E_DATABASE_URL: VALID_URL.replace("droppilot_e2e", "droppilot_wrong_name"),
      E2E_PYTHON: python,
      CI: "false",
    });

    if (availability.available) {
      expect.fail("Expected refusal for mismatched database identity.");
    } else {
      expect(availability.reason).toMatch(/unavailable|refusing database name/i);
    }
  });

  test("valid isolated configuration succeeds when database is reachable", async () => {
    test.skip(
      !process.env.E2E_DATABASE_URL,
      "Set E2E_DATABASE_URL to an isolated loopback database to exercise live seed resolution.",
    );

    const availability = await resolveSeedAvailability({
      E2E_DATABASE_URL: process.env.E2E_DATABASE_URL,
      E2E_PYTHON: process.env.E2E_PYTHON,
      CI: "false",
    });

    expect(availability.available).toBe(true);
    if (availability.available) {
      expect(availability.config.databaseName).toMatch(/^droppilot_/);
      expect(availability.config.python.length).toBeGreaterThan(0);
    }
  });

  test("CI flag helper requires GitHub Actions", () => {
    expect(
      isCiSeedEnvironment({ CI: "true", GITHUB_ACTIONS: "true" }),
    ).toBe(true);
    expect(isCiSeedEnvironment({ CI: "true" })).toBe(false);
  });

  test("CI Playwright job database URL matches postgres service droppilot_e2e", () => {
    const parsed = parseE2eDatabaseUrl(CI_E2E_DATABASE_URL);
    expect(parsed.database).toBe(CI_E2E_POSTGRES_DB);
    validateDatabaseTarget(parsed);
  });

  test("CI Playwright job E2E_PYTHON resolves to workflow interpreter name", () => {
    expect(resolveE2ePython({ E2E_PYTHON: CI_E2E_PYTHON })).toBe("python");
  });

  test("CI Alembic head revision is documented for workflow verification", () => {
    expect(CI_ALEMBIC_HEAD_REVISION).toBe("0032");
  });

  test("seed spawn argv never contains database URL or password", async () => {
    const python = resolveE2ePython({ E2E_PYTHON: process.env.E2E_PYTHON });
    const secretUrl =
      "postgresql+psycopg://seed_user:seed_secret_pass@127.0.0.1:5432/droppilot_e2e";
    await expect(
      runSeedPythonScript(python, "seed_db_identity.py", {
        databaseUrl: secretUrl,
        expectedDatabase: "droppilot_missing",
      }),
    ).rejects.toThrow();

    const argv = peekLastSeedSpawnArgvForTests();
    expect(argv).toBeDefined();
    const joined = argv!.join(" ");
    expect(joined).not.toContain("seed_secret_pass");
    expect(joined).not.toContain("postgresql+psycopg://");
    expect(joined).not.toContain("droppilot_e2e");
  });

  test("malformed stdin fails safely without leaking credentials", async () => {
    const python = resolveE2ePython({ E2E_PYTHON: process.env.E2E_PYTHON });
    await expect(
      runSeedPythonScript(python, "seed_db_identity.py", {
        notDatabaseUrl: "postgresql+psycopg://x:y@127.0.0.1:5432/droppilot_e2e",
      }),
    ).rejects.toThrow(/missing databaseUrl/i);

    const argv = peekLastSeedSpawnArgvForTests();
    expect(argv!.join(" ")).not.toContain("postgresql+psycopg://");
  });

  test("seed drafts succeeds without credentials in argv when database is configured", async ({
    request,
  }) => {
    test.skip(
      !process.env.E2E_DATABASE_URL,
      "Set E2E_DATABASE_URL to an isolated loopback database to exercise live seeding.",
    );

    const availability = await resolveSeedAvailability({
      E2E_DATABASE_URL: process.env.E2E_DATABASE_URL,
      E2E_PYTHON: process.env.E2E_PYTHON,
      CI: "false",
    });
    test.skip(!availability.available, availability.available ? "" : availability.reason);

    const registered = await registerViaApi(request);
    await seedDrafts(registered.tenantId, 1, {
      prefix: "R7 seed transport",
    });

    const argv = peekLastSeedSpawnArgvForTests();
    expect(argv).toBeDefined();
    const joined = argv!.join(" ");
    expect(joined).not.toMatch(/postgresql(\+\w+)?:\/\//i);
    expect(joined).not.toContain(":droppilot");
  });
});
