import fs from "node:fs";
import path from "node:path";

import { expect, type Page } from "@playwright/test";

import { resolveSuiteShotRoot } from "./evidence-paths";

const SHOT_ENV_KEYS = ["UX_L2B_R6_SHOT_ROOT", "UX_L2B_R3_SHOT_ROOT"] as const;

export function resolveShotRoot(): string {
  return resolveSuiteShotRoot("ux-l2b-visual-evidence", SHOT_ENV_KEYS);
}

/**
 * Ensure the evidence directory exists, capture a PNG, and fail if the file
 * is missing or empty. Silent screenshot failures were the R5 evidence gap.
 */
export async function captureEvidenceScreenshot(
  page: Page,
  name: string,
  options?: { fullPage?: boolean; root?: string },
): Promise<string> {
  const root = options?.root ?? resolveShotRoot();
  try {
    fs.mkdirSync(root, { recursive: true });
  } catch (error) {
    throw new Error(
      `Evidence screenshot directory could not be created at "${root}": ${String(error)}`,
    );
  }

  const file = path.join(root, `${name}.png`);
  await page.screenshot({ path: file, fullPage: options?.fullPage ?? true });

  if (!fs.existsSync(file)) {
    throw new Error(`Evidence screenshot was not written: ${file}`);
  }

  const size = fs.statSync(file).size;
  expect(size, `Evidence screenshot must not be empty: ${file}`).toBeGreaterThan(0);

  return file;
}
