import { defineConfig, devices } from "@playwright/test";

const BASE_URL = process.env.E2E_BASE_URL ?? "http://localhost:3000";

export default defineConfig({
  testDir: "./tests/e2e",

  // Fail the run if a `test.only` was committed. Without this, one focused test
  // silently disables the entire suite in CI while still reporting green.
  forbidOnly: Boolean(process.env.CI),

  // Retry in CI only. Locally a flake should be visible and fixed, not hidden
  // behind a retry.
  retries: process.env.CI ? 2 : 0,

  // A single worker in CI: shared runners are slow and parallel browsers cause
  // timeout flakes that look like real failures.
  workers: process.env.CI ? 1 : undefined,

  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : [["list"]],

  use: {
    baseURL: BASE_URL,
    // Artefacts on first retry only — capturing them for every passing test is
    // gigabytes of storage for nothing.
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },

  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    // Mobile viewport is a first-class target: the responsive layout is a
    // stated requirement, so it needs to be exercised rather than assumed.
    { name: "mobile-chrome", use: { ...devices["Pixel 7"] } },
  ],

  // Start the app automatically unless one is already running.
  webServer: {
    command: "npm run start",
    url: BASE_URL,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
