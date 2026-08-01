import type {
  ActivityEvent,
  Analysis,
  ChatResponse,
  CreateProjectResponse,
  Customer,
  CustomerDetail,
  CustomerInput,
  Delivery,
  EmailPreview,
  FinalizeResponse,
  HealthReady,
  MapConfig,
  Paged,
  ProjectResponse,
  ProjectSummary,
  Proposal,
  ProposalRevision,
  RoofModel,
} from "@/types/api";

/**
 * All requests go to this origin and are rewritten to the API by Next.
 * Nothing here talks to Google or PVGIS directly: keeping every external call
 * behind the backend is what keeps the API key server-side and the Konva
 * canvas same-origin (and therefore exportable).
 */
const BASE = "/api/v1";

export class ApiRequestError extends Error {
  readonly code: string;
  readonly status: number;
  readonly details: unknown;

  constructor(message: string, code: string, status: number, details?: unknown) {
    super(message);
    this.name = "ApiRequestError";
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });

  if (!response.ok) {
    let code = "REQUEST_FAILED";
    let message = `Request failed (${response.status})`;
    let details: unknown;
    try {
      const body = await response.json();
      if (body?.error) {
        code = body.error.code ?? code;
        message = body.error.message ?? message;
        details = body.error.details;
      }
    } catch {
      // A non-JSON error body is still an error; keep the status message.
    }
    throw new ApiRequestError(message, code, response.status, details);
  }

  return (await response.json()) as T;
}

export const api = {
  ready: () => request<HealthReady>("/health/ready"),

  createProject: (body?: { customerId?: string; name?: string }) =>
    request<CreateProjectResponse>("/projects", {
      method: "POST",
      ...(body ? { body: JSON.stringify(body) } : {}),
    }),

  getProject: (projectId: string) => request<ProjectResponse>(`/projects/${projectId}`),

  listProjects: (params?: {
    q?: string;
    customerId?: string;
    page?: number;
    pageSize?: number;
  }) => {
    const search = new URLSearchParams();
    if (params?.q) search.set("q", params.q);
    if (params?.customerId) search.set("customerId", params.customerId);
    if (params?.page) search.set("page", String(params.page));
    if (params?.pageSize) search.set("pageSize", String(params.pageSize));
    const query = search.toString();
    return request<Paged<{ projects: ProjectSummary[] }>>(`/projects${query ? `?${query}` : ""}`);
  },

  // --- customers ---------------------------------------------------------

  listCustomers: (params?: { q?: string; page?: number; pageSize?: number }) => {
    const search = new URLSearchParams();
    if (params?.q) search.set("q", params.q);
    if (params?.page) search.set("page", String(params.page));
    if (params?.pageSize) search.set("pageSize", String(params.pageSize));
    const query = search.toString();
    return request<Paged<{ customers: Customer[] }>>(`/customers${query ? `?${query}` : ""}`);
  },

  createCustomer: (body: CustomerInput) =>
    request<{ customer: Customer }>("/customers", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  getCustomer: (customerId: string) => request<CustomerDetail>(`/customers/${customerId}`),

  updateCustomer: (customerId: string, body: Partial<CustomerInput>) =>
    request<{ customer: Customer }>(`/customers/${customerId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  archiveCustomer: (customerId: string) =>
    request<{ customer: Customer }>(`/customers/${customerId}/archive`, { method: "POST" }),

  unarchiveCustomer: (customerId: string) =>
    request<{ customer: Customer }>(`/customers/${customerId}/unarchive`, { method: "POST" }),

  // Refused by the server when anything has been issued to them. The UI offers
  // archiving as the alternative rather than hiding the button.
  deleteCustomer: (customerId: string) =>
    request<{ deleted: boolean }>(`/customers/${customerId}`, { method: "DELETE" }),

  renameProject: (projectId: string, name: string | null) =>
    request<ProjectResponse>(`/projects/${projectId}`, {
      method: "PATCH",
      body: JSON.stringify({ name }),
    }),

  deleteProject: (projectId: string) =>
    request<{ deleted: boolean }>(`/projects/${projectId}`, { method: "DELETE" }),

  assignCustomer: (projectId: string, customerId: string) =>
    request<ProjectResponse>(`/projects/${projectId}/customer`, {
      method: "POST",
      body: JSON.stringify({ customerId }),
    }),

  // --- history -----------------------------------------------------------

  revisions: (projectId: string) =>
    request<{ revisions: ProposalRevision[] }>(`/projects/${projectId}/revisions`),

  activity: (projectId: string, params?: { limit?: number; cursor?: string }) => {
    const search = new URLSearchParams();
    if (params?.limit) search.set("limit", String(params.limit));
    if (params?.cursor) search.set("cursor", params.cursor);
    const query = search.toString();
    return request<{ events: ActivityEvent[]; nextCursor: string | null }>(
      `/projects/${projectId}/activity${query ? `?${query}` : ""}`,
    );
  },

  // --- delivery ----------------------------------------------------------
  //
  // These take the internal proposal id, never the share token. The token is
  // what customers hold; a send endpoint reachable with it would let anyone
  // forwarded a proposal link cause mail to be sent from this system.

  emailPreview: (proposalId: string) =>
    request<{ preview: EmailPreview }>(`/proposals/${proposalId}/email-preview`),

  deliveries: (proposalId: string) =>
    request<{ deliveries: Delivery[] }>(`/proposals/${proposalId}/deliveries`),

  sendProposal: (proposalId: string, options?: { resendNonce?: string }) =>
    request<{ delivery: Delivery }>(`/proposals/${proposalId}/send`, {
      method: "POST",
      // `confirm` is always literally true here, and the *caller* is the
      // two-step UI: this function is only ever reached from the second click.
      body: JSON.stringify({ confirm: true, resendNonce: options?.resendNonce }),
    }),

  retryDelivery: (proposalId: string, deliveryId: string) =>
    request<{ delivery: Delivery }>(
      `/proposals/${proposalId}/deliveries/${deliveryId}/retry`,
      { method: "POST", body: JSON.stringify({ confirm: true }) },
    ),

  chat: (projectId: string, message: string) =>
    request<ChatResponse>(`/projects/${projectId}/chat`, {
      method: "POST",
      body: JSON.stringify({ message }),
    }),

  runAnalysis: (projectId: string) =>
    request<{ analysis: Analysis; capacityWarning: string | null; currentStep: string }>(
      `/projects/${projectId}/run-analysis`,
      { method: "POST" },
    ),

  finalize: (projectId: string) =>
    request<FinalizeResponse>(`/projects/${projectId}/finalize`, { method: "POST" }),

  mapConfig: () => request<MapConfig>("/maps/config"),

  roofModel: () => request<RoofModel>("/roof/fixed-model"),

  proposal: (token: string) => request<Proposal>(`/proposals/${token}`),

  recordView: (token: string) =>
    request<{ recorded: boolean; viewCount: number; counted: boolean }>(
      `/proposals/${token}/view`,
      { method: "POST" },
    ),

  pdfUrl: (token: string) => `${BASE}/proposals/${token}/pdf`,

  uploadLayoutSnapshot: async (projectId: string, blob: Blob) => {
    const form = new FormData();
    form.append("file", blob, "layout.png");
    const response = await fetch(`${BASE}/projects/${projectId}/layout-snapshot`, {
      method: "POST",
      body: form,
    });
    if (!response.ok) throw new ApiRequestError("Snapshot upload failed", "UPLOAD_FAILED", response.status);
    return (await response.json()) as { stored: boolean; bytes: number };
  },
};
