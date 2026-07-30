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
  /** Present on replies received in this session; absent on reloaded history. */
  interpretation?: Interpretation | null;
};

export type CreateProjectResponse = {
  projectId: string;
  currentStep: string;
  assistantMessage: string;
  progress: ProgressStep[];
};

/**
 * Why a fallback happened. The first two are ordinary operation; the rest mean
 * a model was reached and could not deliver.
 */
export type FallbackReason =
  | "rules_sufficient"
  | "not_configured"
  | "unreachable"
  | "timeout"
  | "http_error"
  | "empty_response"
  | "invalid_json"
  | "schema_rejected"
  | "domain_rejected";

/** How one message came to be understood. */
export type Interpretation = {
  configuredProvider: string;
  /** Non-null only when an HTTP call was actually issued. */
  attemptedProvider: string | null;
  effectiveProvider: string;
  fallbackReason: FallbackReason | null;
  modelName: string | null;
  latencyMs: number | null;
};

export type ChatResponse = {
  projectId: string;
  currentStep: string;
  assistantMessage: string;
  accepted: boolean;
  /** Derived from `interpretation.effectiveProvider`; kept for compatibility. */
  parserSource: string;
  progress: ProgressStep[];
  readyForAnalysis: boolean;
  analysisStatus?: string;
  interpretation?: Interpretation | null;
  /** Set when the reply came from a revision this change forked. */
  revisionOfProjectId?: string | null;
  /** Which inputs this message recalculated, if any. */
  recalculated?: string[] | null;
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
  /** Why the last analysis failed. Present only when `analysisStatus` is `failed`. */
  analysisError?: { code: string; message: string; details?: Record<string, unknown> } | null;
  progress: ProgressStep[];
  messages: ChatMessage[];
  analysis: Analysis | null;
  revisionOfProjectId?: string | null;
  revisionProjectId?: string | null;
  hasProposal?: boolean;
};

export type MapConfig = {
  /** Whether imagery comes from Google's own origin, or from a test stub. */
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
  /** Signature of the imagery configuration currently in force. */
  requestSignature: string;
  /** The signature the committed roof calibration was traced against. */
  calibrationSignature: string | null;
  calibrationTracedOn: string | null;
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
    /** Absent on proposals issued before PVGIS became a mandatory live call. */
    pvgis?: {
      source: string;
      endpoint: string;
      origin: string;
      apiVersion: string | null;
      batchCompletedAt: string;
      radiationDatabase: string;
      request: Record<string, unknown>;
      probes: {
        facetId: string;
        compassAzimuthDeg: number;
        pvgisAspectDeg: number;
        angleDeg: number;
        specificYieldKwhPerKwp: number;
        monthlySpecificYieldKwhPerKwp: number[];
        radiationDatabase: string;
        retrievedAt: string;
        losses: Record<string, number> | null;
      }[];
    } | null;
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
