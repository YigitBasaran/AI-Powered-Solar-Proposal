import { test as base, expect } from "@playwright/test";

import { captureConsole, type ConsoleCapture } from "../helpers/console";
import { CalibrationPage } from "../pages/calibration.page";
import { ProposalPage } from "../pages/proposal.page";
import { RoofViewPage } from "../pages/roof-view.page";
import { SolarFlowPage } from "../pages/solar-flow.page";
import { ApiClient } from "./api";
import { readStackMode, type StackMode } from "./environment";

/**
 * The suite's test fixtures.
 *
 * ## Isolation
 *
 * `POST /api/v1/projects` creates a fresh project on every call, and the
 * browser page object starts one on load. So every test already owns its own
 * project and proposal — there is no shared mutable state to reset, and no
 * test-only reset endpoint had to be added to production for the suite's
 * benefit. Nothing here mocks the application; the API client only *reads*
 * what the real backend produced.
 */

type Fixtures = {
  api: ApiClient;
  solarFlow: SolarFlowPage;
  roofView: RoofViewPage;
  proposalPage: ProposalPage;
  calibration: CalibrationPage;
  consoleCapture: ConsoleCapture;
};

type WorkerFixtures = {
  stack: StackMode;
};

export const test = base.extend<Fixtures, WorkerFixtures>({
  stack: [
    async ({}, use, workerInfo) => {
      const baseURL = workerInfo.project.use.baseURL;
      if (!baseURL) throw new Error("baseURL is not configured for this project");
      await use(await readStackMode(baseURL));
    },
    { scope: "worker" },
  ],

  api: async ({ request }, use) => {
    await use(new ApiClient(request));
  },

  // Attached before the page object navigates, so boot-time errors are caught.
  consoleCapture: async ({ page }, use) => {
    await use(captureConsole(page));
  },

  solarFlow: async ({ page, consoleCapture }, use) => {
    void consoleCapture;
    await use(new SolarFlowPage(page));
  },

  roofView: async ({ page }, use) => {
    await use(new RoofViewPage(page));
  },

  proposalPage: async ({ page, consoleCapture }, use) => {
    void consoleCapture;
    await use(new ProposalPage(page));
  },

  calibration: async ({ page }, use) => {
    await use(new CalibrationPage(page));
  },
});

export { expect };
