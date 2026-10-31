import { defineConfig, devices } from "@playwright/test";

/**
 * See https://playwright.dev/docs/test-configuration.
 *
 * With PLAYWRIGHT_TEST_BASE_URL set the tests run against an already running stack and no dev
 * server is started.
 */
const externalBaseURL = process.env.PLAYWRIGHT_TEST_BASE_URL;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  // A full run compiles, tests natively and verifies on the simulated board.
  timeout: externalBaseURL ? 240_000 : 30_000,
  expect: { timeout: externalBaseURL ? 20_000 : 5_000 },
  // Against a running stack the output goes to a directory the container mounts from the host.
  outputDir: externalBaseURL ? "e2e-output/results" : "test-results",
  reporter: [
    ["list"],
    [
      "html",
      {
        open: "never",
        outputFolder: externalBaseURL
          ? "e2e-output/report"
          : "playwright-report",
      },
    ],
  ],
  use: {
    baseURL: externalBaseURL || "http://localhost:3000",
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: externalBaseURL
    ? undefined
    : {
        command: "pnpm dev",
        url: "http://localhost:3000",
        reuseExistingServer: !process.env.CI,
        timeout: 120000,
      },
});
