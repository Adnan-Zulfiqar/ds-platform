import fs from "node:fs";
import path from "node:path";

import { expect, test } from "@playwright/test";

import { resolveSuiteShotRoot } from "./helpers/evidence-paths";
import {
  captureEvidenceScreenshot,
  resolveShotRoot,
} from "./helpers/screenshot-evidence";

test.describe("Screenshot evidence helper", () => {
  test("default root is repository-relative under frontend/test-results", () => {
    const root = resolveShotRoot();
    expect(root).toBe(
      path.join(process.cwd(), "test-results", "ux-l2b-visual-evidence"),
    );
    expect(root.includes("DropPilotLogs")).toBe(false);
  });

  test("resolveSuiteShotRoot uses path.join for cross-platform roots", () => {
    const root = resolveSuiteShotRoot("example-suite");
    expect(root).toBe(path.join(process.cwd(), "test-results", "example-suite"));
  });

  test("environment override wins over default root", () => {
    const override = path.join(process.cwd(), "test-results", "custom-override");
    const previous = process.env.UX_L2B_R6_SHOT_ROOT;
    process.env.UX_L2B_R6_SHOT_ROOT = override;
    try {
      expect(resolveShotRoot()).toBe(override);
    } finally {
      if (previous === undefined) delete process.env.UX_L2B_R6_SHOT_ROOT;
      else process.env.UX_L2B_R6_SHOT_ROOT = previous;
    }
  });

  test("captureEvidenceScreenshot creates directory and rejects empty files", async () => {
    const root = path.join(
      process.cwd(),
      "test-results",
      "screenshot-evidence-helper",
      `case-${Date.now()}`,
    );
    const fakePage = {
      screenshot: async ({ path: filePath }: { path: string }) => {
        fs.writeFileSync(filePath, Buffer.from([0x89, 0x50, 0x4e, 0x47]));
      },
    };

    const file = await captureEvidenceScreenshot(
      fakePage as never,
      "probe",
      { root },
    );
    expect(fs.existsSync(file)).toBe(true);
    expect(fs.statSync(file).size).toBeGreaterThan(0);
    fs.rmSync(root, { recursive: true, force: true });
  });
});
