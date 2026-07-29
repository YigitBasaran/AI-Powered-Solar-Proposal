import { defineConfig, devices } from "@playwright/test";

import {
  DEGRADED_ENABLED,
  EXTERNAL_TARGET,
  PORTS,
  PRIMARY_BASE_URL,
  PVGIS_DOWN_ENABLED,
  URLS,
} from "./e2e/ports";

/**
 * E2E configuration.
 *
 * The suite **owns its servers**. Both stacks are started by `webServer` with
 * `reuseExistingServer: false`, on ports well clear of 3000/8000, each with its
 * own temporary SQLite database. There is no step where a human has to remember
 * to start something first — a suite with a hidden dependency on a manually
 * launched process eventually reports a pass for a stack nobody was running.
 *
 * The one exception is explicit and total: `E2E_TARGET_URL` runs against a
 * stack the suite does not own (the Docker Compose containers, say) and starts
 * *no* servers at all, so it can never be half of one and half of the other.
 *
 * ## Two stacks
 *
 * | Tier | Stack | Configuration |
 * |------|-------|---------------|
 * | A — deterministic | :3100 / :8100 | every external dependency is a committed fixture |
 * | B — degraded      | :3101 / :8101 | PVGIS, FX and Ollama point at dead endpoints |
 * | C — live          | :3100 / :8100 | `@live`, skips itself unless the stack really is live |
 *
 * PVGIS and FX are called by the backend, not the browser, so Playwright's
 * route interception cannot reach them. Tier B is the only honest way to test
 * those fallbacks: a second stack genuinely configured to fail.
 *
 * ## Parallelism and SQLite
 *
 * One API process serves all workers, so they share one SQLite file.
 * `fullyParallel` is therefore **off**: files run in parallel, tests inside a
 * file run in order in a single worker, which serialises each workflow's own
 * writes. The engine additionally runs SQLite in WAL mode with a bounded
 * `busy_timeout` (`apps/api/app/db/session.py`), so a concurrent writer waits
 * rather than failing instantly with "database is locked".
 *
 * Per-worker databases would need a per-worker API *and* web server, because
 * Next bakes the API origin into its build. That is four-plus Next builds to
 * remove contention that WAL already handles. Reliability was the goal, not
 * maximum parallelism.
 */

const IS_CI = Boolean(process.env.CI);

/**
 * `E2E_TARGET_URL` runs the suite against a stack it does not own — the Docker
 * Compose containers, for instance. No `webServer` is started, so there is no
 * chance of half-attaching to a mixture of the two.
 */
const webServers = EXTERNAL_TARGET !== null ? [] : [
  {
    // Started first: the application has no PVGIS fixture mode, so every API
    // stack below makes a real HTTP call and this is what answers it.
    command: `node e2e/scripts/serve-pvgis-stub.mjs --port ${PORTS.pvgisStub}`,
    url: `${URLS.pvgisStub}/__stub/health`,
    reuseExistingServer: false,
    timeout: 60_000,
    stdout: "pipe" as const,
    stderr: "pipe" as const,
  },
  {
    command: `node e2e/scripts/serve-api.mjs --port ${PORTS.api} --db e2e-deterministic.db --mode deterministic`,
    url: `${URLS.api}/api/v1/health/live`,
    reuseExistingServer: false,
    timeout: 120_000,
    stdout: "pipe" as const,
    stderr: "pipe" as const,
  },
  {
    command: `node e2e/scripts/serve-web.mjs --port ${PORTS.web} --api ${URLS.api} --dist .next`,
    url: URLS.web,
    reuseExistingServer: false,
    // Includes a production `next build` from cold.
    timeout: 420_000,
    stdout: "pipe" as const,
    stderr: "pipe" as const,
  },
];

