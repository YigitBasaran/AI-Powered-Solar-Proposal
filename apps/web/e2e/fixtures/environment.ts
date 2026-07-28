/**
 * What stack is this test talking to?
 *
 * A `@live` test must never silently run against fixtures and report a pass —
 * that is how a suite comes to claim coverage it does not have. Mode is read
 * from the running application's own health endpoint rather than from an
 * environment variable the test process happens to hold, so it reflects the
 * server actually under test.
 */

export type StackMode = {
  maps: "live" | "fixture";
  pvgis: "live" | "fixture";
  fx: "live" | "fixture";
  llm: "ollama" | "rules" | "disabled";
  llmModel: string | null;
  status: "ok" | "degraded";
  sourceWidthPx: number;
  sourceHeightPx: number;
  groundMetresPerSourcePixel: number;
};

type ReadyPayload = {
  status: "ok" | "degraded";
  checks: {
    maps: { mode: string };
    pvgis: { mode: string };
    fx: { mode: string };
    llm: { provider: string; model: string | null };
  };
  sourceRaster: {
    sourceWidthPx: number;
    sourceHeightPx: number;
    groundMetresPerSourcePixel: number;
  };
};

export async function readStackMode(baseURL: string): Promise<StackMode> {
  const response = await fetch(`${baseURL.replace(/\/$/, "")}/api/v1/health/ready`);
  if (!response.ok) {
    throw new Error(`health/ready returned ${response.status} at ${baseURL}`);
  }
  const body = (await response.json()) as ReadyPayload;
  return {
    maps: body.checks.maps.mode as "live" | "fixture",
    pvgis: body.checks.pvgis.mode as "live" | "fixture",
    fx: body.checks.fx.mode as "live" | "fixture",
    llm: body.checks.llm.provider as "ollama" | "rules" | "disabled",
    llmModel: body.checks.llm.model,
    status: body.status,
    sourceWidthPx: body.sourceRaster.sourceWidthPx,
    sourceHeightPx: body.sourceRaster.sourceHeightPx,
    groundMetresPerSourcePixel: body.sourceRaster.groundMetresPerSourcePixel,
  };
}

/** Is an Ollama daemon up, and is `model` actually installed? */
export async function probeOllama(
  baseUrl = process.env.OLLAMA_BASE_URL ?? "http://127.0.0.1:11434",
  model = process.env.OLLAMA_MODEL ?? "qwen3.5:2b",
): Promise<{ reachable: boolean; installed: boolean; models: string[] }> {
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 3000);
    const response = await fetch(`${baseUrl.replace(/\/$/, "")}/api/tags`, {
      signal: controller.signal,
    });
    clearTimeout(timer);
    if (!response.ok) return { reachable: false, installed: false, models: [] };
    const body = (await response.json()) as { models?: { name: string }[] };
    const models = (body.models ?? []).map((m) => m.name);
    // Ollama reports `name:tag`; an untagged request means `:latest`.
    const wanted = model.includes(":") ? model : `${model}:latest`;
    return { reachable: true, installed: models.includes(wanted), models };
  } catch {
    return { reachable: false, installed: false, models: [] };
  }
}

/**
 * Reasons a `@live` test may legitimately not run, phrased so a skipped test
 * reads as a fact about the environment rather than as an unexplained gap.
 */
export const SKIP = {
  notLive: (what: string, mode: string) =>
    `${what} is in ${mode} mode — this stack is deterministic, not live`,
  ollamaAbsent: (model: string) =>
    `${model} is not installed — run scripts/pull-model.ps1 (or .sh) first`,
  ollamaUnreachable: (url: string) => `no Ollama daemon answered at ${url}`,
} as const;
