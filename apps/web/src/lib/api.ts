import type {
  Analysis,
  ChatResponse,
  CreateProjectResponse,
  FinalizeResponse,
  HealthReady,
  MapConfig,
  ProjectResponse,
  Proposal,
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

  createProject: () => request<CreateProjectResponse>("/projects", { method: "POST" }),

  getProject: (projectId: string) => request<ProjectResponse>(`/projects/${projectId}`),

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

  project: (projectId: string) => request<ProjectResponse>(`/projects/${projectId}`),

  proposal: (token: string) => request<Proposal>(`/proposals/${token}`),

  recordView: (token: string) =>
    request<{ recorded: boolean; viewCount: number }>(`/proposals/${token}/view`, {
      method: "POST",
    }),

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