if (DEGRADED_ENABLED) {
  webServers.push(
    {
      command: `node e2e/scripts/serve-api.mjs --port ${PORTS.degradedApi} --db e2e-degraded.db --mode degraded`,
      url: `${URLS.degradedApi}/api/v1/health/live`,
      reuseExistingServer: false,
      timeout: 120_000,
      stdout: "pipe" as const,
      stderr: "pipe" as const,
    },
    {
      command:
        `node e2e/scripts/serve-web.mjs --port ${PORTS.degradedWeb} --api ${URLS.degradedApi} ` +
        `--dist .next-degraded --after ${URLS.web}`,
      url: URLS.degradedWeb,
      reuseExistingServer: false,
      // Waits for the first build to finish, then runs its own.
      timeout: 840_000,
      stdout: "pipe" as const,
      stderr: "pipe" as const,
    },
  );
}

if (PVGIS_DOWN_ENABLED) {
  // API only. Its whole job is the PVGIS-unavailable path, so it needs no
  // browser and no Next build - about a second to start.
  webServers.push({
    command:
      `node e2e/scripts/serve-api.mjs --port ${PORTS.pvgisDownApi} ` +
      `--db e2e-pvgis-down.db --mode pvgis-down`,
    url: `${URLS.pvgisDownApi}/api/v1/health/live`,
    reuseExistingServer: false,
    timeout: 120_000,
    stdout: "pipe" as const,
    stderr: "pipe" as const,
  });
}

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./test-results",
  globalSetup: "./e2e/global-setup.ts",
  globalTeardown: "./e2e/global-teardown.ts",

  timeout: 120_000,
  expect: { timeout: 15_000 },

  // See the note above: parallel across files, serial within a file.
  fullyParallel: false,
  workers: IS_CI ? 2 : 3,

  // A retry masks flake, so retries are for CI only — locally a flaky test
  // should be seen and fixed rather than quietly passed on the second go.
  retries: IS_CI ? 1 : 0,
  forbidOnly: IS_CI,

  reporter: IS_CI
    ? [["list"], ["html", { open: "never", outputFolder: "playwright-report" }], ["github"]]
    : [["list"], ["html", { open: "never", outputFolder: "playwright-report" }]],

  use: {
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    actionTimeout: 20_000,
    navigationTimeout: 45_000,
  },

  projects: [
    {
      name: "chromium",
      testIgnore: [/degraded\/.*/, /pvgis-failure\.spec\.ts/],
      use: { ...devices["Desktop Chrome"], baseURL: PRIMARY_BASE_URL },
    },
    {
      // Smoke only. The brief warns against multiplying a heavy suite across a
      // browser matrix; what matters on mobile is that the flow is usable, and
      // that is a handful of tests, not all of them.
      name: "mobile-chromium",
      testIgnore: [/degraded\/.*/, /pvgis-failure\.spec\.ts/],
      grep: /@mobile/,
      use: { ...devices["Pixel 7"], baseURL: PRIMARY_BASE_URL },
    },
    ...(PVGIS_DOWN_ENABLED
      ? [
          {
            // API-only, so no browser and no baseURL. Its specs use the `api`
            // fixture against a stack whose PVGIS never answers.
            name: "pvgis-down",
            testMatch: /pvgis-failure\.spec\.ts/,
            timeout: 120_000,
            use: { baseURL: URLS.pvgisDownApi },
          },
        ]
      : []),
    ...(DEGRADED_ENABLED
      ? [
          {
            name: "degraded",
            testMatch: /degraded\/.*\.spec\.ts/,
            // A stack whose dependencies are unreachable is *supposed* to be
            // slow: every PVGIS and FX call burns its full retry budget before
            // falling back. One analysis takes ~25 s here against well under a
            // second on the healthy stack. These are longer waits for a slower
            // stack, not looser assertions — every expectation is identical.
            timeout: 240_000,
            use: {
              ...devices["Desktop Chrome"],
              baseURL: URLS.degradedWeb,
              actionTimeout: 90_000,
            },
          },
        ]
      : []),
  ],

  webServer: webServers,
});
