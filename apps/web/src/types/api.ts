/**
 * Shapes returned by the API.
 *
 * Money arrives as strings because it is Decimal on the server; keeping it a
 * string all the way to the formatter means a cent can never be lost to a
 * float round-trip in the browser.
 */

export type Point = { x: number; y: number };

export type ProgressStep = {
  step: string;
  label: string;
  state: "done" | "active" | "pending";
};

export type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  step: string | null;
  parserSource: string | null;
  createdAt: string;
};

export type CreateProjectResponse = {
  projectId: string;
  currentStep: string;
  assistantMessage: string;
  progress: ProgressStep[];
};

export type ChatResponse = {
  projectId: string;
  currentStep: string;
  assistantMessage: string;
  accepted: boolean;
  parserSource: string;
  progress: ProgressStep[];
  readyForAnalysis: boolean;
};

export type ProjectResponse = {
  projectId: string;
  currentStep: string;
  rawLocationInput: string | null;
  resolvedLatitude: number | null;
  resolvedLongitude: number | null;
  monthlyConsumptionKwh: number | null;
  annualConsumptionKwh: number | null;
  selectedSystemSizeKwp: number | null;
  requestedPanelCount: number | null;
  analysisStatus: string;
  progress: ProgressStep[];
  messages: ChatMessage[];
  analysis: Analysis | null;
};

export type MapConfig = {
  mode: "live" | "fixture";
  isLive: boolean;
  center: { latitude: number; longitude: number };
  zoom: number;
  scale: number;
  requestedSize: string;
  sourceWidthPx: number;
  sourceHeightPx: number;
  groundMetresPerSourcePixel: number;
  groundSpanM: number;
  attribution: string;
  imageUrl: string;
};

export type RoofEdge = {
  id: string;
  type: "eave" | "hip" | "ridge";
  startVertexId: string;
  endVertexId: string;
  projectedLengthM: number;
  true3dLengthM: number | null;
};

export type RoofFacetSummary = {
  id: string;
  label: string;
  shape: string;
  compassAzimuthDeg: number;
  pvgisAspectDeg: number;
  projectedAreaM2: number;
  slopedAreaM2: number;
};

export type RoofModel = {
  id: string;
  pitchDeg: number;
  groundMetresPerSourcePixel: number;
  totalProjectedAreaM2: number;
  totalSlopedAreaM2: number;
  sourceWidthPx: number;
  sourceHeightPx: number;
  facets: RoofFacetSummary[];
  edges: RoofEdge[];
  edgeGeometry: RoofEdge[];
  vertices: { id: string; sourcePixel: Point; heightM: number | null }[];
  facetGeometry: {
    id: string;
    label: string;
    vertexIds: string[];
    eaveEdgeId: string;
    sourcePixelPolygon: Point[];
  }[];
};

export type PanelShape = { id: string; sourcePixelPolygon: Point[] };

export type Analysis = {
  roof: {
    id: string;
    pitchDeg: number;
    groundMetresPerSourcePixel: number;
    totalProjectedAreaM2: number;
    totalSlopedAreaM2: number;
    facets: RoofFacetSummary[];
    edges: RoofEdge[];
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
      panels: PanelShape[];
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
      pitchDeg: number;
      compassAzimuthDeg: number;
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
    retrievedAt: string;
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
    cashFlow: {
      year: number;
      annualSavingsEur: string;
      cumulativeCashFlowEur: string;
    }[];
  };
  meta?: {
    rawLocationInput: string | null;
    location: { latitude: number; longitude: number };
    monthlyConsumptionKwh: number;
    annualConsumptionKwh: number;
    finalisedAt: string;
  };
};

export type Proposal = Analysis & {
  shareToken: string;
  createdAt: string;
  capacityWarning: string | null;
  aiSummary: string | null;
  layoutSnapshotUrl: string | null;
  views?: { viewCount: number; lastOpenedAt: string | null };
};

export type FinalizeResponse = {
  proposalId: string;
  shareToken: string;
  shareUrl: string;
  pdfUrl: string;
  capacityWarning: string | null;
};

export type HealthCheck = {
  mode?: string;
  provider?: string;
  model?: string | null;
  dataProvider?: string;
  ready: boolean;
  detail?: string;
};

export type HealthReady = {
  status: "ok" | "degraded";
  checks: Record<string, HealthCheck>;
  sourceRaster: {
    zoom: number;
    scale: number;
    sourceWidthPx: number;
    groundMetresPerSourcePixel: number;
    groundSpanM: number;
  };
};

export type ApiError = {
  error: { code: string; message: string; details?: unknown; requestId?: string };
};
