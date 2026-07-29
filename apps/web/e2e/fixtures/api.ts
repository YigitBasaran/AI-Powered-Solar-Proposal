import type { APIRequestContext } from "@playwright/test";

/**
 * Typed backend client for assertions that the DOM cannot express.
 *
 * Panel containment, non-overlap and layout determinism are geometry
 * questions. Answering them from pixels would be a screenshot heuristic; the
 * API already publishes the real polygons the backend placed, so the tests
 * assert against those. The application is never mocked — this only *reads*.
 */

export type Point = { x: number; y: number };

export type AnalysisPayload = {
  roof: {
    pitchDeg: number;
    groundMetresPerSourcePixel: number;
    totalProjectedAreaM2: number;
    totalSlopedAreaM2: number;
    facets: {
      id: string;
      label: string;
      compassAzimuthDeg: number;
      pvgisAspectDeg: number;
      projectedAreaM2: number;
      slopedAreaM2: number;
    }[];
    edges: {
      id: string;
      type: "eave" | "hip" | "ridge";
      projectedLengthM: number;
      true3dLengthM: number | null;
    }[];
    facetGeometry?: { id: string; sourcePixelPolygon: Point[] }[];
  };
  layout: {
    requestedSystemSizeKwp: number;
    requestedPanelCount: number;
    placedPanelCount: number;
    feasibleSystemSizeKwp: number;
    capacityWarning: string | null;
    facets: {
      facetId: string;
      orientation: string;
      panelCount: number;
      panels: { id: string; sourcePixelPolygon: Point[] }[];
    }[];
  };
  energy: {
    totalAnnualProductionKwh: number;
    totalMonthlyProductionKwh: number[];
    installedPowerKwp: number;
    dataSource: string;
    radiationDatabase: string | null;
    facets: {
      facetId: string;
      panelCount: number;
      installedPowerKwp: number;
      pvgisAspectDeg: number;
      annualProductionKwh: number;
      specificYieldKwhPerKwp: number;
      monthlyProductionKwh: number[];
      dataSource: string;
    }[];
  };
  exchangeRate: {
    rate: string;
    rateDate: string;
    baseCurrency: string;
    quoteCurrency: string;
    sourceApi: string;
    dataProvider: string;
    retrievalSource: string;
    isLive: boolean;
    isFixture: boolean;
  };
  financial: {
    annualConsumptionKwh: number;
    annualProductionKwh: number;
    coveredEnergyKwh: number;
    coveragePercent: number;
    electricityPriceEurPerKwh: string;
    annualSavingsEur: string;
    originalCapex: { amount: string; currency: string };
    convertedCapex: { amount: string; currency: string };
    simplePaybackYears: number | null;
    twentyYearNetBenefitEur: string;
    cashFlow: { year: number; annualSavingsEur: string; cumulativeCashFlowEur: string }[];
  };
  meta?: { rawLocationInput: string | null; location: { latitude: number; longitude: number } };
};

export class ApiClient {
  constructor(
    private readonly request: APIRequestContext,
    private readonly base = "/api/v1",
  ) {}

  async health() {
    const response = await this.request.get(`${this.base}/health/ready`);
    return { status: response.status(), body: await response.json() };
  }

  async caseLocation() {
    return (await this.request.get(`${this.base}/health/case-location`)).json();
  }

  async mapConfig() {
    return (await this.request.get(`${this.base}/maps/config`)).json();
  }

  async roofModel() {
    return (await this.request.get(`${this.base}/roof/fixed-model`)).json();
  }

  /** A fresh project per call — this is how tests stay isolated. */
  async createProject(): Promise<{ projectId: string; currentStep: string }> {
    const response = await this.request.post(`${this.base}/projects`);
    if (response.status() !== 201) {
      throw new Error(`createProject failed: ${response.status()} ${await response.text()}`);
    }
    return response.json();
  }

  async chat(projectId: string, message: string) {
    const response = await this.request.post(`${this.base}/projects/${projectId}/chat`, {
      data: { message },
    });
    return { status: response.status(), body: await response.json() };
  }

