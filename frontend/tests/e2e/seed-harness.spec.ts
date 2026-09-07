import { expect, test } from "@playwright/test";

import {
  E2eSeedConfigError,
  isCiSeedEnvironment,
  parseE2eDatabaseUrl,
  resolveE2ePython,
  resolveRepoRoot,
  resolveSeedAvailability,
  validateDatabaseTarget,
} from "./helpers/seed-config";
import { resetSeedConfigCacheForTests } from "./helpers/seed";

const VALID_URL =
  "postgresql+psycopg://droppilot:droppilot@127.0.0.1:5432/droppilot_e2e";

test.describe("E2E seed harness", () => {
  test.beforeEach(() => {
    resetSeedConfigCacheForTests();
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
});
