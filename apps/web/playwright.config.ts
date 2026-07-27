import { defineConfig, devices } from "@playwright/test";

/**
 * E2E runs against the real stack. Both servers must already be running:
 *
 *   cd apps/api && ./.venv/Scripts/python -m uvicorn app.main:app --port 8000
 *   cd apps/web && npm run build && npm start
 *
 * Fixture mode is the intended configuration — it is what a reviewer gets from
 * a clean clone, and it keeps the suite deterministic and offline.
 */
export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 90_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
