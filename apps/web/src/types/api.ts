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
  name?: string | null;
  /** Null for a project with no customer, including every legacy one. */
  customer?: Customer | null;
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

/** Something standing on the roof that no panel may be placed over. */
export type RoofObstruction = {
  id: string;
  label: string;
  kind: string;
  facetId: string;
  sourcePixelPolygon: Point[];
};

export type RoofModel = {
  id: string;
  pitchDeg: number;
  groundMetresPerSourcePixel: number;
  totalProjectedAreaM2: number;
  totalSlopedAreaM2: number;
  totalObstructedAreaM2: number;
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
  obstructionGeometry: RoofObstruction[];
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
  revisionNumber: number;
  reference: string | null;
  /**
   * The display name only. The public payload is an allow-list, so an email
   * address cannot appear here even if one is added to the stored snapshot.
   */
  customer: { displayName: string } | null;
  views?: ProposalViews;
};

/**
 * Page views, not email opens. There is no open tracking anywhere in this
 * system, and the wording in the UI has to keep saying so.
 */
export type ProposalViews = {
  viewCount: number;
  firstOpenedAt: string | null;
  lastOpenedAt: string | null;
};

export type Customer = {
  customerId: string;
  firstName: string;
  lastName: string;
  displayName: string;
  email: string;
  phone: string | null;
  companyName: string | null;
  address: string | null;
  archivedAt: string | null;
  createdAt: string;
  updatedAt: string;
  /** Present on list rows so the operator can see who has work in flight. */
  projectCount?: number;
};

/** One of a customer's projects, as their detail page lists it. */
export type CustomerProject = {
  projectId: string;
  name: string | null;
  currentStep: string;
  analysisStatus: string;
  isRevision: boolean;
  revisionOfProjectId: string | null;
  hasProposal: boolean;
  proposalId: string | null;
  shareToken: string | null;
  reference: string | null;
  revisionNumber: number | null;
  systemSizeKwp: number | null;
  finalisedAt: string | null;
  createdAt: string;
  updatedAt: string;
};

/**
 * A page of rows, with what a numbered pager needs to render itself.
 *
 * `total` and `totalPages` come from the server: a client that only knows
 * "there might be more" cannot say "page 3 of 9", and one that counts by
 * fetching every page has defeated the point of paging.
 */
export type Paged<T> = T & {
  page: number;
  pageSize: number;
  total: number;
  totalPages: number;
};

/** A row on the all-projects list. Its customer may be null. */
export type ProjectSummary = {
  projectId: string;
  name: string | null;
  currentStep: string;
  analysisStatus: string;
  isRevision: boolean;
  customer: { customerId: string; displayName: string } | null;
  hasProposal: boolean;
  shareToken: string | null;
  reference: string | null;
  revisionNumber: number | null;
  systemSizeKwp: number | null;
  createdAt: string;
};

export type CustomerDetail = {
  customer: Customer;
  projects: CustomerProject[];
  activity: ActivityEvent[];
};

export type CustomerInput = {
  firstName: string;
  lastName: string;
  email: string;
  phone?: string | null;
  companyName?: string | null;
  address?: string | null;
  displayName?: string | null;
};

/** What would be sent. Rendered by the same code that sends it. */
export type EmailPreview = {
  /** Null when the proposal has no customer — a preview describes what would
   *  happen, and "nothing, because there is nobody to send to" is an answer. */
  to: string | null;
  toMasked: string | null;
  subject: string | null;
  textBody: string | null;
  htmlBody: string | null;
  publicUrl: string;
  revisionNumber: number;
  reference: string | null;
  from: string | null;
  fromName: string;
  replyTo: string | null;
  provider: string;
  /** False in console mode. The UI must not say "sent" when this is false. */
  providerSends: boolean;
  providerAvailable: boolean;
  providerDetail: string | null;
  includesPdf: boolean;
};

/**
 * `sent` means the provider accepted the message - not that it arrived. There
 * is deliberately no `delivered`, `bounced` or `opened`, because SMTP offers
 * no way to know any of them.
 */
export type DeliveryStatus = "pending" | "sending" | "sent" | "failed";

export type Delivery = {
  deliveryId: string;
  proposalId: string;
  channel: string;
  recipientMasked: string | null;
  status: DeliveryStatus;
  provider: string;
  providerSends: boolean;
  attemptCount: number;
  errorCode: string | null;
  errorMessage: string | null;
  requestedAt: string;
  lastAttemptAt: string | null;
  sentAt: string | null;
  failedAt: string | null;
};

export type ActivityEvent = {
  eventId: string;
  eventType: string;
  actor: "user" | "system" | "customer";
  projectId: string | null;
  customerId: string | null;
  proposalId: string | null;
  deliveryId: string | null;
  metadata: Record<string, string | number | boolean>;
  occurredAt: string;
};

export type ProposalRevision = {
  revisionNumber: number;
  projectId: string;
  isCurrent: boolean;
  isSuperseded: boolean;
  proposalId: string | null;
  shareToken: string | null;
  reference: string | null;
  finalisedAt: string | null;
  systemSizeKwp: number | null;
  annualProductionKwh: number | null;
  customer: { displayName: string } | null;
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
  /**
   * Email only: whether this provider actually transmits.
   *
   * Distinct from `ready`, and the distinction is the point — console mode is
   * perfectly ready and sends nothing.
   */
  sends?: boolean;
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