  /** Run the analysis expecting it to fail, and return the error envelope. */
  async runAnalysisExpectingFailure(projectId: string) {
    const response = await this.request.post(`${this.base}/projects/${projectId}/run-analysis`);
    if (response.status() < 400) {
      throw new Error(
        `run-analysis unexpectedly succeeded (${response.status()}); the stack under ` +
          `test is supposed to have an unreachable PVGIS`,
      );
    }
    return { status: response.status(), body: await response.json() };
  }

  /** The stored project, for asserting what a message did *not* write. */
  /** Finalise, expecting a refusal. Returns the status and the error body. */
  async finalizeExpectingFailure(projectId: string) {
    const response = await this.request.post(`${this.base}/projects/${projectId}/finalize`);
    return { status: response.status(), body: await response.json() };
  }

  async project(projectId: string) {
    const response = await this.request.get(`${this.base}/projects/${projectId}`);
    if (response.status() !== 200) {
      throw new Error(`project failed: ${response.status()} ${await response.text()}`);
    }
    return response.json();
  }

  /**
   * Chat, insisting the step actually advanced.
   *
   * A silently-swallowed intake failure surfaces later as an unrelated 409
   * from `run-analysis` — a confusing symptom two calls away from its cause.
   * Setup steps fail where they fail.
   */
  private async chatOrThrow(projectId: string, message: string): Promise<void> {
    const { status, body } = await this.chat(projectId, message);
    if (status !== 200) {
      throw new Error(`chat("${message}") failed: ${status} ${JSON.stringify(body)}`);
    }
    if (body.accepted !== true) {
      throw new Error(
        `chat("${message}") was not accepted at step ${body.currentStep}: ${body.assistantMessage}`,
      );
    }
  }

  async runAnalysis(projectId: string): Promise<AnalysisPayload> {
    const response = await this.request.post(`${this.base}/projects/${projectId}/run-analysis`);
    if (response.status() !== 200) {
      throw new Error(`runAnalysis failed: ${response.status()} ${await response.text()}`);
    }
    return (await response.json()).analysis as AnalysisPayload;
  }

  async finalize(projectId: string) {
    const response = await this.request.post(`${this.base}/projects/${projectId}/finalize`);
    return { status: response.status(), body: await response.json() };
  }

  async proposal(token: string) {
    const response = await this.request.get(`${this.base}/proposals/${token}`);
    return { status: response.status(), body: await response.json() };
  }

  async proposalPdf(token: string) {
    const response = await this.request.get(`${this.base}/proposals/${token}/pdf`);
    return {
      status: response.status(),
      contentType: response.headers()["content-type"] ?? "",
      disposition: response.headers()["content-disposition"] ?? "",
      body: await response.body(),
    };
  }

  async recordView(token: string) {
    const response = await this.request.post(`${this.base}/proposals/${token}/view`);
    return { status: response.status(), body: await response.json() };
  }

  /** Drive a project to a completed analysis without touching the browser. */
  async completeIntake(size: "3.6 kWp" | "6 kWp" | "9.6 kWp" | string, consumption = "1,150 kWh") {
    const { projectId } = await this.createProject();
    await this.chatOrThrow(projectId, "-34.04658242871865, 18.46491476666948");
    await this.chatOrThrow(projectId, consumption);
    await this.chatOrThrow(projectId, size);
    return projectId;
  }

  async analysedProject(size: "3.6 kWp" | "6 kWp" | "9.6 kWp") {
    const projectId = await this.completeIntake(size);
    const analysis = await this.runAnalysis(projectId);
    return { projectId, analysis };
  }

  async finalisedProposal(size: "3.6 kWp" | "6 kWp" | "9.6 kWp") {
    const { projectId, analysis } = await this.analysedProject(size);
    const finalized = await this.finalize(projectId);
    if (finalized.status !== 200) {
      throw new Error(`finalize failed: ${finalized.status} ${JSON.stringify(finalized.body)}`);
    }
    return { projectId, analysis, shareToken: finalized.body.shareToken as string };
  }
}
